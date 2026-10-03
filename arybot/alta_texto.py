"""Entrada/lectura de controles de alta de efectivo, NO de la base de datos.

WM_GETTEXT se envía directamente con búfer propio y tiempo máximo: no se
interpreta un WM_GETTEXTLENGTH cero de un proveedor VCL como lectura válida.
WM_COPY solo se acepta si el portapapeles CAMBIÓ y su propietario es Polaris;
el texto que el bot puso para pegar NO es prueba de lo escrito en la pantalla.

No se usa WM_SETTEXT, Enter, Aceptar, SQL, OCR ni servicios de red.
Las dependencias Windows se cargan al usarse; el módulo se puede importar en
pruebas con un transporte simulado. Los errores no contienen valores fiscales.
"""
from __future__ import annotations
import ctypes
import time


class TextoControlError(RuntimeError):
    def __init__(self, codigo: str, winerror: int | None = None):
        self.codigo = codigo
        self.winerror = winerror
        super().__init__(codigo)


def normalizar_campo(campo: str, valor: str) -> str:
    """Solo formato de presentación; jamás inventa caracteres faltantes."""
    texto = str(valor).strip()
    if campo == 'rfc':
        # Admite mayúsculas y relleno FINAL de una máscara de longitud fija.
        # Un guion/espacio/underscore interior no se elimina ni se adivina.
        return texto.rstrip(' _').upper()
    if campo == 'telefono':
        return ''.join(c for c in texto if c not in ' ()-')
    return texto


def literal_teclas(texto: str) -> str:
    """Evita que caracteres del correo/RFC se interpreten como atajos."""
    if len(texto) > 320 or any(ord(c) < 32 or ord(c) == 127 for c in texto):
        raise TextoControlError('TEXTO_NO_ADMITIDO')
    return ''.join('{' + c + '}' if c in '{}+^%~()' else c for c in texto)


