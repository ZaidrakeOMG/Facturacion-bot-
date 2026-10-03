"""Regresión v3.1.9: Gmail admite solicitudes, la cola opera y las alertas siguen separadas."""
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from arybot.cola_operaciones import ColaOperaciones
from arybot.db import DB
from arybot.processor import Processor
fake=types.ModuleType('arybot.gmail_client')
for key in ('HistoryExpired','NeedGmailAuth'):setattr(fake,key,type(key,(RuntimeError,),{}))
with patch.dict(sys.modules,{'arybot.gmail_client':fake}):
    from arybot.worker import BotWorker

BODY='Estación: ARY I\nRFC: AAA010101AAA\nFolio: 123\nForma de pago: DEBITO\nCorreo: factura@example.com'
ALTA='Estación: ARY I\nRFC: AAA010101AAA\nidCIF: 12345678901\nTeléfono: 8711234567\nCorreo: alta@example.com'

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);base=Path(self.tmp.name)
        self.cfg={'app':{'modo_prueba':False,'intervalo_segundos':5,'correo':'emisioncfdi@grupoary.com'},
                  'ocr':{},'gmail':{'labels':{'procesando':'PROCESANDO','esperando':'ESPERANDO'}}}
        self.db=DB(base/'data'/'old.sqlite3');self.g=Mock();self.p=Mock();self.alert=Mock();self.logs=Mock()
        self.p.invoice.return_value='ENVIO_SOLICITADO';self.p.etapa_alerta='FACTURACION: prueba'
        self.q=ColaOperaciones(base,self.cfg,self.p,self.logs,alertas=self.alert,db=self.db)
        self.proc=Processor(base,self.cfg,self.db,self.g,self.p,self.logs,alertas=self.alert,cola=self.q)
        self.g.basics.return_value={'subject':'FACTURA ARY I','from_email':'solicitante@example.com'}
        self.g.body_text.return_value=BODY;self.g.download_attachments.return_value=[]
        self.msg={'id':'m1','threadId':'t1','payload':{'headers':[]}}
    def test_gmail_enqueues_never_calls_polaris(self):
        self.assertEqual(self.proc.process(self.msg),'EN_COLA');self.p.invoice.assert_not_called()
    def test_error_alert_in_screen_thread_only(self):
        self.p.invoice.side_effect=RuntimeError('Fake fallo')
        self.proc.process(self.msg);self.alert.reportar.assert_not_called()
        self.q.iniciar_cola();self.q.ejecutar_una();self.alert.reportar.assert_called_once()
        self.assertIs(self.alert.reportar.call_args.kwargs['bot'],self.p)
    def test_mail_stage_error_internal_category(self):
        self.p.etapa_alerta='ENVIO_CORREO: Aceptar';self.p.invoice.side_effect=RuntimeError('Fake')
        self.proc.process(self.msg);self.q.iniciar_cola();self.q.ejecutar_una()
        self.assertEqual(self.alert.reportar.call_args.args[0],'ENVIO_FACTURA')
    def test_success_no_internal_error(self):
        self.proc.process(self.msg);self.q.iniciar_cola();self.q.ejecutar_una();self.alert.reportar.assert_not_called()
    def test_label_failure_not_failure_of_invoice(self):
        self.g.add_label.side_effect=RuntimeError('Gmail label fake')
        self.assertEqual(self.proc.process(self.msg),'EN_COLA');self.assertEqual(len(self.q.store.list()),1)
    def test_internal_message_ignored(self):
        self.msg['payload']['headers']=[{'name':'X-ARY-Alerta-Interna','value':'1'}]
        self.assertEqual(self.proc.process(self.msg),'AVISO_INTERNO');self.assertFalse(self.q.store.list())
    def test_customer_notice_ignored(self):
        self.msg['payload']['headers']=[{'name':'X-ARY-Aviso-Cliente','value':'1'}]
        self.assertEqual(self.proc.process(self.msg),'AVISO_INTERNO')
    def test_subject_notice_ignored(self):
        self.g.basics.return_value['subject']='Re: [ARY Solicitud] FACTURA'
        self.assertEqual(self.proc.process(self.msg),'AVISO_INTERNO')
    def test_out_of_office_ignored(self):
        self.msg['payload']['headers']=[{'name':'Auto-Submitted','value':'auto-replied'}]
        self.assertEqual(self.proc.process(self.msg),'AVISO_INTERNO')
    def test_no_manual_force_duplicate(self):
        self.proc.process(self.msg);self.proc.process(self.msg,force=True)
        self.assertEqual(len(self.q.store.list()),1);self.assertEqual(len(self.q.store.notices()),1)
    def test_old_history_not_reemitted(self):
        self.db.crear('m1','t1','x@example.com','FACTURA')
        self.assertEqual(self.proc.process(self.msg),'YA_REGISTRADA');self.p.invoice.assert_not_called()
    def test_new_email_same_ticket_gets_review_not_second_job(self):
        self.proc.process(self.msg);self.msg['id']='m2'
        self.assertEqual(self.proc.process(self.msg),'DUPLICADA')
        self.assertEqual(sum(r['estado']=='EN_COLA' for r in self.q.store.list()),1)
    def test_receipt_to_requester_invoice_to_selected_email(self):
        self.proc.process(self.msg);job=self.q.store.list()[0]
        self.assertEqual(job['correo'],'solicitante@example.com')
        self.assertEqual(job['datos']['correo_destino'],'factura@example.com')
    def test_missing_payment_not_invented(self):
        self.g.body_text.return_value=BODY.replace('Forma de pago: DEBITO\n','')
        self.assertEqual(self.proc.process(self.msg),'ESPERANDO_DATOS');self.p.invoice.assert_not_called()
    def test_missing_folio_receives_request_for_data(self):
        self.g.body_text.return_value=BODY.replace('Folio: 123\n','')
        self.assertEqual(self.proc.process(self.msg),'ESPERANDO_DATOS')
        self.assertEqual(self.q.store.notices()[0]['evento'],'faltantes')
    def test_alta_four_fields_is_supported(self):
        self.g.basics.return_value['subject']='ALTA CLIENTE ARY I';self.g.body_text.return_value=ALTA
        self.assertEqual(self.proc.process(self.msg),'EN_COLA')
        job=self.q.store.list()[0];self.assertEqual(job['tipo'],'ALTA')
        self.assertEqual(job['datos']['idcif'],'12345678901');self.p.invoice.assert_not_called()
    def test_alta_needs_station(self):
        self.g.basics.return_value['subject']='ALTA CLIENTE';self.g.body_text.return_value=ALTA.replace('Estación: ARY I\n','')
        self.assertEqual(self.proc.process(self.msg),'ESPERANDO_DATOS')
    def test_alta_safe_snapshot(self):
        self.cfg['app']['modo_prueba']=True;self.g.basics.return_value['subject']='ALTA CLIENTE ARY I';self.g.body_text.return_value=ALTA
        self.proc.process(self.msg);self.assertTrue(self.q.store.list()[0]['seguro'])
    def test_auto_sender_not_reprocessed(self):
        self.g.basics.return_value['from_email']='emisioncfdi@grupoary.com'
        self.assertEqual(self.proc.process(self.msg),'AVISO_INTERNO')
    def test_worker_reads_second_after_first_failed_screen_job(self):
        # Receiving only: an error state in one job cannot stop intake of another.
        proc=Mock();proc.process.side_effect=['ERROR','EN_COLA']
        db=Mock();db.meta_get.return_value='h1'
        self.g.new_messages_from_history.return_value=([{'id':'a'},{'id':'b'}],'h2')
        worker=BotWorker(self.cfg,db,self.g,proc,self.logs)
        worker.wake_event.wait=Mock(side_effect=lambda *_:worker.stop())
        worker.run();self.assertEqual(proc.process.call_count,2)
        db.meta_set.assert_called_with('gmail_history_id','h2')
    def test_worker_login_error_no_screen(self):
        self.g.connect.side_effect=RuntimeError('Fake login')
        worker=BotWorker(self.cfg,self.db,self.g,self.proc,self.logs);worker.run()
        self.p.invoice.assert_not_called();self.alert.reportar.assert_called_once()
    def test_failed_reception_keeps_checkpoint(self):
        proc=Mock();proc.process.side_effect=RuntimeError('Fake reception')
        db=Mock();db.meta_get.return_value='h1';self.g.new_messages_from_history.return_value=([{'id':'a'}],'h2')
        w=BotWorker(self.cfg,db,self.g,proc,self.logs);w.wake_event.wait=Mock(side_effect=lambda *_:w.stop())
        w.run();db.meta_set.assert_not_called()

if __name__=='__main__':unittest.main()
