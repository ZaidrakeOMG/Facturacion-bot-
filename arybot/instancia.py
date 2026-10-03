"""Exclusión de instancias del bot en el escritorio Windows (sin acceso a Polaris)."""
from __future__ import annotations
import os
from pathlib import Path

class InstanciaBot:
    def __init__(self,base):self.base=Path(base);self.handle=None;self.file=None
    def adquirir(self):
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
            kernel.CreateMutexW.restype=wintypes.HANDLE
            ctypes.set_last_error(0)
            h=kernel.CreateMutexW(None,False,'Local\\ARY_Polaris_Bot_Cola_Unica')
            if not h:raise OSError('No se pudo reservar la instancia del bot.')
            if ctypes.get_last_error()==183:
                kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(h)
                raise RuntimeError('Ya hay una instancia de este bot abierta en la sesión. Cierra la otra antes de iniciar.')
            self.handle=h
        else:
            import fcntl
            folder=self.base/'data';folder.mkdir(parents=True,exist_ok=True)
            self.file=(folder/'instancia.lock').open('a+b')
            try:fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                self.file.close();self.file=None;raise RuntimeError('Ya hay una instancia abierta en esta carpeta.')
        return self
    def cerrar(self):
        if self.handle:
            import ctypes
            from ctypes import wintypes
            k=ctypes.WinDLL('kernel32',use_last_error=True);k.CloseHandle.argtypes=[wintypes.HANDLE]
            k.CloseHandle(self.handle);self.handle=None
        if self.file:
            import fcntl
            fcntl.flock(self.file.fileno(),fcntl.LOCK_UN);self.file.close();self.file=None
