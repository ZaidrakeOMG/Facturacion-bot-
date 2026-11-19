"""Validación de fotos de tickets para el portal de facturación ARY.
No guarda imágenes y no confía en el folio recibido del navegador.
"""
from __future__ import annotations

import base64
import binascii
import io
import re
import unicodedata
from collections import Counter

MAX_FOTO_BYTES = 3 * 1024 * 1024
MAX_PIXELS = 16_000_000
MIN_WIDTH = 650
MIN_HEIGHT = 800


class FotoTicketError(ValueError):
    """Mensaje seguro para mostrar al cliente."""


def _normalizar(texto: str) -> str:
    out = unicodedata.normalize('NFKD', texto.upper())
    return ''.join(x for x in out if not unicodedata.combining(x))


def _es_reimpresion(texto: str) -> bool:
    # Incluir RE-IMPRESION, REIMPRESIÓN, REIMPRESO y REIMP.
    limpio = re.sub(r'[^A-Z0-9]', '', _normalizar(texto))
    return bool(re.search(r'REIMP(?:RES(?:ION|O|A)?|R(?:ES)?|$)', limpio))


def _folios_etiquetados(texto: str) -> list[str]:
    folios = []
    for linea in texto.splitlines():
        normal = _normalizar(linea)
        # Solo tras la etiqueta FOLIO: nunca el teléfono, importe ni RFC.
        m = re.search(r'\bF[O0]L[I1]O\b\s*(?:N[O0]\.?\s*)?[:#-]?\s*([0-9OIL|]{6,20})(?![0-9])', normal)
        if not m:
            continue
        numero = m.group(1).translate(str.maketrans({'O': '0', 'I': '1', 'L': '1', '|': '1'}))
        if numero.isdigit() and 6 <= len(numero) <= 20:
            folios.append(numero)
    return folios


def _numeros_independientes(texto: str) -> list[str]:
    """Números fuera de la línea FOLIO, p. ej. el impreso bajo el código de barras."""
    encontrados = []
    for linea in texto.splitlines():
        if re.search(r'F[O0]L[I1]O', _normalizar(linea)):
            continue
        encontrados.extend(re.findall(r'(?<!\d)\d{8,20}(?!\d)', linea))
    return encontrados



def _folios_codigo_barras(imagen) -> list[str]:
    """Lee CODE39/CODE128 con pyzbar si esta instalado; no sustituye el OCR."""
    try:
        from pyzbar.pyzbar import decode
    except (ImportError, OSError):
        return []
    try:
        valores = []
        for codigo in decode(imagen):
            if codigo.type not in ('CODE39', 'CODE128'):
                continue
            try:
                valor = codigo.data.decode('ascii')
            except (UnicodeDecodeError, AttributeError):
                continue
            if re.fullmatch(r'[0-9]{6,20}', valor) and valor not in valores:
                valores.append(valor)
        return valores
    except (OSError, ValueError, RuntimeError):
        # Si falla la libreria externa, la validacion OCR tradicional continua.
        return []


def _coincide_codigo_y_ocr(codigo: str, lectura: str) -> bool:
    """Permite hasta dos errores de OCR, nunca elimina los ceros iniciales."""
    return (len(codigo) == len(lectura)
            and sum(a != b for a, b in zip(codigo, lectura)) <= 2)



def _folios_en_zona_folio(im, pytesseract) -> list[str]:
    """Lee SOLO la linea FOLIO, incluso cuando falla el OCR de toda la foto.

    Localiza el rotulo en la imagen original, recorta a su derecha,
    agranda y analiza con dos modos de Tesseract. Un unico resultado
    dudoso nunca autoriza un folio.
    """
    from PIL import Image, ImageOps

    try:
        datos = pytesseract.image_to_data(
            im, lang='spa+eng', config='--psm 6',
            output_type=pytesseract.Output.DICT, timeout=7,
        )
    except Exception:
        return []
    lecturas: list[str] = []
    for i, palabra in enumerate(datos.get('text', [])):
        if not re.fullmatch(r'F[O0]L[I1]O', str(palabra).strip().upper()):
            continue
        try:
            x = int(datos['left'][i])
            y = int(datos['top'][i])
            w = int(datos['width'][i])
            h = int(datos['height'][i])
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        if w < 15 or h < 8:
            continue
        # El numero impreso tiene aproximadamente el doble de anchura
        # que FOLIO; el margen adicional evita cortar ceros del borde.
        zona = (
            max(0, x - round(w * 0.17)),
            max(0, y - round(h * 0.125)),
            min(im.width, x + round(w * 3.6)),
            min(im.height, y + round(h * 1.5)),
        )
        if zona[2] <= zona[0] or zona[3] <= zona[1]:
            continue
        recorte = ImageOps.autocontrast(ImageOps.grayscale(im.crop(zona)))
        recorte = recorte.resize(
            (recorte.width * 2, recorte.height * 2),
            Image.Resampling.LANCZOS,
        )
        for psm in (6, 7):
            try:
                lectura = pytesseract.image_to_string(
                    recorte, lang='spa+eng',
                    config=f'--psm {psm}', timeout=6,
                )
                lecturas.extend(_folios_etiquetados(lectura))
            except Exception:
                continue
        if len(lecturas) >= 2:
            break  # una sola linea FOLIO por ticket
    votos = Counter(lecturas)
    return [n for n, veces in votos.items() if veces >= 2]




