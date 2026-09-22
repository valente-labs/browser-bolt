"""Explicit, isolated benchmark profiles. No environment mutation or decision fallback."""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Mapping

import httpx

from . import model
from .policy import _QWEN_SYSTEM
from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

PROFILE_NAMES = (
    "qwen",
    "astra",
    "opus",
    "jev_qwen",
    "jev_astra",
    "jev_opus",
    "jev",
    "qwen_openrouter",
    "jev_qwen_openrouter",
)
CHAT_MODELS = {
    "qwen": "qwen-3.8-27b",
    "astra": "openai/gpt-6-astra",
    "opus": "anthropic/claude-opus-5",
    "qwen_openrouter": "qwen/qwen3.8-27b",
}
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    endpoint: str
    model: str
    key: str = field(repr=False)
    reasoning: str | None = None


def _usage(value):
    """Keep numeric accounting, never arbitrary provider error strings."""
    if not isinstance(value, dict):
        return {}
    return {
        key: _usage(item) if isinstance(item, dict) else item
        for key, item in value.items()
        if isinstance(key, str) and (isinstance(item, dict) or (type(item) in (int, float) and -(2**63) < item < 2**63))
    }


def _schema(name, properties):
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


def _recent(history):
    return [{k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]]


def _valid_text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 2000


