"""Selector de facturación por RFC único para el arranque integrado.

Adaptador explícito para el núcleo Polaris legado: se instala antes de crear App.
No altera login, estación, captura del ticket, timbrado ni modo seguro.
"""
from __future__ import annotations
import time


def seleccionar_cliente(self, inv, rfc, razon_social='', calle=''):
    from . import polaris as p
    from .cliente_facturacion import exigir_cliente_facturable
    from .coincidencia_fiscal import CoincidenciaFiscalError
    from .lectura_catalogo_fiscal import seleccionar_cliente_exacto
    rfc = (rfc or '').strip().upper().replace(' ', '')
    if not rfc:
        raise CoincidenciaFiscalError('Debe existir RFC de la constancia PDF. No se timbró.')
    self.log('Buscando por RFC único; no se compara nombre ni domicilio. RFC duplicado queda en revisión.')
    self._click_rel(inv, self.FACTURA['buscar_cliente'])
    dlg = self._wait_text_dialog('Seleccione el cliente deseado', 10)
    if not dlg:
        raise p.PolarisError('No apareció la búsqueda de clientes.')
    if not self._same_polaris_process(inv, dlg):
        raise p.PolarisError('El buscador pertenece a otro proceso. No se capturan datos.')
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
    seleccionar_cliente_exacto(dlg, rfc=rfc, razon_social=razon_social, bot=self)
    exigir_cliente_facturable(self, dlg)
    self._click_rel(dlg, self.BUSCAR['aceptar'])
    time.sleep(1.0)
    exigir_cliente_facturable(self, inv)
    inv = self._invoice_window() or inv
    r = self._rect(inv)
    if r.width < 650 or r.height < 450:
        self._screenshot_error('factura_post_cliente')
        raise p.PolarisError('No recuperé la ventana de Facturación después de seleccionar cliente.')
    self._activate(inv)
    self.log('Cliente único confirmado por RFC. Polaris conserva los datos fiscales del cliente registrado.')
    self._pasos_factura['cliente'] = True
    self.diag.evento('FACTURACION: cliente', 'OK', 'Cliente seleccionado por RFC único',
                     {'rfc_capturado': True})
    return inv


def instalar_seleccion_cliente():
    """Idempotente; sólo sustituye la política de selección del núcleo legado."""
    from .polaris import PolarisBot
    PolarisBot._select_client = seleccionar_cliente
