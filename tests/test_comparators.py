"""Offline contracts for isolated comparison arms and explicit Agent injection."""

import json
import os
from unittest.mock import Mock

import httpx
import pytest

from jev_ultrafast import agent, comparators, model

KEYS = {
    "TYPESAFE_API_KEY": "offline-typesafe",
    "OPENROUTER_API_KEY": "offline-router",
    "CEREBRAS_API_KEY": "offline-cerebras",
}


@pytest.fixture
def page():
    return {
        "url": "https://example.test/",
        "title": "Form",
        "text": "Complete form",
        "fingerprint": "unchanged",
        "actions": [
            {"id": "title", "node": 1, "kind": "fill", "label": "Title", "value": ""},
            {"id": "submit", "node": 2, "kind": "click", "label": "Submit"},
            {"id": "option", "node": 3, "kind": "select", "label": "Category → Travel", "value": "travel"},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }


def chat(operation="CLICK", choice="submit", text=None):
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "operation": operation,
                            "choice": choice,
                            "text": text,
                        }
                    )
                }
            }
        ],
        "usage": {"total_tokens": 31},
    }


def jev(body, operation="CLICK"):
    def answer(criteria, choice):
        return {"choice": choice, "confidence": 1, "probabilities": {k: float(k == choice) for k in criteria}}

    questions = body["questions"]
    answers = {"operation": answer(questions["operation"]["criteria"], operation)}
    head = operation.lower() + "_target"
    if head in questions:
        criteria = questions[head]["criteria"]
        answers[head] = answer(criteria, next(iter(criteria)))
    return {"answers": answers, "usage": {"total_tokens": 17}}


def client_for(payload, *, status=200):
    def respond(request):
        value = payload(json.loads(request.content)) if callable(payload) else payload
        return httpx.Response(status, json=value)

    return httpx.Client(transport=httpx.MockTransport(respond))


@pytest.mark.parametrize("name", comparators.PROFILE_NAMES)
def test_profiles_pinned_and_do_not_mutate_environment(page, name):
    before = dict(os.environ)
    paired = name.startswith("jev_") or name == "jev"
    payload = jev if paired else chat()
    with client_for(payload) as client:
        profile = comparators.get_profile(name, environ=KEYS, client=client)
        assert profile.preflight() is profile
        result = profile.choose(page, "Submit", [])
        (call,) = result["routing"]["model_calls"]
        expected = (
            model.TYPESAFE_OPENROUTER_MODEL
            if paired else comparators.CHAT_MODELS[name]
        )
        assert call["model"] == result["model"] == expected
        expected_provider = "cerebras" if name == "qwen" else "openrouter"
        assert call["provider"] == expected_provider
        assert call["success"] and call["role"] == "decision"
        assert result["routing"]["fallback"] is False
        assert result["choice"] == "submit"
        assert "offline" not in repr(profile.decision)
        profile.close()
        assert not client.is_closed
    assert dict(os.environ) == before


@pytest.mark.parametrize(
    "name,key",
    [
        ("qwen", "CEREBRAS_API_KEY"),
        ("astra", "OPENROUTER_API_KEY"),
        ("opus", "OPENROUTER_API_KEY"),
        ("jev_qwen", "CEREBRAS_API_KEY"),
        ("jev_qwen", "OPENROUTER_API_KEY"),
        ("jev_qwen_openrouter_fast", "OPENROUTER_API_KEY"),
        ("jev_astra", "OPENROUTER_API_KEY"),
        ("jev_opus", "OPENROUTER_API_KEY"),
        ("jev", "OPENROUTER_API_KEY"),
    ],
)
def test_missing_keys_fail_before_client_creation(monkeypatch, name, key):
    factory = Mock(side_effect=AssertionError("No client should be created"))
    monkeypatch.setattr(comparators.httpx, "Client", factory)
    with pytest.raises(ValueError, match=key):
        comparators.get_profile(name, environ={k: v for k, v in KEYS.items() if k != key})
    factory.assert_not_called()


@pytest.mark.parametrize(
    "name,key",
    [
        ("qwen", "CEREBRAS_API_KEY"),
        ("astra", "OPENROUTER_API_KEY"),
        ("opus", "OPENROUTER_API_KEY"),
        ("jev", "OPENROUTER_API_KEY"),
    ],
)
def test_only_required_key_is_read(name, key):
    with client_for({}) as client:
        profile = comparators.get_profile(name, environ={key: KEYS[key]}, client=client)
        assert profile.decision.key == KEYS[key]
    with pytest.raises(ValueError):
        comparators.get_profile(name, environ={"TEXT_MODEL_API_KEY": "wrong", "TYPESAFE_API_KEY": "wrong"})


