"""Lector de filas del buscador VCL/DevExpress mediante UI Automation.

Nunca usa OCR ni coordenadas de la primera fila. Sólo selecciona una fila si
UI Automation puede acreditar que leyó todas las filas del resultado de RFC.
Si Polaris no expone GridPattern, la solicitud queda para revisión humana.
"""
from __future__ import annotations
import re
import time
from .coincidencia_fiscal import CoincidenciaFiscalError, verificar_unico

RFC_RE = re.compile(r'^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$')


def _texto(control):
    try:
        return str(control.window_text() or control.element_info.name or '').strip()
    except Exception:
        return ''


def _columnas(fila):
    try:
        controles = fila.children()
    except Exception:
        controles = []
    valores = []
    for control in controles:
        valor = _texto(control)
        if valor and valor not in valores:
            valores.append(valor)
    # Algunos controles VCL sólo exponen una línea delimitada por tabuladores.
    if len(valores) < 4:
        packed = _texto(fila)
        separadas = [part.strip() for part in packed.split('\t') if part.strip()]
        if len(separadas) >= 4:
            valores = separadas
    if len(valores) < 4:
        return None
    for i, valor in enumerate(valores):
        if RFC_RE.fullmatch(valor.upper().strip()):
            if i < 2 or i + 1 >= len(valores):
                return None
            return {'numero': valores[i - 2], 'razon_social': valores[i - 1],
                    'rfc': valor, 'calle': valores[i + 1]}
    return None


def _leer_filas_completas(dialogo):
    try:
        from pywinauto import Desktop
        ventana = Desktop(backend='uia').window(handle=dialogo)
        controles = ventana.descendants()
    except Exception as exc:
        raise CoincidenciaFiscalError(
            'No se pudo consultar la tabla de clientes con accesibilidad de Windows; no se seleccionó ninguno.') from exc
    totales = set()
    for control in controles:
        try:
            grid = getattr(control, 'iface_grid', None)
            if grid is not None:
                numero = int(grid.CurrentRowCount)
                if numero > 0:
                    totales.add(numero)
        except Exception:
            continue
    if len(totales) != 1:
        raise CoincidenciaFiscalError(
            'Polaris no expuso el total verificable de filas del RFC. No se puede elegir un cliente con seguridad.')
    total = totales.pop()
    if total > 200:
        raise CoincidenciaFiscalError('La búsqueda tiene demasiados clientes; requiere revisión.')
    filas = []
    for control in controles:
        try:
            tipo = control.element_info.control_type
            if tipo not in ('DataItem', 'ListItem', 'TreeItem'):
                continue
            item = _columnas(control)
            if item:
                item['control'] = control
                filas.append(item)
        except Exception:
            continue
    # Si hay paginación, virtualización o una celda ilegible, NO adivinar.
    if len(filas) != total:
        raise CoincidenciaFiscalError(
            'No fue posible leer TODAS las filas del RFC en Polaris. '
            'Se requiere revisión del catálogo antes de facturar.')
    return filas



def _fila_del_portapapeles(texto):
    """Admite solo una fila tabulada inequívoca: Número | Nombre | RFC | Calle.

    No se interpreta el portapapeles como prueba de una coincidencia si está
    incompleto, hay encabezados ajenos o se mezclaron varias filas.
    """
    if not isinstance(texto, str) or len(texto) > 16000:
        return None
    rows = []
    for line in texto.splitlines():
        line = line.strip()
        if not line:
            continue
        # DevExpress suele exportar con tabs, pero admite texto por columnas.
        fields = ([v.strip() for v in line.split('\t')]
                  if '\t' in line else
                  [v.strip() for v in re.split(r' {2,}', line)])
        fields = [v for v in fields if v]
        if len(fields) < 4:
            continue
        for idx, val in enumerate(fields):
            if not RFC_RE.fullmatch(val.upper()):
                continue
            if idx < 2 or idx + 1 >= len(fields):
                continue
            numero, razon, calle = fields[idx - 2], fields[idx - 1], fields[idx + 1]
            if not re.fullmatch(r'[0-9]{1,12}', numero) or not razon or not calle:
                continue
            rows.append({'numero': numero, 'razon_social': razon,
                         'rfc': val.upper(), 'calle': calle})
    return rows[0] if len(rows) == 1 else None


