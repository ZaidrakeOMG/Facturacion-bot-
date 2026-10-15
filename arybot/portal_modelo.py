"""Validación y respuestas del portal. No conecta a Polaris ni captura tarjetas."""
from __future__ import annotations
from .cliente_model import AltaCliente
from .coincidencia_fiscal import CODIGO_VERIFICACION_FISCAL, MENSAJE_VERIFICACION_FISCAL
from .constancia_fiscal import leer_constancia_pdf_base64, ConstanciaError
from .cliente_facturacion import (CODIGO_SIN_REGIMEN, MENSAJE_SIN_REGIMEN,
                                 CODIGO_NO_REGISTRADO, MENSAJE_NO_REGISTRADO)
from .parser import STATIONS, USO_CFDI, Solicitud
from .cola_modelo import datos_alta, factura_datos, tracking
from .asistencia_whatsapp import datos_asistencia

PAGOS = {
    'EFECTIVO': 'Efectivo',
    'TARJETA DE CREDITO': 'Tarjeta de crédito',
    'TARJETA DE DEBITO': 'Tarjeta de débito',
    'TRANSFERENCIA ELECTRONICA DE FONDOS': 'Transferencia',
}

class EntradaPortalError(ValueError):
    pass


def texto(body, key, *, max_len=254, opcional=False):
    value = body.get(key, '' if opcional else None)
    if not isinstance(value, str):
        raise EntradaPortalError('Falta el campo ' + key + ' o su formato no es válido.')
    if len(value) > max_len or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise EntradaPortalError('Revisa el campo ' + key + ': tiene caracteres o una longitud no permitidos.')
    return value.strip()


def validar_entrada(body):
    if not isinstance(body, dict):
        raise EntradaPortalError('La solicitud debe contener un formulario válido.')
    campos_portal = {
        'tipo', 'estacion', 'constancia_pdf', 'correo', 'correo_confirmacion',
        'confirmado', 'sitio_web', 'telefono', 'ticket', 'fecha_ticket',
        'forma_pago', 'uso_cfdi'
    }
    if len(body) > len(campos_portal) or any(not isinstance(k, str) for k in body):
        raise EntradaPortalError('El formulario contiene una estructura no admitida.')
    if set(body) - campos_portal:
        raise EntradaPortalError('El formulario contiene campos no admitidos.')
    if body.get('confirmado') is not True:
        raise EntradaPortalError('Confirma que revisaste los datos antes de enviar.')
    if body.get('sitio_web', '') != '':
        raise EntradaPortalError('No se pudo validar el formulario. Recarga la página.')
    kind = body.get('tipo')
    if kind not in ('ALTA', 'FACTURA'):
        raise EntradaPortalError('Selecciona alta de cliente o facturar ticket.')
    station = texto(body, 'estacion', max_len=50)
    if station not in STATIONS:
        raise EntradaPortalError('Selecciona la estación que corresponde a tu ticket.')
    # El RFC y la razón social SOLO proceden del PDF, nunca de campos inyectados.
    # La calle no es requisito de facturación; el alta conserva su validación.
    try:
        identidad = leer_constancia_pdf_base64(body.get('constancia_pdf'), alta=kind == 'ALTA')
    except ConstanciaError as exc:
        raise EntradaPortalError(str(exc)) from exc
    rfc = identidad.rfc
    email = texto(body, 'correo')
    confirmation = texto(body, 'correo_confirmacion')
    if email != confirmation:
        raise EntradaPortalError('Los dos correos deben coincidir exactamente.')
    allowed = {'tipo', 'estacion', 'constancia_pdf', 'correo', 'correo_confirmacion', 'confirmado', 'sitio_web'}
    try:
        if kind == 'ALTA':
            allowed |= {'telefono'}
            sol = AltaCliente.crear(station, rfc, identidad.idcif,
                                    texto(body, 'telefono', max_len=24, opcional=True), email,
                                    razon_social=identidad.razon_social, calle_constancia=identidad.calle)
            data = datos_alta(sol)
        else:
            allowed |= {'ticket', 'fecha_ticket', 'forma_pago', 'uso_cfdi'}
            payment = texto(body, 'forma_pago', max_len=50)
            if payment not in PAGOS:
                raise EntradaPortalError('Selecciona cómo pagaste: efectivo, crédito, débito o transferencia.')
            usage = texto(body, 'uso_cfdi', max_len=4)
            if usage not in USO_CFDI:
                raise EntradaPortalError('Selecciona un uso de CFDI del catálogo del bot.')
            sol = Solicitud(estacion=station, rfc=rfc, ticket=texto(body, 'ticket', max_len=20),
                            fecha_ticket=texto(body, 'fecha_ticket', max_len=10),
                            forma_pago=payment, uso_cfdi=USO_CFDI[usage],
                            correo_destino=email, remitente=email,
                            razon_social=identidad.razon_social,
                            calle_constancia=identidad.calle)
            data = factura_datos(sol)
        if set(body) - allowed:
            raise EntradaPortalError('El formulario contiene campos no admitidos.')
    except EntradaPortalError:
        raise
    except Exception as exc:
        raise EntradaPortalError(str(exc)) from exc
    return kind, sol, data


