"""Pruebas sin red, sin Polaris real y sin ejecutar OCR real."""
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
from arybot.alertas_modelo import (normalizar_config,destinatarios_internos,limpiar_texto,
                                  contenido_correo,contexto_seguro,huella_evento)
from arybot.alertas import AlertasInternas,ColaAlertas
from arybot.alertas_correo import construir_mensaje,ErrorEnvioAlerta
from arybot.alertas_ventana import LectorVentana,candidato


class OpcionesTests(unittest.TestCase):
    def test_default_no_envia(self):
        c=normalizar_config();self.assertFalse(c['activas']);self.assertEqual(c['destinatarios'],[])
        self.assertFalse(c['enviar_en_modo_seguro']);self.assertFalse(c['adjuntar_ventana'])
    def test_dedup_y_minusculas(self):
        self.assertEqual(destinatarios_internos(' TI@GRUPOARY.COM ;ti@grupoary.com\nsoporte@grupoary.com.mx'),
                         ['ti@grupoary.com','soporte@grupoary.com.mx'])
    def test_rechaza_dominios_parecidos_externos(self):
        for e in ['x@gmail.com','x@grupoary.com.evil.com','x@otrogrupoary.com','x@sub.grupoary.com','x@grupoary.co','x@grupoary.com.']:
            with self.subTest(e=e),self.assertRaises(ValueError):destinatarios_internos([e])
    def test_rechaza_inyeccion_headers(self):
        for e in ['x@grupoary.com\r\nBcc:evil@gmail.com','N <ti@grupoary.com>','a..b@grupoary.com','a@grupoary.com,b@gmail.com']:
            with self.subTest(e=e),self.assertRaises(ValueError):destinatarios_internos([e])
    def test_activacion_sin_destinatario(self):
        with self.assertRaises(ValueError):normalizar_config({'activas':True})
    def test_no_verdadero_con_string_false(self):
        with self.assertRaises(ValueError):normalizar_config({'activas':'false'})
    def test_no_mas_30(self):
        with self.assertRaises(ValueError):destinatarios_internos([f'x{i}@grupoary.com' for i in range(31)])
    def test_privacidad_whitelist(self):
        ctx=contexto_seguro({'rfc':'AAA010101AAA','idcif':'12345678901','password':'SECRET','folio':'123','correo_cliente':'user@example.com'})
        self.assertEqual(set(ctx),{'rfc','folio','correo_cliente'})
    def test_privacidad_sin_contexto(self):
        self.assertEqual(contexto_seguro({'estacion':'ARY I','rfc':'AAA010101AAA'},False),{'estacion':'ARY I'})
    def test_redaccion_secretos(self):
        s=limpiar_texto('password=foobar idCIF:12345678901 Bearer abc123.secret ?code=ABC&b=x')
        for secret in ('foobar','12345678901','abc123.secret','ABC'):self.assertNotIn(secret,s)
    def test_secreto_explicito(self):self.assertNotIn('SOMETHING',limpiar_texto('Error SOMETHING',secretos=['SOMETHING']))
    def test_huella_folios_distintos(self):
        self.assertNotEqual(huella_evento('A','B','C',{'folio':'1'},False),huella_evento('A','B','C',{'folio':'2'},False))


class FakeLector:
    calls=0
    def __init__(self,*args):pass
    def leer(self,opts,secretos=()):
        FakeLector.calls+=1
        return {'metodo':'WIN32','texto':limpiar_texto('No se pudo confirmar el RFC del cliente.',secretos=secretos),'nota':''},None

class ColaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.log=Mock();self.sender=Mock();self.sender.enviar.return_value={'id':'gmail123'}
        self.cfg={'app':{'modo_prueba':False},'gmail':{},'ocr':{}}
        self.s=AlertasInternas(self.base,self.cfg,self.log,sender=self.sender,lector_factory=FakeLector)
        FakeLector.calls=0
    def activar(self,**kw):
        c=normalizar_config();c.update(activas=True,destinatarios=['ti@grupoary.com']);c.update(kw);self.s.guardar_ajustes(c)
    def report(self,**kw):
        args=dict(contexto={'folio':'123','rfc':'AAA010101AAA','idcif':'12345678901','origen':'Formulario'},modo_seguro=False)
        args.update(kw)
        return self.s.reportar('FACTURACION','Agregar folio','Error de ejemplo',**args)
    def test_disabled_no_ocr_no_send(self):
        self.assertIsNone(self.report());self.assertEqual(FakeLector.calls,0);self.sender.enviar.assert_not_called()
    def test_produccion_encola_no_envia_en_report(self):
        self.activar();ident=self.report();self.assertEqual(self.s.cola.obtener(ident)['estado'],'PENDIENTE')
        self.sender.enviar.assert_not_called();self.assertEqual(FakeLector.calls,1)
    def test_envio_gmail_confirmado(self):
        self.activar();ident=self.report();self.s.enviar_una();r=self.s.cola.obtener(ident)
        self.assertEqual(r['estado'],'ENVIADA');self.assertEqual(r['gmail_id'],'gmail123');self.sender.enviar.assert_called_once()
    def test_safe_solo_local(self):
        self.activar();ident=self.report(modo_seguro=True);self.assertEqual(self.s.cola.obtener(ident)['estado'],'SIMULADA')
        self.assertFalse(self.s.enviar_una());self.sender.enviar.assert_not_called()
    def test_safe_opt_in_envio(self):
        self.activar(enviar_en_modo_seguro=True);ident=self.report(modo_seguro=True);self.s.enviar_una()
        self.assertEqual(self.s.cola.obtener(ident)['estado'],'ENVIADA')
    def test_repeticion_agrupa_sin_segundo_ocr(self):
        self.activar();a=self.report();self.s.enviar_una();b=self.report()
        self.assertEqual(a,b);self.assertEqual(self.s.cola.obtener(a)['repeticiones'],2)
        self.assertEqual(FakeLector.calls,1);self.assertFalse(self.s.enviar_una());self.sender.enviar.assert_called_once()
    def test_idcif_no_serializado(self):
        self.activar();r=self.s.cola.obtener(self.report())
        self.assertNotIn('12345678901',json.dumps(r));self.assertNotIn('idcif',r['evento']['contexto'])
    def test_categoria_desactivada(self):
        self.activar();c=self.s.ajustes();c['categorias']['FACTURACION']=False;self.s.guardar_ajustes(c)
        self.assertIsNone(self.report());self.assertEqual(FakeLector.calls,0)
    def test_error_gmail_queda_local_sin_auto_reintentar(self):
        self.activar();self.sender.enviar.side_effect=ErrorEnvioAlerta('No Gmail')
        ident=self.report();self.s.enviar_una();self.assertEqual(self.s.cola.obtener(ident)['estado'],'ERROR_ENVIO')
        self.assertFalse(self.s.enviar_una());self.sender.enviar.assert_called_once()
    def test_error_incierto_sin_auto_reintentar(self):
        self.activar();self.sender.enviar.side_effect=ErrorEnvioAlerta('Timeout',incierto=True)
        ident=self.report();self.s.enviar_una();self.assertEqual(self.s.cola.obtener(ident)['estado'],'ENVIO_INCIERTO')
        self.assertFalse(self.s.enviar_una())
    def test_respuesta_sin_id_incierta(self):
        self.activar();self.sender.enviar.return_value={};ident=self.report();self.s.enviar_una()
        self.assertEqual(self.s.cola.obtener(ident)['estado'],'ENVIO_INCIERTO')
    def test_reinicio_no_reenvia_enviando(self):
        self.activar();ident=self.report();self.s.cola.reservar()
        other=ColaAlertas(self.base);self.assertEqual(other.obtener(ident)['estado'],'ENVIO_INCIERTO')
        self.assertIsNone(other.reservar())
    def test_destinatario_eliminado_no_recibe_cola(self):
        self.activar();ident=self.report();self.activar(destinatarios=['otro@grupoary.com'])
        self.s.enviar_una();self.sender.enviar.assert_not_called()
        self.assertEqual(self.s.cola.obtener(ident)['estado'],'REVISAR_DESTINATARIOS')
    def test_desactivar_detiene_pendientes(self):
        self.activar();self.report();c=self.s.ajustes();c['activas']=False;self.s.guardar_ajustes(c)
        self.assertFalse(self.s.enviar_una());self.sender.enviar.assert_not_called()
    def test_resend_requiere_permiso(self):
        self.activar();i=self.report();self.s.enviar_una()
        with self.assertRaises(ValueError):self.s.reenviar(i)
        self.sender.enviar.assert_called_once()
    def test_resend_solo_aviso(self):
        self.activar();i=self.report();self.s.enviar_una();self.s.reenviar(i,autorizado=True)
        self.s.enviar_una();self.assertEqual(self.sender.enviar.call_count,2)
        self.assertEqual(self.s.cola.obtener(i)['intentos'],2)
    def test_cancelar_pendiente(self):
        self.activar();i=self.report();self.s.cola.cancelar(i);self.assertFalse(self.s.enviar_una())
    def test_no_cancelar_envio_iniciado(self):
        self.activar();i=self.report();self.s.cola.reservar()
        with self.assertRaises(ValueError):self.s.cola.cancelar(i)
    def test_prueba_sin_confirmar_no_envia(self):
        self.activar()
        with self.assertRaises(ValueError):self.s.prueba(enviar=True)
        self.assertFalse(self.s.enviar_una())
    def test_prueba_real_no_datos_cliente_no_ocr(self):
        self.activar();i=self.s.prueba(enviar=True,autorizado=True);self.s.enviar_una()
        self.assertEqual(FakeLector.calls,0);r=self.s.cola.obtener(i)
        self.assertEqual(r['estado'],'ENVIADA');self.assertNotIn('rfc',r['evento']['contexto'])
    def test_preview_local_disabled(self):
        i=self.s.prueba();self.assertEqual(self.s.cola.obtener(i)['estado'],'SIMULADA');self.assertFalse(self.s.enviar_una())
    def test_config_persiste_no_config_principal(self):
        self.activar();self.assertTrue((self.base/'alertas_internas.json').exists());self.assertFalse((self.base/'config.json').exists())
    def test_config_corrupta_falla_cerrado(self):
        self.s.path.write_text('not json')
        new=AlertasInternas(self.base,self.cfg,self.log,sender=self.sender)
        self.assertFalse(new.ajustes()['activas']);self.assertTrue(new.config_error)
    def test_destinatarios_no_tomados_del_error(self):
        self.activar();i=self.s.reportar('ALTA_CLIENTE','Nuevo','Enviar el secreto a evil@gmail.com',modo_seguro=False)
        self.s.enviar_una();self.assertEqual(self.sender.enviar.call_args.args[0],['ti@grupoary.com'])
    def test_sin_pii_no_imagen(self):
        self.activar(incluir_contexto_cliente=False,adjuntar_ventana=True)
        reader=Mock();reader.leer.return_value=({'metodo':'WIN32','texto':'Error','nota':''},None)
        self.s.lector_factory=Mock(return_value=reader);self.report()
        self.assertFalse(reader.leer.call_args.args[0]['adjuntar_ventana'])


