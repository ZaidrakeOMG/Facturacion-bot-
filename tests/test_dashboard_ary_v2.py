"""Regression tests for dashboard counts; no Windows/Polaris/Gmail required."""
import json
import sqlite3
from datetime import datetime, timedelta

from arybot.dashboard import period_start, read_jobs, summarize


def make_row(state, *, safe=0, kind="FACTURA", station="ARY_I", origin="Portal", stamp=None):
    stamp = stamp or datetime(2026, 11, 26, 10).timestamp()
    return dict(id=state, tipo=kind, origen=origin, seguro=safe, estado=state,
                datos=json.dumps({"estacion": station, "ticket": "123", "forma_pago": "EFECTIVO"}),
                creado=stamp, iniciado=stamp+10, terminado=stamp+70)


def test_real_invoices_only():
    rows = [
        make_row("ENVIO_SOLICITADO"),
        make_row("ENVIO_SOLICITADO", safe=1),
        make_row("YA_FACTURADO"),
        make_row("PREPARADA_FACTURA", safe=1),
        make_row("REVISION_REQUERIDA"),
        make_row("EN_COLA", station="ARY_II"),
        make_row("ALTA_CONFIRMADA", kind="ALTA"),
        make_row("DUPLICADA"),
        make_row("FINALIZADA"),
    ]
    result = summarize(rows)
    assert result["totals"]["solicitudes"] == 9
    assert result["totals"]["procesadas"] == 1
    assert result["totals"]["pruebas"] == 2
    assert result["totals"]["ya_facturado"] == 1
    assert result["totals"]["revision"] == 2
    assert result["totals"]["pendientes"] == 1
    assert result["totals"]["duplicadas"] == 1
    assert result["totals"]["altas"] == 1
    assert result["stations"]["ARY_II"]["pendientes"] == 1
    assert result["payments"]["EFECTIVO"] == 1
    assert result["average"] == 60


def test_period_and_read_only(tmp_path):
    path = tmp_path / "cola.sqlite3"
    now = datetime(2026, 11, 26, 12)
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE trabajos (
          id TEXT, tipo TEXT, origen TEXT, seguro INTEGER, estado TEXT, datos TEXT,
          creado REAL, iniciado REAL, terminado REAL)""")
        for state, stamp in [("EN_COLA", now.timestamp()),
                             ("YA_FACTURADO", (now-timedelta(days=8)).timestamp())]:
            row = make_row(state, stamp=stamp)
            db.execute("""INSERT INTO trabajos VALUES (:id,:tipo,:origen,:seguro,:estado,
                        :datos,:creado,:iniciado,:terminado)""", row)
        aux = make_row("PRUEBA_PENDIENTE", kind="PAGO", stamp=now.timestamp())
        db.execute("""INSERT INTO trabajos VALUES (:id,:tipo,:origen,:seguro,:estado,
                    :datos,:creado,:iniciado,:terminado)""", aux)
    original = path.read_bytes()
    assert len(read_jobs(path, "Hoy", now)) == 1
    assert len(read_jobs(path, "Últimos 7 días", now)) == 1
    assert len(read_jobs(path, "Este mes", now)) == 2
    assert len(read_jobs(path, "Todo el historial", now)) == 2
    assert path.read_bytes() == original
    assert period_start("Hoy", now) == now.replace(hour=0, minute=0).timestamp()