class Profile:
    """A pinned decision model and optional writer sharing one bounded connection pool.

    Use as a context manager, or call close(). A supplied client remains caller-owned,
    allowing a benchmark cohort to reuse connections across profiles.
    """

    def __init__(self, name, decision, writer, *, client=None):
        self.name = name
        self.decision = decision
        self.writer = writer
        self._owns_client = client is None
        self.client = (
            client
            if client is not None
            else httpx.Client(
                http2=True,
                timeout=httpx.Timeout(30, connect=5),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4, keepalive_expiry=120),
            )
        )

    def preflight(self):
        """Factory configuration is validated before construction; no requests are made."""
        return self

    def close(self):
        if self._owns_client:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def _request(self, config, body, parse, role):
        started = time.perf_counter()
        result = None
        call = {"provider": config.provider, "model": config.model, "role": role, "usage": {}, "success": False}
        try:
            response = self.client.post(
                config.endpoint,
                json=body,
                headers={"Authorization": f"Bearer {config.key}"},
                timeout=httpx.Timeout(30, connect=5),
            )
            call["http_status"] = response.status_code
            try:
                result = response.json()
            except ValueError:
                raise ValueError("Model provider returned invalid JSON; no action executed.") from None
            call["usage"] = _usage(result.get("usage")) if isinstance(result, dict) else {}
            if response.is_error:
                raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
            parsed = parse(result)
        except (httpx.HTTPError, RuntimeError, AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
            call["latency_ms"] = round((time.perf_counter() - started) * 1000)
            # Do not expose provider payloads, HTTP exception URLs or authorization headers.
            message = (
                "Model connection failed; no action executed."
                if isinstance(error, httpx.HTTPError)
                else ("Model response rejected; no action executed.")
            )
            raise model.InvalidModelResponse(message, call) from None
        call.update(success=True, latency_ms=round((time.perf_counter() - started) * 1000))
        return parsed, call

    def _routing(self, started, calls):
        is_jev = self.name == "jev" or self.name.startswith("jev_")
        return {
            "policy": self.name,
            "profile": self.name,
            "route": "jev" if is_jev else self.name,
            "fallback": False,
            "reason": None,
            "model_calls": calls,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "jev_latency_ms": sum(c["latency_ms"] for c in calls) if is_jev else 0,
            "fallback_latency_ms": 0,
            "jev_usage": calls[0]["usage"] if is_jev and calls else {},
            "fallback_usage": {},
        }

    def choose(self, state, goal, history):
        started, calls = time.perf_counter(), []
        try:
            elements, targets, controls = model.action_space(state["actions"])
            if self.name == "jev" or self.name.startswith("jev_"):
                body, parse = self._jev_contract(state, goal, history, elements, targets, controls)
            else:
                body, parse = self._chat_contract(state, goal, history, targets, controls)
            decision, call = self._request(self.decision, body, parse, "decision")
            calls.append(call)
        except (ValueError, KeyError, TypeError) as error:
            if getattr(error, "model_call", None):
                calls.append(error.model_call)
            error.routing = self._routing(started, calls)
            raise
        routing = self._routing(started, calls)
        decision.update(
            model=self.decision.model,
            provider=self.decision.provider,
            usage=call["usage"],
            request=body,
            latency_ms=routing["latency_ms"],
            routing=routing,
            jev_usage=routing["jev_usage"],
            fallback_usage={},
        )
        return decision

    @staticmethod
    def _writer_body(config, system, context, response_format, max_tokens):
        reasoning = (
            {"reasoning_effort": config.reasoning}
            if config.provider == "cerebras"
            else {"reasoning": {"enabled": False} if config.reasoning == "none" else {"effort": config.reasoning}}
        )
        return {
            "model": config.model,
            "max_tokens": max_tokens,
            "response_format": response_format,
            **reasoning,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context)}],
        }

    def _chat_contract(self, state, goal, history, targets, controls):
        allowed = {op: {a["id"]: index for index, a in candidates.items()} for op, candidates in targets.items()}
        allowed.update({op: {action["id"]: None} for op, action in controls.items()})
        allowed.update(DONE={"DONE": None}, BLOCKED={"BLOCKED": None})
        actions = [
            {
                "operation": op,
                **{
                    k: a[k]
                    for k in ("id", "label", "role", "value", "current_value", "checked", "selected", "expanded")
                    if k in a
                },
            }
            for op, choices in allowed.items()
            for a in state["actions"]
            if a["id"] in choices
        ]
        body = self._writer_body(
            self.decision,
            _QWEN_SYSTEM,
            {
                "goal": goal,
                "page": {k: state[k] for k in ("url", "title", "text")},
                "observed_actions": actions,
                "allowed_operations": {op: list(choices) for op, choices in allowed.items()},
                "recent_actions": _recent(history),
            },
            _schema(
                "browser_decision",
                {
                    "operation": {"type": "string", "enum": list(allowed)},
                    "choice": {"type": "string", "enum": [c for choices in allowed.values() for c in choices]},
                    "text": {"type": ["string", "null"]},
                },
            ),
            2048,
        )

        def parse(result):
            payload = json.loads(result["choices"][0]["message"]["content"])
            if not isinstance(payload, dict) or set(payload) != {"operation", "choice", "text"}:
                raise ValueError()
            operation, choice, text = payload["operation"], payload["choice"], payload["text"]
            if (
                not isinstance(operation, str)
                or not isinstance(choice, str)
                or choice not in allowed.get(operation, {})
            ):
                raise ValueError()
            if operation == "TYPE_TEXT":
                if not _valid_text(text):
                    raise ValueError()
            elif text not in (None, ""):
                raise ValueError()
            return {
                "choice": choice,
                "operation": operation,
                "target": allowed[operation][choice],
                "confidence": None,
                "probabilities": {},
                "operation_probabilities": {},
                "target_probabilities": {},
                "target_confidence": None,
                "raw_answers": {},
                **({"inline_text": text} if operation == "TYPE_TEXT" else {}),
            }

        return body, parse

    def _jev_contract(self, state, goal, history, elements, targets, controls):
        labels = {
            "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
            "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
            "SELECT": "Select an observed dropdown value.",
        }
        operations = {key: labels[key] for key in targets}
        operations.update({key: value["label"] for key, value in controls.items()})
        operations.update(
            DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress."
        )
        questions = {
            "operation": {
                "type": "choice",
                "criteria": operations,
                "instructions": {"goal": goal, "rules": NEXT_ACTION},
            }
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
        if self.decision.provider == "openrouter":
            for question in questions.values():
                question["instructions"] = json.dumps(question["instructions"])
                question["criteria"] = {
                    key: value if isinstance(value, str) else json.dumps(value)
                    for key, value in question["criteria"].items()
                }
        body = {
            "model": self.decision.model,
            "questions": questions,
            "state": {
                "page": {k: state[k] for k in ("url", "title", "text")},
                "elements": elements,
                "recent_actions": _recent(history),
            },
        }

        def parse(result):
            answer = model.validate_choice(result["answers"].get("operation", {}), operations)
            operation = answer["choice"]
            target, target_answer = None, None
            if operation in targets:
                target_answer = model.validate_choice(
                    result["answers"].get(operation.lower() + "_target", {}), targets[operation]
                )
                target = target_answer["choice"]
                choice = targets[operation][target]["id"]
                probabilities = {a["id"]: target_answer["probabilities"][i] for i, a in targets[operation].items()}
            else:
                choice = controls[operation]["id"] if operation in controls else operation
                probabilities = {choice: answer["probabilities"][operation]}
            return {
                "choice": choice,
                "operation": operation,
                "target": target,
                "confidence": answer["confidence"],
                "probabilities": probabilities,
                "operation_probabilities": answer["probabilities"],
                "target_probabilities": target_answer["probabilities"] if target_answer else {},
                "target_confidence": target_answer["confidence"] if target_answer else None,
                "raw_answers": result["answers"],
            }

        return body, parse

    def field_text(self, context):
        if self.writer is None:
            raise ValueError(f"Profile {self.name} has no field writer; TYPE_TEXT unsupported; no request was sent.")
        body = self._writer_body(
            self.writer,
            TEXT_VALUE,
            context,
            _schema(
                "field_value",
                {
                    "text": {"type": "string"},
                },
            ),
            1024,
        )

        def parse(result):
            payload = json.loads(result["choices"][0]["message"]["content"])
            if not isinstance(payload, dict) or set(payload) != {"text"} or not _valid_text(payload["text"]):
                raise ValueError()
            return payload["text"]

        return self._request(self.writer, body, parse, "field_text")


def get_profile(name, *, environ: Mapping[str, str] | None = None, client=None, jev_provider="openrouter"):
    """Validate only this arm's credentials before any request; models are pinned.

    Jev uses OpenRouter by default for comparable runs. Explicit ``jev_provider='direct'``
    uses TypeSafe's ``jev-latest`` model and requires TYPESAFE_API_KEY instead.
    No generic TEXT_MODEL key or model override is inherited from production settings.
    """
    if name not in PROFILE_NAMES:
        raise ValueError("Unknown comparator profile; no request was sent.")
    env = os.environ if environ is None else environ

    def key(variable):
        value = env.get(variable)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Profile {name} needs {variable}; no request was sent.")
        return value.strip()

    def chat_config(arm):
        if arm == "qwen":
            return ProviderConfig(
                "cerebras",
                model.CEREBRAS_BASE_URL + "/chat/completions",
                CHAT_MODELS[arm],
                key("CEREBRAS_API_KEY"),
                "none",
            )
        return ProviderConfig(
            "openrouter",
            OPENROUTER_CHAT_URL,
            CHAT_MODELS[arm],
            key("OPENROUTER_API_KEY"),
            "none" if arm == "qwen_openrouter" else "low",
        )

    writer = chat_config(name[4:]) if name.startswith("jev_") else None
    if name == "jev" or name.startswith("jev_"):
        if jev_provider == "openrouter":
            decision = ProviderConfig(
                "openrouter", model.TYPESAFE_OPENROUTER_URL, model.TYPESAFE_OPENROUTER_MODEL, key("OPENROUTER_API_KEY")
            )
        elif jev_provider == "direct":
            decision = ProviderConfig("typesafe", model.TYPESAFE_DIRECT_URL, "jev-latest", key("TYPESAFE_API_KEY"))
        else:
            raise ValueError("Unsupported Jev provider; no request was sent.")
    else:
        decision = chat_config(name)
    return Profile(name, decision, writer, client=client)
