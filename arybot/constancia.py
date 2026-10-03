from __future__ import annotations
import re
from pathlib import Path
from pypdf import PdfReader

RFC_RE = re.compile(r"\b([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})\b", re.I)


def read_constancia(path: Path) -> dict:
    try:
        reader = PdfReader(str(path))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:
        return {}
    out = {}
    m = RFC_RE.search(text.upper())
    if m: out["rfc"] = m.group(1).upper()
    patterns = [
        ("codigo_postal", r"(?i)C[oó]digo\s+Postal\s*:?\s*(\d{5})"),
        ("regimen_fiscal", r"(?i)R[eé]gimen\s+Fiscal\s*:?\s*([^\n]+)"),
        ("razon_social", r"(?i)(?:Denominaci[oó]n\s*/?\s*Raz[oó]n\s+Social|Raz[oó]n\s+Social)\s*:?\s*([^\n]+)"),
    ]
    for k, pat in patterns:
        mm = re.search(pat, text)
        if mm:
            v = mm.group(1).strip(" :.-\t")
            if v: out[k] = v
    return out
