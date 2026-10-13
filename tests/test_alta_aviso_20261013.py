"""Regresiones sin Polaris real, Gmail, SAT ni timbrado."""
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from threading import Lock
from unittest.mock import Mock
import pytest

from arybot import polaris
from arybot.alta_clientes import AltaClientes, DiarioAltas
from arybot.cliente_model import AltaCliente, AltaClienteError
from arybot.cliente_facturacion import (
    ClienteSinRegimenError, CODIGO_SIN_REGIMEN, MENSAJE_SIN_REGIMEN,
    es_aviso_sin_regimen, exigir_cliente_facturable,
)
from arybot.cola_modelo import DEFAULTS, texto_cliente, leer_alta
from arybot.cola_operaciones import ColaOperaciones
from arybot.parser import Solicitud
from arybot.portal_modelo import validar_entrada, estado_publico, EntradaPortalError

AVISO = ('El cliente no tiene definido el régimen fiscal por lo que no se puede '
         'facturar vaya al módulo de clientes de efectivo para asignarle un régimen.')


def solicitud(telefono='', rfc='AAA010101AAA'):
    return AltaCliente.crear('ARY V', rfc, '00123456789', telefono, 'cliente@example.com')


@pytest.mark.parametrize('telefono', ['', '   '])
def test_telefono_vacio_es_opcional(telefono):
    assert solicitud(telefono).telefono == ''


@pytest.mark.parametrize('telefono', ['abc', '123', '--- ---', '+', '1' * 25, '871\n1111111'])
def test_telefono_proporcionado_invalido_se_rechaza(telefono):
    with pytest.raises(AltaClienteError):
        solicitud(telefono)


def test_telefono_valido_conserva_digitos_y_ceros():
    assert solicitud('001 234-5678').telefono == '0012345678'


def entrada():
    return dict(tipo='ALTA', estacion='ARY V', rfc='AAA010101AAA', idcif='00123456789',
                correo='cliente@example.com', correo_confirmacion='cliente@example.com',
                confirmado=True, sitio_web='')


@pytest.mark.parametrize('omitido', [True, False])
def test_portal_acepta_telefono_omitido_o_vacio(omitido):
    body=entrada()
    if not omitido:
        body['telefono']=''
    kind, sol, data=validar_entrada(body)
    assert kind=='ALTA' and sol.telefono=='' and data['telefono']==''


@pytest.mark.parametrize('valor', [None, 1234567890, [], '--- ---'])
def test_portal_no_convierte_telefono_invalido_en_omision(valor):
    with pytest.raises(EntradaPortalError):
        validar_entrada(dict(entrada(), telefono=valor))


def test_correo_de_alta_sin_telefono():
    sol=leer_alta('ALTA DE CLIENTE',
                  'Estación: ARY V\nRFC: AAA010101AAA\nidCIF: 00123456789\nCorreo: cliente@example.com',
                  'cliente@example.com')
    assert sol.validar().telefono==''


class UIAlta:
    def __init__(self, telefono_residual='', guardar_error=False):
        self.valores=dict(rfc='', idcif='', nombre='', cp='', telefono='', correo='', numero='')
        self.telefono_residual=telefono_residual
        self.guardar_error=guardar_error
        self.escrituras=[]
        self.aceptados=0
        self.nuevos=0
    def proteger_edicion_abierta(self, main): pass
    def abrir(self, main): return 202
    def snapshot(self, main, form): return dict(self.valores)
    def nuevo(self, main, form):
        self.nuevos+=1
        self.valores={k:'' for k in self.valores}
        self.valores['telefono']=self.telefono_residual
    def exigir_nuevo(self, main, form): pass
    def escribir(self, main, form, campo, valor):
        self.escrituras.append((campo, valor))
        self.valores[campo]=valor
    def consultar_idcif(self, main, form):
        self.valores.update(nombre='CLIENTE DE PRUEBA', cp='35000')
    def esperar_fiscales(self, main, form, sol): pass
    def verificar_contexto(self, main, form, estacion, pid): pass
    def validar_aceptar(self, main, form): pass
    def aceptar(self, main, form): self.aceptados+=1
    def verificar_guardado(self, main, form, sol):
        if self.guardar_error:
            raise AltaClienteError('Guardado sin número confirmado')
        return '12345'


def servicio(tmp_path, *, seguro=False, ui=None):
    bot=SimpleNamespace(base=tmp_path, cfg={'app':{'modo_prueba':seguro}}, _lock=Lock(),
                        log=Mock(), ensure_ready=lambda:101, _pid=lambda h:8765,
                        _change_station=lambda main, station:main)
    return AltaClientes(bot, ui=ui or UIAlta())


