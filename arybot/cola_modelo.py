"""Modelos puros de la cola LOCAL y de los avisos al cliente. Sin acceso a Polaris."""
from __future__ import annotations
import hashlib
import re
from dataclasses import fields
from .parser import Solicitud, normalize_station, normalize_payment, normalize_cfdi_usage, normalize_ticket_date, _field, _plain
from .cliente_model import AltaCliente
from .factura_final import validar_correo

VERSION = '3.4.0'
HEADER_AVISO = 'X-ARY-Aviso-Cliente'
ASUNTO_AVISO = '[ARY Solicitud]'
DEFAULTS = {
    'avisar_cliente': True,
    'avisar_en_modo_seguro': False,
    'minutos_factura': 5,
    'minutos_alta': 3,
    'margen_minutos': 5,
}
ESTADOS_REVISION = {'PREPARADA_FACTURA', 'PREPARADA_ALTA', 'REVISION_REQUERIDA', 'PRUEBA_PENDIENTE'}
ESTADOS_TERMINALES = {'ENVIO_SOLICITADO','YA_FACTURADO','ALTA_CONFIRMADA','CANCELADA','PRUEBA_CERRADA',
                     'REVISION_CERRADA','FINALIZADA','ESPERANDO_DATOS','DUPLICADA','REVISION_ENTRADA'}
# Solo bloquean duplicados los trabajos que siguen activos o cuyo resultado es incierto.
# Los resultados ya terminados no impiden una nueva solicitud: Polaris/control local decide el resultado.
BLOQUEAN_CLAVE = {'EN_COLA','EJECUTANDO'}
BLOQUEAN_CLAVE_ALTA = {'EN_COLA','EJECUTANDO','PREPARADA_ALTA','ACEPTAR_ALTA'}

class ColaError(RuntimeError): pass
class SolicitudDuplicada(ColaError): pass


def ajustes_cola(cfg):
    raw = cfg.get('cola', {})
    out = dict(DEFAULTS)
    for key in ('avisar_cliente','avisar_en_modo_seguro'):
        out[key] = raw.get(key, out[key]) is True
    for key in ('minutos_factura','minutos_alta','margen_minutos'):
        try: out[key] = int(raw.get(key, out[key]))
        except (TypeError, ValueError): raise ValueError('El estimado debe contener minutos enteros.')
        if not 1 <= out[key] <= 240: raise ValueError('Los tiempos deben estar entre 1 y 240 minutos.')
    return out


def factura_datos(sol: Solicitud):
    """Instantánea serializable; no conserva adjuntos ni objetos de interfaz."""
    st = normalize_station(sol.estacion)
    if not st: raise ValueError('Selecciona una estación válida.')
    rf = str(sol.rfc or '').strip().upper().replace(' ', '')
    if not re.fullmatch(r'[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}', rf): raise ValueError('El RFC tiene un formato inválido.')
    ticket = str(sol.ticket or '').strip()
    if not re.fullmatch(r'\d{1,20}', ticket) or int(ticket) <= 0:
        raise ValueError('Escribe un solo folio numérico válido.')
    fecha_ticket = normalize_ticket_date(getattr(sol,'fecha_ticket',''))
    if not fecha_ticket:
        raise ValueError('Falta la fecha del ticket.')
    # Mantiene ceros para la captura; la huella normaliza el número para evitar duplicados.
    payment = normalize_payment(sol.forma_pago) if str(sol.forma_pago or '').strip() else ''
    if not payment or payment == 'POR DEFINIR': raise ValueError('Falta la forma de pago elegida por el cliente.')
    email = validar_correo(sol.correo_destino or sol.remitente)
    return {'estacion':st,'rfc':rf,'ticket':ticket,'fecha_ticket':fecha_ticket,'forma_pago':payment,
            'uso_cfdi':normalize_cfdi_usage(sol.uso_cfdi), 'correo_destino':email,
            'remitente':str(sol.remitente or email), 'razon_social':str(sol.razon_social or '')}


def como_solicitud(data):
    allowed = {f.name for f in fields(Solicitud)}
    return Solicitud(**{k:v for k,v in data.items() if k in allowed})


def clave_trabajo(kind, data):
    if kind == 'FACTURA':
        identity = 'FACTURA|'+data['estacion']+'|'+str(int(data['ticket']))
    elif kind == 'ALTA':
        identity = 'ALTA|'+data['estacion']+'|'+data['rfc']
    else: return ''
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


def datos_alta(sol):
    checked = sol.validar()
    return {'estacion':checked.estacion, **checked.campos_cliente()}


def es_alta(subject, text):
    op = _plain(_field(text, [r'operaci[oó]n',r'tipo\s+de\s+solicitud']))
    subj = _plain(subject)
    return op in {'ALTA','ALTA CLIENTE','ALTA DE CLIENTE','REGISTRO CLIENTE'} or bool(
        re.search(r'\b(?:ALTA|REGISTRO) (?:DE )?CLIENTE\b', subj))


