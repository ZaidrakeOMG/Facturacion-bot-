"""Regresión específica de los clips 21:37 y 21:38. Win32 es SIMULADO.

Los rectángulos se midieron manualmente en el video, no se generan desde
PantallaAlta.CAMPOS. Esto detecta el mapa equivocado de la versión 3.1.2.
No se abre Windows/Polaris ni una BD durante las pruebas.
"""
import copy
import re
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arybot.alta_clientes import AltaClientes, PantallaAlta
from arybot.cliente_model import AltaCliente, AltaClienteError
from arybot.polaris import PolarisBot, Rect

SOL=AltaCliente.crear('SERVICIO APACHE','AAA010101AAA','12345678901','8711234567','cliente@example.com')
# Ventana del primer video (borde visible): x449 y180, 705 x567.
# Rectángulos medidos, independientes de las coordenadas en producción.
MEASURED={
 'numero':(462,278,529,298),'nombre':(530,278,1132,321),
 'rfc':(563,406,682,425),'idcif':(732,406,851,425),
 'cp':(563,432,682,451),'telefono':(563,564,716,583),
 'correo':(563,595,1132,614),
}

class DesktopScene:
    def __init__(self,scale=1,dx=0,dy=0,opened=True):
        self.nodes={};self.fields={};self.clicks=[];self.actions=[];self.now=0;self.focus=None
        self.mode='Browse Record';self.read_error=False;self.opened=opened
        self.lookup_enabled=True;self.number='12345';self.overlay=False;self.open_credit=False
        self._t=lambda r:Rect(round((r[0]-449)*scale+449+dx),round((r[1]-180)*scale+180+dy),
                              round((r[2]-449)*scale+449+dx),round((r[3]-180)*scale+180+dy))
        self.add(1,0,'TMainForm','Polaris Facturacion',Rect(0,0,1800,1000))
        self.add(2,1,'TToolBar','',Rect(0,43,1800,88))
        self.add(100,1,'TClientesForm','Clientes de Efectivo',self._t((449,180,1154,747)),visible=opened)
        for i,(name,r) in enumerate(MEASURED.items(),201):
            cls='TDBMemo' if name=='nombre' else 'TDBEdit'
            self.add(i,100,cls,'',self._t(r),enabled=name!='numero',visible=opened)
            self.fields[name]=i
        self.add(30,100,'TSpeedButton','&Nuevo',self._t((477,217,551,251)),visible=opened)
        self.add(31,100,'TBitBtn','&Aceptar',self._t((870,686,953,715)),enabled=False,visible=opened)
        self.add(32,100,'TBitBtn','&Cancelar',self._t((958,686,1043,715)),enabled=False,visible=opened)
        self.add(60,100,'TStatusBar','',self._t((457,721,1146,738)),visible=opened)
        self.lupa=self._t((855,405,876,425))
        self.foreground=100 if opened else 1
        self.values={'numero':'23690','nombre':'CLIENTE EXISTENTE','rfc':'BBB010101BBB','idcif':'',
                     'cp':'27000','telefono':'','correo':''}
        self.update_values()
    def add(self,h,parent,cls,text,rect,enabled=True,visible=True):
        self.nodes[h]=dict(parent=parent,cls=cls,text=text,rect=rect,enabled=enabled,visible=visible)
    def descendant(self,root,h):
        seen=set()
        while h in self.nodes and h not in seen:
            if h==root:return True
            seen.add(h);h=self.nodes[h]['parent']
        return False
    def update_values(self):
        for k,v in self.values.items():self.nodes[self.fields[k]]['text']=v
    def IsWindow(self,h):return h in self.nodes
    def IsWindowVisible(self,h):return bool(h in self.nodes and self.nodes[h]['visible'])
    def IsWindowEnabled(self,h):return self.nodes[h]['enabled']
    def GetWindowText(self,h):return self.nodes[h]['text']
    def GetParent(self,h):return self.nodes[h]['parent']
    def GetForegroundWindow(self):return self.foreground
    def GetClientRect(self,h):return (0,0,1800,957)
    def ClientToScreen(self,h,p):return (p[0],p[1]+43)
    def EnumChildWindows(self,h,cb,arg):
        for k in list(self.nodes):
            if k!=h and self.descendant(h,k):cb(k,arg)
    def WindowFromPoint(self,point):
        if self.overlay:return 900
        x,y=point;hits=[]
        for h,n in self.nodes.items():
            r=n['rect']
            if n['visible'] and n['enabled'] and r.left<=x<r.right and r.top<=y<r.bottom:
                hits.append((r.width*r.height,h))
        return sorted(hits)[0][1] if hits else None
    def activate(self,h,**kwargs):self.foreground=h
    def cleanup(self,main):return [h for h in self.nodes if self.IsWindowVisible(h)]
    def popups(self,main):
        if self.opened:
            return [(0,0,100,self.nodes[100]['text'],'TClientesForm',self.nodes[100]['rect'])]
        return []
    def texttree(self,h):return self.mode if h==100 else ''
    def wrapper(self,h):
        scene=self
        class W:
            handle=h
            def window_text(self):
                if scene.read_error:raise RuntimeError('simulated read error')
                return scene.nodes[h]['text']
            def class_name(self):return scene.nodes[h]['cls']
            def descendants(self):return [scene.wrapper(k) for k in scene.nodes if k!=h and scene.descendant(h,k)]
            def texts(self):return [scene.mode] if h==60 else [scene.nodes[h]['text']]
        return W()
    def desktop(self,**kw):
        scene=self
        class D:
            def window(self,handle):
                return SimpleNamespace(wrapper_object=lambda:scene.wrapper(handle),
                                       get_focus=lambda:SimpleNamespace(handle=scene.focus),set_focus=lambda:scene.activate(handle))
        return D()
    def paste(self,value):
        k=next(k for k,h in self.fields.items() if h==self.focus)
        self.actions.append('write:'+k);self.values[k]=value;self.update_values()
    def click(self,x,y):
        self.clicks.append((x,y));h=self.WindowFromPoint((x,y))
        if not self.opened:
            if 0<=x<41 and 43<=y<88:
                self.opened=True;self.actions.append('open')
                for k,n in self.nodes.items():
                    if self.descendant(100,k):n['visible']=True
                if self.open_credit:self.nodes[100]['text']='Clientes de Crédito'
                self.foreground=100
            return
        if h==30:
            self.actions.append('new');self.mode='Insert Record';self.values={k:'' for k in self.fields};self.update_values()
            self.nodes[30]['enabled']=False;self.nodes[31]['enabled']=True;self.nodes[32]['enabled']=True
        elif self.lupa.left<=x<self.lupa.right and self.lupa.top<=y<self.lupa.bottom:
            self.actions.append('lookup')
            if self.lookup_enabled:
                self.values['nombre']='CLIENTE DE PRUEBA';self.values['cp']='27000';self.update_values()
        elif h in self.fields.values():self.focus=h
        elif h==31:
            self.actions.append('accept');self.mode='Browse Record';self.values['numero']=self.number;self.update_values()
            self.nodes[31]['enabled']=False;self.nodes[32]['enabled']=False;self.nodes[30]['enabled']=True
    def sleep(self,s):self.now+=s
    def bot(self,base):
        return SimpleNamespace(base=Path(base),cfg={'app':{'modo_prueba':True}},_lock=threading.Lock(),log=lambda s:None,
             _cleanup_windows=self.cleanup,_rect=lambda h:self.nodes[h]['rect'],_norm=PolarisBot._norm,
             _window_exists_visible=self.IsWindowVisible,_same_polaris_process=lambda a,b:b in self.nodes,
             _activate=self.activate,_window_tree_text=self.texttree,_check_cleanup_prompt=lambda main:None,_popup_candidates=self.popups,
             _class_name=lambda h:self.nodes[h]['cls'],_is_descendant_or_same=self.descendant,_pid=lambda h:99,
             _station_is_active=lambda main,st:True,_paste=self.paste,
             ensure_ready=lambda:1,_change_station=lambda main,st:main)

