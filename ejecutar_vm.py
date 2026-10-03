"""Arranque local verificable. No modifica Windows, Polaris ni la base de datos."""
from __future__ import annotations
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import zipfile

BASE = Path(__file__).resolve().parent
ENV = BASE / '.venv'
PYTHON = ENV / 'Scripts' / 'python.exe'
LOG = BASE / 'logs' / 'arranque_vm.txt'
VERSION = 'VM-BASE-ORIGINAL-2026-10-03-R1'
MODULOS = ['tkinter', 'pyautogui', 'win32gui', 'win32process', 'win32clipboard',
           'win32api', 'win32security', 'win32ui', 'pywinauto', 'keyring',
           'psutil', 'PIL', 'pypdf', 'pytesseract', 'bs4', 'googleapiclient.discovery',
           'google_auth_oauthlib.flow', 'waitress']


def log(text):
    text = str(text)
    print(text, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a', encoding='utf-8') as f:
            f.write(text + '\n')
    except OSError:
        pass


def probe(exe, modules=(), timeout=60):
    code = '''import sys,importlib,json,traceback
out={'exe':sys.executable,'version':list(sys.version_info[:3]),'prefix':sys.prefix,'base':sys.base_prefix,'errores':{}}
for name in json.loads(sys.argv[1]):
 try: importlib.import_module(name)
 except Exception:
  out['errores'][name]=traceback.format_exc()[-3500:]
print('ARY_PROBE='+json.dumps(out))
'''
    try:
        p = subprocess.run([str(exe), '-u', '-c', code, json.dumps(list(modules))],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, cwd=BASE)
        for line in reversed(p.stdout.splitlines()):
            if line.startswith('ARY_PROBE='):
                data = json.loads(line[len('ARY_PROBE='):])
                if p.returncode == 0 and data['version'] >= [3, 10, 0]:
                    return data
        return {'errores': {'Python': (p.stderr or p.stdout or f'Código de salida {p.returncode}')[-3500:]}}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'errores': {'Python': str(exc)}}


def candidates():
    paths = []
    for folder in (Path(os.environ.get('LOCALAPPDATA', ''))/'Programs'/'Python',
                   Path(os.environ.get('ProgramFiles', 'C:/Program Files'))):
        for ver in ('312', '313', '311', '310', '314'):
            paths.append(folder / ('Python'+ver) / 'python.exe')
    paths.append(Path(getattr(sys, '_base_executable', sys.executable)))
    paths.append(Path(sys.executable))
    seen = set()
    for path in paths:
        key = str(path).casefold()
        if key not in seen and path.is_file() and path != PYTHON:
            seen.add(key)
            if not probe(path, timeout=20)['errores']:
                yield path


def run_logged(args):
    # Listas de argumentos: rutas con espacios funcionan; no se usa shell=True.
    p = subprocess.Popen([str(x) for x in args], cwd=BASE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)
    assert p.stdout is not None
    for line in p.stdout:
        log(line.rstrip('\r\n'))
    return p.wait()


def show_probe_errors(data):
    for name, detail in data.get('errores', {}).items():
        log('\nFALLO EN '+name+'\n'+detail)


def valid_local_env(data):
    return bool(data.get('prefix') and Path(data['prefix']).resolve() == ENV.resolve()
                and data.get('prefix') != data.get('base'))


def prepare():
    info = probe(PYTHON, timeout=20) if PYTHON.exists() else {'errores': {'Python': 'Entorno no creado'}}
    if info['errores'] or not valid_local_env(info):
        base_python = next(candidates(), None)
        if base_python is None:
            raise RuntimeError('No se encontró un Python base utilizable. No se borró ningún archivo.')
        if ENV.exists():
            backup = BASE / ('.venv_respaldo_'+time.strftime('%Y%m%d_%H%M%S')+'_'+str(os.getpid()))
            ENV.rename(backup)
            log('Entorno no utilizable conservado como: '+backup.name)
        log('Creando entorno NUEVO con '+str(base_python))
        if run_logged([base_python, '-m', 'venv', str(ENV)]):
            raise RuntimeError('No terminó la creación del entorno. Revisa logs/arranque_vm.txt.')
    else:
        log('Usando entorno local: '+str(PYTHON))
    log('Instalando dependencias SIN caché. No se actualiza el Python global.')
    rc = run_logged([PYTHON, '-m', 'pip', 'install', '--no-cache-dir',
                     '--disable-pip-version-check', '-r', BASE/'requirements.in'])
    if rc:
        raise RuntimeError('pip no terminó correctamente. No se intentó abrir el bot.')
    result = probe(PYTHON, MODULOS)
    if result['errores']:
        show_probe_errors(result)
        raise RuntimeError('Hay dependencias que no cargan. La causa está arriba; no se oculta como pcfg/None.')
    if run_logged([PYTHON, '-m', 'pip', 'check']):
        raise RuntimeError('pip check encontró dependencias incompatibles; revisa el registro.')
    installed = subprocess.run([str(PYTHON), '-m', 'pip', 'freeze'], capture_output=True,
                               text=True, encoding='utf-8', errors='replace', timeout=45)
    if installed.returncode == 0:
        (BASE/'logs'/'dependencias_instaladas_vm.txt').write_text(installed.stdout, encoding='utf-8')
    log('\nENTORNO OK. Abre 2_INICIAR_VM.bat.')
    return result


