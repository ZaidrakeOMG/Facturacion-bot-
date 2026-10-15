"""Datos mínimos para asistencia explícita por WhatsApp.

Sólo en respuestas de estado protegidas por sesión/seguimiento del portal.
Nunca incorpora PDF, idCIF, domicilio, tarjetas, tokens ni errores internos.
"""
from __future__ import annotations
from .cola_modelo import tracking

TELEFONO_ARY = '528711651404'
ESTADOS_ASISTENCIA = frozenset(('REVISION_REQUERIDA', 'REVISION_CERRADA',
                              'ESPERANDO_DATOS', 'CANCELADA', 'YA_FACTURADO'))
CAMPOS = {'rfc': 13, 'razon_social': 180, 'estacion': 50, 'ticket': 20,
          'fecha_ticket': 10, 'forma_pago': 50, 'uso_cfdi': 180,
          'correo_destino': 254}
CAMPOS_PERFIL_LOCAL = {'rfc': 13, 'razon_social': 180, 'correo_destino': 254}


def datos_asistencia(job):
    if (job.get('origen') != 'Portal web' or job.get('tipo') != 'FACTURA'
            or job.get('estado') not in ESTADOS_ASISTENCIA):
        return None
    origen = job.get('datos') or {}
    if not isinstance(origen, dict):
        origen = {}
    datos = {}
    for campo, limite in CAMPOS.items():
        valor = origen.get(campo, '')
        if isinstance(valor, str):
            datos[campo] = ' '.join(valor.split())[:limite]
    return {'telefono': TELEFONO_ARY, 'datos': datos,
            'referencia': tracking(job['id']),
            'nota': ('Solicito revisión de mi factura. Antes de emitir otra, '
                     'confirmen si este ticket ya fue timbrado. No autorizo duplicarlo.')}


def perfil_local_facturacion(job):
    """Perfil mínimo para recordar en el navegador tras una recepción autenticada.

    Sólo se devuelve en la respuesta inmediata/reconfirmación de la misma solicitud.
    Nunca forma parte del estado público de seguimiento.
    """
    if job.get('origen') != 'Portal web' or job.get('tipo') != 'FACTURA':
        return None
    origen = job.get('datos') or {}
    if not isinstance(origen, dict):
        return None
    datos = {}
    for campo, limite in CAMPOS_PERFIL_LOCAL.items():
        valor = origen.get(campo, '')
        if isinstance(valor, str):
            datos[campo] = ' '.join(valor.split())[:limite]
    if not datos.get('rfc') or not datos.get('razon_social') or not datos.get('correo_destino'):
        return None
    return datos
