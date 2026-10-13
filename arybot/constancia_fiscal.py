"""Lectura conservadora de la constancia fiscal en PDF original del SAT.

El PDF se valida en memoria y NO se escribe en disco, cola, logs ni correos.
Rechazar textos ilegibles/ambiguos es preferible a facturar con otro cliente.
"""
from __future__ import annotations
from dataclasses import dataclass
from io import BytesIO
import base64
import binascii
import re
import unicodedata

MAX_PDF_BYTES = 2 * 1024 * 1024
MAX_PDF_PAGES = 3


class ConstanciaError(ValueError):
    pass


@dataclass(frozen=True)
class IdentidadFiscal:
    rfc: str
    razon_social: str
    calle: str
    idcif: str = ''


def _simple(value):
    value = unicodedata.normalize('NFKD', str(value or ''))
    value = ''.join(ch for ch in value if not unicodedata.combining(ch)).upper()
    return re.sub(r'\s+', ' ', value).strip()


# Un valor vacío pasa a la siguiente línea SOLO si esa línea no es otro campo.
ETIQUETAS = (
    'RFC', 'REGISTRO FEDERAL DE CONTRIBUYENTES',
    'DENOMINACION/RAZON SOCIAL', 'DENOMINACION O RAZON SOCIAL',
    'NOMBRE, DENOMINACION O RAZON SOCIAL', 'NOMBRE (S)', 'NOMBRES',
    'PRIMER APELLIDO', 'SEGUNDO APELLIDO',
    'NOMBRE DE VIALIDAD', 'TIPO DE VIALIDAD', 'CALLE',
    'ID CIF', 'IDCIF', 'CIF', 'CODIGO POSTAL', 'NUMERO EXTERIOR',
    'NUMERO INTERIOR', 'ENTIDAD FEDERATIVA', 'MUNICIPIO', 'COLONIA',
)


def _campo(lineas, *etiquetas):
    for i, linea in enumerate(lineas):
        item = _simple(linea)
        for etiqueta in etiquetas:
            label = _simple(etiqueta)
            # Igualdad o separador ':': no confundir "NOMBRE" con "NOMBRE DE VIALIDAD".
            if item == label or item == label + ':':
                candidato = _simple(lineas[i + 1]) if i + 1 < len(lineas) else ''
                if candidato and not any(candidato == x or candidato.startswith(x + ':') for x in ETIQUETAS):
                    return candidato
            prefix = label + ':'
            if item.startswith(prefix):
                candidato = item[len(prefix):].strip()
                # Algunos PDFs extraen varias etiquetas en la misma línea.
                candidato = re.split(
                    r'\s{2,}(?=(?:RFC|NOMBRE|PRIMER APELLIDO|SEGUNDO APELLIDO|'
                    r'CODIGO POSTAL|ENTIDAD FEDERATIVA|NUMERO EXTERIOR|IDCIF)\s*:)',
                    candidato, maxsplit=1
                )[0].strip()
                if candidato:
                    return candidato
    return ''


def _validar_identidad(texto, *, alta=False):
    lineas = [line.strip() for line in texto.splitlines() if line.strip()]
    conjunto = _simple(texto)
    if ('CONSTANCIA DE SITUACION FISCAL' not in conjunto
            or 'CONTRIBUYENTE' not in conjunto):
        raise ConstanciaError('El archivo no parece una Constancia de Situación Fiscal emitida por el SAT.')
    rfc = _campo(lineas, 'RFC', 'Registro Federal de Contribuyentes')
    if not re.fullmatch(r'[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}', rfc):
        raise ConstanciaError('No se pudo identificar un RFC válido en la constancia PDF.')
    razon = _campo(lineas, 'Denominación/Razón Social',
                    'Denominación o Razón Social',
                    'Nombre, denominación o razón social')
    if not razon:
        nombres = _campo(lineas, 'Nombre(s)', 'Nombres')
        paterno = _campo(lineas, 'Primer Apellido')
        materno = _campo(lineas, 'Segundo Apellido')
        if nombres and paterno:
            razon = ' '.join(filter(None, (nombres, paterno, materno)))
    razon = _simple(razon)
    if not 5 <= len(razon) <= 180:
        raise ConstanciaError('No se pudo leer con certeza el nombre o razón social de la constancia.')
    nombre_vialidad = _campo(lineas, 'Nombre de Vialidad', 'Calle')
    tipo_vialidad = _campo(lineas, 'Tipo de Vialidad')
    calle = _simple(nombre_vialidad)
    if tipo_vialidad and calle and not calle.startswith(_simple(tipo_vialidad) + ' '):
        calle = _simple(tipo_vialidad + ' ' + calle)
    if not 3 <= len(calle) <= 180:
        raise ConstanciaError('No se pudo leer con certeza la calle del domicilio fiscal en la constancia.')
    idcif = re.sub(r'\s+', '', _campo(lineas, 'idCIF', 'id CIF'))
    if alta and not re.fullmatch(r'\d{11}', idcif):
        raise ConstanciaError('La constancia PDF debe mostrar un idCIF legible de 11 dígitos para registrar el alta.')
    return IdentidadFiscal(rfc, razon, calle, idcif if re.fullmatch(r'\d{11}', idcif) else '')


def leer_constancia_pdf_base64(valor, *, alta=False):
    """Verifica PDF y retorna únicamente RFC, razón social, calle e idCIF (alta)."""
    if not isinstance(valor, str) or len(valor) > (MAX_PDF_BYTES * 4 // 3 + 16):
        raise ConstanciaError('Adjunta una constancia fiscal PDF de máximo 2 MB.')
    try:
        payload = base64.b64decode(valor, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ConstanciaError('El archivo de constancia no es válido. Descarga el PDF original del SAT.') from exc
    if not 200 <= len(payload) <= MAX_PDF_BYTES or not payload.startswith(b'%PDF-'):
        raise ConstanciaError('Solo se admite la constancia original en formato PDF, máximo 2 MB.')
    try:
        from pypdf import PdfReader
        pdf = PdfReader(BytesIO(payload), strict=True)
        if pdf.is_encrypted:
            raise ConstanciaError('La constancia no debe estar protegida con contraseña.')
        if not 1 <= len(pdf.pages) <= MAX_PDF_PAGES:
            raise ConstanciaError('La constancia PDF debe contener de 1 a 3 páginas.')
        textos = []
        for page in pdf.pages:
            text = page.extract_text() or ''
            if len(text) > 30000:
                raise ConstanciaError('El archivo PDF contiene demasiado texto para una constancia.')
            textos.append(text)
    except ConstanciaError:
        raise
    except Exception as exc:
        raise ConstanciaError('No se pudo leer el PDF. Descarga una constancia digital nueva desde el SAT.') from exc
    texto = '\n'.join(textos)
    if len(texto.strip()) < 80:
        raise ConstanciaError('Este PDF parece una imagen/escaneo. Sube el PDF digital descargado del SAT.')
    return _validar_identidad(texto, alta=alta)
