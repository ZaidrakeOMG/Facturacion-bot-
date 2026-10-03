"""Adaptador de escritorio sobre PolarisBot ORIGINAL. No modifica la captura fiscal.

No modifica resolución, registro, DPI global, certificados, red ni credenciales.
Las entradas de menú se pulsan por sus rectángulos HMENU reales; nunca se adivina
una fila con flechas ni se manda WM_COMMAND a una ventana auxiliar.
"""
from __future__ import annotations
import ctypes
import json
from pathlib import Path
import re
import time
import unicodedata
from dataclasses import dataclass
from . import polaris as original

PolarisError = original.PolarisError


def texto_menu(value):
    value = str(value).split('\t', 1)[0].replace('&', '')
    return ''.join(c for c in unicodedata.normalize('NFKD', value).casefold()
                   if c.isalnum() and not unicodedata.combining(c))


@dataclass(frozen=True)
class ItemMenu:
    posicion: int
    titulo: str
    submenu: int
    estado: int
    identificador: int = -1
    tipo: int = 0

    @property
    def separador(self):
        return bool((self.tipo | self.estado) & 0x0800)

    @property
    def identidad(self):
        # El resaltado cambia al mover el ratón; no forma parte de la identidad.
        return (self.posicion, self.identificador, self.tipo,
                self.submenu, texto_menu(self.titulo))

    @property
    def habilitado(self):
        return self.estado != 0xFFFFFFFF and not self.estado & 0x0003


