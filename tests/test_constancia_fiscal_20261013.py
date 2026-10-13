"""Pruebas sin SAT, credenciales, Gmail real ni timbrado."""
from __future__ import annotations
from base64 import b64encode
from io import BytesIO
from pathlib import Path
from datetime import date
from unittest.mock import Mock
import pytest

from arybot.constancia_fiscal import ConstanciaError, leer_constancia_pdf_base64
from arybot.coincidencia_fiscal import CoincidenciaFiscalError, verificar_unico
from arybot.portal_modelo import validar_entrada, EntradaPortalError


def documento_pdf(*, nombre='SERVICIOS ESPECIALIZADOS PENOLES SA DE CV',
                  calle='LEGARIA', rfc='SEP1312171X9', idcif='00123456789',
                  incluir_idcif=True):
    from reportlab.pdfgen import canvas
    destino=BytesIO()
    documento=canvas.Canvas(destino)
    campos=[
        'Constancia de Situacion Fiscal',
        'Datos de Identificacion del Contribuyente',
        'RFC: '+rfc,
        'Denominacion/Razon Social: '+nombre,
        'Datos del domicilio registrado',
        'Tipo de Vialidad: CALZADA',
        'Nombre de Vialidad: '+calle,
        'Codigo Postal: 27000',
    ]
    if incluir_idcif: campos.append('idCIF: '+idcif)
    for idx,line in enumerate(campos):
        documento.drawString(38,740-idx*24,line)
    documento.save()
    return destino.getvalue()


def cuerpo(*, tipo='FACTURA', pdf=None):
    values={
        'tipo':tipo,'estacion':'ARY V','constancia_pdf':b64encode(pdf or documento_pdf()).decode('ascii'),
        'correo':'cliente@example.com','correo_confirmacion':'cliente@example.com',
        'sitio_web':'','confirmado':True,
    }
    if tipo=='FACTURA':
        values.update(ticket='123',fecha_ticket=date.today().isoformat(),
                      forma_pago='EFECTIVO',uso_cfdi='G03')
    return values


def test_identidad_extraida_del_pdf_en_memoria_sin_guardar_archivo(tmp_path):
    doc=documento_pdf()
    data=leer_constancia_pdf_base64(b64encode(doc).decode('ascii'),alta=True)
    assert data.rfc=='SEP1312171X9'
    assert data.razon_social=='SERVICIOS ESPECIALIZADOS PENOLES SA DE CV'
    assert data.calle=='CALZADA LEGARIA'
    assert data.idcif=='00123456789'
    assert list(tmp_path.iterdir())==[]


def test_factura_toma_solo_identidad_del_pdf_y_no_guarda_base64():
    formulario=cuerpo()
    kind,sol,datos=validar_entrada(formulario)
    assert kind=='FACTURA'
    assert sol.rfc=='SEP1312171X9'
    assert sol.razon_social=='SERVICIOS ESPECIALIZADOS PENOLES SA DE CV'
    assert sol.calle_constancia=='CALZADA LEGARIA'
    assert 'constancia_pdf' not in datos and 'constancia_archivo' not in datos
    assert 'JVBER' not in str(datos)


def test_alta_toma_rfc_e_idcif_del_pdf_y_telefono_es_opcional():
    kind,sol,datos=validar_entrada(cuerpo(tipo='ALTA'))
    assert kind=='ALTA'
    assert sol.idcif=='00123456789' and sol.telefono==''
    assert datos['razon_social']=='SERVICIOS ESPECIALIZADOS PENOLES SA DE CV'
    assert datos['calle_constancia']=='CALZADA LEGARIA'
    assert 'constancia_pdf' not in datos


@pytest.mark.parametrize('value', ['', None, 'invalid', b64encode(b'%PDF- fake').decode('ascii')])
def test_constancia_invalida_se_rechaza(value):
    with pytest.raises(ConstanciaError):
        leer_constancia_pdf_base64(value)


