"""Entrada de teclado al control con foco: no pega, no usa VK_PACKET.

Traduce caracteres con la distribución de teclado del hilo de Polaris.
Sólo se emplea en captura de factura; no cambia alta de clientes ni login.
No escribe SQL, no manda WM_SETTEXT ni ejecuta botones de aceptación.
"""
from __future__ import annotations
import ctypes as C
import time

# Tamaños Windows explícitos (también verificables desde pruebas Linux).
WORD=C.c_uint16; DWORD=C.c_uint32; LONG=C.c_int32; ULONG_PTR=C.c_size_t
class KEYBDINPUT(C.Structure):
    _fields_=[('wVk',WORD),('wScan',WORD),('dwFlags',DWORD),('time',DWORD),('dwExtraInfo',ULONG_PTR)]
class MOUSEINPUT(C.Structure):
    _fields_=[('dx',LONG),('dy',LONG),('mouseData',DWORD),('dwFlags',DWORD),('time',DWORD),('dwExtraInfo',ULONG_PTR)]
class HARDWAREINPUT(C.Structure):
    _fields_=[('uMsg',DWORD),('wParamL',WORD),('wParamH',WORD)]
class INPUT_UNION(C.Union):
    _fields_=[('ki',KEYBDINPUT),('mi',MOUSEINPUT),('hi',HARDWAREINPUT)]
class INPUT(C.Structure):
    _anonymous_=('data',)
    _fields_=[('type',DWORD),('data',INPUT_UNION)]

class TecladoError(RuntimeError): pass

class TecladoFactura:
    """API inyectable para probar sin Windows ni Polaris."""
    MODS=(0x10,0x11,0x12,0x5B,0x5C)  # Shift, Ctrl, Alt, Win
    TECLAS={'backspace':0x08,'tab':0x09,'enter':0x0D,'home':0x24,
            'end':0x23,'up':0x26,'down':0x28}
    def __init__(self, api=None, dormir=time.sleep):
        self._u=api;self.dormir=dormir
    def api(self):
        if self._u is None:
            u=C.WinDLL('user32',use_last_error=True)
            u.SendInput.argtypes=[C.c_uint32,C.POINTER(INPUT),C.c_int];u.SendInput.restype=C.c_uint32
            u.GetWindowThreadProcessId.argtypes=[C.c_void_p,C.POINTER(DWORD)];u.GetWindowThreadProcessId.restype=DWORD
            u.GetKeyboardLayout.argtypes=[DWORD];u.GetKeyboardLayout.restype=C.c_void_p
            u.VkKeyScanExW.argtypes=[C.c_wchar,C.c_void_p];u.VkKeyScanExW.restype=C.c_int16
            u.GetAsyncKeyState.argtypes=[C.c_int];u.GetAsyncKeyState.restype=C.c_int16
            u.GetKeyState.argtypes=[C.c_int];u.GetKeyState.restype=C.c_int16
            self._u=u
        return self._u
    def _sin_modificadores(self):
        if any(self.api().GetAsyncKeyState(k)&0x8000 for k in self.MODS):
            raise TecladoError('Una tecla modificadora está presionada. Suelta el teclado y revisa la captura.')
    def _enviar(self, eventos):
        arr=(INPUT*len(eventos))()
        for i,(vk,up) in enumerate(eventos):
            arr[i].type=1;arr[i].ki=KEYBDINPUT(vk,0,2 if up else 0,0,0)
        n=int(self.api().SendInput(len(arr),arr,C.sizeof(INPUT)))
        if n!=len(arr):
            # Libera sólo las teclas de esta secuencia: jamás reenvía caracteres.
            releases=[(k,True) for k in dict.fromkeys(k for k,_ in eventos)]
            cleanup=(INPUT*len(releases))()
            for i,(vk,_) in enumerate(releases):
                cleanup[i].type=1;cleanup[i].ki=KEYBDINPUT(vk,0,2,0,0)
            self.api().SendInput(len(cleanup),cleanup,C.sizeof(INPUT))
            raise TecladoError('Windows no confirmó el teclado enviado. Revisa foco y nivel de permisos; no se reintenta.')
    def _layout(self,h):
        tid=self.api().GetWindowThreadProcessId(h,None)
        layout=self.api().GetKeyboardLayout(tid) if tid else None
        if not layout:raise TecladoError('No se pudo identificar la distribución de teclado de Polaris.')
        return layout
    def _plan(self,h,texto):
        if not texto or len(texto)>254 or any(ord(c)<32 or ord(c)==127 for c in texto):
            raise TecladoError('Texto vacío o no admitido para la captura.')
        u=self.api();layout=self._layout(h);caps=bool(u.GetKeyState(0x14)&1);plan=[]
        for char in texto:
            code=int(u.VkKeyScanExW(char,layout))
            if code==-1 or code&0xffff==0xffff:
                raise TecladoError('Un carácter no existe en la distribución de teclado de Polaris. No se escribió.')
            vk=code&0xff;bits=(code>>8)&0xff
            # No admite modificadores reservados ni Ctrl/Alt aislados (atajos).
            if bits&~7 or (bits&6 not in (0,6)) or not vk:
                raise TecladoError('Combinación de teclado no admitida. No se escribió.')
            if char.isalpha() and caps:bits^=1
            mods=[k for bit,k in ((1,0x10),(2,0x11),(4,0x12)) if bits&bit]
            plan.append([(k,False) for k in mods]+[(vk,False),(vk,True)]+[(k,True) for k in reversed(mods)])
        return layout,caps,plan
    def validar_texto(self,h,texto):
        self._sin_modificadores();self._plan(h,texto)
    def escribir(self,h,texto,guard):
        guard();self._sin_modificadores();layout,caps,plan=self._plan(h,texto)
        for eventos in plan:
            guard();self._sin_modificadores()
            if self._layout(h)!=layout or bool(self.api().GetKeyState(0x14)&1)!=caps:
                raise TecladoError('Cambió el teclado durante la captura. Revisa el campo; no se reintenta.')
            self._enviar(eventos);self.dormir(.18)
        guard()
    def tecla(self,nombre,guard):
        if nombre not in self.TECLAS:raise TecladoError('Tecla no admitida.')
        guard();self._sin_modificadores();vk=self.TECLAS[nombre]
        self._enviar([(vk,False),(vk,True)]);self.dormir(.15)
