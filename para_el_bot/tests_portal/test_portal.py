"""Pruebas locales: NO abren Polaris ni se conectan a Gmail.
Ejecutar desde la carpeta del bot: python -m unittest discover -s tests_portal -v
"""
from __future__ import annotations
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from arybot.cola_operaciones import ColaOperaciones
from arybot.portal_http import PortalWSGI, DEFAULTS, cargar_ajustes, cargar_secreto, Limitador
from arybot.portal_modelo import catalogos, PAGOS
from arybot.parser import STATIONS, USO_CFDI

class PolarisProhibido:
    def __getattr__(self, name):
        raise AssertionError('La recepción web no debe operar Polaris: ' + name)

class ClienteHTTP:
    def __init__(self, app):
        self.app = app
        self.cookie = ''
        self.csrf = ''
        self.meta = self.request('GET', '/opciones')[2]
        self.csrf = self.meta['csrf']

    def request(self, method, path, data=None, *, origin='http://127.0.0.1:8765', headers=None, request_id='a'*32):
        raw = json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else b''
        env = {'REQUEST_METHOD':method, 'PATH_INFO':'/api/facturacion'+path,
               'CONTENT_TYPE':'application/json', 'CONTENT_LENGTH':str(len(raw)),
               'wsgi.input':io.BytesIO(raw), 'REMOTE_ADDR':'127.0.0.1',
               'HTTP_COOKIE':self.cookie, 'HTTP_X_ARY_CSRF':self.csrf,
               'HTTP_X_ARY_REQUEST_ID':request_id, 'HTTP_X_ARY_MODE':'seguro'}
        if origin is not None:
            env['HTTP_ORIGIN']=origin
        env.update(headers or {})
        captured = {}
        def start(status, result_headers):
            captured['code']=int(status.split()[0]);captured['headers']=dict(result_headers)
        content = b''.join(self.app(env,start))
        cookie=captured['headers'].get('Set-Cookie')
        if cookie:
            self.cookie=cookie.split(';',1)[0]
        if captured['headers']['Content-Type'].startswith('application/json'):
            content=json.loads(content)
        return captured['code'],captured['headers'],content