def desktop_diagnostic():
    """Sólo lectura; no captura valores de Edit, contraseñas ni cookies."""
    import platform
    out = {'version_adaptador': VERSION, 'python': sys.executable, 'windows': platform.platform()}
    try:
        import win32api, win32gui, win32process, psutil
        import pyautogui  # Mismo comportamiento de DPI que el import original.
        out['pantalla_segun_pyautogui'] = list(pyautogui.size())
        out['sesion'] = os.environ.get('SESSIONNAME', '(sin variable)')
        rows = []
        def cb(h, _):
            if not win32gui.IsWindowVisible(h):return
            try:
                pid = win32process.GetWindowThreadProcessId(h)[1]
                exe = psutil.Process(pid).name()
                if 'polaris' not in exe.casefold():return
                item = {'clase': win32gui.GetClassName(h), 'rect': list(win32gui.GetWindowRect(h)),
                        'pid': pid, 'habilitada': bool(win32gui.IsWindowEnabled(h)),
                        'en_foco': h == win32gui.GetForegroundWindow()}
                # No se leen controles de login ni contenido de clientes.
                if item['clase'].casefold() == 'tform_menu':
                    from arybot.compat_vm import MenuWindows
                    menu = MenuWindows(); hm = menu.menu(h)
                    item['menus'] = [x.titulo for x in menu.items(hm)] if hm else []
                rows.append(item)
            except Exception as exc:
                rows.append({'error': type(exc).__name__+': '+str(exc)})
        win32gui.EnumWindows(cb, None)
        out['ventanas_polaris'] = rows
    except Exception:
        out['error_escritorio'] = traceback.format_exc()
    text = json.dumps(out, ensure_ascii=False, indent=2)
    (BASE/'logs'/'diagnostico_vm.json').write_text(text, encoding='utf-8')
    log(text)


def support():
    result = probe(PYTHON, MODULOS) if PYTHON.exists() else {'errores': {'Python': 'No existe .venv local'}}
    (BASE/'logs'/'dependencias_diagnostico_vm.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    show_probe_errors(result)
    if PYTHON.exists():
        run_logged([PYTHON, '-u', __file__, '_escritorio'])
    target = BASE/'SOPORTE_VM.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in ('arranque_vm.txt','dependencias_diagnostico_vm.json','diagnostico_vm.json','dependencias_instaladas_vm.txt'):
            p = BASE/'logs'/name
            if p.is_file():z.writestr(name, p.read_bytes()[-1000000:])
        # La excepción reciente permite distinguir foco/menú/cola; no incluye bases, tokens o claves.
        err = BASE/'logs'/'diagnosticos'/'ultimo_error.json'
        if err.is_file():
            data = json.loads(err.read_text(encoding='utf-8-sig'))
            z.writestr('ultimo_error_resumido.json', json.dumps({k:data.get(k) for k in ('fecha','etapa','error','traceback')}, ensure_ascii=False, indent=2))
        v = BASE/'VERSION_VM.txt'
        if v.exists():z.write(v, v.name)
    log('\nArchivo de soporte: '+str(target))
    log('No incluye config.json, credentials.json, token.json, data ni capturas. Revisa su contenido antes de compartirlo.')


def main():
    action = sys.argv[1] if len(sys.argv)>1 else 'iniciar'
    if sys.platform != 'win32':
        print('Este lanzador requiere Windows. No se modificó nada.');return 1
    os.chdir(BASE)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log('\n=== '+VERSION+' | '+time.strftime('%Y-%m-%d %H:%M:%S')+' | '+action+' ===')
    log('Carpeta activa: '+str(BASE))
    try:
        if action == '_escritorio':desktop_diagnostic();return 0
        if action == 'diagnosticar':support();return 0
        if action == 'preparar':prepare();return 0
        if action not in ('iniciar', 'original'):
            raise ValueError('Acción no reconocida.')
        if not PYTHON.is_file():
            log('Primera ejecución: se prepara el entorno local.');prepare()
        log('Comprobando TODAS las dependencias del mismo Python que abrirá el bot...')
        result = probe(PYTHON, MODULOS)
        if result['errores'] or not valid_local_env(result):
            show_probe_errors(result)
            raise RuntimeError('El entorno no pasó la comprobación. Ejecuta 1_PREPARAR_VM.bat. No se cambió a otro Python silenciosamente.')
        log('Python elegido: '+result['exe'])
        os.environ['PYTHONUNBUFFERED']='1'
        if action == 'original':os.environ['ARY_USAR_ORIGINAL']='1'
        else:os.environ.pop('ARY_USAR_ORIGINAL', None)
        log('Abriendo el bot. Conserva esta consola; los errores quedarán en logs/arranque_vm.txt.')
        return run_logged([PYTHON, '-u', BASE/'ARY_Facturacion_Bot.py'])
    except Exception:
        log(traceback.format_exc())
        log('\nNO TERMINÓ. No se borraron datos ni se automatizó una factura desde este lanzador.')
        log('Registro: '+str(LOG))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
