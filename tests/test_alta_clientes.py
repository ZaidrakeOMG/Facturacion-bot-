from pathlib import Path
import ast
import json
import tempfile
import threading
import unittest
from types import SimpleNamespace

from arybot.cliente_model import AltaCliente,AltaClienteError
from arybot.alta_clientes import AltaClientes,DiarioAltas,PantallaAlta


def sol(**kw):
    v=dict(estacion='ARY VI',rfc='AAA010101AAA',idcif='12345678901',
           telefono='8711234567',correo='cliente@example.com')
    v.update(kw)
    return AltaCliente.crear(**v)


class ModelTests(unittest.TestCase):
    def test_four_customer_fields(self):
        self.assertEqual(set(sol().campos_cliente()),{'rfc','idcif','telefono','correo'})
    def test_station_is_internal_context(self):self.assertEqual(sol(estacion='11507').estacion,'ARY VI')
    def test_rfc_upper(self):self.assertEqual(sol(rfc='aaa010101aaa').rfc,'AAA010101AAA')
    def test_rfc_persona(self):self.assertEqual(len(sol(rfc='AAAA010101AAA').rfc),13)
    def test_idcif_leading_zero(self):self.assertEqual(sol(idcif='00123456789').idcif,'00123456789')
    def test_phone_format(self):self.assertEqual(sol(telefono='+52 (871) 123-4567').telefono,'+528711234567')
    def test_domain_lower(self):self.assertEqual(sol(correo='Cliente@EXAMPLE.COM').correo,'Cliente@example.com')
    def test_reject_missing_fields(self):
        for key in ('estacion','rfc','idcif','telefono','correo'):
            with self.subTest(key=key),self.assertRaises(AltaClienteError):sol(**{key:''})
    def test_reject_bad_rfc(self):
        for v in ('A','AAA 010101AAA','123456789012','AAA010101AAA;DROP'):
            with self.subTest(v=v),self.assertRaises(AltaClienteError):sol(rfc=v)
    def test_reject_generic(self):
        for v in ('XAXX010101000','XEXX010101000'):
            with self.subTest(v=v),self.assertRaises(AltaClienteError):sol(rfc=v)
    def test_reject_idcif_malformed(self):
        for v in ('123','1234567890a','123456789012','١٢٣٤٥٦٧٨٩٠١'):
            with self.subTest(v=v),self.assertRaises(AltaClienteError):sol(idcif=v)
    def test_reject_non_strings(self):
        with self.assertRaises(AltaClienteError):sol(idcif=12345678901)
    def test_reject_keys_as_text(self):
        with self.assertRaises(AltaClienteError):sol(telefono='{ENTER}')
    def test_reject_control_chars(self):
        for k in ('estacion','rfc','idcif','telefono','correo'):
            with self.subTest(key=k),self.assertRaises(AltaClienteError):sol(**{k:'hola\n'})
    def test_reject_email_list(self):
        with self.assertRaises(AltaClienteError):sol(correo='a@b.com;b@c.com')
    def test_reject_bad_email(self):
        for v in ('.a@b.com','a..b@c.com','foo@','@example.com','a@b..com','a@b com'):
            with self.subTest(v=v),self.assertRaises(AltaClienteError):sol(correo=v)
    def test_revalidate_constructed_instance(self):
        s=AltaCliente('ARY VI','x','12345678901','8711234567','a@example.com')
        with self.assertRaises(AltaClienteError):s.validar()
    def test_station_hash_isolation(self):self.assertNotEqual(sol().clave_local(),sol(estacion='ARY V').clave_local())
    def test_email_change_same_key(self):self.assertEqual(sol().clave_local(),sol(correo='b@example.com').clave_local())


