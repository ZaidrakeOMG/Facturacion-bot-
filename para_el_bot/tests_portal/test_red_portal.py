"""Pruebas con IPs simuladas, datos sintéticos y la cola original; no usan Polaris."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from arybot.portal_http import PortalWSGI, DEFAULTS, cargar_ajustes
from arybot.portal_red import PortalRedWSGI, validar_red
from arybot.cola_operaciones import ColaOperaciones

CFG = dict(DEFAULTS, escuchar_en='10.20.20.182', proxy_confiable='10.20.20.154',
           origenes_permitidos=['https://www.grupoary.com.mx'], https_publico=True)
ORIGIN = 'https://www.grupoary.com.mx'
HEADERS = {'HTTP_X_ARY_CLIENT_IP': '198.51.100.10', 'HTTP_X_ARY_PROXY_HTTPS': 'on'}

class PolarisProhibido:
    def __getattr__(self, name):
        raise AssertionError('No debe operar Polaris: ' + name)

class TestRed(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.settings=dict(CFG)
        self.bot={'app':{'modo_prueba':True},'cola':{'avisar_cliente':False}}
        self.cola=ColaOperaciones(self.base,self.bot,PolarisProhibido(),lambda *_:None)
        self.portal=PortalWSGI(self.cola,self.bot,self.settings,self.base,secret=b'R'*48)
        self.app=PortalRedWSGI(self.portal,self.settings)

    def tearDown(self): self.temp.cleanup()

    def req(self, path='/api/facturacion/opciones', peer='10.20.20.154', method='GET', data=None, headers=None, proxy=True, app=None):
        raw=json.dumps(data or {}).encode()
        env={'REMOTE_ADDR':peer,'REQUEST_METHOD':method,'PATH_INFO':path,
             'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),
             'wsgi.input':io.BytesIO(raw),'HTTP_ORIGIN':ORIGIN}
        if proxy: env.update(HEADERS)
        env.update(headers or {})
        result={}
        def start(status,hs):result.update(code=int(status[:3]),headers=dict(hs))
        body=b''.join((app or self.app)(env,start))
        return result['code'],result['headers'],json.loads(body)

    def test_config_dos_equipos(self):
        (self.base/'portal_config.json').write_text(json.dumps(CFG))
        cfg=cargar_ajustes(self.base)
        self.assertEqual(cfg['escuchar_en'],'10.20.20.182')
        self.assertEqual(cfg['proxy_confiable'],'10.20.20.154')
        self.assertFalse(cfg['permitir_facturacion_real'])

    def test_config_excluye_escuchas_amplias(self):
        for bind in ['0.0.0.0','::','*','8.8.8.8','192.168.1.0/24','localhost','10.20.20.182:8765',None,182]:
            with self.subTest(bind=bind), self.assertRaises(ValueError):
                validar_red(dict(CFG,escuchar_en=bind))

    def test_config_exige_iis_exacto(self):
        for proxy in ['',None,'*','10.20.20.0/24','8.8.8.8','127.0.0.1','10.20.20.182',154]:
            with self.subTest(proxy=proxy),self.assertRaises(ValueError):
                validar_red(dict(CFG,proxy_confiable=proxy))
        with self.assertRaises(ValueError):validar_red(dict(CFG,https_publico=False))

    def test_no_permite_otros_equipos_aunque_inventen_headers(self):
        for ip in ['10.20.20.200','198.51.100.10','127.0.0.1','10.20.20.182','','garbage']:
            self.assertEqual(self.req(peer=ip)[0],403)
        self.assertFalse(self.cola.store.list())

    def test_salud_iis_y_propio_bot_sin_cabeceras(self):
        for peer in ['10.20.20.154','10.20.20.182']:
            code,headers,data=self.req('/api/facturacion/salud',peer=peer,proxy=False)
            self.assertEqual(code,200);self.assertTrue(data['ok']);self.assertTrue(data['modo_seguro'])
            self.assertEqual(set(data),{'ok','servicio','version','modo_seguro','recepcion_activa'})
            self.assertNotIn('Set-Cookie',headers)
        self.assertFalse(self.cola.store.list());self.assertIsNone(self.cola.thread)

    def test_salud_rechaza_otros_y_post(self):
        self.assertEqual(self.req('/api/facturacion/salud',peer='10.20.20.200')[0],403)
        self.assertEqual(self.req('/api/facturacion/salud',method='POST')[0],405)

    def test_iis_requiere_marca_https(self):
        self.assertEqual(self.req(proxy=False)[0],403)
        for value in ['', 'off', 'on,on', 'ON']:
            self.assertEqual(self.req(headers={'HTTP_X_ARY_PROXY_HTTPS':value})[0],403)

    def test_ip_cliente_literal_sin_lista_ni_puerto(self):
        for value in ['', '1.2.3.4,5.6.7.8', '1.2.3.4:123', '::', '0.0.0.0', '224.0.0.1','fe80::1%3']:
            self.assertEqual(self.req(headers={'HTTP_X_ARY_CLIENT_IP':value})[0],403)

    def test_ipv6_cliente_valido(self):
        self.assertEqual(self.req(headers={'HTTP_X_ARY_CLIENT_IP':'2001:db8::123'})[0],200)

    def test_par_tcp_mapeado_ipv4(self):
        self.assertEqual(self.req(peer='::ffff:10.20.20.154')[0],200)
        self.assertEqual(self.req(peer='::ffff:10.20.20.200')[0],403)

    def test_forwarded_no_autoriza_par_falso(self):
        headers={'HTTP_X_FORWARDED_FOR':'10.20.20.154','HTTP_FORWARDED':'for=10.20.20.154'}
        self.assertEqual(self.req(peer='10.20.20.200',headers=headers)[0],403)

    def test_limites_separados_por_cliente_no_por_iis(self):
        self.req(headers={'HTTP_X_ARY_CLIENT_IP':'198.51.100.10'})
        self.req(headers={'HTTP_X_ARY_CLIENT_IP':'198.51.100.20'})
        self.assertIn(('general','198.51.100.10'),self.portal.limits.data)
        self.assertIn(('general','198.51.100.20'),self.portal.limits.data)
        self.assertNotIn(('general','10.20.20.154'),self.portal.limits.data)

    def test_archivos_privados_no_disponibles(self):
        for path in ['/','/ary_facturacion.htm','/config.json','/token.json','/data/portal_secret.key']:
            self.assertEqual(self.req(path)[0],404)

    def test_origen_ajeno_denegado_desde_iis(self):
        self.assertEqual(self.req(headers={'HTTP_ORIGIN':'https://otro.example'})[0],403)

    def test_flujo_lan_recibe_sin_procesar_ni_timbrar(self):
        code,hs,data=self.req()
        self.assertEqual(code,200)
        self.assertIn('; Secure',hs['Set-Cookie'])
        h={'HTTP_COOKIE':hs['Set-Cookie'].split(';',1)[0], 'HTTP_X_ARY_CSRF':data['csrf'],
           'HTTP_X_ARY_REQUEST_ID':'b'*32,'HTTP_X_ARY_MODE':'seguro'}
        body={'tipo':'FACTURA','estacion':'ARY I','rfc':'ABC010101AB1','ticket':'000123',
              'forma_pago':'TARJETA DE CREDITO','uso_cfdi':'G03','correo':'test@example.com',
              'correo_confirmacion':'test@example.com','confirmado':True,'sitio_web':''}
        code,_,received=self.req('/api/facturacion/solicitudes',method='POST',data=body,headers=h)
        self.assertEqual(code,201);self.assertTrue(received['modo_seguro'])
        jobs=self.cola.store.list();self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['estado'],'EN_COLA')
        self.assertEqual(jobs[0]['datos']['forma_pago'],'TARJETA DE CREDITO')
        self.assertIsNone(self.cola.thread)

    def test_salud_indica_pausa_sin_falsear_tcp(self):
        self.portal.pausar_recepcion()
        code,_,body=self.req('/api/facturacion/salud',proxy=False)
        self.assertEqual(code,200);self.assertTrue(body['ok']);self.assertFalse(body['recepcion_activa'])

    def test_compatibilidad_modo_local(self):
        settings=dict(DEFAULTS)
        local=PortalRedWSGI(PortalWSGI(self.cola,self.bot,settings,self.base,secret=b'R'*48),settings)
        self.assertEqual(self.req('/api/facturacion/salud',peer='127.0.0.1',app=local,proxy=False)[0],200)
        self.assertEqual(self.req('/api/facturacion/salud',peer='10.20.20.154',app=local)[0],403)

if __name__=='__main__':unittest.main()
