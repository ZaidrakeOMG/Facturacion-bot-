"""Tramo de pantalla: Aceptar factura -> Enviar por Correo -> Aceptar envío.

No usa SQL, SP, red ni API de correo. Polaris realiza la emisión y el envío.
Los pasos irreversibles se intentan una sola vez. El historial es un archivo
LOCAL del bot; no es evidencia de vigencia SAT ni de recepción del correo.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .dialogos_cfdi import (ControlDialogo, PROGRESOS, clasificar_dialogo,
                            es_candidato, es_campo)


class FinalFacturaError(RuntimeError):
    pass

class FacturaYaProcesada(FinalFacturaError):
    """Solo una respuesta afirmativa del aviso de Polaris, no un estado de la cola."""
    def __init__(self, message, *, confirmado_polaris=False):
        super().__init__(message)
        self.confirmado_polaris = bool(confirmado_polaris)


def validar_correo(value):
    email = str(value or '').strip()
    if (len(email) > 254 or not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", email)
            or '..' in email or email.startswith('.') or '.@' in email):
        raise FinalFacturaError('Captura un solo correo destino válido antes de facturar. No se pulsó Aceptar.')
    return email


class RegistroFinalFactura:
    """No almacena RFC/correo. Reserva LOCAL antes del primer clic final.

    El bloqueo es por estación + folio normalizado, incluso tras reiniciar el
    bot. No se libera automáticamente ante un resultado incierto.
    """
    ESTADOS = frozenset(('ACEPTAR_INTENTADO', 'DIALOGO_ENVIO', 'ENVIAR_INTENTADO', 'ENVIO_SOLICITADO'))

    def __init__(self, base, estacion, ticket):
        from .parser import normalize_station
        station = normalize_station(estacion)
        folio = str(ticket or '').strip()
        if not station or not re.fullmatch(r'[0-9]{1,20}', folio):
            raise FinalFacturaError('Selecciona una estación y un folio numérico válido.')
        key = hashlib.sha256((station + '|' + str(int(folio))).encode('utf-8')).hexdigest()
        self.path = Path(base) / 'data' / 'control_facturacion' / (key + '.json')

    def comprobar_libre(self):
        if self.path.exists():
            try:
                state = json.loads(self.path.read_text(encoding='utf-8')).get('estado','')
            except Exception:
                state = ''
            if state == 'ENVIO_SOLICITADO':
                raise FinalFacturaError(
                    'Existe un envío solicitado en el historial LOCAL. No equivale a una consulta a Polaris. '
                    'No se pulsará Aceptar otra vez: revise/recupere el CFDI, sin volver a emitirlo.')
            raise FinalFacturaError(
                'Este bot ya registró un intento de Aceptar para esta estación y folio con resultado no concluyente. '
                'No volverá a timbrar automáticamente. Revisa la factura y el envío en Polaris; '
                'un problema de correo NO requiere volver a facturar. No borres el registro para repetir.')

    @staticmethod
    def _data(estado):
        if estado not in RegistroFinalFactura.ESTADOS:
            raise FinalFacturaError('Estado interno de facturación no válido.')
        return {'version': 1, 'estado': estado,
                'fecha_utc': datetime.now(timezone.utc).isoformat(),
                'alcance': 'intento local; no comprueba vigencia ni recepción'}

    def iniciar(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # x: nunca sobrescribe una reserva hecha por otro intento/instancia.
            with self.path.open('x', encoding='utf-8') as f:
                json.dump(self._data('ACEPTAR_INTENTADO'), f, ensure_ascii=False)
                f.flush(); os.fsync(f.fileno())
        except FileExistsError:
            self.comprobar_libre()
        except OSError as e:
            raise FinalFacturaError('No pude registrar el intento localmente. No se pulsará Aceptar.') from e

    def actualizar(self, estado):
        if not self.path.is_file():
            raise FinalFacturaError('Falta el registro local del intento. Revisa Polaris; no se repetirá Aceptar.')
        tmp = self.path.with_suffix('.tmp')
        try:
            with tmp.open('w', encoding='utf-8') as f:
                json.dump(self._data(estado), f, ensure_ascii=False)
                f.flush(); os.fsync(f.fileno())
            tmp.replace(self.path)
        except OSError as e:
            raise FinalFacturaError('No pude actualizar el estado local. Revisa Polaris antes de continuar.') from e


class PantallaFinalFactura:
    """Controles acotados al proceso y al diálogo exacto, no al MDI detrás."""
    TITULO_ENVIO = 'ENVIOEIMPRESIONDECFDI'

    def __init__(self, bot, *, texto=None, reloj=None, dormir=None, captura=None):
        from .alta_texto import TextoWindows
        self.b = bot
        self.texto = texto if texto is not None else TextoWindows()
        self.reloj = reloj or time.monotonic
        self.dormir = dormir or time.sleep
        self._captura_correo = captura
        self._eventos_espera = []
        self._ultimo_progreso = None
        self._progreso_log_en = -1e9

    @property
    def win(self):
        from . import polaris
        return polaris.win32gui

    def _produccion(self):
        if self.b.cfg.get('app', {}).get('modo_prueba', True) is not False:
            raise FinalFacturaError('MODO SEGURO activo: no se pulsa Aceptar ni se envía correo.')

    def _guard(self, main, root):
        if (not self.b._window_exists_visible(root)
                or not self.b._same_polaris_process(main, root)
                or not self.win.IsWindowEnabled(root)):
            raise FinalFacturaError('La ventana esperada de Polaris no está disponible. No se repetirá Aceptar.')
        fg = self.win.GetForegroundWindow()
        if not fg or not self.b._same_polaris_process(main, fg):
            raise FinalFacturaError('Otra aplicación tomó el foco. Revisa Polaris antes de continuar.')

    def _children(self, root):
        result = []
        def cb(h, _):
            if (self.b._window_exists_visible(h) and self.b._same_polaris_process(root, h)
                    and self.b._is_descendant_or_same(root, h)):
                result.append(h)
        self.win.EnumChildWindows(root, cb, None)
        return result

    def _caption(self, h):
        return self.b._norm((self.win.GetWindowText(h) or '').replace('&', ''))

    def _named(self, root, text, kind='button'):
        matches = []
        target = self.b._norm(text)
        for h in self._children(root):
            cls = self.b._class_name(h).upper()
            is_button = any(k in cls for k in ('BUTTON', 'BITBTN', 'CHECKBOX', 'RADIO'))
            if is_button and self._caption(h) == target:
                matches.append(h)
        if len(matches) != 1:
            raise FinalFacturaError('No pude identificar un único control "' + text + '". No se pulsa a ciegas.')
        h = matches[0]
        if not self.win.IsWindowEnabled(h):
            raise FinalFacturaError('El control "' + text + '" está deshabilitado en Polaris.')
        return h

    def _click(self, main, root, h):
        from . import polaris
        self._guard(main, root)
        if (not self.b._is_descendant_or_same(root, h)
                or not self.b._window_exists_visible(h) or not self.win.IsWindowEnabled(h)):
            raise FinalFacturaError('El control de Polaris cambió antes del clic. Operación detenida.')
        r = self.b._rect(h); x = (r.left + r.right)//2; y = (r.top + r.bottom)//2
        under = self.win.WindowFromPoint((x, y))
        if not self.b._is_descendant_or_same(h, under):
            raise FinalFacturaError('Otra ventana cubre el botón esperado. No se pulsó Aceptar.')
        # UN SOLO clic, nunca doble clic, Enter global ni reintento final.
        polaris.pyautogui.click(x, y)
        self.dormir(.25)

    def buscar_envio(self, main):
        matches = []
        for h in self.b._cleanup_windows(main):
            if self._caption(h) != self.TITULO_ENVIO:
                continue
            r = self.b._rect(h)
            # El diálogo grabado es aprox. 220 x 284, NO de 180000 px de área.
            if r.width >= 150 and r.height >= 150:
                matches.append(h)
        matches = list(dict.fromkeys(matches))
        if len(matches) > 1:
            raise FinalFacturaError('Hay varios diálogos de envío abiertos. Revisa Polaris; no se elige uno al azar.')
        return matches[0] if matches else None

    def _dialogos(self, main, allowed):
        """Instantánea del proceso Polaris; valores de campos no se leen aquí."""
        result = []
        for h in self.b._cleanup_windows(main):
            if h in allowed or not es_candidato(self.b._class_name(h)):
                continue
            if not self.b._window_exists_visible(h) or not self.b._same_polaris_process(main, h):
                continue
            r = self.b._rect(h)
            if r.width <= 120 or r.height <= 24:
                continue
            try:
                controls = []
                for child in self._children(h):
                    cls = self.b._class_name(child)
                    # Campos fiscales y de destinatario nunca forman parte del
                    # clasificador ni del diagnóstico de progreso.
                    text = '' if es_campo(cls) else (self.win.GetWindowText(child) or '')
                    controls.append(ControlDialogo(cls, text, bool(self.win.IsWindowEnabled(child))))
                state = clasificar_dialogo(self.b._class_name(h), self.win.GetWindowText(h) or '', controls)
            except Exception as e:
                # La ventana puede desaparecer entre enumeración y lectura.
                if not self.b._window_exists_visible(h):
                    continue
                raise FinalFacturaError('No pude revisar una ventana de Polaris. '
                                        'No se responderá ni repetirá Aceptar automáticamente.') from e
            if state != 'OTRA':
                result.append((h, state))
        return result

    def _aviso(self, main, allowed):
        # Antes del Aceptar inicial, un progreso pendiente TAMBIÉN bloquea:
        # podría pertenecer a una factura iniciada por otro intento.
        windows = self._dialogos(main, allowed)
        notices = [h for h, state in windows if state == 'AVISO']
        return notices[0] if notices else (windows[0][0] if windows else None)

    def _registrar_espera(self, fase, estado):
        event = {'fase': fase, 'estado': estado}
        if not self._eventos_espera or self._eventos_espera[-1] != event:
            self._eventos_espera.append(event)
            self._eventos_espera = self._eventos_espera[-40:]
        now = self.reloj()
        if estado in PROGRESOS and (estado != self._ultimo_progreso or now - self._progreso_log_en >= 5):
            labels = {'PROGRESO_CFDI': 'Polaris está generando/certificando el CFDI',
                      'PROGRESO_ARCHIVOS': 'Polaris está preparando los documentos',
                      'PROGRESO_CORREO': 'Polaris está procesando el envío del correo'}
            self.b.log('ESPERANDO: ' + labels[estado] + '. No se pulsa ni se repite Aceptar.')
            self._ultimo_progreso = estado
            self._progreso_log_en = now

    def _esperar_envio(self, main, inv):
        limit = self.reloj() + max(45, min(300, float(self.b.pcfg.get('espera_timbrado', 90))))
        ready = None
        ready_since = None
        while self.reloj() < limit:
            dlg = self.buscar_envio(main)
            windows = self._dialogos(main, {main, inv, dlg})
            # Un error simultáneo no se oculta detrás de una barra de progreso
            # ni de un diálogo de envío que ya exista.
            if any(state == 'AVISO' for _, state in windows):
                self._registrar_espera('TIMBRADO', 'AVISO')
                raise FinalFacturaError('Polaris mostró una advertencia o confirmación que requiere revisión '
                    'después de Aceptar. No se respondió ni se reintentó. '
                    'La factura podría haberse generado: revisa Polaris antes de repetir.')
            progress = [state for _, state in windows if state in PROGRESOS]
            if progress:
                ready = ready_since = None
                self._registrar_espera('TIMBRADO', progress[0])
            elif dlg and self.win.IsWindowEnabled(dlg):
                if ready != dlg:
                    ready, ready_since = dlg, self.reloj()
                elif self.reloj() - ready_since >= .5:
                    self._registrar_espera('TIMBRADO', 'DIALOGO_ENVIO_LISTO')
                    return dlg
            else:
                ready = ready_since = None
            self.dormir(.25)
        self._registrar_espera('TIMBRADO', 'TIEMPO_AGOTADO')
        raise FinalFacturaError('Se pulsó Aceptar una sola vez, pero no se confirmó el diálogo de envío '
            'dentro del tiempo de espera. Polaris podría seguir procesando. '
            'NO vuelvas a timbrar: revisa si la factura ya existe y su envío.')

    def _checked(self, h):
        from . import polaris
        try:
            wrapper = polaris.Desktop(backend='win32').window(handle=h).wrapper_object()
            state = wrapper.get_check_state()
            if state in (0, 1):
                return bool(state)
        except Exception:
            pass
        raise FinalFacturaError('No pude leer la selección de correo/XML/PDF. Se detuvo antes de enviar.')

    def _ensure_checked(self, main, dlg, h):
        if not self._checked(h):
            self._click(main, dlg, h)
        end = self.reloj() + 2
        while self.reloj() < end:
            self._guard(main, dlg)
            if self._checked(h):
                return
            self.dormir(.10)
        raise FinalFacturaError('Polaris no confirmó la opción de envío seleccionada. No se envió el correo.')

    def _email_control(self, dlg):
        fields = [h for h in self._children(dlg) if 'EDIT' in self.b._class_name(h).upper()
                  and 'COMBO' not in self.b._class_name(h).upper()]
        if len(fields) != 1 or not self.win.IsWindowEnabled(fields[0]):
            raise FinalFacturaError('No pude identificar el campo de correo del diálogo de envío.')
        return fields[0]

    def _correo(self, main, dlg, h, email):
        from .factura_captura import CapturaFactura
        if self._captura_correo is None:
            self._captura_correo=CapturaFactura(self.b,texto=self.texto,
                reloj=self.reloj,dormir=self.dormir)
        # Teclado, Tab y lectura POST-salida; no basta el texto recién pegado.
        try:
            self._captura_correo.escribir(dlg,h,'correo',email)
        except Exception as e:
            raise FinalFacturaError('No se confirmó el correo después de salir del campo. '
                'La factura puede estar emitida; revisa el envío sin volver a timbrar. '+str(e)) from e

    def enviar(self, main, dlg, email, registro):
        self.b.etapa_alerta="ENVIO_CORREO: preparar destinatario y XML / PDF"
        self.b._activate(dlg)
        self._guard(main, dlg)
        if self._caption(dlg) != self.TITULO_ENVIO:
            raise FinalFacturaError('No es el diálogo de Envío e Impresión de CFDI.')
        self.b.log('FINAL 2/4: seleccionando Enviar por Correo, sin imprimir.')
        radio = self._named(dlg, 'Enviar por Correo')
        self._ensure_checked(main, dlg, radio)
        self.b.log('FINAL 3/4: verificando correo destino y documentos XML + PDF.')
        field = self._email_control(dlg)
        self._correo(main, dlg, field, email)
        xml = self._named(dlg, 'XML'); pdf = self._named(dlg, 'PDF')
        self._ensure_checked(main, dlg, xml); self._ensure_checked(main, dlg, pdf)
        # Se releen inmediatamente antes del tramo irreversible.
        if not all(self._checked(h) for h in (radio, xml, pdf)):
            raise FinalFacturaError('Cambió la selección de envío; no se pulsó Aceptar.')
        try:
            current = self.texto.leer(field).strip()
            if current != email:
                current = self.texto.copiar(field, lambda: self._guard(main, dlg)).strip()
        except Exception as e:
            raise FinalFacturaError('No pude reconfirmar el destinatario antes de enviar.') from e
        if current != email:
            raise FinalFacturaError('Cambió el correo destino antes de enviar. Revisa Polaris.')
        accept = self._named(dlg, 'Aceptar')
        self._produccion(); self._guard(main, dlg)
        registro.actualizar('ENVIAR_INTENTADO')
        self.b.etapa_alerta='ENVIO_CORREO: Aceptar envío solicitado / esperando resultado'
        self.b.log('FINAL 4/4: Aceptar envío, una sola vez.')
        self._click(main, dlg, accept)
        end = self.reloj() + max(15, min(180, float(self.b.pcfg.get('espera_envio', 45))))
        quiet_since = None
        while self.reloj() < end:
            windows = self._dialogos(main, {main, dlg})
            if any(state == 'AVISO' for _, state in windows):
                self._registrar_espera('CORREO', 'AVISO')
                raise FinalFacturaError('Polaris mostró un aviso durante el envío. '
                    'La factura puede estar emitida; no se repetirá el correo ni el timbrado automáticamente.')
            progress = [state for _, state in windows if state in PROGRESOS]
            if progress:
                quiet_since = None
                self._registrar_espera('CORREO', progress[0])
            elif not self.b._window_exists_visible(dlg):
                # El cierre mientras aún hay progreso NO se declara terminado.
                # Además se esperan varios sondeos sin nuevos avisos.
                if quiet_since is None:
                    quiet_since = self.reloj()
                elif self.reloj() - quiet_since >= 1:
                    registro.actualizar('ENVIO_SOLICITADO')
                    self._registrar_espera('CORREO', 'DIALOGO_CERRADO_SIN_AVISOS')
                    self.b.log('Envío solicitado en Polaris (XML + PDF). '
                               'El cierre del diálogo no confirma recepción en el buzón.')
                    return 'ENVIO_SOLICITADO'
            else:
                quiet_since = None
            self.dormir(.25)
        self._registrar_espera('CORREO', 'TIEMPO_AGOTADO')
        raise FinalFacturaError('Aceptar de envío se pulsó una vez, pero el cierre/proceso no quedó confirmado. '
                                'La factura puede estar emitida; revisa el envío sin volver a timbrar.')

    def diagnostico(self, main):
        """Solo clases/posiciones y etiquetas de botones conocidas; sin valores."""
        known={'FACTURACIONDEEFECTIVO','ENVIOEIMPRESIONDECFDI','ACEPTAR',
               'ENVIARPORCORREO','IMPRIMIRYENVIARPORCORREO','IMPRIMIR','XML','PDF',
               'ERROR','AVISO','ADVERTENCIA','CONFIRMACION','MENSAJE','INFORMACION'}
        rows=[]
        try:
            for h in self.b._cleanup_windows(main):
                r=self.b._rect(h);cap=self._caption(h)
                rows.append({'clase':self.b._class_name(h),
                    'etiqueta':cap if cap in known else '(valor omitido)',
                    'rect':[r.left,r.top,r.right,r.bottom],
                    'habilitado':bool(self.win.IsWindowEnabled(h))})
            folder=self.b.base/'logs';folder.mkdir(parents=True,exist_ok=True)
            path=folder/'diagnostico_final_factura.json'
            path.write_text(json.dumps({'version':'3.1.7','controles':rows,
                'espera':self._eventos_espera,
                'nota':'Sin correos, RFC, folios, valores de campos ni contraseñas.'},
                ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception:
            # Un diagnóstico nunca sustituye el error real ni ejecuta reintentos.
            pass

    def completar(self, main, inv, sol, registro):
        email = validar_correo(sol.correo_destino or sol.remitente)
        self._produccion()
        self.b._activate(inv)
        self._guard(main, inv)
        if not self.b._station_is_active(main, sol.estacion):
            raise FinalFacturaError('La estación cambió antes de Aceptar. No se timbró.')
        if self.b._norm(self.win.GetWindowText(inv) or '') != 'FACTURACIONDEEFECTIVO':
            raise FinalFacturaError('No es Facturación de Efectivo. No se timbró.')
        if self.buscar_envio(main) or self._aviso(main, {main, inv}):
            raise FinalFacturaError('Hay un envío o aviso pendiente. Revisa Polaris antes de facturar.')
        accept = self._named(inv, 'Aceptar')
        registro.iniciar()
        self._produccion()
        self.b.etapa_alerta='TIMBRADO: Aceptar factura solicitado / esperando CFDI'
        self.b.log('FINAL 1/4: Aceptar factura REAL, una sola vez. Esperando diálogo de envío...')
        self._click(main, inv, accept)
        dlg = self._esperar_envio(main, inv)
        registro.actualizar('DIALOGO_ENVIO')
        return self.enviar(main, dlg, email, registro)
