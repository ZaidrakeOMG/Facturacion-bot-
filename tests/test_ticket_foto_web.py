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
    # El OCR del papel es otra pasada independiente; simular lo que la
    # foto contiene, no permitir que falte una etiqueta FOLIO en las pruebas.
    import re
    todas = '\n'.join(respuestas).upper()
    rotulo = bool(re.search(r'\bF[O0U]L[I1T]O\b', todas))
    compra = bool(
        re.search(r'\bTOTAL\b', todas) and
        re.search(r'\b(?:OPERACION|DISPENSARIO|LITROS|PRODUCTO|PRECIO)\b', todas)
        and re.search(r'\d+[.,]\d{2}\b', todas))
    with patch('arybot.ocr_ticket.find_tesseract', return_value='tesseract'), \
         patch('pytesseract.image_to_data', return_value={'text': []}), \
         patch('pytesseract.image_to_string', side_effect=respuestas), \
         patch('arybot.ticket_foto._evidencia_folio_y_compra',
               return_value=(rotulo, compra, False)):
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
    with pytest.raises(FotoTicketError, match='folio exacto'):
        leer_con_respuestas([
            'FOLIO 0008430573\nTOTAL 500',
            'FOLIO 0008430578\nTOTAL 500',
        ])


def test_no_inventa_folio_con_numeros_de_telefono():
    with pytest.raises(FotoTicketError, match='folio exacto'):
        leer_con_respuestas([
            'SERVICIO APACHE\nTEL 8711651404\nTOTAL 500',
            'SERVICIO APACHE\nTEL 8711651404\nTOTAL 500',
        ])



def test_barcode_corrige_digitos_ocr_y_conserva_ceros_iniciales():
    with patch('arybot.ticket_foto._folios_codigo_barras', return_value=['0008430573']):
        resultado = leer_con_respuestas([
            'FOLIO 0098430573\nTOTAL $500.00',
            'FOLIO 0003430573\nDISPENSARIO 7',
        ])
    assert resultado['folio'] == '0008430573'


def test_barcode_no_inventa_folio_distinto_del_impreso():
    with patch('arybot.ticket_foto._folios_codigo_barras', return_value=['1234567890']):
        with pytest.raises(FotoTicketError, match='no coincide'):
            leer_con_respuestas([
                'FOLIO 0008430573\nTOTAL $500.00',
                'FOLIO 0008430578\nDISPENSARIO 7',
            ])


def test_reimpresion_gana_incluso_con_codigo_de_barras():
    with patch('arybot.ticket_foto._folios_codigo_barras', return_value=['0008430573']):
        with pytest.raises(FotoTicketError, match='REIMPRESIÓN'):
            leer_con_respuestas([
                'REIMPRESIÓN\nFOLIO 0008430573\nTOTAL $500.00',
                'FOLIO 0008430573\nDISPENSARIO 7',
            ])



def test_folio_en_zona_recupera_ceros_mal_leidos_en_texto_completo():
    from unittest.mock import Mock
    from arybot.ticket_foto import _folios_en_zona_folio

    datos = {
        'text': ['FOLIO', '0098430573'],
        'left': [265, 391], 'top': [255, 257],
        'width': [101, 207], 'height': [40, 46],
    }
    api = Mock()
    api.Output.DICT = 'DICT'
    api.image_to_data.return_value = datos
    api.image_to_string.side_effect = [
        'FOLIO 0008430573', 'FOLIO 0008430573',
    ]
    imagen = Image.new('RGB', (872, 711), 'white')
    assert _folios_en_zona_folio(imagen, api) == ['0008430573']
    assert api.image_to_string.call_count == 2


def test_una_sola_lectura_de_zona_no_es_suficiente():
    from unittest.mock import Mock
    from arybot.ticket_foto import _folios_en_zona_folio

    datos = {
        'text': ['FOLIO'], 'left': [200], 'top': [210],
        'width': [90], 'height': [30],
    }
    api = Mock()
    api.Output.DICT = 'DICT'
    api.image_to_data.return_value = datos
    api.image_to_string.side_effect = [
        'FOLIO 0008430573', 'FOLIO 0008430578',
    ]
    imagen = Image.new('RGB', (800, 900), 'white')
    assert _folios_en_zona_folio(imagen, api) == []




