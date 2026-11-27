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
            self.bot = bot

        def _esperar_envio(self, main, inv):
            return 300

        def leer_correo_precargado_sin_tocar(self, dlg):
            return self.bot._correo_pasivo_mock()

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
    b._copy_current_fresh = Mock()
    b._correo_pasivo_mock = Mock(side_effect=current)
    b._window_exists_visible = Mock(return_value=False)
    b._captura_factura = Mock()
    b._screenshot_error = Mock()
    b.etapa_alerta = ''
    return b, clock


def test_correo_precargado_con_acento_no_se_reemplaza(monkeypatch):
    b, clock = final_bot(monkeypatch, ['aux.administración@ejemplo.com'])
    assert b._send_dialog_estable(1, 2, 'portal@example.com') == 'ENVIO_SOLICITADO'
    b._captura_factura.assert_not_called()
    b._copy_current_fresh.assert_not_called()
    b._correo_pasivo_mock.assert_called_once_with()
    assert [c.args[1] for c in b._click_rel.call_args_list] == [
        b.ENVIO['correo_radio'], b.ENVIO['aceptar']]  # jamás clic en correo
    assert clock.now() >= 15
    assert b.etapa_alerta == 'ENVIO_CORREO: comprobar cierre y actividad'


def test_correo_vacio_detiene_envio_sin_tocar_campo_tras_timbrado(monkeypatch):
    email = 'portal@example.com'
    b, clock = final_bot(monkeypatch, [''])
    with pytest.raises(polaris.PolarisError, match='No se confirmó un correo'):
        b._send_dialog_estable(1, 2, email)
    b._captura_factura.assert_not_called()
    b._copy_current_fresh.assert_not_called()
    assert [c.args[1] for c in b._click_rel.call_args_list] == [
        b.ENVIO['correo_radio']]  # no Aceptar ni escritura ciega


def test_progreso_de_correo_reinicia_espera_de_seguridad(monkeypatch):
    def progress(t):
        return [(901, 'PROGRESO_CORREO')] if t < 12 else []
    b, clock = final_bot(monkeypatch, ['cliente@example.com'], progress)
    assert b._send_dialog_estable(1, 2, 'portal@example.com') == 'ENVIO_SOLICITADO'
    assert clock.now() >= 27  # progreso hasta el segundo 12, luego 15 silenciosos
    assert b._click_rel.call_count == 2


def test_aviso_despues_del_timbrado_no_es_exito(monkeypatch):
    b, clock = final_bot(
        monkeypatch, ['cliente@example.com'], lambda _: [(901, 'AVISO')])
    with pytest.raises(polaris.PolarisError, match='aviso'):
        b._send_dialog_estable(1, 2, 'portal@example.com')
    assert b._click_rel.call_count == 2  # un radio y un único Aceptar



def test_lector_pasivo_de_correo_no_usa_clic_teclado_ni_portapapeles(monkeypatch):
    from arybot.factura_final import PantallaFinalFactura
    b = Mock()
    pantalla = PantallaFinalFactura(b)
    pantalla._email_control = Mock(return_value=555)
    pantalla.texto = Mock()
    pantalla.texto.leer.return_value = 'cliente@example.com'
    pantalla._children = Mock(side_effect=AssertionError('No debe buscar otras ventanas'))
    monkeypatch.setattr(polaris, 'win32gui',
                        SimpleNamespace(GetWindowText=lambda h: 'cliente@example.com'), raising=False)
    assert pantalla.leer_correo_precargado_sin_tocar(99) == 'cliente@example.com'
    b._click_rel.assert_not_called()
    b._captura_factura.assert_not_called()


def test_lector_pasivo_rechaza_destinatario_contradictorio(monkeypatch):
    from arybot.factura_final import PantallaFinalFactura, FinalFacturaError
    b = Mock()
    pantalla = PantallaFinalFactura(b)
    pantalla._email_control = Mock(return_value=555)
    pantalla.texto = Mock()
    pantalla.texto.leer.return_value = 'uno@example.com'
    monkeypatch.setattr(polaris, 'win32gui',
                        SimpleNamespace(GetWindowText=lambda h: 'otro@example.com'), raising=False)
    with pytest.raises(FinalFacturaError, match='lecturas distintas'):
        pantalla.leer_correo_precargado_sin_tocar(99)
    b._click_rel.assert_not_called()


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


# Liberación de revisiones históricas: nunca repetir un CFDI, alta ni correo.
def _pendiente_de_revision_en_lote(store, folio, motivo, *, tipo='FACTURA', estado='REVISION_REQUERIDA'):
    row, _ = store.enqueue(tipo, {'ticket': folio}, source='Portal web',
                           correo='cliente@example.com', safe=False,
                           state=estado, reason=motivo)
    return row


def test_liberacion_masiva_cierra_solo_revisiones_no_inciertas(tmp_path):
    store = ColaLocal(tmp_path)
    ok1 = _pendiente_de_revision_en_lote(store, '1', 'Error al abrir ventana.')
    ok2 = _pendiente_de_revision_en_lote(store, '2', 'Cliente sin régimen fiscal.')
    no1 = _pendiente_de_revision_en_lote(store, '3',
                                        'Posible CFDI timbrado o correo pendiente.')
    no2 = _pendiente_de_revision_en_lote(store, '4',
                                        'La aplicación se cerró con una operación o captura pendiente.')
    no3 = _pendiente_de_revision_en_lote(store, '5',
                                        'Posible alta guardada.', tipo='ALTA')
    preparado = _pendiente_de_revision_en_lote(store, '6', 'Captura preparada.',
                                               estado='PREPARADA_FACTURA')
    store.pause('Pausada voluntariamente por el operador')
    prev = store.resumen_liberacion_masiva()
    assert prev['liberables'] == 2
    assert prev['protegidas'] == 3
    assert not prev['ejecutando']
    result = store.liberar_revisiones_masivas()
    assert result == {k: prev[k] for k in ('liberables', 'protegidas', 'total_revision')}
    for j in (ok1, ok2):
        item = store.get(j['id'])
        assert item['estado'] == 'REVISION_CERRADA'
        assert 'cerrada en lote' in item['motivo']
    for j in (no1, no2, no3):
        assert store.get(j['id'])['estado'] == 'REVISION_REQUERIDA'
    assert store.get(preparado['id'])['estado'] == 'PREPARADA_FACTURA'
    assert store.status()['pausada']
    assert store.status()['motivo'] == 'Pausada voluntariamente por el operador'
    assert store.liberar_revisiones_masivas()['liberables'] == 0


