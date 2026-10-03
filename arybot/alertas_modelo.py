"""Datos/validación de alertas internas. No ejecuta SQL de Polaris ni automatiza ventanas."""
from __future__ import annotations
import copy
import hashlib
import re
import unicodedata

DOMINIOS_INTERNOS = frozenset({'grupoary.com', 'grupoary.com.mx'})
PREFIJO_ASUNTO = '[ARY-INTERNO]'
ETIQUETA_HEADER = 'X-ARY-Alerta-Interna'
CATEGORIAS = {
    'ALTA_CLIENTE': 'Alta de cliente',
    'FACTURACION': 'Facturación',
    'ENVIO_FACTURA': 'Envío de factura',
    'SISTEMA': 'Correo / monitor / otras pruebas',
    'PRUEBA_ALERTA': 'Prueba de alertas internas',
}
DEFAULTS = {
    'version': 1,
    'activas': False,
    'destinatarios': [],
    'enviar_en_modo_seguro': False,
    'leer_ventana': True,
    'ocr_respaldo': True,
    'adjuntar_ventana': False,
    'incluir_contexto_cliente': True,
    'categorias': {k: True for k in CATEGORIAS if k != 'PRUEBA_ALERTA'},
    'deduplicar_minutos': 10,
}
EMAIL_RE = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@[A-Za-z0-9.-]+\Z")


def destinatarios_internos(values):
    """Lista estricta, sin nombres, cabeceras, subdominios ni dominios parecidos."""
    if isinstance(values, str):
        values = [s.strip() for s in re.split(r'[,;\n\r]', values) if s.strip()]
    if not isinstance(values, (list, tuple)):
        raise ValueError('Captura un correo interno por línea.')
    result = []
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError('El correo debe ser texto.')
        s = raw.strip().lower()
        if not s: continue
        if len(s) > 254 or not EMAIL_RE.fullmatch(s):
            raise ValueError('Hay un correo con formato inválido. Usa solo usuario@grupoary.com.')
        local, domain = s.rsplit('@', 1)
        if len(local) > 64 or domain not in DOMINIOS_INTERNOS:
            raise ValueError('Solo se admiten correos @grupoary.com o @grupoary.com.mx (sin subdominios).')
        if s not in result: result.append(s)
    if len(result) > 30:
        raise ValueError('Máximo 30 destinatarios internos por alerta.')
    return result


def normalizar_config(data=None):
    out = copy.deepcopy(DEFAULTS)
    if data is None: return out
    if not isinstance(data, dict): raise ValueError('Configuración de alertas inválida.')
    for k in ('activas', 'enviar_en_modo_seguro', 'leer_ventana', 'ocr_respaldo',
              'adjuntar_ventana', 'incluir_contexto_cliente'):
        if k in data:
            if not isinstance(data[k], bool): raise ValueError('Opción de alertas inválida: '+k)
            out[k] = data[k]
    out['destinatarios'] = destinatarios_internos(data.get('destinatarios', []))
    cats = data.get('categorias', {})
    if not isinstance(cats, dict): raise ValueError('Categorías de alertas inválidas.')
    for k in out['categorias']:
        if k in cats:
            if not isinstance(cats[k], bool): raise ValueError('Categoría de alertas inválida.')
            out['categorias'][k] = cats[k]
    out['deduplicar_minutos'] = max(1, min(60, int(data.get('deduplicar_minutos', 10))))
    if out['activas'] and not out['destinatarios']:
        raise ValueError('Agrega al menos un destinatario interno antes de activar las alertas.')
    return out


def limpiar_texto(value, max_len=6000, secretos=()):
    s = str(value or '')
    # Nunca incluir tokens/contraseñas que el sistema pudiera mencionar en un error.
    for secret in secretos:
        if isinstance(secret, str) and len(secret) >= 4:
            s = s.replace(secret, '[OMITIDO]')
    s = re.sub(r'(?i)(bearer\s+)[\w.\-+/=]+', r'\1[OMITIDO]', s)
    s = re.sub(r'(?i)((?:password|contrase[nñ]a|access_token|refresh_token|client_secret|api[_ -]?key|idcif)\s*[=:]\s*)[^\s,;]+', r'\1[OMITIDO]', s)
    s = re.sub(r'(?i)([?&](?:code|token|access_token|refresh_token)=)[^&\s]+', r'\1[OMITIDO]', s)
    s = ''.join(c for c in s if c in '\n\t' or not unicodedata.category(c).startswith('C'))
    return s.strip()[:max_len]


def contexto_seguro(context=None, incluir=True):
    """Whitelist: idCIF, contraseña, cuerpo del correo y adjuntos nunca se agregan."""
    allowed = ('estacion', 'origen', 'id_solicitud', 'estado_operacion')
    if incluir: allowed += ('rfc', 'folio', 'correo_cliente')
    return {k: limpiar_texto((context or {}).get(k, ''), 200)
            for k in allowed if (context or {}).get(k)}


def huella_evento(categoria, etapa, error, context, safe):
    # Se conservan diferencias de solicitud/folio; no agrupa todos los errores iguales.
    from json import dumps
    return hashlib.sha256(dumps([categoria, etapa, error, context, bool(safe)],
                                 sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def contenido_correo(event):
    """Texto observado != diagnóstico. OCR se etiqueta, nunca manda clics/SQL."""
    ctx = event.get('contexto', {})
    evid = event.get('evidencia', {})
    name = CATEGORIAS.get(event['categoria'], 'Operación')
    mode = 'PRUEBA / MODO SEGURO' if event.get('modo_seguro') else 'PRODUCCIÓN'
    subject = f"{PREFIJO_ASUNTO} {name} | {ctx.get('estacion', 'Bot')} | {event['id'][:10]}"
    parts = [
        'ALERTA INTERNA — GRUPO ARY',
        'Este aviso es para el equipo interno, no para el cliente.', '',
        'Incidente: '+event['id'], 'Fecha: '+event['fecha'],
        'Operación: '+name, 'Modo: '+mode, 'Paso: '+event.get('etapa', 'Sin paso identificado'),
    ]
    labels = {'estacion':'Estación', 'rfc':'RFC', 'folio':'Folio / ticket',
              'correo_cliente':'Correo indicado por el cliente', 'origen':'Origen',
              'id_solicitud':'ID de solicitud', 'estado_operacion':'Estado local de la operación'}
    parts += [labels[k]+': '+v for k, v in ctx.items() if k in labels]
    parts += ['', 'ERROR DETECTADO POR EL BOT:', event.get('error', 'Error no especificado.'), '',
              'MENSAJE DE LA VENTANA (solo lectura):',
              'Método: '+evid.get('metodo', 'SIN_VENTANA'),
              evid.get('texto') or 'No fue posible obtener el texto de una ventana emergente.',
              evid.get('nota', ''), '',
              'ACCIÓN REQUERIDA:',
              'Revisar Polaris y la solicitud. El aviso NO confirma que la operación se haya revertido.',
              'Si ya se pulsó Aceptar, comprobar primero si existe el cliente, la factura o el envío.',
              'El módulo de alertas no acepta/cierra ventanas, no registra clientes y no repite timbrados.',
              'La lectura OCR puede contener errores: verificarla en la ventana original.',
              'No reenviar datos de clientes fuera de los destinatarios internos autorizados.',
    ]
    return subject, '\n'.join(parts)