def _rescate_compra_y_folio(im, pytesseract) -> tuple[list[str], bool]:
    """OCR localizado para fotos tomadas con el celular.

    En una foto con fondo oscuro, el OCR de la foto completa suele leer
    solamente FOLIO y omitir TOTAL, LITROS y DISPENSARIO. A partir del
    rotulo FOLIO se recortan la linea numerica y los datos de compra.
    Nunca se acepta el numero con una sola lectura, ni se salta la
    prohibicion de reimpresiones.
    """
    from PIL import Image, ImageOps, ImageFilter

    contraste = ImageOps.autocontrast(ImageOps.grayscale(im)).filter(ImageFilter.SHARPEN)
    try:
        datos = pytesseract.image_to_data(
            contraste, lang='spa+eng', config='--psm 6',
            output_type=pytesseract.Output.DICT, timeout=9,
        )
    except Exception:
        return [], False

    for i, palabra in enumerate(datos.get('text', [])):
        if not re.fullmatch(r'F[O0]L[I1]O', str(palabra).strip().upper()):
            continue
        try:
            x = int(datos['left'][i])
            y = int(datos['top'][i])
            w = int(datos['width'][i])
            h = int(datos['height'][i])
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        if w < 15 or h < 8:
            continue

        # Margen vertical generoso: recortar demasiado pegado a la linea
        # impresa hace que Tesseract lea mal los ceros iniciales.
        folio_box = (
            max(0, x - round(w * 0.15)),
            max(0, y - h),
            min(im.width, x + round(w * 3.75)),
            min(im.height, y + round(h * 2.4)),
        )
        if folio_box[2] <= folio_box[0] or folio_box[3] <= folio_box[1]:
            continue
        recorte = im.crop(folio_box)
        folios = []
        for zona in (recorte, recorte.resize(
                (recorte.width * 2, recorte.height * 2), Image.Resampling.LANCZOS)):
            try:
                texto = pytesseract.image_to_string(
                    zona, lang='spa+eng', config='--psm 6', timeout=8)
                folios.extend(_folios_etiquetados(texto))
            except Exception:
                continue
        votos = Counter(folios)
        confirmados = [numero for numero, cuenta in votos.items() if cuenta >= 2]

        # La zona inferior conserva datos de compra que se pierden cuando
        # se reconoce todo el fondo, la interfaz de la camara y el ticket.
        compra_box = (
            max(0, x - round(w * 0.8)),
            min(im.height, y + 3 * h),
            min(im.width, x + round(w * 5.1)),
            min(im.height, y + 23 * h),
        )
        compra_valida = False
        if compra_box[2] > compra_box[0] and compra_box[3] > compra_box[1]:
            compra = ImageOps.autocontrast(ImageOps.grayscale(im.crop(compra_box)))
            compra = compra.filter(ImageFilter.UnsharpMask(
                radius=1.5, percent=180, threshold=1))
            try:
                texto_compra = _normalizar(pytesseract.image_to_string(
                    compra, lang='spa+eng', config='--psm 6', timeout=9))
                # En la zona localizada exigir al menos un concepto de
                # compra Y un importe legible, no solo la palabra FOLIO.
                compra_valida = bool(
                    re.search(r'\b(?:TOTAL|DISPENSARIO|PRODUCTO|LITROS|OPERACION)\b', texto_compra)
                    and re.search(r'\$?\s*\d{1,6}[.,]\d{2}\b', texto_compra)
                )
            except Exception:
                pass
        return confirmados, compra_valida
    return [], False