@pytest.mark.parametrize("name", ["qwen", "astra", "opus"])
def test_chat_transport_reasoning_and_exact_provider_credential(page, name):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=chat("TYPE_TEXT", "title", "Trip"))

    with httpx.Client(transport=httpx.MockTransport(respond), timeout=httpx.Timeout(19, connect=3)) as client:
        result = comparators.get_profile(name, environ=KEYS, client=client).choose(page, "Write Trip", [])
    (request,) = requests
    body = json.loads(request.content)
    is_qwen = name == "qwen"
    assert str(request.url) == (
        model.CEREBRAS_BASE_URL + "/chat/completions" if is_qwen else comparators.OPENROUTER_CHAT_URL
    )
    assert request.headers["Authorization"] == "Bearer " + KEYS["CEREBRAS_API_KEY" if is_qwen else "OPENROUTER_API_KEY"]
    assert body.get("reasoning_effort") == ("none" if is_qwen else None)
    assert body.get("reasoning") == (None if is_qwen else {"effort": "low"})
    assert request.extensions["timeout"]["read"] == 19
    assert request.extensions["timeout"]["connect"] == 3
    assert body["response_format"]["json_schema"]["strict"]
    assert result["inline_text"] == "Trip"


def test_openrouter_fast_requests_throughput_priority_without_cerebras_pin(page):
    requests = []

    def respond(request):
        requests.append(request)
        body = json.loads(request.content)
        response = (
            jev(body, "TYPE_TEXT")
            if "questions" in body
            else {"choices": [{"message": {"content": '{"text":"Trip"}'}}]}
        )
        response["usage"] = {
            "total_tokens": 31,
            "private": "provider detail",
            "negative": -1,
            "prompt_tokens_details": {"cached_tokens": 3, "private": "detail"},
        }
        return httpx.Response(200, json=response)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        profile = comparators.get_profile(
            "jev_qwen_openrouter_fast",
            environ={"OPENROUTER_API_KEY": "router-only", "CEREBRAS_API_KEY": "optional-only"},
            client=client,
        )
        result = profile.choose(page, "Write Trip", [])
        value, metadata = profile.field_text({"goal": "Write Trip"})

    request = requests[1]
    body = json.loads(request.content)
    assert str(request.url) == comparators.OPENROUTER_CHAT_URL
    assert request.headers["Authorization"] == "Bearer router-only"
    assert body["provider"] == {"sort": "throughput", "require_parameters": True}
    assert result["provider"] == "openrouter"
    assert value == "Trip"
    assert metadata["usage"] == {"total_tokens": 31, "prompt_tokens_details": {"cached_tokens": 3}}
    assert profile.writer.provider == "openrouter"
    assert profile.writer.throughput_priority is True


@pytest.mark.parametrize(
    "operation,choice,text",
    [
        ("CLICK", "unknown", None),
        ("CLICK", "title", None),
        ("TYPE_TEXT", "submit", "x"),
        ("CLICK", "submit", "code"),
        ("TYPE_TEXT", "title", ""),
        ("TYPE_TEXT", "title", " " * 2),
        ("TYPE_TEXT", "title", "x" * 2001),
        ("TYPE_TEXT", "title", None),
        ("DONE", "submit", None),
        ("WAIT", "submit", None),
        ([], "submit", None),
        ("CLICK", {}, None),
    ],
)
def test_invalid_chat_targets_and_text_keep_paid_accounting(page, operation, choice, text):
    with client_for(chat(operation, choice, text)) as client:
        profile = comparators.get_profile("astra", environ=KEYS, client=client)
        with pytest.raises(model.InvalidModelResponse) as error:
            profile.choose(page, "Submit", [])
    (call,) = error.value.routing["model_calls"]
    assert call["success"] is False
    assert call["usage"] == {"total_tokens": 31}
    assert call["provider"] == "openrouter"
    assert call["model"] == "openai/gpt-6-astra"


@pytest.mark.parametrize(
    "operation,choice,target",
    [
        ("SELECT", "option", "3:1"),
        ("WAIT", "wait", None),
        ("DONE", "DONE", None),
        ("BLOCKED", "BLOCKED", None),
    ],
)
def test_chat_action_mapping(page, operation, choice, target):
    with client_for(chat(operation, choice)) as client:
        result = comparators.get_profile("opus", environ=KEYS, client=client).choose(page, "Complete", [])
    assert result["target"] == target


