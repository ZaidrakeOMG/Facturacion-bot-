"""Offline regression tests for native mapping and safety boundaries."""
from types import SimpleNamespace

import pytest

from arybot import native_controls as n
from arybot.groq_service import GroqError, validate_reply


def sig(ctrl_id, root="TFORMFACTURA"):
    return [[root, 0], ["TCXCUSTOMINNERTEXTEDIT", ctrl_id]]


def bot(tmp_path):
    return SimpleNamespace(base=tmp_path, cfg={"app": {"modo_prueba": True}},
                           _native_pilot=True, _factura_test_mode=True)


def row(signature, handle=123, enabled=True, label="FOLIO"):
    return {"signature": signature, "handle": handle, "enabled": enabled,
            "class": signature[-1][0], "label": label}


def test_only_unique_enabled_native_control_can_resolve():
    a = row(sig(41))
    b = row(sig(42), 456)
    assert n.unique_match([a, b], sig(41))["handle"] == 123
    with pytest.raises(n.NativeError):
        n.unique_match([a, row(sig(41), 789)], sig(41))
    with pytest.raises(n.NativeError):
        n.unique_match([b], sig(41))
    with pytest.raises(n.NativeError):
        n.unique_match([row(sig(41), enabled=False)], sig(41))


def test_pilot_needs_no_calibration_and_is_safe_mode_only(tmp_path):
    b = bot(tmp_path)
    b.log = lambda msg: None
    n.preflight(b, True)
    assert not n.mapped(b, "rfc")
    assert not n.mapped(b, "folio")
    n.save_mapping(b, "rfc", sig(51, "TFORMBUSQUEDA"))
    n.save_mapping(b, "folio", sig(52))
    n.preflight(b, True)
    assert n.mapped(b, "rfc")
    assert n.mapped(b, "folio")
    assert not n.mapped(b, "forma_pago")
    with pytest.raises(n.NativeError):
        n.preflight(b, False)
    b.cfg["app"]["modo_prueba"] = False
    with pytest.raises(n.NativeError):
        n.preflight(b, True)
    b._native_pilot = False
    n.preflight(b, False)
    assert not n.mapped(b, "folio")


def test_missing_mapping_does_not_block_original_before_action(tmp_path):
    b = bot(tmp_path)
    n.save_mapping(b, "rfc", sig(51, "TFORMBUSQUEDA"))
    n.save_mapping(b, "folio", sig(52))
    n.clear_mapping(b, "folio")
    assert not n.mapped(b, "folio")


def test_map_keeps_structure_not_handles_and_rejects_duplicates(tmp_path):
    b = bot(tmp_path)
    n.save_mapping(b, "folio", sig(52))
    with pytest.raises(n.NativeError):
        n.save_mapping(b, "forma_pago", sig(52))
    with pytest.raises(n.NativeError):
        n.save_mapping(b, "aceptar_final", sig(53))
    with pytest.raises(n.NativeError):
        n.save_mapping(b, "rfc", [["TFORM", 0], ["EDIT", 0]])
    assert n.load_map(b) == {"folio": sig(52)}
    assert "handle" not in n.path_for(b).read_text()


def test_groq_only_gets_public_metadata_and_cannot_execute():
    a = row(sig(41), handle=123456789, label="cliente@example.com")
    a["private_value"] = "AAA010101AAA"
    a["x"] = 100
    payload, aliases = n.groq_candidates("folio", [a])
    assert "cliente@example.com" not in payload
    assert "AAA010101AAA" not in payload
    assert "123456789" not in payload
    assert aliases == {"C1": 123456789}
    assert n.validate_suggestion(SimpleNamespace(data={"control": "C1"}), aliases) == 123456789
    assert n.validate_suggestion(SimpleNamespace(data={"control": "NONE"}), aliases) is None
    with pytest.raises(n.NativeError):
        n.validate_suggestion(SimpleNamespace(data={"control": "C2"}), aliases)
    with pytest.raises(GroqError):
        validate_reply("control", {"control": "aceptar", "motivo": "no"})


def test_exact_label_only_and_no_ambiguous_choice():
    a = row(sig(41), label="FOLIO")
    assert n.exact_candidate("folio", [a]) == a
    assert n.exact_candidate("folio", [row(sig(41), label="CUENTA")]) is None
    assert n.exact_candidate("folio", [a, row(sig(42), handle=456, label="FOLIO")]) is None
    assert n.exact_candidate("folio", [row(sig(41), enabled=False)]) is None
    assert n.exact_candidate("folio", [a, row(sig(41), handle=456, label="")]) is None


def test_auto_cache_requires_geometry_and_fresh_identity(tmp_path, monkeypatch):
    b = bot(tmp_path)
    candidate = row(sig(41), label="")
    n.save_mapping(b, "folio", candidate["signature"])
    monkeypatch.setattr(n, "inventory", lambda *a: [candidate])
    monkeypatch.setattr(n, "enrich_labels", lambda b, rows: rows)
    monkeypatch.setattr(n, "corroborates_geometry", lambda *a: False)
    assert n.auto_row(b, 9, "folio") is None
    monkeypatch.setattr(n, "corroborates_geometry", lambda *a: True)
    assert n.auto_row(b, 9, "folio") == candidate
    candidate["label"] = "CUENTA"
    assert n.auto_row(b, 9, "folio") is None


def test_no_native_action_when_identity_disappears(tmp_path, monkeypatch):
    b = bot(tmp_path)
    monkeypatch.setattr(n, "auto_row", lambda *a: None)
    assert not n.mapped(b, "folio", 9)
    with pytest.raises(n.NativeError, match="Cambió la identidad"):
        n.resolve(b, 9, "folio")
