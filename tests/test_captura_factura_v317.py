"""Simulación de controles y eventos; no consulta Polaris ni emite CFDI."""
import ctypes as C
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from arybot.factura_captura import CapturaFactura, CapturaFacturaError, LecturaCombo, coincide
from arybot.teclado_factura import TecladoFactura, TecladoError, INPUT
from arybot.polaris import PolarisBot, Rect

class FakeKeyboardAPI:
    def __init__(self):
        self.events=[];self.caps=0;self.mods=False;self.partial=False
        self.layout=123;self.unmapped=False
    def GetWindowThreadProcessId(self,h,p):return 1
    def GetKeyboardLayout(self,t):return self.layout
    def GetAsyncKeyState(self,k):return 0x8000 if self.mods else 0
    def GetKeyState(self,k):return self.caps
    def VkKeyScanExW(self,char,layout):
        if self.unmapped:return -1
        if char.isdigit():return ord(char)
        if char.isascii() and char.isalpha():return ord(char.upper())|(0x100 if char.isupper() else 0)
        return {'@':0x0651,'&':0x0136,'+':0x01BB,'.':0xBE,'_':0x01BD,'Ñ':0x01C0,'~':0x0634}.get(char,-1)
    def SendInput(self,n,items,size):
        for it in items:self.events.append((int(it.ki.wVk),int(it.ki.dwFlags),int(it.ki.wScan),int(it.type)))
        return n-1 if self.partial else n

class KeyboardTests(unittest.TestCase):
    def setUp(self):self.api=FakeKeyboardAPI();self.k=TecladoFactura(self.api,dormir=lambda _:None)
    def test_input_size_x64_or_x86(self):self.assertEqual(C.sizeof(INPUT),40 if C.sizeof(C.c_void_p)==8 else 28)
    def test_digits_upper_lower_and_altgr_are_native_keys(self):
        self.k.escribir(10,'A1a@',lambda:None)
        self.assertIn((0x11,0,0,1),self.api.events);self.assertIn((0x12,0,0,1),self.api.events)
        self.assertTrue(all(flag in (0,2) for vk,flag,scan,typ in self.api.events))
        self.assertTrue(all(vk!=0xE7 for vk,flag,scan,typ in self.api.events))
    def test_unmappable_no_partial_typing(self):
        with self.assertRaises(TecladoError):self.k.escribir(10,'AAA☃',lambda:None)
        self.assertFalse(self.api.events)
    def test_control_chars_rejected_before_typing(self):
        for val in ('','hello\n','hello\t','a\x00b'):
            with self.subTest(val=repr(val)),self.assertRaises(TecladoError):self.k.escribir(10,val,lambda:None)
        self.assertFalse(self.api.events)
    def test_capslock_compensated_not_toggled(self):
        self.api.caps=1;self.k.escribir(10,'Aa',lambda:None)
        self.assertEqual(self.api.events[:2],[(65,0,0,1),(65,2,0,1)])
        self.assertNotIn(0x14,[e[0] for e in self.api.events]);self.assertIn(0x10,[e[0] for e in self.api.events])
    def test_modifiers_held_block(self):
        self.api.mods=True
        with self.assertRaises(TecladoError):self.k.escribir(10,'ABC',lambda:None)
        self.assertFalse(self.api.events)
    def test_focus_checked_before_each_char(self):
        n=[0]
        def guard():
            n[0]+=1
            if n[0]==3:raise RuntimeError('foco')
        with self.assertRaises(RuntimeError):self.k.escribir(10,'ABCD',guard)
        self.assertNotIn(ord('B'),[e[0] for e in self.api.events])
    def test_partial_input_releases_not_retries(self):
        self.api.partial=True
        with self.assertRaises(TecladoError):self.k.escribir(10,'AB',lambda:None)
        self.assertEqual([e[0] for e in self.api.events if e[1]==0].count(65),1)
        self.assertNotIn(66,[e[0] for e in self.api.events])
    def test_layout_change_stops_before_next_character(self):
        self.k.dormir=lambda _:setattr(self.api,'layout',999)
        with self.assertRaises(TecladoError):self.k.escribir(10,'AB',lambda:None)
        self.assertNotIn(66,[e[0] for e in self.api.events])
    def test_special_character_never_expands_to_enter(self):
        self.k.escribir(10,'~',lambda:None)
        self.assertNotIn(13,[e[0] for e in self.api.events])

