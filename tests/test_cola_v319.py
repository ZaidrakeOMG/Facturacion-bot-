"""Pruebas de cola/avisos sin SQL Server, timbrado, ventanas ni Gmail reales."""
import copy
import json
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from arybot.cola_operaciones import ColaOperaciones
from arybot.cola_local import ColaLocal
from arybot.cola_modelo import *
from arybot.avisos_cliente import AvisosCliente,construir_aviso
from arybot.alertas_correo import ErrorEnvioAlerta
from arybot.instancia import InstanciaBot


def cfg(safe=False):
    return {'app':{'modo_prueba':safe,'correo':'emisioncfdi@grupoary.com','intervalo_segundos':5},
            'gmail':{'labels':{'procesando':'PROCESANDO','esperando':'ESPERANDO'}},'ocr':{},'polaris':{},'cola':dict(DEFAULTS)}

def sol(ticket='123',station='ARY I',email='cliente@example.com'):
    return Solicitud(estacion=station,rfc='AAA010101AAA',ticket=ticket,forma_pago='TARJETA DE DEBITO',correo_destino=email,remitente=email)

def alta(rfc='AAA010101AAA'):
    return AltaCliente.crear('ARY I',rfc,'12345678901','8711234567','cliente@example.com')

class FakeAlta:
    def __init__(self):self.pendiente=None;self.accepts=0;self.prepares=0
    def preparar(self,request):
        self.prepares+=1;self.pendiente=SimpleNamespace(solicitud=request)
        return {'nombre':'CLIENTE DEMO','codigo_postal':'27000','numero_legible':False}
    def aceptar(self,request,**kwargs):
        assert kwargs=={'autorizado':True,'inexistencia_revisada':True}
        assert self.pendiente.solicitud==request
        self.pendiente=None;self.accepts+=1
        return {'numero_cliente':'1234'}
    def liberar(self):self.pendiente=None

@pytest.fixture
def env(tmp_path):
    c=cfg();bot=Mock();bot.invoice.return_value='ENVIO_SOLICITADO';bot.etapa_alerta='FACTURACION: prueba'
    alerts=Mock();a=FakeAlta();q=ColaOperaciones(tmp_path,c,bot,Mock(),alertas=alerts,alta=a)
    yield SimpleNamespace(q=q,cfg=c,bot=bot,alerts=alerts,alta=a,base=tmp_path)
    q.detener()
    for t in (q.thread,q.avisos.thread):
        if t:t.join(timeout=2)

def test_new_queue_paused(env):assert env.q.store.status()['pausada']
def test_enqueue_does_not_operate(env):
    j,_=env.q.encolar_factura(sol(),safe=False);env.bot.invoice.assert_not_called();assert j['estado']=='EN_COLA'
def test_no_claim_while_paused(env):
    env.q.encolar_factura(sol(),safe=False);assert not env.q.ejecutar_una()
def test_follows_fifo(env):
    for i in range(3):env.q.encolar_factura(sol(str(i+1)),safe=False)
    env.q.iniciar_cola()
    for i in range(3):assert env.q.ejecutar_una()
    assert [call.args[0].ticket for call in env.bot.invoice.call_args_list]==['1','2','3']
def test_snapshot_copies_data(env):
    s=sol();j,_=env.q.encolar_factura(s,safe=False);s.ticket='999'
    assert env.q.store.get(j['id'])['datos']['ticket']=='123'
def test_duplicate_zero_padding(env):
    env.q.encolar_factura(sol('000123'),safe=False)
    with pytest.raises(SolicitudDuplicada):env.q.encolar_factura(sol('123'),safe=False)
def test_other_station_not_duplicate(env):
    env.q.encolar_factura(sol(),safe=False);j,_=env.q.encolar_factura(sol(station='ARY VI'),safe=False)
    assert j['estado']=='EN_COLA'
def test_same_source_id_idempotent(env):
    j,a=env.q.encolar_factura(sol(),safe=False,source_id='gmail:m1')
    k,b=env.q.encolar_factura(sol(),safe=False,source_id='gmail:m1')
    assert a and not b and j['id']==k['id'];assert len(env.q.store.notices())==1