class TextoWindows:
    WM_GETTEXT = 0x000D
    EM_SETSEL = 0x00B1
    WM_COPY = 0x0301
    WM_PASTE = 0x0302
    MENSAJES = frozenset((WM_GETTEXT, EM_SETSEL, WM_COPY, WM_PASTE))
    CAPACIDAD = 4096
    TIMEOUT_MS = 900

    def __init__(self, *, user32=None, clipboard=None, pid=None,
                 reloj=time.monotonic, dormir=time.sleep, teclas=None):
        self._dll = user32
        self._clip = clipboard
        self._pid = pid
        self.reloj = reloj
        self.dormir = dormir
        self._teclas = teclas

    def _api(self):
        if self._dll is None:
            from ctypes import wintypes as wt
            u = ctypes.WinDLL('user32', use_last_error=True)
            u.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t,
                ctypes.c_ssize_t, wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t)]
            u.SendMessageTimeoutW.restype = ctypes.c_ssize_t
            u.GetClipboardSequenceNumber.argtypes = []
            u.GetClipboardSequenceNumber.restype = wt.DWORD
            u.GetClipboardOwner.argtypes = []
            u.GetClipboardOwner.restype = wt.HWND
            self._dll = u
        return self._dll

    def _message(self, h: int, msg: int, wp: int = 0, lp: int = 0) -> int:
        if not h or msg not in self.MENSAJES:
            raise TextoControlError('MENSAJE_NO_ADMITIDO')
        result = ctypes.c_size_t(0)
        try:
            if hasattr(ctypes, 'set_last_error'):
                ctypes.set_last_error(0)
            ok = self._api().SendMessageTimeoutW(h, msg, wp, lp, 0x0002 | 0x0020,
                                                self.TIMEOUT_MS, ctypes.byref(result))
            if not ok:
                code = ctypes.get_last_error() if hasattr(ctypes, 'get_last_error') else 0
                raise TextoControlError('CONTROL_SIN_RESPUESTA', code or None)
            return int(result.value)
        except TextoControlError:
            raise
        except Exception as e:
            raise TextoControlError('TRANSPORTE_WINDOWS_NO_DISPONIBLE',
                                    getattr(e, 'winerror', None)) from e

    def leer(self, h: int) -> str:
        buffer = ctypes.create_unicode_buffer(self.CAPACIDAD)
        count = self._message(h, self.WM_GETTEXT, self.CAPACIDAD, ctypes.addressof(buffer))
        if count >= self.CAPACIDAD - 1:
            raise TextoControlError('LECTURA_TRUNCADA')
        # Una respuesta vacía es distinta de un timeout, que lanza excepción.
        return buffer.value

    def _clipboard(self):
        if self._clip is None:
            import win32clipboard
            self._clip = win32clipboard
        return self._clip

    def _con_clipboard(self, operacion):
        end = self.reloj() + 1.2
        while True:
            opened = False
            try:
                c = self._clipboard()
                c.OpenClipboard()
                opened = True
                return operacion(c)
            except TextoControlError:
                raise
            except Exception as e:
                if opened or self.reloj() >= end:
                    raise TextoControlError('PORTAPAPELES_NO_DISPONIBLE',
                                            getattr(e, 'winerror', None)) from e
                self.dormir(.05)
            finally:
                if opened:
                    c.CloseClipboard()

    def _poner_clipboard(self, valor: str):
        def put(c):
            c.EmptyClipboard()
            c.SetClipboardText(valor, 13)  # CF_UNICODETEXT
        self._con_clipboard(put)

    def _leer_clipboard(self) -> str:
        def get(c):
            if not c.IsClipboardFormatAvailable(13):
                raise TextoControlError('COPIA_SIN_TEXTO_UNICODE')
            return str(c.GetClipboardData(13))
        return self._con_clipboard(get)

    def _pid_de(self, h: int) -> int:
        if not h:
            return 0
        if self._pid is not None:
            return int(self._pid(h))
        import win32process
        return int(win32process.GetWindowThreadProcessId(h)[1])

    def _seleccionar(self, h: int):
        # Selección dirigida al Edit identificado; no Ctrl+A global.
        self._message(h, self.EM_SETSEL, 0, -1)

    def pegar(self, h: int, valor: str, guard):
        literal_teclas(valor)  # mismo límite y rechazo de caracteres de control
        guard()
        self._seleccionar(h)
        guard()
        self._poner_clipboard(valor)
        guard()
        # El control procesa el pegado normal y sus eventos; no WM_SETTEXT.
        self._message(h, self.WM_PASTE)
        guard()

    def copiar(self, h: int, guard) -> str:
        """Copia nueva del control. No usa ni acepta el contenido previo."""
        guard()
        self._seleccionar(h)
        guard()
        before = int(self._api().GetClipboardSequenceNumber())
        if not before:
            raise TextoControlError('SECUENCIA_CLIPBOARD_NO_DISPONIBLE')
        self._message(h, self.WM_COPY)
        end = self.reloj() + 1.0
        while True:
            guard()
            seq = int(self._api().GetClipboardSequenceNumber())
            if seq and seq != before:
                owner = self._api().GetClipboardOwner()
                if not owner or self._pid_de(owner) != self._pid_de(h):
                    raise TextoControlError('COPIA_DE_OTRA_APLICACION')
                value = self._leer_clipboard()
                # Si cambió durante la lectura, no hay evidencia consistente.
                if int(self._api().GetClipboardSequenceNumber()) != seq:
                    raise TextoControlError('COPIA_CAMBIO_DURANTE_LECTURA')
                guard()
                return value
            if self.reloj() >= end:
                raise TextoControlError('SIN_COPIA_NUEVA_DEL_CONTROL')
            self.dormir(.08)

    def teclear(self, h: int, valor: str, guard):
        """Un reintento por campo, después de reseleccionar. Sin Enter/Tab."""
        literal_teclas(valor)
        if self._teclas is None:
            from pywinauto.keyboard import send_keys
            self._teclas = send_keys
        guard()
        self._seleccionar(h)
        for char in valor:
            guard()  # comprueba foco antes de cada carácter, no solo al inicio
            self._teclas(literal_teclas(char), pause=.045, with_spaces=True, vk_packet=True)
        guard()
