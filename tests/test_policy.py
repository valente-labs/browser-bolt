"""Routing, accounting and action validation without browser or paid providers."""

import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import model, policy


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    for name in (
        "TYPESAFE_PROVIDER",
        "TYPESAFE_MODEL",
        "QWEV_POLICY",
        "QWEV_CONFIDENCE_THRESHOLD",
        "QWEV_FALLBACK_THRESHOLD",
        "QWEV_CEREBRAS_MODEL",
        "TEXT_MODEL",
        "TEXT_MODEL_REASONING",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-jev")
    monkeypatch.setenv("CEREBRAS_API_KEY", "offline-qwen")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", model.CEREBRAS_BASE_URL)
    monkeypatch.setattr(model.CLIENT, "post", Mock(side_effect=AssertionError("No live API in tests")))


@pytest.fixture
def page():
    return {
        "url": "https://example.test",
        "title": "Form",
        "text": "Complete the form",
        "actions": [
            {"id": "title", "kind": "fill", "label": "Title", "node": 1, "value": ""},
            {"id": "submit", "kind": "click", "label": "Submit", "node": 2},
            {"id": "option", "kind": "select", "label": "Category → Travel", "node": 3, "value": "travel"},
            {"id": "wait", "kind": "wait", "label": "Wait"},
            {"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560},
        ],
    }


def answer(criteria, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {key: float(key == selected) for key in criteria}}


def jev(body, operation="CLICK"):
    questions = body["questions"]
    answers = {"operation": answer(questions["operation"]["criteria"], operation)}
    head = operation.lower() + "_target"
    if head in questions:
        criteria = questions[head]["criteria"]
        answers[head] = answer(criteria, next(iter(criteria)))
    return {"model": "jev-test", "answers": answers, "usage": {"total_tokens": 17}}


def qwen(operation="CLICK", choice="submit", text=None):
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


def test_confident_jev_needs_one_request(page, monkeypatch):
    post = Mock(side_effect=lambda _url, _key, body: jev(body))
    monkeypatch.setattr(model, "post_json", post)
    decision = policy.choose(page, "Submit", [])
    assert decision["choice"] == "submit"
    assert decision["routing"]["route"] == "jev"
    assert len(decision["routing"]["model_calls"]) == post.call_count == 1


@pytest.mark.parametrize(
    "fault",
    [
        "provider",
        "missing_answers",
        "null",
        "answers_list",
        "bad_choice",
        "bad_probabilities",
        "low_operation",
        "low_target",
        "low_probability",
        "blocked",
        "stuck",
    ],
)
def test_hybrid_falls_back_once_with_usage(page, monkeypatch, fault):
    def respond(_url, _key, body):
        if "questions" not in body:
            return qwen("TYPE_TEXT", "title", "Lisbon weekend")
        result = jev(body, "BLOCKED" if fault == "blocked" else "CLICK")
        if fault == "provider":
            raise RuntimeError("provider unavailable")
        if fault == "missing_answers":
            result.pop("answers")
        elif fault == "null":
            return None
        elif fault == "answers_list":
            result["answers"] = []
        elif fault == "bad_choice":
            result["answers"]["click_target"]["choice"] = "999"
        elif fault == "bad_probabilities":
            result["answers"]["operation"]["probabilities"] = []
        elif fault in {"low_operation", "low_target"}:
            result["answers"]["operation" if fault == "low_operation" else "click_target"]["confidence"] = 0.5
        elif fault == "low_probability":
            result["answers"]["operation"]["probabilities"].update(CLICK=0.55, TYPE_TEXT=0.45)
        return result

    post = Mock(side_effect=respond)
    monkeypatch.setattr(model, "post_json", post)
    history = [{"kind": "click", "page_changed": False}] * 2 if fault == "stuck" else []
    decision = policy.choose(page, "Write a title", history)
    assert post.call_count == 2
    assert decision["inline_text"] == "Lisbon weekend"
    assert decision["target"] == "1"
    assert decision["confidence"] is decision["target_confidence"] is None
    assert decision["probabilities"] == {}
    calls = decision["routing"]["model_calls"]
    assert [call["provider"] for call in calls] == ["jev", "qwen"]
    assert calls[1]["usage"] == {"total_tokens": 31}
    assert calls[0]["usage"] == ({} if fault in {"provider", "null"} else {"total_tokens": 17})
    assert decision["routing"]["fallback"] is True
    if fault in {"missing_answers", "null", "answers_list", "bad_choice", "bad_probabilities", "provider"}:
        assert calls[0]["success"] is False


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"message": {"content": "not json"}}]},
        {"operation": "CLICK", "choice": "submit", "text": None},
    ],
)
def test_qwen_malformed_envelope_fails_closed_and_retains_call(page, monkeypatch, payload):
    monkeypatch.setenv("QWEV_POLICY", "qwen")
    post = Mock(return_value=payload)
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid Cerebras") as error:
        policy.choose(page, "Submit", [])
    assert post.call_count == 1
    assert error.value.routing["model_calls"][0]["success"] is False


