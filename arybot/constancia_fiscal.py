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
MAX_PDF_OBJECTS = 5000
ACCIONES_PDF_BLOQUEADAS = frozenset(('/JavaScript', '/Launch', '/SubmitForm', '/ImportData', '/GoToR'))
RFC_EN_TEXTO = re.compile(
    r'(?<![A-Z0-9Ñ&])([A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3})(?![A-Z0-9Ñ&])'
)
RFC_FRACCIONADO = re.compile(
    r'(?<![A-Z0-9Ñ&])(?:R[.\s]*F[.\s]*C[.\s]*|REGISTRO\s+FEDERAL\s+DE\s+CONTRIBUYENTES)'
    r'\s*[:：-]?\s*([A-ZÑ&]{3,4})[\s-]+([0-9]{6})[\s-]+([A-Z0-9]{3})(?![A-Z0-9Ñ&])'
)

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


ETIQUETAS = (
    'RFC', 'REGISTRO FEDERAL DE CONTRIBUYENTES',
    'DENOMINACION/RAZON SOCIAL', 'DENOMINACION O RAZON SOCIAL',
    'NOMBRE, DENOMINACION O RAZON SOCIAL', 'RAZON SOCIAL',
    'NOMBRE (S)', 'NOMBRE(S)', 'NOMBRES', 'NOMBRE COMPLETO',
    'PRIMER APELLIDO', 'SEGUNDO APELLIDO',
    'APELLIDO PATERNO', 'APELLIDO MATERNO',
    'NOMBRE DE VIALIDAD', 'TIPO DE VIALIDAD', 'CALLE',
    'ID CIF', 'IDCIF', 'CIF', 'CODIGO POSTAL', 'NUMERO EXTERIOR',
    'NUMERO INTERIOR', 'ENTIDAD FEDERATIVA', 'MUNICIPIO', 'COLONIA',
)


def _etiqueta_normalizada(valor):
    """Unifica espacios junto a / y la variante Nombre (s) del SAT."""
    valor = _simple(valor)
    valor = re.sub(r'\s*/\s*', '/', valor)
    valor = re.sub(r'\bNOMBRE\s*\(\s*S\s*\)', 'NOMBRES', valor)
    return valor


def _es_etiqueta(valor):
    item = _etiqueta_normalizada(valor)
    if not item or item.startswith(('DATOS DE ', 'CEDULA DE ', 'CONSTANCIA DE ')):
        return True
    for etiqueta in ETIQUETAS:
        label = _etiqueta_normalizada(etiqueta)
        if item == label or item.startswith(label + ':') or item.startswith(label + ' :'):
            return True
    return False


def _campo(lineas, *etiquetas):
    """Admite campo/valor en una línea o en líneas separadas y alias del SAT."""
    for i, linea in enumerate(lineas):
        item = _etiqueta_normalizada(linea)
        for etiqueta in etiquetas:
            label = _etiqueta_normalizada(etiqueta)
            if item == label or item in (label + ':', label + ' -'):
                candidato = _etiqueta_normalizada(lineas[i + 1]) if i + 1 < len(lineas) else ''
                if candidato and not _es_etiqueta(candidato):
                    return candidato
                continue
            if item.startswith(label + ':'):
                candidato = item[len(label) + 1:].strip()
            elif item.startswith(label + ' -'):
                candidato = item[len(label) + 2:].strip()
            elif item.startswith(label + ' '):
                candidato = item[len(label) + 1:].strip()
            else:
                continue
            candidato = re.split(
                r'\s+(?=(?:RFC|NOMBRES|PRIMER APELLIDO|SEGUNDO APELLIDO|'
                r'APELLIDO PATERNO|APELLIDO MATERNO|CODIGO POSTAL|'
                r'ENTIDAD FEDERATIVA|NUMERO EXTERIOR|IDCIF)\s*:)',
                candidato, maxsplit=1,
            )[0].strip(' :;-')
            if candidato and not _es_etiqueta(candidato):
                return candidato
    return ''

def _leer_rfc_sin_adivinar(texto):
    """Acepta variantes de extracción PDF, sin confundir Ñ con N en el RFC."""
    normalizado = re.sub(r'\s+', ' ', unicodedata.normalize('NFC', str(texto or '')).upper()).strip()
    encontrados = set(RFC_EN_TEXTO.findall(normalizado))
    for letras, fecha, homoclave in RFC_FRACCIONADO.findall(normalizado):
        encontrados.add(letras + fecha + homoclave)
    if len(encontrados) == 1:
        return next(iter(encontrados))
    if len(encontrados) > 1:
        raise ConstanciaError(
            'La constancia contiene más de un RFC diferente. No se eligió ninguno '
            'para evitar facturar a otro cliente.')
    raise ConstanciaError(
        'No se pudo reconocer el RFC en el texto del PDF. '
        'Sube el PDF digital original del SAT, no una fotografía o escaneo.')


