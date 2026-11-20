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
    """Decodifica CODE39/CODE128 directamente del papel y de zonas cercanas.

    CODE39 es preferible a tratar de reconstruir 0/6/8 con OCR. No cambia
    ningun digito del dato decodificado y conserva ceros iniciales.
    """
    from PIL import ImageOps

    valores: list[str] = []

    def agregar(valor: str) -> None:
        if re.fullmatch(r'[0-9]{6,20}', valor) and valor not in valores:
            valores.append(valor)

    gris = ImageOps.grayscale(imagen)
    alto = imagen.height
    recorte = gris.crop((0, round(alto * 0.42), imagen.width, alto))
    variantes = (
        imagen,
        gris,
        recorte,
        ImageOps.autocontrast(recorte),
    )

    try:
        import zxingcpp
        for fuente in variantes:
            for codigo in zxingcpp.read_barcodes(fuente):
                formato = str(codigo.format).upper().replace(' ', '').replace('_', '')
                if 'CODE39' in formato or 'CODE128' in formato:
                    agregar(str(codigo.text).strip())
    except (ImportError, OSError, RuntimeError, ValueError):
        pass

    try:
        from pyzbar.pyzbar import decode
        for fuente in variantes:
            for codigo in decode(fuente):
                if codigo.type not in ('CODE39', 'CODE128'):
                    continue
                try:
                    agregar(codigo.data.decode('ascii').strip())
                except (UnicodeDecodeError, AttributeError):
                    continue
    except (ImportError, OSError, RuntimeError, ValueError):
        pass

    return valores



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
            max(0, y - h + 1),
            min(im.width, x + round(w * 3.72)),
            min(im.height, y + round(h * 2.375)),
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