@pytest.mark.parametrize(
    "operation,choice,text",
    [
        ("CLICK", "unknown", None),
        ("CLICK", "title", None),
        ("TYPE_TEXT", "submit", "x"),
        ("SELECT", "title", None),
        ("CLICK", "submit", "code"),
        ("DONE", "DONE", " "),
        ("DONE", "unknown", ""),
        ("TYPE_TEXT", "title", None),
        ("TYPE_TEXT", "title", ""),
        ("TYPE_TEXT", "title", " " * 2),
        ("TYPE_TEXT", "title", "x" * 2001),
        ("DONE", "submit", None),
        ("WAIT", "scroll_down", None),
        ("click", "submit", None),
        ([], "submit", None),
        ("CLICK", {}, None),
    ],
)
def test_qwen_invalid_action_is_rejected(page, monkeypatch, operation, choice, text):
    monkeypatch.setenv("QWEV_POLICY", "qwen")
    post = Mock(return_value=qwen(operation, choice, text))
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError) as error:
        policy.choose(page, "Complete form", [])
    assert post.call_count == 1
    assert error.value.routing["model_calls"] == [
        {**error.value.routing["model_calls"][0], "usage": {"total_tokens": 31}, "success": False}
    ]


@pytest.mark.parametrize(
    "operation,choice,target",
    [
        ("CLICK", "submit", "2"),
        ("SELECT", "option", "3:1"),
        ("WAIT", "wait", None),
        ("SCROLL_DOWN", "scroll_down", None),
        ("DONE", "DONE", None),
        ("BLOCKED", "BLOCKED", None),
    ],
)
def test_qwen_compatible_actions_and_strict_schema(page, monkeypatch, operation, choice, target):
    monkeypatch.setenv("QWEV_POLICY", "qwen")
    monkeypatch.delenv("TYPESAFE_API_KEY")
    post = Mock(return_value=qwen(operation, choice))
    monkeypatch.setattr(model, "post_json", post)
    decision = policy.choose(page, "Complete form", [])
    assert (decision["choice"], decision["target"]) == (choice, target)
    assert decision["routing"]["fallback"] is False
    schema = post.call_args.args[2]["response_format"]
    assert schema["type"] == "json_schema" and schema["json_schema"]["strict"] is True
    schema = schema["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"operation", "choice", "text"}
    assert "SCROLL_UP" not in schema["properties"]["operation"]["enum"]
    assert "unknown" not in schema["properties"]["choice"]["enum"]


@pytest.mark.parametrize("fault", ["extra_key", "failure"])
def test_failed_fallback_never_retries_and_keeps_both_calls(page, monkeypatch, fault):
    response = qwen()
    if fault == "extra_key":
        payload = json.loads(response["choices"][0]["message"]["content"])
        payload["type"] = "object"
        response["choices"][0]["message"]["content"] = json.dumps(payload)
    post = Mock(side_effect=[RuntimeError("Jev down"), response if fault == "extra_key" else RuntimeError("Qwen down")])
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises((ValueError, RuntimeError)) as error:
        policy.choose(page, "Submit", [])
    assert post.call_count == 2
    assert len(error.value.routing["model_calls"]) == 2
    assert all(not c["success"] for c in error.value.routing["model_calls"])


@pytest.mark.parametrize(
    "env,value",
    [
        ("QWEV_POLICY", "typo"),
        ("QWEV_CONFIDENCE_THRESHOLD", "nan"),
        ("QWEV_CONFIDENCE_THRESHOLD", "bad"),
        ("QWEV_CONFIDENCE_THRESHOLD", "1.1"),
        ("QWEV_CEREBRAS_MODEL", ""),
        ("TYPESAFE_PROVIDER", "typo"),
        ("TYPESAFE_API_KEY", ""),
        ("CEREBRAS_API_KEY", ""),
        ("TEXT_MODEL", ""),
    ],
)
def test_configuration_errors_do_not_spend(page, monkeypatch, env, value):
    monkeypatch.setenv(env, value)
    post = Mock()
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError):
        policy.choose(page, "Submit", [])
    post.assert_not_called()


