import json

import httpx
import pytest

from arybot.groq_service import ask, validate_reply, GroqError, ENDPOINT


def mock_client(data, *, status=200, check=None):
    def handler(req):
        assert str(req.url) == ENDPOINT
        if check:
            check(req)
        if status != 200:
            return httpx.Response(status, json=data)
        return httpx.Response(200, json={
            "id": "mock", "object": "chat.completion", "created": 1,
            "model": "openai/gpt-oss-20b",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": data}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        })
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_connection():
    def check(req):
        assert req.headers["authorization"] == "Bearer gsk_fake_for_unit_testing"
        payload = json.loads(req.content)
        assert payload["model"] == "openai/gpt-oss-20b"
        assert "response_format" not in payload
        assert payload["messages"] == [{"role": "user", "content": "Responde solamente OK"}]
    reply = ask("conexion", api_key="gsk_fake_for_unit_testing", http_client=mock_client("OK", check=check))
    assert reply.data["estado"] == "ARY CONECTADO"
    assert reply.prompt_tokens + reply.completion_tokens == 18


def test_connection_does_not_fake_success():
    with pytest.raises(GroqError, match="no confirmó"):
        ask("conexion", api_key="fake", http_client=mock_client("No"))


def test_ticket():
    def check(req):
        payload = json.loads(req.content)
        assert payload["response_format"]["json_schema"]["strict"] is True
    content = json.dumps({"folio": "00012345", "reimpresion": "SI", "observacion": "Texto explícito"})
    assert ask("ticket", "FOLIO: 00012345 REIMPRESIÓN", api_key="fake",
               http_client=mock_client(content, check=check)).data["folio"] == "00012345"
    with pytest.raises(GroqError):
        validate_reply("ticket", {"folio": "abc", "reimpresion": "SI", "observacion": ""})
    with pytest.raises(GroqError):
        ask("ticket", "cliente@example.com", api_key="fake")
    with pytest.raises(GroqError):
        ask("ticket", "RFC: AAA010101AAA", api_key="fake")


def test_http_failure_does_not_expose_key():
    with pytest.raises(GroqError, match="Clave rechazada") as exc:
        ask("conexion", api_key="fake", http_client=mock_client(
            {"error": {"message": "fake secret"}}, status=401))
    assert "fake secret" not in str(exc.value)


@pytest.mark.parametrize("code,expected", [
    ("model_permission_blocked_org", "Organization"),
    ("model_permission_blocked_project", "Project"),
    ("model_terms_required", "Model Terms"),
    ("unrecognized_fake_secret", "código de permiso reconocido"),
])
def test_http_403_safe_diagnostics(code, expected):
    with pytest.raises(GroqError) as exc:
        ask("conexion", api_key="fake", http_client=mock_client(
            {"error": {"code": code, "message": "fake secret"}}, status=403))
    assert "HTTP 403" in str(exc.value)
    assert expected in str(exc.value)
    assert "fake secret" not in str(exc.value)
    assert "unrecognized_fake_secret" not in str(exc.value)
