"""Regresión de Número no expuesto como Edit. Controles Windows SIMULADOS.

No ejecuta Polaris, guarda clientes reales ni usa conexiones SQL. Reproduce la
suposición que falló en v3.1.3 con Static/DBText y con un dibujo sin HWND propio.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_efectivo_regression import DesktopScene, SceneTextIO, SOL
from arybot.alta_clientes import AltaClientes, AltaClienteError, PantallaAlta, ControlAltaNoIdentificado


class NumeroTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.scene=DesktopScene()
        self.bot=self.scene.bot(self.tmp.name)
        self.ui=PantallaAlta(self.bot,texto=SceneTextIO(self.scene))
        self.logs=[];self.bot.log=self.logs.append
        self.patches=[
            patch('arybot.polaris.win32gui',self.scene,create=True),
            patch('arybot.polaris.Desktop',self.scene.desktop,create=True),
            patch('arybot.polaris.pyautogui',SimpleNamespace(click=self.scene.click,failSafeCheck=lambda:None),create=True),
            patch('arybot.alta_clientes.time.sleep',self.scene.sleep),
            patch('arybot.alta_clientes.time.monotonic',lambda:self.scene.now),
        ]
        for p in self.patches:p.start()
        self.addCleanup(lambda:[p.stop() for p in reversed(self.patches)])

    def number_class(self,cls):
        self.scene.nodes[self.scene.fields['numero']]['cls']=cls

    def service(self):return AltaClientes(self.bot,ui=self.ui)

    def windowless_number(self):
        # El dibujo persiste visualmente, pero no hay control de texto con HWND.
        self.number_class('TGraphicNumberHost')
        self.ui._numero_uia=lambda form:None

    def test_static_number_read_without_click(self):
        self.number_class('Static')
        self.assertEqual(self.ui.snapshot(1,100)['numero'],'23690')
        self.assertEqual(self.scene.clicks,[])

    def test_dbtext_number_read_without_click(self):
        self.number_class('TDBText')
        self.assertEqual(self.ui.snapshot(1,100)['numero'],'23690')
        self.assertEqual(self.scene.clicks,[])

    def test_label_number_read_without_click(self):
        self.number_class('TDBLabel')
        self.assertEqual(self.ui.snapshot(1,100)['numero'],'23690')

    def test_control_without_hwnd_is_unknown_not_empty(self):
        self.windowless_number()
        val=self.ui.snapshot(1,100)['numero']
        self.assertIsNone(val);self.assertNotEqual(val,'')

    def test_blank_static_is_unknown_not_empty(self):
        self.number_class('Static');self.scene.values['numero']='';self.scene.update_values()
        self.ui._numero_uia=lambda form:None
        self.assertIsNone(self.ui.snapshot(1,100)['numero'])

    def test_field_caption_is_not_taken_as_client_number(self):
        self.number_class('TLabel');self.scene.values['numero']='Número';self.scene.update_values()
        self.ui._numero_uia=lambda form:None
        self.assertIsNone(self.ui.snapshot(1,100)['numero'])

    def test_number_as_button_is_never_a_text_field(self):
        self.number_class('TButton')
        with self.assertRaises(ControlAltaNoIdentificado):self.ui._field(1,100,'numero')
        self.assertEqual(self.scene.clicks,[])

    def test_static_is_not_allowed_for_user_input(self):
        for name in ('rfc','idcif','telefono','correo'):
            h=self.scene.fields[name];old=self.scene.nodes[h]['cls']
            try:
                self.scene.nodes[h]['cls']='Static'
                with self.subTest(name=name),self.assertRaises(ControlAltaNoIdentificado):
                    self.ui.escribir(1,100,name,'NO PEGAR')
            finally:self.scene.nodes[h]['cls']=old
        self.assertEqual(self.scene.clicks,[])

    def test_missing_writable_field_still_stops_before_new(self):
        self.scene.nodes[self.scene.fields['rfc']]['cls']='TPanel'
        with self.assertRaises(ControlAltaNoIdentificado):self.service().preparar(SOL)
        self.assertEqual(self.scene.actions,[])

    def test_number_is_never_a_paste_destination(self):
        with self.assertRaises(AltaClienteError):self.ui.escribir(1,100,'numero','123')
        self.assertEqual(self.scene.clicks,[])

    def test_snapshot_read_error_in_number_is_unknown_only_for_number(self):
        original=self.ui._read
        def read(h):
            if h==self.scene.fields['numero']:
                from arybot.alta_clientes import LecturaControlAltaError
                raise LecturaControlAltaError('number unreadable')
            return original(h)
        self.ui._read=read;self.ui._numero_uia=lambda form:None
        val=self.ui.snapshot(1,100)
        self.assertIsNone(val['numero']);self.assertEqual(val['rfc'],'BBB010101BBB')

    def test_windowless_preparation_verifies_insert_and_captures_four(self):
        self.windowless_number()
        res=self.service().preparar(SOL)
        self.assertEqual(res['estado'],'PREPARADA_SIN_GUARDAR')
        self.assertFalse(res['numero_legible'])
        self.assertEqual(self.scene.actions,['new','write:rfc','write:idcif','lookup','write:telefono','write:correo'])
        self.assertEqual(self.scene.mode,'Insert Record')
        self.assertNotIn('accept',self.scene.actions)

    def test_static_number_preparation_completes_without_accept(self):
        self.number_class('TDBText');self.ui._numero_uia=lambda form:None
        res=self.service().preparar(SOL)
        self.assertFalse(res['numero_legible']);self.assertNotIn('accept',self.scene.actions)

    def test_unknown_number_needs_explicit_insert_not_only_button_states(self):
        self.windowless_number()
        self.ui.nuevo(1,100)
        self.scene.mode='' # buttons still exactly like Insert
        with self.assertRaisesRegex(AltaClienteError,'Insert Record'):self.ui.exigir_nuevo(1,100)

    def test_unknown_number_browse_cannot_accept(self):
        self.windowless_number()
        with self.assertRaises(AltaClienteError):self.ui.exigir_nuevo(1,100)
        self.assertEqual(self.scene.actions,[])

    def test_unknown_number_edit_cannot_accept(self):
        self.windowless_number();self.scene.mode='Edit Record'
        with self.assertRaises(AltaClienteError):self.service().preparar(SOL)
        self.assertEqual(self.scene.actions,[])

    def test_name_containing_insert_record_cannot_fake_new_state(self):
        self.windowless_number();self.scene.nodes[60]['cls']='TPanel'
        self.scene.mode=''
        self.scene.texttree=lambda form:'NOMBRE CLIENTE Insert Record'
        self.bot._window_tree_text=self.scene.texttree
        self.assertEqual(self.ui._mode_text(1,100),'')
        with self.assertRaises(AltaClienteError):self.service().preparar(SOL)
        self.assertNotIn('write:rfc',self.scene.actions)

    def test_known_number_after_new_still_blocks(self):
        self.ui.nuevo(1,100)
        self.scene.values['numero']='77';self.scene.update_values()
        with self.assertRaises(AltaClienteError):self.ui.exigir_nuevo(1,100)
        self.assertNotIn('write:rfc',self.scene.actions)

    def test_existing_same_rfc_still_blocks_with_unknown_number(self):
        self.windowless_number();self.scene.values['rfc']=SOL.rfc;self.scene.update_values()
        with self.assertRaisesRegex(AltaClienteError,'ya está visible'):self.service().preparar(SOL)
        self.assertEqual(self.scene.actions,[])

    def test_fail_safe_blocked_accept_when_number_unknown(self):
        self.windowless_number();s=self.service();s.preparar(SOL)
        with self.assertRaisesRegex(AltaClienteError,'SEGURO'):
            s.aceptar(SOL,autorizado=True,inexistencia_revisada=True)
        self.assertNotIn('accept',self.scene.actions)

    def test_real_accept_number_unreadable_remains_pending_no_retry(self):
        self.windowless_number();s=self.service();s.preparar(SOL)
        self.bot.cfg['app']['modo_prueba']=False
        with self.assertRaisesRegex(AltaClienteError,'no se confirmó el número'):
            s.aceptar(SOL,autorizado=True,inexistencia_revisada=True)
        self.assertEqual(self.scene.actions.count('accept'),1)
        self.assertEqual(s.diario.leer()[SOL.clave_local()]['estado'],'ACEPTAR_PENDIENTE_DE_VERIFICAR')
        with self.assertRaises(AltaClienteError):s.aceptar(SOL,autorizado=True,inexistencia_revisada=True)
        self.assertEqual(self.scene.actions.count('accept'),1)

    def test_static_number_after_authorized_accept_can_confirm(self):
        self.number_class('TDBText');self.ui._numero_uia=lambda form:None
        s=self.service();s.preparar(SOL);self.bot.cfg['app']['modo_prueba']=False
        res=s.aceptar(SOL,autorizado=True,inexistencia_revisada=True)
        self.assertEqual(res['numero_cliente'],'12345');self.assertEqual(self.scene.actions.count('accept'),1)

    def test_foreign_overlay_stops_even_with_unknown_number(self):
        self.windowless_number();self.scene.overlay=True
        with self.assertRaisesRegex(AltaClienteError,'tapa'):self.ui.snapshot(1,100)
        self.assertEqual(self.scene.actions,[])

    def test_diagnostic_is_local_and_has_no_client_values(self):
        self.windowless_number();self.ui.snapshot(1,100)
        p=Path(self.tmp.name)/'logs'/'diagnostico_alta_controles.json'
        text=p.read_text();d=json.loads(text)
        self.assertEqual(d['campo_no_identificado'],'numero');self.assertEqual(d['version'],'3.1.5')
        for value in ['BBB010101BBB','CLIENTE EXISTENTE','23690','27000',SOL.rfc,SOL.telefono,SOL.correo]:
            self.assertNotIn(value,text)
        self.assertFalse(any('text' in k.lower() for row in d['controles'] for k in row))
        self.assertFalse((Path(self.tmp.name)/'data'/'altas_intentos_locales.json').exists())

    def test_diagnostic_write_failure_does_not_fake_blank_or_click(self):
        self.windowless_number()
        with patch('pathlib.Path.write_text',side_effect=PermissionError('simulated')):
            res=self.ui.snapshot(1,100)
        self.assertIsNone(res['numero']);self.assertEqual(self.scene.clicks,[])

    def test_logs_do_not_repeat_number_warning_each_snapshot(self):
        self.windowless_number()
        self.ui.snapshot(1,100);self.ui.snapshot(1,100)
        self.assertEqual(sum('Número no legible en Windows' in x for x in self.logs),1)

    def test_readable_empty_edit_retains_compatibility(self):
        self.ui.nuevo(1,100)
        self.assertEqual(self.ui.snapshot(1,100)['numero'],'')


class UiaNumberTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.scene=DesktopScene();self.bot=self.scene.bot(self.tmp.name);self.ui=PantallaAlta(self.bot,texto=SceneTextIO(self.scene))
        self.com=[]
        self.pythoncom=SimpleNamespace(CoInitialize=lambda:self.com.append('init'),CoUninitialize=lambda:self.com.append('uninit'))
        self.pc=patch.dict(sys.modules,{'pythoncom':self.pythoncom});self.pc.start();self.addCleanup(self.pc.stop)

    def control(self,value='00123',kind='Text',pid=99,visible=True,rect=None):
        rect=rect or SimpleNamespace(left=463,top=279,right=520,bottom=297)
        return SimpleNamespace(element_info=SimpleNamespace(process_id=pid,control_type=kind),
            is_visible=lambda:visible,rectangle=lambda:rect,window_text=lambda:value,get_value=lambda:value)

    def read(self,controls,rootpid=99):
        root=SimpleNamespace(element_info=SimpleNamespace(process_id=rootpid),descendants=lambda:controls)
        desktop=lambda **k:SimpleNamespace(window=lambda **kw:SimpleNamespace(wrapper_object=lambda:root))
        with patch('arybot.polaris.Desktop',desktop,create=True):return self.ui._numero_uia(100)

    def test_valid_text_number(self):self.assertEqual(self.read([self.control()]),'00123')
    def test_edit_value(self):self.assertEqual(self.read([self.control('777',kind='Edit')]),'777')
    def test_blank_static_returns_unknown(self):self.assertIsNone(self.read([self.control('')]))
    def test_caption_returns_unknown(self):self.assertIsNone(self.read([self.control('Número')]))
    def test_foreign_process_ignored(self):self.assertIsNone(self.read([self.control(pid=888)]))
    def test_foreign_root_ignored(self):self.assertIsNone(self.read([self.control()],rootpid=888))
    def test_hidden_ignored(self):self.assertIsNone(self.read([self.control(visible=False)]))
    def test_button_ignored(self):self.assertIsNone(self.read([self.control(kind='Button')]))
    def test_outside_number_region_ignored(self):
        r=SimpleNamespace(left=563,top=432,right=682,bottom=451) # CP
        self.assertIsNone(self.read([self.control('27000',rect=r)]))
    def test_two_different_numbers_block(self):
        with self.assertRaisesRegex(AltaClienteError,'ambigua'):self.read([self.control('1'),self.control('2')])
    def test_duplicate_provider_same_number_ok(self):self.assertEqual(self.read([self.control('7'),self.control('7')]),'7')
    def test_com_balanced(self):
        self.read([self.control()]);self.assertEqual(self.com,['init','uninit'])
    def test_missing_provider_returns_unknown_not_blank(self):
        with patch('arybot.polaris.Desktop',side_effect=RuntimeError('unavailable'),create=True):
            self.assertIsNone(self.ui._numero_uia(100))
        self.assertEqual(self.com,['init','uninit'])


if __name__=='__main__':unittest.main()
