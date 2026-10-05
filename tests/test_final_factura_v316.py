"""Pantallas simuladas, nunca abre Polaris ni envía correo."""
import ast
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
from arybot.factura_final import (PantallaFinalFactura, RegistroFinalFactura,
                                  FinalFacturaError, validar_correo)
from arybot.polaris import PolarisBot, Rect
from arybot.parser import Solicitud


def sol():
    return Solicitud(estacion='ARY I',rfc='AAA010101AAA',ticket='000123',
                     forma_pago='TARJETA DE CREDITO',correo_destino='cliente@example.com')


class Scene:
    def __init__(self, base):
        self.now=0.;self.actions=[];self.logs=[];self.fg=100;self.copy=False;self.focus=110
        self.station_ok=True;self.show_send=True;self.send_closes=True;self.lag=.5
        self.fail_invoice=False;self.fail_send=False;self.send_due=None;self.close_due=None
        self.mail_wrong=False;self.mail_unread=False;self.mail_copy_wrong=False
        self.after_sleep=None
        self.nodes={}
        def add(h,parent,cls,text,r,vis=True,enabled=True,state=0):
            self.nodes[h]=dict(parent=parent,cls=cls,text=text,r=Rect(*r),vis=vis,enabled=enabled,state=state)
        add(1,0,'TMainForm','Polaris Facturacion', (0,0,1200,900))
        add(100,1,'TfrmFacturacion','Facturación de Efectivo',(200,150,1000,750))
        add(110,100,'TBitBtn','&Aceptar',(750,695,835,725))
        add(200,1,'TfrmEnvio','Envío e Impresión de CFDI',(650,280,870,564),False)
        add(210,200,'TRadioButton','Enviar por Correo',(670,370,846,389))
        add(211,200,'TRadioButton','Imprimir y Enviar por Correo',(670,346,846,364),state=1)
        add(212,200,'TRadioButton','Imprimir',(670,392,846,410))
        add(220,200,'TEdit','anterior@example.net',(670,441,850,460))
        add(230,200,'TCheckBox','XML',(670,473,810,491))
        add(231,200,'TCheckBox','PDF',(670,497,810,515))
        add(240,200,'TBitBtn','&Aceptar',(792,538,855,560))
        add(300,1,'#32770','Error',(300,320,560,450),False)
        self.bot=SimpleNamespace(base=Path(base),cfg={'app':{'modo_prueba':False}},
            pcfg={'espera_timbrado':45,'espera_envio':15,'espera_post_envio':15}, log=self.logs.append,
            _norm=PolarisBot._norm, _rect=lambda h:self.nodes[h]['r'],
            _class_name=lambda h:self.nodes[h]['cls'], _pid=lambda h:2 if h in self.nodes else 9,
            _same_polaris_process=lambda a,b:a in self.nodes and b in self.nodes,
            _is_descendant_or_same=self.desc, _window_exists_visible=self.visible,
            _activate=self.activate, _cleanup_windows=lambda main:[h for h in self.nodes if self.visible(h)],
            _station_is_active=lambda main,station:self.station_ok)
        self.texto=SimpleNamespace(pegar=self.paste,leer=self.read,copiar=self.copy_text,_seleccionar=lambda h:None)
        self.teclado=SimpleNamespace(validar_texto=lambda h,v:None,tecla=self.key,
            escribir=lambda h,v,g:self.paste(h,v,g))
    def key(self,name,guard):
        guard()
        if name=='backspace' and not self.mail_wrong:self.nodes[220]['text']=''
        elif name=='tab':self.focus=240
        self.actions.append('key_'+name)


    def visible(self,h):
        if h not in self.nodes or not self.nodes[h]['vis']:return False
        p=self.nodes[h]['parent']
        return not p or self.visible(p)
    def desc(self,p,h):
        while h in self.nodes:
            if p==h:return True
            h=self.nodes[h]['parent']
        return False
    def activate(self,h,**kwargs):self.fg=h
    def GetWindowText(self,h):return self.nodes[h]['text']
    def GetForegroundWindow(self):return self.fg
    def IsWindowEnabled(self,h):return self.nodes[h]['enabled']
    def EnumChildWindows(self,root,cb,param):
        for h in self.nodes:
            if h!=root and self.desc(root,h):cb(h,param)
    def WindowFromPoint(self,pt):
        x,y=pt;candidates=[]
        for h,n in self.nodes.items():
            r=n['r']
            if self.visible(h) and r.left<=x<r.right and r.top<=y<r.bottom:
                candidates.append((r.width*r.height,h))
        return min(candidates)[1] if candidates else None
    def desktop(self,**kwargs):
        def window(handle):
            wrapper=SimpleNamespace(get_check_state=lambda:self.nodes[handle]['state'])
            return SimpleNamespace(wrapper_object=lambda:wrapper)
        return SimpleNamespace(window=window)
    def click(self,x,y):
        h=self.WindowFromPoint((x,y));self.actions.append(h);self.focus=h
        if h==110:
            self.nodes[110]['enabled']=False
            if self.fail_invoice:self.nodes[300]['vis']=True
            elif self.show_send:self.send_due=self.now+self.lag
        elif h==240:
            if self.fail_send:self.nodes[300]['vis']=True
            elif self.send_closes:self.close_due=self.now+.2
        elif h in (210,230,231):self.nodes[h]['state']=1-self.nodes[h]['state']
    def sleep(self,delta):
        self.now+=delta
        if self.send_due is not None and self.now>=self.send_due:
            self.nodes[200]['vis']=True;self.send_due=None
        if self.close_due is not None and self.now>=self.close_due:
            self.nodes[200]['vis']=False;self.close_due=None;self.fg=100
        if self.after_sleep:self.after_sleep(self)
    def paste(self,h,value,guard):
        guard();self.actions.append('paste_mail')
        if not self.mail_wrong:self.nodes[h]['text']=value
    def read(self,h):return '' if self.mail_unread else self.nodes[h]['text']
    def copy_text(self,h,guard):
        guard();self.copy=True
        return 'otro@example.net' if self.mail_copy_wrong else self.nodes[h]['text']


class FinalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.scene=Scene(self.tmp.name);self.s=self.scene
        from arybot.factura_captura import CapturaFactura
        captura=CapturaFactura(self.s.bot,texto=self.s.texto,teclado=self.s.teclado,
            reloj=lambda:self.s.now,dormir=self.s.sleep,foco=lambda root:self.s.focus)
        self.ui=PantallaFinalFactura(self.s.bot,texto=self.s.texto,reloj=lambda:self.s.now,dormir=self.s.sleep,captura=captura)
        self.reg=RegistroFinalFactura(self.tmp.name,'ARY I','123')
        self.ps=[patch('arybot.polaris.win32gui',self.s,create=True),
                 patch('arybot.polaris.Desktop',self.s.desktop,create=True),
                 patch('arybot.polaris.pyautogui',SimpleNamespace(click=self.s.click,failSafeCheck=lambda:None),create=True)]
        for p in self.ps:p.start()
        self.addCleanup(lambda:[p.stop() for p in reversed(self.ps)])
    def run_final(self):return self.ui.completar(1,100,sol(),self.reg)
    def test_full_final_and_send_only_once(self):
        self.assertEqual(self.run_final(),'ENVIO_SOLICITADO')
        self.assertGreaterEqual(self.s.now,15)
        self.assertEqual(self.s.actions.count(110),1);self.assertEqual(self.s.actions.count(240),1)
        self.assertNotIn(211,self.s.actions);self.assertNotIn(212,self.s.actions)
        self.assertEqual(self.s.nodes[220]['text'],sol().correo_destino)
        self.assertEqual(json.loads(self.reg.path.read_text())['estado'],'ENVIO_SOLICITADO')
    def test_tiny_dialog_selected_not_parent_invoice(self):
        self.s.nodes[200]['vis']=True
        self.assertEqual(self.ui.buscar_envio(1),200)
        self.assertLess(self.s.nodes[200]['r'].width*self.s.nodes[200]['r'].height,180000)
    def test_no_accents_title_also_found(self):
        self.s.nodes[200]['vis']=True;self.s.nodes[200]['text']='Envio e Impresion de CFDI'
        self.assertEqual(self.ui.buscar_envio(1),200)
    def test_safe_mode_never_accepts(self):
        self.s.bot.cfg['app']['modo_prueba']=True
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertEqual(self.s.actions,[]);self.assertFalse(self.reg.path.exists())
    def test_missing_mode_fails_closed(self):
        self.s.bot.cfg={}
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertEqual(self.s.actions,[])
    def test_wrong_station_before_invoice(self):
        self.s.station_ok=False
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertNotIn(110,self.s.actions)
    def test_disabled_accept_before_invoice(self):
        self.s.nodes[110]['enabled']=False
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertFalse(self.reg.path.exists())
    def test_pending_send_not_new_invoice(self):
        self.s.nodes[200]['vis']=True
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertNotIn(110,self.s.actions)
    def test_warning_blocks_before_invoice(self):
        self.s.nodes[300]['vis']=True
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertNotIn(110,self.s.actions)
    def test_invalid_email_before_invoice(self):
        data=sol();data.correo_destino='bad\ncliente@example.com'
        with self.assertRaises(FinalFacturaError):self.ui.completar(1,100,data,self.reg)
        self.assertFalse(self.reg.path.exists())
    def test_warning_after_invoice_no_send(self):
        self.s.fail_invoice=True
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertEqual(self.s.actions.count(110),1);self.assertNotIn(240,self.s.actions)
        self.assertTrue(self.reg.path.exists())
    def test_timbrado_timeout_no_retry(self):
        self.s.show_send=False
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertEqual(self.s.actions.count(110),1);self.assertNotIn(240,self.s.actions)
    def test_waits_for_late_timbrado(self):
        self.s.lag=12
        self.assertEqual(self.run_final(),'ENVIO_SOLICITADO');self.assertGreater(self.s.now,12)
    def test_send_error_no_resend(self):
        self.s.fail_send=True
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertEqual(self.s.actions.count(240),1);self.assertEqual(self.s.actions.count(110),1)
        self.assertEqual(json.loads(self.reg.path.read_text())['estado'],'ENVIAR_INTENTADO')
    def test_send_timeout_no_resend(self):
        self.s.send_closes=False
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertEqual(self.s.actions.count(240),1)
    def test_failed_mail_verification_does_not_send(self):
        self.s.mail_wrong=True
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertNotIn(240,self.s.actions)
    def test_delayed_native_read_uses_fresh_copy(self):
        self.s.mail_unread=True
        self.assertEqual(self.run_final(),'ENVIO_SOLICITADO');self.assertTrue(self.s.copy)
    def test_stale_expected_paste_not_evidence(self):
        self.s.mail_unread=True;self.s.mail_copy_wrong=True
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertNotIn(240,self.s.actions)
    def test_already_checked_xml_pdf_not_toggled_off(self):
        for h in (210,230,231):self.s.nodes[h]['state']=1
        self.run_final()
        for h in (210,230,231):self.assertNotIn(h,self.s.actions)
    def test_indeterminate_checkbox_is_not_accepted(self):
        self.s.nodes[231]['state']=2
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertNotIn(240,self.s.actions)
    def test_safe_enabled_after_invoice_blocks_send(self):
        self.s.after_sleep=lambda s:s.bot.cfg['app'].update(modo_prueba=True) if s.nodes[200]['vis'] else None
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertEqual(self.s.actions.count(110),1);self.assertNotIn(240,self.s.actions)
    def test_duplicate_accept_control_stops(self):
        self.s.nodes[112]=dict(self.s.nodes[110]);self.s.nodes[112]['r']=Rect(500,695,585,725)
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertNotIn(110,self.s.actions)
    def test_unrecognized_dialog_not_matched(self):
        self.s.nodes[200]['vis']=True;self.s.nodes[200]['text']='Otro formulario'
        self.assertIsNone(self.ui.buscar_envio(1))
    def test_other_application_focus_stops(self):
        self.s.bot._activate=lambda h,**kw:None;self.s.fg=999
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertEqual(self.s.actions,[])
    def test_local_history_has_no_pii(self):
        self.run_final();content=self.reg.path.read_text()
        self.assertNotIn(sol().correo_destino,content);self.assertNotIn(sol().rfc,content)
        self.assertNotIn('cliente@example.com',' '.join(self.s.logs))

    def test_diagnostics_omit_email_and_all_unknown_captions(self):
        self.s.nodes[200]['vis']=True
        self.ui.diagnostico(1)
        text=(Path(self.tmp.name)/'logs'/'diagnostico_final_factura.json').read_text()
        self.assertNotIn('anterior@example.net',text);self.assertNotIn(sol().correo_destino,text)
        self.assertIn('ENVIOEIMPRESIONDECFDI',text)
    def test_multiple_send_windows_rejected(self):
        self.s.nodes[200]['vis']=True
        self.s.nodes[201]=dict(self.s.nodes[200])
        with self.assertRaises(FinalFacturaError):self.ui.buscar_envio(1)
    def test_second_email_edit_rejected(self):
        self.s.nodes[221]=dict(self.s.nodes[220]);self.s.nodes[221]['r']=Rect(670,420,850,438)
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertNotIn(240,self.s.actions)
    def test_wrong_radio_label_is_not_imprimir(self):
        self.s.nodes[210]['text']='Enviar algo diferente'
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertNotIn(211,self.s.actions);self.assertNotIn(212,self.s.actions);self.assertNotIn(240,self.s.actions)


class RegistroTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
    def test_duplicate_leading_zeros_same_record(self):
        a=RegistroFinalFactura(self.tmp.name,'ARY I','000123');b=RegistroFinalFactura(self.tmp.name,'ARY 1','123')
        a.iniciar();self.assertEqual(a.path,b.path)
        with self.assertRaises(FinalFacturaError):b.comprobar_libre()
    def test_exclusive_create_does_not_overwrite(self):
        a=RegistroFinalFactura(self.tmp.name,'ARY I','123');a.iniciar();old=a.path.read_bytes()
        with self.assertRaises(FinalFacturaError):a.iniciar()
        self.assertEqual(a.path.read_bytes(),old)
    def test_other_station_independent(self):
        a=RegistroFinalFactura(self.tmp.name,'ARY I','123');b=RegistroFinalFactura(self.tmp.name,'ARY II','123')
        a.iniciar();b.comprobar_libre();b.iniciar();self.assertNotEqual(a.path,b.path)
    def test_result_uncertain_stays_blocked(self):
        a=RegistroFinalFactura(self.tmp.name,'ARY I','123');a.iniciar()
        with self.assertRaises(FinalFacturaError):RegistroFinalFactura(self.tmp.name,'ARY I','123').comprobar_libre()
    def test_invalid_ticket_no_files(self):
        for t in ('','../../file','12;456','abc','12 34'):
            with self.subTest(t=t),self.assertRaises(FinalFacturaError):RegistroFinalFactura(self.tmp.name,'ARY I',t)
        self.assertEqual(list(Path(self.tmp.name).rglob('*')),[])
    def test_mail_validation(self):
        for v in ('','a@','a@b','a@b.com;z@x.com','a@b.com\r\nCc:z@x.com'):
            with self.subTest(v=v),self.assertRaises(FinalFacturaError):validar_correo(v)
        self.assertEqual(validar_correo(' a+factura@example.com '),'a+factura@example.com')
    def test_no_sql_or_exec_in_added_module(self):
        path=Path(__file__).parents[1]/'arybot'/'factura_final.py';tree=ast.parse(path.read_text())
        imports=[alias.name for n in ast.walk(tree) if isinstance(n,ast.Import) for alias in n.names]
        self.assertFalse(set(imports)&{'pyodbc','pymssql','sqlite3'})
        self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
                             and n.func.id in ('exec','eval') for n in ast.walk(tree)))


class InvoiceDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.b=PolarisBot.__new__(PolarisBot);self.b.base=Path(self.tmp.name);self.b._lock=threading.Lock()
        self.b.cfg={'app':{'modo_prueba':False}};self.b.pcfg={};self.b.log=Mock()
        for name,value in [('ensure_ready',1),('_change_station',1),('_open_cash_invoice',100),('_select_client',100),
                           ('_set_payment','EFECTIVO'),('_set_cfdi_usage',None),('_add_ticket',None),('_verificar_captura',None)]:
            setattr(self.b,name,Mock(return_value=value))
    def test_safe_dispatch_never_calls_final(self):
        with patch('arybot.factura_final.PantallaFinalFactura') as final:
            self.assertEqual(self.b.invoice(sol(),test_mode=True),'PRUEBA_OK');final.assert_not_called()
    def test_production_dispatch_reaches_final(self):
        with patch('arybot.factura_final.PantallaFinalFactura') as final:
            final.return_value.completar.return_value='ENVIO_SOLICITADO'
            self.assertEqual(self.b.invoice(sol(),test_mode=False),'ENVIO_SOLICITADO')
            final.return_value.completar.assert_called_once()
    def test_invalid_mode_not_truthy_coercion(self):
        for mode in ('False',None,0):
            with self.subTest(mode=mode),self.assertRaises(Exception):self.b.invoice(sol(),test_mode=mode)
        self.b.ensure_ready.assert_not_called()
    def test_production_rejects_invalid_email_before_ui(self):
        s=sol();s.correo_destino='wrong'
        with self.assertRaises(FinalFacturaError):self.b.invoice(s,test_mode=False)
        self.b.ensure_ready.assert_not_called()
    def test_safe_can_prepare_without_email(self):
        s=sol();s.correo_destino=''
        self.assertEqual(self.b.invoice(s,test_mode=True),'PRUEBA_OK')
    def test_prior_attempt_blocks_before_cleanup_or_station(self):
        RegistroFinalFactura(self.tmp.name,'ARY I','123').iniciar()
        with self.assertRaises(FinalFacturaError):self.b.invoice(sol(),test_mode=False)
        self.b.ensure_ready.assert_not_called();self.b._change_station.assert_not_called()

    def test_preflight_fail_before_final_no_reservation(self):
        self.b._verificar_captura.side_effect=RuntimeError('dato vacío')
        with patch('arybot.factura_final.PantallaFinalFactura') as final:
            with self.assertRaises(RuntimeError):self.b.invoice(sol(),test_mode=False)
            final.assert_not_called()
        self.assertFalse(RegistroFinalFactura(self.tmp.name,'ARY I','123').path.exists())
    def test_preflight_runs_in_safe_mode_too(self):
        with patch('arybot.factura_final.PantallaFinalFactura'):
            self.b.invoice(sol(),test_mode=True)
        self.b._verificar_captura.assert_called_once_with(100,sol())

if __name__=='__main__':unittest.main(verbosity=2)