def catalogos():
    return {
        'estaciones': [{'valor': name, 'texto': name + ' · Est. ' + info['numero']}
                       for name, info in STATIONS.items()],
        'formas_pago': [{'valor': key, 'texto': label} for key, label in PAGOS.items()],
        'usos_cfdi': [{'valor': key, 'texto': key + ' — ' + desc} for key, desc in USO_CFDI.items()],
    }


def estado_publico(job):
    """Estado para rutas autenticadas con sesión/seguimiento firmado.

    En errores de FACTURA web incluye una lista mínima de la captura original
    para que el usuario continúe por WhatsApp. Nunca PDF, idCIF ni errores internos.
    """
    state = job['estado']
    codigo = (job.get('resultado') or {}).get('codigo_cliente', '')
    es_revision_cliente = (job['tipo'] == 'FACTURA' and state in ('REVISION_REQUERIDA', 'REVISION_CERRADA'))
    sin_regimen = es_revision_cliente and codigo == CODIGO_SIN_REGIMEN
    no_registrado = es_revision_cliente and codigo == CODIGO_NO_REGISTRADO
    verificacion = es_revision_cliente and codigo == CODIGO_VERIFICACION_FISCAL
    messages = {
        'EN_COLA': 'Solicitud recibida y registrada en la cola de atención.',
        'EJECUTANDO': 'El bot está atendiendo tu solicitud.',
        'PREPARADA_FACTURA': 'Prueba preparada sin emitir factura. Requiere revisión del operador.',
        'PREPARADA_ALTA': 'Datos preparados. El personal debe revisar y confirmar el alta.',
        'ACEPTAR_ALTA': 'El personal autorizó el registro; se está confirmando.',
        'ALTA_CONFIRMADA': 'Alta confirmada en el sistema. Este trámite no genera una factura.',
        'ENVIO_SOLICITADO': 'Se solicitó el envío de XML y PDF al correo indicado. No se ha verificado su recepción.',
        'YA_FACTURADO': 'El folio ya aparece procesado/facturado. No se volvió a timbrar; se envió un aviso al correo indicado.',
        'REVISION_REQUERIDA': 'La solicitud requiere revisión. Continúa por WhatsApp con los datos capturados; antes de emitir otra factura se debe comprobar si este ticket ya fue timbrado.',
        'REVISION_CERRADA': 'La revisión quedó cerrada. Continúa por WhatsApp para aclaraciones; no repitas el timbrado sin comprobar el resultado anterior.',
        'CANCELADA': 'Solicitud retirada de la cola por el personal.',
        'PRUEBA_CERRADA': 'Prueba cerrada. No se emitió una factura por este trámite.',
        'ESPERANDO_DATOS': 'El personal necesita revisar o completar los datos. Continúa por WhatsApp con tu solicitud.',
    }
    respuesta = {
        'referencia': tracking(job['id']), 'tipo': job['tipo'], 'estado': state,
        'modo_seguro': bool(job['seguro']),
        'mensaje': (MENSAJE_SIN_REGIMEN if sin_regimen else
                    MENSAJE_NO_REGISTRADO if no_registrado else
                    MENSAJE_VERIFICACION_FISCAL if verificacion else
                    messages.get(state, 'Consulta el resultado con el área de facturación.')),
        'codigo_cliente': (CODIGO_SIN_REGIMEN if sin_regimen else
                           CODIGO_NO_REGISTRADO if no_registrado else
                           CODIGO_VERIFICACION_FISCAL if verificacion else ''),
    }
    asistencia = datos_asistencia(job)
    if asistencia:
        respuesta['asistencia'] = asistencia
    return respuesta
