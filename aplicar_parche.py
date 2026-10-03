"""Instala solo codigo v8 sobre la base V7; conserva configuracion y datos.

No inicia Polaris ni Gmail. Verifica versiones y crea respaldo antes de escribir.
"""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath

MUTEX = 'Local\\ARY_Polaris_Bot_Cola_Unica'

class ErrorParche(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def es_bot(path: Path) -> bool:
    return ((path / 'ARY_Facturacion_Bot.py').is_file()
            and (path / 'arybot' / 'polaris.py').is_file())


def localizar_bot(paquete: Path) -> Path:
    for candidate in (paquete, *list(paquete.parents)[:3]):
        if es_bot(candidate):
            return candidate.resolve()
    try:
        import tkinter as tk
        from tkinter import filedialog
        win = tk.Tk()
        win.withdraw()
        win.attributes('-topmost', True)
        try:
            value = filedialog.askdirectory(
                title='Selecciona la carpeta del bot que contiene ARY_Facturacion_Bot.py',
                parent=win)
        finally:
            win.destroy()
        if value and es_bot(Path(value)):
            return Path(value).resolve()
    except Exception as exc:
        raise ErrorParche('No se pudo seleccionar la carpeta del bot.') from exc
    raise ErrorParche('No se encontro el bot. Coloca PARCHE_ARY_v8 dentro de la carpeta del bot e intenta de nuevo.')


@contextmanager
def reservar_instancia():
    """El mismo mutex del bot evita iniciar el programa a mitad de la actualizacion."""
    if os.name != 'nt':
        raise ErrorParche('Este instalador se ejecuta en Windows, con el bot cerrado.')
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = kernel.CreateMutexW(None, False, MUTEX)
    error = ctypes.get_last_error()
    if not handle:
        raise ErrorParche('No se pudo comprobar que el bot este cerrado. No se cambio ningun archivo.')
    try:
        if error == 183:
            raise ErrorParche('El bot o este instalador sigue abierto. Espera a que termine Polaris y cierra el bot antes de actualizar.')
        yield
    finally:
        kernel.CloseHandle(handle)


def _safe_code_path(root: Path, name: str) -> Path:
    rel = PurePosixPath(name)
    if ('\\' in name or rel.is_absolute() or len(rel.parts) != 2
            or rel.parts[0] != 'arybot' or '..' in rel.parts
            or rel.suffix != '.py' or ':' in name):
        raise ErrorParche('Ruta de codigo no permitida en el paquete: ' + str(name))
    root = root.resolve()
    result = root.joinpath(*rel.parts)
    if any(p.is_symlink() for p in (root / 'arybot', result)):
        raise ErrorParche('Se encontro un enlace simbolico; no se reemplazara: ' + name)
    if result.resolve().parent != (root / 'arybot').resolve():
        raise ErrorParche('Ruta fuera del modulo arybot: ' + name)
    # Tambien rechaza un junction de Windows que salga de la instalacion.
    if not result.resolve().is_relative_to(root):
        raise ErrorParche('Ruta fuera de la instalacion: ' + name)
    return result


def preparar_plan(bot: Path, paquete: Path):
    """Prevalidacion completa: ningun archivo del bot se modifica aqui."""
    if not es_bot(bot):
        raise ErrorParche('La carpeta elegida no contiene el bot esperado.')
    try:
        manifest = json.loads((paquete / 'manifiesto.json').read_text(encoding='utf-8'))
        rows = manifest['archivos']
        if manifest.get('version') != 'ARY-v8-reintentos-gmail' or not isinstance(rows, list) or not rows:
            raise ValueError('manifiesto no reconocido')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ErrorParche('Paquete incompleto: no se puede leer el manifiesto.') from exc
    plan = []
    seen = set()
    for row in rows:
        try:
            name, old_hash, new_hash = row['ruta'], row['base_v7_sha256'], row['nuevo_sha256']
        except (TypeError, KeyError) as exc:
            raise ErrorParche('Manifiesto incorrecto.') from exc
        if name in seen:
            raise ErrorParche('Ruta duplicada: ' + name)
        seen.add(name)
        dest = _safe_code_path(bot, name)
        source = _safe_code_path(paquete / 'archivos', name)
        try:
            new = source.read_bytes()
        except OSError as exc:
            raise ErrorParche('Falta el modulo del parche: ' + name) from exc
        if digest(new) != new_hash:
            raise ErrorParche('El paquete esta alterado o incompleto: ' + name)
        try:
            compile(new, name, 'exec')
        except (SyntaxError, ValueError) as exc:
            raise ErrorParche('El modulo no es compatible con este Python: ' + name) from exc
        if dest.exists() and not dest.is_file():
            raise ErrorParche('No es un archivo normal: ' + name)
        old = dest.read_bytes() if dest.exists() else None
        current_hash = digest(old) if old is not None else None
        if current_hash == new_hash:
            continue
        if current_hash != old_hash:
            raise ErrorParche('La version instalada no coincide con V7 en ' + name
                              + '. No se reemplazo ningun archivo. No combines parches de otra version.')
        plan.append((name, dest, old, new))
    return plan


def _replace(dest: Path, data: bytes):
    temporary = dest.with_name(dest.name + '.aryv8-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, dest)
    finally:
        if temporary.exists():
            temporary.unlink()


def aplicar(bot: Path, paquete: Path) -> dict:
    """Llamar bajo reservar_instancia. Solo escribe los .py validados y su respaldo."""
    plan = preparar_plan(bot, paquete)
    if not plan:
        return {'modulos': 0, 'respaldo': None}
    data = bot / 'data'
    if data.is_symlink() or not data.resolve().is_relative_to(bot.resolve()):
        raise ErrorParche('No se usara una carpeta data que redirija a otra ubicacion.')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    backup = data / ('respaldo_parche_v8_' + stamp)
    backup.mkdir(parents=True, exist_ok=False)
    # Primero se termina TODO el respaldo; si falla, el codigo no se modifica.
    for name, dest, old, new in plan:
        if old is not None:
            copy = backup / name
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(old)
    applied = []
    try:
        for name, dest, old, new in plan:
            _replace(dest, new)
            applied.append((name, dest, old))
    except Exception as exc:
        rollback_errors = []
        for name, dest, old in reversed(applied):
            try:
                if old is None:
                    dest.unlink(missing_ok=True)
                else:
                    _replace(dest, old)
            except Exception:
                rollback_errors.append(name)
        if rollback_errors:
            raise ErrorParche('Fallo la copia y la restauracion de: ' + ', '.join(rollback_errors)
                              + '. NO inicies el bot. Usa el respaldo: ' + str(backup)) from exc
        raise ErrorParche('Fallo la copia. Se restauro el codigo anterior. Respaldo: ' + str(backup)) from exc
    # Limpia solo bytecode de los modulos tocados, no logs, datos ni credenciales.
    cache = bot / 'arybot' / '__pycache__'
    if cache.is_dir() and not cache.is_symlink():
        for name, *_ in plan:
            for pyc in cache.glob(Path(name).stem + '.*.pyc'):
                try:
                    pyc.unlink()
                except OSError:
                    pass
    return {'modulos': len(plan), 'respaldo': str(backup)}


def main() -> int:
    if sys.version_info < (3, 10):
        print('Se requiere Python 3.10 o posterior. No se cambio ningun archivo.')
        return 1
    paquete = Path(__file__).resolve().parent
    try:
        bot = localizar_bot(paquete)
        print('Bot: ' + str(bot))
        print('Se conservaran config.json, portal_config.json, credenciales y bases de datos.')
        with reservar_instancia():
            result = aplicar(bot, paquete)
        if not result['modulos']:
            print('Este parche ya esta instalado. No se cambio ningun archivo.')
        else:
            print('Parche v8 instalado: ' + str(result['modulos']) + ' modulos.')
            print('Respaldo del codigo anterior: ' + result['respaldo'])
        print('Abre INICIAR.bat y conserva MODO SEGURO para la primera prueba.')
        print('No se ejecuto Polaris, no se emitieron facturas ni se enviaron correos durante la instalacion.')
        return 0
    except Exception as exc:
        print('ERROR: ' + str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
