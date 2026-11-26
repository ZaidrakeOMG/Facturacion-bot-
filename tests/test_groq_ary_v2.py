import io
import json
import urllib.error
import pytest

from arybot.groq_service import ask, validate_reply, GroqError, ENDPOINT


class Fake:
    def __init__(self, data):
        self.data = data
        self.request = None

    def open(self, request, timeout):
        self.request = request
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(self.data)}}],
                                     "usage": {"prompt_tokens": 11, "completion_tokens": 7}}).encode())


def test_connection():
    fake = Fake({"estado": "ARY CONECTADO"})
    reply = ask("conexion", api_key="gsk_fake_for_unit_testing", opener=fake)
    assert reply.data["estado"] == "ARY CONECTADO"
    assert reply.prompt_tokens + reply.completion_tokens == 18
    assert fake.request.full_url == ENDPOINT
    payload = json.loads(fake.request.data)
    assert payload["response_format"]["json_schema"]["strict"] is True


def test_ticket():
    fake = Fake({"folio": "00012345", "reimpresion": "SI", "observacion": "Texto explícito"})
    assert ask("ticket", "FOLIO: 00012345 REIMPRESIÓN", api_key="fake", opener=fake).data["folio"] == "00012345"
    with pytest.raises(GroqError):
        validate_reply("ticket", {"folio": "abc", "reimpresion": "SI", "observacion": ""})
    with pytest.raises(GroqError):
        ask("ticket", "cliente@example.com", api_key="fake", opener=fake)
    with pytest.raises(GroqError):
        ask("ticket", "RFC: AAA010101AAA", api_key="fake", opener=fake)


def test_http_failure_does_not_expose_key():
    class Bad:
        def open(self, req, timeout):
            raise urllib.error.HTTPError(ENDPOINT, 401, "fake secret", {}, None)
    with pytest.raises(GroqError, match="Clave rechazada"):
        ask("conexion", api_key="fake secret", opener=Bad())
