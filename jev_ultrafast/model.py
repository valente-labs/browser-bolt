"""Validated Jev choices and OpenAI-compatible field generation."""

import atexit
import json
import math
import os
import time

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

# One reusable HTTP/2 pool; provider requests never retry browser mutations.
CLIENT = httpx.Client(
    http2=True,
    timeout=httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=1.0),
    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4, keepalive_expiry=120),
)
atexit.register(CLIENT.close)

TYPESAFE_DIRECT_URL = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_OPENROUTER_URL = "https://openrouter.ai/api/alpha/decisions"
TYPESAFE_OPENROUTER_MODEL = "typesafe/jev-1.13"
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
CEREBRAS_MODEL = "qwen-3.8-27b"


class InvalidModelResponse(ValueError):
    """A paid response was rejected; retain its non-secret call accounting."""

    def __init__(self, message, model_call):
        super().__init__(message)
        self.model_call = model_call


def call_record(provider, model_name, started, result=None, *, success=True):
    usage = result.get("usage", {}) if isinstance(result, dict) else {}
    return {
        "provider": provider,
        "model": model_name,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": usage if isinstance(usage, dict) else {},
        "success": success,
    }


def _required_key(name, purpose):
    key = os.environ.get(name)
    if not key or not key.strip():
        raise ValueError(f"{purpose} needs {name}; no request was sent.")
    return key


def _typesafe_config():
    provider = os.environ.get("TYPESAFE_PROVIDER", "direct").strip().lower()
    if provider == "direct":
        key = _required_key("TYPESAFE_API_KEY", "TypeSafe")
        model = os.environ.get("TYPESAFE_MODEL", "jev-latest").strip()
        if not model:
            raise ValueError("TYPESAFE_MODEL must not be empty; no request was sent.")
        return TYPESAFE_DIRECT_URL, key, model
    if provider == "openrouter":
        key = _required_key("OPENROUTER_API_KEY", "OpenRouter TypeSafe")
        return TYPESAFE_OPENROUTER_URL, key, TYPESAFE_OPENROUTER_MODEL
    raise ValueError(f"Unsupported TYPESAFE_PROVIDER {provider!r}; no request was sent.")