def _detectar_papel_por_luz(im):
    """Detecta la tira blanca dentro de fotos con fondo oscuro o interfaz de cámara.

    Todo es Pillow (sin NumPy, OpenCV ni conexiones externas). Si el contraste
    no permite localizar el papel, las otras vías de OCR siguen funcionando.
    """
    import statistics
    from PIL import Image, ImageOps, ImageFilter

    mini = ImageOps.grayscale(im)
    mini.thumbnail((420, 800), Image.Resampling.LANCZOS)
    mini = mini.filter(ImageFilter.GaussianBlur(3))
    w, h = mini.size
    if w < 40 or h < 40:
        return None
    pixels = mini.load()
    muestras = sorted(pixels[w // 2, yy] for yy in range(h))
    umbral = max(95, min(179, round(muestras[int((h - 1) * 0.72)] * 0.76)))

    def racha_mayor(banderas, hueco):
        mejor = (0, 0)
        inicio = None
        perdidos = 0
        for pos, activo in enumerate(banderas + [False] * (hueco + 1)):
            if activo:
                if inicio is None:
                    inicio = pos
                perdidos = 0
            elif inicio is not None:
                perdidos += 1
                if perdidos > hueco:
                    fin = pos - perdidos + 1
                    if fin - inicio > mejor[1] - mejor[0]:
                        mejor = (inicio, fin)
                    inicio = None
                    perdidos = 0
        return mejor

    candidatos = []
    for fraccion in (0.4, 0.5, 0.6):
        x = int(w * fraccion)
        izq, der = max(0, x - 3), min(w, x + 4)
        franja = [
            sum(pixels[col, yy] for col in range(izq, der)) / (der - izq) > umbral
            for yy in range(h)
        ]
        inicio, fin = racha_mayor(franja, max(4, h // 120))
        candidatos.append((fin - inicio, inicio, fin, x))
    largo, arriba, abajo, centro = max(candidatos)
    if largo < 0.27 * h:
        return None

    limites = []
    for altura in (0.24, 0.43, 0.62):
        yy = min(h - 1, arriba + int((abajo - arriba) * altura))
        izq = der = centro
        while izq > 3 and pixels[izq, yy] > umbral:
            izq -= 1
        while der < w - 4 and pixels[der, yy] > umbral:
            der += 1
        limites.append((izq, der))
    izq = int(statistics.median(a for a, _ in limites))
    der = int(statistics.median(b for _, b in limites))
    if der - izq < 0.27 * w:
        return None

    return (
        max(0, round(izq * im.width / w)),
        max(0, round(arriba * im.height / h)),
        min(im.width, round(der * im.width / w)),
        min(im.height, round(abajo * im.height / h)),
    )


def _ocr_tira_mejorado(im, pytesseract):
    """Lee folio DOS veces en recortes independientes y valida datos de compra.

    No inventa ceros ni depende de encontrar el rótulo FOLIO con image_to_data.
    El ticket puede ocupar una fracción de la foto original.
    """
    from PIL import ImageOps

    bounds = _detectar_papel_por_luz(im)
    if bounds is None:
        bounds = (0, 0, im.width, im.height)
    x0, y0, x1, y1 = bounds
    w, h = x1 - x0, y1 - y0
    if w < 150 or h < 300:
        return [], False, False

    regiones = [
        (
            max(0, x0 - round(w * 0.07)),
            max(0, y0 + round(h * 0.06)),
            min(im.width, x1 + round(w * 0.05)),
            min(im.height, y0 + round(h * 0.63)),
        ),
        (
            max(0, x0 - round(w * 0.055)),
            max(0, y0 + round(h * 0.11)),
            min(im.width, x1 + round(w * 0.035)),
            min(im.height, y0 + round(h * 0.57)),
        ),
        (
            max(0, x0 + round(w * 0.025)),
            max(0, y0 + round(h * 0.30)),
            min(im.width, x1 - round(w * 0.065)),
            min(im.height, y0 + round(h * 0.88)),
        ),
    ]
    textos = []
    for indice, caja in enumerate(regiones):
        if caja[2] <= caja[0] or caja[3] <= caja[1]:
            return [], False, False
        recorte = im.crop(caja)
        if indice == 2:
            recorte = ImageOps.autocontrast(ImageOps.grayscale(recorte))
        try:
            textos.append(pytesseract.image_to_string(
                recorte, lang='spa+eng', config='--psm 6', timeout=10,
            ))
        except Exception:
            return [], False, False

    # Una etiqueta FOLIO legible en dos recortes diferentes es obligatoria.
    primero = set(_folios_etiquetados(textos[0]))
    segundo = set(_folios_etiquetados(textos[1]))
    repetidos = sorted(primero & segundo)
    compra = _normalizar(textos[2])
    tiene_concepto = bool(re.search(
        r'\b(?:TOTAL|SUBTOTAL|DISPENSARIO|LITROS|PRODUCTO|PRECIO|OPERACION|IMPORTE)\b',
        compra,
    ))
    tiene_importe = bool(re.search(
        r'(?:[$S]\s*)?\d{1,7}[.,]\s*\d{2}\b',
        compra,
    ))
    # Si detecta REIMPRESION en cualquier recorte, debe bloquearse.
    reimpresion = any(_es_reimpresion(t) for t in textos)
    return repetidos, tiene_concepto and tiene_importe, reimpresion




def _leer_numeros_por_rotulo(im, pytesseract) -> list[str]:
    """Busca FOLIO y verifica solo los digitos a su derecha.

    El OCR de la linea completa suele leer 0 como 6/8. Dos recortes, dos
    escalas y dos PSM con whitelist numerica permiten exigir al menos
    cinco votos identicos sin inventar ni quitar ceros iniciales.
    """
    from PIL import Image, ImageOps

    papel = _detectar_papel_por_luz(im)
    regiones = []
    if papel:
        x0, y0, x1, y1 = papel
        if x1 > x0 and y1 > y0:
            tira = im.crop(papel)
            altura = max(1, round((y1 - y0) * 0.68))
            superior = im.crop((x0, y0, x1, y0 + altura))
            regiones.extend([
                (tira, x0, y0),
                (ImageOps.autocontrast(ImageOps.grayscale(tira)), x0, y0),
                (superior, x0, y0),
                (ImageOps.autocontrast(ImageOps.grayscale(superior)), x0, y0),
            ])
    regiones.append((im, 0, 0))

    for region, dx, dy in regiones:
        for psm in (6, 11):
            try:
                datos = pytesseract.image_to_data(
                    region, lang='spa+eng', config=f'--psm {psm}',
                    output_type=pytesseract.Output.DICT, timeout=9)
            except Exception:
                continue
            for i, palabra in enumerate(datos.get('text', [])):
                if not re.fullmatch(r'F[O0]L[I1]O', str(palabra).strip().upper()):
                    continue
                try:
                    x = dx + int(datos['left'][i])
                    y = dy + int(datos['top'][i])
                    ancho = int(datos['width'][i])
                    alto = int(datos['height'][i])
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
                if ancho < 20 or alto < 10:
                    continue
                izquierda = max(0, x + ancho + round(ancho * 0.08))
                arriba = max(0, y + round(alto * 0.08))
                derecha = min(im.width, x + ancho + round(ancho * 2.50))
                abajo = min(im.height, y + round(alto * 1.34))
                if derecha - izquierda < 70 or abajo - arriba < 15:
                    continue
                digitos = im.crop((izquierda, arriba, derecha, abajo))
                lecturas = []
                for escala in (1, 2):
                    ampliada = digitos.resize(
                        (digitos.width * escala, digitos.height * escala),
                        Image.Resampling.LANCZOS)
                    for variante in (
                        ampliada, ImageOps.autocontrast(ImageOps.grayscale(ampliada))
                    ):
                        for modo in (7, 8):
                            try:
                                valor = pytesseract.image_to_string(
                                    variante, lang='eng',
                                    config=f'--psm {modo} -c tessedit_char_whitelist=0123456789',
                                    timeout=7).strip()
                            except Exception:
                                continue
                            if re.fullmatch(r'[0-9]{6,20}', valor):
                                lecturas.append(valor)
                votos = Counter(lecturas).most_common(2)
                if votos:
                    mejor, n = votos[0]
                    segundo = votos[1][1] if len(votos) > 1 else 0
                    if n >= 5 and n >= segundo + 3:
                        return [mejor]
    return []




def _validar_compra_en_papel(im, pytesseract) -> tuple[bool, bool]:
    """Verifica la compra en el papel, no en la captura completa del celular.

    El OCR de toda la fotografia (con fondo oscuro y controles de la camara)
    puede omitir TOTAL, LITROS y DISPENSARIO aunque sean claramente visibles.
    Detectar y recortar el papel permite leer esas palabras y el importe.
    El primer booleano indica que hay datos de compra, el segundo si el
    recibo indica REIMPRESION. No guarda fotos ni altera importes.
    """
    from PIL import ImageOps

    caja = _detectar_papel_por_luz(im)
    if caja is None:
        return False, False
    x0, y0, x1, y1 = caja
    if x1 - x0 < 200 or y1 - y0 < 300:
        return False, False

    papel = ImageOps.autocontrast(ImageOps.grayscale(im.crop(caja)))
    for modo in (6, 11):
        try:
            texto = pytesseract.image_to_string(
                papel, lang='spa+eng', config=f'--psm {modo}', timeout=12)
        except Exception:
            continue
        if _es_reimpresion(texto):
            return False, True
        lectura = _normalizar(texto)
        tiene_total = bool(re.search(r'\bTOTAL\b', lectura))
        otro_dato = bool(re.search(
            r'\b(?:DISPENSARIO|LITROS|PRODUCTO|PRECIO|OPERACION|'
            r'SUBTOTAL|MAGNA|DIESEL|PREMIUM)\b', lectura))
        tiene_importe = bool(re.search(
            r'(?:[$S]\s*)?\d{1,7}[.,]\s*\d{2}\b', lectura))
        if tiene_total and otro_dato and tiene_importe:
            return True, False
    return False, False



def _evidencia_folio_y_compra(im, pytesseract) -> tuple[bool, bool, bool]:
    """Busca la ETIQUETA FOLIO y TOTAL en el papel recortado.

    A diferencia del OCR numerico, aqui no reconstruimos el folio:
    solo confirmamos que la barra decodificada pertenece a un ticket
    de compra. Reconoce variantes OCR FULIO/FULTO.
    """
    from PIL import ImageOps

    caja = _detectar_papel_por_luz(im)
    if caja is None:
        papel = im
    else:
        papel = im.crop(caja)
    papel = ImageOps.autocontrast(ImageOps.grayscale(papel))
    tiene_rotulo = False
    tiene_compra = False
    for psm in (6, 11):
        try:
            texto = pytesseract.image_to_string(
                papel, lang='spa+eng', config=f'--psm {psm}', timeout=12,
            )
        except Exception:
            continue
        if _es_reimpresion(texto):
            return False, False, True
        normal = _normalizar(texto)
        tiene_rotulo |= bool(re.search(r'\bF[O0U]L[I1T]O\b', normal))
        tiene_total = bool(re.search(r'\bT[O0]TAL\b', normal))
        otro_concepto = bool(re.search(
            r'\b(?:DISPENSARIO|L[I1]TROS|PRODUCTO|PRECIO|'
            r'OPERACION|SUBTOTAL|MAGNA|DIESEL|PREMIUM)\b',
            normal,
        ))
        tiene_importe = bool(re.search(
            r'(?:[$S]\s*)?\d{1,7}[.,]\s*\d{2}\b', normal))
        tiene_compra |= tiene_total and otro_concepto and tiene_importe
        if tiene_rotulo and tiene_compra:
            break
    return tiene_rotulo, tiene_compra, False



def analizar_codigo_barras_foto(foto_base64: str) -> dict[str, str]:
    """Lee solo CODE39/CODE128: sin OCR, sin suposiciones ni redondeos.

    La foto puede ser un recorte horizontal que incluya unicamente las
    barras; conserva los ceros iniciales. El barcode NO acredita por si
    solo fecha, compra original ni ausencia de reimpresiones. Esos datos
    deben verificarse en el flujo de facturacion.
    """
    if (not isinstance(foto_base64, str) or not foto_base64
            or len(foto_base64) > MAX_FOTO_BYTES * 4 // 3 + 16):
        raise FotoTicketError(
            'Toma una foto JPG, PNG o WEBP del codigo de barras (maximo 3 MB).')
    try:
        datos = base64.b64decode(foto_base64, validate=True)
    except (ValueError, binascii.Error):
        raise FotoTicketError('No se pudo abrir la foto del codigo de barras.') from None
    if not 1000 <= len(datos) <= MAX_FOTO_BYTES:
        raise FotoTicketError('La foto del codigo de barras debe pesar menos de 3 MB.')
    try:
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(datos)) as imagen:
            if imagen.format not in {'JPEG', 'PNG', 'WEBP'}:
                raise FotoTicketError('Usa una imagen JPG, PNG o WEBP.')
            if imagen.width * imagen.height > MAX_PIXELS:
                raise FotoTicketError('La imagen es demasiado grande.')
            imagen.load()
            foto = ImageOps.exif_transpose(imagen).convert('RGB')
    except FotoTicketError:
        raise
    except Exception:
        raise FotoTicketError('La foto del codigo de barras no se pudo abrir.') from None
    # Una foto de solo barras puede medir 350 x 140: no exigir la
    # resolucion minima de un ticket fotografiado completo.
    if min(foto.size) < 90 or max(foto.size) < 260:
        raise FotoTicketError(
            'Acercate al codigo de barras y enfocalo para que ocupe la foto.')

    codigos = _folios_codigo_barras(foto)
    if not codigos:
        raise FotoTicketError(
            'No se pudo decodificar el codigo de barras. '
            'Acercate, enfocando todas las barras y ambos extremos.')
    if len(codigos) != 1:
        raise FotoTicketError(
            'Se detectaron codigos distintos. Toma una foto de un solo codigo de barras.')

    # Cada cifra viene de un decodificador de simbologia, NO de OCR.
    return {'folio': codigos[0]}



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

    # Un OCR que repite 8 veces un digito equivocado NO confirma el folio.
    # Decodificar CODE39/CODE128 proporciona el numero sin sustituir cifras.
    barras = _folios_codigo_barras(im)
    if len(barras) > 1:
        raise FotoTicketError(
            'Se encontraron codigos de barras contradictorios en la foto. '
            'No se acepto ningun folio.')

    rotulo_papel, compra_papel, reimp_papel = _evidencia_folio_y_compra(
        im, pytesseract)
    if reimp_papel:
        raise FotoTicketError(
            'Este ticket indica REIMPRESIÓN. No se puede facturar desde el portal.')

    comprobante = _normalizar(original + '\n' + alternativa)
    rotulo_global = bool(re.search(r'\bF[O0U]L[I1T]O\b', comprobante))
    compra_global = bool(
        re.search(r'\bTOTAL\b', comprobante)
        and re.search(
            r'\b(?:DISPENSARIO|LITROS|PRODUCTO|PRECIO|OPERACION)\b',
            comprobante)
        and re.search(r'(?:[$S]\s*)?\d{1,7}[.,]\s*\d{2}\b',
                      comprobante))

    if barras:
        # Un barcode es mas preciso que los digitos OCR; sin embargo,
        # una lectura etiquetada claramente contradictoria impide
        # confundir un codigo de otro ticket. FULIO/FULTO no generan
        # una lectura numerica etiquetada y no bloquean el barcode.
        impresos = set(_folios_etiquetados(original) +
                       _folios_etiquetados(alternativa))
        if impresos and not any(
            _coincide_codigo_y_ocr(barras[0], impreso)
            for impreso in impresos
        ):
            raise FotoTicketError(
                'El folio impreso no coincide con el codigo de barras. '
                'No se acepto ningun numero.')

        # No exigir que Tesseract lea bien TODOS los digitos: el
        # codigo de barras ya contiene el folio exacto. El OCR solo
        # debe confirmar la etiqueta FOLIO y los datos de una compra.
        if not (rotulo_papel or rotulo_global):
            raise FotoTicketError(
                'El codigo de barras se leyo, pero el OCR no detecto '
                'la palabra FOLIO en el papel.')
        if not (compra_papel or compra_global):
            raise FotoTicketError(
                'El codigo de barras se leyo, pero no se confirmaron '
                'TOTAL y datos de la compra.')
        return {'folio': barras[0]}

    # Sin codigo decodificable NO usar mayoria de 5/8 pasadas. Requerir
    # dos impresiones diferentes de exactamente el mismo folio: la linea
    # FOLIO y los numeros que aparecen debajo del codigo de barras.
    etiquetados = set(
        _folios_etiquetados(original) + _folios_etiquetados(alternativa))
    independientes = set(
        _numeros_independientes(original) + _numeros_independientes(alternativa))
    coincidentes = etiquetados & independientes
    if len(coincidentes) != 1:
        raise FotoTicketError(
            'No se pudo confirmar el folio exacto: no se decodifica el codigo de barras ni se obtienen '
            'dos impresiones identicas del folio. No se acepto ningun numero.')

    if not (compra_papel or compra_global):
        raise FotoTicketError(
            'Se leyo el folio, pero no se confirmaron TOTAL y los '
            'datos de compra.')
    return {'folio': next(iter(coincidentes))}
