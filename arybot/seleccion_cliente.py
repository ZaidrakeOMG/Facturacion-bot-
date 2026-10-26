"""Flujo de seleccion Polaris v3.1 (10/oct/2026), restaurado selectivamente.

Se sigue obteniendo el RFC de la constancia PDF en el API actual. La tabla
VCL no expone GridPattern ni responde a Ctrl+C en la VM: no exigir OCR para
hacer la seleccion del cliente con los mismos clics de la version funcional.

La seleccion directa del primer resultado no prueba que el RFC sea unico.
Por eso se intenta verificar el RFC cargado y NO se autoriza timbrado real
sin una comprobacion independiente del cliente (guardia en polaris.py).
"""
from __future__ import annotations
import re
import time


def _otras_lecturas_rfc(hwnd):
    """Fuentes pasivas del mismo control VCL; nunca interactúa con el formulario.

    UI Automation y GetWindowText complementan WM_GETTEXT cuando DevExpress
    deja el editor interno en modo sólo lectura. Valores ilegibles se omiten.
    """
    candidatos = []
    try:
        from . import polaris
        value = polaris.win32gui.GetWindowText(hwnd)
        if isinstance(value, str) and value:
            candidatos.append(value)
    except Exception:
        pass
    try:
        from pywinauto import Desktop
        elem = Desktop(backend='uia').window(handle=hwnd)
        for nombre in ('iface_value', 'iface_legacy_iaccessible'):
            try:
                pattern = getattr(elem, nombre, None)
                value = getattr(pattern, 'CurrentValue', None) if pattern is not None else None
                if isinstance(value, str) and value:
                    candidatos.append(value)
            except Exception:
                continue
    except Exception:
        pass
    return candidatos


def _rfc_legible(captura, controles, esperado):
    """Compara exclusivamente RFCs del editor del receptor con el del PDF.

    Tolera máscara final de guiones bajos. Cualquier RFC distinto detectado
    se trata como discrepancia: no se pulsa Aceptar del timbrado.
    """
    from .coincidencia_fiscal import CoincidenciaFiscalError
    from .alta_texto import normalizar_campo
    resultados = set()
    for hwnd in dict.fromkeys(controles):
        candidatos = []
        try:
            candidatos.append(captura.texto.leer(hwnd))
        except Exception:
            pass
        # El valor de VCL puede leerse con otro proveedor aunque WM_GETTEXT
        # devuelva vacío. El HWND ya fue limitado a la zona de RFC receptor.
        candidatos.extend(_otras_lecturas_rfc(hwnd))
        for candidato in candidatos:
            valor = normalizar_campo('rfc', candidato)
            if re.fullmatch(r'[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}', valor):
                resultados.add(valor)
    if any(valor != esperado for valor in resultados):
        raise CoincidenciaFiscalError(
            'El RFC observado en el receptor difiere del PDF; no se autorizó timbrado.')
    return esperado in resultados


def _controles_rfc_visibles(bot, formulario):
    """Descendientes Edit en las coordenadas del campo RFC, sin enfocar ni escribir."""
    from .factura_captura import CapturaFactura
    from . import polaris
    w = polaris.win32gui
    region = bot._rect(formulario)
    x = region.left + int(region.width * CapturaFactura.RECEPTOR['rfc'][0])
    y = region.top + int(region.height * CapturaFactura.RECEPTOR['rfc'][1])
    encontrados = []

    def visitar(h, _):
        if len(encontrados) >= 24:
            return
        try:
            if (not w.IsWindowVisible(h) or not bot._same_polaris_process(formulario, h)
                    or 'EDIT' not in bot._class_name(h).upper()):
                return
            r = bot._rect(h)
            if r.left <= x < r.right and r.top <= y < r.bottom:
                encontrados.append(h)
        except Exception:
            return

    w.EnumChildWindows(formulario, visitar, None)
    return encontrados


