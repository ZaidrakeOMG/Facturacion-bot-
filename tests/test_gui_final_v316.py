"""Actualización de regresión UI: el formulario admite trabajos mientras Gmail recibe.
Tk real bajo Xvfb. Todos los controles de Polaris y servicios Gmail son simulados.
"""
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
fake=types.ModuleType('arybot.gmail_client')
for key in ('GmailClient','NeedGmailAuth','WrongGmailAccount','HistoryExpired'):
    setattr(fake,key,type(key,(RuntimeError,),{}))
with patch.dict(sys.modules,{'arybot.gmail_client':fake}):
    import arybot.gui as GUI
from arybot.cola_operaciones import ColaOperaciones
from arybot.cola_gui import ColaTab

class GuiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=tk.Tk();self.root.withdraw()
        def clean():
            for job in self.root.tk.call('after','info'):self.root.tk.call('after','cancel',job)
            self.root.destroy()
        self.addCleanup(clean)
        a=self.a=GUI.App.__new__(GUI.App);a.root=self.root;a.worker=Mock();a.worker.is_alive.return_value=True
        a._direct_busy=False;a._direct_frozen=[];a.alta_clientes_tab=None;a._direct_job_id=None
        a.cfg={'app':{'modo_prueba':True},'cola':{'avisar_cliente':False}};a.cfgm=SimpleNamespace(save=lambda:None)
        a.log=Mock();a.polaris=Mock();a.polaris.invoice.return_value='PRUEBA_OK'
        a.cola=ColaOperaciones(Path(self.tmp.name),a.cfg,a.polaris,a.log)
        a.mode_var=tk.BooleanVar(master=self.root,value=True);a.mode_check=ttk.Checkbutton(self.root,variable=a.mode_var)
        a.tab_test=ttk.Frame(self.root);a.tab_panel=ttk.Frame(self.root);a.tab_cfg=ttk.Frame(self.root)
        a._build_test();a.test_station.set('ARY I');a.test_rfc.set('AAA010101AAA');a.test_ticket.set('123');a.test_email.set('cliente@example.com')
        self.ps=[patch.object(GUI,'BASE',Path(self.tmp.name)),patch.object(GUI.messagebox,'showwarning'),
                 patch.object(GUI.messagebox,'showinfo'),patch.object(GUI.messagebox,'askyesno',return_value=True)]
        self.mocks=[p.start() for p in self.ps];self.confirm=self.mocks[-1]
        self.addCleanup(lambda:[p.stop() for p in reversed(self.ps)])
    def test_safe_button_shows_queue_and_no_timbra(self):
        self.assertIn('Encolar',self.a.btn_test.cget('text'));self.assertIn('NO TIMBRA',self.a.btn_test.cget('text'))
    def test_real_button_label(self):
        self.a.mode_var.set(False);self.assertIn('REAL',self.a.btn_test.cget('text'))
    def test_gmail_active_allows_queue_submission(self):
        self.a.test_polaris();self.assertEqual(len(self.a.cola.store.list()),1)
        self.a.worker.stop.assert_not_called();self.a.polaris.invoice.assert_not_called()
    def test_submit_does_not_directly_operate_screen(self):
        self.a.test_polaris();self.a.polaris.assert_not_called();self.assertIn('EN COLA',self.a.direct_status.get())
    def test_safe_snapshot_queued_as_safe(self):
        self.a.test_polaris();self.confirm.assert_not_called();self.assertTrue(self.a.cola.store.list()[0]['seguro'])
    def test_real_submission_requires_confirmation(self):
        self.a.mode_var.set(False);self.a.cfg['app']['modo_prueba']=False
        self.a.test_polaris();self.confirm.assert_called_once();self.assertFalse(self.a.cola.store.list()[0]['seguro'])
    def test_refused_real_does_not_enqueue(self):
        self.a.mode_var.set(False);self.confirm.return_value=False;self.a.test_polaris()
        self.assertFalse(self.a.cola.store.list())
    def test_missing_email_does_not_enqueue(self):
        self.a.test_email.set('');self.a.test_polaris();self.assertFalse(self.a.cola.store.list())
    def test_duplicate_click_only_one_job(self):
        self.a.test_polaris();self.a.test_polaris();self.assertEqual(len(self.a.cola.store.list()),1)
    def test_receipt_fields_kept_in_snapshot(self):
        self.a.test_payment.set('TRANSFERENCIA');self.a.test_polaris()
        data=self.a.cola.store.list()[0]['datos']
        self.assertEqual(data['ticket'],'123');self.assertEqual(data['correo_destino'],'cliente@example.com')
        self.assertEqual(data['forma_pago'],'TRANSFERENCIA')
    def test_form_not_globally_frozen_on_enqueue(self):
        self.a.test_polaris();self.assertFalse(self.a.btn_station.instate(['disabled']))
        self.assertFalse(self.a.mode_check.instate(['disabled']))
    def test_safe_job_stays_safe_after_toggle(self):
        self.a.test_polaris();self.a.mode_var.set(False);self.a.cfg['app']['modo_prueba']=False
        self.a.cola.iniciar_cola();self.a.cola.ejecutar_una()
        self.assertIs(self.a.polaris.invoice.call_args.kwargs['test_mode'],True)
    def test_aux_buttons_only_enqueue(self):
        self.a.test_station_only();self.a.test_payment_only();self.a.test_cleanup_only()
        self.assertEqual(len(self.a.cola.store.list()),3);self.a.polaris.test_station_only.assert_not_called()
        self.a.polaris.test_cleanup_only.assert_not_called();self.a.polaris.test_payment_only.assert_not_called()
    def test_queued_controls_use_current_data_then_freeze_snapshot(self):
        self.a.test_station_only();self.a.test_station.set('ARY VI')
        self.assertEqual(self.a.cola.store.list()[0]['datos']['estacion'],'ARY I')
    def test_alta_review_does_not_prevent_receiving_new_request(self):
        self.a.alta_clientes_tab=SimpleNamespace(bloquea_otros_flujos=True)
        self.a.test_polaris();self.assertEqual(len(self.a.cola.store.list()),1)
    def test_summary_still_requires_real_confirmation_with_data(self):
        self.a.mode_var.set(False);self.a.test_polaris();text=self.confirm.call_args.args[1]
        for value in ('ARY I','AAA010101AAA','123','cliente@example.com'):self.assertIn(value,text)
    def test_queue_tab_has_separate_mail_history(self):
        tab=ColaTab(self.root,self.a);tab.pack();self.root.update()
        self.assertEqual(len(tab.nb.tabs()),3);self.assertIn('PAUSADA',tab.status.get())
    def test_queue_config_can_set_estimate(self):
        tab=ColaTab(self.root,self.a);tab.fact.set(7);tab.alta.set(4);tab.margin.set(2);tab.save()
        self.assertEqual(self.a.cfg['cola']['minutos_factura'],7)
    def test_queue_config_rejects_invalid_estimate(self):
        tab=ColaTab(self.root,self.a);tab.fact.set(0);tab.save()
        self.assertNotIn('minutos_factura',self.a.cfg['cola'])
    def test_preview_does_not_enqueue_send(self):
        tab=ColaTab(self.root,self.a)
        with patch.object(tab,'_show') as show:
            tab.preview();self.assertIn('5 a 10 minutos',show.call_args.args[1])
        self.assertFalse(self.a.cola.store.notices())

if __name__=='__main__':unittest.main()