def test_rescate_zonas_acepta_folio_y_total_sin_lectura_completa():
    from unittest.mock import Mock
    from arybot.ticket_foto import _rescate_compra_y_folio

    api = Mock()
    api.Output.DICT = 'DICT'
    api.image_to_data.return_value = {
        'text': ['FOLIO', '0008430573'],
        'left': [205, 328], 'top': [478, 481],
        'width': [98, 209], 'height': [24, 30],
    }
    api.image_to_string.side_effect = [
        'FOLIO 0008430573', 'FOLIO 0008430573',
        'OPERACION DE CONTADO\nTOTAL $500.00',
    ]
    imagen = Image.new('RGB', (740, 1600), 'white')
    assert _rescate_compra_y_folio(imagen, api) == (['0008430573'], True)


def test_rescate_zonas_no_confirma_folio_con_lecturas_distintas():
    from unittest.mock import Mock
    from arybot.ticket_foto import _rescate_compra_y_folio

    api = Mock()
    api.Output.DICT = 'DICT'
    api.image_to_data.return_value = {
        'text': ['FOLIO'],
        'left': [205], 'top': [478],
        'width': [98], 'height': [24],
    }
    api.image_to_string.side_effect = [
        'FOLIO 0008430573', 'FOLIO 0008430578',
        'TOTAL $500.00',
    ]
    imagen = Image.new('RGB', (740, 1600), 'white')
    assert _rescate_compra_y_folio(imagen, api) == ([], True)


def test_rescate_zonas_no_acepta_ticket_sin_importe_visible():
    from unittest.mock import Mock
    from arybot.ticket_foto import _rescate_compra_y_folio

    api = Mock()
    api.Output.DICT = 'DICT'
    api.image_to_data.return_value = {
        'text': ['FOLIO'],
        'left': [205], 'top': [478],
        'width': [98], 'height': [24],
    }
    api.image_to_string.side_effect = [
        'FOLIO 0008430573', 'FOLIO 0008430573',
        'TOTAL sin importe',
    ]
    imagen = Image.new('RGB', (740, 1600), 'white')
    assert _rescate_compra_y_folio(imagen, api) == (['0008430573'], False)


def test_rescate_integracion_acepta_folio_y_total_solo_zonas():
    # La compra esta validada por el OCR localizado aunque la lectura
    # global solo vea FOLIO. El codigo de barras confirma el folio exacto.
    with patch('arybot.ocr_ticket.find_tesseract', return_value='tesseract'), \
         patch('pytesseract.image_to_string', side_effect=[
             'FOLIO 0008430573',
             'sin palabras de compra reconocibles',
         ]), \
         patch('arybot.ticket_foto._folios_codigo_barras',
               return_value=['0008430573']), \
         patch('arybot.ticket_foto._evidencia_folio_y_compra',
               return_value=(True, True, False)), \
         patch('arybot.ticket_foto._rescate_compra_y_folio',
               return_value=([], True)):
        resultado = analizar_ticket_foto(foto())
    assert resultado['folio'] == '0008430573'



def test_detector_de_papel_en_captura_con_bordes_oscuros():
    from PIL import ImageDraw
    from arybot.ticket_foto import _detectar_papel_por_luz

    im = Image.new('RGB', (740, 1600), (22, 28, 40))
    dibujo = ImageDraw.Draw(im)
    dibujo.rectangle((125, 253, 652, 1230), fill=(211, 210, 203))
    bounds = _detectar_papel_por_luz(im)
    assert bounds is not None
    x0, y0, x1, y1 = bounds
    assert 105 <= x0 <= 148
    assert 240 <= y0 <= 270
    assert 638 <= x1 <= 665
    assert 1215 <= y1 <= 1250


def test_ocr_mejorado_valida_dos_lecturas_y_compra():
    from unittest.mock import Mock, patch
    from arybot.ticket_foto import _ocr_tira_mejorado

    api = Mock()
    api.image_to_string.side_effect = [
        'FOLIO 0008430573',
        'FOLIO 0008430573',
        'DISPENSARIO 7\nTOTAL $500.00',
    ]
    with patch('arybot.ticket_foto._detectar_papel_por_luz', return_value=(125, 253, 652, 1230)):
        candidatos, compra, reimp = _ocr_tira_mejorado(Image.new('RGB', (740, 1600)), api)
    assert (candidatos, compra, reimp) == (['0008430573'], True, False)


