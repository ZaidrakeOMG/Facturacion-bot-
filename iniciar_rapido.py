"""Arranque directo de la versión funcional del bot dentro de la .venv actual."""
from __future__ import annotations
import os
from pathlib import Path
import sys

BASE=Path(__file__).resolve().parent

def main():
    os.chdir(BASE)
    if sys.platform != "win32":
        raise RuntimeError("ARY Facturación Bot requiere Windows con Polaris.")
    os.environ["ARY_USAR_ORIGINAL"]="1"
    from arybot.inicio import main as iniciar
    return iniciar()

if __name__ == "__main__":
    raise SystemExit(main())