@pytest.mark.parametrize(
    "bad_action",
    [
        {"id": "wait", "kind": "unknown", "label": "Wait"},
        {"id": "scroll_down", "kind": "wait", "label": "Scroll"},
        {"id": "custom", "kind": "scroll", "label": "Custom"},
        {"id": "DONE", "kind": "click", "label": "Done", "node": 1},
        {"id": "title", "kind": "click", "label": "Duplicate", "node": 8},
        {"id": "new", "kind": "click", "label": "No identity"},
    ],
)
def test_bad_observed_actions_fail_before_calls(page, monkeypatch, bad_action):
    page["actions"].append(bad_action)
    post = Mock()
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError):
        policy.choose(page, "Submit", [])
    post.assert_not_called()


def test_jev_mode_never_falls_back(page, monkeypatch):
    monkeypatch.setenv("QWEV_POLICY", "jev")
    post = Mock(side_effect=RuntimeError("down"))
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(RuntimeError) as error:
        policy.choose(page, "Submit", [])
    assert post.call_count == len(error.value.routing["model_calls"]) == 1


def test_wait_resets_consecutive_no_progress(page, monkeypatch):
    post = Mock(side_effect=lambda _url, _key, body: jev(body))
    monkeypatch.setattr(model, "post_json", post)
    history = [{"kind": "click", "page_changed": False}] * 2 + [{"kind": "wait", "page_changed": False}]
    assert policy.choose(page, "Submit", history)["routing"]["route"] == "jev"
    assert post.call_count == 1


def test_field_cache_context_binds_url_and_element_identity(page):
    original = model.field_context("Fill", page["actions"][0], page, [])
    changed = deepcopy(page)
    changed["url"] += "/different"
    assert original != model.field_context("Fill", changed["actions"][0], changed, [])
    changed = deepcopy(page)
    changed["actions"][0]["node"] = 9
    assert original != model.field_context("Fill", changed["actions"][0], changed, [])


@pytest.mark.parametrize(
    "operation,choice",
    [
        ("CLICK", "submit"),
        ("SELECT", "option"),
        ("WAIT", "wait"),
        ("SCROLL_DOWN", "scroll_down"),
        ("DONE", "DONE"),
        ("BLOCKED", "BLOCKED"),
    ],
)
def test_empty_non_typing_payload_means_no_text(page, monkeypatch, operation, choice):
    monkeypatch.setenv("QWEV_POLICY", "qwen")
    monkeypatch.setattr(model, "post_json", Mock(return_value=qwen(operation, choice, "")))
    decision = policy.choose(page, "Complete form", [])
    assert decision["choice"] == choice
    assert "inline_text" not in decision


@pytest.mark.parametrize("field", ["probability", "confidence"])
@pytest.mark.parametrize("number", [10**400, -(10**400)], ids=["huge-positive", "huge-negative"])
def test_huge_jev_numbers_fall_back_and_preserve_usage(page, monkeypatch, field, number):
    def respond(_url, _key, body):
        if "questions" not in body:
            return qwen()
        result = jev(body)
        selected = result["answers"]["operation"]
        if field == "probability":
            selected["probabilities"]["CLICK"] = number
        else:
            selected["confidence"] = number
        return result

    post = Mock(side_effect=respond)
    monkeypatch.setattr(model, "post_json", post)
    decision = policy.choose(page, "Submit", [])
    assert post.call_count == 2
    assert decision["choice"] == "submit"
    assert decision["routing"]["reason"] == "jev_unavailable"
    jev_call, qwen_call = decision["routing"]["model_calls"]
    assert jev_call["success"] is False
    assert jev_call["usage"] == {"total_tokens": 17}
    assert qwen_call["success"] is True
    assert qwen_call["usage"] == {"total_tokens": 31}