def test_concurrent_enqueue_same_source(env):
    rows=[];errors=[]
    def run():
        try:rows.append(env.q.encolar_factura(sol(),safe=False,source_id='gmail:m1'))
        except Exception as e:errors.append(e)
    ts=[threading.Thread(target=run) for _ in range(12)]
    for t in ts:t.start()
    for t in ts:t.join()
    assert not errors;assert len({r[0]['id'] for r in rows})==1
    assert sum(r[1] for r in rows)==1

def test_safe_mode_does_not_upgrade(env):
    env.bot.invoice.return_value='PRUEBA_OK';j,_=env.q.encolar_factura(sol(),safe=True)
    env.q.iniciar_cola();env.q.ejecutar_una()
    assert env.bot.invoice.call_args.kwargs['test_mode'] is True
    assert env.q.store.get(j['id'])['estado']=='PREPARADA_FACTURA'
def test_safe_prepared_holds_queue(env):
    env.bot.invoice.return_value='PRUEBA_OK';j,_=env.q.encolar_factura(sol(),safe=True)
    env.q.encolar_factura(sol('124'),safe=False)
    env.q.iniciar_cola();env.q.ejecutar_una()
    assert not env.q.ejecutar_una();assert env.q.store.status()['bloqueo']==j['id']
def test_release_requires_confirmation(env):
    with pytest.raises(ColaError):env.q.liberar_revision('x')
def test_release_safe_does_not_touch_windows(env):
    env.bot.invoice.return_value='PRUEBA_OK';j,_=env.q.encolar_factura(sol(),safe=True)
    env.q.iniciar_cola();env.q.ejecutar_una();env.bot.reset_mock()
    env.q.liberar_revision(j['id'],confirmado=True)
    env.bot.assert_not_called();assert env.q.store.status()['pausada']
    assert env.q.store.get(j['id'])['estado']=='PRUEBA_CERRADA'
def test_safe_closed_can_explicitly_resubmit(env):
    env.bot.invoice.return_value='PRUEBA_OK';j,_=env.q.encolar_factura(sol(),safe=True)
    env.q.iniciar_cola();env.q.ejecutar_una();env.q.liberar_revision(j['id'],confirmado=True)
    k,_=env.q.encolar_factura(sol(),safe=False);assert k['id']!=j['id']
def test_real_while_global_safe_is_not_executed(env):
    j,_=env.q.encolar_factura(sol(),safe=False);env.cfg['app']['modo_prueba']=True
    env.q.iniciar_cola();assert not env.q.ejecutar_una();env.bot.invoice.assert_not_called()
    assert env.q.store.get(j['id'])['estado']=='EN_COLA';assert env.q.store.status()['pausada']
def test_error_pauses_only_screen_queue(env):
    env.bot.invoice.side_effect=RuntimeError('Prueba fallo PAC')
    j,_=env.q.encolar_factura(sol(),safe=False);env.q.iniciar_cola();env.q.ejecutar_una()
    k,_=env.q.encolar_factura(sol('999'),safe=False)
    assert k['estado']=='EN_COLA';assert not env.q.ejecutar_una()
    assert env.q.store.get(j['id'])['estado']=='REVISION_REQUERIDA';env.alerts.reportar.assert_called_once()
def test_error_text_not_emailed_to_customer(env):
    env.bot.invoice.side_effect=RuntimeError('SQLPASSWORD-secret document.content ERROR')
    j,_=env.q.encolar_factura(sol(),safe=False);env.q.iniciar_cola();env.q.ejecutar_una()
    notices=env.q.store.notices()
    assert any(r['evento']=='revision' for r in notices)
    assert not any('SQLPASSWORD' in r['cuerpo'] or 'document.content' in r['cuerpo'] for r in notices)
def test_review_closed_cannot_blindly_retry(env):
    env.bot.invoice.side_effect=RuntimeError('Fallo')
    j,_=env.q.encolar_factura(sol(),safe=False);env.q.iniciar_cola();env.q.ejecutar_una();env.q.liberar_revision(j['id'],confirmado=True)
    with pytest.raises(SolicitudDuplicada):env.q.encolar_factura(sol(),safe=False)
def test_success_leaves_other_work_runnable(env):
    j,_=env.q.encolar_factura(sol(),safe=False);env.q.iniciar_cola();env.q.ejecutar_una()
    assert not env.q.store.status()['pausada'];assert env.q.store.get(j['id'])['estado']=='ENVIO_SOLICITADO'
