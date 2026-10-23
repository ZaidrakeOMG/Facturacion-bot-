"""Identifica un único cliente por RFC de SAT, sin fiarse del nombre o la calle."""
from __future__ import annotations
import re
import unicodedata

CODIGO_VERIFICACION_FISCAL = 'VERIFICACION_FISCAL_PENDIENTE'
MENSAJE_VERIFICACION_FISCAL = (
    'No fue posible confirmar un único cliente registrado con el RFC '
    'de su constancia fiscal. '
    'La solicitud se detuvo antes de timbrar. Continúe por WhatsApp con los datos de su solicitud.'
)

class CoincidenciaFiscalError(RuntimeError):
    pass


def _normalizar(value):
    s = unicodedata.normalize('NFKD', str(value or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c)).upper()
    return re.sub(r'\s+', ' ', re.sub(r'[^A-Z0-9Ñ&]+', ' ', s)).strip()


def _razon(value):
    s = _normalizar(value)
    for pattern in (r'\bS A DE C V\b', r'\bS A DE CV\b', r'\bSA DE C V\b'):
        s = re.sub(pattern, 'SA DE CV', s)
    return s


def _rfc(value):
    # Ñ y N son RFC distintos. No se quitan acentos ni caracteres del identificador.
    return str(value or '').strip().upper()


def verificar_unico(filas, *, rfc, razon_social, calle=None):
    """Devuelve una fila SOLO si hay exactamente un registro con el RFC.

    `razon_social` y `calle` son parámetros históricos que NO filtran candidatos.
    Un RFC duplicado (aun con nombres distintos) requiere revisión manual.
    La lista de filas debe haber sido leída *completa* por el adaptador Polaris.
    """
    if not isinstance(rfc, str) or not re.fullmatch(r'[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}', _rfc(rfc)):
        raise CoincidenciaFiscalError('Falta un RFC válido de la constancia PDF.')
    if not isinstance(filas, (list, tuple)):
        raise CoincidenciaFiscalError('No se pudo verificar el catálogo completo de clientes.')
    exactas = [fila for fila in filas if isinstance(fila, dict)
               and _rfc(fila.get('rfc')) == _rfc(rfc)]
    if len(exactas) != 1:
        raise CoincidenciaFiscalError(
            'No se confirmó UNA coincidencia única por RFC en Polaris. '
            'Si hay cero o varios clientes con el mismo RFC, se requiere revisión. '
            'No se eligió cliente ni se timbró.')
    return exactas[0]