def test_liberacion_masiva_respeta_bloqueo_aunque_el_motivo_sea_generico(tmp_path):
    store = ColaLocal(tmp_path)
    bloqueado = _pendiente_de_revision_en_lote(store, 'A', 'Error genérico.')
    libre = _pendiente_de_revision_en_lote(store, 'B', 'Falló búsqueda de RFC.')
    with store.connect(True) as c:
        store._set(c, 'bloqueo', bloqueado['id'])
        store._set(c, 'pausada', '1')
    result = store.liberar_revisiones_masivas()
    assert result['liberables'] == 1
    assert result['protegidas'] == 1
    assert store.get(bloqueado['id'])['estado'] == 'REVISION_REQUERIDA'
    assert store.get(libre['id'])['estado'] == 'REVISION_CERRADA'
    assert store.status()['bloqueo'] == bloqueado['id']
    assert store.status()['pausada']
    with pytest.raises(ColaError):
        store.resume()


def test_liberacion_masiva_no_funciona_durante_polaris(tmp_path):
    store = ColaLocal(tmp_path)
    previa = _pendiente_de_revision_en_lote(store, 'A', 'Fallo antes del folio.')
    nuevo, _ = store.enqueue('FACTURA', {'ticket': 'B'}, source='Portal web',
                              correo='cliente@example.com', safe=False)
    store.resume()
    assert store.claim()['id'] == nuevo['id']
    assert store.resumen_liberacion_masiva()['ejecutando']
    with pytest.raises(ColaError, match='ejecutándose'):
        store.liberar_revisiones_masivas()
    assert store.get(previa['id'])['estado'] == 'REVISION_REQUERIDA'
    assert store.get(nuevo['id'])['estado'] == 'EJECUTANDO'


def test_coordinador_liberacion_masiva_exige_confirmacion_y_no_toca_polaris(tmp_path):
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    cfg = {'app': {'modo_prueba': False}, 'gmail': {},
           'ocr': {}, 'polaris': {}, 'cola': dict(DEFAULTS)}
    bot = Mock()
    avisos = Mock()
    ui_event = Mock()
    q = ColaOperaciones(tmp_path, cfg, bot, Mock(), avisos=avisos, on_event=ui_event)
    job = _pendiente_de_revision_en_lote(q.store, 'A', 'Error al buscar cliente')
    with pytest.raises(ColaError, match='Confirme'):
        q.liberar_revisiones_masivas()
    assert q.store.get(job['id'])['estado'] == 'REVISION_REQUERIDA'
    result = q.liberar_revisiones_masivas(confirmado=True)
    assert result['liberables'] == 1
    assert q.store.get(job['id'])['estado'] == 'REVISION_CERRADA'
    bot.assert_not_called()
    avisos.assert_not_called()
    ui_event.assert_called_with('cola', '')


def test_retirar_pendientes_en_lote_preserva_activa_y_revision(tmp_path):
    from arybot.cola_local import ColaLocal
    store=ColaLocal(tmp_path)
    def add(n):
        return store.enqueue('FACTURA',{'ticket':str(n)},source='Portal web',
                             correo='cliente@example.com',safe=False,clave=str(n),minutes=5)[0]
    a,b,c,d=[add(i) for i in range(4)]
    store.resume()
    assert store.claim()['id']==a['id']
    store.finish(d['id'],'REVISION_REQUERIDA',reason='Caso anterior.')
    assert set(store.cancel_pending())=={b['id'],c['id']}
    assert store.get(a['id'])['estado']=='EJECUTANDO'
    assert store.get(d['id'])['estado']=='REVISION_REQUERIDA'
    assert store.get(b['id'])['estado']=='CANCELADA'
    assert store.get(c['id'])['estado']=='CANCELADA'
    assert store.cancel_pending()==[]


def test_pruebas_seguras_no_bloquean_la_siguiente(tmp_path):
    """Dos facturas de prueba nunca deben dejar el procesamiento detenido."""
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    from arybot.parser import Solicitud
    cfg = {'app': {'modo_prueba': True, 'correo': 'bot@example.com'},
           'gmail': {}, 'ocr': {}, 'polaris': {}, 'cola': dict(DEFAULTS)}
    bot = Mock()
    bot.invoice.return_value = 'PRUEBA_OK'
    bot.diag = None
    bot.etapa_alerta = 'FACTURACION: prueba'
    q = ColaOperaciones(tmp_path, cfg, bot, Mock(), avisos=Mock())
    jobs = []
    for folio in ('100001', '100002'):
        sol = Solicitud(estacion='ARY V', rfc='AAA010101AAA', ticket=folio,
                        fecha_ticket=date.today().strftime('%d/%m/%Y'),
                        forma_pago='EFECTIVO', correo_destino='cliente@example.com',
                        remitente='cliente@example.com')
        job, _ = q.encolar_factura(sol, safe=True)
        jobs.append(job)
    assert q.ejecutar_una()
    assert q.store.get(jobs[0]['id'])['estado'] == 'PRUEBA_CERRADA'
    assert not q.store.status()['pausada']
    assert q.ejecutar_una()
    assert q.store.get(jobs[1]['id'])['estado'] == 'PRUEBA_CERRADA'
    assert bot.cerrar_polaris_tras_solicitud.call_count == 2
    assert not q.store.status()['pausada']


