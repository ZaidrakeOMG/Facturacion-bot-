"""Instala únicamente la dependencia adicional del portal. No toca config ni datos."""
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parent
if not (BASE / 'ARY_Facturacion_Bot.py').is_file() or not (BASE / 'arybot' / 'cola_operaciones.py').is_file():
    raise SystemExit('Copia primero para_el_bot dentro de la carpeta del bot actual; no es un bot independiente.')
result = subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(BASE / 'requirements_portal.txt')])
if result.returncode:
    raise SystemExit('No se pudo instalar Waitress. Revisa la conexión e inténtalo con el Python del bot.')
print('Dependencia instalada. Abre ARY_Facturacion_Portal.py. Conserva MODO SEGURO para la primera prueba.')