class TestPortal(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
        self.cfg={'app':{'modo_prueba':True},'cola':{'avisar_cliente':False}}
        self.settings=dict(DEFAULTS)
        self.cola=ColaOperaciones(self.base,self.cfg,PolarisProhibido(),lambda *_:None)
        self.now=[1800000000.0]
        self.app=PortalWSGI(self.cola,self.cfg,self.settings,self.base,secret=b'T'*48,clock=lambda:self.now[0])
        self.client=ClienteHTTP(self.app)
        self.invoice={'tipo':'FACTURA','estacion':'ARY I','rfc':'ABC010101AB1',
                      'ticket':'000123','forma_pago':'TARJETA DE DEBITO','uso_cfdi':'G03',
                      'correo':'cliente@example.com','correo_confirmacion':'cliente@example.com',
                      'confirmado':True,'sitio_web':''}
        self.alta={'tipo':'ALTA','estacion':'ARY VI','rfc':'ABC010101AB1','idcif':'00000000001',
                   'telefono':'5555555555','correo':'cliente@example.com',
                   'correo_confirmacion':'cliente@example.com','confirmado':True,'sitio_web':''}

    def tearDown(self): self.tmp.cleanup()
    def send(self,body=None,**kwargs):return self.client.request('POST','/solicitudes',body or self.invoice,**kwargs)

    def test_catalogos_iguales_a_fuente(self):
        out=self.client.meta
        self.assertEqual([x['valor'] for x in out['estaciones']],list(STATIONS))
        self.assertEqual([x['valor'] for x in out['usos_cfdi']],list(USO_CFDI))
        self.assertEqual(len(out['formas_pago']),4)

    def test_factura_se_registra_en_cola_original(self):
        code,_,data=self.send()
        self.assertEqual(code,201)
        job=self.cola.store.list()[0]
        self.assertEqual(job['tipo'],'FACTURA');self.assertEqual(job['origen'],'Portal web')
        self.assertEqual(job['datos']['ticket'],'000123')
        self.assertEqual(job['datos']['forma_pago'],'TARJETA DE DEBITO')
        self.assertEqual(job['datos']['uso_cfdi'],USO_CFDI['G03'])
        self.assertEqual(job['estado'],'EN_COLA');self.assertTrue(job['seguro'])
        self.assertIsNone(self.cola.thread)
        self.assertNotIn('rfc',data);self.assertNotIn('correo',data)

    def test_cuatro_pagos_se_conservan(self):
        for i,pago in enumerate(PAGOS,1):
            code,_,_=self.send(dict(self.invoice,forma_pago=pago,ticket=str(i)),request_id=f'{i:032x}')
            self.assertEqual(code,201)
            self.assertIn(pago,[j['datos']['forma_pago'] for j in self.cola.store.list()])

    def test_pago_obligatorio_y_sin_default(self):
        for value in ['', 'POR DEFINIR','PUE','PPD',None,'OTRO']:
            self.assertEqual(self.send(dict(self.invoice,forma_pago=value))[0],400)
        self.assertEqual(self.cola.store.list(),[])

    def test_alta_conserva_cuatro_campos_y_ceros(self):
        code,_,_=self.send(self.alta)
        self.assertEqual(code,201)
        data=self.cola.store.list()[0]['datos']
        self.assertEqual(set(data),{'estacion','rfc','idcif','telefono','correo'})
        self.assertEqual(data['idcif'],'00000000001')

    def test_alta_idcif_invalido_rechazado(self):
        self.assertEqual(self.send(dict(self.alta,idcif='123'))[0],400)
        self.assertFalse(self.cola.store.list())

    def test_rfc_generico_no_alta(self):
        self.assertEqual(self.send(dict(self.alta,rfc='XAXX010101000'))[0],400)

    def test_correo_confirmado_y_unico(self):
        for fields in [dict(correo_confirmacion='otro@example.com'),dict(correo='a@x.com\r\nBcc: x@y.com'),
                       dict(correo='no-es-correo',correo_confirmacion='no-es-correo')]:
            self.assertEqual(self.send(dict(self.invoice,**fields))[0],400)

    def test_estacion_ticket_rfc_uso_validos(self):
        for fields in [dict(estacion='ARY XXX'),dict(ticket='0'),dict(ticket='1;drop'),dict(rfc='XX'),dict(uso_cfdi='ZZZ')]:
            self.assertEqual(self.send(dict(self.invoice,**fields))[0],400)

    def test_campos_ajenos_y_comandos_rechazados(self):
        for key,value in [('safe',False),('modo_prueba',False),('comando','aceptar'),('nombre','otro')]:
            self.assertEqual(self.send(dict(self.invoice,**{key:value}))[0],400)
        self.assertEqual(self.send(dict(self.invoice,confirmado=False))[0],400)
        self.assertEqual(self.send(dict(self.invoice,sitio_web='robot'))[0],400)

    def test_idempotencia_misma_solicitud(self):
        first=self.send();second=self.send()
        self.assertEqual(first[0],201);self.assertEqual(second[0],200)
        self.assertEqual(first[2]['referencia'],second[2]['referencia'])
        self.assertEqual(len(self.cola.store.list()),1)

    def test_idempotencia_concurrente(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses=list(pool.map(lambda _:self.send(),range(4)))
        self.assertEqual(sorted(r[0] for r in responses),[200,200,200,201])
        self.assertEqual(len(self.cola.store.list()),1)

    def test_id_mismo_datos_distintos_rechazado(self):
        self.send()
        self.assertEqual(self.send(dict(self.invoice,ticket='124'))[0],409)
        self.assertEqual(len(self.cola.store.list()),1)

    def test_mismo_ticket_otro_id_otra_sesion_no_duplica(self):
        self.send()
        self.assertEqual(self.send(request_id='b'*32)[0],409)
        other=ClienteHTTP(self.app)
        self.assertEqual(other.request('POST','/solicitudes',self.invoice)[0],409)
        self.assertEqual(len(self.cola.store.list()),1)

    def test_recepcion_recupera_sin_datos_fiscales(self):
        _,_,data=self.send()
        code,_,result=self.client.request('POST','/recepcion',{'request_id':'a'*32})
        self.assertEqual(code,200);self.assertEqual(result['referencia'],data['referencia'])
        for key in ['rfc','idcif','correo','datos']:self.assertNotIn(key,result)
        other=ClienteHTTP(self.app)
        self.assertEqual(other.request('POST','/recepcion',{'request_id':'a'*32})[0],404)

    def test_csrf_y_origen_rechazados(self):
        self.assertEqual(self.send(origin='https://malicioso.example')[0],403)
        self.assertEqual(self.send(origin=None)[0],403)
        self.assertEqual(self.send(headers={'HTTP_X_ARY_CSRF':'x'})[0],403)
        self.assertEqual(self.send(headers={'HTTP_COOKIE':'ary_portal_session=alterado'})[0],403)
        self.assertEqual(self.send(headers={'HTTP_SEC_FETCH_SITE':'cross-site'})[0],403)
        self.assertFalse(self.cola.store.list())

    def test_sesion_vencida(self):
        self.now[0]+=86401
        self.assertEqual(self.send()[0],403)

    def test_seguimiento_valido_y_tamper(self):
        _,_,created=self.send()
        token=created['seguimiento']
        code,_,data=self.client.request('POST','/estado',{'seguimiento':token})
        self.assertEqual(code,200);self.assertEqual(data['estado'],'EN_COLA')
        self.assertNotIn('seguimiento',data)
        self.assertEqual(self.client.request('POST','/estado',{'seguimiento':token+'x'})[0],404)

    def test_no_expone_motivos_polarias(self):
        _,_,created=self.send()
        job=self.cola.store.list()[0]
        self.cola.store.finish(job['id'],'REVISION_REQUERIDA',reason='SQL privado RFC 123 contraseña',hold=True)
        _,_,result=self.client.request('POST','/estado',{'seguimiento':created['seguimiento']})
        self.assertNotIn('SQL',json.dumps(result))
        self.assertEqual(result['estado'],'REVISION_REQUERIDA')

    def test_doble_permiso_produccion(self):
        self.cfg['app']['modo_prueba']=False
        self.assertTrue(self.app.modo_seguro())
        self.settings['permitir_facturacion_real']=True
        self.assertFalse(self.app.modo_seguro())
        self.send(headers={'HTTP_X_ARY_MODE':'real'})
        self.assertFalse(self.cola.store.list()[0]['seguro'])
        self.cfg['app']['modo_prueba']=True
        self.assertTrue(self.app.modo_seguro())

    def test_modo_cambiado_no_emite_sin_advertir(self):
        self.cfg['app']['modo_prueba']=False
        self.settings['permitir_facturacion_real']=True
        self.assertEqual(self.send()[0],409)
        self.assertEqual(self.cola.store.list(),[])

    def test_modo_inventado_y_csrf_unicode_rechazados(self):
        self.assertEqual(self.send(headers={'HTTP_X_ARY_MODE':'real'})[0],409)
        self.assertEqual(self.send(headers={'HTTP_X_ARY_CSRF':'ñ'})[0],403)
        self.assertEqual(self.send(headers={'HTTP_X_ARY_MODE':''})[0],409)

    def test_solicitud_vieja_conserva_modo(self):
        self.send()
        self.cfg['app']['modo_prueba']=False;self.settings['permitir_facturacion_real']=True
        self.assertTrue(self.send()[2]['modo_seguro'])
        self.assertEqual(len(self.cola.store.list()),1)

    def test_pausa_y_limite_cola(self):
        self.app.pausar_recepcion()
        self.assertEqual(self.send()[0],503)
        self.app.aceptando=True;self.settings['max_solicitudes_pendientes']=1
        self.assertEqual(self.send()[0],201)
        self.assertEqual(self.send(dict(self.invoice,ticket='2'),request_id='b'*32)[0],503)
        self.assertEqual(self.send()[0],200)

    def test_limite_por_conexion(self):
        self.settings['limite_envios_10_minutos']=1
        self.assertEqual(self.send()[0],201)
        self.assertEqual(self.send(dict(self.invoice,ticket='2'),request_id='b'*32)[0],429)
        self.assertEqual(self.send()[0],200)

    def test_body_tipo_tamano_y_metodos(self):
        self.assertEqual(self.send(headers={'CONTENT_TYPE':'text/plain'})[0],415)
        self.assertEqual(self.send(headers={'CONTENT_LENGTH':'999999'})[0],413)
        self.assertEqual(self.client.request('GET','/solicitudes')[0],405)
        self.assertEqual(self.client.request('POST','/desconocida',{})[0],404)

    def test_cookie_y_cabeceras(self):
        client=ClienteHTTP(self.app)
        self.assertTrue(client.cookie.startswith('ary_portal_session='))
        code,headers,_=self.client.request('GET','/opciones',headers={'HTTP_COOKIE':''})
        self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(headers['X-Frame-Options'],'DENY')
        self.assertNotIn('Access-Control-Allow-Origin',headers)

    def test_no_sirve_credenciales_ni_carpetas(self):
        for path in ['/../config.json','/token.json','/credentials.json','/data/cola_operaciones.sqlite3']:
            raw=io.BytesIO();captured={}
            env={'PATH_INFO':path,'REQUEST_METHOD':'GET','wsgi.input':raw}
            result=b''.join(self.app(env,lambda status,headers:captured.update(code=int(status[:3]))))
            self.assertEqual(captured['code'],404)

    def test_secreto_persistente(self):
        first=cargar_secreto(self.base)
        self.assertGreaterEqual(len(first),32)
        self.assertEqual(first,cargar_secreto(self.base))

    def test_config_publica_requiere_https(self):
        path=self.base/'portal_config.json'
        path.write_text(json.dumps(dict(DEFAULTS,origenes_permitidos=['http://example.com'])))
        with self.assertRaises(ValueError):cargar_ajustes(self.base)
        path.write_text(json.dumps(dict(DEFAULTS,origenes_permitidos=['https://example.com'],https_publico=True)))
        self.assertTrue(cargar_ajustes(self.base)['https_publico'])
        for origin in ['https://*.example.com','https://example.com/','https://a:b@example.com']:
            path.write_text(json.dumps(dict(DEFAULTS,origenes_permitidos=[origin],https_publico=True)))
            with self.assertRaises(ValueError):cargar_ajustes(self.base)

    def test_modo_publico_cookie_secure(self):
        self.settings['https_publico']=True
        _,headers,_=self.client.request('GET','/opciones',headers={'HTTP_COOKIE':''})
        self.assertIn('; Secure',headers['Set-Cookie'])

if __name__=='__main__':unittest.main()