def test_ocr_mejorado_rechaza_folios_discrepantes():
    from unittest.mock import Mock, patch
    from arybot.ticket_foto import _ocr_tira_mejorado

    api = Mock()
    api.image_to_string.side_effect = [
        'FOLIO 0008430573', 'FOLIO 0008430578',
        'TOTAL $500.00',
    ]
    with patch('arybot.ticket_foto._detectar_papel_por_luz', return_value=(125, 253, 652, 1230)):
        candidatos, compra, reimp = _ocr_tira_mejorado(Image.new('RGB', (740, 1600)), api)
    assert candidatos == [] and compra and not reimp


def test_ocr_mejorado_bloquea_reimpresiones():
    from unittest.mock import Mock, patch
    from arybot.ticket_foto import _ocr_tira_mejorado

    api = Mock()
    api.image_to_string.side_effect = [
        'REIMPRESION FOLIO 0008430573',
        'FOLIO 0008430573',
        'TOTAL $500.00',
    ]
    with patch('arybot.ticket_foto._detectar_papel_por_luz', return_value=(125, 253, 652, 1230)):
        _, _, reimp = _ocr_tira_mejorado(Image.new('RGB', (740, 1600)), api)
    assert reimp


def test_integracion_no_acepta_votos_sin_barras_o_doble_impresion():
    from unittest.mock import patch
    with patch('arybot.ocr_ticket.find_tesseract', return_value='tesseract'), \
         patch('pytesseract.image_to_string', side_effect=[
             'texto del fondo sin concepto',
             'lectura general borrosa',
         ]), \
         patch('arybot.ticket_foto._ocr_tira_mejorado',
               return_value=(['0008430573'], True, False)), \
         patch('arybot.ticket_foto._leer_numeros_por_rotulo',
               return_value=['0008430573']), \
         patch('arybot.ticket_foto._folios_codigo_barras', return_value=[]):
        with pytest.raises(FotoTicketError, match='folio exacto'):
            analizar_ticket_foto(foto())


def test_folio_digitos_acepta_ceros_si_8_lecturas_coinciden():
    from unittest.mock import Mock
    from arybot.ticket_foto import _leer_numeros_por_rotulo

    api = Mock()
    api.Output.DICT = 'DICT'
    api.image_to_data.return_value = {
        'text': ['FOLIO'],
        'left': [99], 'top': [107],
        'width': [137], 'height': [64],
    }
    api.image_to_string.side_effect = ['0008430573'] * 8
    im = Image.new('RGB', (740, 1600), 'white')
    with patch('arybot.ticket_foto._detectar_papel_por_luz',
               return_value=(58, 258, 732, 1106)):
        assert _leer_numeros_por_rotulo(im, api) == ['0008430573']


def test_folio_digitos_rechaza_empate_o_lecturas_dudosas():
    from unittest.mock import Mock
    from arybot.ticket_foto import _leer_numeros_por_rotulo

    api = Mock()
    api.Output.DICT = 'DICT'
    api.image_to_data.return_value = {
        'text': ['FOLIO'],
        'left': [99], 'top': [107],
        'width': [137], 'height': [64],
    }
    # Cinco lecturas distintas no permiten inventar ningun folio.
    api.image_to_string.side_effect = [
        '0008430573', '0008430573', '0008430573', '0008430573',
        '6008430573', '6008430573', '6008430573', '6008430573',
    ] * 10
    im = Image.new('RGB', (740, 1600), 'white')
    with patch('arybot.ticket_foto._detectar_papel_por_luz',
               return_value=(58, 258, 732, 1106)):
        assert _leer_numeros_por_rotulo(im, api) == []


