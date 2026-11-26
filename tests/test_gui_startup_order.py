"""Regression: dependent tabs must not precede their base forms."""
import ast
from pathlib import Path


def test_manual_form_initialized_before_customer_tab():
    src = Path(__file__).resolve().parents[1] / "arybot" / "gui.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    app = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "App")
    build = next(n for n in app.body if isinstance(n, ast.FunctionDef) and n.name == "_build")
    calls = {}
    for node in ast.walk(build):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                calls.setdefault(node.func.attr, []).append(node.lineno)
            elif isinstance(node.func, ast.Name):
                calls.setdefault(node.func.id, []).append(node.lineno)
    assert len(calls["_build_test"]) == 1
    assert calls["_build_test"][0] < calls["AltaClientesTab"][0]
    assert calls["_build_panel"][0] < calls["AltaClientesTab"][0]
    assert calls["_build_cfg"][0] < calls["AltaClientesTab"][0]
