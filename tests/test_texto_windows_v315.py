"""Pruebas del límite nativo y del portapapeles con un Win32 falso, no Windows real."""
import ctypes
import unittest
from arybot.alta_texto import TextoWindows, TextoControlError, literal_teclas

class FakeAPI:
    def __init__(self):
        self.text='';self.clip='';self.seq=7;self.owner=0;self.messages=[];self.selected=False
        self.timeout=False;self.copy_noop=False;self.copy_foreign=False;self.copy_race=False
        self.long=False;self.time=0;self.opened=False;self.open_busy=0;self.keys=[];self.partial=''
        self.formats=True
    def SendMessageTimeoutW(self,h,msg,wp,lp,flags,timeout,out):
        self.messages.append((h,msg,wp,lp,flags,timeout))
        if self.timeout:return 0
        ret=0
        if msg==0xD:
            value=self.text[:wp-1];buf=(ctypes.c_wchar*wp).from_address(lp);buf.value=value
            ret=wp-1 if self.long else len(value)
        elif msg==0xB1:self.selected=True
        elif msg==0x302:
            self.text=self.clip if self.selected else self.text+self.clip;self.selected=False
        elif msg==0x301 and not self.copy_noop:
            self.clip=self.text;self.seq+=1;self.owner=888 if self.copy_foreign else h
        ctypes.cast(out,ctypes.POINTER(ctypes.c_size_t))[0]=ret
        return 1
    def GetClipboardSequenceNumber(self):return self.seq
    def GetClipboardOwner(self):return self.owner
    def OpenClipboard(self):
        if self.open_busy>0:self.open_busy-=1;raise OSError('busy')
        self.opened=True
    def CloseClipboard(self):self.opened=False
    def EmptyClipboard(self):self.clip='';self.seq+=1;self.owner=999
    def SetClipboardText(self,value,fmt):self.clip=value;self.seq+=1
    def IsClipboardFormatAvailable(self,fmt):return self.formats
    def GetClipboardData(self,fmt):
        if self.copy_race:self.seq+=1
        return self.clip
    def sleep(self,v):self.time+=v
    def key(self,k,**kw):self.keys.append((k,kw))