class CaptureScene:
    def __init__(self,base):
        self.now=0;self.fg=10;self.focus=20;self.actions=[];self.events=[]
        self.erase_on_tab=False;self.sticky=False;self.wrong=False;self.unread=False
        self.clip_stale=False;self.drop_opens=True;self.refuse_selection=False
        self.nodes={10:dict(parent=0,cls='TForm',r=Rect(0,0,720,550),enabled=True,text='Facturación de Efectivo'),
            20:dict(parent=10,cls='TEdit',r=Rect(30,300,150,325),enabled=True,text=''),
            21:dict(parent=10,cls='TButton',r=Rect(170,300,250,325),enabled=True,text='Agregar Folio'),
            30:dict(parent=10,cls='TComboBox',r=Rect(220,130,330,150),enabled=True,text='EFECTIVO'),
            31:dict(parent=10,cls='TComboBox',r=Rect(340,170,460,190),enabled=True,text='GASTOS EN GENERAL.')}
        self.items={30:['CHEQUE NOMINATIVO','EFECTIVO','TARJETA DE CRÉDITO'],31:['GASTOS EN GENERAL.','ADQUISICION DE MERCANCIAS.']}
        self.indices={30:1,31:0};self.opened={30:False,31:False};self.committed={}
        self.bot=SimpleNamespace(base=Path(base),_norm=PolarisBot._norm,log=self.events.append,
            _window_exists_visible=lambda h:h in self.nodes,
            _same_polaris_process=lambda a,b:a in self.nodes and b in self.nodes,
            _is_descendant_or_same=self.desc,_rect=lambda h:self.nodes[h]['r'],
            _class_name=lambda h:self.nodes[h]['cls'])
        self.text=SimpleNamespace(leer=self.read,copiar=self.copy,_seleccionar=lambda h:self.actions.append('select'))
        self.keyboard=SimpleNamespace(validar_texto=lambda h,v:None,tecla=self.key,escribir=self.write)
        self.combo=SimpleNamespace(opciones=lambda h:self.items[h],actual=self.current,abierto=lambda h:self.opened[h])
    def desc(self,p,h):
        while h in self.nodes:
            if p==h:return True
            h=self.nodes[h]['parent']
        return False
    def GetForegroundWindow(self):return self.fg
    def IsWindowEnabled(self,h):return self.nodes[h]['enabled']
    def EnumChildWindows(self,root,cb,param):
        for h in self.nodes:
            if h!=root and self.desc(root,h):cb(h,param)
    def WindowFromPoint(self,point):
        x,y=point;out=[]
        for h,n in self.nodes.items():
            r=n['r']
            if r.left<=x<r.right and r.top<=y<r.bottom:out.append((r.width*r.height,h))
        return min(out)[1] if out else None
    def GetParent(self,h):return self.nodes[h]['parent']
    def click(self,x,y):
        h=self.WindowFromPoint((x,y));self.focus=h;self.actions.append(('click',h))
        if h in self.items and x>=self.nodes[h]['r'].right-15 and self.drop_opens:self.opened[h]=True
    def current(self,h):
        idx=self.indices[h]
        if idx<0:raise CapturaFacturaError('sin selección')
        return idx,self.items[h][idx]
    def key(self,k,guard):
        guard();self.actions.append(k);h=self.focus
        if k=='backspace':self.nodes[h]['text']=''
        elif k=='tab':
            if self.sticky:return
            self.committed[h]='' if self.erase_on_tab else self.nodes[h]['text']
            if self.erase_on_tab:self.nodes[h]['text']=''
            self.focus=21
        elif k in ('home','down','up'):
            idx=0 if k=='home' else self.indices[h]+(1 if k=='down' else -1)
            self.indices[h]=max(0,min(idx,len(self.items[h])-1))
        elif k=='enter':
            assert self.opened[h]
            self.opened[h]=False
            if self.refuse_selection:self.indices[h]=-1
            else:
                self.nodes[h]['text']=self.items[h][self.indices[h]]
                self.committed[h]=self.nodes[h]['text']
    def write(self,h,v,guard):guard();self.actions.append('keyboard');self.nodes[h]['text']='WRONG' if self.wrong else v;guard()
    def read(self,h):return '' if self.unread else self.nodes[h]['text']
    def copy(self,h,guard):
        guard()
        if self.clip_stale:raise RuntimeError('sin copia nueva')
        self.actions.append('copy_new');return self.nodes[h]['text']
    def sleep(self,s):self.now+=s