def test_pdf_sin_texto_no_se_toma_como_constancia():
    from reportlab.pdfgen import canvas
    dest=BytesIO()
    c=canvas.Canvas(dest)
    c.rect(10,10,100,100)
    c.save()
    with pytest.raises(ConstanciaError):
        leer_constancia_pdf_base64(b64encode(dest.getvalue()).decode('ascii'))


def test_sin_idcif_no_hace_alta_automatica():
    form=cuerpo(tipo='ALTA',pdf=documento_pdf(incluir_idcif=False))
    with pytest.raises(EntradaPortalError, match='idCIF'):
        validar_entrada(form)


def test_no_permite_inyectar_rfc_o_razon_social_manual():
    form=cuerpo()
    form['rfc']='AAA010101AAA'
    with pytest.raises(EntradaPortalError, match='no admitidos'):
        validar_entrada(form)


def test_camino_web_exige_constancia_pdf():
    form=cuerpo()
    form.pop('constancia_pdf')
    with pytest.raises(EntradaPortalError, match='constancia'):
        validar_entrada(form)


def test_coincidencia_estricta_no_elige_primera_fila():
    registros=[
        {'numero':'1071','razon_social':'SERVICIOS ESPECIALIZADOS PENOLES',
         'rfc':'SEP1312171X9','calle':'MANUEL GOMEZ MORIN'},
        {'numero':'9281','razon_social':'SERVICIOS ESPECIALIZADOS PEÑOLES S A DE C V',
         'rfc':'SEP1312171X9','calle':'CALZ. LEGARIA TORRE 2 PISO 3'},
    ]
    match=verificar_unico(registros,rfc='SEP1312171X9',
                         razon_social='SERVICIOS ESPECIALIZADOS PEÑOLES SA DE CV',
                         calle='CALZADA LEGARIA')
    assert match['numero']=='9281'


def test_rfc_solo_o_razon_social_sin_calle_nunca_se_aceptan():
    registros=[{'razon_social':'EMPRESA SA DE CV','rfc':'SEP1312171X9','calle':'CALZADA LEGARIA'}]
    with pytest.raises(CoincidenciaFiscalError):
        verificar_unico(registros,rfc='SEP1312171X9',razon_social='',calle='CALZADA LEGARIA')
    with pytest.raises(CoincidenciaFiscalError):
        verificar_unico(registros,rfc='SEP1312171X9',razon_social='EMPRESA SA DE CV',calle='')


def test_dos_coincidencias_exactas_detenidas_sin_seleccionar():
    fila={'numero':'9281','razon_social':'EMPRESA SA DE CV',
          'rfc':'SEP1312171X9','calle':'CALZADA LEGARIA'}
    with pytest.raises(CoincidenciaFiscalError, match='UNA coincidencia'):
        verificar_unico([fila,dict(fila,numero='9282')],
                         rfc=fila['rfc'],razon_social=fila['razon_social'],calle=fila['calle'])


def test_calle_distinta_nunca_coincide_ni_ignora_sufijo_legal():
    fila={'numero':'9281','razon_social':'EMPRESA SA DE CV',
          'rfc':'SEP1312171X9','calle':'CALZADA LEGARIA'}
    with pytest.raises(CoincidenciaFiscalError):
        verificar_unico([fila],rfc=fila['rfc'],
                         razon_social='EMPRESA',calle='CALZADA LEGARIA')
    with pytest.raises(CoincidenciaFiscalError):
        verificar_unico([fila],rfc=fila['rfc'],
                         razon_social='EMPRESA SA DE CV',calle='MANUEL GOMEZ MORIN')


def test_formularios_solo_tienen_pdf_obligatorio():
    for folder in ('portal_web', 'para_el_sitio'):
        html=(Path(__file__).parents[1]/folder/'ary_facturacion.htm').read_text(encoding='utf-8')
        for kind in ('factura','alta'):
            assert 'id="'+kind+'-constancia"' in html
        assert 'id="factura-rfc"' not in html
        assert 'id="alta-rfc"' not in html
        assert 'id="alta-idcif"' not in html
        assert 'values.constancia_pdf=' in html


