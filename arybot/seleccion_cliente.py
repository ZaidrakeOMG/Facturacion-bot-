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


def _comprobar_rfc_cargado(bot, formulario, rfc):
    """Lectura pasiva del RFC en Facturacion DESPUES de Aceptar el cliente.

    No escribe ni enfoca el RFC. Los editores VCL son anidados y pueden no
    exponer texto; devolver False deja la solicitud en modo de prueba.
    Un RFC efectivamente diferente es un error y cancela siempre.
    """
    from .coincidencia_fiscal import CoincidenciaFiscalError
    from .factura_captura import CapturaFactura, coincide
    from . import polaris
    captura = bot._captura_factura()
    bot._activate(formulario)
    try:
        h = captura.control(formulario, CapturaFactura.RECEPTOR['rfc'],
                            'rfc_receptor', lectura=True)
    except Exception:
        bot.log('RFC receptor: Polaris no expone el editor para lectura independiente.')
        return False

    confirmado = 0
    for _ in range(3):
        try:
            observado = str(captura.texto.leer(h) or '').strip()
        except Exception:
            observado = ''
        if observado and re.fullmatch(r'[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}', observado.upper()):
            if not coincide('rfc', observado, rfc):
                raise CoincidenciaFiscalError(
                    'El RFC del receptor cargado por Polaris no coincide con el PDF. '
                    'No se facturó; se requiere revisión del cliente.')
            confirmado += 1
        time.sleep(.12)
    if confirmado >= 2:
        bot.log('RFC del receptor verificado después de Aceptar el cliente.')
        return True
    bot.log('RFC del receptor sin lectura independiente concluyente; '
            'se admite únicamente la prueba, no el timbrado automático.')
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
