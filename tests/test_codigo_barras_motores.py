"""Regresiones del lector real de barras; no abre Polaris ni emite CFDI."""
import base64
import io
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

from arybot.ticket_foto import analizar_codigo_barras_foto, FotoTicketError


def foto_sintetica():
    # CODE128-C: start C, 00, 00, 12, 34, 56, checksum 42, stop.
    # Letras mayusculas = barras; minusculas = espacios; A/a=1 modulo.
    patron = 'BaAbCbBaBbBbBaBbBbAaBbCbAcAaBcCcAaBaAaBaCcBcCaAaB'
    im = Image.new('RGB', (540, 180), 'white')
    draw = ImageDraw.Draw(im)
    x = 48
    for c in patron:
        ancho = (ord(c.upper()) - ord('A') + 1) * 4
        if c.isupper():
            draw.rectangle((x, 25, x + ancho - 1, 154), fill='black')
        x += ancho
    buf = io.BytesIO()
    im.save(buf, format='JPEG', quality=90)
    return base64.b64encode(buf.getvalue()).decode('ascii')


def test_zxing_real_code128_con_ceros_sin_pyzbar():
    pytest.importorskip('zxingcpp')
    with patch.dict('sys.modules', {'pyzbar.pyzbar': None}):
        assert analizar_codigo_barras_foto(foto_sintetica()) == {'folio': '0000123456'}


def test_pyzbar_real_code128_con_ceros_sin_zxing():
    pytest.importorskip('pyzbar.pyzbar')
    with patch.dict('sys.modules', {'zxingcpp': None}):
        assert analizar_codigo_barras_foto(foto_sintetica()) == {'folio': '0000123456'}


def test_error_de_infraestructura_no_culpa_a_la_foto():
    with patch.dict('sys.modules', {'zxingcpp': None, 'pyzbar.pyzbar': None}):
        with pytest.raises(FotoTicketError, match='no esta disponible'):
            analizar_codigo_barras_foto(foto_sintetica())
