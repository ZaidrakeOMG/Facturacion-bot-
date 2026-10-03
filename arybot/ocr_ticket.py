from __future__ import annotations
from pathlib import Path
import re
import shutil


def find_tesseract(configured=""):
    if configured and Path(configured).exists(): return configured
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for p in candidates:
        if Path(p).exists(): return p
    return shutil.which("tesseract") or ""


def read_ticket(path: Path, configured="", languages="spa+eng") -> tuple[str, str]:
    """Devuelve (folio, texto_ocr). Conservador: no inventa un folio sin etiqueta."""
    tess = find_tesseract(configured)
    if not tess:
        return "", "OCR no disponible (Tesseract no instalado)."
    import pytesseract
    from PIL import Image, ImageOps, ImageFilter
    pytesseract.pytesseract.tesseract_cmd = tess
    try:
        im = Image.open(path)
        im = ImageOps.exif_transpose(im)
        im = ImageOps.grayscale(im)
        im = ImageOps.autocontrast(im)
        if im.width < 1800:
            scale = min(3, max(2, int(1800 / max(1, im.width))))
            im = im.resize((im.width*scale, im.height*scale))
        im = im.filter(ImageFilter.SHARPEN)
        text = pytesseract.image_to_string(im, lang=languages, config="--psm 6")
    except Exception as e:
        return "", f"Error OCR: {e}"
    pats = [
        r"(?i)(?:FOLIO\s+DEL\s+TICKET|FOLIO|TICKET|TRANSACCI[OÓ]N|NOTA)\s*(?:No\.?|N[uú]m(?:ero)?|#|:|-)?\s*([A-Z0-9-]{4,16})",
        r"(?i)(?:VENTA|OPERACI[OÓ]N)\s*(?:No\.?|#|:|-)?\s*([A-Z0-9-]{4,16})",
    ]
    for pat in pats:
        m = re.search(pat, text)
        if m:
            return re.sub(r"[^A-Z0-9_-]", "", m.group(1).upper()), text
    return "", text
