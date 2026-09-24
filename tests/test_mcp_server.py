"""Offline MCP contracts, including the real official SDK stdio transport."""

import asyncio
import json
import sys
from unittest.mock import Mock

import httpx
import pytest

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

from jev_ultrafast import mcp_server as server  # noqa: E402


@pytest.fixture
def arguments():
    return {
        "profile": "qwen_openrouter",
        "goal": "Write a trip title",
        "history": [],
        "state": {
            "url": "https://example.test",
            "title": "Form",
            "text": "untrusted page content",
            "fingerprint": "observed-1",
            "actions": [
                {"id": "title", "node": 1, "kind": "fill", "label": "Title", "value": ""},
                {"id": "submit", "node": 2, "kind": "click", "label": "Submit"},
            ],
        },
    }


def payload(operation="TYPE_TEXT", choice="title", text="Trip"):
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


def service(respond):
    client = httpx.Client(transport=httpx.MockTransport(respond))
    return server.DecisionService(client=client, environ={"OPENROUTER_API_KEY": "offline-only"})


def test_list_profiles_requires_neither_keys_nor_requests():
    svc = server.DecisionService(client=Mock(), environ={})
    result = svc.invoke("list_profiles", {})
    assert result["default_profile"] == "jev_qwen_openrouter"
    profiles = {item["profile"]: item for item in result["profiles"]}
    assert profiles["jev_qwen"]["required_environment"] == ["OPENROUTER_API_KEY", "CEREBRAS_API_KEY"]
    assert profiles["jev_qwen_openrouter"]["required_environment"] == ["OPENROUTER_API_KEY"]
    assert profiles["jev_qwen_openrouter"]["optional_environment"] == ["CEREBRAS_API_KEY"]
    assert profiles["jev_qwen_openrouter_fast"]["provider_routing"] == {
        "sort": "throughput",
        "require_parameters": True,
    }
    assert "no provider pin" in profiles["jev_qwen_openrouter_fast"]["note"]
    assert len(result["profiles"]) == 10
    assert not result["browser_execution"] and not result["screenshots"]
    svc.client.post.assert_not_called()


def test_choose_filters_raw_pages_provider_responses_and_requests(arguments):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=payload())

    svc = service(respond)
    with svc.client:
        result = svc.invoke("choose_browser_action", arguments)
    assert result["ok"] and result["operation"] == "TYPE_TEXT"
    assert result["choice"] == "title" and result["target"] == "1"
    assert result["inline_text"] == "Trip" and not result["requires_field_text"]
    assert result["observation_fingerprint"] == "observed-1"
    assert result["model_calls"][0]["usage"] == {"total_tokens": 31}
    assert len(requests) == 1
    rendered = json.dumps(result)
    for private in ("offline-only", "untrusted page content", "Write a trip title", "request", "raw_answers"):
        assert private not in rendered


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a.update(profile="https://malicious.test/private"),
        lambda a: a.update(api_key="private-key"),
        lambda a: a["state"].update(screenshot="image"),
        lambda a: a["state"]["actions"][0].update(selector="#private"),
        lambda a: a["state"]["actions"][0].update(node=True),
        lambda a: a["state"]["actions"][1].update(id="title"),
        lambda a: a["state"]["actions"][1].update(kind="evaluate"),
        lambda a: a.update(goal=" " * 4),
        lambda a: a.update(history=[{}] * 11),
        lambda a: a["state"].update(text="x" * 40_001),
        lambda a: a["state"].pop("fingerprint"),
    ],
)
def test_invalid_inputs_rejected_before_spend(arguments, mutate):
    mutate(arguments)
    svc = server.DecisionService(client=Mock(), environ={"OPENROUTER_API_KEY": "offline-only"})
    result = svc.invoke("choose_browser_action", arguments)
    assert result["error"] == "invalid_input"
    assert result["model_calls"] == []
    assert "private" not in json.dumps(result)
    svc.client.post.assert_not_called()


def test_total_payload_cap_before_paid_call(arguments):
    arguments["state"]["actions"] = [
        {"id": str(i), "node": i + 1, "kind": "click", "label": "x" * 2000} for i in range(100)
    ]
    svc = server.DecisionService(client=Mock(), environ={})
    assert svc.invoke("choose_browser_action", arguments)["error"] == "payload_too_large"
    svc.client.post.assert_not_called()


@pytest.mark.parametrize(
    "response,status",
    [
        ({"error": "private provider message", "usage": {"total_tokens": 7}}, 429),
        (payload("CLICK", "unobserved-id", None), 200),
        (payload("CLICK", "title", None), 200),
        (payload(text=" " * 2), 200),
    ],
)
def test_provider_failure_keeps_accounting_without_payload(arguments, response, status):
    svc = service(lambda _: httpx.Response(status, json=response))
    with svc.client:
        result = svc.invoke("choose_browser_action", arguments)
    assert not result["ok"] and result["error"] == "provider_response_rejected"
    assert result["model_calls"][0]["usage"] == response["usage"]
    assert not result["model_calls"][0]["success"]
    assert "private provider" not in json.dumps(result)
    assert "unobserved-id" not in json.dumps(result)


