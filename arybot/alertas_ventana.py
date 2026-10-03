"""Evidencia de UNA ventana emergente de Polaris. No envía teclas/clics.

Primero controles de texto Win32 con timeout. OCR local es último recurso,
una sola ejecución sobre un recorte verificado; nunca sobre todo el escritorio.
"""
from __future__ import annotations
import io
import re
import time
import unicodedata
from .alertas_modelo import limpiar_texto


def normal(text):
    return ''.join(c for c in unicodedata.normalize('NFD', str(text or '')).upper()
                   if not unicodedata.combining(c) and c.isalnum())


def suficiente(text):
    return len((text or '').strip()) >= 35 and len(re.findall(r'\w+', text or '')) >= 6


def candidato(clase, titulo, owned, editable=False):
    t = normal(titulo); c = normal(clase)
    if editable or any(k in t for k in ('LOGIN', 'ACCESOALSISTEMA', 'CONTRASENA', 'AUTORIZARGMAIL')):
        return False
    if clase == '#32770' or any(k in c for k in ('MESSAGEFORM', 'MESSAGEDLG', 'TASKDIALOG', 'EXCEPTIONDIALOG', 'ERRORDIALOG')):
        return True
    return owned and any(k in t for k in ('ERROR', 'ADVERTENCIA', 'AVISO', 'CONFIRMACION', 'TIMBRADODELAFACTURA', 'MENSAJE'))