def leer_alta(subject, text, sender):
    # Sin extraer valores de un correo citado: el cliente manda cuatro campos explícitos.
    from .parser import detect_station, RFC_RE
    st = normalize_station(_field(text,[r'estaci[oó]n',r'sucursal'])) or detect_station(subject)
    rfc = _field(text,[r'r\.?f\.?c\.?'])
    cif = _field(text,[r'id\s*cif',r'idcfi',r'idcif'])
    phone = _field(text,[r'tel[eé]fono',r'telefono\s*1',r'celular'])
    email = _field(text,[r'correo(?:\s+electr[oó]nico)?',r'email',r'e-mail']) or sender
    return AltaCliente.crear(st,rfc,cif,phone,email)


def titulo_tipo(kind):
    return 'alta de cliente' if kind == 'ALTA' else 'factura'


def tracking(ident): return 'ARY-'+str(ident).upper()[:12]


def texto_cliente(job, event, opts, *, eta=None, reason=''):
    """Mensajes sin errores PAC/SQL, OCR, datos de otros clientes ni números inventados."""
    kind = titulo_tipo(job['tipo'])
    ref = tracking(job['id'])
    data = job['datos']
    subject = ASUNTO_AVISO+' '+ref+' — '+kind.capitalize()
    lines = ['Hola,', '', 'Solicitud: '+ref, 'Estación: '+str(data.get('estacion','No indicada'))]
    if job['tipo'] == 'FACTURA' and data.get('ticket'): lines.append('Folio: '+str(data['ticket']))
    lines.append('')
    if job['seguro']:
        lines += ['AVISO DE PRUEBA INTERNA. Esta solicitud está en MODO SEGURO.',
                  'No se autoriza guardar un cliente, emitir una factura ni enviar un CFDI desde esta prueba.',
                  'Estado de la prueba: '+job['estado']+'.']
    elif event == 'recibida':
        lines += ['Recibimos su solicitud de '+kind+' y quedó registrada para su atención.']
        if eta:
            lo, hi = eta
            lines += [f'Tiempo estimado de atención: {lo} a {hi} minutos.',
                      'Es una estimación, no una garantía de entrega; depende de la cola y de la disponibilidad del sistema de facturación.']
        else:
            lines += ['Por el momento la atención está pendiente de revisión o de reanudar la cola. '
                      'No hay un tiempo de finalización confirmado.']
        if job['tipo']=='FACTURA':
            lines += ['Al completar la facturación se solicitará el envío de su XML y PDF al correo indicado.']
        else:
            lines += ['Le avisaremos por correo cuando el registro quede confirmado. '
                      'El alta requiere revisión del personal antes de guardar.']
        lines += ['En caso de alguna falla, el área de facturación se comunicará con usted al correo proporcionado.',
                  'No es necesario volver a enviar la misma solicitud.']
    elif event == 'procesando':
        lines += ['Su solicitud de '+kind+' está siendo atendida.']
        if eta: lines += [f'Tiempo restante estimado: {eta[0]} a {eta[1]} minutos; puede variar.']
        lines += ['Al terminar recibirá el resultado por correo. '
                  'Si hay una falla, nuestro personal revisará el caso y se comunicará con usted.']
    elif event == 'ya_facturado':
        lines += ['El folio indicado ya aparece procesado/facturado para la estación seleccionada.',
                  'No se volvió a timbrar el ticket. Si necesita recuperar XML o PDF, contacte al área de facturación con este número de solicitud.']
    elif event == 'completada':
        if job['tipo']=='FACTURA':
            lines += ['Se solicitó el envío de su factura (XML y PDF) al correo indicado.',
                      'Este aviso no contiene la factura ni confirma su recepción. Revise su bandeja y correo no deseado.',
                      'Si no recibe el comprobante, contacte al área de facturación con el número de solicitud. '
                      'No vuelva a solicitar la emisión del mismo folio.']
        else:
            lines += ['El registro del cliente quedó confirmado en nuestro sistema de facturación.',
                      'Este aviso corresponde únicamente al alta; no se ha generado una factura como parte de este registro.']
    elif event == 'revision_alta':
        lines += ['Sus datos fueron preparados y están pendientes de revisión del personal.',
                  'Todavía no se considera registrado al cliente. Le avisaremos cuando se confirme el alta.']
    elif event == 'faltantes':
        lines += ['Para continuar con su solicitud necesitamos corregir o completar estos datos:', reason,
                  'Envíe una nueva solicitud completa con todos los campos. No se inició la operación en Polaris.']
    elif event == 'cancelada':
        lines += ['La solicitud fue retirada de la cola antes de iniciar la operación.',
                  'Para aclaraciones, contacte al área de facturación con el número de solicitud.']
    else:
        lines += ['Su solicitud de '+kind+' quedó pendiente de revisión por el área de facturación.',
                  'No podemos confirmar que el proceso haya concluido. Nuestro personal se comunicará con usted.',
                  'No vuelva a solicitar la misma operación hasta recibir indicaciones, para evitar duplicados.']
    lines += ['', 'Grupo ARY — Facturación', 'Aviso automático. Conserve el número de solicitud para seguimiento.']
    return subject, '\n'.join(lines)