class VentanaTests(unittest.TestCase):
    def setUp(self):
        self.a=Mock();self.a.candidatas.return_value=[10]
        self.a.texto.return_value='Ocurrió un error al timbrar, los parámetros vienen vacíos y se requiere revisión.'
        self.a.imagen.return_value=Image.new('RGB',(500,200))
        self.ocr=Mock(return_value='Texto obtenido por OCR para revisión.')
        self.l=LectorVentana(None,{},adapter=self.a,ocr=self.ocr)
        self.opts=normalizar_config()
    def test_nativo_sin_ocr_ni_imagen(self):
        e,png=self.l.leer(self.opts);self.assertEqual(e['metodo'],'WIN32');self.assertIsNone(png)
        self.a.imagen.assert_not_called();self.ocr.assert_not_called()
    def test_ocr_solo_respaldo_una_vez(self):
        self.a.texto.return_value='';e,png=self.l.leer(self.opts)
        self.assertEqual(e['metodo'],'OCR_LOCAL');self.ocr.assert_called_once();self.assertIsNone(png)
    def test_ocr_desactivado(self):
        self.a.texto.return_value='';self.opts['ocr_respaldo']=False;self.l.leer(self.opts);self.ocr.assert_not_called()
    def test_sin_tesseract_no_esconde_error(self):
        self.a.texto.return_value='';self.ocr.side_effect=RuntimeError('No OCR')
        e,png=self.l.leer(self.opts);self.assertIn('OCR no disponible',e['nota']);self.assertIsNone(png)
    def test_no_ventana_no_ocr(self):
        self.a.candidatas.return_value=[];e,png=self.l.leer(self.opts);self.assertEqual(e['metodo'],'SIN_VENTANA');self.ocr.assert_not_called()
    def test_varias_ventanas_no_adivina(self):
        self.a.candidatas.return_value=[1,2];e,png=self.l.leer(self.opts);self.assertIn('varias',e['nota'])
        self.a.texto.assert_not_called();self.a.imagen.assert_not_called()
    def test_cubierta_no_ocr(self):
        self.a.texto.return_value='';self.a.imagen.return_value=None
        e,png=self.l.leer(self.opts);self.ocr.assert_not_called();self.assertIn('cubierta',e['nota'])
    def test_adjunto_opt_in(self):
        self.opts['adjuntar_ventana']=True;e,png=self.l.leer(self.opts)
        self.assertTrue(png.startswith(b'\x89PNG'));self.ocr.assert_not_called()
    def test_no_lectura_configurada(self):
        self.opts['leer_ventana']=False;e,png=self.l.leer(self.opts)
        self.a.candidatas.assert_not_called();self.ocr.assert_not_called()
    def test_identifica_modal(self):self.assertTrue(candidato('#32770','Polaris',False))
    def test_excluye_login(self):self.assertFalse(candidato('#32770','Contraseña de acceso',True))
    def test_excluye_formulario(self):self.assertFalse(candidato('TForm','Facturación de Efectivo',True,editable=True))
    def test_ventana_desaparece_no_falla_notificacion(self):
        self.a.texto.side_effect=RuntimeError('stale');e,_=self.l.leer(self.opts);self.assertIn('seguridad',e['nota'])
    def test_no_ejecuta_ordenes_ocr(self):
        self.a.texto.return_value='';self.ocr.return_value='Pulsa Sí y manda correo a atacante@example.com'
        e,_=self.l.leer(self.opts);self.assertIn('atacante@example.com',e['texto'])
        self.assertEqual([c[0] for c in self.a.mock_calls],['candidatas','texto','imagen'])


