"""Regresiones del lote de 3 clientes: SIN Windows real, SAT ni timbrado."""
from types import SimpleNamespace
from datetime import date
from unittest.mock import Mock
import pytest

from arybot import polaris
from arybot import compat_vm as vm
from arybot import factura_final
from arybot.cola_local import ColaLocal
from arybot.cola_modelo import ColaError, SolicitudDuplicada


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def simulated_time(monkeypatch, module):
    clock = FakeClock()
    monkeypatch.setattr(module.time, 'monotonic', clock.now)
    monkeypatch.setattr(module.time, 'sleep', clock.sleep)
    return clock


def vm_bot(monkeypatch, rects):
    b = object.__new__(vm.PolarisBotVM)
    b.log = Mock()
    b._activate = Mock()
    b._screenshot_error = Mock()
    b._menu_path = Mock()
    b._window_exists_visible = Mock(return_value=True)
    b._invoice_window = Mock(return_value=900)
    b._factura_controles_listos = Mock(return_value=True)
    b._rect = Mock(side_effect=(polaris.Rect(*x) for x in rects))
    monkeypatch.setattr(
        polaris, 'win32gui',
        SimpleNamespace(IsWindowEnabled=lambda h: True), raising=False)
    return b


def test_facturacion_mdi_815x284_espera_y_termina_lista(monkeypatch):
    clock = simulated_time(monkeypatch, vm)
    windows = [(0, 0, 815, 284)] * 3 + [(0, 0, 833, 638)] * 40
    b = vm_bot(monkeypatch, windows)
    assert b._open_cash_invoice(1) == 900
    assert clock.now() >= 1
    b._activate.assert_called_once_with(900)
    b._menu_path.assert_not_called()
    b._screenshot_error.assert_not_called()


def test_polaris_original_no_aborta_mdi_815x284(monkeypatch):
    # Es el método de la aplicación original (error exacto del video).
    clock = simulated_time(monkeypatch, polaris)
    windows = [(0, 0, 815, 284)] * 3 + [(0, 0, 833, 638)] * 40
    b = object.__new__(polaris.PolarisBot)
    b.log = Mock()
    b._activate = Mock()
    b._screenshot_error = Mock()
    b._window_exists_visible = Mock(return_value=True)
    b._invoice_window = Mock(return_value=900)
    b._factura_controles_listos = Mock(return_value=True)
    b._rect = Mock(side_effect=(polaris.Rect(*x) for x in windows))
    monkeypatch.setattr(
        polaris, 'win32gui',
        SimpleNamespace(IsWindowEnabled=lambda h: True), raising=False)
    assert b._open_cash_invoice(1) == 900
    assert clock.now() >= 1
    b._activate.assert_called_once_with(900)
    b._screenshot_error.assert_not_called()


def test_facturacion_que_nunca_carga_no_recibe_clics(monkeypatch):
    simulated_time(monkeypatch, vm)
    b = vm_bot(monkeypatch, [(0, 0, 815, 284)] * 200)
    with pytest.raises(vm.PolarisError, match='No se enviaron datos'):
        b._open_cash_invoice(1)
    b._activate.assert_not_called()
    b._screenshot_error.assert_called_once()



def test_formulario_blanco_con_tamano_final_se_espera_en_vm(monkeypatch):
    clock=simulated_time(monkeypatch, vm)
    b=vm_bot(monkeypatch,[(0,0,833,638)]*60)
    b._factura_controles_listos=Mock(side_effect=[False]*8+[True]*30)
    assert b._open_cash_invoice(1)==900
    assert clock.now()>=2.7
    b._activate.assert_called_once_with(900)
    b._screenshot_error.assert_not_called()


def test_formulario_blanco_con_tamano_final_se_espera_en_original(monkeypatch):
    clock=simulated_time(monkeypatch, polaris)
    b=object.__new__(polaris.PolarisBot)
    b.log=Mock()
    b._activate=Mock()
    b._screenshot_error=Mock()
    b._window_exists_visible=Mock(return_value=True)
    b._invoice_window=Mock(return_value=900)
    b._rect=Mock(return_value=polaris.Rect(0,0,833,638))
    b._factura_controles_listos=Mock(side_effect=[False]*6+[True]*40)
    monkeypatch.setattr(
        polaris,'win32gui',SimpleNamespace(IsWindowEnabled=lambda h:True),raising=False)
    assert b._open_cash_invoice(1)==900
    assert clock.now()>=2.5
    b._activate.assert_called_once_with(900)


def test_formulario_vacio_nunca_se_considera_listo(monkeypatch):
    simulated_time(monkeypatch, vm)
    b=vm_bot(monkeypatch, [(0,0,833,638)]*300)
    b._factura_controles_listos=Mock(return_value=False)
    with pytest.raises(vm.PolarisError, match='controles'):
        b._open_cash_invoice(1)
    b._activate.assert_not_called()
    b._screenshot_error.assert_called_once()