@pytest.mark.parametrize("name", ["jev_qwen", "jev_astra", "jev_opus", "jev_qwen_openrouter_fast"])
def test_pair_only_uses_jev_then_selected_writer(page, name):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append((request, body))
        return httpx.Response(
            200,
            json=jev(body, "TYPE_TEXT")
            if "questions" in body
            else {
                "choices": [{"message": {"content": '{"text":"Trip"}'}}],
                "usage": {"total_tokens": 9},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        profile = comparators.get_profile(name, environ=KEYS, client=client)
        decision = profile.choose(page, "Write Trip", [])
        assert "inline_text" not in decision
        value, metadata = profile.field_text({"goal": "Write Trip"})
    assert len(requests) == 2
    assert requests[0][1]["model"] == model.TYPESAFE_OPENROUTER_MODEL
    jev_url = model.TYPESAFE_OPENROUTER_URL
    assert str(requests[0][0].url) == jev_url
    assert requests[0][0].headers["Authorization"] == "Bearer " + KEYS[
        "OPENROUTER_API_KEY"
    ]
    assert requests[1][1]["model"] == comparators.CHAT_MODELS[name[4:]]
    assert str(requests[1][0].url) == (
        model.CEREBRAS_BASE_URL + "/chat/completions" if name == "jev_qwen" else comparators.OPENROUTER_CHAT_URL
    )
    assert value == "Trip" and metadata["role"] == "field_text"
    assert metadata["usage"] == {"total_tokens": 9}
    assert metadata["provider"] == ("cerebras" if name == "jev_qwen" else "openrouter")


def test_pure_jev_has_no_writer_and_spends_nothing_on_text(page):
    with client_for(lambda _: pytest.fail("No text request permitted")) as client:
        profile = comparators.get_profile("jev", environ={"OPENROUTER_API_KEY": "offline"}, client=client)
        with pytest.raises(ValueError, match="TYPE_TEXT unsupported") as error:
            profile.field_text({"goal": "Write"})
        assert not getattr(error.value, "model_call", None)


def test_field_writer_trims_outer_whitespace_from_exact_value():
    response = {"choices": [{"message": {"content": '{"text":"\\nMarcella\\n"}'}}]}
    with client_for(response) as client:
        profile = comparators.get_profile("jev_qwen_openrouter", environ=KEYS, client=client)
        value, _ = profile.field_text({"goal": 'Enter "Marcella" into the text field.'})
    assert value == "Marcella"


@pytest.mark.parametrize("name", ["jev", "jev_qwen", "jev_astra", "jev_opus"])
def test_jev_invalid_selected_target_never_falls_back(page, name):
    calls = []

    def payload(body):
        calls.append(body)
        result = jev(body)
        result["answers"]["click_target"]["choice"] = "999"
        return result

    with client_for(payload) as client:
        with pytest.raises(model.InvalidModelResponse) as error:
            comparators.get_profile(name, environ=KEYS, client=client).choose(page, "Submit", [])
    assert len(calls) == 1
    assert error.value.model_call["usage"] == {"total_tokens": 17}
    assert not error.value.routing["fallback"]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"choices": []},
        {"choices": [{"message": {"content": '{"text":"x","extra":true}'}}]},
        {"choices": [{"message": {"content": '{"text":" "}'}}]},
    ],
)
def test_writer_rejection_preserves_usage(payload):
    if isinstance(payload, dict):
        payload["usage"] = {"total_tokens": 11}
    with client_for(payload) as client:
        with pytest.raises(model.InvalidModelResponse) as error:
            comparators.get_profile("jev_opus", environ=KEYS, client=client).field_text({})
    assert not error.value.model_call["success"]
    assert error.value.model_call["usage"] == ({"total_tokens": 11} if isinstance(payload, dict) else {})


def test_http_error_sanitized_and_preserves_usage(page):
    with client_for({"error": "private provider payload", "usage": {"total_tokens": 9}}, status=429) as client:
        with pytest.raises(model.InvalidModelResponse) as error:
            comparators.get_profile("astra", environ=KEYS, client=client).choose(page, "Submit", [])
    assert "private" not in str(error.value)
    assert error.value.model_call["http_status"] == 429
    assert error.value.model_call["usage"] == {"total_tokens": 9}