def _seleccionar_por_copia_compat(bot, dialogo, *, rfc, razon_social, calle):
    """Mismo click al grid que el bot estable, verificando cada fila con Ctrl+C.

    DevExpress VCL a veces NO expone GridPattern en UI Automation. En ese caso,
    probamos recorrer la tabla con Down y copiar la fila activa. Nunca acepta
    simplemente la primera fila ni pulsa Aceptar. Si no hay copia verificable
    o cambia el foco, se detiene antes de timbrar.
    """
    from . import polaris as p
    pa = getattr(p, 'pyautogui', None)
    clip = getattr(p, 'win32clipboard', None)
    if not pa or not clip:
        raise CoincidenciaFiscalError(
            'Polaris no expone filas accesibles ni lectura verificada por teclado. '
            'No se seleccionó ningún cliente.')
    fmt = clip.CF_UNICODETEXT

    # Recuperamos el texto previo al acabar para no dejar datos fiscales en el
    # portapapeles. Nunca registramos el contenido en disco o logs.
    previous = None
    clip.OpenClipboard()
    try:
        if clip.IsClipboardFormatAvailable(fmt):
            previous = clip.GetClipboardData(fmt)
    finally:
        clip.CloseClipboard()

    def copiar():
        clip.OpenClipboard()
        try:
            clip.EmptyClipboard()
        finally:
            clip.CloseClipboard()
        pa.hotkey('ctrl', 'c')
        time.sleep(.15)
        clip.OpenClipboard()
        try:
            value = clip.GetClipboardData(fmt) if clip.IsClipboardFormatAvailable(fmt) else ''
        finally:
            clip.CloseClipboard()
        item = _fila_del_portapapeles(value)
        if not item:
            raise CoincidenciaFiscalError(
                'El catálogo VCL no permite copiar una fila completa con '
                'Número, Nombre, RFC y Calle; no se eligió cliente.')
        return item

    def token(fila):
        return tuple(fila.get(k, '') for k in ('numero', 'razon_social', 'rfc', 'calle'))

    try:
        # Es EXACTAMENTE el clic de la versión original; se usa sólo para
        # enfocar el grid antes de inspeccionarlo, no para aceptar el cliente.
        bot._click_rel(dialogo, bot.BUSCAR['primera_fila'])
        actual = copiar()
        filas = [actual]
        vistos = {token(actual)}
        for _ in range(199):
            pa.press('down')
            siguiente = copiar()
            if token(siguiente) == token(actual):
                # Prueba adicional: Ctrl+End no debe descubrir otra última fila.
                pa.hotkey('ctrl', 'end')
                ultima = copiar()
                if token(ultima) != token(actual):
                    raise CoincidenciaFiscalError(
                        'La navegación de Polaris no confirmó el final del catálogo. '
                        'No se eligió cliente.')
                break
            if token(siguiente) in vistos:
                raise CoincidenciaFiscalError(
                    'La navegación del catálogo se repitió; no se puede verificar '
                    'la totalidad de las coincidencias.')
            vistos.add(token(siguiente))
            filas.append(siguiente)
            actual = siguiente
        else:
            raise CoincidenciaFiscalError(
                'El catálogo tiene más de 200 registros accesibles. '
                'Se requiere revisión; no se selecciona ninguno.')

        fila_correcta = verificar_unico(
            filas, rfc=rfc, razon_social=razon_social, calle=calle)
        indice = next(i for i, row in enumerate(filas)
                      if token(row) == token(fila_correcta))
        # Restablecer la primera fila con el clic previamente calibrado y
        # navegar SOLO hasta el índice ya comprobado, no mediante píxeles.
        bot._click_rel(dialogo, bot.BUSCAR['primera_fila'])
        for _ in range(indice):
            pa.press('down')
        seleccionada = copiar()
        if token(seleccionada) != token(fila_correcta):
            raise CoincidenciaFiscalError(
                'Polaris cambió de fila después de seleccionarla. '
                'No se pulsó Aceptar.')
        verificar_unico([seleccionada], rfc=rfc, razon_social=razon_social,
                        calle=calle)
        return fila_correcta
    finally:
        try:
            clip.OpenClipboard()
            try:
                clip.EmptyClipboard()
                if previous is not None:
                    clip.SetClipboardText(previous, fmt)
            finally:
                clip.CloseClipboard()
        except Exception:
            # No volver a copiar ni a pulsar Aceptar si Windows deniega
            # acceso al portapapeles de otro proceso.
            pass


def seleccionar_cliente_exacto(dialogo, *, rfc, razon_social, calle, bot=None):
    try:
        filas = _leer_filas_completas(dialogo)
    except CoincidenciaFiscalError as exc:
        # La ausencia de GridPattern NO es evidencia de que no haya clientes:
        # usar el teclado como hacía la versión que funcionaba, pero sin
        # aceptar nada hasta verificar de forma única Nombre/RFC/Calle.
        if bot is not None and (
                'total verificable' in str(exc)
                or 'accesibilidad de Windows' in str(exc)
                or 'leer TODAS las filas' in str(exc)):
            return _seleccionar_por_copia_compat(
                bot, dialogo, rfc=rfc, razon_social=razon_social, calle=calle)
        raise
    elegida = verificar_unico(filas, rfc=rfc, razon_social=razon_social, calle=calle)
    control = elegida['control']
    try:
        control.click_input()
        seleccionado = bool(control.iface_selection_item.CurrentIsSelected)
    except Exception as exc:
        if bot is not None:
            return _seleccionar_por_copia_compat(
                bot, dialogo, rfc=rfc, razon_social=razon_social, calle=calle)
        raise CoincidenciaFiscalError(
            'Polaris no confirmó la selección de la fila correcta; no se pulsó Aceptar.') from exc
    if not seleccionado:
        raise CoincidenciaFiscalError(
            'La selección no quedó confirmada en Polaris. No se pulsó Aceptar.')
    return elegida
