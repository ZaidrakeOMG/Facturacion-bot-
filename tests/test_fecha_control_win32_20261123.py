"""Pruebas simuladas: lectura DevExpress anidada, sin acceso real a Polaris."""
from types import SimpleNamespace

import pytest

from arybot import polaris


def _bot(fake_windows, values):
    bot = polaris.PolarisBot.__new__(polaris.PolarisBot)
    events = []
    bot.diag = SimpleNamespace(evento=lambda *args: events.append(args))
    bot._date_hwnd_at_point = lambda inv: 10
    bot._same_polaris_process = lambda inv, h: h in (10,20)
    bot._class_name = lambda h: {10:'TcxCustomInnerTextEdit',20:'TcxDateEdit'}.get(h,'')
    bot._captura_factura = lambda: SimpleNamespace(texto=SimpleNamespace(leer=lambda h: values.get(h,'')))
    bot._screenshot_error = lambda name: None
    return bot, events


def test_lee_fecha_del_wrapper_si_editor_interno_esta_vacio(monkeypatch):
    monkeypatch.setattr(polaris,'win32gui',SimpleNamespace(GetParent=lambda h:{10:20,20:100}.get(h,0),
        GetWindowText=lambda h:''))
    bot,events = _bot(None,{10:'',20:'23-11-2026'})
    h,value = bot._read_date_control(100)
    assert (h,value)==(20,'23-11-2026')
    assert events and events[-1][1]=='LECTURA_FECHA'


def test_fecha_anidada_con_lecturas_discrepantes_se_detiene(monkeypatch):
    monkeypatch.setattr(polaris,'win32gui',SimpleNamespace(GetParent=lambda h:{10:20,20:100}.get(h,0),
        GetWindowText=lambda h:''))
    bot,_ = _bot(None,{10:'23/11/2026',20:'24/11/2026'})
    with pytest.raises(polaris.PolarisError,match='valores diferentes'):
        bot._read_date_control(100)


def test_sin_lectura_no_inventa_fecha(monkeypatch):
    monkeypatch.setattr(polaris,'win32gui',SimpleNamespace(GetParent=lambda h:{10:20,20:100}.get(h,0),
        GetWindowText=lambda h:''))
    bot,_ = _bot(None,{10:'',20:''})
    assert bot._read_date_control(100)==(10,'')