class MenuWindows:
    """Firmas completas para HWND/HMENU de 32 y 64 bits."""
    def __init__(self):
        from ctypes import wintypes as w
        u = ctypes.WinDLL('user32', use_last_error=True)
        self.u = u
        self.RECT = w.RECT
        class MENUITEMINFOW(ctypes.Structure):
            _fields_ = [
                ('cbSize', w.UINT), ('fMask', w.UINT),
                ('fType', w.UINT), ('fState', w.UINT), ('wID', w.UINT),
                ('hSubMenu', w.HMENU), ('hbmpChecked', w.HBITMAP),
                ('hbmpUnchecked', w.HBITMAP), ('dwItemData', ctypes.c_size_t),
                ('dwTypeData', ctypes.c_void_p), ('cch', w.UINT),
                ('hbmpItem', w.HBITMAP),
            ]
        self.INFO = MENUITEMINFOW
        signatures = {
            'GetMenu': ([w.HWND], w.HMENU),
            'GetMenuItemCount': ([w.HMENU], ctypes.c_int),
            'GetMenuItemInfoW': ([w.HMENU, w.UINT, w.BOOL,
                                 ctypes.POINTER(MENUITEMINFOW)], w.BOOL),
            'GetMenuItemRect': ([w.HWND, w.HMENU, w.UINT,
                                ctypes.POINTER(w.RECT)], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(u, name)
            fn.argtypes, fn.restype = args, result

    def menu(self, hwnd):
        return self.u.GetMenu(hwnd)

    def items(self, menu):
        count = self.u.GetMenuItemCount(menu)
        if count < 0 or count > 200:
            raise PolarisError('Windows no permitió leer el menú de Polaris.')
        out = []
        for pos in range(count):
            buf = ctypes.create_unicode_buffer(1024)
            info = self.INFO()
            info.cbSize = ctypes.sizeof(info)
            # MIIM_STATE | MIIM_ID | MIIM_SUBMENU | MIIM_STRING | MIIM_FTYPE.
            # NO usar MIIM_TYPE: en owner-draw dwTypeData no es necesariamente texto.
            info.fMask = 0x0001 | 0x0002 | 0x0004 | 0x0040 | 0x0100
            info.dwTypeData = ctypes.cast(buf, ctypes.c_void_p).value
            info.cch = len(buf)
            if not self.u.GetMenuItemInfoW(menu, pos, True, ctypes.byref(info)):
                raise PolarisError(
                    f'Windows no permitió leer la identidad de la opción {pos}. '
                    'No se hizo clic.')
            out.append(ItemMenu(pos, buf.value, int(info.hSubMenu or 0),
                                int(info.fState), int(info.wID), int(info.fType)))
        return out

    def rect(self, hwnd, menu, pos):
        rect = self.RECT()
        if not self.u.GetMenuItemRect(hwnd, menu, pos, ctypes.byref(rect)):
            raise PolarisError('Windows no devolvió la posición visible de la opción de menú.')
        result = (rect.left, rect.top, rect.right, rect.bottom)
        if rect.right <= rect.left or rect.bottom <= rect.top:
            raise PolarisError('La opción de menú todavía no es visible. No se hizo clic.')
        return result


def elegir_item(items, label):
    wanted = texto_menu(label)
    matches = [item for item in items if texto_menu(item.titulo) == wanted]
    if len(matches) != 1:
        names = ' | '.join(i.titulo for i in items if i.titulo)[:800]
        raise PolarisError(f'No se localizó una única opción "{label}". Menú visible: {names}')
    item = matches[0]
    if not item.habilitado:
        raise PolarisError(f'Polaris tiene deshabilitada la opción "{label}". No se fuerza su ejecución.')
    return item


# Evidencia de esta VM: MEDIDAS_POLARIS_VM.txt, 03/oct/2026 11:22:09 y 11:22:20.
# Video 20261003-1658-04.1592801.mp4, ~14.5 s: Calculadora, Ventas, Buscar,
# separador, Respaldar, separador, Cambio de estación, Cambiar Usuario, etc.
# Correlación del orden VISIBLE con los 14 elementos nativos del reporte.
# Estos números NO son universales: se exige la estructura COMPLETA cada vez.
# El ID no se ejecuta con WM_COMMAND; sólo identifica el rectángulo a pulsar.
UTILERIAS_VM_P02 = (
    (131, 0x0100), (132, 0x0100), (133, 0x0100), (134, 0x0900),
    (136, 0x0100), (139, 0x0900), (140, 0x0100), (141, 0x0100),
    (142, 0x0900), (143, 0x0100), (144, 0x0100), (145, 0x0100),
    (146, 0x0100), (147, 0x0100),
)


def resolver_item_vm(items, label, ruta=()):
    """Texto exacto si existe; perfil medido sólo para Cambio de estación.

    No interpreta cualquier fila vacía como el destino. Cualquier cambio de
    cantidad, ID, tipo, orden, submenú o texto impide aplicar el perfil medido.
    Tampoco habilita una opción que Polaris tenga deshabilitada.
    """
    items = list(items)
    wanted = texto_menu(label)
    matches = [i for i in items if texto_menu(i.titulo) == wanted]
    if matches:
        return elegir_item(items, label)  # conserva error por duplicidad/permisos
    path = tuple(texto_menu(x) for x in ruta)
    if (path != ('utilerias', 'cambiodeestacion')
            or wanted != 'cambiodeestacion'):
        return elegir_item(items, label)
    observed = tuple((i.identificador, i.tipo) for i in items)
    valid = (observed == UTILERIAS_VM_P02
             and all(i.posicion == n and not i.submenu
                     and not str(i.titulo).strip()
                     for n, i in enumerate(items)))
    if not valid:
        raise PolarisError(
            'P02: los nombres del submenú están vacíos y su estructura no coincide '
            'con la medida en tu VM. No se adivinó la fila. '
            'Detalle en logs/navegacion_menu_p02.jsonl.')
    item = items[6]
    if item.separador or not item.habilitado:
        raise PolarisError(
            'P02: Polaris tiene deshabilitado Cambio de estación. '
            'No se fuerza su ejecución.')
    return item


class PolarisBotVM(original.PolarisBot):
    # Referencia leída de logs/diagnosticos del ZIP original, no del monitor:
    # TForm_Menu: [-8,-8,1608,860] -> 1616 x 868 (incluye bordes).
    ANCHO_ORIGINAL = 1616
    ALTO_ORIGINAL = 868

    def _es_principal(self, h):
        wg = original.win32gui
        try:
            cls = wg.GetClassName(h).casefold()
            title = wg.GetWindowText(h) or ''
            if cls in {'tapplication', 'tooltips_class32', '#32768'}:
                return False
            if re.search(self.pcfg.get('ventana_login_regex', r'Entrada al Sistema'), title, re.I):
                return False
            return bool(re.search(self.pcfg['ventana_principal_regex'], title, re.I))
        except Exception:
            return False

    def _find_title(self, regex):
        if regex != self.pcfg.get('ventana_principal_regex'):
            return super()._find_title(regex)
        candidates = []
        for h in self._enum():
            if not self._es_principal(h):
                continue
            try:
                r = self._rect(h)
                # TForm_Menu es la clase observada en el diagnóstico original.
                preferred = original.win32gui.GetClassName(h).casefold() == 'tform_menu'
                candidates.append((preferred, r.width * r.height, h))
            except Exception:
                continue
        preferred = [item for item in candidates if item[0]]
        if len(preferred) > 1:
            raise PolarisError('Hay más de una ventana principal de Polaris abierta. Cierra la instancia que no vas a usar; no se eligió una al azar.')
        return max(candidates)[2] if candidates else None

    def _ajustar_principal(self, h):
        wg, wc = original.win32gui, original.win32con
        from ctypes import wintypes as w
        # MonitorFromWindow y GetMonitorInfoW: sin cambiar DPI ni resolución.
        class MONITORINFO(ctypes.Structure):
            _fields_ = [('cbSize', w.DWORD), ('rcMonitor', w.RECT),
                        ('rcWork', w.RECT), ('dwFlags', w.DWORD)]
        u = ctypes.WinDLL('user32', use_last_error=True)
        u.MonitorFromWindow.argtypes = [w.HWND, w.DWORD]
        u.MonitorFromWindow.restype = w.HANDLE
        u.GetMonitorInfoW.argtypes = [w.HANDLE, ctypes.POINTER(MONITORINFO)]
        u.GetMonitorInfoW.restype = w.BOOL
        info = MONITORINFO(); info.cbSize = ctypes.sizeof(info)
        mon = u.MonitorFromWindow(h, 2)
        if not mon or not u.GetMonitorInfoW(mon, ctypes.byref(info)):
            raise PolarisError('No se pudo medir el escritorio de la sesión actual.')
        work = info.rcWork
        width, height = self.ANCHO_ORIGINAL, self.ALTO_ORIGINAL
        if work.right - work.left < width or work.bottom - work.top < height:
            # En la PC original la ventana maximizada incluye bordes fuera de pantalla.
            # No se extiende fuera de una VM de menor tamaño; los menús siguen siendo nativos.
            wg.ShowWindow(h, wc.SW_MAXIMIZE)
            return
        r = self._rect(h)
        if wg.GetWindowPlacement(h)[1] == wc.SW_SHOWMAXIMIZED or wg.IsIconic(h):
            wg.ShowWindow(h, wc.SW_RESTORE)
            time.sleep(.25)
            r = self._rect(h)
        x = min(max(r.left, work.left), work.right - width)
        y = min(max(r.top, work.top), work.bottom - height)
        if (r.left, r.top, r.width, r.height) != (x, y, width, height):
            wg.SetWindowPos(h, 0, x, y, width, height, wc.SWP_NOZORDER | wc.SWP_NOACTIVATE)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                r = self._rect(h)
                if abs(r.width - width) <= 2 and abs(r.height - height) <= 2:
                    self.log(f'VM: ventana principal ajustada a {r.width}x{r.height}; no se cambió la pantalla.')
                    return
                time.sleep(.15)
            raise PolarisError(f'Polaris no aceptó el tamaño de referencia. Tamaño actual: {r.width}x{r.height}.')

    def _activate(self, h, maximize=False):
        if h and maximize and self._es_principal(h):
            self._ajustar_principal(h)
            maximize = False
        return super()._activate(h, maximize=maximize)

    def _activar_como_usuario(self, h, maximize=True):
        if h and maximize and self._es_principal(h):
            self._ajustar_principal(h)
            maximize = False
        return super()._activar_como_usuario(h, maximize=maximize)

    @staticmethod
    def _token_elevated(pid=None):
        # Pywin32 conserva el tamaño de HANDLE; evita truncarlos con ctypes por defecto.
        try:
            import win32api, win32con, win32security
            process = win32api.GetCurrentProcess() if pid is None else win32api.OpenProcess(0x1000, False, int(pid))
            try:
                token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
                try:
                    return bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
                finally:
                    token.Close()
            finally:
                if pid is not None:
                    process.Close()
        except Exception:
            return None

    def _registrar_menu_p01(self, evento, **datos):
        """Traza acotada: menú/posición/estado, sin datos de cliente ni claves."""
        try:
            path = Path(self.base) / 'logs' / 'navegacion_menu_p02.jsonl'
            path.parent.mkdir(exist_ok=True)
            record = {'parche': 'P02-MENU-MEDIDO-VM',
                      'fecha': time.strftime('%Y-%m-%d %H:%M:%S'),
                      'evento': evento, **datos}
            with path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        except Exception:
            # Registrar nunca ejecuta/repite una operación ni oculta su error.
            pass

    def _resolver_menu_p02(self, api, menu, label):
        items = api.items(menu)
        item = resolver_item_vm(items, label,
                               getattr(self, '_ruta_menu_p02', ()))
        expected = getattr(self, '_identidad_menu_p02', None)
        if expected is not None and item.identidad != expected:
            raise PolarisError('P02: cambió la identidad de la opción. No se hizo clic.')
        return item

    def _preparar_click_menu(self, main, rect, label):
        """P02: un popup visible no demuestra que su fila ya esté lista.

        Relee el menú después de abrirlo, coloca el ratón sobre la opción y
        exige MF_HILITE sobre ESA opción, con geometría estable. No usa flechas,
        coordenadas de la otra PC ni WM_COMMAND, y no pulsa si falta evidencia.
        """
        context = getattr(self, '_contexto_menu_p01', None)
        if context is None:
            return rect
        api, owner, menu, pos, depth, expected = context
        if owner != main or texto_menu(expected) != texto_menu(label):
            raise PolarisError('P02: cambió la ruta del menú. No se hizo clic.')
        if depth == 0:
            return rect
        wg, pa = original.win32gui, original.pyautogui
        limit = time.monotonic() + 5.0
        last = None
        since = None
        last_reason = 'El submenú todavía no está listo.'
        self.etapa_alerta = 'MENÚ P02: verificar ' + label
        self.log('P02: esperando que Polaris resalte la opción exacta ' + label + '...')
        self._registrar_menu_p01('verificar', opcion=label, posicion=pos)

        while time.monotonic() < limit:
            pa.failSafeCheck()
            pid = self._pid_window(main)
            fg = wg.GetForegroundWindow()
            if (not pid or not fg or self._pid_window(fg) != pid
                    or not wg.IsWindowEnabled(main)):
                raise PolarisError('P02: Polaris perdió el foco o abrió un diálogo. No se hizo clic.')
            # Si la opción desapareció, se duplicó o quedó deshabilitada, parar;
            # no reutilizar la posición anterior ni forzar el comando.
            item = self._resolver_menu_p02(api, menu, label)
            if item.posicion != pos:
                raise PolarisError('P02: cambió el orden del menú mientras se abría. No se hizo clic.')
            if item.separador:
                raise PolarisError('P02: el destino es un separador. No se hizo clic.')
            try:
                current = api.rect(None, menu, pos)
            except PolarisError:
                current = None
            if current:
                l, t, r, b = current
                x, y = (l + r)//2, (t + b)//2
                popup = wg.WindowFromPoint((x, y))
                visible = (r > l and b > t and popup
                           and wg.GetClassName(popup) == '#32768'
                           and self._pid_window(popup) == pid)
                if visible:
                    outer = self._rect(popup)
                    visible = (outer.left <= l < r <= outer.right
                               and outer.top <= t < b <= outer.bottom)
                if visible:
                    # Mover, NO pulsar. Dejar que Windows confirme la fila bajo
                    # el puntero en vez de dar por buena una coordenada calculada.
                    if tuple(wg.GetCursorPos()) != (x, y):
                        pa.moveTo(x, y, duration=.08)
                        last, since = None, None
                    state = self._resolver_menu_p02(api, menu, label)
                    fingerprint = (popup, current, state.posicion)
                    correct = (state.posicion == pos and not state.separador
                               and bool(state.estado & 0x0080))
                    if correct:
                        if fingerprint != last:
                            last, since = fingerprint, time.monotonic()
                        elif time.monotonic() - since >= .18:
                            # Última lectura inmediata; también protege ante
                            # movimiento de ventana o ratón durante la espera.
                            final = self._resolver_menu_p02(api, menu, label)
                            if (final.posicion == pos and final.estado & 0x0080
                                    and not final.separador
                                    and api.rect(None, menu, pos) == current
                                    and tuple(wg.GetCursorPos()) == (x, y)
                                    and wg.WindowFromPoint((x, y)) == popup
                                    and self._pid_window(wg.GetForegroundWindow()) == pid
                                    and wg.IsWindowEnabled(main)):
                                self.log('P02: opción resaltada y verificada: ' + label + '.')
                                self._registrar_menu_p01('listo_para_clic', opcion=label,
                                                         posicion=pos, rect=list(current),
                                                         estado=final.estado)
                                return current
                            last, since = None, None
                    else:
                        last, since = None, None
                        last_reason = 'Windows no resaltó la opción solicitada bajo el ratón.'
                else:
                    last, since = None, None
                    last_reason = 'El rectángulo no pertenece a un submenú visible de Polaris.'
            else:
                last, since = None, None
                last_reason = 'Windows aún no devolvió un rectángulo de menú válido.'
            time.sleep(.08)
        raise PolarisError('P02: no se pudo verificar "' + label + '". ' + last_reason
                           + ' No se pulsó otra opción. Revisa logs/navegacion_menu_p02.jsonl.')

    def _click_menu_item(self, main, rect, label):
        rect = self._preparar_click_menu(main, rect, label)
        wg = original.win32gui
        original.pyautogui.failSafeCheck()
        fg = wg.GetForegroundWindow()
        if not fg or self._pid_window(fg) != self._pid_window(main):
            raise PolarisError(f'Polaris perdió el foco antes de "{label}". No se enviaron clics a otro programa.')
        l, t, r, b = rect
        x, y = (l + r)//2, (t + b)//2
        under = wg.WindowFromPoint((x, y))
        # Las ventanas emergentes de menú (#32768) pertenecen al mismo proceso.
        if not under or self._pid_window(under) != self._pid_window(main):
            raise PolarisError(f'Otra ventana tapa la opción "{label}". No se hizo clic.')
        self.log(f'Menú real: {label}; rectángulo {l},{t},{r},{b}.')
        original.pyautogui.click(x, y)
        time.sleep(.4)

    def _rect_menu_visible(self, api, main, menu, pos, depth, timeout=4.0):
        if depth == 0:
            return api.rect(main, menu, pos)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                rect = api.rect(None, menu, pos)
                l, t, r, b = rect
                under = original.win32gui.WindowFromPoint(((l+r)//2, (t+b)//2))
                if (under and original.win32gui.GetClassName(under) == '#32768'
                        and self._pid_window(under) == self._pid_window(main)):
                    return rect
            except PolarisError:
                pass
            time.sleep(.10)
        raise PolarisError('El submenú no quedó visible a tiempo. No se hizo clic en el fondo de Polaris.')

    def _menu_path(self, main, *labels):
        if len(labels) < 2:
            raise PolarisError('Ruta de menú incompleta.')
        if not self._es_principal(main):
            raise PolarisError('La ventana detectada no es la principal de Polaris. No se automatiza TApplication.')
        self._activar_como_usuario(main, maximize=True)
        self._check_cleanup_prompt(main)
        if not original.win32gui.IsWindowEnabled(main):
            raise PolarisError('La ventana principal está bloqueada por un diálogo. Revise Polaris.')
        api = MenuWindows()
        current = api.menu(main)
        if not current:
            raise PolarisError('La ventana principal no expone su menú de Windows. Revise logs/diagnosticos; no se adivinaron coordenadas.')
        try:
            # Dos o más niveles; abrir el superior provoca la actualización nativa del submenú.
            for depth, label in enumerate(labels):
                self._ruta_menu_p02 = tuple(labels[:depth + 1])
                self._identidad_menu_p02 = None
                observed = api.items(current)
                self._registrar_menu_p01(
                    'lectura_menu', ruta=list(self._ruta_menu_p02),
                    items=[{'posicion': i.posicion, 'id': i.identificador,
                            'tipo': i.tipo, 'estado': i.estado, 'texto': i.titulo,
                            'submenu': bool(i.submenu)} for i in observed])
                item = resolver_item_vm(observed, label, self._ruta_menu_p02)
                self._identidad_menu_p02 = item.identidad
                if not item.titulo:
                    self.log('P02: Cambio de estación identificado con el perfil '
                             'medido de esta VM; ID 140, estructura completa verificada.')
                if depth < len(labels) - 1 and not item.submenu:
                    raise PolarisError(f'"{label}" no tiene el submenú esperado.')
                rect = self._rect_menu_visible(api, main, current, item.posicion, depth)
                self._contexto_menu_p01 = (api, main, current, item.posicion, depth, label)
                try:
                    self._click_menu_item(main, rect, label)
                    self._registrar_menu_p01('clic_enviado', opcion=label, nivel=depth)
                finally:
                    self._contexto_menu_p01 = None
                current = item.submenu
        except Exception as exc:
            self._registrar_menu_p01('error', ruta=list(labels), error=str(exc))
            try:
                image = self._screenshot_error('p02_menu_no_verificado')
                self.log('P02: navegación detenida; captura: ' + str(image))
            except Exception:
                pass
            # ESC sólo cierra un menú abierto de ESTE proceso; nunca responde diálogos de datos.
            wg = original.win32gui
            for h in self._enum():
                try:
                    if wg.GetClassName(h) == '#32768' and self._pid_window(h) == self._pid_window(main):
                        fg = wg.GetForegroundWindow()
                        if fg and self._pid_window(fg) == self._pid_window(main):
                            original.pyautogui.press('esc')
                        break
                except Exception:
                    continue
            raise
        finally:
            self._contexto_menu_p01 = None
            self._ruta_menu_p02 = ()
            self._identidad_menu_p02 = None

    def _open_station_catalog(self, main):
        self.etapa_alerta = 'CAMBIO DE ESTACIÓN: abrir menú real'
        self._menu_path(main, 'Utilerías', 'Cambio de estación')
        dlg = self._wait_text_dialog('Seleccione el registro deseado', 12)
        if not dlg:
            self._screenshot_error('vm_estacion_sin_catalogo')
            raise PolarisError('Se pulsó Cambio de estación, pero no apareció el catálogo. No se repitió ni se abrió otra opción.')
        return dlg

    def _open_cash_invoice(self, main):
        h = self._invoice_window()
        if h:
            self._activate(h)
            return h
        self.etapa_alerta = 'FACTURACIÓN: abrir menú real'
        self._menu_path(main, 'Facturación', 'Efectivo')
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            h = self._invoice_window()
            if h:
                r = self._rect(h)
                if r.width < 650 or r.height < 450:
                    raise PolarisError(f'La ventana de facturación tiene tamaño inesperado: {r.width}x{r.height}.')
                self.log(f'Facturación identificada: {r.width}x{r.height} en {r.left},{r.top}. Captura original sin cambios.')
                self._activate(h)
                return h
            time.sleep(.2)
        self._screenshot_error('vm_facturacion_sin_ventana')
        raise PolarisError('Se pulsó Facturación > Efectivo pero no apareció el formulario. No se enviaron datos.')