def analizar_ticket_foto(foto_base64: str) -> dict[str, str]:
    """Extrae el folio como cadena y rechaza tickets ilegibles/reimpresiones."""
    if (not isinstance(foto_base64, str) or not foto_base64
            or len(foto_base64) > MAX_FOTO_BYTES * 4 // 3 + 16):
        raise FotoTicketError('Sube una foto JPG, PNG o WEBP del ticket (máximo 3 MB).')
    try:
        foto = base64.b64decode(foto_base64, validate=True)
    except (ValueError, binascii.Error):
        raise FotoTicketError('La fotografía no se pudo leer. Toma otra foto del ticket.') from None
    if not 1000 <= len(foto) <= MAX_FOTO_BYTES:
        raise FotoTicketError('La foto debe ser legible y no superar 3 MB.')
    try:
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(foto)) as loaded:
            if loaded.format not in {'JPEG', 'PNG', 'WEBP'}:
                raise FotoTicketError('Usa una foto JPG, PNG o WEBP.')
            if loaded.width * loaded.height > MAX_PIXELS:
                raise FotoTicketError('La foto es demasiado grande. Reduce su tamaño e inténtalo.')
            loaded.load()
            im = ImageOps.exif_transpose(loaded).convert('RGB')
    except FotoTicketError:
        raise
    except Exception:
        raise FotoTicketError('No se pudo abrir la foto. Vuelve a tomarla, por favor.') from None
    if min(im.width, im.height) < MIN_WIDTH or max(im.width, im.height) < MIN_HEIGHT:
        raise FotoTicketError('La foto tiene poca resolución. Acércate al ticket e inténtalo otra vez.')
    try:
        import pytesseract
        from PIL import ImageOps, ImageFilter
        from .ocr_ticket import find_tesseract
        exe = find_tesseract()
        if not exe:
            raise FotoTicketError('La lectura del ticket no está disponible. Comunícate con facturación.')
        pytesseract.pytesseract.tesseract_cmd = exe
        original = pytesseract.image_to_string(im, lang='spa+eng', config='--psm 6', timeout=12)
        contrastada = ImageOps.autocontrast(ImageOps.grayscale(im)).filter(ImageFilter.SHARPEN)
        alternativa = pytesseract.image_to_string(contrastada, lang='spa+eng', config='--psm 6', timeout=12)
    except FotoTicketError:
        raise
    except Exception:
        raise FotoTicketError('No se pudo leer el ticket. Intenta otra foto más nítida.') from None

    if _es_reimpresion(original) or _es_reimpresion(alternativa):
        raise FotoTicketError('Este ticket indica REIMPRESIÓN. No se puede facturar desde el portal.')

    etiquetados = _folios_etiquetados(original) + _folios_etiquetados(alternativa)
    independientes = _numeros_independientes(original) + _numeros_independientes(alternativa)
    votos = Counter(etiquetados)
    # Leer el codigo de barras puede recuperar ceros o digitos confundidos
    # por Tesseract. No se acepta un codigo sin evidencia de FOLIO impreso.
    codigos = _folios_codigo_barras(im)
    folio_barras = codigos[0] if len(codigos) == 1 else None
    folios_zona = _folios_en_zona_folio(im, pytesseract)
    comprobante = _normalizar(original + '\n' + alternativa)
    tiene_compra = bool(re.search(
        r'\b(?:TOTAL|DISPENSARIO|PRODUCTO|LITROS|OPERACION)\b', comprobante))
    if not tiene_compra or not folios_zona:
        recuperados, compra_local = _rescate_compra_y_folio(im, pytesseract)
        if recuperados and folios_zona and set(recuperados) != set(folios_zona):
            raise FotoTicketError(
                'Las lecturas del folio no coinciden. Toma otra foto mas nitida.')
        if recuperados:
            folios_zona = recuperados
        tiene_compra = tiene_compra or compra_local
    if not tiene_compra:
        raise FotoTicketError(
            'No se reconoce el ticket completo. Toma una foto donde aparezcan '
            'folio y datos de la compra.')
    if len(folios_zona) == 1:
        folio_zona = folios_zona[0]
        if codigos and folio_zona not in codigos:
            raise FotoTicketError(
                'El codigo de barras y el folio impreso no coinciden. '
                'Toma una fotografia completa y mas nitida.')
        # Dos reconocimientos de la misma linea aislada son mejores
        # que interpretar el documento completo en una foto inclinada.
        return {'folio': folio_zona}
    if folio_barras and any(_coincide_codigo_y_ocr(folio_barras, n) for n in etiquetados):
        # El codigo decodificado prevalece sobre lecturas OCR con 1-2
        # digitos distintos; su formato y sus ceros se preservan literalmente.
        return {'folio': folio_barras}
    # Sin barras validas, conservar el criterio estricto anterior:
    # dos lecturas OCR iguales o FOLIO cotejado con otro numero impreso.
    candidatos = [n for n in votos if votos[n] >= 2 or n in independientes]
    if len(candidatos) != 1:
        raise FotoTicketError(
            'No pudimos leer el folio con seguridad. '
            'Sube otra foto más legible, completa y bien iluminada.')
    return {'folio': candidatos[0]}
