"""Native Polaris pilot: exact local control identity, no screen coordinates.

The pilot is opt-in, safe-mode only. The operator calibrates a control once,
then the robot resolves it again in the current window. No HWND is persisted,
no customer data is read by the inspector, and Groq cannot approve mappings.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
from pathlib import Path

ROLES = {
    "rfc": ("busqueda", "edit", "RFC de búsqueda"),
    "folio": ("factura", "edit", "Folio del ticket"),
    "forma_pago": ("factura", "combo", "Forma de pago"),
    "uso_cfdi": ("factura", "combo", "Uso CFDI"),
    "buscar": ("busqueda", "button", "Buscar"),
    "agregar_folio": ("factura", "button", "Agregar Folio"),
}
REQUIRED = frozenset({"rfc", "folio"})
BUTTON_LABELS = {"buscar": {"BUSCAR"}, "agregar_folio": {"AGREGARFOLIO"}}
PUBLIC_LABELS = {"RFC", "RFCDEBUSQUEDA", "FOLIO", "FOLIODELTICKET", "TICKET",
                 "FORMAPAGO", "FORMADEPAGO", "USOCFDI", "BUSCAR", "AGREGARFOLIO",
                 "BUSCARCLIENTE", "ACEPTAR", "CANCELAR", "CUENTA", "FECHA"}


class NativeError(RuntimeError):
    pass


def requested(bot):
    return getattr(bot, "_native_pilot", False) is True


def active(bot):
    return (requested(bot) and getattr(bot, "_factura_test_mode", False) is True
            and bot.cfg.get("app", {}).get("modo_prueba", True) is True)


def path_for(bot):
    return Path(bot.base) / "data" / "native_controls.json"


def load_map(bot):
    path = path_for(bot)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError()
        maps = raw["controls"]
        if not isinstance(maps, dict) or not set(maps) <= set(ROLES):
            raise ValueError()
        for role, sig in maps.items():
            if not valid_signature(sig):
                raise ValueError()
        return maps
    except (OSError, ValueError, TypeError, KeyError):
        raise NativeError("El mapa local de controles es inválido; vuelve a calibrarlo.") from None


def valid_signature(sig):
    return (isinstance(sig, list) and 2 <= len(sig) <= 20
            and all(isinstance(p, list) and len(p) == 2 and isinstance(p[0], str)
                    and re.fullmatch(r"[A-Za-z0-9_.$:\-]{1,100}", p[0])
                    and type(p[1]) is int for p in sig)
            and any(p[1] != 0 for p in sig[1:]))


def save_mapping(bot, role, signature):
    if role not in ROLES or not valid_signature(signature):
        raise NativeError("El control no tiene una identidad nativa utilizable.")
    maps = load_map(bot)
    if any(other != role and ROLES[other][0] == ROLES[role][0] and sig == signature
           for other, sig in maps.items()):
        raise NativeError("El mismo control ya está asignado a otra función.")
    maps[role] = signature
    path = path_for(bot)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "controls": maps}, indent=2), encoding="utf-8")
    tmp.replace(path)


def clear_mapping(bot, role):
    maps = load_map(bot)
    maps.pop(role, None)
    path = path_for(bot)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "controls": maps}, indent=2), encoding="utf-8")
    tmp.replace(path)


def mapped(bot, role):
    if role not in ROLES or not requested(bot):
        return False
    if not active(bot):
        return False
    maps = load_map(bot)
    if role in REQUIRED and role not in maps:
        raise NativeError('Falta una identidad obligatoria; no se utilizarán coordenadas.')
    return role in maps


def preflight(bot, test_mode):
    if not requested(bot):
        return
    if test_mode is not True or bot.cfg.get("app", {}).get("modo_prueba", True) is not True:
        raise NativeError("Piloto nativo activo: solo permite facturas en MODO SEGURO.")
    if not REQUIRED <= set(load_map(bot)):
        raise NativeError("Calibra primero RFC de búsqueda y Folio en Motor de automatización.")


def _windows():
    from . import polaris
    return polaris.win32gui


def _id(hwnd):
    from ctypes import wintypes
    dll = ctypes.WinDLL("user32", use_last_error=True)
    fn = dll.GetDlgCtrlID
    fn.argtypes = [wintypes.HWND]
    fn.restype = ctypes.c_int
    return int(fn(hwnd))


def _class(bot, h):
    name = bot._class_name(h)
    if not re.fullmatch(r"[A-Za-z0-9_.$:\-]{1,100}", name):
        raise NativeError("Clase de control no reconocida.")
    return name.upper()


def _verify_process(bot, root):
    import psutil
    pid = bot._pid_window(root)
    expected = str(bot.pcfg.get("executable") or "")
    if not pid or not expected:
        raise NativeError("No se pudo comprobar el proceso de Polaris.")
    try:
        real = psutil.Process(pid).exe()
        if os.path.normcase(os.path.realpath(real)) != os.path.normcase(os.path.realpath(expected)):
            raise NativeError("La ventana no pertenece al ejecutable de Polaris configurado.")
    except (OSError, psutil.Error):
        raise NativeError("No se pudo comprobar el ejecutable de Polaris.") from None
    if not bot._window_exists_visible(root):
        raise NativeError("La ventana de Polaris ya no está visible.")


def find_root(bot, role):
    if role not in ROLES:
        raise NativeError("Función no admitida.")
    scope = ROLES[role][0]
    root = (bot._invoice_window() if scope == "factura"
            else bot._find_parent_containing_text("Seleccione el cliente deseado"))
    if not root:
        raise NativeError("Abre en Polaris " + ("Facturación de Efectivo." if scope == "factura"
                                                  else "el catálogo de búsqueda de clientes."))
    _verify_process(bot, root)
    if scope == "factura" and not bot._factura_controles_listos(root):
        raise NativeError("El formulario de facturación aún no expone sus controles completos.")
    return root


def _signature(bot, root, hwnd):
    w = _windows()
    chain = []
    current = hwnd
    seen = set()
    while current and current not in seen and len(chain) < 20:
        if not bot._same_polaris_process(root, current):
            break
        seen.add(current)
        chain.append([_class(bot, current), 0 if current == root else _id(current)])
        if current == root:
            signature = list(reversed(chain))
            if not valid_signature(signature):
                raise NativeError("El control no expone un identificador nativo utilizable.")
            return signature
        current = w.GetParent(current)
    raise NativeError("El control no pertenece al formulario esperado.")


def _kind(bot, root, hwnd):
    w = _windows()
    cls = _class(bot, hwnd)
    if "COMBO" in cls:
        return "combo"
    if "BUTTON" in cls or "BITBTN" in cls:
        return "button"
    if "EDIT" in cls and "DATE" not in cls:
        current = w.GetParent(hwnd)
        while current and current != root:
            parent_class = _class(bot, current)
            if any(word in parent_class for word in ("COMBO", "DATE", "PASSWORD")):
                return ""
            current = w.GetParent(current)
        if "PASSWORD" not in cls and not (int(w.GetWindowLong(hwnd, -16)) & 0x20):
            return "edit"
    return ""


def _label(bot, hwnd, kind, *, uia_names=False):
    # A textbox's current value must never be included in the inspector or API.
    w = _windows()
    if kind == "button":
        name = bot._norm(w.GetWindowText(hwnd) or "")
        if name in PUBLIC_LABELS:
            return name
    if uia_names:
        try:
            from pywinauto.uia_element_info import UIAElementInfo
            name = bot._norm(UIAElementInfo(hwnd).name or "")
            if name in PUBLIC_LABELS:
                return name
        except Exception:
            pass
    return ""


def _allowed_label(role, label):
    return role not in BUTTON_LABELS or label in BUTTON_LABELS[role]


def inventory(bot, root, role, *, uia_names=False):
    _verify_process(bot, root)
    if find_root(bot, role) != root:
        raise NativeError("La ventana de Polaris cambió.")
    w = _windows()
    expected_kind = ROLES[role][1]
    rows = []
    def visit(h, _):
        try:
            if (not bot._window_exists_visible(h) or not bot._same_polaris_process(root, h)
                    or not bot._is_descendant_or_same(root, h)
                    or _kind(bot, root, h) != expected_kind):
                return True
            label = _label(bot, h, expected_kind, uia_names=uia_names)
            if not _allowed_label(role, label):
                return True
            rows.append({"handle": int(h), "signature": _signature(bot, root, h),
                         "class": _class(bot, h), "label": label, "enabled": bool(w.IsWindowEnabled(h))})
        except (NativeError, OSError):
            pass
        return True
    w.EnumChildWindows(root, visit, None)
    if len(rows) > 300:
        raise NativeError("Demasiados controles para verificar una identidad de forma segura.")
    return rows


def unique_match(rows, signature, *, enabled=True):
    matches = [r for r in rows if r["signature"] == signature]
    if len(matches) != 1 or (enabled and not matches[0]["enabled"]):
        raise NativeError("Identidad ausente, ambigua o deshabilitada. No se usará una coordenada de respaldo.")
    return matches[0]


def calibrate(bot, root, role, hwnd):
    rows = inventory(bot, root, role)
    chosen = [r for r in rows if r["handle"] == hwnd]
    if len(chosen) != 1:
        raise NativeError("El control seleccionado no es válido para esta función.")
    signature = chosen[0]["signature"]
    unique_match(rows, signature, enabled=False)
    save_mapping(bot, role, signature)
    return signature


def focused_candidate(bot, root, role):
    w = _windows()
    from .factura_captura import CapturaFactura
    if not bot._same_polaris_process(root, w.GetForegroundWindow()):
        raise NativeError("Polaris debe tener el foco para calibrar.")
    h = CapturaFactura(bot).foco(root)
    if not h or not bot._is_descendant_or_same(root, h):
        raise NativeError("El foco no está en el formulario indicado.")
    expected = ROLES[role][1]
    while h and h != root:
        if _kind(bot, root, h) == expected:
            return int(h)
        h = w.GetParent(h)
    raise NativeError("El campo enfocado no coincide con el tipo solicitado.")


def resolve(bot, root, role, *, lectura=False):
    if not active(bot):
        raise NativeError("La automatización nativa no está activa para esta operación.")
    signature = load_map(bot).get(role)
    if signature is None:
        raise NativeError("Falta calibrar " + ROLES[role][2] + ".")
    row = unique_match(inventory(bot, root, role), signature, enabled=not lectura)
    bot.log("MOTOR NATIVO: " + ROLES[role][2] + " localizado por identidad de control.")
    return row["handle"]


def _guard(bot, root, h):
    w = _windows()
    if (not active(bot) or not bot._window_exists_visible(root)
            or not w.IsWindowEnabled(root) or not bot._window_exists_visible(h)
            or not w.IsWindowEnabled(h) or not bot._same_polaris_process(root, h)
            or not bot._is_descendant_or_same(root, h)
            or not bot._same_polaris_process(root, w.GetForegroundWindow())):
        raise NativeError("Cambió el foco, formulario o modo de seguridad; operación detenida.")


def focus_native(bot, root, h):
    from . import polaris
    polaris.pyautogui.failSafeCheck()
    _guard(bot, root, h)
    try:
        from pywinauto.uia_element_info import UIAElementInfo
        element = UIAElementInfo(h).element
        if int(element.CurrentNativeWindowHandle) != int(h):
            raise NativeError("UIA no confirmó la identidad del control.")
        element.SetFocus()
    except Exception:
        raise NativeError("El control no admite foco nativo UIA. No se hará clic de respaldo.") from None
    _guard(bot, root, h)


def invoke_mapped(bot, root, role):
    if role not in BUTTON_LABELS:
        raise NativeError("Este botón no está autorizado para el piloto.")
    from . import polaris
    polaris.pyautogui.failSafeCheck()
    h = resolve(bot, root, role)
    _guard(bot, root, h)
    if _label(bot, h, "button") not in BUTTON_LABELS[role]:
        raise NativeError("La etiqueta del botón cambió.")
    try:
        from pywinauto import Desktop
        wrapper = Desktop(backend="uia").window(handle=h).wrapper_object()
        if int(wrapper.element_info.element.CurrentNativeWindowHandle) != int(h):
            raise NativeError("UIA no confirmó la identidad del botón.")
        pattern = wrapper.iface_invoke
    except Exception:
        raise NativeError("El botón no expone InvokePattern. No se hará clic.") from None
    _guard(bot, root, h)
    if resolve(bot, root, role) != h:
        raise NativeError("El botón cambió antes de ejecutar.")
    try:
        pattern.Invoke()
    except Exception:
        raise NativeError("Resultado de invocación incierto; revisa Polaris. No se repite.") from None
    bot.log("MOTOR NATIVO: invocación UIA solicitada una sola vez: " + ROLES[role][2])


def enrich_labels(bot, rows):
    """Local UIA names only: retain only the closed set of known UI labels."""
    import pythoncom
    pythoncom.CoInitialize()
    try:
        result = []
        for row in rows:
            copy = dict(row)
            if not copy['label']:
                copy['label'] = _label(bot, row['handle'], 'edit', uia_names=True)
            result.append(copy)
        return result
    finally:
        pythoncom.CoUninitialize()


def groq_candidates(role, rows):
    """Only fixed metadata and ephemeral aliases; no HWND, text values or path."""
    if role not in ROLES or not 1 <= len(rows) <= 24:
        raise NativeError("Selecciona entre 1 y 24 controles para el análisis.")
    candidates = {}
    parts = ["Funcion: " + ROLES[role][2]]
    for i, row in enumerate(rows, 1):
        code = "C" + str(i)
        if not valid_signature(row["signature"]):
            raise NativeError("Identidad de control inválida.")
        cls = row["class"]
        if not re.fullmatch(r"[A-Z0-9_.$:\-]{1,100}", cls):
            raise NativeError("Clase de control inválida.")
        label = row["label"] if row["label"] in PUBLIC_LABELS else ""
        parts.append(f"{code} | clase={cls} | etiqueta={label or 'sin etiqueta'} | habilitado={bool(row['enabled'])}")
        candidates[code] = row["handle"]
    payload = "\n".join(parts)
    if len(payload) > 1800:
        raise NativeError("Hay demasiados controles para una consulta. Selecciona un grupo menor.")
    return payload, candidates


def validate_suggestion(reply, candidates):
    data = reply.data
    code = data.get("control")
    if code == "NONE":
        return None
    if code not in candidates:
        raise NativeError("Groq sugirió un control que no está en el inventario.")
    return candidates[code]
