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
    # Dos lecturas OCR concordantes, o una línea FOLIO respaldada
    # por el número impreso por separado bajo el código de barras.
    candidatos = [n for n in votos if votos[n] >= 2 or n in independientes]
    if len(candidatos) != 1:
        raise FotoTicketError(
            'No pudimos leer el folio con seguridad. '
            'Sube otra foto más legible, completa y bien iluminada.')
    comprobante = _normalizar(original + '\n' + alternativa)
    if not re.search(r'\b(?:TOTAL|DISPENSARIO|PRODUCTO|LITROS|OPERACION)\b', comprobante):
        raise FotoTicketError(
            'No se reconoce el ticket completo. Toma una foto donde aparezcan '
            'folio y datos de la compra.')
    return {'folio': candidatos[0]}
