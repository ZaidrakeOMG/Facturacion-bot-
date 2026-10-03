"""Alertas internas con cola LOCAL. No conoce ni abre la base de datos Polaris."""
from __future__ import annotations
import copy
from contextlib import contextmanager
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from .alertas_modelo import (normalizar_config, destinatarios_internos, contexto_seguro,
                             limpiar_texto, huella_evento, contenido_correo, CATEGORIAS)
from .alertas_ventana import LectorVentana


class ColaAlertas:
    def __init__(self, base):
        self.base = Path(base)
        self.folder = self.base/'data'/'alertas_internas'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder/'cola.sqlite3'
        self.lock = threading.RLock()
        with self.lock, self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS alertas (
                id TEXT PRIMARY KEY, huella TEXT NOT NULL, creado REAL NOT NULL,
                actualizado REAL NOT NULL, estado TEXT NOT NULL, datos TEXT NOT NULL,
                intentos INTEGER NOT NULL DEFAULT 0, repeticiones INTEGER NOT NULL DEFAULT 1,
                error_envio TEXT NOT NULL DEFAULT '', gmail_id TEXT NOT NULL DEFAULT '')''')
            db.execute('CREATE INDEX IF NOT EXISTS ix_alertas_huella ON alertas(huella,creado)')
            cols = {r[1] for r in db.execute('PRAGMA table_info(alertas)')}
            if 'proximo_intento' not in cols:
                db.execute('ALTER TABLE alertas ADD COLUMN proximo_intento REAL NOT NULL DEFAULT 0')
            # Un envío interrumpido puede haber llegado a Gmail. Nunca reintentar a ciegas.
            db.execute("UPDATE alertas SET estado='ENVIO_INCIERTO', error_envio=? WHERE estado='ENVIANDO'",
                       ('El programa se cerró durante el envío. Revisa Enviados antes de reenviar.',))

    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=8)
        db.row_factory = sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def guardar(self, event, fingerprint, state, minutos=10, image=None):
        now = time.time()
        with self.lock, self._db() as db:
            row = db.execute('SELECT id FROM alertas WHERE huella=? AND creado>=? ORDER BY creado DESC LIMIT 1',
                             (fingerprint, now-minutos*60)).fetchone()
            if row:
                db.execute('UPDATE alertas SET repeticiones=repeticiones+1, actualizado=? WHERE id=?', (now,row['id']))
                return row['id'], False
            if image:
                path = self.folder/(event['id']+'.png')
                temp = path.with_suffix('.tmp'); temp.write_bytes(image); temp.replace(path)
                event['adjunto'] = path.name
            db.execute('INSERT INTO alertas (id,huella,creado,actualizado,estado,datos) VALUES(?,?,?,?,?,?)',
                       (event['id'], fingerprint, now, now, state, json.dumps(event, ensure_ascii=False)))
            return event['id'], True

    def agrupar_reciente(self, fingerprint, minutos):
        """Evita volver a capturar/OCR el mismo fallo repetido."""
        now=time.time()
        with self.lock, self._db() as db:
            row=db.execute('SELECT id FROM alertas WHERE huella=? AND creado>=? ORDER BY creado DESC LIMIT 1',
                           (fingerprint,now-minutos*60)).fetchone()
            if not row:return None
            db.execute('UPDATE alertas SET repeticiones=repeticiones+1,actualizado=? WHERE id=?',(now,row['id']))
            return row['id']

    def recientes(self, limit=100):
        with self.lock, self._db() as db:
            rows = db.execute('SELECT * FROM alertas ORDER BY creado DESC LIMIT ?', (int(limit),)).fetchall()
        return [self._row(r) for r in rows]

    def _row(self, row):
        result = dict(row); result['evento'] = json.loads(result.pop('datos')); return result

    def obtener(self, ident):
        with self.lock, self._db() as db:
            row = db.execute('SELECT * FROM alertas WHERE id=?', (ident,)).fetchone()
        return self._row(row) if row else None

    def reservar(self):
        with self.lock, self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM alertas WHERE estado='PENDIENTE' AND proximo_intento<=? ORDER BY creado LIMIT 1",(time.time(),)).fetchone()
            if not row: return None
            db.execute("UPDATE alertas SET estado='ENVIANDO', intentos=intentos+1, actualizado=? WHERE id=?",
                       (time.time(), row['id']))
            result = self._row(row)
            result['intentos'] = int(row['intentos']) + 1
            return result

    def estado(self, ident, state, error='', gmail_id=''):
        with self.lock, self._db() as db:
            db.execute('UPDATE alertas SET estado=?,error_envio=?,gmail_id=?,actualizado=? WHERE id=?',
                       (state, limpiar_texto(error, 900), gmail_id, time.time(), ident))

    def posponer(self, ident, segundos, detalle):
        now = time.time()
        with self.lock, self._db() as db:
            db.execute("UPDATE alertas SET estado='PENDIENTE',error_envio=?,proximo_intento=?,actualizado=? WHERE id=? AND estado='ENVIANDO'",
                       (limpiar_texto(detalle, 900), now + max(1, segundos), now, ident))

    def reencolar(self, ident, recipients):
        with self.lock, self._db() as db:
            row = db.execute('SELECT datos,estado FROM alertas WHERE id=?', (ident,)).fetchone()
            if not row: raise ValueError('No se encontró la alerta.')
            if row['estado'] in ('PENDIENTE', 'ENVIANDO'): raise ValueError('La alerta ya está en cola o en envío.')
            event = json.loads(row['datos']); event['destinatarios'] = destinatarios_internos(recipients)
            event['reenvio_manual'] = True
            db.execute("UPDATE alertas SET estado='PENDIENTE',datos=?,error_envio='',proximo_intento=0,intentos=0,actualizado=? WHERE id=?",
                       (json.dumps(event, ensure_ascii=False), time.time(), ident))

    def cancelar(self, ident):
        with self.lock, self._db() as db:
            n = db.execute("UPDATE alertas SET estado='CANCELADA' WHERE id=? AND estado='PENDIENTE'", (ident,)).rowcount
        if not n: raise ValueError('Solo se cancela una alerta pendiente, no un envío ya iniciado.')


class AlertasInternas:
    def __init__(self, base, cfg, log, *, sender=None, lector_factory=LectorVentana):
        self.base = Path(base); self.cfg = cfg; self.log = log
        self.path = self.base/'alertas_internas.json'
        self.lock = threading.RLock(); self.config_error = ''
        try:
            self.options = normalizar_config(json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else None)
        except Exception:
            self.options = normalizar_config()
            self.config_error = 'Configuración de alertas inválida. Quedaron desactivadas; revisa y guarda.'
        self.cola = ColaAlertas(base)
        self.sender = sender; self.lector_factory = lector_factory
        self._stop = threading.Event(); self._wake = threading.Event(); self._thread = None
        self.sending = False

    def ajustes(self):
        with self.lock: return copy.deepcopy(self.options)

    def guardar_ajustes(self, data):
        valid = normalizar_config(data)
        with self.lock:
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(valid, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(self.path)
            self.options = valid; self.config_error = ''
        self._wake.set()
        return valid

    def reportar(self, categoria, etapa, error, *, contexto=None, modo_seguro=True, bot=None):
        """Se llama ANTES del messagebox del bot, conservando el aviso de Polaris.
        Los errores del notificador nunca ocultan ni sustituyen el error original.
        """
        try:
            opts = self.ajustes()
            if categoria not in CATEGORIAS or categoria == 'PRUEBA_ALERTA': categoria='SISTEMA'
            if not opts['activas'] or not opts['categorias'].get(categoria, False): return None
            secret = [str((contexto or {}).get('idcif', ''))]
            context = contexto_seguro(contexto, opts['incluir_contexto_cliente'])
            if not opts['incluir_contexto_cliente']:
                secret += [str((contexto or {}).get(k,'')) for k in ('rfc','folio','correo_cliente')]
            err = limpiar_texto(error, secretos=secret)
            step = limpiar_texto(etapa, 400, secretos=secret)
            if not step: step = 'Paso no identificado'
            fingerprint = huella_evento(categoria, step, err, context, modo_seguro)
            prior=self.cola.agrupar_reciente(fingerprint,opts['deduplicar_minutos'])
            if prior:
                self.log('ALERTA INTERNA '+prior[:10]+': repetición agrupada; no se repite OCR ni correo.')
                return prior
            # Sin contexto de cliente: tampoco se adjunta imagen que pudiera contenerlo.
            capture_opts = dict(opts)
            if not opts['incluir_contexto_cliente']: capture_opts['adjuntar_ventana'] = False
            evid, image = self.lector_factory(bot, self.cfg).leer(capture_opts, secretos=secret)
            event = self._event(categoria, step, err, context, bool(modo_seguro), opts['destinatarios'], evid)
            state = 'SIMULADA' if modo_seguro and not opts['enviar_en_modo_seguro'] else 'PENDIENTE'
            # No repetir alerta idéntica para la misma solicitud durante el intervalo.
            fingerprint = huella_evento(categoria, step, err, context, modo_seguro)
            ident, new = self.cola.guardar(event, fingerprint, state, opts['deduplicar_minutos'], image)
            self.log('ALERTA INTERNA '+ident[:10]+': '+(state if new else 'repetición agrupada')+'.')
            self._wake.set()
            return ident
        except Exception:
            self.log('No fue posible guardar la alerta interna. El error original requiere revisión en pantalla.')
            return None

    def _event(self, category, stage, error, context, safe, recipients, evidence):
        return {'id': uuid.uuid4().hex, 'fecha': datetime.now().astimezone().isoformat(timespec='seconds'),
                'categoria': category, 'etapa': stage, 'error': error, 'contexto': context,
                'modo_seguro': bool(safe), 'destinatarios': destinatarios_internos(recipients),
                'evidencia': evidence, 'adjunto': ''}

    def prueba(self, *, enviar=False, autorizado=False):
        opts = self.ajustes()
        if enviar and not autorizado: raise ValueError('Falta confirmar el correo real de prueba.')
        if enviar and (not opts['activas'] or not opts['destinatarios']):
            raise ValueError('Guarda y activa los destinatarios internos antes de enviar.')
        event = self._event('PRUEBA_ALERTA', 'Comprobación de alertas internas',
                            'PRUEBA SIMULADA. No ocurrió un error real y no se operó Polaris.',
                            {'origen':'Prueba interna sin datos de clientes'}, True, opts['destinatarios'],
                            {'metodo':'SIMULACION', 'texto':'Ejemplo: no se confirmó la captura del RFC.',
                             'nota':'No se leyó ni se fotografió ninguna ventana real.'})
        ident, _ = self.cola.guardar(event, uuid.uuid4().hex, 'PENDIENTE' if enviar else 'SIMULADA')
        self._wake.set(); return ident

    def reenviar(self, ident, *, autorizado=False):
        if not autorizado: raise ValueError('Confirma el reenvío real.')
        opts = self.ajustes()
        if not opts['activas'] or not opts['destinatarios']: raise ValueError('Activa las alertas y guarda destinatarios.')
        self.cola.reencolar(ident, opts['destinatarios']); self._wake.set()

    def _get_sender(self):
        if self.sender is None:
            from .alertas_correo import CorreoAlertasGmail
            self.sender = CorreoAlertasGmail(self.base, self.cfg)
        return self.sender

    def enviar_una(self):
        """Solo la usa el hilo de alertas. Nunca hace reintentos de Polaris."""
        opts = self.ajustes()
        if not opts['activas']: return False
        row = self.cola.reservar()
        if not row: return False
        event = row['evento']; ident = row['id']; self.sending = True
        try:
            recipients = destinatarios_internos(event['destinatarios'])
            # Una baja/cambio de destinatarios NO permite enviar a los anteriores sin revisar.
            if not recipients or any(x not in opts['destinatarios'] for x in recipients):
                self.cola.estado(ident, 'REVISAR_DESTINATARIOS', 'Cambió la lista de destinatarios. Reenvía manualmente tras revisarla.')
                return True
            subject, body = contenido_correo(event)
            attachment = None
            if event.get('adjunto') and opts['adjuntar_ventana'] and opts['incluir_contexto_cliente']:
                candidate = self.cola.folder/event['adjunto']
                if candidate.resolve().parent != self.cola.folder.resolve() or candidate.suffix != '.png':
                    raise ValueError('Adjunto no permitido.')
                if candidate.exists() and candidate.stat().st_size <= 3_000_000:
                    attachment = candidate.read_bytes()
            result = self._get_sender().enviar(recipients, subject, body, ident, attachment)
            if not isinstance(result, dict) or not result.get('id'):
                self.cola.estado(ident, 'ENVIO_INCIERTO', 'Gmail no devolvió confirmación identificable. Revisa Enviados.')
            else:
                self.cola.estado(ident, 'ENVIADA', gmail_id=str(result['id']))
                self.log('ALERTA INTERNA '+ident[:10]+': aceptada por Gmail; recepción no verificada.')
        except Exception as exc:
            from .alertas_correo import ErrorEnvioAlerta
            if isinstance(exc, ErrorEnvioAlerta) and exc.reintentable and int(row.get('intentos', 1)) < 6:
                self.cola.posponer(ident, exc.espera, str(exc))
                self.log('ALERTA INTERNA '+ident[:10]+f': pendiente; reintento de correo en {exc.espera} s. No se repite Polaris.')
                return True
            if isinstance(exc, ErrorEnvioAlerta):
                state = 'ENVIO_INCIERTO' if exc.incierto else 'ERROR_ENVIO'
                detail = str(exc)
            else:
                state = 'ENVIO_INCIERTO'
                detail = 'Fallo no confirmado del envío. Revisa Gmail Enviados antes de reenviar.'
            self.cola.estado(ident, state, detail)
            self.log('ALERTA INTERNA '+ident[:10]+': '+state+'. Se conserva localmente, sin reenvío automático.')
        finally: self.sending = False
        return True

    def iniciar(self):
        if self._thread and self._thread.is_alive(): return
        self._stop.clear()
        def run():
            while not self._stop.is_set():
                try:
                    if self.enviar_una(): continue
                except Exception:
                    self.log('Cola de alertas no disponible. Revisa el apartado Alertas internas.')
                self._wake.wait(2); self._wake.clear()
        self._thread = threading.Thread(target=run, daemon=True, name='ARY-Alertas-Internas')
        self._thread.start()

    def detener(self):
        self._stop.set(); self._wake.set()
