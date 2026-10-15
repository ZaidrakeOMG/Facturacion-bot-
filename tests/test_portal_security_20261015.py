from __future__ import annotations

from io import BytesIO
import json

import pytest

from arybot import portal_modelo
from arybot.constancia_fiscal import ConstanciaError, _validar_pdf_pasivo
from arybot.portal_http import HttpError, PortalWSGI
from arybot.portal_red import PortalRedWSGI


def portal_stub():
    app = object.__new__(PortalWSGI)
    app.origins = {'https://www.grupoary.com.mx'}
    app._session = lambda environ: {'sid': 'a' * 32}
    app._csrf = lambda session: 'csrf-test'
    return app


def test_fetch_metadata_bloquea_cross_site_y_navegacion():
    app = portal_stub()
    with pytest.raises(HttpError) as exc:
        app._guard({'HTTP_SEC_FETCH_SITE': 'cross-site'}, post=False)
    assert exc.value.code == 403

    with pytest.raises(HttpError) as exc:
        app._guard({'HTTP_SEC_FETCH_SITE': 'same-origin', 'HTTP_SEC_FETCH_DEST': 'document'}, post=False)
    assert exc.value.code == 403


def test_post_exige_origen_modo_y_csrf_pero_admite_navegadores_sin_fetch_metadata():
    app = portal_stub()
    env = {
        'HTTP_ORIGIN': 'https://www.grupoary.com.mx',
        'HTTP_SEC_FETCH_SITE': 'same-origin',
        'HTTP_SEC_FETCH_DEST': 'empty',
        'HTTP_SEC_FETCH_MODE': 'cors',
        'HTTP_X_ARY_CSRF': 'csrf-test',
    }
    assert app._guard(env, post=True)['sid'] == 'a' * 32

    legacy = {
        'HTTP_ORIGIN': 'https://www.grupoary.com.mx',
        'HTTP_X_ARY_CSRF': 'csrf-test',
    }
    assert app._guard(legacy, post=True)['sid'] == 'a' * 32


def test_body_json_rechaza_incompleto_y_demasiadas_claves():
    app = portal_stub()

    payload = json.dumps({str(i): i for i in range(17)}).encode()
    env = {
        'CONTENT_TYPE': 'application/json',
        'CONTENT_LENGTH': str(len(payload)),
        'wsgi.input': BytesIO(payload),
    }
    with pytest.raises(HttpError) as exc:
        app._body(env)
    assert exc.value.code == 400

    payload = b'{"tipo":"FACTURA"}'
    env = {
        'CONTENT_TYPE': 'application/json',
        'CONTENT_LENGTH': str(len(payload) + 5),
        'wsgi.input': BytesIO(payload),
    }
    with pytest.raises(HttpError) as exc:
        app._body(env)
    assert exc.value.code == 400


def test_campos_desconocidos_se_rechazan_antes_del_pdf(monkeypatch):
    called = {'pdf': False}

    def no_debe_leer(*args, **kwargs):
        called['pdf'] = True
        raise AssertionError('No debe parsear el PDF')

    monkeypatch.setattr(portal_modelo, 'leer_constancia_pdf_base64', no_debe_leer)
    with pytest.raises(portal_modelo.EntradaPortalError):
        portal_modelo.validar_entrada({'tipo': 'FACTURA', 'campo_inventado': 'x'})
    assert called['pdf'] is False


class Ref:
    def __init__(self, value):
        self.value = value

    def get_object(self):
        return self.value


class FakePdf:
    def __init__(self, root=None, size=100):
        self.trailer = {'/Size': size}
        self.root_object = root or {}


def test_pdf_pasivo_rechaza_js_adjuntos_acciones_y_complejidad():
    _validar_pdf_pasivo(FakePdf())

    with pytest.raises(ConstanciaError):
        _validar_pdf_pasivo(FakePdf({'/Names': Ref({'/JavaScript': {}})}))

    with pytest.raises(ConstanciaError):
        _validar_pdf_pasivo(FakePdf({'/Names': Ref({'/EmbeddedFiles': {}})}))

    with pytest.raises(ConstanciaError):
        _validar_pdf_pasivo(FakePdf({'/OpenAction': Ref({'/S': '/Launch'})}))

    with pytest.raises(ConstanciaError):
        _validar_pdf_pasivo(FakePdf({'/AA': {}}))

    with pytest.raises(ConstanciaError):
        _validar_pdf_pasivo(FakePdf(size=5001))


def test_json_responses_llevan_csp_no_store_y_corp():
    app = object.__new__(PortalWSGI)
    app._dispatch = lambda environ: (200, {'ok': True}, 'application/json; charset=utf-8', [])
    captured = {}

    def start_response(status, headers):
        captured['status'] = status
        captured['headers'] = dict(headers)

    body = b''.join(app({}, start_response))
    assert json.loads(body) == {'ok': True}
    headers = captured['headers']
    assert headers['Cache-Control'].startswith('no-store')
    assert headers['Cross-Origin-Resource-Policy'] == 'same-origin'
    assert "default-src 'none'" in headers['Content-Security-Policy']
    assert headers['X-XSS-Protection'] == '0'


def test_capa_red_tambien_responde_con_cabeceras_defensivas():
    captured = {}

    def start_response(status, headers):
        captured['status'] = status
        captured['headers'] = dict(headers)

    body = b''.join(PortalRedWSGI.responder(start_response, 403, {'error': 'bloqueado'}))
    assert json.loads(body)['error'] == 'bloqueado'
    headers = captured['headers']
    assert headers['Cache-Control'].startswith('no-store')
    assert headers['Cross-Origin-Resource-Policy'] == 'same-origin'
    assert "default-src 'none'" in headers['Content-Security-Policy']