def test_success_does_not_claim_delivery(env):
    j,_=env.q.encolar_factura(sol(),safe=False);env.q.iniciar_cola();env.q.ejecutar_una()
    row=next(n for n in env.q.store.notices() if n['evento']=='completada')
    assert 'ni confirma su recepción' in row['cuerpo']
def test_invalid_result_holds_no_retry(env):
    env.bot.invoice.return_value='OK'
    j,_=env.q.encolar_factura(sol(),safe=False);env.q.iniciar_cola();env.q.ejecutar_una()
    assert env.q.store.get(j['id'])['estado']=='REVISION_REQUERIDA'
def test_cancel_only_pending(env):
    j,_=env.q.encolar_factura(sol(),safe=False);env.q.cancelar(j['id']);assert env.q.store.get(j['id'])['estado']=='CANCELADA'
    env.q.iniciar_cola();assert not env.q.ejecutar_una()
def test_cannot_cancel_done(env):
    j,_=env.q.encolar_factura(sol(),safe=False);env.q.iniciar_cola();env.q.ejecutar_una()
    with pytest.raises(ColaError):env.q.cancelar(j['id'])

def test_alta_only_prepares(env):
    j,_=env.q.encolar_alta(alta(),safe=False);env.q.iniciar_cola();env.q.ejecutar_una()
    assert env.alta.prepares==1 and env.alta.accepts==0
    assert env.q.store.get(j['id'])['estado']=='PREPARADA_ALTA'
@pytest.mark.parametrize('auth,review',[(False,False),(True,False),(False,True)])
def test_alta_needs_both_permissions(env,auth,review):
    j,_=env.q.encolar_alta(alta(),safe=False);env.q.iniciar_cola();env.q.ejecutar_una()
    with pytest.raises(ColaError):env.q.aceptar_alta(j['id'],autorizado=auth,inexistencia_revisada=review)
def test_alta_cannot_accept_safe(env):
    j,_=env.q.encolar_alta(alta(),safe=True);env.q.iniciar_cola();env.q.ejecutar_una();env.cfg['app']['modo_prueba']=True
    with pytest.raises(ColaError):env.q.aceptar_alta(j['id'],autorizado=True,inexistencia_revisada=True)
def test_alta_authorized_continues_same_reserved_request(env):
    j,_=env.q.encolar_alta(alta(),safe=False);env.q.encolar_factura(sol(),safe=False)
    env.q.iniciar_cola();env.q.ejecutar_una()
    env.q.aceptar_alta(j['id'],autorizado=True,inexistencia_revisada=True);env.q.ejecutar_una()
    assert env.alta.accepts==1;env.bot.invoice.assert_not_called()
    assert env.q.store.get(j['id'])['estado']=='ALTA_CONFIRMADA'
def test_alta_wrong_pending_data_rejected(env):
    j,_=env.q.encolar_alta(alta(),safe=False);env.q.iniciar_cola();env.q.ejecutar_una()
    env.alta.pendiente=SimpleNamespace(solicitud=alta('BBB010101BBB'))
    with pytest.raises(ColaError):env.q.aceptar_alta(j['id'],autorizado=True,inexistencia_revisada=True)
def test_alta_double_accept_not_allowed(env):
    j,_=env.q.encolar_alta(alta(),safe=False);env.q.iniciar_cola();env.q.ejecutar_una()
    env.q.aceptar_alta(j['id'],autorizado=True,inexistencia_revisada=True)
    with pytest.raises(ColaError):env.q.aceptar_alta(j['id'],autorizado=True,inexistencia_revisada=True)

def test_restart_keeps_queued_but_paused(env):
    j,_=env.q.encolar_factura(sol(),safe=False);env.q.iniciar_cola()
    store=ColaLocal(env.base);store.recover()
    assert store.get(j['id'])['estado']=='EN_COLA';assert store.status()['pausada']
def test_restart_executing_is_review_not_pending(env):
    j,_=env.q.encolar_factura(sol(),safe=False);env.q.iniciar_cola();env.q.store.claim()
    store=ColaLocal(env.base);recovered=store.recover()
    assert recovered[0]['estado']=='REVISION_REQUERIDA';assert not store.claim()
def test_restart_sending_is_uncertain(env):
    j,_=env.q.encolar_factura(sol(),safe=False);env.q.store.claim_notice();env.q.store.recover()
    assert env.q.store.notices()[0]['estado']=='ENVIO_INCIERTO'