class FakeUI:
    def __init__(self):
        self.calls=[];self.fields={k:'' for k in PantallaAlta.CAMPOS}
        self.fail_lookup=False;self.changed=False;self.saved='600';self.context=True;self.blank_error=False
        self.accept_error=False;self.verify_error=False;self.button_error=False;self.fiscal_error=False
    def proteger_edicion_abierta(self,main):self.calls.append('protect')
    def abrir(self,main):self.calls.append('open');return 22
    def snapshot(self,*args):return dict(self.fields)
    def nuevo(self,*args):
        self.calls.append('new');self.fields={k:'' for k in PantallaAlta.CAMPOS}
        if self.blank_error:self.fields['rfc']='BBB010101BBB'
    def exigir_nuevo(self,*args):self.calls.append('require_new')
    def escribir(self,main,form,key,value):self.calls.append('write:'+key);self.fields[key]=value
    def consultar_idcif(self,*args):self.calls.append('lookup')
    def esperar_fiscales(self,*args):
        if self.fail_lookup:raise AltaClienteError('SAT timeout')
        self.fields.update(nombre='CLIENTE DE PRUEBA',cp='27000')
        if self.fiscal_error:self.fields['cp']=''
    def verificar_contexto(self,*args):
        if not self.context:raise AltaClienteError('station/process changed')
    def validar_aceptar(self,*args):
        if self.button_error:raise AltaClienteError('no button')
    def aceptar(self,*args):
        self.calls.append('accept')
        if self.accept_error:raise AltaClienteError('click interrupted')
    def verificar_guardado(self,*args):
        if self.verify_error:raise AltaClienteError('save unconfirmed')
        return self.saved


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.logs=[];self.now=10.;self.bot=SimpleNamespace(base=Path(self.tmp.name),cfg={'app':{'modo_prueba':True}},
                    _lock=threading.Lock(),log=self.logs.append,ensure_ready=lambda:11,
                    _change_station=lambda main,st:main,_pid=lambda h:101)
        self.ui=FakeUI();self.svc=AltaClientes(self.bot,ui=self.ui,reloj=lambda:self.now)
    def prepare(self):return self.svc.preparar(sol())
    def activate(self):self.bot.cfg['app']['modo_prueba']=False
    def accept(self,**kw):
        vals=dict(autorizado=True,inexistencia_revisada=True);vals.update(kw)
        return self.svc.aceptar(sol(),**vals)
    def test_prepare_never_accepts(self):
        r=self.prepare();self.assertEqual(r['estado'],'PREPARADA_SIN_GUARDAR');self.assertNotIn('accept',self.ui.calls)
    def test_prepare_writes_only_four(self):
        self.prepare();self.assertEqual([x for x in self.ui.calls if x.startswith('write:')],
            ['write:rfc','write:idcif','write:telefono','write:correo'])
    def test_lookup_before_contacts(self):
        self.prepare();self.assertLess(self.ui.calls.index('lookup'),self.ui.calls.index('write:telefono'))
    def test_prepare_no_local_journal(self):self.prepare();self.assertFalse(self.svc.diario.path.exists())
    def test_same_record_detected(self):
        self.ui.fields['rfc']=sol().rfc
        with self.assertRaises(AltaClienteError):self.prepare()
        self.assertNotIn('new',self.ui.calls)
    def test_new_must_be_blank(self):
        self.ui.blank_error=True
        with self.assertRaises(AltaClienteError):self.prepare()
        self.assertFalse(any(x.startswith('write') for x in self.ui.calls))
    def test_lookup_failure_no_contacts_no_accept(self):
        self.ui.fail_lookup=True
        with self.assertRaises(AltaClienteError):self.prepare()
        self.assertNotIn('write:correo',self.ui.calls);self.assertNotIn('accept',self.ui.calls)
    def test_no_name_cp_blocks(self):
        self.ui.fiscal_error=True
        with self.assertRaises(AltaClienteError):self.prepare()
        self.assertIsNone(self.svc.pendiente)
    def test_double_prepare_blocks(self):
        self.prepare()
        with self.assertRaises(AltaClienteError):self.prepare()
        self.assertEqual(self.ui.calls.count('new'),1)
    def test_busy_blocks_before_ui(self):
        self.bot._lock.acquire()
        try:
            with self.assertRaises(AltaClienteError):self.prepare()
            self.assertEqual(self.ui.calls,[])
        finally:self.bot._lock.release()
    def test_lock_released_on_failure(self):
        self.ui.fail_lookup=True
        with self.assertRaises(AltaClienteError):self.prepare()
        self.assertTrue(self.bot._lock.acquire(False));self.bot._lock.release()
    def test_safe_mode_blocks_accept(self):
        self.prepare()
        with self.assertRaises(AltaClienteError):self.accept()
        self.assertNotIn('accept',self.ui.calls)
    def test_missing_authorization(self):
        self.prepare();self.activate()
        with self.assertRaises(AltaClienteError):self.accept(autorizado=False)
        self.assertNotIn('accept',self.ui.calls)
    def test_missing_duplicate_review(self):
        self.prepare();self.activate()
        with self.assertRaises(AltaClienteError):self.accept(inexistencia_revisada=False)
        self.assertNotIn('accept',self.ui.calls)
    def test_cannot_accept_without_prepare(self):
        self.activate()
        with self.assertRaises(AltaClienteError):self.accept()
        self.assertNotIn('accept',self.ui.calls)
    def test_good_accept_once(self):
        self.prepare();self.activate();r=self.accept()
        self.assertEqual(r['numero_cliente'],'600');self.assertEqual(self.ui.calls.count('accept'),1)
        with self.assertRaises(AltaClienteError):self.accept()
        self.assertEqual(self.ui.calls.count('accept'),1)
    def test_request_changed_blocks(self):
        self.prepare();self.activate()
        with self.assertRaises(AltaClienteError):self.svc.aceptar(sol(correo='other@example.com'),autorizado=True,inexistencia_revisada=True)
        self.assertNotIn('accept',self.ui.calls)
    def test_changed_station_blocks(self):
        self.prepare();self.activate();self.ui.context=False
        with self.assertRaises(AltaClienteError):self.accept()
        self.assertNotIn('accept',self.ui.calls)
    def test_modified_snapshot_blocks(self):
        self.prepare();self.activate();self.ui.fields['nombre']='OTRO CLIENTE'
        with self.assertRaises(AltaClienteError):self.accept()
        self.assertNotIn('accept',self.ui.calls)
    def test_modified_phone_blocks(self):
        self.prepare();self.activate();self.ui.fields['telefono']='1234567890'
        with self.assertRaises(AltaClienteError):self.accept()
        self.assertNotIn('accept',self.ui.calls)
    def test_already_has_number_blocks(self):
        self.prepare();self.activate();self.ui.fields['numero']='600'
        with self.assertRaises(AltaClienteError):self.accept()
        self.assertNotIn('accept',self.ui.calls)
    def test_expired_blocks(self):
        self.prepare();self.activate();self.now=920
        with self.assertRaises(AltaClienteError):self.accept()
        self.assertNotIn('accept',self.ui.calls)
    def test_button_unreadable_blocks(self):
        self.prepare();self.activate();self.ui.button_error=True
        with self.assertRaises(AltaClienteError):self.accept()
        self.assertNotIn('accept',self.ui.calls);self.assertFalse(self.svc.diario.path.exists())
    def test_journal_failure_before_click(self):
        self.prepare();self.activate()
        def fail(*args):raise OSError('disk full')
        self.svc.diario.registrar=fail
        with self.assertRaises(OSError):self.accept()
        self.assertNotIn('accept',self.ui.calls)
    def test_unknown_save_not_retried(self):
        self.prepare();self.activate();self.ui.verify_error=True
        with self.assertRaises(AltaClienteError):self.accept()
        self.assertIsNone(self.svc.pendiente)
        self.assertEqual(self.svc.diario.leer()[sol().clave_local()]['estado'],'ACEPTAR_PENDIENTE_DE_VERIFICAR')
        with self.assertRaises(AltaClienteError):self.prepare()
        self.assertEqual(self.ui.calls.count('accept'),1)
    def test_click_error_preserves_unknown_state(self):
        self.prepare();self.activate();self.ui.accept_error=True
        with self.assertRaises(AltaClienteError):self.accept()
        self.assertIsNone(self.svc.pendiente)
        with self.assertRaises(AltaClienteError):self.prepare()
    def test_previous_attempt_survives_restart(self):
        self.prepare();self.activate();self.accept()
        new=AltaClientes(self.bot,ui=FakeUI())
        with self.assertRaises(AltaClienteError):new.preparar(sol())
    def test_journal_contains_no_customer_data(self):
        self.prepare();self.activate();self.accept();text=self.svc.diario.path.read_text()
        for v in sol().campos_cliente().values():self.assertNotIn(v,text)
    def test_release_no_screen_actions(self):
        self.prepare();n=len(self.ui.calls);self.svc.liberar();self.assertIsNone(self.svc.pendiente)
        self.assertEqual(n,len(self.ui.calls))
    def test_log_does_not_leak_data(self):
        self.prepare();self.activate();self.accept();text=' '.join(self.logs)
        for v in sol().campos_cliente().values():self.assertNotIn(v,text)
    def test_bad_journal_blocks(self):
        self.svc.diario.path.parent.mkdir();self.svc.diario.path.write_text('not json')
        with self.assertRaises(AltaClienteError):self.prepare()
        self.assertEqual(self.ui.calls,[])
    def test_revalidation_before_ui(self):
        bad=AltaCliente('ARY VI','invalid','12345678901','8711234567','a@example.com')
        with self.assertRaises(AltaClienteError):self.svc.preparar(bad)
        self.assertEqual(self.ui.calls,[])


class StaticTests(unittest.TestCase):
    def test_no_sql_or_network_driver_in_new_modules(self):
        root=Path(__file__).resolve().parents[1]/'arybot'
        for name in ('cliente_model.py','alta_clientes.py','alta_gui.py'):
            tree=ast.parse((root/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,(ast.Import,ast.ImportFrom)):
                    names=[a.name for a in node.names]+([node.module] if isinstance(node,ast.ImportFrom) and node.module else [])
                    self.assertFalse(any(x.split('.')[0] in {'pyodbc','pymssql','sqlalchemy','requests','httpx','urllib','socket'} for x in names))
    def test_no_direct_original_invoice_method_calls(self):
        root=Path(__file__).resolve().parents[1]/'arybot'
        for name in ('alta_clientes.py','alta_gui.py'):
            tree=ast.parse((root/name).read_text())
            calls=[n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
            self.assertNotIn('invoice',calls);self.assertNotIn('_send_dialog',calls)
    def test_critical_accept_no_relative_only_click(self):
        import inspect
        self.assertIn('critical=True',inspect.getsource(PantallaAlta.aceptar))


if __name__=='__main__':unittest.main(verbosity=2)
