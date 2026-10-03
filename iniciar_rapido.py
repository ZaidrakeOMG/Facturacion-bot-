"""Arranque OPCIONAL: utiliza .venv y datos actuales. No reescribe el proyecto."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import sys
import traceback

BASE = Path(__file__).resolve().parent
EXPECTED = {
    'arybot/polaris.py': '71d047742cf5653ede07f6609f9e542be657623244176959933d9a430f3b6767',
    'arybot/compat_vm.py': '1faddf4a146db006acf1422362a3381408c93724317eefb8b7cc740cb90247cc',
    'arybot/compat_vm_rapido.py': '88ffb0749d373bdd34db903c08cb3f6fd833ea6c223c20a3aacb8c371ff87771',
    'arybot/gui.py': '07b7a49f4f3d166d8738e6d7235f135e45ae53048d7ad6677ece970662c44b35',
    'ejecutar_vm.py': '94c9a58498b69fcd67d9bbfd5c7a27eb0d990741cc24747afd41b01a77efb91c',
}


def comprobar_base(base=BASE):
    for name, wanted in EXPECTED.items():
        path = base / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != wanted:
            raise RuntimeError('Este arranque requiere R1 con P02 MENU MEDIDO. '
                               'No coincide el archivo: ' + name + '. No se cambió ningún original. '
                               'Reemplaza los tres archivos de P02 en la misma carpeta R1.')


class Tee:
    def __init__(self, stream, file):
        self.stream, self.file = stream, file
    def write(self, text):
        if self.stream is not None:
            self.stream.write(text)
        self.file.write(text)
        self.file.flush()
        return len(text)
    def flush(self):
        if self.stream is not None:
            self.stream.flush()
        self.file.flush()
    def isatty(self):
        return False
    def __getattr__(self, name):
        return getattr(self.stream if self.stream is not None else self.file, name)


def ejecutar():
    if sys.platform != 'win32':
        raise RuntimeError('Este arranque requiere Windows con Polaris.')
    if Path(sys.prefix).resolve() != (BASE / '.venv').resolve() or sys.prefix == sys.base_prefix:
        raise RuntimeError('Abre INICIAR_RAPIDO.bat para usar la .venv de esta carpeta.')
    comprobar_base()
    # Mantener las comprobaciones de dependencias de R1; no reinstalar ni bajar
    # otra versión de paquetes en una máquina que ya está funcionando.
    import ejecutar_vm as launch
    print('Comprobando el entorno existente; no se reinstala nada.', flush=True)
    result = launch.probe(Path(sys.executable), launch.MODULOS)
    if result.get('errores') or not launch.valid_local_env(result):
        launch.show_probe_errors(result)
        raise RuntimeError('El entorno no pasó la comprobación. Revisa la consola.')
    os.environ.pop('ARY_USAR_ORIGINAL', None)
    from arybot import gui
    from arybot.compat_vm_rapido import PolarisBotRapido
    # Inyección sólo en este proceso, antes de crear App. El lanzador normal
    # sigue importando la clase R1 original. No modifica gui.py en disco.
    gui.PolarisBot = PolarisBotRapido
    print('MODO RÁPIDO R1 + P02 MENU MEDIDO. Se mantienen configuración, contraseñas, cola y modo seguro.', flush=True)
    from arybot.inicio import main
    return main()


def main():
    os.chdir(BASE)
    folder = BASE / 'logs'
    folder.mkdir(exist_ok=True)
    with (folder / 'arranque_rapido.txt').open('a', encoding='utf-8') as f:
        stdout, stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = Tee(stdout, f), Tee(stderr, f)
        try:
            return ejecutar()
        except Exception:
            traceback.print_exc()
            print('No se abrió el bot. Los archivos originales siguen intactos.', flush=True)
            return 1
        finally:
            sys.stdout, sys.stderr = stdout, stderr


if __name__ == '__main__':
    raise SystemExit(main())