def test_no_se_confirma_cliente_si_la_uia_no_expone_total_de_filas(monkeypatch):
    """No se debe elegir la primera fila cuando faltan controles accesibles."""
    import sys
    from types import SimpleNamespace
    from arybot.lectura_catalogo_fiscal import seleccionar_cliente_exacto
    class FakeWindow:
        def descendants(self):
            return []
    fake = SimpleNamespace(Desktop=lambda backend: SimpleNamespace(
        window=lambda handle: FakeWindow()))
    monkeypatch.setitem(sys.modules, 'pywinauto', fake)
    with pytest.raises(CoincidenciaFiscalError, match='total verificable'):
        seleccionar_cliente_exacto(77, rfc='SEP1312171X9',
            razon_social='EMPRESA SA DE CV',calle='CALZADA LEGARIA')


def test_verificacion_fiscal_no_concluyente_continua_siguientes_facturas(tmp_path):
    from datetime import date
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS, texto_cliente
    from arybot.parser import Solicitud
    from arybot.portal_modelo import estado_publico
    from arybot.coincidencia_fiscal import CODIGO_VERIFICACION_FISCAL, MENSAJE_VERIFICACION_FISCAL

    config={'app':{'modo_prueba':False,'correo':'test@example.com'},
            'gmail':{},'ocr':{},'polaris':{},'cola':dict(DEFAULTS)}
    bot=Mock()
    bot.diag=None
    def ejecutar(sol,**kw):
        if sol.ticket=='2':
            bot.etapa_alerta='FACTURACION: buscar cliente por RFC'
            raise CoincidenciaFiscalError('Ninguna coincidencia única.')
        return 'ENVIO_SOLICITADO'
    bot.invoice.side_effect=ejecutar
    q=ColaOperaciones(tmp_path,config,bot,Mock(),avisos=Mock())
    jobs=[]
    for n in range(1,5):
        sol=Solicitud(estacion='ARY V', rfc='SEP1312171X9',
                      razon_social='SERVICIOS ESPECIALIZADOS PENOLES SA DE CV',
                      calle_constancia='CALZADA LEGARIA',
                      ticket=str(n), fecha_ticket=date.today().isoformat(),
                      forma_pago='EFECTIVO',correo_destino='cliente@example.com',
                      remitente='cliente@example.com')
        jobs.append(q.encolar_factura(sol,safe=False)[0])
    q.iniciar_cola()
    for job in jobs: assert q.ejecutar_una()
    records=[q.store.get(job['id']) for job in jobs]
    assert [r['estado'] for r in records] == ['ENVIO_SOLICITADO',
        'REVISION_REQUERIDA','ENVIO_SOLICITADO','ENVIO_SOLICITADO']
    assert records[1]['resultado']['codigo_cliente']==CODIGO_VERIFICACION_FISCAL
    assert estado_publico(records[1])['mensaje']==MENSAJE_VERIFICACION_FISCAL
    assert any(c.args[1]=='verificacion_fiscal' for c in q.avisos.publicar.call_args_list)
    _,text=texto_cliente(records[1],'verificacion_fiscal',DEFAULTS)
    assert MENSAJE_VERIFICACION_FISCAL in text
    assert not q.store.status()['bloqueo'] and not q.store.status()['pausada']
    assert bot.cerrar_polaris_tras_solicitud.call_count==4


def test_verificacion_fiscal_no_reclasifica_error_posterior_al_timbrado(tmp_path):
    from datetime import date
    from arybot.cola_operaciones import ColaOperaciones
    from arybot.cola_modelo import DEFAULTS
    from arybot.parser import Solicitud
    from arybot.portal_modelo import estado_publico
    config={'app':{'modo_prueba':False},'gmail':{},'ocr':{},'polaris':{},'cola':dict(DEFAULTS)}
    bot=Mock()
    bot.diag=None
    bot.invoice.side_effect=CoincidenciaFiscalError('No pudo verificar la coincidencia.')
    bot.etapa_alerta='TIMBRADO: Aceptar factura'
    q=ColaOperaciones(tmp_path,config,bot,Mock(),avisos=Mock())
    sol=Solicitud(estacion='ARY V',rfc='SEP1312171X9',ticket='1',
                  fecha_ticket=date.today().isoformat(),forma_pago='EFECTIVO',
                  correo_destino='cliente@example.com',remitente='cliente@example.com')
    job=q.encolar_factura(sol,safe=False)[0]
    q.iniciar_cola()
    assert q.ejecutar_una()
    saved=q.store.get(job['id'])
    assert saved['estado']=='REVISION_REQUERIDA'
    assert q.store.status()['bloqueo']==job['id']
    assert not estado_publico(saved)['codigo_cliente']
    bot.cerrar_polaris_tras_solicitud.assert_not_called()


