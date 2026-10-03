"""Alta de Clientes de Efectivo: recorrido revisado en los dos videos
20260925-2137-54.6746743.mp4 y 20260925-2138-55.7918907.mp4.

v3.1.5 verifica los cuatro campos con lectura directa y espera acotada; si
la lectura VCL no refleja el texto, exige una copia NUEVA desde ese control.
Un solo reintento con teclado sustituye el contenido, nunca lo concatena.
Mantiene Número como dato de salida y Nuevo / Insert Record obligatorio.

Base de facturación intacta. Sin conexión SQL, sentencias de BD ni servicios SAT
externos. La lupa de Polaris obtiene los datos fiscales, como en el video.

Preparar NO pulsa Aceptar. Aceptar requiere una preparación válida, confirmación
explícita y revisión previa del operador de que el RFC no exista. No se inventa
una búsqueda automática de duplicados que no fue mostrada en el video.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import json
import re
import time
import uuid

from .cliente_model import AltaCliente, AltaClienteError
from . import polaris as pmod
from .alta_texto import TextoWindows, TextoControlError, normalizar_campo


class ControlAltaNoIdentificado(AltaClienteError):
    """La geometría no resolvió un control admitido; no implica campo vacío."""


class LecturaControlAltaError(AltaClienteError):
    """El control no respondió a una lectura de Windows."""


@dataclass
class AltaPreparada:
    solicitud: AltaCliente
    formulario: int
    principal: int
    pid: int
    valores: dict
    creada: float
    identificador: str


class DiarioAltas:
    """Registro LOCAL de intentos; jamás escribe en Polaris ni guarda los 4 datos."""
    def __init__(self, base: Path):
        self.path=Path(base)/'data'/'altas_intentos_locales.json'

    def leer(self):
        if not self.path.exists(): return {}
        try:
            d=json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(d,dict): raise ValueError('Formato inválido')
            return d
        except Exception as e:
            raise AltaClienteError('No se pudo leer el registro LOCAL de altas. Revisa el archivo antes de continuar.') from e

    def revisar(self, solicitud):
        state=self.leer().get(solicitud.clave_local())
        if state:
            raise AltaClienteError('Ya hay un intento de alta guardado localmente para ese RFC y estación '
                                   '('+str(state.get('estado','REVISAR'))+'). Revisa Polaris; no se repite el alta.')

    def registrar(self, solicitud, estado, numero=''):
        data=self.leer()
        data[solicitud.clave_local()]={'estado':estado,'fecha_local':time.strftime('%Y-%m-%dT%H:%M:%S'),
                                      'numero_cliente':str(numero)}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        tmp.replace(self.path)


class AltaClientes:
    def __init__(self, bot, *, ui=None, diario=None, reloj=time.monotonic, progreso=None):
        self.bot=bot
        self.ui=ui if ui is not None else PantallaAlta(bot)
        self.diario=diario if diario is not None else DiarioAltas(bot.base)
        self.reloj=reloj
        self.pendiente=None
        self.progreso=progreso

    def _paso(self,mensaje):
        self.bot.etapa_alerta=mensaje
        self.bot.log(mensaje)
        if self.progreso is not None:
            self.progreso(mensaje)

    @contextmanager
    def _exclusive(self):
        if not self.bot._lock.acquire(blocking=False):
            raise AltaClienteError('Polaris está ocupado. Espera a que termine la otra operación.')
        try: yield
        finally: self.bot._lock.release()

    def preparar(self, solicitud: AltaCliente):
        sol=solicitud.validar()
        with self._exclusive():
            if self.pendiente:
                raise AltaClienteError('Ya hay una preparación pendiente. Revísala antes de iniciar otra.')
            self.diario.revisar(sol)
            self._paso('ALTA EFECTIVO 1/7: preparando Polaris y verificando la estación. No se presionará Aceptar.')
            main=self.bot.ensure_ready()
            # Primero evita que la limpieza cierre una alta/edición sin guardar.
            self.ui.proteger_edicion_abierta(main)
            main=self.bot._change_station(main,sol.estacion)
            self._paso('ALTA EFECTIVO 2/7: abriendo Clientes de Efectivo.')
            form=self.ui.abrir(main)
            current=self.ui.snapshot(main,form)
            if current.get('rfc','').strip().upper()==sol.rfc:
                raise AltaClienteError('El RFC ya está visible como cliente registrado. No se presiona Nuevo ni se edita.')
            self._paso('ALTA EFECTIVO 3/7: ventana identificada; pulsando Nuevo y verificando campos vacíos.')
            self.ui.nuevo(main,form)
            blank=self.ui.snapshot(main,form)
            if (any(blank.get(k,'').strip() for k in ('rfc','idcif','nombre'))
                    or (blank.get('numero') is not None and blank['numero'].strip())):
                raise AltaClienteError('Nuevo no dejó los campos vacíos; no se escribe encima de otro cliente.')
            self.ui.exigir_nuevo(main,form)
            self._paso('ALTA EFECTIVO 4/7: capturando RFC e idCIF.')
            self.ui.escribir(main,form,'rfc',sol.rfc)
            self.ui.escribir(main,form,'idcif',sol.idcif)
            self.ui.consultar_idcif(main,form)
            self._paso('ALTA EFECTIVO 5/7: esperando nombre y C. P. de la lupa (C. P. debajo del RFC).')
            self.ui.esperar_fiscales(main,form,sol)
            self._paso('ALTA EFECTIVO 6/7: capturando Teléfono 1 y Correo Electrónico.')
            self.ui.escribir(main,form,'telefono',sol.telefono)
            self.ui.escribir(main,form,'correo',sol.correo)
            vals=self.ui.snapshot(main,form)
            self._validar_captura(sol,vals)
            self.ui.exigir_nuevo(main,form)
            self.pendiente=AltaPreparada(sol,form,main,self.bot._pid(main),vals,self.reloj(),uuid.uuid4().hex)
            self._paso('ALTA EFECTIVO 7/7: preparación verificada. Aceptar NO se pulsó.')
            return {'estado':'PREPARADA_SIN_GUARDAR','nombre':vals['nombre'],
                    'codigo_postal':vals['cp'],'numero_cliente':vals.get('numero') or '',
                    'numero_legible':vals.get('numero') is not None}

    @staticmethod
    def _validar_captura(sol,vals):
        if normalizar_campo('rfc',vals.get('rfc',''))!=sol.rfc or normalizar_campo('idcif',vals.get('idcif',''))!=sol.idcif:
            raise AltaClienteError('El RFC/idCIF en la pantalla no coincide con la solicitud. No se acepta.')
        if not vals.get('nombre','').strip() or not re.fullmatch(r'[0-9]{5}',vals.get('cp','').strip()):
            raise AltaClienteError('Polaris no completó nombre y código postal. Revisa la consulta idCIF.')
        phone=re.sub(r'[ ()-]','',vals.get('telefono','').strip())
        if phone!=sol.telefono or vals.get('correo','').strip()!=sol.correo:
            raise AltaClienteError('El teléfono/correo en Polaris no coincide con lo capturado. No se acepta.')
        if vals.get('numero') is not None and vals['numero'].strip():
            raise AltaClienteError('La pantalla ya tiene número de cliente. No se repetirá Aceptar.')

    def aceptar(self, solicitud: AltaCliente, *, autorizado=False, inexistencia_revisada=False):
        self.bot.etapa_alerta="ALTA: validar autorización antes de Aceptar"
        sol=solicitud.validar()
        with self._exclusive():
            if not autorizado or not inexistencia_revisada:
                raise AltaClienteError('Hace falta autorizar el guardado y revisar que el RFC no esté registrado.')
            if self.bot.cfg.get('app',{}).get('modo_prueba',True):
                raise AltaClienteError('MODO SEGURO activo: Aceptar está bloqueado. La preparación no guarda clientes.')
            p=self.pendiente
            if not p: raise AltaClienteError('Primero prepara y revisa los datos del alta.')
            if p.solicitud!=sol: raise AltaClienteError('Los datos cambiaron desde la preparación. No se acepta.')
            if self.reloj()-p.creada>900:
                raise AltaClienteError('La preparación superó 15 minutos. Revisa/cancela manualmente y prepara otra vez.')
            self.diario.revisar(sol)
            self.ui.verificar_contexto(p.principal,p.formulario,sol.estacion,p.pid)
            vals=self.ui.snapshot(p.principal,p.formulario)
            self._validar_captura(sol,vals)
            if vals!=p.valores:
                raise AltaClienteError('La pantalla cambió después de la preparación. No se presiona Aceptar.')
            self.ui.exigir_nuevo(p.principal,p.formulario)
            # Comprobar el botón ANTES de registrar intención irreversible.
            self.ui.validar_aceptar(p.principal,p.formulario)
            if self.bot.cfg.get('app',{}).get('modo_prueba',True):
                raise AltaClienteError('Se activó MODO SEGURO durante la revisión. No se pulsa Aceptar.')
            # Si falla la escritura LOCAL, no se pulsa Aceptar.
            self.diario.registrar(sol,'ACEPTAR_PENDIENTE_DE_VERIFICAR')
            self.pendiente=None   # nunca reintentar automáticamente tras el clic
            self.bot.log('ALTA REAL AUTORIZADA: pulsando Aceptar una sola vez en Clientes de Efectivo.')
            self.bot.etapa_alerta='ALTA: Aceptar solicitado / verificación de guardado'
            self.ui.aceptar(p.principal,p.formulario)
            number=self.ui.verificar_guardado(p.principal,p.formulario,sol)
            self.diario.registrar(sol,'ALTA_CONFIRMADA_EN_PANTALLA',number)
            self.bot.log('ALTA: Polaris muestra número de cliente y terminó la edición. No se abrió facturación.')
            return {'estado':'ALTA_CONFIRMADA_EN_PANTALLA','numero_cliente':number}

    def liberar(self):
        # Solo descarta la autorización en memoria. No cancela/cierra ni responde diálogos de Polaris.
        with self._exclusive(): self.pendiente=None


class PantallaAlta:
    """Adaptador Windows. Los puntos se refieren al formulario, no al escritorio."""
    TITULO=r'^\s*Clientes\s+de\s+Efectivo\s*$'
    # Mapa MEDIDO en Clientes de Efectivo (ventana restaurada de 704 x 566).
    # Se adapta a la posición/tamaño de ese formulario, no al tamaño del monitor.
    # Especialmente: C. P. está DEBAJO del RFC, no a la derecha de idCIF.
    CAMPOS={
        'numero':(45/704,107/566), 'nombre':(382/704,116/566),
        'rfc':(173/704,235/566), 'idcif':(342/704,235/566),
        'cp':(173/704,262/566), 'telefono':(190/704,394/566),
        'correo':(398/704,424/566),
    }
    NUEVO=(62/704,53/566)
    LUPA=(416/704,235/566)
    ACEPTAR=(462/704,520/566)

    def __init__(self,bot,*,texto=None):
        self.bot=bot
        self._texto=texto if texto is not None else TextoWindows()
        self._lectura_por_copia={}
        self._ultimo_error_lectura=None
        self._diagnosticos_emitidos=set()
        self._aviso_numero_formularios=set()
        self._uia_no_disponible={}

    @property
    def wg(self): return pmod.win32gui

    def _form(self,main):
        candidates=[]
        for h in self.bot._cleanup_windows(main):
            title=self.wg.GetWindowText(h) or ''
            if re.fullmatch(self.TITULO,title,re.I):
                r=self.bot._rect(h)
                if r.width>=550 and r.height>=440: candidates.append(h)
        if len(candidates)>1:
            raise AltaClienteError('Hay más de una ventana de Clientes de Efectivo. Revisa Polaris.')
        return candidates[0] if candidates else None

    def _guard(self,main,form):
        if not self.bot._window_exists_visible(form) or not self.bot._same_polaris_process(main,form):
            raise AltaClienteError('La ventana de Clientes de Efectivo ya no está disponible.')
        if not re.fullmatch(self.TITULO,self.wg.GetWindowText(form) or '',re.I):
            raise AltaClienteError('El formulario activo no es Clientes de Efectivo.')
        r=self.bot._rect(form)
        ratio=r.width/r.height
        if r.width<550 or r.height<440 or not 1.19<=ratio<=1.31:
            raise AltaClienteError('El tamaño de Clientes de Efectivo no coincide con el video. '
                                   'Restaura esa ventana (no maximizada) antes de probar.')
        if not self.wg.IsWindowEnabled(form):
            raise AltaClienteError('Polaris abrió un aviso o bloqueó el formulario. Revísalo; no se responde automáticamente.')
        self.bot._check_cleanup_prompt(main)
        for row in self.bot._popup_candidates(main):
            h=row[2]
            if h!=form and self.wg.IsWindowEnabled(h):
                raise AltaClienteError('Apareció otra ventana de Polaris: '+row[3]+'. Revisión manual requerida.')
        self.bot._activate(form)
        fg=self.wg.GetForegroundWindow()
        if not fg or not self.bot._same_polaris_process(main,fg):
            try: pmod.Desktop(backend='win32').window(handle=form).set_focus()
            except Exception: pass
            fg=self.wg.GetForegroundWindow()
            if not fg or not self.bot._same_polaris_process(main,fg):
                raise AltaClienteError('Polaris no tiene el foco. No se enviaron teclas a otra aplicación.')
        pmod.pyautogui.failSafeCheck()

    def _point(self,form,rel):
        r=self.bot._rect(form)
        x=r.left+round(r.width*rel[0]);y=r.top+round(r.height*rel[1])
        h=self.wg.WindowFromPoint((x,y))
        if not h or not self.bot._is_descendant_or_same(form,h):
            raise AltaClienteError('Otro programa tapa el punto de captura. No se hará clic.')
        return x,y,h

    def _field(self,main,form,name,*,verify=True):
        if name not in self.CAMPOS:
            raise AltaClienteError('Campo de alta desconocido.')
        if verify: self._guard(main,form)
        x,y,h=self._point(form,self.CAMPOS[name])
        # El control debe ser un edit/memo, nunca un botón, combobox o una ventana externa.
        cur=h
        while cur and cur!=form:
            cls=self.bot._class_name(cur).upper()
            if self._clase_de_lectura(name,cls):
                return cur,x,y
            cur=self.wg.GetParent(cur)
        # WindowFromPoint puede omitir controles deshabilitados (por ejemplo
        # Número después de guardar). Para lecturas, resolver por rectángulo
        # únicamente dentro del mismo formulario; jamás aceptar controles ajenos.
        candidates=[]
        def consider(ch,_):
            if not self.bot._window_exists_visible(ch): return
            cls=self.bot._class_name(ch).upper()
            if self._clase_de_lectura(name,cls):
                r=self.bot._rect(ch)
                if r.left<=x<r.right and r.top<=y<r.bottom:
                    candidates.append((r.width*r.height,ch))
        self.wg.EnumChildWindows(form,consider,None)
        if candidates:
            candidates.sort()
            return candidates[0][1],x,y
        self._diagnosticar_controles(form,name)
        raise ControlAltaNoIdentificado('No pude identificar el campo '+name+
            ' en Clientes de Efectivo. No se continúa a Aceptar. '
            'Diagnóstico local: logs/diagnostico_alta_controles.json.')

    def _read(self,h):
        # No depende de WM_GETTEXTLENGTH ni transforma un timeout en vacío.
        self._ultimo_error_lectura=None
        try:
            return self._texto.leer(h).strip()
        except TextoControlError as exc:
            self._ultimo_error_lectura={'codigo':exc.codigo,'winerror':exc.winerror}
            if exc.winerror==5:
                raise LecturaControlAltaError('Windows negó acceso a la lectura del control. '
                    'Revisa que Polaris y el bot tengan el mismo nivel de permisos. No se pulsa Aceptar.') from exc
            raise LecturaControlAltaError('No se pudo leer el control de Clientes de Efectivo. '
                                          'No se interpreta como vacío.') from exc

    @staticmethod
    def _clase_de_lectura(name,cls):
        """Nunca usar etiquetas/paneles como destino de pegado.

        Número NO es capturado por el usuario. Algunas versiones de VCL lo
        exponen como Static/Label/DBText en vez de Edit (o no exponen un HWND).
        No se amplía esta excepción a RFC, idCIF, teléfono ni correo.
        """
        cls=cls.upper()
        if any(k in cls for k in ('COMBO','BUTTON','BITBTN','SPEEDBUTTON','GRID')):
            return False
        if 'EDIT' in cls or 'MEMO' in cls:
            return True
        return name=='numero' and (cls=='STATIC' or 'STATICTEXT' in cls
                                   or 'DBTEXT' in cls or 'LABEL' in cls)

    def _diagnosticar_controles(self,form,campo):
        """Solo geometría/clases de Windows. Sin textos, datos fiscales ni SQL.

        Ayuda a diagnosticar versiones VCL que no publican todos sus controles.
        No cambia el foco, no captura pantalla ni envía teclas o clics.
        """
        key=(form,campo)
        if key in self._diagnosticos_emitidos:
            return
        self._diagnosticos_emitidos.add(key)
        try:
            frame=self.bot._rect(form)
            rows=[]
            def cb(h,_):
                if len(rows)>=300 or not self.bot._window_exists_visible(h):
                    return
                if not self.bot._is_descendant_or_same(form,h) or h==form:
                    return
                rect=self.bot._rect(h)
                rows.append({'clase':self.bot._class_name(h),
                             'habilitado':bool(self.wg.IsWindowEnabled(h)),
                             'rect_relativo':[rect.left-frame.left,rect.top-frame.top,
                                              rect.right-frame.left,rect.bottom-frame.top]})
            self.wg.EnumChildWindows(form,cb,None)
            data={'version':'3.1.5','campo_no_identificado':campo,
                  'formulario':'Clientes de Efectivo',
                  'tamano_formulario':[frame.width,frame.height],
                  'controles':rows,
                  'nota':'Sin valores de campos; solo clases y geometría. No prueba guardado.'}
            path=Path(self.bot.base)/'logs'/'diagnostico_alta_controles.json'
            path.parent.mkdir(parents=True,exist_ok=True)
            temp=path.with_suffix('.json.tmp')
            temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            temp.replace(path)
            self.bot.log('ALTA: diagnóstico de controles guardado localmente en logs/diagnostico_alta_controles.json (sin datos del cliente).')
        except Exception:
            self.bot.log('ALTA: no fue posible guardar el diagnóstico local de controles. No se cambia la decisión de seguridad.')

    def _numero_uia(self,form):
        """Fallback de SOLO LECTURA, acotado al rectángulo del Número.

        No invoca botones ni usa OCR. Nunca usa todo el texto de la ventana
        para extraer un número: podría tomar un RFC, CP o teléfono por error.
        Devuelve None si UI Automation no puede distinguir el valor.
        """
        if time.monotonic()<self._uia_no_disponible.get(form,0):
            return None
        coinit=False
        try:
            # El bot corre este módulo en un hilo de trabajo de Tk.
            # COM se inicializa en este hilo solo para la lectura opcional.
            import pythoncom
            pythoncom.CoInitialize()
            coinit=True
            root=pmod.Desktop(backend='uia').window(handle=form).wrapper_object()
            if root.element_info.process_id!=self.bot._pid(form):
                return None
            rect=self.bot._rect(form)
            left=rect.left+round(rect.width*10/704)
            top=rect.top+round(rect.height*96/566)
            right=rect.left+round(rect.width*82/704)
            bottom=rect.top+round(rect.height*122/566)
            candidates=[]
            for c in root.descendants():
                info=c.element_info
                if info.control_type not in ('Text','Edit'):
                    continue
                if info.process_id!=self.bot._pid(form) or not c.is_visible():
                    continue
                r=c.rectangle()
                if not (left<=r.left<r.right<=right and top<=r.top<r.bottom<=bottom):
                    continue
                # Edit requiere el valor, no el nombre accesible de su etiqueta.
                if info.control_type=='Edit':
                    try: value=c.get_value()
                    except Exception: continue
                else:
                    value=c.window_text()
                if value is None:
                    continue
                value=str(value).strip()
                # Un Text vacío podría ser un dibujo/proveedor incompleto.
                # No certifica que el campo Número esté vacío.
                if re.fullmatch(r'[0-9]+',value):
                    candidates.append(value)
            distinct=set(candidates)
            if len(distinct)>1:
                raise AltaClienteError('Lectura ambigua de Número. No se considera vacío ni se continúa a Aceptar.')
            return next(iter(distinct)) if distinct else None
        except AltaClienteError:
            raise
        except Exception:
            # La ausencia de UIA NO equivale a un Número vacío.
            self._uia_no_disponible[form]=time.monotonic()+3.0
            return None
        finally:
            if coinit:
                pythoncom.CoUninitialize()

    def snapshot(self,main,form):
        self._guard(main,form)
        result={};seen=set()
        for name in self.CAMPOS:
            try:
                h,_,_=self._field(main,form,name,verify=False)
            except ControlAltaNoIdentificado:
                if name!='numero':
                    raise
                result[name]=self._numero_uia(form)
                continue
            if h in seen:
                raise AltaClienteError('Dos campos apuntan al mismo control. '
                                       'El diseño no coincide con Clientes de Efectivo; no se continúa.')
            seen.add(h)
            try:
                if (form,name) in self._lectura_por_copia:
                    # Releer siempre; JAMÁS reutilizar el valor enviado o una copia anterior.
                    saved_h=self._lectura_por_copia[(form,name)]
                    if saved_h!=h:
                        raise AltaClienteError('Cambió el control de '+name+'. No se acepta el alta.')
                    self._enfocar_campo(main,form,name,h)
                    value=self._texto.copiar(h,lambda:self._verificar_foco(main,form,name,h))
                else:
                    value=self._read(h)
                if name in ('rfc','idcif','telefono','correo'):
                    value=normalizar_campo(name,value)
            except TextoControlError as exc:
                raise LecturaControlAltaError('No se confirmó una lectura nueva de '+name+
                    '. Revisa Polaris; no se acepta el alta.') from exc
            except LecturaControlAltaError:
                if name!='numero':
                    raise
                value=None
            if name=='numero':
                # Static vacío no acredita un campo vacío: puede ser un dibujo.
                cls=self.bot._class_name(h).upper()
                editable_class='EDIT' in cls or 'MEMO' in cls
                if (value is None or not re.fullmatch(r'[0-9]*',value)
                        or (value=='' and not editable_class)):
                    value=self._numero_uia(form)
            result[name]=value
        if result.get('numero') is None:
            self._diagnosticar_controles(form,'numero')
            if form not in self._aviso_numero_formularios:
                self._aviso_numero_formularios.add(form)
                self.bot.log('ALTA: Número no legible en Windows; NO se considera vacío. '
                             'Para preparar se exigirá Nuevo / Insert Record explícito. '
                             'Nunca se escribe en Número.')
        return result

    def _focus_handle(self,form):
        try:
            obj=pmod.Desktop(backend='win32').window(handle=form).get_focus()
            return obj.handle
        except Exception:
            # Alternativa Win32 documentada; no usa GetFocus() del hilo Python.
            import ctypes
            from ctypes import wintypes as wt
            class GUIINFO(ctypes.Structure):
                _fields_=[('cbSize',wt.DWORD),('flags',wt.DWORD),
                          ('hwndActive',wt.HWND),('hwndFocus',wt.HWND),
                          ('hwndCapture',wt.HWND),('hwndMenuOwner',wt.HWND),
                          ('hwndMoveSize',wt.HWND),('hwndCaret',wt.HWND),('rcCaret',wt.RECT)]
            fn=ctypes.WinDLL('user32',use_last_error=True).GetGUIThreadInfo
            fn.argtypes=[wt.DWORD,ctypes.POINTER(GUIINFO)];fn.restype=wt.BOOL
            g=GUIINFO();g.cbSize=ctypes.sizeof(GUIINFO)
            tid=pmod.win32process.GetWindowThreadProcessId(form)[0]
            return g.hwndFocus if fn(tid,ctypes.byref(g)) else None

    def _verificar_foco(self,main,form,name,h):
        """Validación sin activar ventanas: detenerse si el operador/aviso robó foco."""
        pmod.pyautogui.failSafeCheck()
        if (not self.bot._window_exists_visible(form) or not self.wg.IsWindowEnabled(form)
                or not self.bot._window_exists_visible(h) or not self.wg.IsWindowEnabled(h)):
            raise AltaClienteError('Se cerró o bloqueó el campo '+name+'. No se envían más datos.')
        if not self.bot._is_descendant_or_same(form,h):
            raise AltaClienteError('El control ya no pertenece al alta. No se continúa.')
        foreground=self.wg.GetForegroundWindow()
        focus=self._focus_handle(form)
        if (not foreground or not self.bot._same_polaris_process(main,foreground)
                or (focus!=h and not (focus and self.bot._is_descendant_or_same(h,focus)))):
            raise AltaClienteError('Se perdió el foco de '+name+'. No se envían más datos.')
        _,_,under=self._point(form,self.CAMPOS[name])
        if under!=h and not self.bot._is_descendant_or_same(h,under):
            raise AltaClienteError('Cambió el control situado en '+name+'. No se continúa.')

    def _enfocar_campo(self,main,form,name,h):
        found,x,y=self._field(main,form,name)
        if found!=h:
            raise AltaClienteError('Cambió el control '+name+'. No se escribirá en otro campo.')
        if not self.wg.IsWindowEnabled(h):
            raise AltaClienteError('El campo '+name+' no está habilitado.')
        pmod.pyautogui.click(x,y)
        end=time.monotonic()+1.2
        while True:
            focus=self._focus_handle(form)
            if focus==h or (focus and self.bot._is_descendant_or_same(h,focus)):
                self._verificar_foco(main,form,name,h)
                return
            if time.monotonic()>=end:
                raise AltaClienteError('El foco no quedó en '+name+'. No se capturaron datos.')
            time.sleep(.08)

    def _confirmar_campo(self,main,form,name,h,value,lecturas):
        expected=normalizar_campo(name,value)
        end=time.monotonic()+2.4
        stable=0
        # Dos lecturas consecutivas, no una lectura inmediata tras pegar.
        while True:
            self._verificar_foco(main,form,name,h)
            try:
                actual=self._read(h)
                matches=normalizar_campo(name,actual)==expected
                item={'canal':'WM_GETTEXT','longitud':len(actual),'coincide':matches}
                stable=stable+1 if matches else 0
            except LecturaControlAltaError:
                stable=0
                item={'canal':'WM_GETTEXT','disponible':False,
                      'error':self._ultimo_error_lectura}
            lecturas.append(item)
            if stable>=2:
                self._lectura_por_copia.pop((form,name),None)
                return 'WM_GETTEXT'
            if time.monotonic()>=end:
                break
            time.sleep(.15)
        # Una lectura VCL vacía/distinta no certifica un pegado fallido.
        # Copiar de nuevo, con secuencia y propietario, prueba qué hay en el Edit.
        try:
            actual=self._texto.copiar(h,lambda:self._verificar_foco(main,form,name,h))
            matches=normalizar_campo(name,actual)==expected
            lecturas.append({'canal':'COPIA_NUEVA','longitud':len(actual),'coincide':matches})
            if matches:
                self._lectura_por_copia[(form,name)]=h
                return 'COPIA_NUEVA'
        except TextoControlError as exc:
            lecturas.append({'canal':'COPIA_NUEVA','disponible':False,
                              'error':{'codigo':exc.codigo,'winerror':exc.winerror}})
            if exc.codigo in ('COPIA_DE_OTRA_APLICACION','COPIA_CAMBIO_DURANTE_LECTURA'):
                raise AltaClienteError('Otra aplicación cambió el portapapeles durante la verificación. '
                                       'Se detiene el alta sin Aceptar.') from exc
        return None

    def _diagnostico_captura(self,form,name,h,value,lecturas,resultado):
        """Solo metadatos, sin texto del control ni RFC/correo/teléfono/idCIF."""
        try:
            self._diagnosticar_controles(form,name)
            data={'version':'3.1.5','campo':name,'resultado':resultado,
                  'clase_control':self.bot._class_name(h),'longitud_esperada':len(str(value)),
                  'lecturas':lecturas[-12:],
                  'nota':'Sin valores ni hashes de datos del cliente. No prueba alta guardada.'}
            path=Path(self.bot.base)/'logs'/'diagnostico_alta_captura.json'
            path.parent.mkdir(parents=True,exist_ok=True)
            tmp=path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            tmp.replace(path)
        except Exception:
            self.bot.log('ALTA: no se pudo guardar el diagnóstico local de captura.')

    def escribir(self,main,form,name,value):
        if name not in ('rfc','idcif','telefono','correo'):
            raise AltaClienteError('Este módulo solo captura RFC, idCIF, teléfono y correo.')
        self.exigir_nuevo(main,form)
        h,_,_=self._field(main,form,name)
        self._enfocar_campo(main,form,name,h)
        guard=lambda:self._verificar_foco(main,form,name,h)
        lecturas=[]
        try:
            for intento in (0,1):
                if intento:
                    self.bot.log('ALTA: reintento único de '+name+' con teclado y foco verificado.')
                    self.exigir_nuevo(main,form)
                    self._enfocar_campo(main,form,name,h)
                try:
                    if not intento:
                        self._texto.pegar(h,str(value),guard)
                    else:
                        self._texto.teclear(h,str(value),guard)
                except TextoControlError as exc:
                    lecturas.append({'canal':'PEGADO' if not intento else 'TECLADO',
                                      'error':{'codigo':exc.codigo,'winerror':exc.winerror}})
                    if exc.winerror==5:
                        raise AltaClienteError('Windows negó acceso al control. Revisa que Polaris y el bot '
                            'se ejecuten con el mismo nivel de permisos. No se pulsa Aceptar.') from exc
                canal=self._confirmar_campo(main,form,name,h,value,lecturas)
                if canal:
                    self._diagnostico_captura(form,name,h,value,lecturas,'CONFIRMADA_'+canal)
                    self.bot.log('ALTA: '+name+' confirmado mediante '+canal+'. No se pulsó Aceptar.')
                    return
            raise AltaClienteError('No se confirmó el texto de '+name+' después del pegado y el reintento. '
                'No se continúa a Aceptar. Diagnóstico: logs/diagnostico_alta_captura.json. '
                'Revisa el contenido visible en Polaris antes de repetir.')
        except Exception:
            self._diagnostico_captura(form,name,h,value,lecturas,'DETENIDA_SIN_ACEPTAR')
            raise

    def _find_button(self,root,wanted):
        target=self.bot._norm(wanted.replace('&',''))
        found=[]
        def cb(h,_):
            if not self.bot._window_exists_visible(h): return
            cls=self.bot._class_name(h).upper()
            if not any(k in cls for k in ('BUTTON','BITBTN','SPEEDBUTTON')): return
            caption=(self.wg.GetWindowText(h) or '').replace('&','')
            if self.bot._norm(caption)!=target: return
            r=self.bot._rect(h)
            if r.width>12 and r.height>12: found.append(h)
        self.wg.EnumChildWindows(root,cb,None)
        if len(found)>1:
            raise AltaClienteError('Hay más de un botón '+wanted+' en el formulario. Revisión requerida.')
        return found[0] if found else None

    def _button(self,main,form,text,rel,*,critical=False):
        self._guard(main,form)
        h=self._find_button(form,text)
        if h and self.wg.IsWindowEnabled(h):
            cls=self.bot._class_name(h).upper()
            if any(k in cls for k in ('BUTTON','BITBTN','SPEEDBUTTON')):
                r=self.bot._rect(h);xy=((r.left+r.right)//2,(r.top+r.bottom)//2)
                under=self.wg.WindowFromPoint(xy)
                if under==h or self.bot._is_descendant_or_same(h,under): return xy
        if h and not self.wg.IsWindowEnabled(h):
            raise AltaClienteError('El botón '+text+' está deshabilitado. No se hará clic.')
        if critical:
            raise AltaClienteError('No se identificó el botón '+text+' habilitado. No se hará clic a ciegas.')
        x,y,_=self._point(form,rel)
        return x,y

    def _mode_text(self,main,form):
        self._guard(main,form)
        # El estado se toma SOLO de la barra de estado. Un Nombre/Comentario
        # que diga "Insert Record" no acredita que se haya pulsado Nuevo.
        texts=[]
        try:
            obj=pmod.Desktop(backend='win32').window(handle=form).wrapper_object()
            for c in obj.descendants():
                if 'STATUSBAR' in c.class_name().upper():
                    texts.extend(c.texts())
        except Exception:
            pass
        return self.bot._norm(' '.join(str(x) for x in texts))

    def _new_enabled(self,form):
        h=self._find_button(form,'Nuevo')
        return bool(h and self.wg.IsWindowEnabled(h))

    def exigir_nuevo(self,main,form):
        self._guard(main,form)
        m=self._mode_text(main,form)
        vals=self.snapshot(main,form)
        if 'EDITRECORD' in m or 'BROWSERECORD' in m or vals['numero']:
            raise AltaClienteError('La pantalla no está en un alta nueva sin guardar. No se toca el registro.')
        if vals['numero'] is None and 'INSERTRECORD' not in m:
            raise AltaClienteError('Número no es legible y no se confirmó Nuevo / Insert Record. '
                                   'No se captura ni se acepta el alta.')
        if 'INSERTRECORD' not in m:
            # Con VCL sin statusbar legible, exige campos vacíos de número,
            # Cancelar/Aceptar habilitados y Nuevo deshabilitado identificable.
            nh=self._find_button(form,'Nuevo')
            ah=self._find_button(form,'Aceptar')
            ch=self._find_button(form,'Cancelar')
            if not (nh and not self.wg.IsWindowEnabled(nh) and ah and ch
                    and self.wg.IsWindowEnabled(ah) and self.wg.IsWindowEnabled(ch)):
                raise AltaClienteError('No se pudo confirmar el modo Nuevo/Insert Record. No se acepta.')

    def proteger_edicion_abierta(self,main):
        # Proteger también un cliente de crédito abierto por el operador antes
        # de ejecutar la limpieza común. NO usarlo como formulario de alta.
        for h in self.bot._cleanup_windows(main):
            title=self.wg.GetWindowText(h) or ''
            if not re.fullmatch(r'\s*Clientes de (?:Efectivo|Cr[eé]dito)\s*',title,re.I):
                continue
            texts=[self.bot._window_tree_text(h)]
            try:
                obj=pmod.Desktop(backend='win32').window(handle=h).wrapper_object()
                for c in obj.descendants():
                    if 'STATUS' in c.class_name().upper(): texts.extend(c.texts())
            except Exception: pass
            mode=self.bot._norm(' '.join(str(x) for x in texts))
            ah=self._find_button(h,'Aceptar')
            if ('INSERTRECORD' in mode or 'EDITRECORD' in mode
                    or (ah and self.wg.IsWindowEnabled(ah))):
                raise AltaClienteError('Hay un cliente en captura/edición. Guarda o cancela manualmente '
                                       'antes de preparar otra alta; el bot no descarta sus datos.')

    def _scale(self,main):
        try:
            import ctypes
            from ctypes import wintypes as wt
            fn=ctypes.windll.user32.GetDpiForWindow
            fn.argtypes=[wt.HWND];fn.restype=wt.UINT
            return (fn(main) or 96)/96.0
        except Exception: return 1.0

    def _acceso_efectivo(self,main):
        # Preferir el nombre exacto si el botón expone un HWND/texto.
        h=self._find_button(main,'Clientes de Efectivo')
        if h:
            if not self.wg.IsWindowEnabled(h):
                raise AltaClienteError('El acceso a Clientes de Efectivo está deshabilitado.')
            r=self.bot._rect(h)
            return (r.left+r.right)//2,(r.top+r.bottom)//2
        # Video 21:37, segundo 0.9: PRIMER icono (hoja), no el segundo
        # (dos personas). Medir la barra permite mover/escalar la ventana.
        mainrect=self.bot._rect(main)
        bars=[]
        def cb(ch,_):
            if not self.bot._window_exists_visible(ch): return
            if 'TOOLBAR' not in self.bot._class_name(ch).upper(): return
            r=self.bot._rect(ch)
            if (r.width>250 and 20<=r.height<=120 and
                    mainrect.top<=r.top<mainrect.top+190 and
                    mainrect.left<=r.left<mainrect.left+50):
                bars.append((r.top,r.height,ch,r))
        self.wg.EnumChildWindows(main,cb,None)
        if bars:
            bars.sort(key=lambda item:(item[0],item[1]))
            r=bars[0][3]
            return r.left+round(r.height/2),(r.top+r.bottom)//2
        # VCL puede dibujar los iconos dentro de un panel sin toolbar nativa.
        # GetClientRect/ClientToScreen EXCLUYEN título/menú: centro del primer
        # icono a (20,22) lógicos dentro del cliente de la principal (video).
        scale=self._scale(main)
        w=self.wg.GetClientRect(main)[2]
        if w<400:
            raise AltaClienteError('Polaris no tiene tamaño suficiente para localizar Clientes de Efectivo.')
        return self.wg.ClientToScreen(main,(round(20*scale),round(22*scale)))

    def abrir(self,main):
        h=self._form(main)
        if h:
            self._guard(main,h)
            self.bot.log('ALTA: se reconoció la ventana Clientes de Efectivo ya abierta.')
            return h
        self.bot._activate(main,maximize=True)
        self.bot._check_cleanup_prompt(main)
        if self.bot._popup_candidates(main):
            raise AltaClienteError('Hay otra ventana de Polaris abierta. No se pulsa la barra de Clientes.')
        fg=self.wg.GetForegroundWindow()
        if not self.bot._same_polaris_process(main,fg):
            raise AltaClienteError('No se pudo activar Polaris para abrir Clientes de Efectivo.')
        x,y=self._acceso_efectivo(main)
        under=self.wg.WindowFromPoint((x,y))
        if not under or not self.bot._is_descendant_or_same(main,under):
            raise AltaClienteError('Otro programa tapa el acceso a Clientes de Efectivo. No se hará clic.')
        cls=self.bot._class_name(under).upper()
        if any(k in cls for k in ('EDIT','MEMO','COMBO','GRID')):
            raise AltaClienteError('El acceso calculado cayó en un campo de datos, no en la barra. No se hará clic.')
        self.bot.log('ALTA: abriendo Clientes de Efectivo con el primer icono de la barra.')
        pmod.pyautogui.click(x,y)
        end=time.monotonic()+12
        while time.monotonic()<end:
            h=self._form(main)
            if h:
                self._guard(main,h)
                self.bot.log('ALTA: Clientes de Efectivo reconocido; se continúa a Nuevo.')
                return h
            # No continuar en crédito ni en cualquier otro catálogo por parecido.
            for row in self.bot._popup_candidates(main):
                if 'CLIENTESDECREDITO' in self.bot._norm(row[3]):
                    raise AltaClienteError('Se abrió Clientes de Crédito, no Clientes de Efectivo. '
                                           'No se presionó Nuevo ni se capturaron datos.')
            self.bot._check_cleanup_prompt(main)
            time.sleep(.25)
        raise AltaClienteError('No se reconoció Clientes de Efectivo después de abrirlo. '
                               'No se presionó Nuevo ni se capturaron datos.')

    def nuevo(self,main,form):
        self._guard(main,form)
        mode=self._mode_text(main,form)
        if 'INSERTRECORD' in mode or 'EDITRECORD' in mode:
            raise AltaClienteError('Ya hay una captura/edición en curso. No se vuelve a presionar Nuevo.')
        if 'BROWSERECORD' not in mode and not self._new_enabled(form):
            raise AltaClienteError('No se confirmó que el formulario permita Nuevo. No se hace clic.')
        self._lectura_por_copia={k:v for k,v in self._lectura_por_copia.items() if k[0]!=form}
        xy=self._button(main,form,'Nuevo',self.NUEVO)
        pmod.pyautogui.click(*xy)
        # Se espera el CAMBIO DE MODO, no solamente una pausa fija.
        end=time.monotonic()+6
        while time.monotonic()<end:
            mode=self._mode_text(main,form)
            if 'INSERTRECORD' in mode:
                self.exigir_nuevo(main,form)
                return
            if 'EDITRECORD' in mode:
                raise AltaClienteError('Polaris entró en Editar en vez de Nuevo. No se capturan datos.')
            if 'BROWSERECORD' not in mode and not self._new_enabled(form):
                self.exigir_nuevo(main,form)
                return
            time.sleep(.2)
        raise AltaClienteError('Nuevo no cambió el formulario a Insert Record. No se sobrescribió ningún cliente.')

    def consultar_idcif(self,main,form):
        self._guard(main,form)
        x,y,_=self._point(form,self.LUPA)
        pmod.pyautogui.click(x,y);time.sleep(.35)

    def esperar_fiscales(self,main,form,sol):
        end=time.monotonic()+40
        previous=None;stable=0
        while time.monotonic()<end:
            vals=self.snapshot(main,form)
            if vals['rfc'].strip().upper()!=sol.rfc or vals['idcif'].strip()!=sol.idcif:
                raise AltaClienteError('Polaris cambió el RFC/idCIF al consultar. Revisa los datos; no se guarda.')
            if vals['nombre'].strip() and re.fullmatch(r'[0-9]{5}',vals['cp']):
                key=(vals['nombre'],vals['cp'])
                stable=stable+1 if previous==key else 1;previous=key
                if stable>=2: return vals
            time.sleep(.6)
        raise AltaClienteError('La lupa de idCIF no completó nombre y CP dentro del tiempo de espera. '
                               'Revisa RFC/idCIF y cualquier aviso de Polaris. No se presionó Aceptar.')

    def verificar_contexto(self,main,form,station,pid):
        if self.bot._pid(main)!=pid or not self.bot._station_is_active(main,station):
            raise AltaClienteError('Cambió el proceso o la estación de Polaris. No se acepta el alta.')
        self._guard(main,form)

    def validar_aceptar(self,main,form):
        self._button(main,form,'Aceptar',self.ACEPTAR,critical=True)

    def aceptar(self,main,form):
        xy=self._button(main,form,'Aceptar',self.ACEPTAR,critical=True)
        pmod.pyautogui.click(*xy)  # Único clic que solicita guardar el cliente; no timbra.
        time.sleep(.6)

    def verificar_guardado(self,main,form,sol):
        end=time.monotonic()+15
        while time.monotonic()<end:
            self._guard(main,form)
            vals=self.snapshot(main,form)
            mode=self._mode_text(main,form)
            ah=self._find_button(form,'Aceptar')
            if (vals['rfc'].strip().upper()==sol.rfc and vals['nombre'].strip()
                    and vals.get('numero') is not None
                    and vals['numero'].isdigit() and int(vals['numero'])>0
                    and 'BROWSERECORD' in mode and ah and not self.wg.IsWindowEnabled(ah)):
                return vals['numero']
            time.sleep(.5)
        raise AltaClienteError('Se solicitó Aceptar, pero no se confirmó el número de cliente guardado. '
                               'Revisa Polaris antes de reintentar. El bot NO repetirá el clic.')
