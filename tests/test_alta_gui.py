"""Se ejecuta con Tk disponible; las interacciones con Polaris se simulan."""
import os
import tempfile
import threading
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import patch,Mock
from arybot.alta_gui import AltaClientesTab
from arybot.cola_operaciones import ColaOperaciones
from arybot.alta_clientes import AltaClientes


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name=='nt','Requiere escritorio Tk')
class GuiTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.geometry('900x620')
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        def cleanup_ui():
            # Las pruebas llaman _drain manualmente; cancelar todas las colas
            # evita temporizadores huérfanos entre los escritorios simulados.
            for job in self.root.tk.call('after','info'):
                self.root.tk.call('after','cancel',job)
            self.root.destroy()
        self.addCleanup(cleanup_ui)
        cfg={'app':{'modo_prueba':True}}
        bot=SimpleNamespace(base=Path(self.tmp.name),cfg=cfg,_lock=threading.Lock(),log=lambda s:None)
        self.app=SimpleNamespace(polaris=bot,worker=None,mode_var=tk.BooleanVar(value=True),test_station=tk.StringVar(value='ARY I'),
            tab_panel=ttk.Frame(self.root),tab_test=ttk.Frame(self.root),save_config=lambda **kw:None,log=lambda s:None)
        self.app.alta_service=AltaClientes(bot)
        self.app.cola=ColaOperaciones(Path(self.tmp.name),cfg,bot,lambda *_:None,alta=self.app.alta_service)
        self.other=ttk.Button(self.app.tab_test,text='Prueba factura')
        self.was_disabled=ttk.Button(self.app.tab_panel,text='Detener',state='disabled')
        self.tab=AltaClientesTab(self.root,self.app);self.tab.pack(fill='both',expand=True);self.root.update()
    def ready(self):
        self.tab.holding=True;self.tab.service.pendiente=object();self.tab.reviewed.set(True)
    def test_only_four_entries(self):self.assertEqual(len(self.tab.entries),4)
    def test_safe_default_blocks_accept(self):
        self.ready();self.tab._sync_buttons();self.assertTrue(self.tab.btn_accept.instate(['disabled']))
    def test_prepare_button_enabled_initially(self):self.assertFalse(self.tab.btn_prepare.instate(['disabled']))
    def test_real_mode_ready_enables_accept(self):
        self.ready();self.app.mode_var.set(False);self.tab._sync_buttons()
        self.assertFalse(self.tab.btn_accept.instate(['disabled']))
    def test_no_review_blocks(self):
        self.ready();self.app.mode_var.set(False);self.tab.reviewed.set(False)
        self.assertTrue(self.tab.btn_accept.instate(['disabled']))
    def test_busy_disables_both(self):
        self.ready();self.app.mode_var.set(False);self.tab.busy=True;self.tab._sync_buttons()
        self.assertTrue(self.tab.btn_accept.instate(['disabled']));self.assertTrue(self.tab.btn_prepare.instate(['disabled']))
    def test_no_pending_blocks(self):
        self.ready();self.app.mode_var.set(False);self.tab.service.pendiente=None;self.tab._sync_buttons()
        self.assertTrue(self.tab.btn_accept.instate(['disabled']))
    def test_other_controls_frozen_and_restored(self):
        self.tab._freeze();self.assertFalse(self.other.instate(['disabled']))
        self.tab._thaw();self.assertFalse(self.other.instate(['disabled']))
        self.assertTrue(self.was_disabled.instate(['disabled']))
    def test_entries_locked_during_review(self):
        self.ready();self.tab._sync_buttons()
        self.assertTrue(all(e.instate(['disabled']) for e in self.tab.entries))
    def test_switching_safe_mode_blocks_ready_accept(self):
        self.ready();self.app.mode_var.set(False);self.assertFalse(self.tab.btn_accept.instate(['disabled']))
        self.app.mode_var.set(True);self.assertTrue(self.tab.btn_accept.instate(['disabled']))
    def test_scroll_present_for_small_screen(self):
        self.root.update();self.assertTrue(self.tab.canvas.bbox('all')[3]>0)
        self.tab.canvas.yview_moveto(1);self.root.update()
        self.assertAlmostEqual(self.tab.canvas.yview()[1],1.0,places=2)
    def test_invalid_data_no_worker(self):
        with patch('arybot.alta_gui.messagebox.showwarning') as warn:
            self.tab.prepare();self.assertFalse(warn.called);self.assertIn('RFC',self.tab.status.get())
        self.assertFalse(self.tab.busy)
    def test_enqueued_alta_does_not_stop_gmail(self):
        self.app.worker=Mock();self.app.worker.is_alive.return_value=True
        self.tab.rfc.set('AAA010101AAA');self.tab.idcif.set('12345678901')
        self.tab.phone.set('8711234567');self.tab.email.set('cliente@example.com')
        self.tab.prepare()
        self.assertTrue(self.tab.busy);self.app.worker.stop.assert_not_called()
        self.assertEqual(self.app.cola.store.get(self.tab.job_id)['estado'],'EN_COLA')
    def test_heading_names_efectivo_not_credito(self):
        labels=[c.cget('text') for c in self.tab.body.winfo_children() if isinstance(c,ttk.Label)]
        text=' '.join(str(v) for v in labels)
        self.assertIn('Clientes de Efectivo',text);self.assertNotIn('Clientes de Crédito',text)
    def test_unreadable_number_display_is_not_empty_or_confirmed(self):
        from arybot.cliente_model import AltaCliente
        sol=AltaCliente.crear('ARY I','AAA010101AAA','12345678901','8711234567','cliente@example.com')
        job,_=self.app.cola.encolar_alta(sol,safe=True)
        self.app.cola.store.finish(job['id'],'PREPARADA_ALTA',result={'nombre':'DEMO','codigo_postal':'27000','numero_legible':False},hold=True)
        self.tab.load_job(self.app.cola.store.get(job['id']));self.tab._drain()
        self.assertIn('NO guardada',self.tab.numero.get());self.assertTrue(self.tab.btn_accept.instate(['disabled']))

    def test_no_validation_network(self):
        self.tab.rfc.set('AAA010101AAA');self.tab.idcif.set('12345678901');self.tab.phone.set('8711234567');self.tab.email.set('cliente@example.com')
        self.assertEqual(self.tab._sol().rfc,'AAA010101AAA')

if __name__=='__main__':unittest.main(verbosity=2)
