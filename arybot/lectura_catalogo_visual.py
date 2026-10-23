"""Respaldo visual conservador para catálogo VCL de Polaris sin GridPattern/portapapeles.

Nunca pulsa Aceptar ni guarda pantallazos: solamente permite verificar una
única fila seleccionada, visible y legible, por RFC. La captura se queda en RAM.
"""
from __future__ import annotations
import re
import time

from .coincidencia_fiscal import CoincidenciaFiscalError, verificar_unico

RFC_RE = re.compile(r'(?<![A-Z0-9Ñ&])([A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3})(?![A-Z0-9Ñ&])')


def _parsear_cuadricula(texto, rfc):
    """Sólo una fila de datos, con número de cliente y RFC legibles."""
    lineas = [line.upper().strip() for line in str(texto or '').splitlines() if line.strip()]
    filas = []
    for linea in lineas:
        if len(linea) < 3:
            # Un píxel del borde VCL puede detectarse como un solo carácter.
            # La comprobación de píxeles ya descartó otras filas visibles.
            continue
        head = re.sub(r'[^A-Z]', '', linea)
        if 'NUMERO' in head and 'NOMBRE' in head and 'RFC' in head:
            continue
        # Otra línea de datos o ruido: nunca elegir al primero.
        resultados = list(RFC_RE.finditer(linea))
        if len(resultados) != 1:
            raise CoincidenciaFiscalError(
                'La imagen del catálogo no permite contar las filas de clientes de forma segura.')
        encontrado = resultados[0]
        previa = linea[:encontrado.start()]
        match = re.match(r'^\s*(\d{1,12})\s*[|]?\s*(.+?)\s*$', previa)
        if not match or not re.search(r'[A-ZÁÉÍÓÚÑ]', match.group(2)):
            raise CoincidenciaFiscalError('No se verificó el número y nombre de la fila de Polaris.')
        filas.append({'numero': match.group(1),
                      'razon_social': match.group(2), 'rfc': encontrado.group(1)})
    if len(filas) != 1:
        raise CoincidenciaFiscalError('La tabla no muestra exactamente una fila identificable.')
    return verificar_unico(filas, rfc=rfc, razon_social='')


def _una_sola_fila_dibujada(imagen):
    """Exige la única fila azul del VCL y ninguna otra fila de texto debajo."""
    image = imagen.convert('RGB')
    w, h = image.size
    if w < 430 or h < 160:
        raise CoincidenciaFiscalError('El tamaño del catálogo no admite lectura visual segura.')
    pixels = image.load()
    xs = range(9, w - 9, 2)
    blues = []
    for y in range(12, min(105, h // 3)):
        count = 0
        for x in xs:
            r, g, b = pixels[x, y]
            if r < 65 and 50 < g < 195 and b > 155 and b > g:
                count += 1
        if count >= .60 * len(xs):
            blues.append(y)
    if not blues or blues[0] > min(55, h // 6) or blues[-1] - blues[0] > min(55, h // 6):
        raise CoincidenciaFiscalError('No se comprobó una única fila seleccionada en Polaris.')
    if any(b - a > 2 for a, b in zip(blues, blues[1:])):
        raise CoincidenciaFiscalError('Se observó más de una selección visual en Polaris.')
    # Una fila adicional no seleccionada presenta texto oscuro más abajo.
    for y in range(blues[-1] + 5, h - 16):
        tinta = 0
        for x in xs:
            r, g, b = pixels[x, y]
            if max(r, g, b) < 155:
                tinta += 1
        if tinta > max(8, .015 * len(xs)):
            raise CoincidenciaFiscalError('Se observaron otras filas de clientes en Polaris.')


def _lectura_visual(imagen, rfc, tesseract):
    """OCR del grid exclusivamente; excluye el RFC escrito en la búsqueda."""
    _una_sola_fila_dibujada(imagen)
    try:
        import pytesseract
        from PIL import ImageOps
    except ImportError as exc:
        raise CoincidenciaFiscalError('Falta Pillow o pytesseract para verificar Polaris VCL.') from exc
    pytesseract.pytesseract.tesseract_cmd = tesseract
    img = ImageOps.autocontrast(ImageOps.grayscale(
        imagen.resize((imagen.width * 2, imagen.height * 2))))
    try:
        texto = pytesseract.image_to_string(img, lang='eng', config='--psm 6', timeout=8)
    except Exception as exc:
        raise CoincidenciaFiscalError('Tesseract no pudo comprobar visualmente Polaris.') from exc
    return _parsear_cuadricula(texto, rfc)


def _capturar_grid(bot, dialogo, pa):
    rect = bot._rect(dialogo)
    if not 560 <= rect.width <= 1600 or not 340 <= rect.height <= 1100:
        raise CoincidenciaFiscalError('El diálogo cambió de tamaño; no se usará lectura visual.')
    region = (rect.left + int(rect.width * .006),
              rect.top + int(rect.height * .173),
              int(rect.width * .950), int(rect.height * .625))
    return pa.screenshot(region=region)


def seleccionar_cliente_visual(bot, dialogo, *, rfc):
    """Verifica la misma única fila al principio, al final y antes de Aceptar.

    No pulsa Aceptar ni timbra; el flujo superior conserva esa decisión.
    """
    from . import polaris
    from .ocr_ticket import find_tesseract
    pa = getattr(polaris, 'pyautogui', None)
    motor = find_tesseract()
    if not pa or not motor:
        raise CoincidenciaFiscalError(
            'Polaris VCL no permite copiar filas y falta Tesseract OCR local. '
            'Instala Tesseract o revisa manualmente; no se timbró.')
    try:
        bot._click_rel(dialogo, bot.BUSCAR['primera_fila'])
        pa.hotkey('ctrl', 'home')
        time.sleep(.18)
        inicial = _lectura_visual(_capturar_grid(bot, dialogo, pa), rfc, motor)
        pa.hotkey('ctrl', 'end')
        time.sleep(.18)
        final = _lectura_visual(_capturar_grid(bot, dialogo, pa), rfc, motor)
        if final['numero'] != inicial['numero'] or final['rfc'] != inicial['rfc']:
            raise CoincidenciaFiscalError(
                'Polaris cambió la fila al final: RFC duplicado o listado virtual.')
        bot._click_rel(dialogo, bot.BUSCAR['primera_fila'])
        pa.hotkey('ctrl', 'home')
        time.sleep(.18)
        comprobacion = _lectura_visual(_capturar_grid(bot, dialogo, pa), rfc, motor)
        if (comprobacion['numero'] != inicial['numero']
                or comprobacion['rfc'] != inicial['rfc']):
            raise CoincidenciaFiscalError('Polaris cambió la selección antes de Aceptar.')
        return inicial
    except CoincidenciaFiscalError:
        raise
    except Exception as exc:
        raise CoincidenciaFiscalError(
            'La lectura visual de Polaris falló; se requiere revisión sin timbrar.') from exc