def test_prueba_no_pasa_siguiente_si_polaris_no_cerro(tmp_path):
    """Nunca abrir otro cliente sobre una captura cuyo cierre falló."""
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    from arybot.parser import Solicitud
    cfg = {'app': {'modo_prueba': True, 'correo': 'bot@example.com'},
           'gmail': {}, 'ocr': {}, 'polaris': {}, 'cola': dict(DEFAULTS)}
    bot = Mock()
    bot.invoice.return_value = 'PRUEBA_OK'
    bot.cerrar_polaris_tras_solicitud.side_effect = RuntimeError('No se cerró Polaris')
    bot.diag = None
    bot.etapa_alerta = 'FACTURACION: prueba'
    q = ColaOperaciones(tmp_path, cfg, bot, Mock(), avisos=Mock())
    for folio in ('100003', '100004'):
        sol = Solicitud(estacion='ARY V', rfc='AAA010101AAA', ticket=folio,
                        fecha_ticket=date.today().strftime('%d/%m/%Y'),
                        forma_pago='EFECTIVO', correo_destino='cliente@example.com',
                        remitente='cliente@example.com')
        q.encolar_factura(sol, safe=True)
    assert q.ejecutar_una()
    assert q.store.status()['pausada']
    assert not q.ejecutar_una()
    assert bot.invoice.call_count == 1


def test_modo_seguro_no_congela_una_emision_pendiente(tmp_path):
    """Emisiones esperan autorización, pero no frenan solicitudes de prueba."""
    store = ColaLocal(tmp_path)
    real, _ = store.enqueue('FACTURA', {'ticket':'101'}, source='Portal web',
                            safe=False, clave='REAL-101')
    test, _ = store.enqueue('FACTURA', {'ticket':'102'}, source='Manual',
                            safe=True, clave='TEST-102')
    store.resume()
    job = store.claim(production_allowed=False)
    assert job and job['id'] == test['id']
    store.finish(test['id'], 'PRUEBA_CERRADA', reason='Prueba sin emisión')
    assert store.claim(production_allowed=False) is None
    assert not store.status()['pausada']
    job = store.claim(production_allowed=True)
    assert job and job['id'] == real['id']


def test_archivar_solicitudes_cancelar_solo_espera(tmp_path):
    """Borrar de pantalla una pendiente nunca significa emitirla."""
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    from arybot.parser import Solicitud
    cfg = {'app': {'modo_prueba': False}, 'gmail': {}, 'ocr': {},
           'polaris': {}, 'cola': dict(DEFAULTS)}
    bot = Mock()
    q = ColaOperaciones(tmp_path, cfg, bot, Mock(), avisos=Mock())
    sol = Solicitud(estacion='ARY V', rfc='AAA010101AAA', ticket='30001',
                   fecha_ticket=date.today().strftime('%d/%m/%Y'),
                   forma_pago='EFECTIVO', correo_destino='prueba@example.com')
    job, _ = q.encolar_factura(sol, source_id='web:30001', safe=False)
    with pytest.raises(ColaError, match='Confirma'):
        q.archivar_solicitudes(job['id'])
    result = q.archivar_solicitudes(job['id'], confirmado=True)
    assert result['archivadas'] == 1
    assert result['canceladas'] == 1
    assert q.store.list() == []
    assert q.store.get(job['id'])['estado'] == 'CANCELADA'
    assert q.store.get(job['id'])['archivado'] == 1
    assert q.store.source('web:30001')['id'] == job['id']
    assert len(q.store.list(incluir_archivadas=True)) == 1
    bot.invoice.assert_not_called()


def test_archivar_no_borra_factura_ya_procesada_de_estadisticas(tmp_path):
    """El historial permanece, incluso al limpiar toda la lista."""
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    cfg = {'app': {'modo_prueba': False}, 'gmail': {}, 'ocr': {},
           'polaris': {}, 'cola': dict(DEFAULTS)}
    bot = Mock()
    q = ColaOperaciones(tmp_path, cfg, bot, Mock(), avisos=Mock())
    done, _ = q.store.enqueue('FACTURA', {'ticket':'31001','estacion':'ARY V'},
                               source='Portal web', safe=False)
    q.store.finish(done['id'], 'ENVIO_SOLICITADO',reason='Polaris solicitó envío')
    pending, _ = q.store.enqueue('FACTURA', {'ticket':'31002','estacion':'ARY VI'},
                                 source='Portal web', safe=False)
    result = q.archivar_solicitudes(confirmado=True)
    assert result['archivadas'] == 2
    assert result['canceladas'] == 1
    assert not q.store.list()
    assert q.store.get(done['id'])['estado'] == 'ENVIO_SOLICITADO'
    assert q.store.get(pending['id'])['estado'] == 'CANCELADA'
    from arybot.dashboard import read_jobs, summarize
    summary = summarize(read_jobs(q.store.path, 'Todo el historial'))
    assert summary['totals']['kpi_si'] == 1
    assert summary['totals']['kpi_no'] == 0
    bot.assert_not_called()


def test_archivar_no_interrumpe_consumidor_activo(tmp_path):
    """No se borra una solicitud mientras Polaris esté capturando de verdad."""
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    cfg = {'app': {'modo_prueba': False}, 'gmail': {}, 'ocr': {},
           'polaris': {}, 'cola': dict(DEFAULTS)}
    bot = Mock()
    q = ColaOperaciones(tmp_path, cfg, bot, Mock(), avisos=Mock())
    job, _ = q.store.enqueue('FACTURA', {'ticket':'32001'},
                             source='Portal web', safe=False)
    q._consumer.acquire()
    try:
        with pytest.raises(ColaError, match='Polaris está capturando'):
            q.archivar_solicitudes(job['id'], confirmado=True)
    finally:
        q._consumer.release()
    assert q.store.get(job['id'])['archivado'] == 0