def test_registro_real_sin_telefono_pulsa_aceptar_una_vez(tmp_path):
    service=servicio(tmp_path)
    result=service.registrar(solicitud(), autorizado=True)
    assert result['numero_cliente']=='12345'
    assert result['nombre']=='CLIENTE DE PRUEBA'
    assert service.ui.aceptados==1 and service.pendiente is None
    assert all(campo!='telefono' for campo, _ in service.ui.escrituras)
    assert service.diario.leer()[solicitud().clave_local()]['estado']=='ALTA_CONFIRMADA_EN_PANTALLA'
    with pytest.raises(AltaClienteError):
        service.registrar(solicitud(), autorizado=True)
    assert service.ui.aceptados==1


def test_registro_real_con_telefono_lo_captura(tmp_path):
    service=servicio(tmp_path)
    service.registrar(solicitud('8711234567'), autorizado=True)
    assert ('telefono','8711234567') in service.ui.escrituras
    assert service.ui.aceptados==1


@pytest.mark.parametrize('autorizado', [False, None, 1, 'si'])
def test_registro_no_guarda_sin_autorizacion_booleana(tmp_path, autorizado):
    service=servicio(tmp_path)
    with pytest.raises(AltaClienteError):
        service.registrar(solicitud(), autorizado=autorizado)
    assert service.ui.aceptados==0 and service.ui.nuevos==0


def test_modo_seguro_no_guarda_ni_prepara_por_registrar(tmp_path):
    service=servicio(tmp_path, seguro=True)
    with pytest.raises(AltaClienteError, match='MODO SEGURO'):
        service.registrar(solicitud(), autorizado=True)
    assert service.ui.aceptados==0 and service.ui.nuevos==0


def test_preparar_sigue_sin_guardar_y_aceptar_manual_exige_revision(tmp_path):
    service=servicio(tmp_path)
    result=service.preparar(solicitud())
    assert result['estado']=='PREPARADA_SIN_GUARDAR' and service.ui.aceptados==0
    with pytest.raises(AltaClienteError):
        service.aceptar(solicitud(), autorizado=True)
    assert service.ui.aceptados==0
    service.aceptar(solicitud(), autorizado=True, inexistencia_revisada=True)
    assert service.ui.aceptados==1


def test_cambio_a_modo_seguro_antes_de_aceptar_bloquea(tmp_path):
    service=servicio(tmp_path)
    service.preparar(solicitud())
    service.bot.cfg['app']['modo_prueba']=True
    with pytest.raises(AltaClienteError, match='MODO SEGURO'):
        service.aceptar(solicitud(), autorizado=True, registro_automatico=True)
    assert service.ui.aceptados==0


def test_telefono_omitido_no_hereda_contacto_ajeno(tmp_path):
    service=servicio(tmp_path, ui=UIAlta(telefono_residual='8711234567'))
    with pytest.raises(AltaClienteError, match='teléfono/correo'):
        service.registrar(solicitud(), autorizado=True)
    assert service.ui.aceptados==0


def test_datos_fiscales_incompletos_no_se_aceptan(tmp_path):
    ui=UIAlta()
    ui.consultar_idcif=lambda main, form:None
    service=servicio(tmp_path, ui=ui)
    with pytest.raises(AltaClienteError, match='nombre y código postal'):
        service.registrar(solicitud(), autorizado=True)
    assert ui.aceptados==0


def test_guardado_incierto_no_reintenta_ni_tras_reiniciar(tmp_path):
    service=servicio(tmp_path, ui=UIAlta(guardar_error=True))
    with pytest.raises(AltaClienteError):
        service.registrar(solicitud(), autorizado=True)
    assert service.ui.aceptados==1 and service.pendiente is None
    assert service.bot.etapa_alerta.startswith('ALTA: Aceptar solicitado')
    restart=servicio(tmp_path)
    with pytest.raises(AltaClienteError):
        restart.registrar(solicitud(), autorizado=True)
    assert restart.ui.aceptados==0


def cola(tmp_path, *, seguro=False, alta=None, bot=None):
    cfg={'app':{'modo_prueba':seguro, 'correo':'bot@example.com'},
         'gmail':{}, 'ocr':{}, 'polaris':{}, 'cola':dict(DEFAULTS)}
    bot=bot or Mock()
    bot.diag=None
    bot.etapa_alerta='FACTURACION: buscar cliente por RFC'
    q=ColaOperaciones(tmp_path,cfg,bot,Mock(),alta=alta,avisos=Mock())
    return q, bot


