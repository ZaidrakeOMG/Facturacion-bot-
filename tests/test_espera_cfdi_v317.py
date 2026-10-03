import unittest
from arybot.dialogos_cfdi import ControlDialogo as D, clasificar_dialogo, es_candidato
from arybot.polaris import Rect
from arybot.factura_final import FinalFacturaError
from test_final_factura_v316 import FinalTests, sol

class ClasificacionTests(unittest.TestCase):
    def test_dialog_progress_not_error(self):
        self.assertEqual(clasificar_dialogo('#32770','',[D('TLabel','Generando el CFDI...'),D('msctls_progress32')]),'PROGRESO_CFDI')
    def test_empty_parameter_error_is_not_hidden(self):
        self.assertEqual(clasificar_dialogo('#32770','Polaris',[D('Static','Parámetro vacío'),D('Button','Aceptar')]),'AVISO')
    def test_progress_plus_error_is_error(self):
        self.assertEqual(clasificar_dialogo('#32770','',[D('TLabel','Generando el CFDI...'),D('Static','Parámetro vacío')]),'AVISO')
    def test_xml_pdf_progress_classification(self):
        for s in ('Generando el PDF...','Generando el XML...'):
            with self.subTest(s=s):self.assertEqual(clasificar_dialogo('TForm','',[D('TLabel',s)]),'PROGRESO_ARCHIVOS')
    def test_email_progress(self):
        self.assertEqual(clasificar_dialogo('TForm','Enviando correo electrónico...',[D('TProgressBar')]),'PROGRESO_CORREO')
    def test_known_progress_with_accept_stops(self):
        self.assertEqual(clasificar_dialogo('#32770','Generando el CFDI...',[D('Button','Aceptar')]),'AVISO')
    def test_unknown_dialog_stops(self):self.assertEqual(clasificar_dialogo('#32770','',[D('Static','Desconocido')]),'AVISO')
    def test_edit_text_never_treated_as_progress(self):
        self.assertEqual(clasificar_dialogo('#32770','',[D('TEdit','Generando el CFDI...')]),'AVISO')
    def test_cancel_progress_not_clicked_but_can_wait(self):
        self.assertEqual(clasificar_dialogo('#32770','Generando el CFDI...',[D('TProgressBar'),D('Button','Cancelar')]),'PROGRESO_CFDI')
    def test_progress_control_not_dialog(self):self.assertFalse(es_candidato('TProgressBar'))
    def test_confirmation_title_blocks_even_with_progress(self):
        self.assertEqual(clasificar_dialogo('#32770','Confirmación',[D('Static','Generando el CFDI...')]),'AVISO')

# Reutiliza sólo el armado de la pantalla; no cuenta otra vez los tests base.
class EsperaTests(unittest.TestCase):
    setUp=FinalTests.setUp
    run_final=FinalTests.run_final
    def progreso(self,text='Generando el CFDI...',visible=True):
        self.s.nodes[400]=dict(parent=1,cls='#32770',text='',r=Rect(360,320,640,400),vis=visible,enabled=True,state=0)
        self.s.nodes[401]=dict(parent=400,cls='Static',text=text,r=Rect(390,335,620,360),vis=True,enabled=True,state=0)
        self.s.nodes[402]=dict(parent=400,cls='msctls_progress32',text='',r=Rect(385,365,625,385),vis=True,enabled=True,state=0)
    def test_preexisting_progress_blocks_before_accept(self):
        self.progreso()
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertNotIn(110,self.s.actions)
    def test_progress_then_ready_is_waited_not_accepted(self):
        self.progreso(visible=False)
        def tick(s):
            s.nodes[400]['vis']=110 in s.actions and s.now<4
        self.s.after_sleep=tick
        self.assertEqual(self.run_final(),'ENVIO_SOLICITADO')
        self.assertGreaterEqual(self.s.now,4);self.assertNotIn(400,self.s.actions)
        self.assertEqual(self.s.actions.count(110),1);self.assertEqual(self.s.actions.count(240),1)
    def test_progress_forever_no_repeat(self):
        self.progreso(visible=False)
        self.s.after_sleep=lambda s:s.nodes[400].update(vis=110 in s.actions)
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertEqual(self.s.actions.count(110),1);self.assertNotIn(240,self.s.actions)
    def test_empty_parameter_after_progress_blocks_and_preserves_history(self):
        self.progreso(visible=False)
        def tick(s):
            if 110 in s.actions:
                s.nodes[400]['vis']=True
                if s.now>=2:s.nodes[401]['text']='Parámetro vacío'
        self.s.after_sleep=tick
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertTrue(self.reg.path.exists());self.assertEqual(self.s.actions.count(110),1)
        self.assertNotIn(240,self.s.actions)
    def test_error_and_send_ready_prioritize_error(self):
        self.s.after_sleep=lambda s:s.nodes[300].update(vis=s.nodes[200]['vis'])
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertNotIn(240,self.s.actions)
    def test_progress_only_after_email_waits(self):
        self.progreso('Enviando correo...',False)
        due=[None]
        def tick(s):
            if 240 in s.actions:
                if due[0] is None:due[0]=s.now+3
                s.nodes[400]['vis']=s.now<due[0]
        self.s.after_sleep=tick
        self.assertEqual(self.run_final(),'ENVIO_SOLICITADO')
        self.assertGreaterEqual(self.s.now,due[0]);self.assertEqual(self.s.actions.count(240),1)
    def test_capture_error_before_accept_cannot_emit(self):
        # Sigue siendo una prueba de envío: el fallo aquí ocurre tras emitir,
        # por eso debe preservar su registro y no volver a Aceptar factura.
        self.ui._captura_correo.escribir=lambda *a:(_ for _ in ()).throw(RuntimeError('salida vacía'))
        with self.assertRaises(FinalFacturaError):self.run_final()
        self.assertTrue(self.reg.path.exists());self.assertNotIn(240,self.s.actions)

# No exportar FinalTests como TestCase en este módulo de discovery.
del FinalTests
if __name__=='__main__':unittest.main()
