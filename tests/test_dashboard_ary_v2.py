"""Regression tests for dashboard counts; no Windows/Polaris/Gmail required."""
import json
import sqlite3
from datetime import datetime, timedelta

from arybot.dashboard import period_start, read_jobs, summarize
from arybot.cola_modelo import resultado_facturacion


def make_row(state, *, safe=0, kind="FACTURA", station="ARY_I", origin="Portal", stamp=None):
    stamp = stamp or datetime(2026, 11, 26, 10).timestamp()
    return dict(id=state, tipo=kind, origen=origin, seguro=safe, estado=state,
                datos=json.dumps({"estacion": station, "ticket": "123", "forma_pago": "EFECTIVO"}),
                creado=stamp, iniciado=stamp+10, terminado=stamp+70, motivo="")


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
    assert result["totals"]["kpi_si"] == 1
    assert result["totals"]["kpi_no"] == 1
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
          creado REAL, iniciado REAL, terminado REAL, motivo TEXT)""")
        for state, stamp in [("EN_COLA", now.timestamp()),
                             ("YA_FACTURADO", (now-timedelta(days=8)).timestamp())]:
            row = make_row(state, stamp=stamp)
            db.execute("""INSERT INTO trabajos VALUES (:id,:tipo,:origen,:seguro,:estado,
                        :datos,:creado,:iniciado,:terminado,:motivo)""", row)
        aux = make_row("PRUEBA_PENDIENTE", kind="PAGO", stamp=now.timestamp())
        db.execute("""INSERT INTO trabajos VALUES (:id,:tipo,:origen,:seguro,:estado,
                    :datos,:creado,:iniciado,:terminado,:motivo)""", aux)
    original = path.read_bytes()
    assert len(read_jobs(path, "Hoy", now)) == 1
    assert len(read_jobs(path, "Últimos 7 días", now)) == 1
    assert len(read_jobs(path, "Este mes", now)) == 2
    assert len(read_jobs(path, "Todo el historial", now)) == 2
    assert path.read_bytes() == original
    assert period_start("Hoy", now) == now.replace(hour=0, minute=0).timestamp()


def test_resultado_fiscal_si_no_y_motivo():
    assert resultado_facturacion(dict(tipo='FACTURA', estado='ENVIO_SOLICITADO',
                                     seguro=0, motivo=''))[0] == 'SÍ'
    assert resultado_facturacion(dict(tipo='FACTURA', estado='YA_FACTURADO',
                                     seguro=0, motivo=''))[0] == 'SÍ'
    answer, reason = resultado_facturacion(dict(
        tipo='FACTURA', estado='REVISION_REQUERIDA', seguro=0,
        motivo='No se encontró el RFC antes de Aceptar'))
    assert answer == 'NO' and 'RFC' in reason
    assert resultado_facturacion(dict(tipo='FACTURA', estado='EN_COLA',
                                     seguro=0, motivo=''))[0] == '—'
    assert resultado_facturacion(dict(tipo='FACTURA', estado='PRUEBA_CERRADA',
                                     seguro=1, motivo=''))[0] == 'NO'
    unknown, explanation = resultado_facturacion(dict(
        tipo='FACTURA', estado='REVISION_REQUERIDA', seguro=0,
        motivo='Posible CFDI timbrado o correo pendiente.'))
    assert unknown == 'SIN CONFIRMAR' and 'Polaris' in explanation


def test_panel_no_muestra_estado_tecnico_en_filas():
    rows = [make_row('REVISION_REQUERIDA'), make_row('ENVIO_SOLICITADO')]
    summary = summarize(rows)
    assert summary['details'][0][4] == 'NO'
    assert summary['details'][1][4] == 'SÍ'
    assert all('REVISION_REQUERIDA' not in d[5] for d in summary['details'])


def test_dos_kpi_excluyen_pendientes_pruebas_y_factura_incierta():
    rows = [
        make_row("ENVIO_SOLICITADO"),
        make_row("ENVIO_SOLICITADO", safe=1),
        make_row("YA_FACTURADO"),
        make_row("EN_COLA"),
        make_row("EJECUTANDO"),
        make_row("PRUEBA_CERRADA", safe=1),
        make_row("REVISION_REQUERIDA", safe=0),
        make_row("ALTA_CONFIRMADA", kind="ALTA"),
        make_row("DUPLICADA"),
    ]
    rows.append(dict(make_row("REVISION_REQUERIDA"), id="incierta",
                     motivo="Posible CFDI timbrado o correo pendiente."))
    s = summarize(rows)
    assert s["totals"]["kpi_si"] == 1
    assert s["totals"]["kpi_no"] == 1
    assert s["totals"]["inciertas"] == 1


def test_los_dos_resultados_no_inventan_cfdi_ya_existentes():
    rows = [
        make_row("YA_FACTURADO"),
        make_row("REVISION_CERRADA", safe=0),
        make_row("ESPERANDO_DATOS", safe=0),
        make_row("REVISION_REQUERIDA", safe=1),
        make_row("CANCELADA", safe=0),
    ]
    totals = summarize(rows)["totals"]
    assert totals["kpi_si"] == 0
    assert totals["kpi_no"] == 2


def test_estadisticas_nueva_etapa_ocultan_solicitudes_archivadas(tmp_path):
    from arybot.cola_local import ColaLocal
    store=ColaLocal(tmp_path)
    old,_=store.enqueue('FACTURA',{'ticket':'10001','estacion':'ARY I'},
                        source='Portal web',safe=False)
    store.finish(old['id'],'ENVIO_SOLICITADO',reason='Flujo completado')
    store.archivar_solicitudes(old['id'],confirmado=True)
    new,_=store.enqueue('FACTURA',{'ticket':'10002','estacion':'ARY II'},
                        source='Portal web',safe=False)
    hoy=read_jobs(store.path,'Hoy')
    assert [job['id'] for job in hoy]==[new['id']]
    historial=read_jobs(store.path,'Todo el historial')
    assert {job['id'] for job in historial}=={old['id'],new['id']}