def factura(ticket):
    return Solicitud(estacion='ARY V',rfc='AAA010101AAA',ticket=str(ticket),
                     fecha_ticket=date.today().strftime('%d/%m/%Y'),forma_pago='EFECTIVO',
                     correo_destino='cliente@example.com',remitente='cliente@example.com')


def test_cuatro_altas_reales_terminan_sin_segundo_clic_manual(tmp_path):
    alta=Mock()
    alta.registrar.side_effect=[{'numero_cliente':str(i)} for i in range(1,5)]
    q,bot=cola(tmp_path,alta=alta)
    jobs=[q.encolar_alta(solicitud(rfc='AAA010101A0'+str(i)),safe=False)[0] for i in range(4)]
    q.iniciar_cola()
    for job in jobs:
        assert q.ejecutar_una()
        assert q.store.get(job['id'])['estado']=='ALTA_CONFIRMADA'
    assert alta.registrar.call_count==4
    assert all(c.kwargs=={'autorizado':True} for c in alta.registrar.call_args_list)
    assert alta.preparar.call_count==0 and alta.aceptar.call_count==0
    assert bot.cerrar_polaris_tras_solicitud.call_count==4
    assert not q.store.status()['bloqueo'] and not q.ejecutar_una()


def test_cola_segura_prepara_sin_aceptar(tmp_path):
    alta=Mock()
    alta.preparar.return_value={'estado':'PREPARADA_SIN_GUARDAR'}
    q,bot=cola(tmp_path,seguro=True,alta=alta)
    job=q.encolar_alta(solicitud(),safe=True)[0]
    q.iniciar_cola()
    assert q.ejecutar_una()
    assert q.store.get(job['id'])['estado']=='PREPARADA_ALTA'
    assert q.store.status()['bloqueo']
    alta.registrar.assert_not_called()
    alta.aceptar.assert_not_called()
    bot.cerrar_polaris_tras_solicitud.assert_not_called()


def test_guardado_alta_incierto_pausa_cola_y_no_cierra_polaris(tmp_path):
    alta=Mock()
    q,bot=cola(tmp_path,alta=alta)
    def guardar(*args,**kwargs):
        bot.etapa_alerta='ALTA: Aceptar solicitado / verificación de guardado'
        raise AltaClienteError('Sin número confirmado')
    alta.registrar.side_effect=guardar
    first=q.encolar_alta(solicitud(),safe=False)[0]
    second=q.encolar_alta(solicitud(rfc='BBB010101BBB'),safe=False)[0]
    q.iniciar_cola()
    assert q.ejecutar_una()
    assert q.store.get(first['id'])['estado']=='REVISION_REQUERIDA'
    assert q.store.get(second['id'])['estado']=='EN_COLA'
    assert q.store.status()['bloqueo'] and not q.ejecutar_una()
    bot.cerrar_polaris_tras_solicitud.assert_not_called()
    alta.liberar.assert_not_called()


def test_cuatro_facturas_segunda_sin_regimen_avisa_y_continua(tmp_path):
    q,bot=cola(tmp_path)
    def emitir(sol,**kwargs):
        if sol.ticket=='2':
            bot.etapa_alerta='FACTURACION: buscar cliente por RFC'
            raise ClienteSinRegimenError()
        bot.etapa_alerta='ENVIO_CORREO: comprobar cierre y actividad'
        return 'ENVIO_SOLICITADO'
    bot.invoice.side_effect=emitir
    jobs=[q.encolar_factura(factura(i),safe=False)[0] for i in range(1,5)]
    q.iniciar_cola()
    for job in jobs:
        assert q.ejecutar_una()
    records=[q.store.get(job['id']) for job in jobs]
    assert [j['estado'] for j in records]==['ENVIO_SOLICITADO','REVISION_REQUERIDA','ENVIO_SOLICITADO','ENVIO_SOLICITADO']
    assert records[1]['resultado']['codigo_cliente']==CODIGO_SIN_REGIMEN
    assert any(c.args[1]=='cliente_sin_regimen' for c in q.avisos.publicar.call_args_list)
    assert estado_publico(records[1])['mensaje']==MENSAJE_SIN_REGIMEN
    subject,body=texto_cliente(records[1],'cliente_sin_regimen',DEFAULTS)
    assert MENSAJE_SIN_REGIMEN in body and 'No es necesario crear otro cliente' in body
    assert bot.cerrar_polaris_tras_solicitud.call_count==4
    assert not q.store.status()['bloqueo'] and not q.ejecutar_una()