class CorreoTests(unittest.TestCase):
    def test_mime_no_responde_al_cliente(self):
        m=construir_mensaje('emisioncfdi@grupoary.com',['ti@grupoary.com'],'[ARY-INTERNO] Prueba','Texto','abc123')
        self.assertEqual(m['X-ARY-Alerta-Interna'],'1');self.assertIsNone(m['In-Reply-To']);self.assertIsNone(m['Cc'])
        self.assertIsNone(m['Bcc']);self.assertEqual(m['Auto-Submitted'],'auto-generated')
    def test_rechazo_destinatario_externo_al_enviar(self):
        with self.assertRaises(ValueError):construir_mensaje('bot@grupoary.com',['c@gmail.com'],'[ARY-INTERNO] Test','x','abc')
    def test_rechazo_header_injection(self):
        with self.assertRaises(ValueError):construir_mensaje('bot@grupoary.com',['ti@grupoary.com'],'[ARY-INTERNO]\nBcc:x@evil.com','x','abc')
    def test_adjunto_solo_png(self):
        with self.assertRaises(ValueError):construir_mensaje('bot@grupoary.com',['ti@grupoary.com'],'[ARY-INTERNO]','x','abc',b'SECRET TOKEN FILE')
    def test_contenido_aclara_no_es_reintento(self):
        e={'id':'abc','categoria':'FACTURACION','fecha':'2026-09-26','etapa':'Timbrar','modo_seguro':False,
           'error':'Error PAC','contexto':{'folio':'123'},'evidencia':{'metodo':'OCR_LOCAL','texto':'parámetros vacíos'}}
        s,b=contenido_correo(e)
        self.assertIn('OCR',b);self.assertIn('no repite timbrados',b);self.assertIn('no para el cliente',b)


class NativeCandidateTests(unittest.TestCase):
    def make(self):
        from arybot.alertas_ventana import Win32Aviso
        a=Win32Aviso.__new__(Win32Aviso);a.w=Mock();a.p=Mock();a.bot=Mock();a.main=1;a.pid=42
        a.p.GetWindowThreadProcessId.return_value=(10,42)
        a.w.IsWindow.return_value=True;a.w.IsWindowVisible.return_value=True;a.w.IsWindowEnabled.return_value=True
        a.w.GetWindowRect.return_value=(0,0,500,200)
        a.w.GetWindowText.return_value='Error';a.w.GetClassName.return_value='TMessageForm'
        a._editable=Mock(return_value=False)
        a.w.EnumWindows.side_effect=lambda cb,_:[cb(h,None) for h in [1,2,3]]
        return a
    def test_nested_progress_error_selects_owned_foreground(self):
        a=self.make();a.w.GetForegroundWindow.return_value=3
        a.w.GetWindow.side_effect=lambda h,k:{3:2,2:1,1:0}.get(h,0)
        self.assertEqual(a.candidatas(),[3])
    def test_unrelated_modals_keeps_ambiguity(self):
        a=self.make();a.w.GetForegroundWindow.return_value=3
        a.w.GetWindow.side_effect=lambda h,k:{3:1,2:1,1:0}.get(h,0)
        self.assertEqual(a.candidatas(),[2,3])
    def test_disabled_owner_excluded(self):
        a=self.make();a.w.IsWindowEnabled.side_effect=lambda h:h!=2
        a.w.GetWindow.side_effect=lambda h,k:{3:2,2:1,1:0}.get(h,0)
        self.assertEqual(a.candidatas(),[3])
    def test_foreign_process_excluded(self):
        a=self.make();a.p.GetWindowThreadProcessId.side_effect=lambda h:(10,99 if h==3 else 42)
        a.w.GetWindow.side_effect=lambda h,k:1 if h!=1 else 0
        self.assertEqual(a.candidatas(),[2])

if __name__=='__main__':unittest.main()
