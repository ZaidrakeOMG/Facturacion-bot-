"""Aplicación WSGI del portal: HTTP -> validación -> MISMA ColaOperaciones.

La recepción HTTP nunca llama invoice(), preparar() o aceptar(). Reutiliza el
coordinador creado por la GUI; no crea otro consumidor ni recupera otra cola.
En producción se sirve con Waitress, no con el servidor de prueba de Python.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
from http import HTTPStatus
from http.cookies import SimpleCookie, CookieError
import ipaddress
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit
from .cola_modelo import SolicitudDuplicada
from .portal_modelo import validar_entrada, catalogos, estado_publico, EntradaPortalError

PREFIX = '/api/facturacion'
COOKIE_NAME = 'ary_portal_session'
MAX_BODY = 16 * 1024
DEFAULTS = {
    'puerto': 8765,
    'escuchar_en': '127.0.0.1',
    'origenes_permitidos': ['http://127.0.0.1:8765', 'http://localhost:8765'],
    'https_publico': False,
    'proxy_confiable': '',
    'permitir_facturacion_real': False,
    'max_solicitudes_pendientes': 100,
    'limite_envios_10_minutos': 8,
}


class HttpError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


class Limitador:
    """Límites por IP; memoria acotada. No sustituye la protección del proxy."""
    def __init__(self):
        self.data = {}
        self.lock = threading.Lock()

    def aceptar(self, key, amount, seconds):
        now = time.monotonic()
        with self.lock:
            if len(self.data) >= 4096:
                self.data = {k: v for k, v in self.data.items() if v[0] > now}
                if len(self.data) >= 4096 and key not in self.data:
                    return False
            expiry, count = self.data.get(key, (now + seconds, 0))
            if expiry <= now:
                expiry, count = now + seconds, 0
            self.data[key] = (expiry, count + 1)
            return count < amount


def cargar_ajustes(base):
    path = Path(base) / 'portal_config.json'
    raw = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValueError('Revisa las claves de portal_config.json.')
    cfg = dict(DEFAULTS, **raw)
    for key, low, high in [('puerto', 1024, 65535), ('max_solicitudes_pendientes', 1, 2000),
                           ('limite_envios_10_minutos', 1, 100)]:
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise ValueError('Valor inválido en portal_config.json: ' + key)
    for key in ['https_publico', 'permitir_facturacion_real']:
        if type(cfg[key]) is not bool:
            raise ValueError(key + ' debe ser true o false.')
    origins = cfg['origenes_permitidos']
    if not isinstance(origins, list) or not origins or len(origins) > 10:
        raise ValueError('Configura entre 1 y 10 orígenes exactos, sin rutas ni comodines.')
    for origin in origins:
        if not isinstance(origin, str):
            raise ValueError('Cada origen debe ser texto.')
        u = urlsplit(origin)
        if (u.scheme not in {'http', 'https'} or not u.hostname or u.path or u.query
                or u.fragment or u.username or u.password or '*' in origin):
            raise ValueError('Origen inválido: usa solo esquema y dominio, sin diagonal final.')
        try:
            local = u.hostname == 'localhost' or ipaddress.ip_address(u.hostname).is_loopback
        except ValueError:
            local = False
        if not local and (u.scheme != 'https' or not cfg['https_publico']):
            raise ValueError('Los dominios públicos requieren HTTPS y https_publico=true.')
        if cfg['https_publico'] and u.scheme != 'https':
            raise ValueError('En modo publicado todos los orígenes deben usar HTTPS.')
    from .portal_red import validar_red
    validar_red(cfg)
    return cfg


def cargar_secreto(base):
    path = Path(base) / 'data' / 'portal_secret.key'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as f:
            f.write(secrets.token_bytes(48))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except FileExistsError:
        pass
    secret = path.read_bytes()
    if len(secret) < 32:
        raise ValueError('La clave local del portal es inválida. No publiques el servicio.')
    return secret


class PortalWSGI:
    def __init__(self, cola, bot_cfg, settings, base, *, secret=None, clock=time.time):
        self.cola = cola
        self.bot_cfg = bot_cfg
        self.settings = settings
        self.base = Path(base)
        self.clock = clock
        self.secret = secret or cargar_secreto(base)
        self.limits = Limitador()
        self.lock = threading.RLock()
        self.aceptando = True
        self.origins = set(settings['origenes_permitidos'])

    def modo_seguro(self):
        return not (self.settings['permitir_facturacion_real'] is True
                    and self.bot_cfg.get('app', {}).get('modo_prueba', True) is False)

    def pausar_recepcion(self):
        with self.lock:
            self.aceptando = False

    def _mac(self, purpose, text):
        return hmac.new(self.secret, (purpose + ':' + text).encode('utf-8'), hashlib.sha256).hexdigest()

    def _sign(self, purpose, data):
        raw = base64.urlsafe_b64encode(json.dumps(data, separators=(',', ':')).encode()).decode().rstrip('=')
        return raw + '.' + self._mac(purpose, raw)

    def _read_token(self, purpose, value, lifetime):
        try:
            if not isinstance(value, str) or len(value) > 1024:
                return None
            raw, mac = value.rsplit('.', 1)
            if not hmac.compare_digest(mac, self._mac(purpose, raw)):
                return None
            data = json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
            age = self.clock() - data['iat']
            if not 0 <= age <= lifetime:
                return None
            return data
        except (ValueError, TypeError, KeyError, UnicodeError):
            return None

    def _session(self, environ):
        try:
            cookie = SimpleCookie()
            raw = environ.get('HTTP_COOKIE', '')
            if len(raw) > 4096:
                return None
            cookie.load(raw)
            value = cookie.get(COOKIE_NAME)
            data = self._read_token('session', value.value if value else '', 86400)
            return data if data and re.fullmatch('[0-9a-f]{32}', data.get('sid', '')) else None
        except (CookieError, TypeError):
            return None

    def _csrf(self, session):
        return self._mac('csrf', session['sid'])

    def _body(self, environ):
        if environ.get('CONTENT_TYPE', '').split(';')[0].strip().lower() != 'application/json':
            raise HttpError(415, 'El formulario debe enviarse como JSON.')
        try:
            length = int(environ.get('CONTENT_LENGTH', '0'))
        except ValueError:
            raise HttpError(400, 'Longitud de solicitud inválida.')
        if not 0 < length <= MAX_BODY:
            raise HttpError(413 if length > MAX_BODY else 400, 'Solicitud vacía o demasiado grande.')
        try:
            body = json.loads(environ['wsgi.input'].read(length).decode('utf-8'))
        except (ValueError, UnicodeError):
            raise HttpError(400, 'No se pudo leer la solicitud.')
        if not isinstance(body, dict):
            raise HttpError(400, 'Formato de solicitud inválido.')
        return body

    def _guard(self, environ, *, post=False):
        origin = environ.get('HTTP_ORIGIN', '')
        if origin and origin not in self.origins:
            raise HttpError(403, 'Este origen no está autorizado para el portal.')
        if environ.get('HTTP_SEC_FETCH_SITE') == 'cross-site':
            raise HttpError(403, 'Abre el formulario desde el sitio autorizado de ARY.')
        if post and origin not in self.origins:
            raise HttpError(403, 'Abre el formulario desde el sitio autorizado de ARY.')
        if post:
            session = self._session(environ)
            supplied = environ.get('HTTP_X_ARY_CSRF', '')
            if not session or not hmac.compare_digest(supplied.encode('utf-8'), self._csrf(session).encode('ascii')):
                raise HttpError(403, 'La sesión venció. Recarga la página antes de enviar.')
            return session
        return None

    def _response_job(self, job):
        result = estado_publico(job)
        result['seguimiento'] = self._sign('tracking', {'id': job['id'], 'iat': int(self.clock())})
        return result

    def _dispatch(self, environ):
        method = environ.get('REQUEST_METHOD', 'GET').upper()
        path = environ.get('PATH_INFO', '/')
        headers = []
        if path in ('/', '/ary_facturacion.htm') and method == 'GET':
            page = self.base / 'portal_web' / 'ary_facturacion.htm'
            if not page.is_file():
                raise HttpError(503, 'Falta portal_web/ary_facturacion.htm en la carpeta del bot.')
            return 200, page.read_bytes(), 'text/html; charset=utf-8', headers
        if not path.startswith(PREFIX + '/'):
            raise HttpError(404, 'Ruta no disponible.')
        self._guard(environ, post=(method == 'POST'))
        ip = environ.get('REMOTE_ADDR', 'local')
        if not self.limits.aceptar(('general', ip), 180, 60):
            raise HttpError(429, 'Hay demasiadas consultas. Inténtalo nuevamente más tarde.')
        if path == PREFIX + '/opciones' and method == 'GET':
            session = self._session(environ)
            if not session:
                session = {'sid': secrets.token_hex(16), 'iat': int(self.clock())}
                cookie = COOKIE_NAME + '=' + self._sign('session', session) + '; Path=/api/facturacion; HttpOnly; SameSite=Strict; Max-Age=86400'
                if self.settings['https_publico']:
                    cookie += '; Secure'
                headers.append(('Set-Cookie', cookie))
            data = dict(catalogos(), csrf=self._csrf(session), modo_seguro=self.modo_seguro(),
                        aceptando=self.aceptando)
            return 200, data, 'application/json; charset=utf-8', headers
        if method != 'POST':
            raise HttpError(405, 'Método no permitido.')
        session = self._session(environ)
        body = self._body(environ)
        if path == PREFIX + '/recepcion':
            request_id = body.get('request_id', '')
            if not isinstance(request_id, str) or not re.fullmatch('[0-9a-f]{32}', request_id):
                raise HttpError(400, 'Identificador inválido.')
            source_id = 'web:' + self._mac('request', session['sid'] + ':' + request_id)
            with self.lock:
                job = self.cola.store.source(source_id)
            if not job:
                raise HttpError(404, 'No se encontró una solicitud registrada para este envío.')
            return 200, self._response_job(job), 'application/json; charset=utf-8', headers
        if path == PREFIX + '/estado':
            token = self._read_token('tracking', body.get('seguimiento', ''), 7 * 86400)
            if not token or not re.fullmatch('[0-9a-f]{32}', str(token.get('id', ''))):
                raise HttpError(404, 'El seguimiento no es válido o venció.')
            job = self.cola.store.get(token['id'])
            if not job or job['origen'] != 'Portal web':
                raise HttpError(404, 'Seguimiento no disponible.')
            return 200, estado_publico(job), 'application/json; charset=utf-8', headers
        if path != PREFIX + '/solicitudes':
            raise HttpError(404, 'Ruta no disponible.')
        request_id = environ.get('HTTP_X_ARY_REQUEST_ID', '')
        if not re.fullmatch('[0-9a-f]{32}', request_id):
            raise HttpError(400, 'Falta el identificador único del formulario.')
        kind, sol, data = validar_entrada(body)
        source_id = 'web:' + self._mac('request', session['sid'] + ':' + request_id)
        with self.lock:
            # Primero recuperar un envío de resultado incierto; no se repite en Polaris.
            old = self.cola.store.source(source_id)
            if old:
                if old['tipo'] != kind or old['datos'] != data:
                    raise HttpError(409, 'Esta solicitud ya se envió con otros datos. Revisa su seguimiento antes de iniciar otra.')
                return 200, self._response_job(old), 'application/json; charset=utf-8', headers
            expected_mode = environ.get('HTTP_X_ARY_MODE', '')
            actual_mode = 'seguro' if self.modo_seguro() else 'real'
            if expected_mode != actual_mode:
                raise HttpError(409, 'El modo de operación cambió o no se confirmó. Recarga la página y revisa si es prueba o trámite real antes de enviar.')
            if not self.aceptando:
                raise HttpError(503, 'La recepción está pausada. Los datos no se enviaron al bot.')
            if not self.limits.aceptar(('envios', ip), self.settings['limite_envios_10_minutos'], 600):
                raise HttpError(429, 'Se alcanzó el límite de solicitudes de esta conexión. Inténtalo más tarde.')
            counts = self.cola.store.status()['conteos']
            pending = sum(counts.get(k, 0) for k in ['EN_COLA', 'EJECUTANDO', 'PREPARADA_FACTURA',
                           'PREPARADA_ALTA', 'ACEPTAR_ALTA', 'PRUEBA_PENDIENTE'])
            if pending >= self.settings['max_solicitudes_pendientes']:
                raise HttpError(503, 'La cola está llena. No se registró esta solicitud.')
            safe = self.modo_seguro()
            if kind == 'ALTA':
                job, _ = self.cola.encolar_alta(sol, source='Portal web', source_id=source_id, safe=safe)
            else:
                job, _ = self.cola.encolar_factura(sol, source='Portal web', source_id=source_id, safe=safe)
        return 201, self._response_job(job), 'application/json; charset=utf-8', headers

    def __call__(self, environ, start_response):
        headers = []
        try:
            code, data, content_type, headers = self._dispatch(environ)
        except EntradaPortalError as exc:
            code, data, content_type = 400, {'error': str(exc)}, 'application/json; charset=utf-8'
        except SolicitudDuplicada:
            code, data, content_type = 409, {'error': 'Hay una solicitud ACTIVA para ese ticket o RFC en la estación. Espera su resultado o el cierre de la prueba. No significa que ya esté facturado.'}, 'application/json; charset=utf-8'
        except HttpError as exc:
            code, data, content_type = exc.code, {'error': exc.message}, 'application/json; charset=utf-8'
        except Exception:
            # No divulgar consultas, rutas del equipo, datos personales ni errores internos.
            code, data, content_type = 500, {'error': 'No se confirmó el resultado. Reintenta desde este mismo formulario sin cambiar los datos; se conserva su identificador para evitar duplicados.'}, 'application/json; charset=utf-8'
            try:
                self.cola.log('Portal: resultado HTTP no confirmado. Revise Cola y avisos; no repita Polaris manualmente.')
            except Exception:
                pass
        payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode('utf-8')
        headers += [('Content-Type', content_type), ('Content-Length', str(len(payload))),
                    ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
                    ('Referrer-Policy', 'no-referrer'), ('X-Frame-Options', 'DENY'),
                    ('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')]
        start_response(str(code) + ' ' + HTTPStatus(code).phrase, headers)
        return [payload]
