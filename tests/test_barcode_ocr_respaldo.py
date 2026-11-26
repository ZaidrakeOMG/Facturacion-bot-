"""Regresiones OCR del portal; no abre Polaris ni timbra."""
import base64
import io
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw
from arybot.ticket_foto import (
    FotoTicketError, analizar_codigo_barras_foto, _folio_ocr_bajo_barras,
    _zona_numero_bajo_barras,
)


def foto():
    im = Image.new('RGB', (650, 500), 'white')
    d = ImageDraw.Draw(im)
    for x in range(75, 575, 17):
        d.rectangle((x, 200, x + 7, 380), fill='black')
    return im


def contenido():
    b = io.BytesIO()
    foto().save(b, format='JPEG')
    return base64.b64encode(b.getvalue()).decode('ascii')


def test_zona_bajo_barras():
    zona = _zona_numero_bajo_barras(foto())
    assert zona is not None and zona.height > 30


def test_ocr_ceros():
    with patch('arybot.ocr_ticket.find_tesseract', return_value='tesseract'), \
         patch('pytesseract.image_to_string', side_effect=['', '0000123456\n', '0000123456\n']):
        assert _folio_ocr_bajo_barras(foto()) == '0000123456'


@pytest.mark.parametrize('lecturas', [
    ['0000123456', '0000123456', '0000123458'],
    ['0000123456', '', ''],
    ['$0000123456', '$0000123456', ''],
    ['000123456', '000123456', '000123456'],
])
def test_rechaza_dudas(lecturas):
    with patch('arybot.ocr_ticket.find_tesseract', return_value='tesseract'), \
         patch('pytesseract.image_to_string', side_effect=lecturas):
        assert _folio_ocr_bajo_barras(foto()) is None


def test_portal_usa_ocr():
    with patch('arybot.ticket_foto._folios_codigo_barras', return_value=[]), \
         patch('arybot.ticket_foto._folio_ocr_bajo_barras', return_value='0000123456'):
        assert analizar_codigo_barras_foto(contenido()) == {'folio': '0000123456'}


def test_portal_usa_ocr_si_motor_no_disponible():
    with patch('arybot.ticket_foto._folios_codigo_barras', side_effect=FotoTicketError('motor')), \
         patch('arybot.ticket_foto._folio_ocr_bajo_barras', return_value='0000123456'):
        assert analizar_codigo_barras_foto(contenido()) == {'folio': '0000123456'}


def test_portal_prefiere_barcode_y_rechaza_ambiguedad():
    with patch('arybot.ticket_foto._folios_codigo_barras', return_value=['0000123456']), \
         patch('arybot.ticket_foto._folio_ocr_bajo_barras') as ocr:
        assert analizar_codigo_barras_foto(contenido()) == {'folio': '0000123456'}
        ocr.assert_not_called()
    with patch('arybot.ticket_foto._folios_codigo_barras', return_value=['0000123456','0000123458']), \
         patch('arybot.ticket_foto._folio_ocr_bajo_barras') as ocr:
        with pytest.raises(FotoTicketError, match='codigos distintos'):
            analizar_codigo_barras_foto(contenido())
        ocr.assert_not_called()