def test_archivar_atorada_huerfana_sin_reintentar_ni_perder_bloqueo_fiscal(tmp_path):
    """Una ejecución huérfana se puede retirar, pero nunca reusar su ticket."""
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS, SolicitudDuplicada
    cfg = {'app': {'modo_prueba': False}, 'gmail': {}, 'ocr': {},
           'polaris': {}, 'cola': dict(DEFAULTS)}
    bot = Mock()
    q = ColaOperaciones(tmp_path, cfg, bot, Mock(), avisos=Mock())
    job, _ = q.store.enqueue('FACTURA', {'ticket':'33001'},
                             source='Portal web', safe=False, clave='ARY-V:33001')
    q.store.resume()
    assert q.store.claim()['id'] == job['id']
    result = q.archivar_solicitudes(job['id'], confirmado=True)
    assert result['atascadas'] == 1
    assert result['riesgos_protegidos'] == 1
    assert q.store.get(job['id'])['archivado'] == 1
    assert q.store.get(job['id'])['estado'] == 'REVISION_CERRADA'
    assert q.store.get(job['id'])['riesgo_cfdi'] == 1
    assert not q.store.status()['activa']
    with pytest.raises(SolicitudDuplicada, match='operación archivada'):
        q.store.enqueue('FACTURA', {'ticket':'33001'}, source='Portal web',
                        safe=False, clave='ARY-V:33001')
    bot.invoice.assert_not_called()


def test_archivar_bloqueada_limpia_lista_sin_confirmar_cfdi(tmp_path):
    """No ocultar una revisión incierta sin conservar protección del folio."""
    from arybot.cola_local import ColaLocal
    store = ColaLocal(tmp_path)
    job, _ = store.enqueue('FACTURA', {'ticket':'34001'}, source='Portal web',
                           safe=False, clave='ARY-V:34001')
    store.finish(job['id'], 'REVISION_REQUERIDA',
                 reason='Posible CFDI timbrado o correo pendiente.', hold=True)
    with pytest.raises(ColaError, match='Confirma'):
        store.archivar_solicitudes(job['id'])
    result = store.archivar_solicitudes(job['id'], confirmado=True)
    assert result['requiere_reanudar']
    assert store.status()['bloqueo'] == ''
    assert store.status()['pausada']
    assert store.get(job['id'])['riesgo_cfdi'] == 1
    assert store.get(job['id'])['archivado'] == 1
    assert not store.list()
    with pytest.raises(SolicitudDuplicada):
        store.enqueue('FACTURA', {'ticket':'34001'}, source='Portal web',
                      safe=False, clave='ARY-V:34001')


def test_archivar_no_envia_avisos_que_esperaban(tmp_path):
    """Quitar un ticket evita enviar recibos aún pendientes; no borra enviados."""
    from arybot.cola_local import ColaLocal
    store = ColaLocal(tmp_path)
    job, _ = store.enqueue('FACTURA', {'ticket':'35001'}, source='Portal web',
                           safe=False, correo='cliente@example.com')
    store.notice(job, 'recibida', '[ARY Solicitud] Recibida', 'Recibimos tu solicitud', send=True)
    assert store.notices()[0]['estado'] == 'PENDIENTE'
    store.archivar_solicitudes(job['id'], confirmado=True)
    assert store.notices()[0]['estado'] == 'SUPERADA'
    assert store.claim_notice() is None


