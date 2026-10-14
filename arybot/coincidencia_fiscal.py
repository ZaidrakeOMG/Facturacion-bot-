"""Identifica un único cliente por RFC y razón social, nunca por domicilio."""
from __future__ import annotations
import re
import unicodedata

CODIGO_VERIFICACION_FISCAL = 'VERIFICACION_FISCAL_PENDIENTE'
MENSAJE_VERIFICACION_FISCAL = (
    'No fue posible confirmar una coincidencia única entre el RFC y la razón social '
    'de su constancia fiscal y los clientes registrados. '
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
    """Devuelve una sola fila por RFC + razón social. `calle` se ignora.

    Se conserva el argumento únicamente para solicitudes y adaptadores antiguos.
    Dos registros con igual identidad, incluso con calles diferentes, requieren revisión.
    """
    if not all(isinstance(v, str) and v.strip() for v in (rfc, razon_social)):
        raise CoincidenciaFiscalError('Falta RFC o razón social de la constancia PDF.')
    if not re.fullmatch(r'[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}', _rfc(rfc)) or not _razon(razon_social):
        raise CoincidenciaFiscalError('RFC o razón social no válidos para identificar al cliente.')
    exactas = [fila for fila in filas if isinstance(fila, dict)
               and _rfc(fila.get('rfc')) == _rfc(rfc)
               and _razon(fila.get('razon_social')) == _razon(razon_social)]
    if len(exactas) != 1:
        raise CoincidenciaFiscalError(
            'No se confirmó UNA coincidencia de RFC y razón social en Polaris. '
            'La solicitud queda en revisión; no se eligió cliente ni se timbró.')
    return exactas[0]