def test_integracion_prevalece_folio_numerico_con_compra_comprobada():
    with patch('arybot.ocr_ticket.find_tesseract', return_value='tesseract'), \
         patch('pytesseract.image_to_string', side_effect=[
             'FOLIO 6008430573\nTOTAL $500.00\nLITROS 20.842', 'MAGNA',
         ]), \
         patch('arybot.ticket_foto._ocr_tira_mejorado',
               return_value=([], True, False)), \
         patch('arybot.ticket_foto._leer_numeros_por_rotulo',
               return_value=['0008430573']), \
         patch('arybot.ticket_foto._folios_codigo_barras', return_value=['0008430573']):
        resultado = analizar_ticket_foto(foto())
    assert resultado == {'folio': '0008430573'}


def test_integracion_no_acepta_folio_si_papel_es_reimpresion():
    with patch('arybot.ocr_ticket.find_tesseract', return_value='tesseract'), \
         patch('pytesseract.image_to_string', side_effect=[
             'REIMPRESION FOLIO 0008430573', 'MAGNA',
         ]), \
         patch('arybot.ticket_foto._folios_codigo_barras', return_value=['0008430573']):
        with pytest.raises(FotoTicketError, match='REIMPRESIÓN'):
            analizar_ticket_foto(foto())




def test_compra_en_papel_recortado_reconoce_total_litros_y_precio():
    from unittest.mock import Mock
    from arybot.ticket_foto import _validar_compra_en_papel

    api = Mock()
    api.image_to_string.return_value = (
        'DISPENSARIO : 7\nLITROS : 20.842\nPRECIO : $23.99\nTOTAL : $506.00'
    )
    with patch('arybot.ticket_foto._detectar_papel_por_luz',
               return_value=(58, 258, 732, 1106)):
        assert _validar_compra_en_papel(Image.new('RGB', (740, 1600)), api) == (True, False)
    assert api.image_to_string.call_count == 1


def test_compra_recortada_no_acepta_numero_sin_datos_de_compra():
    from unittest.mock import Mock
    from arybot.ticket_foto import _validar_compra_en_papel

    api = Mock()
    api.image_to_string.return_value = 'FOLIO 0008430573\nTOTAL $500.00'
    with patch('arybot.ticket_foto._detectar_papel_por_luz',
               return_value=(58, 258, 732, 1106)):
        assert _validar_compra_en_papel(Image.new('RGB', (740, 1600)), api) == (False, False)


def test_compra_recortada_rechaza_reimpresion():
    from unittest.mock import Mock
    from arybot.ticket_foto import _validar_compra_en_papel

    api = Mock()
    api.image_to_string.return_value = (
        'REIMPRESION\nDISPENSARIO: 7\nLITROS 20.842\nTOTAL $500.00'
    )
    with patch('arybot.ticket_foto._detectar_papel_por_luz',
               return_value=(58, 258, 732, 1106)):
        assert _validar_compra_en_papel(Image.new('RGB', (740, 1600)), api) == (False, True)


def test_integracion_folio_confirmado_con_compra_visible_solo_en_papel():
    with patch('arybot.ocr_ticket.find_tesseract', return_value='tesseract'), \
         patch('pytesseract.image_to_string',
               side_effect=['FOLIO 6008430573', 'mas texto borroso']), \
         patch('arybot.ticket_foto._ocr_tira_mejorado',
               return_value=([], False, False)), \
         patch('arybot.ticket_foto._leer_numeros_por_rotulo',
               return_value=['0008430573']), \
         patch('arybot.ticket_foto._evidencia_folio_y_compra',
               return_value=(True, True, False)), \
         patch('arybot.ticket_foto._folios_codigo_barras', return_value=['0008430573']):
        assert analizar_ticket_foto(foto()) == {'folio': '0008430573'}


def test_integracion_compra_en_papel_sigue_rechazando_reimpresiones():
    with patch('arybot.ocr_ticket.find_tesseract', return_value='tesseract'), \
         patch('pytesseract.image_to_string',
               side_effect=['FOLIO 6008430573', 'mas texto borroso']), \
         patch('arybot.ticket_foto._ocr_tira_mejorado',
               return_value=([], False, False)), \
         patch('arybot.ticket_foto._leer_numeros_por_rotulo',
               return_value=['0008430573']), \
         patch('arybot.ticket_foto._evidencia_folio_y_compra',
               return_value=(True, False, True)), \
         patch('arybot.ticket_foto._folios_codigo_barras', return_value=['0008430573']):
        with pytest.raises(FotoTicketError, match='REIMPRESIÓN'):
            analizar_ticket_foto(foto())