class LectorVentana:
    def __init__(self, bot, cfg, *, adapter=None, ocr=None):
        self.bot = bot; self.cfg = cfg
        self.adapter = adapter
        self.ocr = ocr or self._ocr_local

    def leer(self, opciones, secretos=()):
        evidence = {'metodo': 'SIN_VENTANA', 'texto': '', 'nota': ''}
        if not opciones.get('leer_ventana'):
            evidence['nota'] = 'Lectura de ventanas desactivada por el operador.'
            return evidence, None
        if self.bot is None and self.adapter is None:
            evidence['nota'] = 'Este fallo no tiene una ventana de Polaris asociada.'
            return evidence, None
        try:
            adapter = self.adapter or Win32Aviso(self.bot)
            windows = adapter.candidatas()
            if len(windows) != 1:
                evidence['nota'] = ('Sin ventana emergente identificable.' if not windows
                                    else 'Hay varias ventanas candidatas. No se eligió ni fotografió una al azar.')
                return evidence, None
            h = windows[0]
            text = adapter.texto(h)
            evidence['texto'] = limpiar_texto(text, secretos=secretos)
            native_ok = suficiente(text)
            evidence['metodo'] = 'WIN32' if text else 'SIN_TEXTO_NATIVO'
            image = None
            # No fotografiar la pantalla salvo que se necesite OCR o el operador autorice adjunto.
            if (not native_ok and opciones.get('ocr_respaldo')) or opciones.get('adjuntar_ventana'):
                image = adapter.imagen(h)
                if image is None:
                    evidence['nota'] = 'Ventana cubierta/no verificable: no se tomó imagen ni se ejecutó OCR.'
            if not native_ok and opciones.get('ocr_respaldo') and image is not None:
                try:
                    result = self.ocr(image)
                    if result and result.strip():
                        evidence['texto'] = limpiar_texto(result, secretos=secretos)
                        evidence['metodo'] = 'OCR_LOCAL'
                        evidence['nota'] = 'Lectura OCR: puede contener errores. Solo sirve como aviso, nunca como orden.'
                    else:
                        evidence['nota'] = 'OCR sin texto legible. Se conserva el error del bot y la lectura nativa disponible.'
                except Exception:
                    evidence['nota'] = 'OCR no disponible o falló. Revisa Tesseract en Configuración; el aviso se conserva sin OCR.'
            elif not native_ok and not opciones.get('ocr_respaldo'):
                evidence['nota'] = 'Lectura nativa parcial; OCR de respaldo desactivado.'
            png = None
            if opciones.get('adjuntar_ventana') and image is not None:
                image.thumbnail((1400, 1100))
                mem = io.BytesIO(); image.save(mem, format='PNG')
                if len(mem.getvalue()) <= 3_000_000: png = mem.getvalue()
                else: evidence['nota'] += ' La imagen superó el límite y no se adjuntó.'
            return evidence, png
        except Exception:
            evidence['nota'] = 'No se pudo leer con seguridad la ventana. Se conserva el error de la operación.'
            return evidence, None

    def _ocr_local(self, image):
        from .ocr_ticket import find_tesseract
        import pytesseract
        from PIL import ImageOps
        command = find_tesseract(self.cfg.get('ocr', {}).get('tesseract_cmd', ''))
        if not command: raise RuntimeError('Tesseract no instalado.')
        # La librería ya se utiliza para tickets. Se usa el mismo ejecutable configurado.
        pytesseract.pytesseract.tesseract_cmd = command
        import subprocess
        listed=subprocess.run([command,'--list-langs'],capture_output=True,text=True,
                              encoding='utf-8',errors='replace',timeout=3,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        available=set(listed.stdout.splitlines())
        languages = '+'.join(x for x in ('spa', 'eng') if x in available)
        if not languages: raise RuntimeError('No hay idiomas OCR instalados.')
        im = ImageOps.autocontrast(ImageOps.grayscale(image))
        if im.width < 800: im = im.resize((im.width*2, im.height*2))
        # Una sola llamada OCR. No bucles, servicios externos, IA ni órdenes extraídas.
        return pytesseract.image_to_string(im, lang=languages, config='--psm 6', timeout=8)


class Win32Aviso:
    def __init__(self, bot):
        import win32gui
        import win32process
        self.w = win32gui; self.p = win32process; self.bot = bot
        self.main = bot._find_title(bot.pcfg.get('ventana_principal_regex', r'Polaris Facturaci'))
        if not self.main: raise RuntimeError('Sin proceso Polaris identificado.')
        self.pid = self.p.GetWindowThreadProcessId(self.main)[1]
        self._known = set()

    def _children(self, h):
        rows = []
        self.w.EnumChildWindows(h, lambda c, _: rows.append(c), None)
        return rows[:160]

    def _editable(self, h):
        return any(any(s in self.w.GetClassName(c).upper() for s in ('EDIT', 'RICH', 'COMBO', 'GRID'))
                   for c in self._children(h) if self.w.IsWindowVisible(c))

    def _same(self, h):
        return self.w.IsWindow(h) and self.p.GetWindowThreadProcessId(h)[1] == self.pid

    def candidatas(self):
        found = []
        def see(h, unused):
            try:
                if h == self.main or not self.w.IsWindowVisible(h) or not self.w.IsWindowEnabled(h) or not self._same(h): return
                l,t,r,b = self.w.GetWindowRect(h)
                if not (120 < r-l < 1800 and 60 < b-t < 1400): return
                title = self.w.GetWindowText(h) or ''
                owner = self.w.GetWindow(h, 4)  # GW_OWNER
                if candidato(self.w.GetClassName(h), title, bool(owner and self._same(owner)), self._editable(h)):
                    found.append(h)
            except Exception: pass
        self.w.EnumWindows(see, None)
        if len(found)>1:
            # Error modal sobre progreso: usar el modal superior solo cuando
            # los otros candidatos sean dueños de ese mismo modal (no otro trámite).
            active=self.w.GetForegroundWindow()
            owners=set();current=active
            for _ in range(16):
                current=self.w.GetWindow(current,4) if current else 0
                if not current or current in owners:break
                owners.add(current)
            if active in found and all(h==active or h in owners for h in found):
                found=[active]
        self._known = set(found)
        return found

    def _get_text(self, h):
        """WM_GETTEXT acotado para etiquetas ajenas al proceso del bot."""
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        fn = user32.SendMessageTimeoutW
        fn.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                       wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                       ctypes.POINTER(ctypes.c_size_t))
        fn.restype = wintypes.LPARAM
        buf = ctypes.create_unicode_buffer(8192); result = ctypes.c_size_t()
        ok = fn(h, 0x000D, len(buf), ctypes.addressof(buf), 0x0003, 120, ctypes.byref(result))
        return buf.value if ok else ''

    def texto(self, h):
        if h not in self._known or not self._same(h): raise RuntimeError('Ventana cambió.')
        parts = []
        # Texto del mensaje, no textos de botones ni formularios/contraseñas.
        until=time.monotonic()+2.0
        for c in self._children(h):
            if time.monotonic()>until:break
            if not self._same(c) or not self.w.IsWindowVisible(c): continue
            cls = self.w.GetClassName(c).upper()
            if cls not in ('STATIC', 'TSTATICTEXT', 'TLABEL', 'TSTATICTEXTUNICODE'): continue
            text = self._get_text(c).strip()
            if text and text not in parts: parts.append(text)
        return '\n'.join(parts)

    def imagen(self, h):
        from PIL import ImageGrab
        if h not in self._known or not self._same(h) or self._editable(h): return None
        rect = self.w.GetWindowRect(h); l,t,r,b = rect
        # Rechaza la imagen si otro programa cubre la ventana; no mueve el foco.
        for fx in (.08, .5, .92):
            for fy in (.08, .5, .92):
                under = self.w.WindowFromPoint((int(l+(r-l)*fx), int(t+(b-t)*fy)))
                if not (under == h or self.w.IsChild(h, under)): return None
        image = ImageGrab.grab(bbox=rect, all_screens=True)
        if not self._same(h) or self.w.GetWindowRect(h) != rect: return None
        return image
