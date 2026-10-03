from __future__ import annotations
import json
import os
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path


def _simple(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        # Los diagnósticos no guardan RFC, correo, folio, contraseñas ni texto de campos.
        return value[:500]
    if isinstance(value, (list, tuple)):
        return [_simple(v) for v in value[:80]]
    if isinstance(value, dict):
        return {str(k)[:80]: _simple(v) for k, v in list(value.items())[:120]}
    return repr(value)[:500]


class Diagnostico:
    """Bitácora local de diagnóstico. Nunca reintenta ni pulsa botones.

    Guarda pasos, excepción, captura de pantalla y estructura de ventanas/controles.
    Por privacidad no guarda valores de RFC, correo, folio ni contraseñas; para campos
    sólo registra si están vacíos, longitud, clase, foco, visibilidad y geometría.
    """
    def __init__(self, base, log=None):
        self.base = Path(base)
        self.log = log
        self.folder = self.base/'logs'/'diagnosticos'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.events = self.folder/'eventos.jsonl'
        self.last = self.folder/'ultimo_error.json'
        self.lock = threading.RLock()
        self._last_event = None

    def evento(self, etapa, estado='INFO', detalle='', datos=None):
        row = {
            'fecha': datetime.now().astimezone().isoformat(timespec='milliseconds'),
            'etapa': str(etapa or 'SIN_ETAPA')[:300],
            'estado': str(estado or 'INFO')[:40],
            'detalle': str(detalle or '')[:700],
            'datos': _simple(datos or {}),
        }
        with self.lock:
            self._last_event = row
            try:
                with self.events.open('a', encoding='utf-8') as f:
                    f.write(json.dumps(row, ensure_ascii=False)+'\n')
            except OSError:
                pass
        return row

    def campo(self, campo, *, vacio=None, longitud=None, clase='', foco=None, visible=None, habilitado=None, etapa='CAMPO'):
        datos = {'campo': str(campo), 'vacio': vacio, 'longitud': longitud,
                 'clase': str(clase or ''), 'foco': foco, 'visible': visible,
                 'habilitado': habilitado}
        return self.evento(etapa, 'OK' if vacio is False else 'REVISION', 'Estado de campo', datos)

    def _ventanas(self, bot):
        rows=[]
        if bot is None:
            return rows
        try:
            from . import polaris
            wg=polaris.win32gui
            fg=wg.GetForegroundWindow()
            pids=set()
            main=bot._find_title(r'Polaris Facturaci')
            if main:
                try:pids.add(polaris.win32process.GetWindowThreadProcessId(main)[1])
                except Exception:pass
            def add(h, nivel, parent=0):
                try:
                    if not wg.IsWindowVisible(h): return
                    pid=polaris.win32process.GetWindowThreadProcessId(h)[1]
                    if pids and pid not in pids:return
                    r=bot._rect(h)
                    title=(wg.GetWindowText(h) or '').strip()
                    cls=bot._class_name(h)
                    # Sólo títulos de ventana y etiquetas cortas conocidas. No valores de Edit/Combo.
                    clsup=cls.upper()
                    if any(k in clsup for k in ('EDIT','COMBO','MEMO')):
                        title='(valor omitido)'
                    rows.append({'nivel':nivel,'hwnd':int(h),'parent':int(parent or 0),
                                 'clase':cls[:160],'titulo':title[:220],
                                 'rect':[r.left,r.top,r.right,r.bottom],
                                 'habilitado':bool(wg.IsWindowEnabled(h)),
                                 'foco':bool(h==fg)})
                except Exception: pass
            tops=[]
            for h in bot._enum():
                try:
                    pid=polaris.win32process.GetWindowThreadProcessId(h)[1]
                    if not pids or pid in pids:tops.append(h)
                except Exception:pass
            for top in tops:
                add(top,0,0)
                try:
                    def cb(ch,_): add(ch,1,top)
                    wg.EnumChildWindows(top,cb,None)
                except Exception:pass
        except Exception:
            pass
        return rows[:1200]

    def capturar(self, etapa, error=None, *, bot=None, contexto=None, manual=False):
        ident=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
        d=self.folder/ident
        try:d.mkdir(parents=True, exist_ok=False)
        except OSError:return None
        exc_text=''
        tb=''
        if error is not None:
            exc_text=f'{type(error).__name__}: {error}'
            try:tb=''.join(traceback.format_exception(type(error), error, error.__traceback__))
            except Exception:tb=''
        data={
            'id':ident,
            'fecha':datetime.now().astimezone().isoformat(timespec='seconds'),
            'manual':bool(manual),
            'etapa':str(etapa or 'SIN_ETAPA')[:400],
            'error':exc_text[:2000],
            'traceback':tb[-12000:],
            'contexto':_simple(contexto or {}),
            'ultimo_evento':self._last_event,
            'ventanas':self._ventanas(bot),
        }
        try:
            cap=getattr(bot,'_captura317',None) if bot else None
            if cap is not None:
                data['captura_factura_eventos']=_simple(getattr(cap,'eventos',[])[-100:])
        except Exception:pass
        # Captura de pantalla: evidencia exacta del momento del fallo.
        try:
            from . import polaris
            if getattr(polaris,'pyautogui',None):
                polaris.pyautogui.screenshot(str(d/'pantalla.png'))
        except Exception:
            pass
        try:(d/'diagnostico.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:pass
        try:self.last.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:pass
        self.evento(etapa,'ERROR' if error else 'CAPTURA_MANUAL',exc_text or 'Captura manual',{'id':ident})
        if self.log:
            try:self.log('DIAGNÓSTICO '+ident+': '+(exc_text or 'captura manual guardada'))
            except Exception:pass
        return d

    def ultimos_eventos(self, limit=220):
        try:
            lines=self.events.read_text(encoding='utf-8',errors='replace').splitlines()[-max(1,int(limit)):]
            out=[]
            for line in lines:
                try:out.append(json.loads(line))
                except Exception:pass
            return out
        except OSError:return []

    def ultimo_error(self):
        try:return json.loads(self.last.read_text(encoding='utf-8'))
        except Exception:return {}

    def limpiar_eventos(self):
        try:self.events.unlink(missing_ok=True)
        except Exception:pass


def diagnosticar_operacion(nombre):
    def deco(fn):
        def wrapped(self,*args,**kwargs):
            diag=getattr(self,'diag',None)
            if diag:diag.evento(getattr(self,'etapa_alerta',nombre),'INICIO',nombre)
            try:
                result=fn(self,*args,**kwargs)
                if diag:diag.evento(getattr(self,'etapa_alerta',nombre),'FIN',nombre+' terminada')
                return result
            except Exception as exc:
                if diag:
                    diag.capturar(getattr(self,'etapa_alerta',nombre),exc,bot=self,
                                  contexto={'operacion':nombre})
                raise
        wrapped.__name__=getattr(fn,'__name__','operacion')
        wrapped.__doc__=getattr(fn,'__doc__',None)
        return wrapped
    return deco