@pytest.mark.parametrize('etapa', ['TIMBRADO: Aceptar factura','ENVIO_CORREO: aceptar'])
def test_error_posterior_a_timbrado_nunca_se_vuelve_previo(tmp_path,etapa):
    q,bot=cola(tmp_path)
    bot.invoice.side_effect=ClienteSinRegimenError()
    bot.etapa_alerta=etapa
    job=q.encolar_factura(factura(1),safe=False)[0]
    q.iniciar_cola()
    assert q.ejecutar_una()
    row=q.store.get(job['id'])
    assert row['estado']=='REVISION_REQUERIDA' and q.store.status()['bloqueo']
    assert not row['resultado'].get('codigo_cliente')
    bot.cerrar_polaris_tras_solicitud.assert_not_called()


def test_error_generico_no_dice_cliente_no_registrado(tmp_path):
    q,bot=cola(tmp_path)
    bot.invoice.side_effect=RuntimeError('Error de foco')
    job=q.encolar_factura(factura(1),safe=False)[0]
    q.iniciar_cola();q.ejecutar_una()
    row=q.store.get(job['id'])
    assert not estado_publico(row)['codigo_cliente']
    assert 'dado de alta' not in estado_publico(row)['mensaje']


@pytest.mark.parametrize('texto',[AVISO,AVISO.upper(),AVISO.replace('régimen','regimen').replace(' ','\n')])
def test_firma_del_aviso_del_video(texto):
    assert es_aviso_sin_regimen(texto)


@pytest.mark.parametrize('texto',['','Error SQL','El régimen fiscal es obligatorio','El cliente ya tiene definido el régimen fiscal','No se pudo leer el régimen fiscal'])
def test_otros_errores_no_son_alta_incompleta(texto):
    assert not es_aviso_sin_regimen(texto)


@pytest.mark.parametrize('clase,texto,mismo_proceso,detecta',[
    ('Static',AVISO,True,True),('Edit',AVISO,True,False),
    ('Static',AVISO,False,False),('Static','Otro error',True,False),
])
def test_detector_solo_lee_etiquetas_del_dialogo_de_polaris(monkeypatch,clase,texto,mismo_proceso,detecta):
    gui=SimpleNamespace(IsWindowVisible=lambda h:True,
        GetClassName=lambda h:'#32770' if h==300 else clase,
        GetWindowText=lambda h:texto,
        EnumChildWindows=lambda h,callback,param:callback(301,param))
    monkeypatch.setattr(polaris,'win32gui',gui,raising=False)
    bot=SimpleNamespace(_enum=lambda:[300],_same_polaris_process=lambda a,b:mismo_proceso)
    if detecta:
        with pytest.raises(ClienteSinRegimenError): exigir_cliente_facturable(bot,200)
    else:
        exigir_cliente_facturable(bot,200)


def test_invoice_reconoce_aviso_antes_de_pago_o_timbrado(monkeypatch,tmp_path):
    import arybot.cliente_facturacion as cf
    b=object.__new__(polaris.PolarisBot)
    b.cfg={'app':{'modo_prueba':False}}
    b.base=tmp_path;b._lock=Lock();b.log=Mock();b.diag=Mock()
    for name in ('_guardia_campos_solicitud','ensure_ready','_change_station',
                 '_open_cash_invoice','_select_client','_set_payment','_set_cfdi_usage','_add_ticket'):
        setattr(b,name,Mock(return_value=200))
    check=Mock(side_effect=ClienteSinRegimenError())
    monkeypatch.setattr(cf,'exigir_cliente_facturable',check)
    with pytest.raises(ClienteSinRegimenError): b.invoice(factura(1),test_mode=False)
    check.assert_called_once()
    b._set_payment.assert_not_called();b._add_ticket.assert_not_called()


class Inputs(HTMLParser):
    def __init__(self):super().__init__();self.inputs={}
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='input' and 'id' in attrs:self.inputs[attrs['id']]=attrs


@pytest.mark.parametrize('folder',['portal_web','para_el_sitio'])
def test_formularios_marcan_solo_telefono_opcional(folder):
    text=(Path(__file__).parents[1]/folder/'ary_facturacion.htm').read_text(encoding='utf-8')
    parser=Inputs();parser.feed(text)
    assert 'required' not in parser.inputs['alta-telefono']
    assert 'Teléfono (opcional)' in text
    for name in ('alta-rfc','alta-idcif','alta-correo','alta-confirmacion'):
        assert 'required' in parser.inputs[name]
    assert 'autorizo registrar mi alta' in text
