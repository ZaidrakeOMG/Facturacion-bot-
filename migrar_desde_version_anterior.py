from pathlib import Path
import json, shutil
import tkinter as tk
from tkinter import filedialog, messagebox

BASE=Path(__file__).resolve().parent
root=tk.Tk(); root.withdraw()
old=filedialog.askdirectory(title='Selecciona la carpeta de tu versión anterior de ARY Facturación Bot')
if not old:
    raise SystemExit
old=Path(old)
msgs=[]
# Gmail OAuth token
src=old/'token.json'; dst=BASE/'token.json'
if src.exists():
    shutil.copy2(src,dst); msgs.append('token.json de Gmail copiado')
# Merge useful config, keeping v3.1 defaults/new fields
src_cfg=old/'config.json'; dst_cfg=BASE/'config.json'
if src_cfg.exists() and dst_cfg.exists():
    try:
        oldc=json.loads(src_cfg.read_text(encoding='utf-8'))
        newc=json.loads(dst_cfg.read_text(encoding='utf-8'))
        for key in ('executable','usuario'):
            if oldc.get('polaris',{}).get(key): newc['polaris'][key]=oldc['polaris'][key]
        for key in ('tesseract_cmd','idiomas'):
            if oldc.get('ocr',{}).get(key): newc['ocr'][key]=oldc['ocr'][key]
        for key in ('intervalo_segundos','modo_prueba','auto_responder_faltantes','auto_responder_completada','asunto_palabras'):
            if key in oldc.get('app',{}): newc['app'][key]=oldc['app'][key]
        dst_cfg.write_text(json.dumps(newc,ensure_ascii=False,indent=2),encoding='utf-8')
        msgs.append('configuración de Polaris/Gmail/OCR importada')
    except Exception as e:
        msgs.append(f'No se pudo mezclar config.json: {e}')
messagebox.showinfo('Migración v3.1','\n'.join(msgs) if msgs else 'No encontré token/config para importar.\nLa contraseña de Polaris se conserva en Windows si usas el mismo usuario.')