class NativeTests(unittest.TestCase):
    def setUp(self):
        self.api=FakeAPI();self.checks=0
        self.io=TextoWindows(user32=self.api,clipboard=self.api,pid=lambda h:10 if h==123 else h,
                             reloj=lambda:self.api.time,dormir=self.api.sleep,teclas=self.api.key)
    def guard(self):self.checks+=1
    def test_read_sends_gettext_without_gettextlength(self):
        self.api.text='AAA010101AAA';self.assertEqual(self.io.leer(123),self.api.text)
        self.assertEqual([x[1] for x in self.api.messages],[0xD])
    def test_native_unicode_buffer(self):
        self.api.text='NIÑO & COMPAÑÍA';self.assertEqual(self.io.leer(123),self.api.text)
    def test_empty_response_distinct_from_timeout(self):
        self.assertEqual(self.io.leer(123),'');self.api.timeout=True
        with self.assertRaises(TextoControlError):self.io.leer(123)
    def test_truncated_response_rejected(self):
        self.api.long=True
        with self.assertRaisesRegex(TextoControlError,'TRUNCADA'):self.io.leer(123)
    def test_native_timeout_flags_and_bound(self):
        self.io.leer(123);row=self.api.messages[0];self.assertEqual(row[-2],0x22);self.assertLessEqual(row[-1],1000)
    def test_other_message_rejected(self):
        with self.assertRaises(TextoControlError):self.io._message(123,0xC) # WM_SETTEXT
        self.assertEqual(self.api.messages,[])
    def test_zero_handle_rejected(self):
        with self.assertRaises(TextoControlError):self.io.leer(0)
    def test_paste_replaces_not_appends(self):
        self.api.text='PARTIAL';self.io.pegar(123,'AAA010101AAA',self.guard)
        self.assertEqual(self.api.text,'AAA010101AAA')
        self.assertEqual([x[1] for x in self.api.messages],[0xB1,0x302])
    def test_clipboard_busy_retries_before_paste(self):
        self.api.open_busy=2;self.io.pegar(123,'AAA',self.guard)
        self.assertEqual(self.api.text,'AAA');self.assertFalse(self.api.opened)
    def test_clipboard_timeout_does_not_paste(self):
        self.api.open_busy=999
        with self.assertRaises(TextoControlError):self.io.pegar(123,'AAA',self.guard)
        self.assertNotIn(0x302,[x[1] for x in self.api.messages])
    def test_copy_returns_actual_text_not_clipboard_input(self):
        self.api.clip='EXPECTED';self.api.text='ACTUAL'
        self.assertEqual(self.io.copiar(123,self.guard),'ACTUAL')
    def test_stale_clipboard_expected_value_is_not_evidence(self):
        self.api.clip='EXPECTED';self.api.text='WRONG';self.api.copy_noop=True
        with self.assertRaisesRegex(TextoControlError,'SIN_COPIA_NUEVA'):self.io.copiar(123,self.guard)
    def test_new_clipboard_but_foreign_owner_rejected(self):
        self.api.copy_foreign=True
        with self.assertRaisesRegex(TextoControlError,'OTRA_APLICACION'):self.io.copiar(123,self.guard)
    def test_missing_sequence_rejected(self):
        self.api.seq=0
        with self.assertRaisesRegex(TextoControlError,'SECUENCIA'):self.io.copiar(123,self.guard)
    def test_copy_changed_during_read_rejected(self):
        self.api.copy_race=True
        with self.assertRaisesRegex(TextoControlError,'CAMBIO'):self.io.copiar(123,self.guard)
    def test_clipboard_nontext_rejected(self):
        self.api.formats=False
        with self.assertRaisesRegex(TextoControlError,'UNICODE'):self.io.copiar(123,self.guard)
    def test_focus_loss_prevents_copy(self):
        def stop():raise RuntimeError('focus lost')
        with self.assertRaises(RuntimeError):self.io.copiar(123,stop)
        self.assertEqual(self.api.messages,[])
    def test_focus_loss_after_clipboard_before_paste_stops(self):
        count=[0]
        def stop():
            count[0]+=1
            if count[0]==3:raise RuntimeError('focus')
        with self.assertRaises(RuntimeError):self.io.pegar(123,'AAA',stop)
        self.assertEqual(self.api.text,'')
    def test_type_guards_every_character(self):
        self.io.teclear(123,'AB1',self.guard);self.assertEqual(len(self.api.keys),3)
        self.assertEqual(self.checks,5);self.assertEqual([x[1] for x in self.api.messages],[0xB1])
    def test_type_stops_when_focus_lost_midway(self):
        count=[0]
        def stop():
            count[0]+=1
            if count[0]==3:raise RuntimeError('focus')
        with self.assertRaises(RuntimeError):self.io.teclear(123,'AB1',stop)
        self.assertEqual([x[0] for x in self.api.keys],['A'])
    def test_literal_plus_not_shift(self):self.assertEqual(literal_teclas('a+b@c.com'),'a{+}b@c.com')
    def test_literal_metacharacters_not_shortcuts(self):
        self.assertEqual(literal_teclas('^%~{}'),'{^}{%}{~}{{}{}}')
    def test_no_enter_tab_null_in_input(self):
        for text in ('ABC\n','ABC\r','A\tB','A\x00B'):
            with self.subTest(text=text),self.assertRaises(TextoControlError):self.io.teclear(123,text,self.guard)
        self.assertEqual(self.api.messages,[])
    def test_unicode_rfc_not_forced_to_ascii(self):
        self.io.teclear(123,'AÑ&A',self.guard)
        self.assertEqual([x[0] for x in self.api.keys],['A','Ñ','&','A'])
    def test_no_number_button_or_enter_messages(self):
        self.io.pegar(123,'AAA',self.guard);self.io.leer(123);self.io.copiar(123,self.guard)
        self.assertTrue(set(x[1] for x in self.api.messages)<=TextoWindows.MENSAJES)

if __name__=='__main__':unittest.main()