def test_archivar_migra_base_sqlite_antigua_sin_perder_registros(tmp_path):
    """La VM existente puede abrir la base anterior sin borrar trabajos."""
    import sqlite3
    from arybot.cola_local import ColaLocal
    base = tmp_path / 'data'
    base.mkdir()
    path = base / 'cola_operaciones.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE trabajos (
            id TEXT PRIMARY KEY, fuente_id TEXT UNIQUE, clave TEXT, tipo TEXT NOT NULL,
            origen TEXT NOT NULL, seguro INTEGER NOT NULL, estado TEXT NOT NULL,
            datos TEXT NOT NULL, correo TEXT NOT NULL DEFAULT '',
            motivo TEXT NOT NULL DEFAULT '', creado REAL NOT NULL,
            actualizado REAL NOT NULL, iniciado REAL, terminado REAL,
            resultado TEXT NOT NULL DEFAULT '{}', minutos INTEGER NOT NULL DEFAULT 5)""")
        db.execute("""INSERT INTO trabajos
            (id,clave,tipo,origen,seguro,estado,datos,creado,actualizado)
            VALUES ('ANTIGUO-001','X:123','FACTURA','Portal web',0,
                    'ENVIO_SOLICITADO','{"ticket":"123"}',100,100)""")
    store = ColaLocal(tmp_path)
    assert store.get('ANTIGUO-001')['estado'] == 'ENVIO_SOLICITADO'
    assert store.get('ANTIGUO-001')['archivado'] == 0
    store.archivar_solicitudes('ANTIGUO-001', confirmado=True)
    assert store.list() == []
    assert store.get('ANTIGUO-001')['archivado'] == 1


def test_archivar_bloqueo_no_reanuda_al_llegar_nueva_solicitud(tmp_path):
    """No reactivar Polaris desde el portal hasta una confirmación posterior."""
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    cfg = {'app': {'modo_prueba': False}, 'gmail': {}, 'ocr': {},
           'polaris': {}, 'cola': dict(DEFAULTS)}
    bot = Mock()
    q = ColaOperaciones(tmp_path, cfg, bot, Mock(), avisos=Mock())
    bloqueo, _ = q.store.enqueue('FACTURA', {'ticket':'36001'},
                                 source='Portal web',safe=False,clave='ARY-V:36001')
    q.store.finish(bloqueo['id'], 'REVISION_REQUERIDA',
                   reason='Posible CFDI timbrado o envío pendiente.',hold=True)
    q.archivar_solicitudes(bloqueo['id'], confirmado=True)
    assert q._manual_pause
    assert q.store.status()['pausada']
    nuevo, _ = q.store.enqueue('FACTURA', {'ticket':'36002'},
                               source='Portal web', safe=False, clave='ARY-V:36002')
    assert q.store.get(nuevo['id'])['estado'] == 'EN_COLA'
    assert q.store.status()['pausada']
    assert not q.ejecutar_una()
    bot.invoice.assert_not_called()


def test_primera_instalacion_magenta_lista_blanca_y_sin_reintentos(tmp_path):
    """La actualización archiva solicitudes antiguas una sola vez y no timbra."""
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    store=ColaLocal(tmp_path)
    anterior,_=store.enqueue('FACTURA', {'ticket':'40101','estacion':'ARY VI'},
                             source='Portal web',source_id='web:antiguo',safe=False)
    antiguo_ok,_=store.enqueue('FACTURA', {'ticket':'40102','estacion':'ARY VI'},
                               source='Portal web',safe=False)
    store.finish(antiguo_ok['id'],'ENVIO_SOLICITADO',reason='Factura emitida')
    cfg={'app':{'modo_prueba':False,'ary_nueva_etapa_magenta':True},
         'gmail':{},'ocr':{},'polaris':{},'cola':dict(DEFAULTS)}
    bot=Mock()
    q=ColaOperaciones(tmp_path,cfg,bot,Mock(),avisos=Mock())
    assert q.store.list()==[]
    assert q.store.get(anterior['id'])['estado']=='CANCELADA'
    assert q.store.get(antiguo_ok['id'])['estado']=='ENVIO_SOLICITADO'
    assert all(row['archivado']==1 for row in q.store.list(incluir_archivadas=True))
    assert q.store.meta('ary_v2_nueva_etapa_magenta_20261127')=='1'
    assert q.store.source('web:antiguo')['id']==anterior['id']
    nuevo,_=q.store.enqueue('FACTURA', {'ticket':'40103','estacion':'ARY VI'},
                            source='Portal web',safe=False)
    q2=ColaOperaciones(tmp_path,cfg,bot,Mock(),avisos=Mock())
    assert [r['id'] for r in q2.store.list()]==[nuevo['id']]
    assert len(q2.store.list(incluir_archivadas=True))==3
    bot.invoice.assert_not_called()


def test_nueva_etapa_fiscal_incierto_no_auto_reanuda(tmp_path):
    """Al limpiar un CFDI posiblemente timbrado, sigue bloqueado contra duplicado."""
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    store=ColaLocal(tmp_path)
    viejo,_=store.enqueue('FACTURA',{'ticket':'40201','estacion':'ARY V'},
                          source='Portal web',safe=False,clave='ARY_V:40201')
    store.finish(viejo['id'],'REVISION_REQUERIDA',
                 reason='Posible CFDI timbrado o envío pendiente.',hold=True)
    cfg={'app':{'modo_prueba':False,'ary_nueva_etapa_magenta':True},
         'gmail':{},'ocr':{},'polaris':{},'cola':dict(DEFAULTS)}
    bot=Mock()
    q=ColaOperaciones(tmp_path,cfg,bot,Mock(),avisos=Mock())
    assert q.store.list()==[]
    assert q._manual_pause
    assert q.store.meta('pendiente_comprobar_polaris')=='1'
    assert q.store.get(viejo['id'])['riesgo_cfdi']==1
    with pytest.raises(ColaError,match='Confirma'):
        q.iniciar_cola()
    with pytest.raises(SolicitudDuplicada):
        q.store.enqueue('FACTURA', {'ticket':'40201','estacion':'ARY V'},
                        source='Portal web',safe=False,clave='ARY_V:40201')
    q.iniciar_cola(polaris_comprobado=True)
    assert not q.store.status()['pausada']
    bot.invoice.assert_not_called()


def test_nueva_etapa_incierta_sin_bloqueo_exige_comprobar(tmp_path):
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    store=ColaLocal(tmp_path)
    viejo,_=store.enqueue('FACTURA', {'ticket':'40301','estacion':'ARY VI'},
                          source='Portal web',safe=False,clave='ARY_VI:40301')
    store.finish(viejo['id'],'REVISION_REQUERIDA',
                 reason='Posible CFDI timbrado; no se confirmó cierre.',hold=False)
    assert not store.status()['bloqueo']
    cfg={'app':{'modo_prueba':False,'ary_nueva_etapa_magenta':True},
         'gmail':{},'ocr':{},'polaris':{},'cola':dict(DEFAULTS)}
    q=ColaOperaciones(tmp_path,cfg,Mock(),Mock(),avisos=Mock())
    assert q.store.list()==[]
    assert q.store.meta('pendiente_comprobar_polaris')=='1'
    assert q.store.status()['pausada']
    assert q.store.get(viejo['id'])['riesgo_cfdi']==1


def test_migracion_auto_procesa_nueva_factura_sin_clic(tmp_path):
    """Caso real: histórico incierto archivado, nueva solicitud web recibida."""
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    from arybot.parser import Solicitud
    store = ColaLocal(tmp_path)
    anterior, _ = store.enqueue('FACTURA',
        {'ticket':'5550001','estacion':'ARY IV'}, source='Portal web',
        safe=False, clave='OLD:5550001')
    store.finish(anterior['id'],'REVISION_REQUERIDA',
                 reason='Posible CFDI timbrado o envío pendiente.',hold=True)
    cfg={'app':{'modo_prueba':False,'ary_nueva_etapa_magenta':True},
         'gmail':{},'ocr':{},'polaris':{},'cola':dict(DEFAULTS)}
    bot=Mock()
    bot.pantalla_libre_para_nueva_solicitud.return_value=True
    bot.invoice.return_value='ENVIO_SOLICITADO'
    q=ColaOperaciones(tmp_path,cfg,bot,Mock(),avisos=Mock())
    assert q.store.list()==[]
    assert q.store.meta('pendiente_comprobar_polaris')=='1'
    nueva=Solicitud(estacion='ARY IV', rfc='AAA010101AAA', ticket='0006043150',
                    fecha_ticket=date.today().strftime('%d/%m/%Y'),
                    forma_pago='EFECTIVO', correo_destino='cliente@example.com')
    job,created=q.encolar_factura(nueva,source='Portal web',
                                source_id='web:nueva',safe=False)
    assert created
    assert not q.store.status()['pausada']
    assert not q._manual_pause
    assert q.store.meta('pendiente_comprobar_polaris')=='0'
    bot.pantalla_libre_para_nueva_solicitud.assert_called()
    assert q.ejecutar_una()
    bot.invoice.assert_called_once()
    assert q.store.get(job['id'])['estado']=='ENVIO_SOLICITADO'
    assert q.store.get(anterior['id'])['archivado']==1
    assert q.store.get(anterior['id'])['riesgo_cfdi']==1


def test_migracion_auto_espera_si_hay_ventana_de_polaris(tmp_path):
    """No iniciar otra factura encima de un CFDI anterior todavía visible."""
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    from arybot.parser import Solicitud
    store=ColaLocal(tmp_path)
    old,_=store.enqueue('FACTURA', {'ticket':'5550011','estacion':'ARY VI'},
                         source='Portal web',safe=False)
    store.finish(old['id'],'REVISION_REQUERIDA',
                 reason='Posible CFDI timbrado.',hold=True)
    cfg={'app':{'modo_prueba':False,'ary_nueva_etapa_magenta':True},
         'gmail':{},'ocr':{},'polaris':{},'cola':dict(DEFAULTS)}
    bot=Mock()
    bot.pantalla_libre_para_nueva_solicitud.return_value=False
    bot.invoice.return_value='ENVIO_SOLICITADO'
    q=ColaOperaciones(tmp_path,cfg,bot,Mock(),avisos=Mock())
    job,_=q.encolar_factura(Solicitud(estacion='ARY VI',rfc='AAA010101AAA',
                        ticket='0006043151',fecha_ticket=date.today().strftime('%d/%m/%Y'),
                        forma_pago='EFECTIVO',correo_destino='cliente@example.com'),
                        safe=False)
    assert q.store.status()['pausada']
    assert not q.ejecutar_una()
    bot.invoice.assert_not_called()
    # Una revisión posterior, aún automática, reanuda cuando Polaris está libre.
    bot.pantalla_libre_para_nueva_solicitud.return_value=True
    q._last_auto_probe=0.0
    assert q._reactivar_automaticamente_tras_migracion()
    assert not q.store.status()['pausada']
    assert q.ejecutar_una()
    assert q.store.get(job['id'])['estado']=='ENVIO_SOLICITADO'
    bot.invoice.assert_called_once()


def test_pausa_del_operador_no_se_reanuda_sola(tmp_path):
    """La reactivación automática no debe anular el botón de Detener."""
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    store=ColaLocal(tmp_path)
    old,_=store.enqueue('FACTURA', {'ticket':'5550021','estacion':'ARY V'},
                         source='Portal web',safe=False)
    store.finish(old['id'],'REVISION_REQUERIDA',
                 reason='Posible CFDI timbrado.',hold=True)
    cfg={'app':{'modo_prueba':False,'ary_nueva_etapa_magenta':True},
         'gmail':{},'ocr':{},'polaris':{},'cola':dict(DEFAULTS)}
    bot=Mock()
    bot.pantalla_libre_para_nueva_solicitud.return_value=True
    q=ColaOperaciones(tmp_path,cfg,bot,Mock(),avisos=Mock())
    job,_=q.store.enqueue('FACTURA', {'ticket':'5550022','estacion':'ARY V'},
                          source='Portal web',safe=False)
    q.pausar()
    assert not q._reactivar_automaticamente_tras_migracion()
    assert q.store.status()['pausada']
    assert q.store.get(job['id'])['estado']=='EN_COLA'


def test_comprobacion_libre_lee_ventanas_sin_tocarlas():
    bot=polaris.PolarisBot.__new__(polaris.PolarisBot)
    bot.pcfg={'ventana_principal_regex':'Polaris',
              'ventana_login_regex':'Entrada al Sistema'}
    bot.log=Mock()
    bot._find_title=Mock(side_effect=lambda rx: 10 if rx=='Polaris' else None)
    bot._invoice_window=Mock(return_value=None)
    bot._validar_misma_sesion_windows=Mock()
    bot._popup_candidates=Mock(return_value=[])
    bot._has_unsaved_prompt=Mock(return_value=None)
    assert bot.pantalla_libre_para_nueva_solicitud() is True
    bot._invoice_window.return_value=25
    assert bot.pantalla_libre_para_nueva_solicitud() is False
    bot._invoice_window.return_value=None
    bot._popup_candidates.return_value=[(1,2,3,4,'Aviso',Mock(),Mock())]
    assert bot.pantalla_libre_para_nueva_solicitud() is False
    bot._popup_candidates.return_value=[]
    bot._has_unsaved_prompt.return_value=42
    assert bot.pantalla_libre_para_nueva_solicitud() is False
    bot._has_unsaved_prompt.return_value=None
    bot._validar_misma_sesion_windows.side_effect=RuntimeError('Otro escritorio')
    assert bot.pantalla_libre_para_nueva_solicitud() is False


def test_portal_reenvio_identico_devuelve_referencia_original(monkeypatch,tmp_path):
    """Reenvío de la misma constancia+ticket vuelve al seguimiento, no a Polaris."""
    import threading
    from arybot.cola_local import ColaLocal
    from arybot.cola_modelo import factura_datos,clave_trabajo
    from arybot.portal_http import PortalWSGI
    from arybot.parser import Solicitud
    from arybot import portal_http
    data=factura_datos(Solicitud(
        estacion='ARY IV',rfc='AAA010101AAA',ticket='0006043150',
        fecha_ticket=date.today().strftime('%d/%m/%Y'),forma_pago='EFECTIVO',
        correo_destino='cliente@example.com',remitente='cliente@example.com'))
    store=ColaLocal(tmp_path)
    old,_=store.enqueue('FACTURA',data,source='Portal web',
                        source_id='web:original',safe=False,
                        clave=clave_trabajo('FACTURA',data))
    assert store.solicitud_activa_identica('FACTURA',data,safe=False)['id']==old['id']
    assert store.solicitud_activa_identica('FACTURA',data,safe=True) is None
    assert store.solicitud_activa_identica('FACTURA',dict(data,correo_destino='otro@example.com'),safe=False) is None
    assert store.solicitud_activa_identica('FACTURA',dict(data,rfc='BBB010101BBB'),safe=False) is None

    api=PortalWSGI.__new__(PortalWSGI)
    api.cola=Mock(store=store)
    api.cola.despertar_solicitud_existente=Mock()
    api.cola.encolar_factura=Mock(side_effect=AssertionError('No encolar otra vez'))
    api._guard=lambda env,post=False:{'sid':'a'*32}
    api._body=lambda env:{}
    api._mac=lambda purpose,value:'x'*64
    api._sign=lambda purpose,value:'firma-original'
    api.clock=lambda:0
    api.limits=Mock()
    api.limits.aceptar.return_value=True
    api.lock=threading.RLock()
    api.aceptando=True
    api.settings={'max_solicitudes_pendientes':100,'limite_envios_10_minutos':8}
    api.modo_seguro=lambda:False
    monkeypatch.setattr(portal_http,'validar_entrada',lambda body,**kwargs:('FACTURA',None,data))
    monkeypatch.setattr(portal_http,'piloto_habilitado',lambda:False)
    code,returned,content_type,_=api._dispatch({
        'REQUEST_METHOD':'POST','PATH_INFO':'/api/facturacion/solicitudes',
        'HTTP_X_ARY_REQUEST_ID':'0'*32,'HTTP_X_ARY_MODE':'real',
        'REMOTE_ADDR':'127.0.0.1'})
    assert code==200
    assert returned['referencia'].startswith('ARY-')
    assert returned['estado']=='EN_COLA'
    assert returned['seguimiento']=='firma-original'
    assert len(store.list())==1
    assert store.get(old['id'])['estado']=='EN_COLA'
    api.cola.despertar_solicitud_existente.assert_called_once()
    api.cola.encolar_factura.assert_not_called()


def test_portal_duplicado_no_revela_seguimiento_a_otros_datos(tmp_path):
    from arybot.cola_local import ColaLocal
    from arybot.cola_modelo import clave_trabajo
    store=ColaLocal(tmp_path)
    original=dict(estacion='ARY IV',ticket='0006043150',rfc='AAA010101AAA',
                  fecha_ticket=date.today().isoformat(),forma_pago='EFECTIVO',
                  uso_cfdi='G03',correo_destino='cliente@example.com')
    job,_=store.enqueue('FACTURA',original,source='Portal web',
                        safe=False,clave=clave_trabajo('FACTURA',original))
    assert store.solicitud_activa_identica('FACTURA',dict(original,correo_destino='intruso@example.com'),safe=False) is None
    assert store.solicitud_activa_identica('FACTURA',dict(original,uso_cfdi='S01'),safe=False) is None
    store.archivar_solicitudes(job['id'],confirmado=True)
    assert store.solicitud_activa_identica('FACTURA',original,safe=False) is None


def _factura_web_guardada(store, folio, *, estado='REVISION_REQUERIDA',
                          motivo='Posible CFDI timbrado o envío pendiente.',
                          seguro=False):
    """Simula un único trámite web; jamás toca Windows ni Polaris."""
    from arybot.parser import Solicitud
    from arybot.cola_modelo import factura_datos, clave_trabajo
    data=factura_datos(Solicitud(estacion='ARY IV',ticket=folio,
        rfc='AAA010101AAA',fecha_ticket=date.today().strftime('%d/%m/%Y'),
        forma_pago='EFECTIVO',correo_destino='cliente@example.com'))
    job,_=store.enqueue('FACTURA',data,source='Portal web',safe=seguro,
                        source_id='web:unico-'+folio,clave=clave_trabajo('FACTURA',data))
    if estado!='EN_COLA':
        store.finish(job['id'],estado,reason=motivo,hold=estado=='REVISION_REQUERIDA')
    return store.get(job['id'])


def test_retomar_folio_archivado_tiene_misma_referencia_y_auditoria(tmp_path):
    """Un ticket NO timbrado vuelve a EN_COLA una vez sin perder su trazabilidad."""
    import sqlite3
    from arybot.cola_local import ColaLocal
    store=ColaLocal(tmp_path)
    old=_factura_web_guardada(store,'0006043150')
    old_id=old['id']
    archive=store.archivar_solicitudes(old_id,confirmado=True)
    assert archive['archivadas']==1
    assert store.get(old_id)['riesgo_cfdi']==1
    with pytest.raises(ColaError,match='confirmar'):
        store.retomar_folio_sin_cfdi('ARY IV','0006043150')
    new=store.retomar_folio_sin_cfdi('ARY IV','0006043150',confirmado=True)
    assert new['id']==old_id
    assert new['estado']=='EN_COLA'
    assert new['archivado']==0
    assert new['riesgo_cfdi']==0
    assert new['iniciado'] is None
    assert new['seguro'] is False
    assert store.source('web:unico-0006043150')['id']==old_id
    assert len(store.list(incluir_archivadas=True))==1
    assert not store.meta('bloqueo')
    with sqlite3.connect(store.path) as db:
        row=db.execute('SELECT trabajo,estado_anterior,motivo_anterior,accion '
                       'FROM recuperaciones_folio').fetchone()
    assert row[0]==old_id
    assert row[1]=='REVISION_REQUERIDA'
    assert 'timbrado' in row[2]
    assert row[3]=='OPERADOR_VERIFICO_POLARIS_SIN_CFDI'


def test_retomar_folio_no_autoriza_si_ya_hay_factura_o_activa(tmp_path):
    from arybot.cola_local import ColaLocal
    store=ColaLocal(tmp_path)
    done=_factura_web_guardada(store,'7000001',estado='ENVIO_SOLICITADO',motivo='CFDI terminado')
    store.archivar_solicitudes(done['id'],confirmado=True)
    with pytest.raises(ColaError,match='confirmada'):
        store.retomar_folio_sin_cfdi('ARY IV','7000001',confirmado=True)
    active=_factura_web_guardada(store,'7000002',estado='EN_COLA')
    with pytest.raises(ColaError,match='activa'):
        store.retomar_folio_sin_cfdi('ARY IV','7000002',confirmado=True)
    assert store.get(active['id'])['estado']=='EN_COLA'
    with pytest.raises(ColaError,match='no aparece'):
        store.retomar_folio_sin_cfdi('ARY IV','7999999',confirmado=True)


def test_retomar_folio_nunca_emite_si_no_se_ha_confirmado_polaris(tmp_path):
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    from arybot.cola_local import ColaLocal
    store=ColaLocal(tmp_path)
    old=_factura_web_guardada(store,'7000010')
    store.archivar_solicitudes(old['id'],confirmado=True)
    bot=Mock()
    bot.pantalla_libre_para_nueva_solicitud.return_value=False
    bot.invoice.return_value='ENVIO_SOLICITADO'
    cfg={'app':{'modo_prueba':False},'gmail':{},'ocr':{},'polaris':{},'cola':dict(DEFAULTS)}
    q=ColaOperaciones(tmp_path,cfg,bot,Mock(),store=store,avisos=Mock())
    with pytest.raises(ColaError,match='confirma'):
        q.retomar_folio_sin_cfdi('ARY IV','7000010')
    with pytest.raises(ColaError,match='pantalla abierta'):
        q.retomar_folio_sin_cfdi('ARY IV','7000010',confirmado=True)
    bot.invoice.assert_not_called()
    assert store.get(old['id'])['archivado']==1
    bot.pantalla_libre_para_nueva_solicitud.return_value=True
    result=q.retomar_folio_sin_cfdi('ARY IV','7000010',confirmado=True)
    assert result['id']==old['id']
    assert store.status()['pausada'] is False
    assert store.meta('pendiente_comprobar_polaris')=='0'
    assert q.ejecutar_una()
    assert store.get(old['id'])['estado']=='ENVIO_SOLICITADO'
    bot.invoice.assert_called_once()
    assert len(store.list(incluir_archivadas=True))==1


def test_retomar_folio_rechaza_modo_seguro_y_polaris_ocupado(tmp_path):
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    from arybot.cola_local import ColaLocal
    store=ColaLocal(tmp_path)
    old=_factura_web_guardada(store,'7000020')
    store.archivar_solicitudes(old['id'],confirmado=True)
    bot=Mock()
    bot.pantalla_libre_para_nueva_solicitud.return_value=True
    cfg={'app':{'modo_prueba':True},'gmail':{},'ocr':{},'polaris':{},'cola':dict(DEFAULTS)}
    q=ColaOperaciones(tmp_path,cfg,bot,Mock(),store=store,avisos=Mock())
    with pytest.raises(ColaError,match='MODO SEGURO'):
        q.retomar_folio_sin_cfdi('ARY IV','7000020',confirmado=True)
    cfg['app']['modo_prueba']=False
    q._consumer.acquire()
    try:
        with pytest.raises(ColaError,match='trabajando'):
            q.retomar_folio_sin_cfdi('ARY IV','7000020',confirmado=True)
    finally:q._consumer.release()
    assert store.get(old['id'])['archivado']==1
    bot.invoice.assert_not_called()


def test_archivo_no_marca_timbrado_no_intentado_como_incierto(tmp_path):
    """Un fallo antes de Aceptar no crea una falsa marca de CFDI incierto."""
    from arybot.cola_local import ColaLocal
    store=ColaLocal(tmp_path)
    job=_factura_web_guardada(store,'7000030',estado='REVISION_REQUERIDA',
            motivo='Error antes del timbrado: no se pudo abrir facturación.')
    store.archivar_solicitudes(job['id'],confirmado=True)
    assert store.get(job['id'])['riesgo_cfdi']==0
    assert store.get(job['id'])['archivado']==1
    assert not store.status()['bloqueo']
    # El mismo ticket puede volver a consultarse: Polaris determina después
    # si existe un CFDI. La gestión de fechas sigue vigente.
    new,_=store.enqueue('FACTURA',job['datos'],source='Portal web',safe=False,
                        clave=job['clave'])
    assert new['id']!=job['id']