def _comprobar_rfc_cargado(bot, formulario, rfc):
    """Lectura pasiva del RFC del receptor tras Aceptar el cliente.

    Verifica el campo dos veces sin editarlo. Si Polaris VCL no permite lectura,
    retorna False y la cola conserva el formulario abierto para revisión manual.
    """
    from .coincidencia_fiscal import CoincidenciaFiscalError
    from .factura_captura import CapturaFactura
    captura = bot._captura_factura()
    bot._activate(formulario)
    directos = []
    try:
        directos.append(captura.control(
            formulario, CapturaFactura.RECEPTOR['rfc'],
            'rfc_receptor', lectura=True))
    except Exception:
        bot.log('RFC receptor: lectura del editor principal no disponible; revisando editores anidados.')

    confirmado = 0
    for _ in range(3):
        controles = list(directos)
        try:
            controles.extend(_controles_rfc_visibles(bot, formulario))
        except Exception:
            pass
        if _rfc_legible(captura, controles, rfc):
            confirmado += 1
        time.sleep(.12)
    if confirmado >= 2:
        bot.log('RFC del receptor leído y comprobado por coincidencia exacta.')
        return True
    bot.log('RFC del receptor visible pero no verificable desde controles VCL; '
            'se conservará el formulario para revisión en lugar de cerrarlo.')
    return False


def seleccionar_cliente(self, inv, rfc, razon_social='', calle=''):
    """Recupera el flujo v3.1, sin bloqueo VCL/clipboard/Tesseract."""
    from . import polaris as p
    from .cliente_facturacion import exigir_cliente_facturable
    from .coincidencia_fiscal import CoincidenciaFiscalError
    rfc = str(rfc or '').strip().upper().replace(' ', '')
    if not re.fullmatch(r'[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}', rfc):
        raise CoincidenciaFiscalError('El RFC extraído de la constancia no tiene formato válido.')
    self.log('RFC: restaurado flujo de selección v3.1. '
             'No se necesita Ctrl+C ni Tesseract para pulsar Aceptar cliente.')
    self._click_rel(inv, self.FACTURA['buscar_cliente'])
    dlg = self._wait_text_dialog('Seleccione el cliente deseado', 10)
    if not dlg:
        raise p.PolarisError('No apareció la búsqueda de clientes.')
    if not self._same_polaris_process(inv, dlg):
        raise p.PolarisError('El buscador de clientes pertenece a otro proceso.')
    self._activate(dlg)

    r = self._rect(dlg)
    hx = r.left + int(r.width * self.BUSCAR['columna_rfc'][0])
    hy = r.top + int(r.height * self.BUSCAR['columna_rfc'][1])
    p.pyautogui.moveTo(hx, hy, duration=.25)
    p.pyautogui.click(hx, hy)
    time.sleep(.55)
    self._captura_factura().escribir_rel(dlg, self.BUSCAR['campo_busqueda'], 'rfc', rfc)
    self._click_rel(dlg, self.BUSCAR['buscar'], wait=.20)
    time.sleep(1.2)
    exigir_cliente_facturable(self, dlg)

    # Clic/Aceptar directos del codigo v3.1, en lugar de leer/copy el grid VCL.
    # Este gesto no acredita unicidad: no se puede usar como unica garantia
    # para timbrado automatico si no se verifica el RFC post-seleccion.
    self._click_rel(dlg, self.BUSCAR['primera_fila'])
    exigir_cliente_facturable(self, dlg)
    self._click_rel(dlg, self.BUSCAR['aceptar'])
    time.sleep(1.0)
    exigir_cliente_facturable(self, inv)

    inv = self._invoice_window() or inv
    r = self._rect(inv)
    if r.width < 650 or r.height < 450:
        self._screenshot_error('factura_post_cliente')
        raise p.PolarisError('No se recuperó la ventana de Facturación tras Aceptar cliente.')
    self._activate(inv)
    self._pasos_factura['cliente_rfc_confirmado'] = _comprobar_rfc_cargado(self, inv, rfc)
    self._pasos_factura['cliente'] = True
    self.diag.evento('FACTURACION: cliente', 'OK',
                     'Flujo v3.1 restaurado; RFC postselección revisado',
                     {'rfc_capturado': True,
                      'rfc_receptor_confirmado': self._pasos_factura['cliente_rfc_confirmado']})
    self.log('Polaris aceptó cliente. Continuando forma de pago, fecha y ticket.')
    return inv


def instalar_seleccion_cliente():
    """Idempotente: adapta solo la selección, no el arranque ni el timbrado."""
    from .polaris import PolarisBot
    PolarisBot._select_client = seleccionar_cliente
