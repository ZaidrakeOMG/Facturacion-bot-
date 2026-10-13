"""Lector de filas del buscador VCL/DevExpress mediante UI Automation.

Nunca usa OCR ni coordenadas de la primera fila. Sólo selecciona una fila si
UI Automation puede acreditar que leyó todas las filas del resultado de RFC.
Si Polaris no expone GridPattern, la solicitud queda para revisión humana.
"""
from __future__ import annotations
import re
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


def seleccionar_cliente_exacto(dialogo, *, rfc, razon_social, calle):
    filas = _leer_filas_completas(dialogo)
    elegida = verificar_unico(filas, rfc=rfc, razon_social=razon_social, calle=calle)
    control = elegida['control']
    try:
        control.click_input()
        # La fila seleccionada debe responder expresamente que quedó seleccionada.
        seleccionado = bool(control.iface_selection_item.CurrentIsSelected)
    except Exception as exc:
        raise CoincidenciaFiscalError(
            'Polaris no confirmó la selección de la fila correcta; no se pulsó Aceptar.') from exc
    if not seleccionado:
        raise CoincidenciaFiscalError(
            'La selección no quedó confirmada en Polaris. No se pulsó Aceptar.')
    return elegida
