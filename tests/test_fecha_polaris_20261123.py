"""Regresiones de lectura segura de fecha para Polaris; no interactúan con Windows."""
from datetime import date

from arybot.factura_captura import coincide


def test_fecha_polaris_acepta_separadores_y_iso():
    assert coincide('fecha', '23/11/2026', '23/11/2026')
    assert coincide('fecha', '23-11-2026', '23/11/2026')
    assert coincide('fecha', '2026-11-23', '23/11/2026')
    assert coincide('fecha', '3/1/2026', '03/01/2026')


def test_fecha_polaris_no_valida_fecha_distinta_o_invalida():
    assert not coincide('fecha', '22/11/2026', '23/11/2026')
    assert not coincide('fecha', '', '23/11/2026')
    assert not coincide('fecha', '31/02/2026', '23/11/2026')
    assert not coincide('fecha', '11/23/2026', '23/11/2026')
    assert not coincide('fecha', 'Fecha', '23/11/2026')


def test_calendario_de_polaris_noviembre_2026_tiene_fila_previa():
    """Video real 23/11/2026: el 23 está en quinta fila, no cuarta."""
    from arybot.polaris import PolarisBot
    assert PolarisBot._calendar_day_cell(2026, 11, 23) == (1, 4)
    assert PolarisBot._calendar_day_cell(2026, 11, 16) == (1, 3)
    assert PolarisBot._calendar_day_cell(2026, 11, 1) == (0, 1)


def test_calendario_de_polaris_mes_que_no_empieza_en_domingo():
    """La semana adicional sólo debe aplicarse al comienzo en domingo."""
    from arybot.polaris import PolarisBot
    assert PolarisBot._calendar_day_cell(2026, 12, 1) == (2, 0)
    assert PolarisBot._calendar_day_cell(2026, 12, 23) == (3, 3)
    assert PolarisBot._calendar_day_cell(2022, 1, 1) == (6, 0)


def test_fecha_ticket_25_noviembre():
    from arybot.polaris import PolarisBot
    assert PolarisBot._calendar_day_cell(2026,11,25)==(3,4)


def test_plazo_fiscal_72h_sin_hora_no_acepta_fecha_de_tres_dias():
    """Nov 27: Nov 25-27 seguros; Nov 24 puede haber vencido por la hora."""
    import pytest
    from arybot.parser import fecha_facturable_portal
    hoy=date(2026,11,27)
    for d in ("2026-11-27", "2026-11-26", "2026-11-25"):
        assert fecha_facturable_portal(d,hoy=hoy)==d
    for d in ("2026-11-24", "2026-11-23", "2026-10-31"):
        with pytest.raises(ValueError):
            fecha_facturable_portal(d,hoy=hoy)


def test_plazo_mismo_mes_y_tres_fechas_contando_hoy():
    import pytest
    from arybot.parser import fecha_facturable_portal
    hoy=date(2026,11,2)
    assert fecha_facturable_portal("2026-11-01",hoy=hoy)=="2026-11-01"
    with pytest.raises(ValueError,match='mes actual'):
        fecha_facturable_portal("2026-10-31",hoy=hoy)