class SceneTextIO:
    """Transporte Windows simulado. El estado leído es el control, no el texto enviado."""
    def __init__(self,scene):
        self.scene=scene
    def leer(self,h):
        if self.scene.read_error:
            from arybot.alta_texto import TextoControlError
            raise TextoControlError('ERROR_LECTURA_SIMULADA')
        return self.scene.nodes[h]['text']
    def pegar(self,h,value,guard):
        guard()
        assert self.scene.focus==h
        self.scene.paste(value)
        guard()
    def teclear(self,h,value,guard):
        self.pegar(h,value,guard)
    def copiar(self,h,guard):
        guard()
        return self.scene.nodes[h]['text']


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.use_scene(DesktopScene())
    def use_scene(self,scene):
        if hasattr(self,'patches'):
            for p in reversed(self.patches):p.stop()
        self.scene=scene;self.bot=scene.bot(self.tmp.name);self.ui=PantallaAlta(self.bot,texto=SceneTextIO(scene))
        self.patches=[patch('arybot.polaris.win32gui',scene,create=True),
                      patch('arybot.polaris.Desktop',scene.desktop,create=True),
                      patch('arybot.polaris.pyautogui',SimpleNamespace(click=scene.click,failSafeCheck=lambda:None),create=True),
                      patch('arybot.alta_clientes.time.sleep',scene.sleep),
                      patch('arybot.alta_clientes.time.monotonic',lambda:scene.now)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
    def service(self):return AltaClientes(self.bot,ui=self.ui)
    def test_recognizes_exact_efectivo_title(self):self.assertEqual(self.ui._form(1),100)
    def test_does_not_recognize_credito(self):
        self.scene.nodes[100]['text']='Clientes de Crédito';self.assertIsNone(self.ui._form(1))
    def test_credito_guard_rejects(self):
        self.scene.nodes[100]['text']='Clientes de Crédito'
        with self.assertRaisesRegex(AltaClienteError,'no es Clientes de Efectivo'):self.ui._guard(1,100)
    def test_cp_is_below_rfc_not_nrif(self):
        self.assertEqual(self.ui.CAMPOS['cp'][0],self.ui.CAMPOS['rfc'][0])
        self.assertGreater(self.ui.CAMPOS['cp'][1],self.ui.CAMPOS['rfc'][1])
    def test_all_points_hit_independently_measured_fields(self):
        for name in MEASURED:
            with self.subTest(name=name):
                h,x,y=self.ui._field(1,100,name)
                self.assertEqual(h,self.scene.fields[name])
                r=self.scene.nodes[h]['rect'];self.assertTrue(r.left<=x<r.right and r.top<=y<r.bottom)
    def test_disabled_number_can_be_read(self):
        self.assertFalse(self.scene.nodes[self.scene.fields['numero']]['enabled'])
        self.assertEqual(self.ui.snapshot(1,100)['numero'],'23690')
    def test_measured_fields_work_after_moving_window(self):
        self.use_scene(DesktopScene(dx=230,dy=70))
        self.assertEqual(self.ui.snapshot(1,100)['rfc'],'BBB010101BBB')
        for name in MEASURED:self.assertEqual(self.ui._field(1,100,name)[0],self.scene.fields[name])
    def test_measured_fields_scale_80_percent(self):
        self.use_scene(DesktopScene(scale=.8))
        for name in MEASURED:self.assertEqual(self.ui._field(1,100,name)[0],self.scene.fields[name])
    def test_measured_fields_scale_125_percent(self):
        self.use_scene(DesktopScene(scale=1.25))
        for name in MEASURED:self.assertEqual(self.ui._field(1,100,name)[0],self.scene.fields[name])
    def test_unsupported_maximized_layout_blocks(self):
        self.scene.nodes[100]['rect']=Rect(0,80,1584,800)
        with self.assertRaisesRegex(AltaClienteError,'tamaño'):self.ui._guard(1,100)
    def test_reuse_existing_dialog_no_toolbar_click(self):
        self.assertEqual(self.ui.abrir(1),100);self.assertEqual(self.scene.clicks,[])
    def test_open_first_toolbar_icon(self):
        self.use_scene(DesktopScene(opened=False));self.ui.abrir(1)
        self.assertEqual(self.scene.actions,['open'])
        x,y=self.scene.clicks[0];self.assertLess(x,41);self.assertTrue(43<y<88)
    def test_does_not_continue_when_credito_opens(self):
        self.use_scene(DesktopScene(opened=False));self.scene.open_credit=True
        with self.assertRaisesRegex(AltaClienteError,'Crédito'):self.ui.abrir(1)
        self.assertNotIn('new',self.scene.actions)
    def test_fallback_toolbar_uses_client_coordinates(self):
        self.use_scene(DesktopScene(opened=False));self.scene.nodes[2]['cls']='TPanel'
        with patch.object(self.ui,'_scale',return_value=1):
            self.assertEqual(self.ui._acceso_efectivo(1),(20,65));self.assertEqual(self.ui.abrir(1),100)
    def test_foreign_overlay_blocks_open(self):
        self.use_scene(DesktopScene(opened=False));self.scene.overlay=True
        with self.assertRaisesRegex(AltaClienteError,'tapa'):self.ui.abrir(1)
        self.assertEqual(self.scene.clicks,[])
    def test_foreign_overlay_blocks_field(self):
        self.scene.overlay=True
        with self.assertRaisesRegex(AltaClienteError,'tapa'):self.ui._field(1,100,'rfc')
    def test_button_accelerators_are_recognized(self):
        self.assertEqual(self.ui._find_button(100,'Nuevo'),30)
        self.assertEqual(self.ui._find_button(100,'Aceptar'),31)
    def test_disabled_accept_does_not_fallback_to_pixel(self):
        with self.assertRaisesRegex(AltaClienteError,'deshabilitado'):self.ui.aceptar(1,100)
        self.assertEqual(self.scene.clicks,[])
    def test_new_mode_is_verified(self):
        self.ui.nuevo(1,100);self.assertEqual(self.scene.mode,'Insert Record');self.assertEqual(self.scene.actions,['new'])
    def test_never_press_new_on_existing_edit(self):
        self.scene.mode='Edit Record'
        with self.assertRaises(AltaClienteError):self.ui.nuevo(1,100)
        self.assertEqual(self.scene.actions,[])
    def test_protect_preexisting_insert(self):
        self.scene.mode='Insert Record'
        with self.assertRaisesRegex(AltaClienteError,'captura/edición'):self.ui.proteger_edicion_abierta(1)
    def test_protect_credito_edit_without_using_it(self):
        self.scene.nodes[100]['text']='Clientes de Crédito';self.scene.mode='Edit Record'
        with self.assertRaisesRegex(AltaClienteError,'captura/edición'):self.ui.proteger_edicion_abierta(1)
    def test_read_failure_is_not_treated_as_blank(self):
        self.scene.read_error=True
        with self.assertRaisesRegex(AltaClienteError,'leer'):self.ui.snapshot(1,100)
    def test_duplicate_resolved_fields_block(self):
        with patch.object(self.ui,'CAMPOS',dict(self.ui.CAMPOS,cp=self.ui.CAMPOS['rfc'])):
            with self.assertRaisesRegex(AltaClienteError,'mismo control'):self.ui.snapshot(1,100)
    def test_prepare_real_adapter_full_capture_without_accept(self):
        r=self.service().preparar(SOL)
        self.assertEqual(r['estado'],'PREPARADA_SIN_GUARDAR')
        self.assertEqual(self.scene.actions,['new','write:rfc','write:idcif','lookup','write:telefono','write:correo'])
        self.assertEqual(self.scene.values['correo'],SOL.correo)
    def test_lupa_point_lands_on_correct_icon(self):
        self.ui.nuevo(1,100);self.ui.consultar_idcif(1,100)
        self.assertEqual(self.scene.actions,['new','lookup'])
    def test_lookup_timeout_does_not_capture_contacts_or_accept(self):
        self.scene.lookup_enabled=False
        with self.assertRaisesRegex(AltaClienteError,'lupa'):self.service().preparar(SOL)
        self.assertNotIn('write:telefono',self.scene.actions);self.assertNotIn('accept',self.scene.actions)
    def test_only_four_fields_writable(self):
        with self.assertRaisesRegex(AltaClienteError,'solo captura'):self.ui.escribir(1,100,'nombre','NO CAMBIAR')
        self.assertEqual(self.scene.actions,[])
    def test_safe_mode_never_accepts_after_prepare(self):
        s=self.service();s.preparar(SOL)
        with self.assertRaisesRegex(AltaClienteError,'SEGURO'):s.aceptar(SOL,autorizado=True,inexistencia_revisada=True)
        self.assertNotIn('accept',self.scene.actions)
    def test_authorized_accept_real_adapter_once_and_verifies_number(self):
        s=self.service();s.preparar(SOL);self.bot.cfg['app']['modo_prueba']=False
        r=s.aceptar(SOL,autorizado=True,inexistencia_revisada=True)
        self.assertEqual(r['numero_cliente'],'12345');self.assertEqual(self.scene.actions.count('accept'),1)
        with self.assertRaises(AltaClienteError):s.aceptar(SOL,autorizado=True,inexistencia_revisada=True)
        self.assertEqual(self.scene.actions.count('accept'),1)
    def test_stage_updates_have_seven_distinct_steps(self):
        events=[];s=AltaClientes(self.bot,ui=self.ui,progreso=events.append);s.preparar(SOL)
        self.assertEqual(len(events),7)
        for i,e in enumerate(events,1):self.assertIn(str(i)+'/7',e)

if __name__=='__main__':unittest.main(verbosity=2)