@pytest.mark.parametrize('linea_rfc', [
    'RFC: SEP1312171X9',
    'RFC : SEP1312171X9',
    'R.F.C.: SEP1312171X9',
    'Registro Federal de Contribuyentes:\nSEP1312171X9',
    'RFC:\nSEP1312171X9',
    'RFC: SEP 131217 1X9',
    'RFC: SEP-131217-1X9',
    'SEP1312171X9\nRFC:',
])
def test_constancia_pdf_sat_rfc_variantes_de_extraccion(linea_rfc):
    from arybot.constancia_fiscal import _validar_identidad
    texto = (
        'Constancia de Situacion Fiscal\n'
        'Datos de Identificacion del Contribuyente\n'
        + linea_rfc + '\n'
        'Denominacion/Razon Social: EMPRESA DE PRUEBA SA DE CV\n'
        'Tipo de Vialidad: CALZADA\n'
        'Nombre de Vialidad: LEGARIA\n'
    )
    identificacion = _validar_identidad(texto)
    assert identificacion.rfc == 'SEP1312171X9'
    assert identificacion.razon_social == 'EMPRESA DE PRUEBA SA DE CV'
    assert identificacion.calle == 'CALZADA LEGARIA'


def test_constancia_pdf_sat_rfc_fraccionado_en_pdf_end_to_end():
    pdf = documento_pdf(rfc='SEP 131217 1X9')
    fiscal = leer_constancia_pdf_base64(b64encode(pdf).decode('ascii'))
    assert fiscal.rfc == 'SEP1312171X9'


def test_constancia_pdf_con_dos_rfc_distintos_se_rechaza():
    from arybot.constancia_fiscal import _validar_identidad
    texto = (
        'Constancia de Situacion Fiscal\n'
        'Datos de Identificacion del Contribuyente\n'
        'RFC: SEP1312171X9\n'
        'RFC: ABC010101ABC\n'
        'Denominacion/Razon Social: EMPRESA DE PRUEBA SA DE CV\n'
        'Tipo de Vialidad: CALZADA\nNombre de Vialidad: LEGARIA\n'
    )
    with pytest.raises(ConstanciaError, match='más de un RFC'):
        _validar_identidad(texto)


def test_constancia_pdf_rfc_repetido_igual_es_valido():
    from arybot.constancia_fiscal import _validar_identidad
    texto = (
        'Constancia de Situacion Fiscal\n'
        'Datos de Identificacion del Contribuyente\n'
        'RFC: SEP1312171X9\n'
        'Registro Federal de Contribuyentes: SEP1312171X9\n'
        'Denominacion/Razon Social: EMPRESA DE PRUEBA SA DE CV\n'
        'Tipo de Vialidad: CALZADA\nNombre de Vialidad: LEGARIA\n'
    )
    assert _validar_identidad(texto).rfc == 'SEP1312171X9'


def test_constancia_pdf_sin_rfc_no_inventa_y_bloquea():
    from arybot.constancia_fiscal import _validar_identidad
    texto = (
        'Constancia de Situacion Fiscal\n'
        'Datos de Identificacion del Contribuyente\n'
        'CURP: SEP1312171X9ABC1234\n'
        'Denominacion/Razon Social: EMPRESA DE PRUEBA SA DE CV\n'
        'Tipo de Vialidad: CALZADA\nNombre de Vialidad: LEGARIA\n'
    )
    with pytest.raises(ConstanciaError, match='No se pudo reconocer el RFC'):
        _validar_identidad(texto)
