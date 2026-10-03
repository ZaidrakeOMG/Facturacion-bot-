"""Perfil opcional R1: elimina esperas redundantes sin tocar captura/timbrado.

No sustituye archivos originales. No cambia coordenadas, DPI, resolución,
PyAutoGUI.PAUSE, escritura, fechas, folios, cierre de ventanas o confirmaciones.
Las comprobaciones recientes sólo se reutilizan dentro del mismo ensure_ready.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import time

from . import polaris as original
from .compat_vm import PolarisBotVM, PolarisError


class PolarisBotRapido(PolarisBotVM):
    VERSION_RAPIDA = 'R1-RAPIDO-OPCIONAL-20261003'
    # La pausa de entrada de PyAutoGUI del original se conserva además de ésta.
    PAUSA_MENU = 0.12
    VIGENCIA_ESTABILIDAD = 1.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estable_rapido = None
        self.log('MODO RÁPIDO R1 activo: inicio/navegación; captura y timbrado originales.')

    def _huella_principal(self, h):
        """Sólo una principal real, grande, visible, habilitada y sin login."""
        if not h or not self._es_principal(h):
            return None
        wg = original.win32gui
        try:
            if (not wg.IsWindow(h) or not wg.IsWindowVisible(h)
                    or not wg.IsWindowEnabled(h) or wg.IsIconic(h)):
                return None
            if self._find_title(self.pcfg['ventana_login_regex']):
                return None
            r = self._rect(h)
            if r.width < 800 or r.height < 500:
                return None
            return (h, self._pid_window(h), wg.GetClassName(h), wg.GetWindowText(h),
                    r.left, r.top, r.right, r.bottom)
        except Exception:
            return None

    def _guardar_estabilidad(self, h):
        fingerprint = self._huella_principal(h)
        self._estable_rapido = (h, fingerprint, time.monotonic()) if fingerprint else None
        return h

    def _principal_reciente(self):
        cached = getattr(self, '_estable_rapido', None)
        if not cached:
            return None
        h, fingerprint, timestamp = cached
        age = time.monotonic() - timestamp
        if not 0 <= age <= self.VIGENCIA_ESTABILIDAD:
            return None
        # Refrescar identidad, geometría, título, sesión y permisos: no confiar
        # únicamente en una variable guardada o en que todavía exista el HWND.
        if self._find_title(self.pcfg['ventana_principal_regex']) != h:
            return None
        if self._huella_principal(h) != fingerprint:
            return None
        original.pyautogui.failSafeCheck()
        self._validar_misma_sesion_windows(h)
        return h

    def _esperar_principal_estable(self, timeout=30.0, stable_seconds=2.0):
        self._estable_rapido = None
        h = super()._esperar_principal_estable(timeout, stable_seconds)
        return self._guardar_estabilidad(h)

    @contextmanager
    def _medir(self, nombre):
        started = time.perf_counter()
        state = 'OK'
        self.log('RÁPIDO: ' + nombre + '...')
        try:
            yield
        except BaseException:
            state = 'ERROR'
            raise
        finally:
            elapsed = round(time.perf_counter() - started, 3)
            # Sólo tiempos y nombre del paso; sin RFC, folios ni credenciales.
            try:
                self.log(f'RÁPIDO: {nombre} — {elapsed:.2f} s — {state}')
                path = Path(self.base) / 'logs' / 'tiempos_rapido.jsonl'
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('a', encoding='utf-8') as f:
                    f.write(json.dumps({'perfil': self.VERSION_RAPIDA,
                                        'fecha': time.strftime('%Y-%m-%d %H:%M:%S'),
                                        'paso': nombre, 'segundos': elapsed,
                                        'estado': state}, ensure_ascii=False) + '\n')
            except Exception:
                # Un problema al registrar tiempos no repite la operación.
                pass

    def ensure_ready(self):
        # No reutilizar estabilidad de facturas/operaciones anteriores.
        self._estable_rapido = None
        with self._medir('Preparar Polaris'):
            source = self._open()
            state, handle = self._esperar_login_o_principal_listo(
                timeout=90.0, stable_seconds=1.8)
            if state == 'login':
                # El login ORIGINAL ya hace una espera de 1.8 s de estabilidad.
                self._login()
                main = self._principal_reciente()
                if main is None:
                    main = self._esperar_principal_estable(timeout=60.0, stable_seconds=1.8)
                else:
                    self.log('RÁPIDO: se reutilizó la comprobación que acaba de terminar el login.')
            else:
                main = self._guardar_estabilidad(handle)

            self._activar_como_usuario(main, maximize=True)
            recent = self._principal_reciente()
            if recent == main:
                self.log('RÁPIDO: principal sin cambios de tamaño/título; no se repite otra espera.')
            else:
                # Si la adaptación VM movió/redimensionó Polaris, mantener TODA
                # la espera original de estabilización, no teclear durante el repintado.
                main = self._esperar_principal_estable(timeout=25.0, stable_seconds=1.2)
            self.log('Polaris listo (perfil rápido). Origen=' + str(source))
            return main

    def _foco_exacto(self, h):
        try:
            wg = original.win32gui
            return bool(h and wg.IsWindow(h) and wg.IsWindowVisible(h)
                        and wg.IsWindowEnabled(h) and not wg.IsIconic(h)
                        and wg.GetForegroundWindow() == h)
        except Exception:
            return False

    def _activar_como_usuario(self, h, maximize=True):
        original.pyautogui.failSafeCheck()
        before = self._huella_principal(h)
        if h and maximize and self._es_principal(h):
            self._ajustar_principal(h)
            maximize = False
        after = self._huella_principal(h)
        # Sólo omitir activación/pausa cuando es EXACTAMENTE la principal ya
        # activa, sin cambios. Un modal del mismo PID NO cuenta como principal.
        if (not maximize and before is not None and before == after
                and self._foco_exacto(h)):
            self._validar_misma_sesion_windows(h)
            return h
        return original.PolarisBot._activar_como_usuario(self, h, maximize=maximize)

    def _activate(self, h, maximize=False):
        # No acelerar activación de formularios MDI, login, clientes o envío.
        original.pyautogui.failSafeCheck()
        before = self._huella_principal(h)
        if h and maximize and self._es_principal(h):
            self._ajustar_principal(h)
            maximize = False
        after = self._huella_principal(h)
        if (not maximize and before is not None and before == after
                and self._foco_exacto(h)):
            self._validar_misma_sesion_windows(h)
            return None
        return original.PolarisBot._activate(self, h, maximize=maximize)

    def _click_menu_item(self, main, rect, label):
        rect = self._preparar_click_menu(main, rect, label)
        # Mismos guardas de foco/oclusión de R1. La ruta de menú heredada espera
        # el submenú real, y el llamador espera el catálogo/formulario real.
        wg = original.win32gui
        original.pyautogui.failSafeCheck()
        fg = wg.GetForegroundWindow()
        if not fg or self._pid_window(fg) != self._pid_window(main):
            raise PolarisError(f'Polaris perdió el foco antes de "{label}". No se enviaron clics a otro programa.')
        l, t, r, b = rect
        if r <= l or b <= t:
            raise PolarisError('Rectángulo de menú inválido. No se hizo clic.')
        x, y = (l + r) // 2, (t + b) // 2
        under = wg.WindowFromPoint((x, y))
        if not under or self._pid_window(under) != self._pid_window(main):
            raise PolarisError(f'Otra ventana tapa la opción "{label}". No se hizo clic.')
        self.log(f'Menú real: {label}; rectángulo {l},{t},{r},{b}.')
        original.pyautogui.click(x, y)
        time.sleep(self.PAUSA_MENU)

    def _cleanup_before_station_change(self, main):
        with self._medir('Revisar y cerrar ventanas'):
            return super()._cleanup_before_station_change(main)

    def _change_station(self, main, station, force=False):
        with self._medir('Confirmar estación'):
            return super()._change_station(main, station, force=force)

    def _open_cash_invoice(self, main):
        with self._medir('Abrir facturación'):
            return super()._open_cash_invoice(main)
