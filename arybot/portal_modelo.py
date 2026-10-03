"""Validación del portal. Reutiliza los modelos del ZIP ARY v3.1.9.

No conecta a Polaris ni a servicios fiscales. No captura datos de tarjeta.
"""
from __future__ import annotations
from .cliente_model import AltaCliente
from .parser import STATIONS, USO_CFDI, Solicitud
from .cola_modelo import datos_alta, factura_datos, tracking

PAGOS = {
    'EFECTIVO': 'Efectivo',
    'TARJETA DE CREDITO': 'Tarjeta de crédito',
    'TARJETA DE DEBITO': 'Tarjeta de débito',
    'TRANSFERENCIA ELECTRONICA DE FONDOS': 'Transferencia',
}

class EntradaPortalError(ValueError):
    pass


def texto(body, key, *, max_len=254):
    value = body.get(key)
    if not isinstance(value, str):
        raise EntradaPortalError('Falta el campo ' + key + ' o su formato no es válido.')
    if len(value) > max_len or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise EntradaPortalError('Revisa el campo ' + key + ': tiene caracteres o una longitud no permitidos.')
    return value.strip()


def validar_entrada(body):
    if not isinstance(body, dict):
        raise EntradaPortalError('La solicitud debe contener un formulario válido.')
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
    rfc = texto(body, 'rfc', max_len=13).upper()
    email = texto(body, 'correo')
    confirmation = texto(body, 'correo_confirmacion')
    if email != confirmation:
        raise EntradaPortalError('Los dos correos deben coincidir exactamente.')
    allowed = {'tipo', 'estacion', 'rfc', 'correo', 'correo_confirmacion', 'confirmado', 'sitio_web'}
    try:
        if kind == 'ALTA':
            allowed |= {'idcif', 'telefono'}
            sol = AltaCliente.crear(station, rfc, texto(body, 'idcif', max_len=11),
                                    texto(body, 'telefono', max_len=24), email)
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
                            correo_destino=email, remitente=email)
            data = factura_datos(sol)
        if set(body) - allowed:
            raise EntradaPortalError('El formulario contiene campos no admitidos.')
    except EntradaPortalError:
        raise
    except Exception as exc:
        # Estos modelos locales solo emiten errores de validación, no errores de Polaris.
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
    """No devuelve RFC, correo, idCIF, pantallas ni mensajes internos de Polaris."""
    state = job['estado']
    messages = {
        'EN_COLA': 'Solicitud recibida y registrada en la cola de atención.',
        'EJECUTANDO': 'El bot está atendiendo tu solicitud.',
        'PREPARADA_FACTURA': 'Prueba preparada sin emitir factura. Requiere revisión del operador.',
        'PREPARADA_ALTA': 'Datos preparados. El personal debe revisar y confirmar el alta.',
        'ACEPTAR_ALTA': 'El personal autorizó el registro; se está confirmando.',
        'ALTA_CONFIRMADA': 'Alta confirmada en el sistema. Este trámite no genera una factura.',
        'ENVIO_SOLICITADO': 'Se solicitó el envío de XML y PDF al correo indicado. No se ha verificado su recepción.',
        'YA_FACTURADO': 'El folio ya aparece procesado/facturado. No se volvió a timbrar; se envió un aviso al correo indicado.',
        'REVISION_REQUERIDA': 'La solicitud terminó en revisión. Puedes iniciar una nueva consulta del ticket cuando no haya otra solicitud activa.',
        'REVISION_CERRADA': 'La revisión anterior quedó cerrada. Si necesitas comprobar de nuevo el ticket, puedes iniciar una nueva solicitud.',
        'CANCELADA': 'Solicitud retirada de la cola por el personal.',
        'PRUEBA_CERRADA': 'Prueba cerrada. No se emitió una factura por este trámite.',
        'ESPERANDO_DATOS': 'El personal necesita revisar o completar los datos.',
    }
    return {
        'referencia': tracking(job['id']), 'tipo': job['tipo'], 'estado': state,
        'modo_seguro': bool(job['seguro']),
        'mensaje': messages.get(state, 'Consulta el resultado con el área de facturación.'),
    }