def post_json(url, key, body):
    """One bounded request: let the policy fail over instead of sleeping/retrying."""
    try:
        response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError:
        raise RuntimeError("Model connection failed; no action executed.") from None
    if response.is_error:
        raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
    try:
        return response.json()
    except ValueError:
        raise ValueError("Model provider returned invalid JSON; no action executed.") from None


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and 0 <= n <= 1 and math.isfinite(n) for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    seen = set()
    control_kinds = {"wait": "wait", "scroll_up": "scroll", "scroll_down": "scroll"}
    if not isinstance(actions, list):
        raise ValueError("Invalid observed action table; no request was sent.")
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("Invalid observed action table; no request was sent.")
        action_id, kind = action.get("id"), action.get("kind")
        if (
            not isinstance(action_id, str)
            or not action_id
            or action_id in seen
            or action_id in {"DONE", "BLOCKED"}
            or not isinstance(action.get("label"), str)
        ):
            raise ValueError("Invalid observed action table; no request was sent.")
        seen.add(action_id)
        if kind not in operations:
            if control_kinds.get(action_id) != kind or action_id not in control_kinds:
                raise ValueError("Unsupported observed operation; no request was sent.")
            controls[action_id.upper()] = action
            continue
        node = action.get("node")
        if type(node) is not int or node < 1:
            raise ValueError("Invalid observed element identity; no request was sent.")
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            element.setdefault("options", [])
            if not isinstance(action.get("value"), str):
                raise ValueError("Invalid observed select value; no request was sent.")
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    # Resolve and validate credentials before constructing a live request. The
    # OpenRouter path uses the native Decisions endpoint and a pinned Jev model.
    endpoint, key, model = _typesafe_config()
    if endpoint == TYPESAFE_OPENROUTER_URL:
        # OpenRouter's alpha schema accepts strings where direct TypeSafe also accepts JSON.
        for question in questions.values():
            question["instructions"] = json.dumps(question["instructions"])
            question["criteria"] = {
                name: value if isinstance(value, str) or value is None else json.dumps(value)
                for name, value in question["criteria"].items()
            }
    body = {
        "model": model,
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result = post_json(endpoint, key, body)
    try:
        operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
        operation = operation_answer["choice"]
        target = None
        target_answer = None
        probabilities = {}
        if operation in targets:
            # Unused target heads cannot cause an action. Validate the head selected by the operation.
            target_answer = validate_choice(
                result["answers"].get(operation.lower() + "_target", {}), targets[operation]
            )
            target = target_answer["choice"]
            choice = targets[operation][target]["id"]
            probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
        else:
            choice = controls[operation]["id"] if operation in controls else operation
            probabilities[choice] = operation_answer["probabilities"][operation]
        return {
            "choice": choice,
            "operation": operation,
            "target": target,
            "confidence": operation_answer["confidence"],
            "probabilities": probabilities,
            "operation_probabilities": operation_answer["probabilities"],
            "target_probabilities": target_answer["probabilities"] if target_answer else {},
            "target_confidence": target_answer["confidence"] if target_answer else None,
            "raw_answers": result["answers"],
            "model": result.get("model", model),
            "usage": result.get("usage", {}),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "request": body,
        }
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        raise InvalidModelResponse(
            "Invalid TypeSafe response; no action executed.",
            call_record("jev", model, started, result, success=False),
        ) from None


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("id", "node", "label", "role", "value")},
        "page": {
            "url": page["url"],
            "title": page["title"],
            "text": page["text"][:6000],
            "fingerprint": page.get("fingerprint"),
        },
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def text_config():
    base = os.environ.get("TEXT_MODEL_BASE_URL", CEREBRAS_BASE_URL).rstrip("/")
    try:
        endpoint = httpx.URL(base)
        if endpoint.scheme not in {"http", "https"} or not endpoint.host:
            raise ValueError()
    except (httpx.InvalidURL, ValueError):
        raise ValueError("TEXT_MODEL_BASE_URL must be an absolute HTTP(S) URL; no request was sent.") from None
    is_cerebras = base == CEREBRAS_BASE_URL
    if is_cerebras:
        # Keep the dedicated Cerebras credential usable while retaining the
        # existing generic TEXT_MODEL_API_KEY configuration for compatibility.
        key = os.environ.get("CEREBRAS_API_KEY") or os.environ.get("TEXT_MODEL_API_KEY")
        if not key or not key.strip():
            raise ValueError("Cerebras text helper needs CEREBRAS_API_KEY or TEXT_MODEL_API_KEY; no request was sent.")
        model = os.environ.get("TEXT_MODEL", CEREBRAS_MODEL).strip()
        if not model:
            raise ValueError("TEXT_MODEL must not be empty; no request was sent.")
        reasoning = {"reasoning_effort": os.environ.get("TEXT_MODEL_REASONING", "none")}
    else:
        key = _required_key("TEXT_MODEL_API_KEY", "TYPE_TEXT")
        model = os.environ.get("TEXT_MODEL", "deepseek-chat").strip()
        if not model:
            raise ValueError("TEXT_MODEL must not be empty; no request was sent.")
        reasoning = (
            {"thinking": {"type": "disabled"}} if "api.deepseek.com/" in base else {"reasoning": {"effort": "low"}}
        )
        if os.environ.get("TEXT_MODEL_REASONING") == "none":
            reasoning = {"reasoning": {"enabled": False}}
    return base, key, model, reasoning


def field_text(context):
    base, key, model, reasoning = text_config()
    response_format = {"type": "json_object"}
    if base == CEREBRAS_BASE_URL:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "field_value",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        }
    started = time.perf_counter()
    try:
        result = post_json(
            base + "/chat/completions",
            key,
            {
                "model": model,
                "max_tokens": 1024,
                "response_format": response_format,
                **reasoning,
                "messages": [
                    {"role": "system", "content": TEXT_VALUE},
                    {
                        "role": "user",
                        "content": json.dumps(context),
                    },
                ],
            },
        )
    except (httpx.HTTPError, RuntimeError, ValueError) as error:
        error.model_call = call_record("text", model, started, success=False)
        raise
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (AttributeError, IndexError, ValueError, KeyError, TypeError):
        raise InvalidModelResponse(
            "Text helper returned no valid field value; nothing typed.",
            call_record("text", model, started, result, success=False),
        ) from None
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }
