"""Regresión fiscal y WhatsApp. Sin SAT, credenciales, Gmail real ni timbrado.

La política del 14/10 reemplaza las tres expectativas antiguas de calle
obligatoria, distinta y VCL. Se conservan PDF, ambigüedad, cola y portapapeles.
"""
from __future__ import annotations
from base64 import b64encode
from io import BytesIO
from pathlib import Path
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from arybot.constancia_fiscal import ConstanciaError, leer_constancia_pdf_base64, _validar_identidad
from arybot.coincidencia_fiscal import CoincidenciaFiscalError, verificar_unico
from arybot.portal_modelo import validar_entrada, EntradaPortalError, estado_publico


def documento_pdf(*, nombre='SERVICIOS ESPECIALIZADOS PENOLES SA DE CV',
                  calle='LEGARIA', rfc='SEP1312171X9', idcif='00123456789', incluir_idcif=True):
    from reportlab.pdfgen import canvas
    destino=BytesIO(); documento=canvas.Canvas(destino)
    campos=['Constancia de Situacion Fiscal','Datos de Identificacion del Contribuyente',
            'RFC: '+rfc,'Denominacion/Razon Social: '+nombre,'Datos del domicilio registrado']
    if calle is not None:
        campos += ['Tipo de Vialidad: CALZADA','Nombre de Vialidad: '+calle]
    campos.append('Codigo Postal: 27000')
    if incluir_idcif: campos.append('idCIF: '+idcif)
    for idx,line in enumerate(campos): documento.drawString(38,740-idx*24,line)
    documento.save()
    return destino.getvalue()


def cuerpo(*, tipo='FACTURA', pdf=None):
    values={'tipo':tipo,'estacion':'ARY V','constancia_pdf':b64encode(pdf or documento_pdf()).decode('ascii'),
            'correo':'cliente@example.com','correo_confirmacion':'cliente@example.com','sitio_web':'','confirmado':True}
    if tipo=='FACTURA':
        values.update(ticket='123',fecha_ticket=date.today().isoformat(),forma_pago='EFECTIVO',uso_cfdi='G03')
    return values


def test_identidad_extraida_del_pdf_en_memoria_sin_guardar_archivo(tmp_path):
    data=leer_constancia_pdf_base64(b64encode(documento_pdf()).decode('ascii'),alta=True)
    assert (data.rfc,data.razon_social,data.calle,data.idcif)==(
        'SEP1312171X9','SERVICIOS ESPECIALIZADOS PENOLES SA DE CV','CALZADA LEGARIA','00123456789')
    assert list(tmp_path.iterdir())==[]


def test_factura_toma_solo_identidad_del_pdf_y_no_guarda_base64():
    kind,sol,datos=validar_entrada(cuerpo())
    assert kind=='FACTURA' and sol.rfc=='SEP1312171X9'
    assert sol.razon_social=='SERVICIOS ESPECIALIZADOS PENOLES SA DE CV'
    assert sol.calle_constancia=='CALZADA LEGARIA'
    assert 'constancia_pdf' not in datos and 'constancia_archivo' not in datos and 'JVBER' not in str(datos)


def test_factura_sin_calle_desde_pdf_hasta_modelo():
    kind,sol,datos=validar_entrada(cuerpo(pdf=documento_pdf(calle=None)))
    assert kind=='FACTURA' and sol.calle_constancia==''
    assert datos['rfc']==sol.rfc and datos['razon_social']==sol.razon_social
    fila={'numero':'70','rfc':sol.rfc,'razon_social':sol.razon_social}
    assert verificar_unico([fila],rfc=sol.rfc,razon_social=sol.razon_social) is fila


def test_alta_toma_rfc_e_idcif_del_pdf_y_telefono_es_opcional():
    kind,sol,datos=validar_entrada(cuerpo(tipo='ALTA'))
    assert kind=='ALTA' and sol.idcif=='00123456789' and sol.telefono==''
    assert datos['razon_social']=='SERVICIOS ESPECIALIZADOS PENOLES SA DE CV'
    assert datos['calle_constancia']=='CALZADA LEGARIA' and 'constancia_pdf' not in datos


def test_alta_sigue_exigiendo_domicilio():
    with pytest.raises(EntradaPortalError,match='calle'):
        validar_entrada(cuerpo(tipo='ALTA',pdf=documento_pdf(calle=None)))


@pytest.mark.parametrize('value',['',None,'invalid',b64encode(b'%PDF- fake').decode('ascii')])
def test_constancia_invalida_se_rechaza(value):
    with pytest.raises(ConstanciaError): leer_constancia_pdf_base64(value)


def test_pdf_sin_texto_no_se_toma_como_constancia():
    from reportlab.pdfgen import canvas
    dest=BytesIO(); c=canvas.Canvas(dest); c.rect(10,10,100,100); c.save()
    with pytest.raises(ConstanciaError): leer_constancia_pdf_base64(b64encode(dest.getvalue()).decode('ascii'))


def test_sin_idcif_no_hace_alta_automatica():
    with pytest.raises(EntradaPortalError,match='idCIF'):
        validar_entrada(cuerpo(tipo='ALTA',pdf=documento_pdf(incluir_idcif=False)))


@pytest.mark.parametrize('campo',['rfc','razon_social','calle'])
def test_no_permite_inyectar_rfc_o_razon_social_manual(campo):
    form=cuerpo(); form[campo]='AAA010101AAA'
    with pytest.raises(EntradaPortalError,match='no admitidos'): validar_entrada(form)


def test_camino_web_exige_constancia_pdf():
    form=cuerpo(); form.pop('constancia_pdf')
    with pytest.raises(EntradaPortalError,match='constancia'): validar_entrada(form)


def test_rfc_duplicado_con_nombres_diferentes_no_se_elige():
    registros=[{'numero':'1071','razon_social':'SERVICIOS ESPECIALIZADOS PENOLES','rfc':'SEP1312171X9','calle':'LEGARIA'},
               {'numero':'9281','razon_social':'SERVICIOS ESPECIALIZADOS PEÑOLES S A DE C V','rfc':'SEP1312171X9','calle':'OTRA'}]
    with pytest.raises(CoincidenciaFiscalError,match='UNA coincidencia'):
        verificar_unico(registros,rfc='SEP1312171X9',razon_social='SERVICIOS ESPECIALIZADOS PEÑOLES SA DE CV',calle='CALZADA LEGARIA')


def test_rfc_solo_se_acepta_si_registro_es_unico():
    fila={'numero':'70','razon_social':'EMPRESA SA DE CV','rfc':'SEP1312171X9'}
    assert verificar_unico([fila],rfc=fila['rfc'],razon_social='') is fila
    assert verificar_unico([fila],rfc=fila['rfc'],razon_social='NOMBRE DISTINTO',calle='') is fila


@pytest.mark.parametrize('calle',['CALZADA LEGARIA','OTRO DOMICILIO','',None])
def test_dos_coincidencias_exactas_detenidas_aunque_calle_difiera(calle):
    fila={'numero':'9281','razon_social':'EMPRESA SA DE CV','rfc':'SEP1312171X9','calle':'CALZADA LEGARIA'}
    with pytest.raises(CoincidenciaFiscalError,match='UNA coincidencia'):
        verificar_unico([fila,dict(fila,numero='9282',calle=calle)],rfc=fila['rfc'],razon_social=fila['razon_social'],calle=fila['calle'])


def test_unico_rfc_ignora_nombre_sufijo_y_calle():
    fila={'numero':'9281','razon_social':'EMPRESA SA DE CV','rfc':'SEP1312171X9','calle':'CALZADA LEGARIA'}
    assert verificar_unico([fila],rfc=fila['rfc'],razon_social='EMPRESA',calle='OTRA CALLE') is fila


def test_rfc_no_confunde_enie_con_ene():
    fila={'numero':'1','razon_social':'EMPRESA SA DE CV','rfc':'ÑAA010101AAA'}
    with pytest.raises(CoincidenciaFiscalError): verificar_unico([fila],rfc='NAA010101AAA',razon_social=fila['razon_social'])
    assert verificar_unico([fila],rfc=fila['rfc'],razon_social=fila['razon_social']) is fila


def test_formularios_solo_tienen_pdf_obligatorio():
    for folder in ('portal_web','para_el_sitio'):
        html=(Path(__file__).parents[1]/folder/'ary_facturacion.htm').read_text(encoding='utf-8')
        for kind in ('factura','alta'): assert 'id="'+kind+'-constancia"' in html
        for name in ('factura-rfc','alta-rfc','alta-idcif'): assert 'id="'+name+'"' not in html
        assert 'values.constancia_pdf=' in html


def test_no_se_confirma_cliente_si_la_uia_no_expone_total_de_filas(monkeypatch):
    import sys
    from arybot.lectura_catalogo_fiscal import seleccionar_cliente_exacto
    fake=SimpleNamespace(Desktop=lambda backend:SimpleNamespace(window=lambda handle:SimpleNamespace(descendants=lambda:[])))
    monkeypatch.setitem(sys.modules,'pywinauto',fake)
    with pytest.raises(CoincidenciaFiscalError,match='total verificable'):
        seleccionar_cliente_exacto(77,rfc='SEP1312171X9',razon_social='EMPRESA SA DE CV')


def solicitud(n='1'):
    from arybot.parser import Solicitud
    return Solicitud(estacion='ARY V',rfc='SEP1312171X9',razon_social='SERVICIOS ESPECIALIZADOS PENOLES SA DE CV',
                     ticket=n,fecha_ticket=date.today().isoformat(),forma_pago='EFECTIVO',
                     correo_destino='cliente@example.com',remitente='cliente@example.com')


def test_verificacion_fiscal_no_concluyente_continua_siguientes_facturas(tmp_path):
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS,texto_cliente
    from arybot.coincidencia_fiscal import CODIGO_VERIFICACION_FISCAL,MENSAJE_VERIFICACION_FISCAL
    config={'app':{'modo_prueba':False,'correo':'test@example.com'},'gmail':{},'ocr':{},'polaris':{},'cola':dict(DEFAULTS)}
    bot=Mock(); bot.diag=None
    def ejecutar(sol,**kw):
        if sol.ticket=='2':
            bot.etapa_alerta='FACTURACION: buscar cliente por RFC'
            raise CoincidenciaFiscalError('Ninguna coincidencia única.')
        return 'ENVIO_SOLICITADO'
    bot.invoice.side_effect=ejecutar
    q=ColaOperaciones(tmp_path,config,bot,Mock(),avisos=Mock())
    jobs=[q.encolar_factura(solicitud(str(n)),safe=False)[0] for n in range(1,5)]
    q.iniciar_cola()
    for job in jobs: assert q.ejecutar_una()
    records=[q.store.get(job['id']) for job in jobs]
    assert [r['estado'] for r in records]==['ENVIO_SOLICITADO','REVISION_REQUERIDA','ENVIO_SOLICITADO','ENVIO_SOLICITADO']
    assert records[1]['resultado']['codigo_cliente']==CODIGO_VERIFICACION_FISCAL
    assert estado_publico(records[1])['mensaje']==MENSAJE_VERIFICACION_FISCAL
    assert any(c.args[1]=='verificacion_fiscal' for c in q.avisos.publicar.call_args_list)
    _,text=texto_cliente(records[1],'verificacion_fiscal',DEFAULTS)
    assert MENSAJE_VERIFICACION_FISCAL in text
    assert not q.store.status()['bloqueo'] and not q.store.status()['pausada']
    assert bot.cerrar_polaris_tras_solicitud.call_count==4


def test_verificacion_fiscal_no_reclasifica_error_posterior_al_timbrado(tmp_path):
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    config={'app':{'modo_prueba':False},'gmail':{},'ocr':{},'polaris':{},'cola':dict(DEFAULTS)}
    bot=Mock(); bot.diag=None
    bot.invoice.side_effect=CoincidenciaFiscalError('No pudo verificar la coincidencia.')
    bot.etapa_alerta='TIMBRADO: Aceptar factura'
    q=ColaOperaciones(tmp_path,config,bot,Mock(),avisos=Mock())
    job=q.encolar_factura(solicitud(),safe=False)[0]; q.iniciar_cola()
    assert q.ejecutar_una()
    saved=q.store.get(job['id'])
    assert saved['estado']=='REVISION_REQUERIDA' and q.store.status()['bloqueo']==job['id']
    assert not estado_publico(saved)['codigo_cliente']
    bot.cerrar_polaris_tras_solicitud.assert_not_called()


def texto_constancia(linea):
    return ('Constancia de Situacion Fiscal\nDatos de Identificacion del Contribuyente\n'+linea+
            '\nDenominacion/Razon Social: EMPRESA DE PRUEBA SA DE CV\nTipo de Vialidad: CALZADA\nNombre de Vialidad: LEGARIA\n')


@pytest.mark.parametrize('linea_rfc',['RFC: SEP1312171X9','RFC : SEP1312171X9','R.F.C.: SEP1312171X9',
    'Registro Federal de Contribuyentes:\nSEP1312171X9','RFC:\nSEP1312171X9','RFC: SEP 131217 1X9',
    'RFC: SEP-131217-1X9','SEP1312171X9\nRFC:'])
def test_constancia_pdf_sat_rfc_variantes_de_extraccion(linea_rfc):
    fiscal=_validar_identidad(texto_constancia(linea_rfc))
    assert fiscal.rfc=='SEP1312171X9' and fiscal.razon_social=='EMPRESA DE PRUEBA SA DE CV' and fiscal.calle=='CALZADA LEGARIA'


def test_constancia_pdf_sat_rfc_fraccionado_en_pdf_end_to_end():
    assert leer_constancia_pdf_base64(b64encode(documento_pdf(rfc='SEP 131217 1X9')).decode('ascii')).rfc=='SEP1312171X9'


def test_constancia_pdf_con_dos_rfc_distintos_se_rechaza():
    with pytest.raises(ConstanciaError,match='más de un RFC'):
        _validar_identidad(texto_constancia('RFC: SEP1312171X9\nRFC: ABC010101ABC'))


def test_constancia_pdf_rfc_repetido_igual_es_valido():
    assert _validar_identidad(texto_constancia('RFC: SEP1312171X9\nRegistro Federal de Contribuyentes: SEP1312171X9')).rfc=='SEP1312171X9'


def test_constancia_pdf_sin_rfc_no_inventa_y_bloquea():
    with pytest.raises(ConstanciaError,match='No se pudo reconocer el RFC'):
        _validar_identidad(texto_constancia('CURP: SEP1312171X9ABC1234'))


def test_constancia_preserva_enie_en_rfc():
    assert _validar_identidad(texto_constancia('RFC: ÑAA010101AAA')).rfc=='ÑAA010101AAA'


def simular_catalogo_vcl_clipboard(monkeypatch,filas,*,copy_enabled=True):
    import sys
    from arybot import polaris
    class WinClipboard:
        CF_UNICODETEXT=13
        texto='Texto anterior de otro programa'
        def OpenClipboard(self): pass
        def CloseClipboard(self): pass
        def EmptyClipboard(self): self.texto=None
        def IsClipboardFormatAvailable(self,formato): return self.texto is not None
        def GetClipboardData(self,formato): return self.texto
        def SetClipboardText(self,texto,formato): self.texto=texto
    class Teclado:
        def __init__(self): self.indice=0; self.acciones=[]
        def hotkey(self,*keys):
            self.acciones.append(('hotkey',keys))
            if keys==('ctrl','home'): self.indice=0
            if keys==('ctrl','end'): self.indice=len(filas)-1
            if keys==('ctrl','c') and copy_enabled:
                self.portapapeles.texto='\t'.join(filas[self.indice].get(k,'') for k in ('numero','razon_social','rfc','calle'))
        def press(self,key):
            self.acciones.append(('press',key))
            if key=='down': self.indice=min(len(filas)-1,self.indice+1)
    class Bot:
        BUSCAR={'primera_fila':(.45,.232)}
        def __init__(self,k): self.keyboard=k; self.clicks=[]
        def _click_rel(self,dialogo,posicion):
            assert dialogo==733
            self.keyboard.indice=0; self.clicks.append(posicion)
    teclado=Teclado(); clip=WinClipboard(); teclado.portapapeles=clip; bot=Bot(teclado)
    monkeypatch.setattr(polaris,'pyautogui',teclado,raising=False)
    monkeypatch.setattr(polaris,'win32clipboard',clip,raising=False)
    fake=SimpleNamespace(Desktop=lambda backend:SimpleNamespace(window=lambda handle:SimpleNamespace(descendants=lambda:[])))
    monkeypatch.setitem(sys.modules,'pywinauto',fake)
    monkeypatch.setattr('arybot.lectura_catalogo_fiscal.time.sleep',lambda _:None)
    return bot,teclado,clip


def test_polaris_vcl_sin_gridpattern_selecciona_cliente_exacto_con_teclado(monkeypatch):
    from arybot.lectura_catalogo_fiscal import seleccionar_cliente_exacto
    filas=[{'numero':'1071','razon_social':'SERVICIOS ESPECIALIZADOS PENOLES','rfc':'AAA010101AAA','calle':'LEGARIA'},
           {'numero':'9281','razon_social':'SERVICIOS ESPECIALIZADOS PEÑOLES SA DE CV','rfc':'SEP1312171X9'},
           {'numero':'10099','razon_social':'EMPRESA AJENA SA DE CV','rfc':'BBB010101BBB','calle':'OTRO DOMICILIO'}]
    bot,teclado,clip=simular_catalogo_vcl_clipboard(monkeypatch,filas)
    seleccion=seleccionar_cliente_exacto(733,rfc='SEP1312171X9',razon_social='SERVICIOS ESPECIALIZADOS PENOLES SA DE CV',bot=bot)
    assert seleccion['numero']=='9281' and teclado.indice==1 and len(bot.clicks)==2
    assert ('hotkey',('ctrl','end')) in teclado.acciones
    assert clip.texto=='Texto anterior de otro programa'


@pytest.mark.parametrize('calle',[None,'','CALZADA LEGARIA','MANUEL GOMEZ MORIN'])
def test_polaris_vcl_selecciona_unico_cliente_sin_exigir_calle(monkeypatch,calle):
    from arybot.lectura_catalogo_fiscal import seleccionar_cliente_exacto
    fila={'numero':'70','razon_social':'EMPRESA DE PRUEBA SA DE CV','rfc':'SEP1312171X9'}
    bot,teclado,clip=simular_catalogo_vcl_clipboard(monkeypatch,[fila])
    elegido=seleccionar_cliente_exacto(733,rfc=fila['rfc'],razon_social=fila['razon_social'],calle=calle,bot=bot)
    assert elegido['numero']=='70' and teclado.indice==0 and len(bot.clicks)==2
    assert clip.texto=='Texto anterior de otro programa'


def test_polaris_vcl_dos_clientes_con_misma_identidad_no_se_eligen(monkeypatch):
    from arybot.lectura_catalogo_fiscal import seleccionar_cliente_exacto
    fila={'numero':'70','razon_social':'EMPRESA DE PRUEBA SA DE CV','rfc':'SEP1312171X9','calle':'CALZADA LEGARIA'}
    bot,teclado,clip=simular_catalogo_vcl_clipboard(monkeypatch,[fila,dict(fila,numero='71',calle='OTRA')])
    with pytest.raises(CoincidenciaFiscalError,match='UNA coincidencia'):
        seleccionar_cliente_exacto(733,rfc=fila['rfc'],razon_social=fila['razon_social'],calle=fila['calle'],bot=bot)
    assert len(bot.clicks)==1 and clip.texto=='Texto anterior de otro programa'


def test_polaris_vcl_no_copia_filas_no_elige_primera(monkeypatch):
    from arybot.lectura_catalogo_fiscal import seleccionar_cliente_exacto
    fila={'numero':'70','razon_social':'EMPRESA DE PRUEBA SA DE CV','rfc':'SEP1312171X9'}
    bot,teclado,clip=simular_catalogo_vcl_clipboard(monkeypatch,[fila],copy_enabled=False)
    with pytest.raises(CoincidenciaFiscalError,match='leer una fila'):
        seleccionar_cliente_exacto(733,rfc=fila['rfc'],razon_social=fila['razon_social'],bot=bot)
    assert len(bot.clicks)==1 and clip.texto=='Texto anterior de otro programa'


@pytest.mark.parametrize('texto',['1\tEMPRESA SA DE CV\tSEP1312171X9','1\tEMPRESA SA DE CV\tSEP1312171X9\t'])
def test_clipboard_admite_tres_columnas(texto):
    from arybot.lectura_catalogo_fiscal import _fila_del_portapapeles
    assert _fila_del_portapapeles(texto)['numero']=='1'


@pytest.mark.parametrize('texto',['SEP1312171X9','1\tSEP1312171X9',
    '1\tEMPRESA SA DE CV\tSEP1312171X9\ntexto ilegible',
    '1\tEMPRESA SA DE CV\tSEP1312171X9\n2\tEMPRESA SA DE CV\tSEP1312171X9'])
def test_clipboard_rechaza_lecturas_incompletas_o_multiples(texto):
    from arybot.lectura_catalogo_fiscal import _fila_del_portapapeles
    assert _fila_del_portapapeles(texto) is None


def test_selector_integrado_ignora_calle_y_no_timbra(monkeypatch):
    from arybot import polaris,seleccion_cliente
    monkeypatch.setattr(polaris,'pyautogui',Mock(),raising=False)
    monkeypatch.setattr(seleccion_cliente.time,'sleep',lambda _:None)
    monkeypatch.setattr('arybot.cliente_facturacion.exigir_cliente_facturable',Mock())
    selector=Mock(); monkeypatch.setattr('arybot.lectura_catalogo_fiscal.seleccionar_cliente_exacto',selector)
    bot=Mock(); bot.FACTURA=polaris.PolarisBot.FACTURA; bot.BUSCAR=polaris.PolarisBot.BUSCAR
    bot._pasos_factura={}; bot._wait_text_dialog.return_value=733; bot._same_polaris_process.return_value=True
    bot._rect.return_value=SimpleNamespace(left=0,top=0,width=800,height=600); bot._invoice_window.return_value=55
    assert seleccion_cliente.seleccionar_cliente(bot,55,'SEP1312171X9','EMPRESA SA DE CV','')==55
    selector.assert_called_once_with(733,rfc='SEP1312171X9',razon_social='EMPRESA SA DE CV',bot=bot)
    assert bot._pasos_factura['cliente'] is True
    bot.invoice.assert_not_called()


def test_instalacion_selector_idempotente(monkeypatch):
    from arybot import polaris,seleccion_cliente
    monkeypatch.setattr(polaris.PolarisBot,'_select_client',polaris.PolarisBot._select_client)
    seleccion_cliente.instalar_seleccion_cliente(); seleccion_cliente.instalar_seleccion_cliente()
    assert polaris.PolarisBot._select_client is seleccion_cliente.seleccionar_cliente
    source=(Path(__file__).parents[1]/'arybot'/'inicio.py').read_text(encoding='utf-8')
    assert source.index('instalar_seleccion_cliente()') < source.index('result = abrir_bot()')


def job_portal(estado='REVISION_REQUERIDA'):
    return {'id':'a'*32,'tipo':'FACTURA','origen':'Portal web','estado':estado,'seguro':False,
            'datos':{'rfc':'SEP1312171X9','razon_social':'EMPRESA SA DE CV','estacion':'ARY V','ticket':'123',
                     'fecha_ticket':'2026-10-14','forma_pago':'TARJETA DE DEBITO','uso_cfdi':'GASTOS EN GENERAL.',
                     'correo_destino':'cliente@example.com','constancia_pdf':'PDF-PRIVADO','idcif':'IDCIF-PRIVADO',
                     'calle_constancia':'DOMICILIO-PRIVADO','token':'TOKEN-PRIVADO'},
            'resultado':{'error':'ERROR-INTERNO-PRIVADO'}}


@pytest.mark.parametrize('state',['REVISION_REQUERIDA','REVISION_CERRADA','ESPERANDO_DATOS','CANCELADA','YA_FACTURADO'])
def test_asistencia_whatsapp_conserva_captura_sin_datos_privados_extra(state):
    from arybot.asistencia_whatsapp import CAMPOS
    public=estado_publico(job_portal(state)); ayuda=public['asistencia']
    assert ayuda['telefono']=='528711651404' and set(ayuda['datos'])==set(CAMPOS)
    assert ayuda['datos']['rfc']=='SEP1312171X9' and ayuda['datos']['forma_pago']=='TARJETA DE DEBITO'
    assert ayuda['datos']['fecha_ticket']=='2026-10-14' and ayuda['referencia']==public['referencia']
    assert 'PRIVADO' not in str(public) and 'duplicarlo' in ayuda['nota']


@pytest.mark.parametrize('state',['EN_COLA','EJECUTANDO','ENVIO_SOLICITADO','PREPARADA_FACTURA'])
def test_asistencia_no_expone_captura_en_estados_normales(state):
    assert 'asistencia' not in estado_publico(job_portal(state))


def test_perfil_local_facturacion_solo_incluye_identidad_minima():
    from arybot.asistencia_whatsapp import perfil_local_facturacion
    perfil=perfil_local_facturacion(job_portal('EN_COLA'))
    assert perfil=={'rfc':'SEP1312171X9','razon_social':'EMPRESA SA DE CV',
                    'correo_destino':'cliente@example.com'}
    assert 'PRIVADO' not in str(perfil)
    assert perfil_local_facturacion(dict(job_portal(),origen='Gmail')) is None
    assert perfil_local_facturacion(dict(job_portal(),tipo='ALTA')) is None


def test_estado_publico_no_expone_perfil_local():
    public=estado_publico(job_portal('EN_COLA'))
    assert 'perfil_local' not in public
    assert 'asistencia' not in public


def test_asistencia_no_se_expone_para_correo_o_alta():
    from arybot.asistencia_whatsapp import datos_asistencia
    assert datos_asistencia(dict(job_portal(),origen='Gmail')) is None
    assert datos_asistencia(dict(job_portal(),tipo='ALTA')) is None


def test_factura_solo_rfc_pdf_sin_nombre_acepta_alta_no(tmp_path):
    doc = documento_pdf(nombre='')
    kind, solicitud_web, datos = validar_entrada(cuerpo(pdf=doc))
    assert kind == 'FACTURA' and solicitud_web.rfc == 'SEP1312171X9'
    assert solicitud_web.razon_social == '' and datos['rfc'] == solicitud_web.rfc
    with pytest.raises(EntradaPortalError, match='nombre o razón social'):
        validar_entrada(cuerpo(tipo='ALTA', pdf=doc))


def test_portal_rechaza_fecha_mas_antigua_que_tres_dias():
    from datetime import timedelta
    formulario = cuerpo()
    formulario['fecha_ticket'] = (date.today() - timedelta(days=4)).isoformat()
    with pytest.raises(EntradaPortalError, match='72 horas'):
        validar_entrada(formulario)
    formulario['fecha_ticket'] = (date.today() - timedelta(days=3)).isoformat()
    assert validar_entrada(formulario)[0] == 'FACTURA'
