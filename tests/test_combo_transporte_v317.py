import ctypes
import unittest
from arybot.factura_captura import LecturaCombo, CapturaFacturaError
from arybot.alta_texto import TextoControlError

class ComboNativeTests(unittest.TestCase):
    def make(self,items=None,sel=0,errlen=None):
        c=LecturaCombo();data=items or ['EFECTIVO','TARJETA DE CRÉDITO'];calls=[]
        def message(h,m,wp=0,lp=0):
            calls.append(m)
            if m==c.CB_GETCOUNT:return len(data)
            if m==c.CB_GETCURSEL:return ctypes.c_size_t(sel).value
            if m==c.CB_GETLBTEXTLEN:return errlen if errlen is not None else len(data[wp])
            if m==c.CB_GETLBTEXT:
                buf=ctypes.create_unicode_buffer(data[wp]);ctypes.memmove(lp,buf,ctypes.sizeof(buf));return len(data[wp])
            if m==c.CB_GETDROPPEDSTATE:return 1
            raise AssertionError(m)
        c._message=message;return c,calls
    def test_reads_catalog_and_selection_unicode(self):
        c,calls=self.make(sel=1);self.assertEqual(c.actual(12),(1,'TARJETA DE CRÉDITO'))
        self.assertNotIn(0x14e,calls)  # CB_SETCURSEL never sent
    def test_error_signed_result_not_used_as_index(self):
        c,_=self.make(sel=-1)
        with self.assertRaises(CapturaFacturaError):c.actual(12)
    def test_empty_option_no_buffer_access(self):
        c,calls=self.make(errlen=0)
        with self.assertRaises(CapturaFacturaError):c.opciones(12)
        self.assertNotIn(c.CB_GETLBTEXT,calls)
    def test_big_option_no_buffer_allocation(self):
        c,calls=self.make(errlen=100000)
        with self.assertRaises(CapturaFacturaError):c.opciones(12)
    def test_max_catalog_bound(self):
        c,_=self.make(items=['A']*121)
        with self.assertRaises(CapturaFacturaError):c.opciones(12)
    def test_all_messages_are_reads(self):
        self.assertEqual(LecturaCombo.MENSAJES,{0x000d,0x146,0x147,0x148,0x149,0x157})
    def test_timeout_propagates_not_success(self):
        c=LecturaCombo()
        def fail(*a):raise TextoControlError('CONTROL_SIN_RESPUESTA')
        c._message=fail
        with self.assertRaises(TextoControlError):c.actual(1)
if __name__=='__main__':unittest.main()