def _validar_identidad(texto, *, alta=False):
    lineas = [line.strip() for line in texto.splitlines() if line.strip()]
    conjunto = _simple(texto)
    if ('CONSTANCIA DE SITUACION FISCAL' not in conjunto
            or 'CONTRIBUYENTE' not in conjunto):
        raise ConstanciaError('El archivo no parece una Constancia de Situación Fiscal emitida por el SAT.')
    rfc = _leer_rfc_sin_adivinar(texto)
    razon = _campo(lineas, 'Denominación/Razón Social',
                    'Denominación o Razón Social',
                    'Nombre, denominación o razón social', 'Razón Social')
    if not razon:
        nombres = _campo(lineas, 'Nombre(s)', 'Nombre (s)', 'Nombres')
        paterno = _campo(lineas, 'Primer Apellido', 'Apellido Paterno')
        materno = _campo(lineas, 'Segundo Apellido', 'Apellido Materno')
        if nombres and paterno:
            razon = ' '.join(filter(None, (nombres, paterno, materno)))
        else:
            razon = _campo(lineas, 'Nombre completo')
    razon = _simple(razon)
    if not 5 <= len(razon) <= 180:
        raise ConstanciaError('No se pudo leer con certeza el nombre o razón social de la constancia.')
    nombre_vialidad = _campo(lineas, 'Nombre de Vialidad', 'Calle')
    tipo_vialidad = _campo(lineas, 'Tipo de Vialidad')
    calle = _simple(nombre_vialidad)
    if tipo_vialidad and calle and not calle.startswith(_simple(tipo_vialidad) + ' '):
        calle = _simple(tipo_vialidad + ' ' + calle)
    # Facturación identifica al cliente exclusivamente por RFC y razón social.
    # El alta conserva su validación del domicilio; no se modifica esa operación.
    if alta and not 3 <= len(calle) <= 180:
        raise ConstanciaError('No se pudo leer con certeza la calle del domicilio fiscal en la constancia.')
    if not alta and not 3 <= len(calle) <= 180:
        calle = ''
    idcif = re.sub(r'\s+', '', _campo(lineas, 'idCIF', 'id CIF'))
    if alta and not re.fullmatch(r'\d{11}', idcif):
        raise ConstanciaError('La constancia PDF debe mostrar un idCIF legible de 11 dígitos para registrar el alta.')
    return IdentidadFiscal(rfc, razon, calle, idcif if re.fullmatch(r'\d{11}', idcif) else '')


def _pdf_obj(value):
    return value.get_object() if hasattr(value, 'get_object') else value


def _accion_pdf_peligrosa(value):
    value = _pdf_obj(value)
    if isinstance(value, (list, tuple)):
        return any(_accion_pdf_peligrosa(item) for item in value)
    if not isinstance(value, dict):
        return False
    if str(value.get('/S', '')) in ACCIONES_PDF_BLOQUEADAS or '/JS' in value:
        return True
    return any(_accion_pdf_peligrosa(value.get(key)) for key in ('/Next',) if key in value)


def _validar_pdf_pasivo(pdf):
    """Rechaza estructuras activas o anormalmente complejas antes de extraer texto."""
    try:
        objetos = int(pdf.trailer.get('/Size', 0) or 0)
    except (TypeError, ValueError):
        objetos = 0
    if not 1 <= objetos <= MAX_PDF_OBJECTS:
        raise ConstanciaError('El PDF tiene una estructura demasiado compleja para una constancia fiscal.')

    root = _pdf_obj(pdf.root_object)
    if not isinstance(root, dict):
        raise ConstanciaError('La estructura principal del PDF no es válida.')

    names = root.get('/Names')
    if names is not None:
        names = _pdf_obj(names)
        if not isinstance(names, dict):
            raise ConstanciaError('La estructura de recursos del PDF no es válida.')
        if '/JavaScript' in names or '/EmbeddedFiles' in names:
            raise ConstanciaError('La constancia PDF no debe contener JavaScript ni archivos adjuntos.')

    if '/AA' in root:
        raise ConstanciaError('La constancia PDF no debe contener acciones automáticas.')
    if '/OpenAction' in root and _accion_pdf_peligrosa(root['/OpenAction']):
        raise ConstanciaError('La constancia PDF contiene una acción activa no permitida.')

    form = root.get('/AcroForm')
    if form is not None:
        form = _pdf_obj(form)
        if isinstance(form, dict) and ('/XFA' in form or '/AA' in form):
            raise ConstanciaError('La constancia PDF contiene formularios activos no permitidos.')


