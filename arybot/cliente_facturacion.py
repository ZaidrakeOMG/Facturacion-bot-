"""Diagnóstico de avisos EXPLÍCITOS de cliente en Polaris.

La ausencia de un control, foco o respuesta HTTP NO demuestra que el RFC no exista.
Solo se examinan textos visibles de avisos del proceso Polaris antes de timbrar.
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

CODIGO_NO_REGISTRADO = 'CLIENTE_NO_REGISTRADO'
MENSAJE_NO_REGISTRADO = (
    'Su cliente no está dado de alta en nuestro sistema de facturación. '
    'Regístrese primero para poder solicitar su factura.'
)
ACCION_NO_REGISTRADO = (
    'Ingrese a la opción Registrar cliente del portal de Grupo ARY y complete su alta. '
    'Este ticket no se facturó. Conserve su referencia para aclaraciones.'
)


class ClienteSinRegimenError(RuntimeError):
    codigo_cliente = CODIGO_SIN_REGIMEN

    def __init__(self):
        super().__init__(MENSAJE_SIN_REGIMEN)


class ClienteNoRegistradoError(RuntimeError):
    codigo_cliente = CODIGO_NO_REGISTRADO

    def __init__(self):
        super().__init__(MENSAJE_NO_REGISTRADO)


def _normalizar(texto):
    value = unicodedata.normalize('NFKD', str(texto or ''))
    return ''.join(ch for ch in value if not unicodedata.combining(ch)).upper()


def es_aviso_sin_regimen(texto: str) -> bool:
    """Firma observada del aviso de Polaris; no equivale a RFC inexistente."""
    compact = re.sub(r'[^A-Z0-9]', '', _normalizar(texto))
    return ('ELCLIENTENOTIENEDEFINIDOELREGIMENFISCAL' in compact
            and 'NOSEPUEDEFACTURAR' in compact)


def es_aviso_cliente_no_registrado(texto: str) -> bool:
    """Sólo avisos inequívocos de inexistencia/no coincidencias de cliente.

    NO clasifica RFC inválido, ventanas no abiertas o errores de foco como
    inexistencia en el catálogo. Se invoca durante búsqueda de cliente.
    """
    compact = re.sub(r'[^A-Z0-9]', '', _normalizar(texto))
    firmas = (
        'ELRFCNOEXISTE', 'NOEXISTEELRFC', 'RFCNOEXISTE',
        'ELRFCNOESTAREGISTRADO', 'RFCNOESTAREGISTRADO',
        'ELRFCNOSEENCUENTRAREGISTRADO', 'RFCNOSEENCUENTRAREGISTRADO',
        'RFCNOREGISTRADO', 'RFCNOENCONTRADO', 'NOSEENCONTROELRFC',
        'CLIENTENOEXISTE', 'ELCLIENTENOEXISTE', 'CLIENTEINEXISTENTE',
        'ELCLIENTENOESTAREGISTRADO', 'CLIENTENOESTAREGISTRADO',
        'ELCLIENTENOSEENCUENTRAREGISTRADO', 'CLIENTENOSEENCUENTRAREGISTRADO',
        'CLIENTENOENCONTRADO', 'NOSEENCONTROELCLIENTE',
        'NOSEENCONTRONINGUNCLIENTE', 'NOSEENCONTRARONCLIENTES',
        'NOHAYCLIENTESREGISTRADOS', 'NOEXISTENCLIENTESCONELRFC',
        'NOSEENCONTRARONCOINCIDENCIAS', 'NOSEENCONTRARONREGISTROS',
        'NOSEENCONTROREGISTRO', 'NOEXISTENREGISTROSPARAELRFC',
    )
    return any(firma in compact for firma in firmas)


def exigir_cliente_facturable(bot, formulario):
    """Detecta avisos de cliente sin régimen o sin registro del mismo proceso.

    No lee campos del formulario, no pulsa botones ni cierra un aviso. No se
    usa tras pulsar timbrado; la cola conserva sus reglas de resultado incierto.
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
        try:
            if (ventana == formulario or not wg.IsWindowVisible(ventana)
                    or not bot._same_polaris_process(formulario, ventana)):
                continue
            # Windows MessageBox (#32770) y diálogo de mensaje VCL de Polaris.
            clase = (wg.GetClassName(ventana) or '').upper()
            if clase != '#32770' and 'MESSAGEFORM' not in clase:
                continue
            captions = []
            def leer(control, _):
                if len(captions) >= 64 or not wg.IsWindowVisible(control):
                    return
                tipo = (wg.GetClassName(control) or '').upper()
                if tipo == 'STATIC' or 'LABEL' in tipo:
                    captions.append((wg.GetWindowText(control) or '')[:4096])
            wg.EnumChildWindows(ventana, leer, None)
            aviso = ' '.join(captions)
        except Exception:
            # Un fallo de lectura no significa que el cliente no exista.
            continue
        if es_aviso_sin_regimen(aviso):
            raise ClienteSinRegimenError()
        if es_aviso_cliente_no_registrado(aviso):
            raise ClienteNoRegistradoError()
