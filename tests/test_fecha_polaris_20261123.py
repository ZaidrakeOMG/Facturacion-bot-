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