class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=CaptureScene(self.tmp.name)
        self.c=CapturaFactura(self.s.bot,texto=self.s.text,teclado=self.s.keyboard,combo=self.s.combo,
            reloj=lambda:self.s.now,dormir=self.s.sleep,foco=lambda root:self.s.focus)
        ps=[patch('arybot.polaris.win32gui',self.s,create=True),patch('arybot.polaris.pyautogui',
            SimpleNamespace(click=self.s.click,failSafeCheck=lambda:None),create=True)]
        for p in ps:p.start();self.addCleanup(p.stop)
    def test_keyboard_then_tab_before_verification(self):
        self.c.escribir(10,20,'folio','000123')
        self.assertEqual(self.s.committed[20],'000123');self.assertLess(self.s.actions.index('keyboard'),self.s.actions.index('tab'))
        self.assertNotIn('enter',self.s.actions)
    def test_value_erased_when_exiting_blocks(self):
        self.s.erase_on_tab=True
        with self.assertRaises(CapturaFacturaError):self.c.escribir(10,20,'folio','000123')
        self.assertNotIn('enter',self.s.actions)
    def test_wrong_written_value_stops(self):
        self.s.wrong=True
        with self.assertRaises(CapturaFacturaError):self.c.escribir(10,20,'rfc','AAA010101AAA')
    def test_cannot_exit_validation_stops(self):
        self.s.sticky=True
        with self.assertRaises(CapturaFacturaError):self.c.escribir(10,20,'folio','123')
        self.assertEqual(self.s.actions.count('tab'),1)
    def test_stale_clipboard_not_evidence(self):
        self.s.unread=True;self.s.clip_stale=True
        with self.assertRaises(CapturaFacturaError):self.c.escribir(10,20,'correo','cliente@example.com')
    def test_fresh_copy_after_blur_supported(self):
        self.s.unread=True
        self.c.escribir(10,20,'correo','cliente@example.com')
        self.assertEqual(self.s.focus,21);self.assertEqual(self.s.actions.count('tab'),1)
        self.assertIn('copy_new',self.s.actions)
    def test_other_app_not_touched(self):
        self.s.fg=999
        with self.assertRaises(CapturaFacturaError):self.c.escribir(10,20,'folio','123')
        self.assertFalse(self.s.actions)
    def test_disabled_input_stops(self):
        self.s.nodes[20]['enabled']=False
        with self.assertRaises(CapturaFacturaError):self.c.escribir(10,20,'folio','123')
        self.assertNotIn('keyboard',self.s.actions)
    def test_selector_uses_real_catalog_not_hardcoded_order(self):
        self.c.seleccionar(10,(.39,.255),'pago','EFECTIVO')
        self.assertEqual(self.s.indices[30],1);self.assertEqual(self.s.committed[30],'EFECTIVO')
        self.assertIn('down',self.s.actions)
    def test_default_g03_is_explicit_selected(self):
        self.c.seleccionar(10,(.55,.327),'uso','GASTOS EN GENERAL.')
        for k in ('home','down','up','enter','tab'):self.assertIn(k,self.s.actions)
        self.assertEqual(self.s.committed[31],'GASTOS EN GENERAL.')
    def test_missing_option_no_input(self):
        with self.assertRaises(CapturaFacturaError):self.c.seleccionar(10,(.39,.255),'pago','OTRA')
        self.assertFalse(self.s.actions)
    def test_duplicate_option_no_arbitrary_choice(self):
        self.s.items[30].append('EFECTIVO')
        with self.assertRaises(CapturaFacturaError):self.c.seleccionar(10,(.39,.255),'pago','EFECTIVO')
        self.assertFalse(self.s.actions)
    def test_enter_not_sent_if_dropdown_wont_open(self):
        self.s.drop_opens=False
        with self.assertRaises(CapturaFacturaError):self.c.seleccionar(10,(.39,.255),'pago','EFECTIVO')
        self.assertNotIn('enter',self.s.actions)
    def test_no_selected_index_even_if_text_visible_blocks(self):
        self.s.refuse_selection=True
        with self.assertRaises(CapturaFacturaError):self.c.seleccionar(10,(.39,.255),'pago','EFECTIVO')
        self.assertEqual(self.s.actions.count('enter'),1)
    def test_combo_not_edit(self):
        with self.assertRaises(CapturaFacturaError):self.c.control(10,(.39,.255),'folio')
    def test_control_outside_root(self):
        with self.assertRaises(CapturaFacturaError):self.c.control(10,(1.2,.3),'folio')
    def test_two_edits_ambiguous_stops(self):
        self.s.nodes[22]=dict(self.s.nodes[20])
        with self.assertRaises(CapturaFacturaError):self.c.control(10,(.1,.565),'folio')
    def test_diagnostic_contains_no_values(self):
        self.c.escribir(10,20,'correo','cliente@example.com')
        text=(Path(self.tmp.name)/'logs/diagnostico_captura_factura.json').read_text()
        self.assertNotIn('cliente@example.com',text);self.assertIn('SALIDA_DE_CAMPO_CONFIRMADA',text)
    def test_leading_zero_folio_supported_not_arbitrary_letters(self):
        self.assertTrue(coincide('folio','123','000123'))
        self.assertFalse(coincide('folio','123A','123'));self.assertFalse(coincide('folio','','123'))
    def test_receiver_mismatch_blocks_without_editing(self):
        self._receptor('BBB010101BBB')
        with self.assertRaises(CapturaFacturaError):self.c.verificar_receptor(10,'AAA010101AAA')
        self.assertFalse(self.s.actions)
    def _receptor(self,rfc='AAA010101AAA'):
        vals={'cliente':'123','rfc':rfc,'nombre':'CLIENTE EJEMPLO','cp':'27000','regimen':'601 - GENERAL DE LEY'}
        for i,(name,pt) in enumerate(self.c.RECEPTOR.items(),50):
            x=int(pt[0]*720);y=int(pt[1]*550)
            self.s.nodes[i]=dict(parent=10,cls='TDBEdit',r=Rect(x-10,y-8,x+20,y+8),enabled=False,text=vals[name])
    def test_receiver_readonly_valid(self):
        self._receptor();self.c.verificar_receptor(10,'AAA010101AAA');self.assertFalse(self.s.actions)
    def test_receiver_empty_cp_blocks(self):
        self._receptor();self.s.nodes[53]['text']=''
        with self.assertRaises(CapturaFacturaError):self.c.verificar_receptor(10,'AAA010101AAA')
        self.assertFalse(self.s.actions)

class Dispatch317Tests(unittest.TestCase):
    def test_default_payment_must_select_not_return(self):
        b=PolarisBot.__new__(PolarisBot);cap=Mock();cap.seleccionar.return_value='EFECTIVO'
        b._captura_factura=lambda:cap;b._activate=Mock();b.log=Mock()
        b._set_payment(100,'EFECTIVO');cap.seleccionar.assert_called_once()
    def test_default_usage_must_select_not_return(self):
        b=PolarisBot.__new__(PolarisBot);cap=Mock();cap.seleccionar.return_value='GASTOS EN GENERAL.'
        b._captura_factura=lambda:cap;b._activate=Mock();b.log=Mock()
        b._set_cfdi_usage(100,'G03');cap.seleccionar.assert_called_once_with(100,b.FACTURA['uso_cfdi'],'uso_cfdi','GASTOS EN GENERAL.')
    def test_blank_payment_rejected_not_defaulted(self):
        b=PolarisBot.__new__(PolarisBot)
        with self.assertRaises(RuntimeError):b._set_payment(100,'')

if __name__=='__main__':unittest.main()