def test_folios_diferentes_en_codigos_barras_se_rechazan():
    with patch('arybot.ticket_foto._folios_codigo_barras',
               return_value=['0008430573', '0008430578']):
        with pytest.raises(FotoTicketError, match='codigos de barras contradictorios'):
            leer_con_respuestas([
                'FOLIO 0008430573\nTOTAL $500.00\nLITROS 20.842',
                'FOLIO 0008430573\nTOTAL $500.00',
            ])


def test_codigo_barras_sin_rotulo_folio_no_se_acepta():
    with patch('arybot.ticket_foto._folios_codigo_barras',
               return_value=['0008430573']):
        with pytest.raises(FotoTicketError, match='no detecto la palabra FOLIO'):
            leer_con_respuestas([
                'SERVICIO APACHE\nTOTAL $500.00\nLITROS 20.842',
                'TOTAL $500.00',
            ])


def test_codigo_barras_valido_puede_corregir_dos_errores_ocr():
    with patch('arybot.ticket_foto._folios_codigo_barras',
               return_value=['0008430573']):
        result = leer_con_respuestas([
            'FOLIO 6008430573\nTOTAL $500.00\nLITROS 20.842',
            'FOLIO 0008430578\nDISPENSARIO 7\nTOTAL $500.00',
        ])
    assert result['folio'] == '0008430573'


def test_sin_codigo_barras_no_basta_que_ocr_repita_error():
    with patch('arybot.ticket_foto._folios_codigo_barras', return_value=[]):
        with pytest.raises(FotoTicketError, match='folio exacto'):
            leer_con_respuestas([
                'FOLIO 6008430573\nTOTAL $500.00\nLITROS 20.842',
                'FOLIO 6008430573\nTOTAL $500.00\nLITROS 20.842',
            ])




def test_evidencia_en_papel_folio_y_compra_sin_digitos_ocr():
    from unittest.mock import Mock
    from arybot.ticket_foto import _evidencia_folio_y_compra
    api = Mock()
    api.image_to_string.return_value = (
        'CLAVE PEMEX 123\nFOLIO GAG5Q3A5R3\n'
        'OPERACION DE CONTADO\nDISPENSARIO 7\n'
        'LITROS 20,842\nTOTAL $506.06')
    with patch('arybot.ticket_foto._detectar_papel_por_luz',
               return_value=(58, 258, 732, 1106)):
        assert _evidencia_folio_y_compra(
            Image.new('RGB', (740, 1600)), api) == (True, True, False)


def test_evidencia_en_papel_detecta_fulto_de_ocr_movil():
    from unittest.mock import Mock
    from arybot.ticket_foto import _evidencia_folio_y_compra
    api = Mock()
    api.image_to_string.return_value = (
        'FULTO UUG84305 73\nDISPENSARIO 7\n'
        'LITROS 20.842\nTOTAL $500.00')
    with patch('arybot.ticket_foto._detectar_papel_por_luz',
               return_value=(58, 258, 732, 1106)):
        assert _evidencia_folio_y_compra(
            Image.new('RGB', (740, 1600)), api) == (True, True, False)


def test_evidencia_en_papel_rechaza_reimpresion():
    from unittest.mock import Mock
    from arybot.ticket_foto import _evidencia_folio_y_compra
    api = Mock()
    api.image_to_string.return_value = (
        'REIMPRESION\nFOLIO 0008430573\n'
        'DISPENSARIO 7\nTOTAL $500.00')
    with patch('arybot.ticket_foto._detectar_papel_por_luz',
               return_value=(58, 258, 732, 1106)):
        assert _evidencia_folio_y_compra(
            Image.new('RGB', (740, 1600)), api) == (False, False, True)


def test_barra_code39_coincide_aunque_texto_del_folio_se_deforme():
    with patch('arybot.ticket_foto._folios_codigo_barras',
               return_value=['0008430573']):
        resultado = leer_con_respuestas([
            'FULTO UUG84305 73\nDISPENSARIO 7\nTOTAL $500.00',
            'LITROS 20.842',
        ])
    assert resultado['folio'] == '0008430573'



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
