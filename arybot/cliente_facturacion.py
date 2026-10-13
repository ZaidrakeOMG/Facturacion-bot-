"""Aviso conocido de cliente sin régimen fiscal. Solo lectura de diálogos.

No interpreta campos del cliente, OCR, correos ni textos de otras aplicaciones.
No confirma que el RFC sea inexistente: el aviso observado indica alta incompleta.
"""
from __future__ import annotations
import re
import unicodedata

CODIGO_SIN_REGIMEN = 'CLIENTE_SIN_REGIMEN'
MENSAJE_SIN_REGIMEN = (
    'Su cliente no está dado de alta correctamente para facturar. '
    'Falta registrar su régimen fiscal.'
)
ACCION_SIN_REGIMEN = (
    'Solicite al área de facturación completar el régimen fiscal en su registro '
    'de Clientes de Efectivo. No es necesario crear otro cliente. '
    'Esta solicitud se detuvo antes de timbrar.'
)

class ClienteSinRegimenError(RuntimeError):
    codigo_cliente = CODIGO_SIN_REGIMEN

    def __init__(self):
        super().__init__(MENSAJE_SIN_REGIMEN)


def es_aviso_sin_regimen(texto: str) -> bool:
    """Firma del aviso mostrado por Polaris; no basta con un campo vacío."""
    value = unicodedata.normalize('NFKD', str(texto or ''))
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r'[^A-Z0-9]', '', value.upper())
    return ('ELCLIENTENOTIENEDEFINIDOELREGIMENFISCAL' in value
            and 'NOSEPUEDEFACTURAR' in value)


def exigir_cliente_facturable(bot, formulario):
    """Detiene la captura si un diálogo DEL MISMO proceso contiene el aviso.

    No pulsa Aceptar ni convierte fallos de lectura en un diagnóstico fiscal.
    Si no puede leerse, las guardias existentes conservan el error genérico.
    """
    from . import polaris
    wg = getattr(polaris, 'win32gui', None)
    if wg is None or not formulario:
        return
    try:
        ventanas = bot._enum()
    except Exception:
        return
    for ventana in ventanas:
        encontrado = False
        try:
            if (ventana == formulario or not wg.IsWindowVisible(ventana)
                    or not bot._same_polaris_process(formulario, ventana)):
                continue
            # El aviso del video es MessageBox de Windows. No se lee el
            # formulario de factura ni una tabla que pudiera contener datos.
            if wg.GetClassName(ventana) != '#32770':
                continue
            captions = []
            def leer(control, _):
                if len(captions) >= 64 or not wg.IsWindowVisible(control):
                    return
                clase = wg.GetClassName(control).upper()
                if clase == 'STATIC' or 'LABEL' in clase:
                    captions.append((wg.GetWindowText(control) or '')[:4096])
            wg.EnumChildWindows(ventana, leer, None)
            encontrado = es_aviso_sin_regimen(' '.join(captions))
        except Exception:
            # Nunca sustituir un error desconocido por «cliente no registrado».
            continue
        if encontrado:
            raise ClienteSinRegimenError()
