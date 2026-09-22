"""Transport failure tests: no retry sleeps or leaked provider bodies."""

import httpx
import pytest

from jev_ultrafast import model


@pytest.mark.parametrize("status", [401, 429, 500, 503, 529])
def test_errors_do_not_retry_or_echo_body(monkeypatch, status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, text="sensitive-provider-body")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(model, "CLIENT", client)
        with pytest.raises(RuntimeError, match=f"HTTP {status}") as error:
            model.post_json("https://example.test", "secret-key", {})
    assert len(calls) == 1
    assert "secret" not in str(error.value)
    assert "sensitive" not in str(error.value)


def test_timeout_is_sanitized(monkeypatch):
    def respond(request):
        raise httpx.ReadTimeout("secret-key", request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(model, "CLIENT", client)
        with pytest.raises(RuntimeError, match="connection failed") as error:
            model.post_json("https://example.test", "secret-key", {})
    assert "secret" not in str(error.value)


def test_invalid_json_is_sanitized(monkeypatch):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text="private"))) as client:
        monkeypatch.setattr(model, "CLIENT", client)
        with pytest.raises(ValueError, match="invalid JSON"):
            model.post_json("https://example.test", "secret-key", {})
