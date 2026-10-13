"""Comparación estricta para no seleccionar la primera coincidencia por RFC.

La coincidencia sólo es válida si RFC, razón social y calle concuerdan y hay
exactamente UN registro. Los identificadores de cliente jamás son adivinados.
"""
from __future__ import annotations
import re
import unicodedata


class CoincidenciaFiscalError(RuntimeError):
    pass


def _normalizar(value):
    s = unicodedata.normalize('NFKD', str(value or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c)).upper()
    s = re.sub(r'[^A-Z0-9Ñ&]+', ' ', s).strip()
    return re.sub(r'\s+', ' ', s)


def _razon(value):
    tokens = _normalizar(value).split()
    # La columna VCL puede separar las abreviaturas letra a letra.
    s = ' '.join(tokens)
    s = re.sub(r'\bS A DE C V\b', 'SA DE CV', s)
    s = re.sub(r'\bS A DE CV\b', 'SA DE CV', s)
    s = re.sub(r'\bSA DE C V\b', 'SA DE CV', s)
    return s


def _calle(value):
    s = _normalizar(value)
    patrones = (
        (r'^CALZ(?:ADA)?\b', 'CALZADA'),
        (r'^AV(?:ENIDA)?\b', 'AVENIDA'),
        (r'^BLVD\b', 'BOULEVARD'),
        (r'^C\b', 'CALLE'),
    )
    for pattern, reemplazo in patrones:
        s = re.sub(pattern, reemplazo, s)
    return s


def _misma_calle(esperada, observada):
    e, o = _calle(esperada), _calle(observada)
    if not e or not o:
        return False
    if e == o:
        return True
    # El catálogo puede mostrar torre, piso o número después de la MISMA calle.
    if not o.startswith(e + ' '):
        return False
    extras = o[len(e):].strip().split()
    return bool(extras and (extras[0].isdigit() or extras[0] in {
        'NUMERO', 'NUM', 'NO', 'N', 'TORRE', 'PISO', 'LOCAL',
        'INTERIOR', 'EXT', 'EXTERIOR', 'EDIFICIO', 'DEPTO', 'DEPARTAMENTO',
        'LOTE', 'MANZANA', 'MZ',
    }))


def verificar_unico(filas, *, rfc, razon_social, calle):
    """Devuelve UNA fila íntegra. Nunca aceptar una coincidencia solo por RFC."""
    if not all(isinstance(v, str) and v.strip() for v in (rfc, razon_social, calle)):
        raise CoincidenciaFiscalError('Falta RFC, razón social o calle verificada de la constancia PDF.')
    exactas = [
        fila for fila in filas
        if (_normalizar(fila.get('rfc')) == _normalizar(rfc)
            and _razon(fila.get('razon_social')) == _razon(razon_social)
            and _misma_calle(calle, fila.get('calle')))
    ]
    if len(exactas) != 1:
        raise CoincidenciaFiscalError(
            'No se confirmó UNA coincidencia de RFC, razón social y calle en Polaris. '
            'La solicitud queda en revisión; no se eligió cliente ni se timbró.')
    return exactas[0]
