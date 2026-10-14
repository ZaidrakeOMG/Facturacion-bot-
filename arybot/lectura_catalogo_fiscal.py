"""Lectura completa del catálogo VCL. Selección únicamente por RFC y razón social.

La calle puede existir como columna informativa; nunca es obligatoria ni se
utiliza para decidir el cliente. Ante una tabla parcial o ambigua no se acepta.
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


def _identidad(valores):
    matches = []
    for i, valor in enumerate(valores):
        if RFC_RE.fullmatch(valor.upper().strip()) and i >= 2:
            numero, razon = valores[i - 2].strip(), valores[i - 1].strip()
            if re.fullmatch(r'[0-9]{1,12}', numero) and razon:
                matches.append({'numero': numero, 'razon_social': razon,
                                'rfc': valor.upper().strip(),
                                'calle': valores[i + 1].strip() if i + 1 < len(valores) else ''})
    return matches[0] if len(matches) == 1 else None


def _columnas(fila):
    try:
        controles = fila.children()
    except Exception:
        controles = []
    # No deduplicar valores: cambiaría las posiciones de las columnas.
    valores = [_texto(c) for c in controles]
    item = _identidad(valores)
    if item:
        return item
    packed = _texto(fila)
    return _identidad([v.strip() for v in packed.split('\t')])


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
            if grid is not None and int(grid.CurrentRowCount) > 0:
                totales.add(int(grid.CurrentRowCount))
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
            if control.element_info.control_type not in ('DataItem', 'ListItem', 'TreeItem'):
                continue
            item = _columnas(control)
            if item:
                item['control'] = control
                filas.append(item)
        except Exception:
            continue
    if len(filas) != total:
        raise CoincidenciaFiscalError(
            'No fue posible leer TODAS las filas del RFC en Polaris. Se requiere revisión del catálogo antes de facturar.')
    return filas


def _fila_del_portapapeles(texto):
    """Una fila Número | Nombre | RFC; cualquier domicilio posterior es opcional."""
    if not isinstance(texto, str) or len(texto) > 16000:
        return None
    rows = []
    for line in texto.splitlines():
        if not line.strip():
            continue
        fields = ([v.strip() for v in line.split('\t')] if '\t' in line
                  else [v.strip() for v in re.split(r' {2,}', line.strip())])
        item = _identidad(fields)
        if item:
            rows.append(item)
        else:
            # Se admite un encabezado reconocible, nunca se descarta otra fila ilegible.
            header = re.sub(r'[^A-Z]', '', line.upper())
            if not ('NUMERO' in header and 'NOMBRE' in header and 'RFC' in header):
                return None
    return rows[0] if len(rows) == 1 else None


def _seleccionar_por_copia_compat(bot, dialogo, *, rfc, razon_social, calle=None):
    from . import polaris as p
    pa, clip = getattr(p, 'pyautogui', None), getattr(p, 'win32clipboard', None)
    if not pa or not clip:
        raise CoincidenciaFiscalError(
            'Polaris no expone filas accesibles ni lectura verificada por teclado. No se seleccionó ningún cliente.')
    fmt = clip.CF_UNICODETEXT
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
                'El catálogo VCL no permite copiar una fila completa con Número, Nombre y RFC; no se eligió cliente.')
        return item

    def token(fila):
        # Calle sólo distingue filas durante el recorrido; no filtra coincidencias.
        return tuple(fila.get(k, '') for k in ('numero', 'razon_social', 'rfc', 'calle'))

    try:
        bot._click_rel(dialogo, bot.BUSCAR['primera_fila'])
        pa.hotkey('ctrl', 'home')
        actual = copiar()
        filas, vistos = [actual], {token(actual)}
        for _ in range(199):
            pa.press('down')
            siguiente = copiar()
            if token(siguiente) == token(actual):
                pa.hotkey('ctrl', 'end')
                if token(copiar()) != token(actual):
                    raise CoincidenciaFiscalError('La navegación de Polaris no confirmó el final del catálogo. No se eligió cliente.')
                break
            if token(siguiente) in vistos:
                raise CoincidenciaFiscalError('La navegación del catálogo se repitió; no se puede verificar la totalidad de las coincidencias.')
            vistos.add(token(siguiente))
            filas.append(siguiente)
            actual = siguiente
        else:
            raise CoincidenciaFiscalError('El catálogo tiene más de 200 registros accesibles. Se requiere revisión; no se selecciona ninguno.')
        correcta = verificar_unico(filas, rfc=rfc, razon_social=razon_social)
        indice = next(i for i, row in enumerate(filas) if token(row) == token(correcta))
        bot._click_rel(dialogo, bot.BUSCAR['primera_fila'])
        pa.hotkey('ctrl', 'home')
        for _ in range(indice):
            pa.press('down')
        seleccionada = copiar()
        if token(seleccionada) != token(correcta):
            raise CoincidenciaFiscalError('Polaris cambió de fila después de seleccionarla. No se pulsó Aceptar.')
        verificar_unico([seleccionada], rfc=rfc, razon_social=razon_social)
        return correcta
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
            pass


def seleccionar_cliente_exacto(dialogo, *, rfc, razon_social, calle=None, bot=None):
    try:
        filas = _leer_filas_completas(dialogo)
    except CoincidenciaFiscalError as exc:
        if bot is not None and any(text in str(exc) for text in
                                  ('total verificable', 'accesibilidad de Windows', 'leer TODAS las filas')):
            return _seleccionar_por_copia_compat(bot, dialogo, rfc=rfc, razon_social=razon_social)
        raise
    elegida = verificar_unico(filas, rfc=rfc, razon_social=razon_social)
    try:
        elegida['control'].click_input()
        seleccionado = bool(elegida['control'].iface_selection_item.CurrentIsSelected)
    except Exception as exc:
        if bot is not None:
            return _seleccionar_por_copia_compat(bot, dialogo, rfc=rfc, razon_social=razon_social)
        raise CoincidenciaFiscalError('Polaris no confirmó la selección de la fila correcta; no se pulsó Aceptar.') from exc
    if not seleccionado:
        raise CoincidenciaFiscalError('La selección no quedó confirmada en Polaris. No se pulsó Aceptar.')
    return elegida
