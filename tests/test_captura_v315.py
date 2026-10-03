"""Lectura retardada, máscaras, reintento y copia REAL simulados, no Polaris real."""
import ast
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from arybot.alta_clientes import AltaClientes, PantallaAlta, AltaClienteError
from arybot.alta_texto import normalizar_campo, TextoControlError
from test_efectivo_regression import DesktopScene, SceneTextIO, SOL


class FlexibleIO(SceneTextIO):
    def __init__(self, scene):
        super().__init__(scene)
        self.transforms={};self.due={};self.delay=0.;self.ignore_paste=False;self.ignore_keys=False
        self.stale_read=set();self.fail_copy=False;self.copy_calls=[];self.writes=[]
        self.focus_loss=False;self.before_keys=None;self.paste_error=None
    def _name(self,h):return next(k for k,v in self.scene.fields.items() if h==v)
    def leer(self,h):
        name=self._name(h)
        if name in self.stale_read:return ''
        if name in self.due and self.scene.now<self.due[name]:return ''
        return self.transforms.get(name,lambda x:x)(super().leer(h))
    def pegar(self,h,value,guard):
        guard();name=self._name(h);self.writes.append(('paste',name))
        if self.paste_error:raise TextoControlError(*self.paste_error)
        if not self.ignore_paste:
            super().pegar(h,value,guard)
            self.due[name]=self.scene.now+self.delay
        if self.focus_loss:self.scene.focus=self.scene.fields['nombre']
    def teclear(self,h,value,guard):
        name=self._name(h);self.writes.append(('keys',name));guard()
        if self.before_keys:self.before_keys()
        guard()
        if not self.ignore_keys:
            self.scene.paste(value);self.due.pop(name,None)
    def copiar(self,h,guard):
        self.copy_calls.append(self._name(h));guard()
        if self.fail_copy:raise TextoControlError('SIN_COPIA_NUEVA_DEL_CONTROL')
        return super().copiar(h,guard)


class CapturaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.scene=DesktopScene();self.bot=self.scene.bot(self.tmp.name)
        self.logs=[];self.bot.log=self.logs.append
        self.io=FlexibleIO(self.scene);self.ui=PantallaAlta(self.bot,texto=self.io)
        self.ps=[patch('arybot.polaris.win32gui',self.scene,create=True),
            patch('arybot.polaris.Desktop',self.scene.desktop,create=True),
            patch('arybot.polaris.pyautogui',SimpleNamespace(click=self.scene.click,failSafeCheck=lambda:None),create=True),
            patch('arybot.alta_clientes.time.sleep',self.scene.sleep),
            patch('arybot.alta_clientes.time.monotonic',lambda:self.scene.now)]
        for p in self.ps:p.start()
        self.addCleanup(lambda:[p.stop() for p in reversed(self.ps)])
    def service(self):return AltaClientes(self.bot,ui=self.ui)
    def new(self):self.ui.nuevo(1,100)
    def write(self,name='rfc',value=None):self.ui.escribir(1,100,name,value or SOL.rfc)
    def test_delayed_control_waits_without_retyping(self):
        self.new();self.io.delay=1.1;self.write()
        self.assertEqual(self.io.writes,[('paste','rfc')]);self.assertGreaterEqual(self.scene.now,1.1)
    def test_old_single_comparison_fails_on_lowercase_new_succeeds(self):
        self.new();self.io.transforms['rfc']=str.lower
        h,x,y=self.ui._field(1,100,'rfc');self.scene.click(x,y);self.scene.paste(SOL.rfc)
        self.assertNotEqual(self.ui._read(h),SOL.rfc) # condición que bloqueaba la versión anterior
        self.write();self.assertNotIn('accept',self.scene.actions)
    def test_mask_trailing_padding_only(self):
        self.new();self.io.transforms['rfc']=lambda x:x.lower()+' _'
        self.write();self.assertEqual(self.ui.snapshot(1,100)['rfc'],SOL.rfc)
    def test_interior_error_not_silently_removed(self):
        self.new();self.io.transforms['rfc']=lambda x:x[:3]+'_'+x[4:]
        self.io.fail_copy=True
        with self.assertRaisesRegex(AltaClienteError,'No se confirmó'):self.write()
        self.assertNotIn('accept',self.scene.actions)
    def test_paste_ignored_single_keyboard_retry(self):
        self.new();self.io.ignore_paste=True;self.write()
        self.assertEqual(self.io.writes,[('paste','rfc'),('keys','rfc')])
        self.assertEqual(self.scene.values['rfc'],SOL.rfc)
    def test_clipboard_unavailable_keyboard_fallback(self):
        self.new();self.io.paste_error=('PORTAPAPELES_NO_DISPONIBLE',None);self.write()
        self.assertEqual(self.scene.values['rfc'],SOL.rfc)
    def test_paste_and_keys_ignored_stops(self):
        self.new();self.io.ignore_paste=True;self.io.ignore_keys=True
        with self.assertRaisesRegex(AltaClienteError,'No se confirmó'):self.write()
        self.assertEqual(len(self.io.writes),2);self.assertNotIn('accept',self.scene.actions)
    def test_copy_ignores_stale_win32_but_reads_live_input(self):
        self.new();self.io.stale_read.add('rfc');self.write()
        self.assertIn((100,'rfc'),self.ui._lectura_por_copia)
        self.assertEqual(self.ui.snapshot(1,100)['rfc'],SOL.rfc)
    def test_stale_read_and_no_new_copy_never_assumes_expected(self):
        self.new();self.io.stale_read.add('rfc');self.io.fail_copy=True
        with self.assertRaises(AltaClienteError):self.write()
        self.assertNotIn((100,'rfc'),self.ui._lectura_por_copia)
    def test_later_snapshot_rechecks_copy_not_cache(self):
        self.new();self.io.stale_read.add('rfc');self.write()
        self.scene.values['rfc']='BBB010101BBB';self.scene.update_values()
        self.assertEqual(self.ui.snapshot(1,100)['rfc'],'BBB010101BBB')
    def test_later_copy_failure_blocks_not_cached_success(self):
        self.new();self.io.stale_read.add('rfc');self.write();self.io.fail_copy=True
        with self.assertRaises(AltaClienteError):self.ui.snapshot(1,100)
    def test_foreign_clipboard_causes_stop_not_keyboard_retry(self):
        self.new();self.io.stale_read.add('rfc')
        def foreign(*a):raise TextoControlError('COPIA_DE_OTRA_APLICACION')
        self.io.copiar=foreign
        with self.assertRaisesRegex(AltaClienteError,'Otra aplicación'):self.write()
        self.assertEqual(self.io.writes,[('paste','rfc')])
    def test_focus_loss_stops_before_retry(self):
        self.new();self.io.focus_loss=True
        with self.assertRaisesRegex(AltaClienteError,'foco'):self.write()
        self.assertEqual(self.io.writes,[('paste','rfc')])
    def test_form_disabled_after_paste_stops(self):
        self.new();original=self.io.pegar
        def blocked(*args):original(*args);self.scene.nodes[100]['enabled']=False
        self.io.pegar=blocked
        with self.assertRaises(AltaClienteError):self.write()
        self.assertNotIn('accept',self.scene.actions)
    def test_access_denied_message_no_retry(self):
        self.new();self.io.paste_error=('CONTROL_SIN_RESPUESTA',5)
        with self.assertRaisesRegex(AltaClienteError,'permisos'):self.write()
        self.assertEqual(self.io.writes,[('paste','rfc')])
    def test_never_edits_browse_record(self):
        with self.assertRaises(AltaClienteError):self.write()
        self.assertEqual(self.io.writes,[])
    def test_unexpected_edit_mode_stops_before_keyboard(self):
        self.new();self.io.ignore_paste=True
        def change():self.scene.mode='Edit Record'
        original=self.ui._confirmar_campo
        def after(*a):result=original(*a);change();return result
        self.ui._confirmar_campo=after
        with self.assertRaises(AltaClienteError):self.write()
        self.assertEqual(self.io.writes,[('paste','rfc')])
    def test_accept_not_called_if_unreadable_rfc(self):
        self.io.stale_read.add('rfc');self.io.fail_copy=True
        with self.assertRaises(AltaClienteError):self.service().preparar(SOL)
        self.assertNotIn('accept',self.scene.actions)
        self.assertNotIn('lookup',self.scene.actions)
    def test_full_flow_with_copy_only_rfc(self):
        self.io.stale_read.add('rfc');service=self.service();result=service.preparar(SOL)
        self.assertEqual(result['estado'],'PREPARADA_SIN_GUARDAR')
        self.assertGreater(len(self.io.copy_calls),2);self.assertNotIn('accept',self.scene.actions)
    def test_full_flow_with_copy_for_all_four_inputs(self):
        self.io.stale_read.update(('rfc','idcif','telefono','correo'))
        result=self.service().preparar(SOL)
        self.assertEqual(result['estado'],'PREPARADA_SIN_GUARDAR')
        self.assertEqual(set(self.io.copy_calls),{'rfc','idcif','telefono','correo'})
    def test_fresh_snapshot_mutation_blocks_authorized_accept(self):
        self.io.stale_read.add('rfc');service=self.service();service.preparar(SOL)
        self.bot.cfg['app']['modo_prueba']=False
        self.scene.values['rfc']='CCC010101CCC';self.scene.update_values()
        with self.assertRaises(AltaClienteError):service.aceptar(SOL,autorizado=True,inexistencia_revisada=True)
        self.assertNotIn('accept',self.scene.actions)
    def test_safe_mode_still_blocks_accept_with_copy(self):
        self.io.stale_read.add('rfc');service=self.service();service.preparar(SOL)
        with self.assertRaisesRegex(AltaClienteError,'SEGURO'):
            service.aceptar(SOL,autorizado=True,inexistencia_revisada=True)
        self.assertNotIn('accept',self.scene.actions)
    def test_phone_formatted_norm_does_not_touch_rfc(self):
        self.assertEqual(normalizar_campo('telefono','(871) 123-4567'),'8711234567')
        self.assertEqual(normalizar_campo('rfc','AAA-010101AAA'),'AAA-010101AAA')
    def test_email_case_and_idcif_zeros_preserved(self):
        self.assertEqual(normalizar_campo('correo','  Cliente@ejemplo.com '),'Cliente@ejemplo.com')
        self.assertEqual(normalizar_campo('idcif','00000000001'),'00000000001')
    def test_diagnostic_has_lengths_not_customer_values(self):
        self.new();self.io.ignore_paste=True;self.io.ignore_keys=True
        with self.assertRaises(AltaClienteError):self.write()
        text=(Path(self.tmp.name)/'logs'/'diagnostico_alta_captura.json').read_text();d=json.loads(text)
        self.assertEqual(d['campo'],'rfc');self.assertEqual(d['resultado'],'DETENIDA_SIN_ACEPTAR')
        for v in SOL.campos_cliente().values():self.assertNotIn(v,text)
        self.assertTrue(all('valor' not in x and 'texto' not in x for x in d['lecturas']))
    def test_success_diagnostic_no_data(self):
        self.new();self.write()
        text=(Path(self.tmp.name)/'logs'/'diagnostico_alta_captura.json').read_text()
        self.assertNotIn(SOL.rfc,text);self.assertIn('CONFIRMADA_WM_GETTEXT',text)
    def test_input_logs_have_no_rfc_or_email(self):
        self.service().preparar(SOL)
        for v in SOL.campos_cliente().values():self.assertNotIn(v,' '.join(self.logs))
    def test_no_clipboard_read_when_raw_verification_good(self):
        self.new();self.write();self.assertEqual(self.io.copy_calls,[])
    def test_only_editable_user_fields_targeted(self):
        self.new()
        for name in ('numero','nombre','cp'):
            with self.subTest(name=name),self.assertRaises(AltaClienteError):self.write(name,'123')
        self.assertEqual(self.io.writes,[])
    def test_no_sql_network_ocr_imports_new_helper(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'arybot'/'alta_texto.py').read_text())
        for n in ast.walk(tree):
            if isinstance(n,(ast.Import,ast.ImportFrom)):
                names=[a.name.split('.')[0] for a in n.names]
                if isinstance(n,ast.ImportFrom) and n.module:names.append(n.module.split('.')[0])
                self.assertFalse(set(names)&{'pyodbc','pymssql','sqlalchemy','requests','socket','pytesseract','cv2'})

if __name__=='__main__':unittest.main()
