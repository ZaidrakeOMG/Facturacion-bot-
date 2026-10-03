"""Tk real con Xvfb; notificador/transporte y Polaris simulados."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from tkinter import ttk
from arybot.alertas import AlertasInternas
from arybot.alertas_gui import AlertasTab

class TabTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=tk.Tk();self.root.withdraw();self.addCleanup(self.root.destroy)
        self.s=AlertasInternas(Path(self.tmp.name),{'app':{'modo_prueba':True}},Mock(),sender=Mock())
        self.app=SimpleNamespace(alertas=self.s)
        self.tab=AlertasTab(self.root,self.app);self.tab.pack(fill='both',expand=True)
        self.patches=[patch('arybot.alertas_gui.messagebox.showwarning'),patch('arybot.alertas_gui.messagebox.showerror'),
                      patch('arybot.alertas_gui.messagebox.askyesno',return_value=True)]
        self.warn,self.err,self.confirm=[p.start() for p in self.patches]
        self.addCleanup(lambda:[p.stop() for p in reversed(self.patches)])
    def add(self,e='ti@grupoary.com'):
        self.tab.email.set(e);self.tab.agregar()
    def enable(self):
        self.add();self.tab.vars['activas'].set(True);self.assertTrue(self.tab.guardar())
    def test_initial_disabled_empty(self):
        self.assertFalse(self.tab.vars['activas'].get());self.assertEqual(self.tab.list.size(),0)
    def test_add_internal(self):
        self.add();self.assertEqual(self.tab.list.get(0,'end'),('ti@grupoary.com',))
    def test_reject_external(self):
        self.add('persona@gmail.com');self.assertEqual(self.tab.list.size(),0);self.warn.assert_called_once()
    def test_add_not_saved_until_button(self):
        self.add();self.assertEqual(self.s.ajustes()['destinatarios'],[])
    def test_remove(self):
        self.add();self.tab.list.selection_set(0);self.tab.quitar();self.assertEqual(self.tab.list.size(),0)
    def test_save_activation_confirmation(self):
        self.enable();self.confirm.assert_called_once();self.assertTrue(self.s.ajustes()['activas'])
    def test_cancel_activation_no_persist(self):
        self.add();self.tab.vars['activas'].set(True);self.confirm.return_value=False
        self.assertFalse(self.tab.guardar());self.assertFalse(self.s.ajustes()['activas'])
    def test_preview_never_sends(self):
        self.tab.ejemplo();self.assertEqual(self.s.cola.recientes()[0]['estado'],'SIMULADA')
        self.s.sender.enviar.assert_not_called();self.confirm.assert_not_called()
    def test_real_test_needs_confirmation(self):
        self.enable();self.confirm.reset_mock();self.confirm.return_value=False;self.tab.probar()
        self.confirm.assert_called_once();self.assertEqual(self.s.cola.recientes(),[])
    def test_real_test_queued_not_retimbrado(self):
        self.enable();self.tab.probar();self.assertEqual(self.s.cola.recientes()[0]['estado'],'PENDIENTE')
        self.s.sender.enviar.assert_not_called()
    def test_safe_switch_default_false(self):self.assertFalse(self.tab.vars['enviar_en_modo_seguro'].get())
    def test_missing_save_recipient_warns(self):
        self.tab.email.set('ti@grupoary.com');self.assertFalse(self.tab.guardar());self.warn.assert_called_once()
    def test_selection_survives_refresh(self):
        i=self.s.prueba();self.tab.actualizar();self.tab.tree.selection_set(i);self.tab.actualizar()
        self.assertEqual(self.tab.tree.selection(),(i,))
    def test_list_auto_scroll_body(self):
        self.root.deiconify();self.root.geometry('900x620');self.root.update()
        self.assertGreater(int(self.tab.canvas.bbox('all')[3]),self.tab.canvas.winfo_height())
    def test_enviar_disabled_no_config(self):
        self.tab.probar();self.warn.assert_called_once();self.assertEqual(self.s.cola.recientes(),[])

if __name__=='__main__':unittest.main()