def test_repeated_recovery_not_resend_receipt(env):
    env.q.encolar_factura(sol(),safe=False)
    other=ColaOperaciones(env.base,env.cfg,env.bot,Mock())
    assert len(other.store.notices())==1;other.detener()
def test_sqlite_location_only_local(env):assert env.q.store.path==env.base/'data'/'cola_operaciones.sqlite3'

def test_paused_has_no_eta(env):
    j,_=env.q.encolar_factura(sol(),safe=False);assert env.q.store.eta(j['id'],5) is None
def test_eta_sums_waiting(env):
    env.q.encolar_factura(sol('1'),safe=False);j,_=env.q.encolar_factura(sol('2'),safe=False)
    env.q.iniciar_cola();assert env.q.store.eta(j['id'],5)==(10,15)
def test_eta_no_guarantee_after_alta(env):
    env.q.encolar_alta(alta(),safe=False);j,_=env.q.encolar_factura(sol(),safe=False)
    env.q.iniciar_cola();assert env.q.store.eta(j['id'],5) is None
def test_config_change_not_mutate_job_time(env):
    j,_=env.q.encolar_factura(sol(),safe=False);env.cfg['cola']['minutos_factura']=20
    env.q.iniciar_cola();assert env.q.store.eta(j['id'],5)==(5,10)

@pytest.mark.parametrize('field',list(DEFAULTS)[2:])
@pytest.mark.parametrize('bad',[0,-1,241,'no',None])
def test_eta_invalid_configs(field,bad):
    with pytest.raises(ValueError):ajustes_cola({'cola':{field:bad}})
@pytest.mark.parametrize('ticket',['','0','-1','1;DROP TABLE x','123\n456','a123','9'*21])
def test_invalid_ticket_rejected(ticket):
    with pytest.raises(ValueError):factura_datos(sol(ticket))
@pytest.mark.parametrize('mail',['','a@b','a@b.com\r\nBcc:x@y.com','a@b.com,c@d.com','a..b@c.com'])
def test_invalid_mail_rejected(mail):
    with pytest.raises(Exception):factura_datos(sol(email=mail))
def test_payment_missing_not_default():
    s=sol();s.forma_pago=''
    with pytest.raises(ValueError):factura_datos(s)

def test_safe_notifications_simulated(env):
    env.q.encolar_factura(sol(),safe=True);assert env.q.store.notices()[0]['estado']=='SIMULADA'
def test_real_notice_sends_only_once(env):
    env.q.encolar_factura(sol(),safe=False);sender=Mock();sender.enviar.return_value={'id':'fake-id'}
    env.q.avisos.sender=sender;assert env.q.avisos.enviar_una();assert not env.q.avisos.enviar_una()
    assert sender.enviar.call_count==1 and env.q.store.notices()[0]['estado']=='ENVIADA'
def test_safe_real_notice_optin_marked_test(env):
    env.cfg['cola']['avisar_en_modo_seguro']=True
    env.q.encolar_factura(sol(),safe=True);row=env.q.store.notices()[0]
    assert row['estado']=='PENDIENTE';assert 'PRUEBA' in row['cuerpo'];assert 'No se autoriza' in row['cuerpo']
def test_disabled_notices_not_sent(env):
    env.cfg['cola']['avisar_cliente']=False;env.q.encolar_factura(sol(),safe=False)
    assert env.q.store.notices()[0]['estado']=='SIMULADA';assert not env.q.avisos.enviar_una()
def test_notification_failure_does_not_repeat_invoice(env):
    sender=Mock();sender.enviar.side_effect=ErrorEnvioAlerta('No token')
    env.q.avisos.sender=sender
    j,_=env.q.encolar_factura(sol(),safe=False);env.q.iniciar_cola();env.q.ejecutar_una()
    env.q.avisos.enviar_una();assert env.bot.invoice.call_count==1
    assert env.q.store.get(j['id'])['estado']=='ENVIO_SOLICITADO'
    assert any(n['estado']=='ERROR_ENVIO' for n in env.q.store.notices())
def test_network_timeout_never_auto_retried(env):
    sender=Mock();sender.enviar.side_effect=ErrorEnvioAlerta('Timeout',incierto=True)
    env.q.avisos.sender=sender;env.q.encolar_factura(sol(),safe=False)
    env.q.avisos.enviar_una();assert not env.q.avisos.enviar_una()
    assert env.q.store.notices()[0]['estado']=='ENVIO_INCIERTO';assert sender.enviar.call_count==1
