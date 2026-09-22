"""Offline provider contracts for the model adapter."""

import json
from unittest.mock import Mock

import pytest

from jev_ultrafast import model


def state():
    return {
        "url": "https://example.test/",
        "title": "Example",
        "text": "Example page",
        "actions": [{"id": "go", "kind": "click", "label": "Go", "role": "button", "node": 1}],
    }


def choice(criteria, selected):
    return {
        "choice": selected,
        "confidence": 1.0,
        "probabilities": {option: float(option == selected) for option in criteria},
    }


def decision_response(body):
    operation = body["questions"]["operation"]["criteria"]
    return {
        "model": body["model"],
        "answers": {
            "operation": choice(operation, "CLICK"),
            "click_target": choice(body["questions"]["click_target"]["criteria"], "1"),
        },
    }


def test_openrouter_typesafe_uses_native_decisions_endpoint_and_pinned_model(monkeypatch):
    monkeypatch.setenv("TYPESAFE_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    monkeypatch.setenv("TYPESAFE_MODEL", "jev-latest")
    post = Mock(side_effect=lambda url, key, body: decision_response(body))
    monkeypatch.setattr(model, "post_json", post)

    result = model.choose(state(), "Click Go", [])

    post.assert_called_once()
    url, key, body = post.call_args.args
    assert url == "https://openrouter.ai/api/alpha/decisions"
    assert key == "openrouter-test-key"
    assert body["model"] == "typesafe/jev-1.13"
    assert all(isinstance(question["instructions"], str) for question in body["questions"].values())
    assert all(
        isinstance(value, str) for question in body["questions"].values() for value in question["criteria"].values()
    )
    assert json.loads(body["questions"]["operation"]["instructions"])["goal"] == "Click Go"
    assert result["choice"] == "go"


def test_missing_openrouter_key_is_rejected_before_request(monkeypatch):
    monkeypatch.setenv("TYPESAFE_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    post = Mock()
    monkeypatch.setattr(model, "post_json", post)

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        model.choose(state(), "Click Go", [])

    post.assert_not_called()


def test_missing_provider_keeps_direct_typesafe_defaults(monkeypatch):
    monkeypatch.delenv("TYPESAFE_PROVIDER", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-test-key")
    monkeypatch.delenv("TYPESAFE_MODEL", raising=False)
    post = Mock(side_effect=lambda url, key, body: decision_response(body))
    monkeypatch.setattr(model, "post_json", post)

    model.choose(state(), "Click Go", [])

    url, key, body = post.call_args.args
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert key == "typesafe-test-key"
    assert body["model"] == "jev-latest"
    assert isinstance(body["questions"]["operation"]["instructions"], dict)
    assert isinstance(body["questions"]["click_target"]["criteria"]["1"], dict)


def test_cerebras_text_helper_uses_reasoning_effort_none(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://api.cerebras.ai/v1")
    monkeypatch.setenv("CEREBRAS_API_KEY", "cerebras-test-key")
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("TEXT_MODEL", "qwen-3.8-27b")
    monkeypatch.setenv("TEXT_MODEL_REASONING", "none")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)

    value, metadata = model.field_text({"goal": "Enter Zurich"})

    assert value == "Zurich"
    assert metadata["model"] == "qwen-3.8-27b"
    url, key, body = post.call_args.args
    assert url == "https://api.cerebras.ai/v1/chat/completions"
    assert key == "cerebras-test-key"
    assert body["reasoning_effort"] == "none"
    assert "reasoning" not in body


def test_existing_openrouter_text_helper_payload_remains_compatible(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "text-test-key")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("TEXT_MODEL", "inception/mercury-2.5")
    monkeypatch.setenv("TEXT_MODEL_REASONING", "none")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)

    model.field_text({"goal": "Enter Zurich"})

    url, key, body = post.call_args.args
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert key == "text-test-key"
    assert body["model"] == "inception/mercury-2.5"
    assert body["reasoning"] == {"enabled": False}
    assert "reasoning_effort" not in body
    assert json.loads(body["messages"][1]["content"])["goal"] == "Enter Zurich"


def test_missing_cerebras_key_is_rejected_before_request(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://api.cerebras.ai/v1")
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    post = Mock()
    monkeypatch.setattr(model, "post_json", post)

    with pytest.raises(ValueError, match="CEREBRAS_API_KEY"):
        model.field_text({"goal": "Enter Zurich"})

    post.assert_not_called()


def test_cerebras_text_helper_requests_strict_schema(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", model.CEREBRAS_BASE_URL)
    monkeypatch.setenv("CEREBRAS_API_KEY", "offline")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    model.field_text({"goal": "Enter Zurich"})
    schema = post.call_args.args[2]["response_format"]
    assert schema["type"] == "json_schema"
    assert schema["json_schema"]["strict"] is True
    assert schema["json_schema"]["schema"] == {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"message": {"content": '{"text":"x","extra":true}'}}]},
    ],
)
def test_malformed_field_envelopes_are_sanitized_and_accounted(monkeypatch, response):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "offline")
    if isinstance(response, dict):
        response["usage"] = {"total_tokens": 12}
    monkeypatch.setattr(model, "post_json", Mock(return_value=response))
    with pytest.raises(model.InvalidModelResponse, match="nothing typed") as error:
        model.field_text({"goal": "Write"})
    assert error.value.model_call["success"] is False
    assert error.value.model_call["usage"] == ({"total_tokens": 12} if isinstance(response, dict) else {})


def test_field_helper_defaults_to_cerebras(monkeypatch):
    monkeypatch.setenv("CEREBRAS_API_KEY", "offline")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    model.field_text({"goal": "Enter Zurich"})
    assert post.call_args.args[0] == model.CEREBRAS_BASE_URL + "/chat/completions"
    assert post.call_args.args[2]["model"] == model.CEREBRAS_MODEL


@pytest.mark.parametrize("failure", [RuntimeError("connection failed"), ValueError("invalid JSON")])
def test_field_provider_failures_are_counted_without_retry(monkeypatch, failure):
    monkeypatch.setenv("CEREBRAS_API_KEY", "offline")
    post = Mock(side_effect=failure)
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(type(failure)) as error:
        model.field_text({"goal": "Write"})
    assert post.call_count == 1
    assert error.value.model_call["success"] is False
    assert error.value.model_call["provider"] == "text"


def test_invalid_text_endpoint_rejected_before_any_request(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "invalid")
    post = Mock()
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="TEXT_MODEL_BASE_URL"):
        model.field_text({"goal": "Write"})
    post.assert_not_called()