def _texto_ocr_pdf(pdf):
    """OCR local opcional para constancias escaneadas, sin enviar datos externos."""
    try:
        from .ocr_ticket import find_tesseract
        tesseract = find_tesseract()
        if not tesseract:
            return ''
        import pytesseract
        from PIL import Image, ImageOps
        pytesseract.pytesseract.tesseract_cmd = tesseract
    except (ImportError, OSError):
        return ''

    textos = []
    for pagina in pdf.pages:
        imagenes = 0
        try:
            for imagen_pdf in pagina.images:
                if imagenes >= 2:
                    break
                imagenes += 1
                if len(imagen_pdf.data) > 8 * 1024 * 1024:
                    continue
                with Image.open(BytesIO(imagen_pdf.data)) as original:
                    ancho, alto = original.size
                    if ancho * alto < 150_000 or ancho * alto > 15_000_000:
                        continue
                    imagen = ImageOps.autocontrast(ImageOps.grayscale(original))
                    texto = pytesseract.image_to_string(
                        imagen, lang='spa+eng', config='--psm 3', timeout=8)
                    textos.append(texto[:30000])
        except Exception:
            # OCR es un respaldo; un fallo no elimina las validaciones del PDF.
            continue
    return '\n'.join(textos)


def leer_constancia_pdf_base64(valor, *, alta=False):
    """Verifica en memoria PDFs digitales y, si hay OCR local, escaneados."""
    if not isinstance(valor, str) or len(valor) > (MAX_PDF_BYTES * 4 // 3 + 16):
        raise ConstanciaError('Adjunta una constancia fiscal PDF de máximo 2 MB.')
    try:
        payload = base64.b64decode(valor, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ConstanciaError('El archivo de constancia no es válido. Descarga el PDF original del SAT.') from exc
    if not 200 <= len(payload) <= MAX_PDF_BYTES or not payload.startswith(b'%PDF-'):
        raise ConstanciaError('Solo se admite la constancia en formato PDF, máximo 2 MB.')

    try:
        from pypdf import PdfReader
        pdf = PdfReader(BytesIO(payload), strict=True)
        if pdf.is_encrypted:
            raise ConstanciaError('La constancia no debe estar protegida con contraseña.')
        _validar_pdf_pasivo(pdf)
        if not 1 <= len(pdf.pages) <= MAX_PDF_PAGES:
            raise ConstanciaError('La constancia PDF debe contener de 1 a 3 páginas.')
        variantes = []
        for modo in ('plain', 'layout'):
            textos = []
            try:
                for pagina in pdf.pages:
                    if modo == 'layout':
                        texto = pagina.extract_text(extraction_mode='layout') or ''
                    else:
                        texto = pagina.extract_text() or ''
                    if len(texto) > 30000:
                        raise ConstanciaError('El archivo PDF contiene demasiado texto para una constancia.')
                    textos.append(texto)
            except ConstanciaError:
                raise
            except Exception:
                if modo == 'layout':
                    continue
                raise
            variantes.append('\n'.join(textos))
    except ConstanciaError:
        raise
    except Exception as exc:
        raise ConstanciaError('No se pudo leer el PDF. Descarga una constancia digital nueva desde el SAT.') from exc

    ultimo_error = None
    for texto in variantes:
        if len(texto.strip()) < 80:
            continue
        try:
            return _validar_identidad(texto, alta=alta)
        except ConstanciaError as exc:
            if 'más de un RFC' in str(exc):
                raise
            ultimo_error = exc

    texto_ocr = _texto_ocr_pdf(pdf)
    if len(texto_ocr.strip()) >= 80:
        try:
            return _validar_identidad(texto_ocr, alta=alta)
        except ConstanciaError as exc:
            if 'más de un RFC' in str(exc):
                raise
            ultimo_error = exc

    if ultimo_error is not None:
        raise ultimo_error
    raise ConstanciaError(
        'No se pudo extraer texto legible del PDF. Sube la constancia digital '
        'del SAT o un escaneo nítido; para escaneos, el servidor requiere OCR instalado.')
