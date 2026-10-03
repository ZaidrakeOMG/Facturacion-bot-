"""Punto de entrada único: bot de escritorio y receptor HTTP en la misma instancia."""
from __future__ import annotations
import sys
from pathlib import Path


def main():
    if sys.platform != 'win32':
        print('ARY Facturación Bot requiere Windows con Polaris y sesión de escritorio abierta.')
        return 1
    try:
        if sys.version_info < (3, 10):
            raise RuntimeError('Usa Python 3.10 o posterior. Después ejecuta INSTALAR.bat.')
        from .ajustes_iniciales import asegurar_archivos
        asegurar_archivos(Path(__file__).resolve().parent.parent)
        from .portal_gui import main as abrir_bot
        result = abrir_bot()
        return result if isinstance(result, int) else 0
    except Exception as exc:
        message = ('No se pudo iniciar ARY Facturación Bot.\n\n'
                   + type(exc).__name__ + ': ' + str(exc)
                   + '\n\nEjecuta INSTALAR.bat en esta misma carpeta. '
                     'No borres config.json, credentials.json, token.json ni data.')
        print(message, file=sys.stderr)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk(); root.withdraw()
            messagebox.showerror('Arranque de ARY', message, parent=root)
            root.destroy()
        except Exception:
            pass
        return 1