def test_transport_failure_has_single_failed_call(arguments):
    calls = []

    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout("private request", request=request)

    svc = service(respond)
    with svc.client:
        result = svc.invoke("choose_browser_action", arguments)
    assert len(calls) == 1 and len(result["model_calls"]) == 1
    assert result["model_calls"][0]["usage"] == {}
    assert "private" not in json.dumps(result)


def test_usage_keys_cannot_echo_arbitrary_provider_data(arguments):
    response = payload()
    response["usage"] = {
        "total_tokens": 31,
        "private-client-name": 2,
        "details": {"private": 1},
        "prompt_tokens_details": {"cached_tokens": 3, "private-client-name": 7},
    }
    svc = service(lambda _: httpx.Response(200, json=response))
    with svc.client:
        result = svc.invoke("choose_browser_action", arguments)
    assert result["model_calls"][0]["usage"] == {
        "total_tokens": 31,
        "prompt_tokens_details": {"cached_tokens": 3},
    }


def test_writer_and_no_writer_preflight(arguments):
    context = {
        "goal": arguments["goal"],
        "field": arguments["state"]["actions"][0],
        "page": {k: v for k, v in arguments["state"].items() if k != "actions"},
    }
    response = {"choices": [{"message": {"content": '{"text":"Trip"}'}}], "usage": {"total_tokens": 9}}
    svc = service(lambda _: httpx.Response(200, json=response))
    with svc.client:
        result = svc.invoke("write_browser_field", {"profile": "qwen_openrouter", "context": context})
        assert result["ok"] and result["text"] == "Trip" and result["field_id"] == "title"
        assert result["model_calls"][0]["role"] == "field_text"
        assert svc.invoke("write_browser_field", {"profile": "jev", "context": context})["error"] == (
            "profile_has_no_writer"
        )
        context["field"]["kind"] = "click"
        assert svc.invoke("write_browser_field", {"profile": "qwen_openrouter", "context": context})["error"] == (
            "invalid_input"
        )


def test_no_credentials_and_busy_are_sanitized(arguments):
    svc = server.DecisionService(client=Mock(), environ={})
    assert svc.invoke("choose_browser_action", arguments)["error"] == "profile_unavailable"
    svc.environ = {"OPENROUTER_API_KEY": "offline-only"}
    for _ in range(4):
        svc.slots.acquire()
    assert svc.invoke("choose_browser_action", arguments)["error"] == "server_busy"
    svc.client.post.assert_not_called()


def test_lifespan_closes_shared_client():
    svc = server.DecisionService(environ={})

    async def run():
        async with Client(server.create_server(lambda: svc)) as client:
            for _ in range(2):
                result = await client.call_tool("list_profiles", {})
                assert result.structured_content["ok"]
            assert not svc.client.is_closed
        assert svc.client.is_closed

    asyncio.run(run())


def test_real_sdk_stdio_initialize_list_call_offline():
    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "jev_ultrafast.mcp_server"], env={})
        async with Client(params, read_timeout_seconds=15) as client:
            listed = await client.list_tools()
            assert {tool.name for tool in listed.tools} == set(server.SCHEMAS)
            result = await client.call_tool("list_profiles", {})
            assert not result.is_error
            assert result.structured_content["default_profile"] == "jev_qwen_openrouter"
            invalid = await client.call_tool("choose_browser_action", {"api_key": "private-test"})
            assert invalid.is_error and "private-test" not in str(invalid.content)
            missing = await client.call_tool("not_a_tool", {})
            assert missing.is_error and missing.structured_content["error"] == "unknown_tool"

    asyncio.run(run())


def test_native_snapshot_and_field_context_normalization(arguments):
    from copy import deepcopy

    from jev_ultrafast import model

    native = deepcopy(arguments["state"])
    native["actions"][0].update(rect={"x": 1, "y": 2, "w": 200, "h": 20}, expanded="false")
    native["actions"][1].update(checked="mixed", selected="true")
    native["actions"].append({"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 720})
    native["guard"] = "host-only guard"
    before = deepcopy(native)
    normalized = server.normalize_native_state(native)
    assert native == before
    assert normalized["actions"][0]["expanded"] is False
    assert normalized["actions"][1]["selected"] is True
    assert normalized["actions"][1]["checked"] == "mixed"
    assert "rect" not in normalized["actions"][0] and "delta" not in normalized["actions"][2]
    svc = service(lambda _: httpx.Response(200, json=payload()))
    with svc.client:
        result = svc.invoke("choose_browser_action", {**arguments, "state": normalized})
    assert result["ok"]
    native_context = model.field_context(
        arguments["goal"], native["actions"][0], native, [{"action": "submit", "text": None}]
    )
    context = server.normalize_native_field_context(native_context)
    assert context["field"]["kind"] == "fill" and "text" not in context["recent_actions"][0]
    svc = service(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"text":"Trip"}'}}],
                "usage": {"total_tokens": 8},
            },
        )
    )
    with svc.client:
        assert svc.invoke("write_browser_field", {"profile": "qwen_openrouter", "context": context})["ok"]
