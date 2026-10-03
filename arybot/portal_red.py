"""Filtro de red antes del portal. Nunca inicia una operación en Polaris.

Waitress NO debe configurar trusted_proxy: necesitamos la IP real del par TCP.
IIS debe sobrescribir X-ARY-Client-IP y X-ARY-Proxy-HTTPS en cada solicitud.
Los headers estándar Forwarded/X-Forwarded-* no se usan para autorizar equipos.
"""
from __future__ import annotations
import ipaddress
import json
from http import HTTPStatus

SALUD = '/api/facturacion/salud'
PRIVADAS = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def direccion(value):
    if not isinstance(value, str) or not value or value != value.strip() or '%' in value:
        raise ValueError('Usa una dirección IP literal, sin rangos, puertos ni comodines.')
    addr = ipaddress.ip_address(value)
    return addr.ipv4_mapped if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped else addr


def privada_v4(addr):
    return isinstance(addr, ipaddress.IPv4Address) and any(addr in net for net in PRIVADAS)


def validar_red(cfg):
    bind = direccion(cfg.get('escuchar_en', '127.0.0.1'))
    proxy_raw = cfg.get('proxy_confiable', '')
    if not isinstance(proxy_raw, str):
        raise ValueError('proxy_confiable debe ser una IP o texto vacío en modo local.')
    proxy = direccion(proxy_raw) if proxy_raw else None
    if not bind.is_loopback and not privada_v4(bind):
        raise ValueError('escuchar_en debe ser loopback o una IPv4 privada RFC1918 concreta.')
    if bind.is_loopback:
        if proxy is not None and not proxy.is_loopback:
            raise ValueError('Un proxy en otra computadora requiere escuchar en la IP privada del bot.')
    elif (proxy is None or not privada_v4(proxy) or proxy == bind or not cfg.get('https_publico')):
        raise ValueError('El modo LAN requiere HTTPS público y la IP privada exacta de otro equipo IIS.')
    return bind, proxy


class PortalRedWSGI:
    """Restringe el receptor al IIS indicado; salud también admite al propio bot."""
    def __init__(self, app, settings):
        self.app = app
        self.settings = settings
        self.bind, self.proxy = validar_red(settings)
        self.lan = not self.bind.is_loopback

    @staticmethod
    def responder(start_response, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        start_response(f'{code} {HTTPStatus(code).phrase}', [
            ('Content-Type', 'application/json; charset=utf-8'),
            ('Content-Length', str(len(body))), ('Cache-Control', 'no-store'),
            ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
            ('X-Frame-Options', 'DENY')])
        return [body]

    def __call__(self, environ, start_response):
        try:
            peer = direccion(environ.get('REMOTE_ADDR', ''))
        except ValueError:
            return self.responder(start_response, 403, {'error': 'Conexión no autorizada.'})
        path = environ.get('PATH_INFO', '')
        if path == SALUD:
            allowed = peer in {self.bind, self.proxy} if self.lan else peer.is_loopback
            if not allowed:
                return self.responder(start_response, 403, {'error': 'Conexión no autorizada.'})
            if environ.get('REQUEST_METHOD', 'GET').upper() != 'GET':
                return self.responder(start_response, 405, {'error': 'La prueba solo admite GET.'})
            return self.responder(start_response, 200, {
                'ok': True, 'servicio': 'ARY Portal Facturacion', 'version': '1.2-integrado',
                'modo_seguro': self.app.modo_seguro(), 'recepcion_activa': self.app.aceptando})

        if self.lan:
            # Comprobar el par TCP ANTES de leer cualquier dirección reenviada.
            if peer != self.proxy:
                return self.responder(start_response, 403, {'error': 'Solo el servidor IIS autorizado puede enviar solicitudes.'})
            if not path.startswith('/api/facturacion/'):
                return self.responder(start_response, 404, {'error': 'Abre el formulario desde el sitio HTTPS de ARY.'})
            if environ.get('HTTP_X_ARY_PROXY_HTTPS') != 'on':
                return self.responder(start_response, 403, {'error': 'Falta la confirmación HTTPS del proxy IIS.'})
            try:
                client = direccion(environ.get('HTTP_X_ARY_CLIENT_IP', ''))
                if client.is_unspecified or client.is_multicast:
                    raise ValueError('IP de cliente inválida.')
            except ValueError:
                return self.responder(start_response, 403, {'error': 'Revisa la cabecera de cliente configurada en IIS.'})
            environ = dict(environ)
            environ['ary.proxy_peer'] = str(peer)
            environ['REMOTE_ADDR'] = str(client)
            environ['wsgi.url_scheme'] = 'https'
        elif not peer.is_loopback:
            return self.responder(start_response, 403, {'error': 'El modo local solo admite loopback.'})
        return self.app(environ, start_response)
