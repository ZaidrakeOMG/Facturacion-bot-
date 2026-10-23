from __future__ import annotations
from pathlib import Path
import os
import re
import shutil


def find_tesseract(configured=""):
    """Ubica Tesseract instalado en Windows, incluso fuera de Program Files.

    No instala software, no descarga nada y no invoca el OCR para buscarlo.
    TESSERACT_EXE permite indicar una ruta personalizada sin cambiar el bot.
    """
    paths = [
        configured,
        os.environ.get("TESSERACT_EXE", ""),
        os.environ.get("TESSERACT_CMD", ""),
        shutil.which("tesseract.exe") or "",
        shutil.which("tesseract") or "",
    ]
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        root = os.environ.get(env)
        if root:
            paths.append(str(Path(root) / "Tesseract-OCR" / "tesseract.exe"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local)
        paths += [
            str(base / "Programs" / "Tesseract-OCR" / "tesseract.exe"),
            str(base / "Tesseract-OCR" / "tesseract.exe"),
        ]
        winget = base / "Microsoft" / "WinGet" / "Packages"
        if winget.is_dir():
            for item in list(winget.glob("*Tesseract*"))[:40]:
                paths.extend(str(p) for p in list(item.glob("tesseract.exe"))[:5])
                paths.extend(str(p) for p in list(item.glob("*/tesseract.exe"))[:5])
    user = os.environ.get("USERPROFILE")
    if user:
        paths.append(str(Path(user) / "scoop" / "apps" / "tesseract" / "current" / "tesseract.exe"))
    paths += [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\ProgramData\chocolatey\bin\tesseract.exe",
    ]
    for candidate in paths:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return ""


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