def test_verificador_exige_controles_no_solo_titulo(monkeypatch):
    b=object.__new__(polaris.PolarisBot)
    names=['TcxPageControl','TcxCustomInnerTextEdit']
    def children(h, cb, param):
        for i in range(len(names)):
            cb(i+1, param)
    monkeypatch.setattr(
        polaris,'win32gui',
        SimpleNamespace(IsWindowVisible=lambda h:True,EnumChildWindows=children),
        raising=False)
    b._class_name=lambda h: names[h-1]
    assert not b._factura_controles_listos(900)
    names.extend(['TcxGrid','TcxButton'])
    assert b._factura_controles_listos(900)


def final_bot(monkeypatch, current, progress=None):
    clock = simulated_time(monkeypatch, polaris)
    progress = progress or (lambda t: [])
    instances = []

    class FakeFinal:
        def __init__(self, bot):
            instances.append(self)

        def _esperar_envio(self, main, inv):
            return 300

        def _dialogos(self, main, allowed):
            return progress(clock.now())

    monkeypatch.setattr(factura_final, 'PantallaFinalFactura', FakeFinal)
    b = object.__new__(polaris.PolarisBot)
    b.pcfg = {'espera_post_envio': 15, 'espera_envio': 45}
    b.log = Mock()
    b.diag = SimpleNamespace(evento=Mock())
    b._activate = Mock()
    b._assert_point_inside_window = Mock()
    b._click_rel = Mock()
    b._copy_current_fresh = Mock(side_effect=current)
    b._window_exists_visible = Mock(return_value=False)
    b._captura_factura = Mock()
    b._screenshot_error = Mock()
    b.etapa_alerta = ''
    return b, clock


def test_correo_precargado_con_acento_no_se_reemplaza(monkeypatch):
    b, clock = final_bot(monkeypatch, ['aux.administración@ejemplo.com'])
    assert b._send_dialog_estable(1, 2, 'portal@example.com') == 'ENVIO_SOLICITADO'
    b._captura_factura.assert_not_called()
    b._copy_current_fresh.assert_called_once()
    assert b._click_rel.call_count == 3  # radio, foco correo, Aceptar envío
    assert clock.now() >= 15
    assert b.etapa_alerta == 'ENVIO_CORREO: comprobar cierre y actividad'


def test_correo_vacio_usa_el_del_portal_una_sola_vez(monkeypatch):
    email = 'portal@example.com'
    b, clock = final_bot(monkeypatch, ['', email])
    writer = Mock()
    b._captura_factura.return_value = writer
    assert b._send_dialog_estable(1, 2, email) == 'ENVIO_SOLICITADO'
    writer.escribir_rel.assert_called_once()
    assert b._click_rel.call_count == 3


def test_progreso_de_correo_reinicia_espera_de_seguridad(monkeypatch):
    def progress(t):
        return [(901, 'PROGRESO_CORREO')] if t < 12 else []
    b, clock = final_bot(monkeypatch, ['cliente@example.com'], progress)
    assert b._send_dialog_estable(1, 2, 'portal@example.com') == 'ENVIO_SOLICITADO'
    assert clock.now() >= 27  # progreso hasta el segundo 12, luego 15 silenciosos
    assert b._click_rel.call_count == 3


def test_aviso_despues_del_timbrado_no_es_exito(monkeypatch):
    b, clock = final_bot(
        monkeypatch, ['cliente@example.com'], lambda _: [(901, 'AVISO')])
    with pytest.raises(polaris.PolarisError, match='aviso'):
        b._send_dialog_estable(1, 2, 'portal@example.com')
    assert b._click_rel.call_count == 3  # no hay segundo Aceptar


def test_bloqueo_persiste_si_el_envio_quedo_incierto(tmp_path):
    store = ColaLocal(tmp_path)
    clave = 'FACTURA:ARYV:00001'
    j1, _ = store.enqueue(
        'FACTURA', {'ticket': '00001'}, source='Formulario', safe=False,
        correo='cliente@example.com', clave=clave)
    j2, _ = store.enqueue(
        'FACTURA', {'ticket': '00002'}, source='Formulario', safe=False,
        correo='otro@example.com', clave='FACTURA:ARYV:00002')
    store.resume()
    assert store.claim(production_allowed=True)['id'] == j1['id']
    store.finish(j1['id'], 'REVISION_REQUERIDA', hold=True,
                 reason='Correo/CFDI pendiente')
    assert store.status()['pausada']
    assert store.status()['bloqueo'] == j1['id']
    assert store.claim(production_allowed=True) is None
    with pytest.raises(ColaError):
        store.resume()
    with pytest.raises(SolicitudDuplicada):
        store.enqueue(
            'FACTURA', {'ticket': '00001'}, source='Formulario', safe=False,
            correo='cliente@example.com', clave=clave)
    assert store.recover() == []
    assert store.status()['pausada']
    store.release(j1['id'])
    assert not store.status()['pausada']
    assert store.claim(production_allowed=True)['id'] == j2['id']


