"""Clasificación agregada y aproximada del dispositivo que envía una solicitud web.

Únicamente se almacena una categoría anónima. No conserva User-Agent,
cabeceras del navegador, IP ni huella digital. No es comprobación antifraude.
"""
from __future__ import annotations


CATEGORIAS = frozenset({"telefono", "computadora", "tableta", "no_identificado"})


def clasificar(user_agent="", client_hint_mobile=""):
    ua = (user_agent if isinstance(user_agent, str) else "")[:700].lower()
    hint = (client_hint_mobile if isinstance(client_hint_mobile, str) else "").strip()
    if "ipad" in ua or "tablet" in ua or "kindle" in ua or "silk/" in ua:
        return "tableta"
    if "android" in ua and "mobile" not in ua:
        return "tableta"
    if "iphone" in ua or "ipod" in ua or ("android" in ua and "mobile" in ua):
        return "telefono"
    if "windows phone" in ua or "windows mobile" in ua:
        return "telefono"
    if hint == "?1" and ua and not any(t in ua for t in ("ipad", "tablet", "kindle")):
        return "telefono"
    if any(t in ua for t in ("windows nt", "macintosh", "x11;", "cros ", "linux x86_64")):
        return "computadora"
    return "no_identificado"


def datos_equivalentes(uno, dos):
    """Deduplicación fiscal excluye solo metadato NO fiscal de dispositivo."""
    if not isinstance(uno, dict) or not isinstance(dos, dict):
        return False
    return {k: v for k, v in uno.items() if k != "dispositivo_origen"} == {
        k: v for k, v in dos.items() if k != "dispositivo_origen"
    }
