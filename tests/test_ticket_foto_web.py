"""Pruebas del filtro de tickets; no se necesita Polaris ni conexión a Internet."""
import base64
import io
from unittest.mock import patch

import pytest
from PIL import Image

from arybot.ticket_foto import (
    FotoTicketError, analizar_ticket_foto, _es_reimpresion,
)


def foto():
    im = Image.new('RGB', (900, 1600), 'white')
    buf = io.BytesIO()
    im.save(buf, format='JPEG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def leer_con_respuestas(respuestas):
    with patch('arybot.ocr_ticket.find_tesseract', return_value='tesseract'), \
         patch('pytesseract.image_to_string', side_effect=respuestas):
        return analizar_ticket_foto(foto())


def test_folio_ceros_iniciales_y_confirmacion_bajo_barras():
    resultado = leer_con_respuestas([
        'FOLIO 0008430573\nOPERACION DE CONTADO\nTOTAL $500.00\n0008430573',
        'FOLIO 8688438573\nDISPENSARIO 7\nTOTAL $500.00\n0008430573',
    ])
    assert resultado['folio'] == '0008430573'


def test_reimpresion_bloquea_aunque_tenga_folio_legible():
    for marcador in ['REIMPRESIÓN', 'RE-IMPRESION', 'REIMPRESO', 'REIMP.']:
        assert _es_reimpresion(marcador)
    with pytest.raises(FotoTicketError, match='REIMPRESIÓN'):
        leer_con_respuestas([
            'REIMPRESIÓN\nFOLIO 0008430573\nTOTAL 500\n0008430573',
            'FOLIO 0008430573\nTOTAL 500\n0008430573',
        ])


def test_folio_conflictivo_no_se_acepta():
    with pytest.raises(FotoTicketError, match='más legible'):
        leer_con_respuestas([
            'FOLIO 0008430573\nTOTAL 500',
            'FOLIO 0008430578\nTOTAL 500',
        ])


def test_no_inventa_folio_con_numeros_de_telefono():
    with pytest.raises(FotoTicketError, match='más legible'):
        leer_con_respuestas([
            'SERVICIO APACHE\nTEL 8711651404\nTOTAL 500',
            'SERVICIO APACHE\nTEL 8711651404\nTOTAL 500',
        ])


def test_formato_invalido_rechazado():
    with pytest.raises(FotoTicketError):
        analizar_ticket_foto('un_folio_inventado')


def _cuerpo_final(foto='foto-ejemplo', folio='0008430573'):
    from datetime import date
    return dict(
        tipo='FACTURA', estacion='ARY V', constancia_pdf='PDF-FICTICIO',
        correo='cliente@example.com', correo_confirmacion='cliente@example.com',
        sitio_web='', confirmado=True, ticket=folio, foto_ticket=foto,
        fecha_ticket=date.today().isoformat(), forma_pago='EFECTIVO', uso_cfdi='G03',
    )


def test_no_permite_enviar_folio_manual_sin_foto():
    from arybot.portal_modelo import validar_entrada, EntradaPortalError
    from types import SimpleNamespace
    identidad = SimpleNamespace(rfc='SEP1312171X9', razon_social='EMPRESA', calle='', idcif='')
    with patch('arybot.portal_modelo.leer_constancia_pdf_base64', return_value=identidad):
        with pytest.raises(EntradaPortalError, match='foto'):
            validar_entrada(_cuerpo_final(foto=''))


def test_el_servidor_rechaza_folio_alterado_aunque_haya_foto():
    from arybot.portal_modelo import validar_entrada, EntradaPortalError
    from types import SimpleNamespace
    identidad = SimpleNamespace(rfc='SEP1312171X9', razon_social='EMPRESA', calle='', idcif='')
    with patch('arybot.portal_modelo.leer_constancia_pdf_base64', return_value=identidad), \
         patch('arybot.portal_modelo.analizar_ticket_foto', return_value={'folio': '0008430573'}):
        with pytest.raises(EntradaPortalError, match='no coincide'):
            validar_entrada(_cuerpo_final(folio='0008430574'))
        kind,sol,datos=validar_entrada(_cuerpo_final())
    assert kind == 'FACTURA' and sol.ticket == '0008430573'
    assert 'foto_ticket' not in datos
