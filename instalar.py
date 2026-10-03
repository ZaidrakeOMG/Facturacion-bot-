"""Instala las dependencias del bot y del portal, sin actualizar ni borrar datos."""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def main():
    print('ARY FACTURACION BOT + PORTAL - INSTALACION')
    print('Python:', sys.executable)
    if sys.platform != 'win32':
        print('Este bot necesita Windows con Polaris. No se instaló nada.')
        return 1
    if sys.version_info < (3, 10):
        print('Necesitas Python 3.10 o posterior.')
        return 1
    try:
        from arybot.ajustes_iniciales import asegurar_archivos
        asegurar_archivos(BASE)
        test = subprocess.run([sys.executable, '-m', 'pip', '--version'], capture_output=True)
        if test.returncode:
            subprocess.run([sys.executable, '-m', 'ensurepip', '--upgrade'], check=True)
        print('Instalando dependencias de requirements.in (incluye Waitress).')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                        '-r', str(BASE / 'requirements.in')], check=True, cwd=str(BASE))
    except (OSError, subprocess.CalledProcessError) as exc:
        print('No terminó la instalación:', exc)
        print('Revisa la conexión y vuelve a ejecutar INSTALAR.bat con el mismo Python.')
        return 1
    print('Listo. Abre INICIAR.bat: inicia el bot y la pestaña Portal web.')
    print('Se conservaron tus archivos existentes. No se modificó IIS ni el firewall.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