def test_cola_con_error_antes_de_timbrar_continua(tmp_path):
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    from arybot.parser import Solicitud
    cfg = {'app': {'modo_prueba': False, 'correo': 'bot@example.com'},
           'gmail': {}, 'ocr': {}, 'polaris': {}, 'cola': dict(DEFAULTS)}
    bot = Mock()
    bot.invoice.side_effect = [RuntimeError('Formulario transitorio'), 'ENVIO_SOLICITADO']
    bot.diag = None
    bot.etapa_alerta = 'FACTURACIÓN: abrir menú real'
    q = ColaOperaciones(tmp_path, cfg, bot, Mock(), avisos=Mock())
    def sol(folio):
        return Solicitud(estacion='ARY V', rfc='AAA010101AAA', ticket=folio,
                         fecha_ticket=date.today().strftime('%d/%m/%Y'),
                         forma_pago='EFECTIVO',
                         correo_destino='cliente@example.com',
                         remitente='cliente@example.com')
    j1, _ = q.encolar_factura(sol('123'), safe=False)
    j2, _ = q.encolar_factura(sol('456'), safe=False)
    q.iniciar_cola()
    assert q.ejecutar_una()
    assert q.store.get(j1['id'])['estado'] == 'REVISION_REQUERIDA'
    assert not q.store.status()['pausada']
    assert q.ejecutar_una()
    assert q.store.get(j2['id'])['estado'] == 'ENVIO_SOLICITADO'
    assert bot.cerrar_polaris_tras_solicitud.call_count == 2


def test_cola_no_cierra_polaris_con_cfdi_incierto(tmp_path):
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    from arybot.parser import Solicitud
    cfg = {'app': {'modo_prueba': False, 'correo': 'bot@example.com'},
           'gmail': {}, 'ocr': {}, 'polaris': {}, 'cola': dict(DEFAULTS)}
    bot = Mock()
    bot.invoice.side_effect = RuntimeError('El envío no concluyó')
    bot.diag = None
    bot.etapa_alerta = 'ENVIO_CORREO: comprobar cierre y actividad'
    q = ColaOperaciones(tmp_path, cfg, bot, Mock(), avisos=Mock())
    sol = Solicitud(estacion='ARY V', rfc='AAA010101AAA', ticket='123',
                   fecha_ticket=date.today().strftime('%d/%m/%Y'),
                   forma_pago='EFECTIVO', correo_destino='cliente@example.com',
                   remitente='cliente@example.com')
    j, _ = q.encolar_factura(sol, safe=False)
    q.iniciar_cola()
    assert q.ejecutar_una()
    assert q.store.get(j['id'])['estado'] == 'REVISION_REQUERIDA'
    assert q.store.status()['pausada']
    bot.cerrar_polaris_tras_solicitud.assert_not_called()
    assert not q.ejecutar_una()


def test_tres_solicitudes_se_procesan_una_por_una(tmp_path):
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    from arybot.parser import Solicitud
    cfg = {'app': {'modo_prueba': False, 'correo': 'bot@example.com'},
           'gmail': {}, 'ocr': {}, 'polaris': {}, 'cola': dict(DEFAULTS)}
    bot = Mock()
    bot.invoice.return_value = 'ENVIO_SOLICITADO'
    bot.diag = None
    bot.etapa_alerta = 'ENVIO_CORREO: comprobar cierre y actividad'
    q = ColaOperaciones(tmp_path, cfg, bot, Mock(), avisos=Mock())
    jobs = []
    for ticket in ('000111', '000222', '000333'):
        sol = Solicitud(estacion='ARY V', rfc='AAA010101AAA', ticket=ticket,
                       fecha_ticket=date.today().strftime('%d/%m/%Y'),
                       forma_pago='EFECTIVO',
                       correo_destino='cliente@example.com',
                       remitente='cliente@example.com')
        job, _ = q.encolar_factura(sol, safe=False)
        jobs.append(job)
    q.iniciar_cola()
    for job in jobs:
        assert q.ejecutar_una()
        assert q.store.get(job['id'])['estado'] == 'ENVIO_SOLICITADO'
    assert bot.invoice.call_count == 3
    assert bot.cerrar_polaris_tras_solicitud.call_count == 3
    assert not q.store.status()['pausada']
    assert not q.ejecutar_una()