def test_transport_failure_sanitized_and_not_retried(page):
    requests = []

    def fail(request):
        requests.append(request)
        raise httpx.ReadTimeout("private-token", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(model.InvalidModelResponse) as error:
            comparators.get_profile("jev_astra", environ=KEYS, client=client).choose(page, "Submit", [])
    assert len(requests) == 1
    assert "private-token" not in str(error.value)
    assert error.value.model_call["success"] is False


def test_explicit_agent_injection_and_failed_writer_accounting(page, monkeypatch):
    browser = Mock(observe=Mock(return_value=page), fresh=Mock(return_value=True))
    monkeypatch.setattr(agent, "Browser", Mock(return_value=browser))
    with client_for(
        lambda body: jev(body, "TYPE_TEXT") if "questions" in body else {"usage": {"total_tokens": 7}}
    ) as client:
        profile = comparators.get_profile("jev_qwen", environ=KEYS, client=client)
        with agent.Agent(page["url"], "Write Trip", decision_fn=profile.choose, text_fn=profile.field_text) as runner:
            with pytest.raises(model.InvalidModelResponse):
                runner.command("tick")
            assert runner.state["status"] == "blocked"
            assert runner.state["decisions"][0]["routing"]["profile"] == "jev_qwen"
            assert runner.state["text_calls"][0]["usage"] == {"total_tokens": 7}
    browser.act.assert_not_called()


def test_owned_client_closes_and_production_timeout_unchanged():
    before = model.CLIENT.timeout
    with comparators.get_profile("qwen", environ=KEYS) as profile:
        assert profile.client.timeout.read == 30
        assert not profile.client.is_closed
    assert profile.client.is_closed
    assert model.CLIENT.timeout == before


def test_nonfinite_and_unbounded_usage_cannot_break_evidence_json(page):
    response = chat()
    response["usage"] = {
        "total_tokens": 31,
        "bad_nan": float("nan"),
        "bad_inf": float("inf"),
        "huge": 2**1000,
        "details": {"cached_tokens": 3, "error": "private message"},
    }
    # This malformed accounting is injected after HTTP parsing because strict JSON
    # encoders correctly refuse NaN/Infinity before a response can be constructed.
    transport = Mock()
    transport.post.return_value = Mock(status_code=200, is_error=False, json=Mock(return_value=response))
    result = comparators.get_profile("qwen", environ=KEYS, client=transport).choose(page, "Submit", [])
    assert result["usage"] == {"total_tokens": 31, "details": {"cached_tokens": 3}}
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("name", ["qwen_openrouter", "jev_qwen_openrouter"])
def test_openrouter_qwen_needs_only_router_key_and_disables_reasoning(name):
    with client_for({"choices": [{"message": {"content": '{"text":"Example"}'}}]}) as client:
        profile = comparators.get_profile(name, environ={"OPENROUTER_API_KEY": "router-only"}, client=client)
        config = profile.writer or profile.decision
        assert config.provider == "openrouter"
        assert config.model == "qwen/qwen3.8-27b"
        body = profile._writer_body(config, "Write", {}, {}, 1024)
        assert body["reasoning"] == {"enabled": False}
        assert config.key == "router-only"


def test_default_pair_uses_openrouter_jev_and_explicit_direct_route_is_available():
    with client_for({}) as client:
        default = comparators.get_profile(
            "jev_qwen",
            environ={"OPENROUTER_API_KEY": "router-only", "CEREBRAS_API_KEY": "qwen-only"},
            client=client,
        )
        assert default.decision.endpoint == model.TYPESAFE_OPENROUTER_URL
        assert default.decision.key == "router-only"
        assert default.writer.endpoint == model.CEREBRAS_BASE_URL + "/chat/completions"
        assert default.writer.key == "qwen-only"

        direct = comparators.get_profile(
            "jev_qwen",
            environ={"TYPESAFE_API_KEY": "jev-only", "CEREBRAS_API_KEY": "qwen-only"},
            client=client,
            jev_provider="direct",
        )
        assert direct.decision.endpoint == model.TYPESAFE_DIRECT_URL
        assert direct.decision.key == "jev-only"
        assert direct.writer.endpoint == model.CEREBRAS_BASE_URL + "/chat/completions"
        assert direct.writer.key == "qwen-only"

        alternative = comparators.get_profile(
            "jev_qwen_openrouter", environ={"OPENROUTER_API_KEY": "router-only"}, client=client
        )
        assert alternative.decision.endpoint == model.TYPESAFE_OPENROUTER_URL
        assert alternative.writer.endpoint == comparators.OPENROUTER_CHAT_URL
        assert alternative.decision.key == alternative.writer.key == "router-only"


def test_openrouter_jev_prefers_direct_cerebras_writer_when_key_available():
    with client_for({}) as client:
        profile = comparators.get_profile(
            "jev_qwen_openrouter",
            environ={"OPENROUTER_API_KEY": "router-only", "CEREBRAS_API_KEY": "cerebras-only"},
            client=client,
        )
        assert profile.decision.endpoint == model.TYPESAFE_OPENROUTER_URL
        assert profile.writer.endpoint == model.CEREBRAS_BASE_URL + "/chat/completions"
        assert profile.writer.model == "qwen-3.8-27b"
        assert profile.writer.key == "cerebras-only"
