"""Regresiones SAT/portal/Polaris del campo Régimen Fiscal.

No usa Windows real, no escribe en Polaris y no da de alta clientes.
"""
from unittest.mock import Mock
import pytest

from arybot.cliente_model import AltaCliente, AltaClienteError
from arybot.regimen_fiscal import REGIMENES, validar_regimen, normalizar_regimen
from arybot.constancia_fiscal import IdentidadFiscal
from arybot.portal_modelo import validar_entrada, EntradaPortalError, catalogos
from arybot import portal_modelo
from arybot.alta_clientes import PantallaAlta


def alta(rfc, regimen):
    return AltaCliente.crear(
        'ARY IV', rfc, '00123456789', '', 'cliente@example.com',
        razon_social='RAZON FISCAL DEMO', calle_constancia='CALLE DEMO',
        regimen_fiscal=regimen
    )


def test_601_persona_moral_and_612_persona_fisica():
    assert alta('AAA010101AAA', '601').regimen_fiscal == '601'
    assert alta('AAAA010101AAA', '612').regimen_fiscal == '612'
    assert alta('AAA010101AAA', '601').campos_cliente()['regimen_fiscal'] == '601'


def test_601_no_corresponde_a_rfc_de_persona_fisica():
    with pytest.raises(AltaClienteError, match='no corresponde'):
        alta('AAAA010101AAA', '601')
    with pytest.raises(AltaClienteError, match='no corresponde'):
        alta('AAA010101AAA', '612')


def test_no_asignar_un_regimen_por_defecto():
    assert alta('AAA010101AAA', '').regimen_fiscal == ''
    with pytest.raises(ValueError, match='Selecciona'):
        validar_regimen('AAA010101AAA', '')


def test_catalogo_no_cambia_codigos_regimen():
    assert len(REGIMENES) == 19
    choices = catalogos()['regimenes_fiscales']
    assert len(choices) == 19
    assert any(x['valor'] == '601' and x['tipo'] == 'M' for x in choices)
    assert any(x['valor'] == '612' and x['tipo'] == 'F' for x in choices)


def test_portal_alta_exige_regimen_e_identifica_compatibilidad(monkeypatch):
    monkeypatch.setattr(portal_modelo, 'leer_constancia_pdf_base64',
        lambda *a, **k: IdentidadFiscal(
            'AAA010101AAA', 'EMPRESA DEMO', 'CALLE DEMO', '00123456789'))
    body = {
        'tipo':'ALTA', 'estacion':'ARY IV', 'constancia_pdf':'PDF-FAKE',
        'telefono':'', 'correo':'cliente@example.com',
        'correo_confirmacion':'cliente@example.com',
        'confirmado':True, 'sitio_web':'',
    }
    with pytest.raises(EntradaPortalError, match='régimen fiscal'):
        validar_entrada(body)
    body['regimen_fiscal'] = '612'
    with pytest.raises(EntradaPortalError, match='no corresponde'):
        validar_entrada(body)
    body['regimen_fiscal'] = '601'
    kind, sol, data = validar_entrada(body)
    assert kind == 'ALTA'
    assert sol.regimen_fiscal == '601'
    assert data['regimen_fiscal'] == '601'
    assert 'constancia_pdf' not in data


def test_polaris_no_confunde_regimen_por_aproximacion():
    assert (normalizar_regimen('GENERAL DE LEY PERSONAS MORALES') ==
            normalizar_regimen('General de Ley Personas Morales'))
    assert (normalizar_regimen('PERSONAS MORALES CON FINES NO LUCRATIVOS') !=
            normalizar_regimen('General de Ley Personas Morales'))


def test_seleccion_polaris_si_ya_esta_correcta_no_toca_combo():
    # Fake HWND; el controlador real verifica visibilidad y pertenencia.
    ui=PantallaAlta.__new__(PantallaAlta)
    ui.exigir_nuevo=Mock()
    ui._regimen_control=Mock(return_value=4)
    ui._regimen_actual=Mock(return_value='GENERAL DE LEY PERSONAS MORALES')
    ui.verificar_regimen=Mock(return_value=True)
    assert ui.seleccionar_regimen(1,2,'601')
    ui.verificar_regimen.assert_called_once_with(1,2,'601')


def test_polaris_rechaza_regimen_desconocido_antes_de_aceptar():
    ui=PantallaAlta.__new__(PantallaAlta)
    with pytest.raises(AltaClienteError, match='no válido'):
        ui.seleccionar_regimen(1,2,'000')