def test_no_gmail_response_id_is_uncertain(env):
    sender=Mock();sender.enviar.return_value={};env.q.avisos.sender=sender
    env.q.encolar_factura(sol(),safe=False);env.q.avisos.enviar_una()
    assert env.q.store.notices()[0]['estado']=='ENVIO_INCIERTO'
def test_terminal_supersedes_unsent_receipt(env):
    env.q.encolar_factura(sol(),safe=False);env.q.iniciar_cola();env.q.ejecutar_una()
    rows=env.q.store.notices();assert next(r for r in rows if r['evento']=='recibida')['estado']=='SUPERADA'
def test_updated_receipt_does_not_use_paused_eta(env):
    env.q.iniciar_cola();env.q.encolar_factura(sol(),safe=False);env.q.pausar()
    sender=Mock();sender.enviar.return_value={'id':'sim'};env.q.avisos.sender=sender;env.q.avisos.enviar_una()
    body=sender.enviar.call_args.args[0]['cuerpo'];assert 'No hay un tiempo' in body
    assert '5 a 10' not in body
def test_resend_only_mail(env):
    env.q.encolar_factura(sol(),safe=False);sender=Mock();sender.enviar.side_effect=ErrorEnvioAlerta('Error')
    env.q.avisos.sender=sender;env.q.avisos.enviar_una();row=env.q.store.notices()[0]
    env.q.avisos.reenviar(row['id']);sender.enviar.side_effect=None;sender.enviar.return_value={'id':'sent'}
    env.q.avisos.enviar_una();env.bot.invoice.assert_not_called();assert env.alta.accepts==0

def test_mail_has_loop_protection(env):
    env.q.encolar_factura(sol(),safe=False);row=env.q.store.notices()[0]
    em=construir_aviso('emisioncfdi@grupoary.com',row)
    assert em[HEADER_AVISO]=='1';assert em['Auto-Submitted']=='auto-generated'
    assert em['To']=='cliente@example.com';assert not em.get('Bcc')
def test_mail_header_injection_blocked(env):
    env.q.encolar_factura(sol(),safe=False);row=env.q.store.notices()[0];row['asunto']+='\nBcc: attacker@example.com'
    with pytest.raises(ValueError):construir_aviso('emisioncfdi@grupoary.com',row)

@pytest.mark.parametrize('event',['recibida','procesando','completada','revision','revision_alta','faltantes','cancelada'])
def test_all_safe_templates_explain_no_action(event):
    job={'id':'a'*32,'tipo':'FACTURA','estado':'EN_COLA','seguro':True,'datos':{'estacion':'ARY I','ticket':'123'}}
    _,body=texto_cliente(job,event,DEFAULTS,eta=(5,10))
    assert 'PRUEBA INTERNA' in body and 'No se autoriza' in body

def test_receiving_and_mail_continue_during_screen_job(env):
    entered=threading.Event();release=threading.Event()
    active=[];max_active=[0];guard=threading.Lock()
    def invoice(*args,**kw):
        with guard:active.append(1);max_active[0]=max(max_active[0],len(active))
        entered.set();assert release.wait(3)
        with guard:active.pop()
        return 'ENVIO_SOLICITADO'
    env.bot.invoice.side_effect=invoice
    env.q.encolar_factura(sol(),safe=False);env.q.iniciar_cola()
    thread=threading.Thread(target=env.q.ejecutar_una);thread.start();assert entered.wait(1)
    # A second request is persisted AND a receipt is sent while the desktop call is blocked.
    j,_=env.q.encolar_factura(sol('124'),safe=False);assert j['estado']=='EN_COLA'
    sender=Mock();sender.enviar.return_value={'id':'fake'};env.q.avisos.sender=sender
    assert env.q.avisos.enviar_una();assert sender.enviar.called
    t2=threading.Thread(target=env.q.ejecutar_una);t2.start();t2.join(1)
    assert env.bot.invoice.call_count==1
    release.set();thread.join(2);assert not thread.is_alive();assert max_active[0]==1

def test_one_app_instance_lock(tmp_path):
    a=InstanciaBot(tmp_path).adquirir();b=InstanciaBot(tmp_path)
    try:
        with pytest.raises(RuntimeError):b.adquirir()
    finally:a.cerrar()
    b.adquirir();b.cerrar()
