"""Owned-target lifecycle and cooperative input boundary tests. No browser access."""

from unittest.mock import Mock

import pytest

from jev_ultrafast import browser


@pytest.fixture(autouse=True)
def no_pending_dialog(monkeypatch):
    monkeypatch.setattr(browser, "_send", Mock(return_value={"dialog": None}))


@pytest.mark.parametrize("failed_method", [
    "Target.attachToTarget", "Page.enable", "Emulation.setDeviceMetricsOverride", "Emulation.setFocusEmulationEnabled",
    "Page.navigate", "Runtime.evaluate",
])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_partial_setup_closes_only_owned_target(monkeypatch, failed_method, cleanup_fails):
    original = RuntimeError("setup failed")

    def cdp(method, **kwargs):
        if method == failed_method:
            raise original
        if method == "Target.createTarget":
            return {"targetId": "owned-target"}
        if method == "Target.attachToTarget":
            return {"sessionId": "owned-session"}
        if method == "Target.closeTarget":
            assert kwargs == {"targetId": "owned-target"}
            if cleanup_fails:
                raise RuntimeError("close failed")
        return {}

    call = Mock(side_effect=cdp)
    monkeypatch.setattr(browser, "ensure_daemon", Mock())
    monkeypatch.setattr(browser, "cdp", call)
    with pytest.raises(RuntimeError) as caught:
        browser.Browser("https://example.test")
    assert caught.value is original
    closes = [c for c in call.call_args_list if c.args[0] == "Target.closeTarget"]
    assert len(closes) == 1
    if cleanup_fails:
        assert "Browser cleanup failed: close failed" in caught.value.__notes__


def test_repeated_close_is_idempotent(monkeypatch):
    owned = browser.Browser.__new__(browser.Browser)
    owned.target = "owned-target"
    cdp = Mock()
    monkeypatch.setattr(browser, "cdp", cdp)
    owned.close()
    owned.close()
    cdp.assert_called_once_with("Target.closeTarget", targetId="owned-target")


def test_navigation_timeout_closes_target(monkeypatch):
    responses = {
        "Target.createTarget": {"targetId": "owned-target"},
        "Target.attachToTarget": {"sessionId": "owned-session"},
    }
    cdp = Mock(side_effect=lambda method, **_kwargs: responses.get(method, {}))
    monkeypatch.setattr(browser, "ensure_daemon", Mock())
    monkeypatch.setattr(browser, "cdp", cdp)
    monkeypatch.setattr(browser.time, "monotonic", Mock(side_effect=[0, 16]))
    with pytest.raises(TimeoutError, match="navigation"):
        browser.Browser("https://example.test")
    assert cdp.call_args.args == ("Target.closeTarget",)
    assert cdp.call_args.kwargs == {"targetId": "owned-target"}


def test_cancellation_during_freshness_prevents_input(monkeypatch):
    owned = browser.Browser.__new__(browser.Browser)
    owned.session = "owned-session"
    owned.fresh = Mock(return_value=True)
    owned.before_action = Mock(side_effect=ValueError("cancelled"))
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(ValueError, match="cancelled"):
        owned.act({"id": "e1", "kind": "click"}, {})
    owned.fresh.assert_called_once()
    operation.assert_not_called()


def test_navigation_error_response_closes_target(monkeypatch):
    responses = {
        "Target.createTarget": {"targetId": "owned-target"},
        "Target.attachToTarget": {"sessionId": "owned-session"},
        "Page.navigate": {"errorText": "net::ERR_NAME_NOT_RESOLVED"},
    }
    cdp = Mock(side_effect=lambda method, **_kwargs: responses.get(method, {}))
    monkeypatch.setattr(browser, "ensure_daemon", Mock())
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="ERR_NAME_NOT_RESOLVED"):
        browser.Browser("https://example.test")
    assert cdp.call_args.args == ("Target.closeTarget",)
    assert cdp.call_args.kwargs == {"targetId": "owned-target"}


@pytest.mark.parametrize("failed_method", [
    "Target.createTarget", "Target.attachToTarget", "Page.enable", "Page.navigate",
])
def test_partial_context_setup_disposes_owned_context(monkeypatch, failed_method):
    original = RuntimeError("interrupted setup")
    responses = {
        "Target.createBrowserContext": {"browserContextId": "owned-context"},
        "Target.createTarget": {"targetId": "owned-target"},
        "Target.attachToTarget": {"sessionId": "owned-session"},
    }

    def call(method, **params):
        if method == failed_method:
            raise original
        return responses.get(method, {})

    cdp = Mock(side_effect=call)
    monkeypatch.setattr(browser, "ensure_daemon", Mock())
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError) as caught:
        browser.Browser("about:blank", fresh_context=True)
    assert caught.value is original
    assert cdp.call_args.args == ("Target.disposeBrowserContext",)
    assert cdp.call_args.kwargs == {"browserContextId": "owned-context"}
    assert not any(c.args[0] == "Target.closeTarget" for c in cdp.call_args_list)


def test_failed_context_disposal_preserves_ownership_for_retry(monkeypatch):
    owned = browser.Browser.__new__(browser.Browser)
    owned.context, owned.target, owned.session = "context", "target", "session"
    cdp = Mock(side_effect=[RuntimeError("dispose failed"), {}])
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="dispose failed"):
        owned.close()
    assert (owned.context, owned.target, owned.session) == ("context", "target", "session")
    assert owned.cleanup_error == "dispose failed"
    owned.close()
    owned.close()
    assert (owned.context, owned.target, owned.session) == (None, None, None)
    assert cdp.call_count == 2
    assert all(c.args == ("Target.disposeBrowserContext",) for c in cdp.call_args_list)


def test_failed_context_creation_never_closes_an_unowned_target(monkeypatch):
    cdp = Mock(side_effect=RuntimeError("context creation failed"))
    monkeypatch.setattr(browser, "ensure_daemon", Mock())
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="context creation failed"):
        browser.Browser("about:blank", fresh_context=True)
    cdp.assert_called_once_with("Target.createBrowserContext", disposeOnDetach=True)


@pytest.mark.parametrize("hijack_after", ["mouseReleased", "keyDown", "keyUp"])
def test_fill_focus_hijack_blocks_text_and_agent_retry(monkeypatch, hijack_after):
    from jev_ultrafast import agent

    events = []
    active = "target"
    values = {"target": "original", "decoy": "untouched"}

    def cdp(method, **params):
        nonlocal active
        events.append((method, params))
        if method == "Runtime.evaluate":
            if len(events) == 1:
                return {"result": {"value": {"x": 20, "y": 20}}}
            return {"result": {"value": active == "target"}}
        if params.get("type") == hijack_after:
            active = "decoy"
        if method == "Input.insertText":
            values[active] = params["text"]
        return {}

    monkeypatch.setattr(browser, "cdp", cdp)
    owned = browser.Browser.__new__(browser.Browser)
    owned.session = "owned-session"
    owned.fresh = Mock(return_value=True)
    action = {"id": "e1", "kind": "fill", "node": 1, "label": "Target", "role": "textbox"}
    page = {
        "actions": [action], "fingerprint": "observed", "text": "Target", "url": "about:blank", "title": "Focus test",
    }
    owned.observe = Mock(return_value=page)
    monkeypatch.setattr(agent, "Browser", Mock(return_value=owned))
    decision = Mock(return_value={
        "choice": "e1", "inline_text": "private text", "model": "local-test", "confidence": 1,
        "probabilities": {"e1": 1}, "latency_ms": 0, "operation": "TYPE_TEXT", "target": "1", "usage": {},
    })
    runner = agent.Agent("about:blank", "Fill Target", decision_fn=decision)
    with pytest.raises(RuntimeError, match="Fill execution uncertain") as caught:
        runner.command("tick")
    assert not isinstance(caught.value, browser.StalePage)
    assert values == {"target": "original", "decoy": "untouched"}
    assert not any(method == "Input.insertText" for method, _ in events)
    assert sum(params.get("type") == "mouseReleased" for _, params in events) == 1
    if hijack_after == "mouseReleased":
        assert not any(method == "Input.dispatchKeyEvent" for method, _ in events)
    assert runner.state["status"] == "blocked"
    assert runner.state["decision"] is None
    assert runner.state["history"] == []
    count = len(events)
    assert runner.command("tick")["status"] == "blocked"
    assert list(runner.run()) == []
    assert len(events) == count
    decision.assert_called_once()


@pytest.mark.parametrize("response", [
    {"exceptionDetails": {"text": "Execution context destroyed"}},
    {"result": {}},
    {"result": {"value": None}},
    {"result": {"value": {"unexpected": "truthy"}}},
])
@pytest.mark.parametrize("guard_index", [0, 1])
def test_fill_focus_check_failure_after_click_is_not_stale(monkeypatch, response, guard_index):
    evaluations = iter([{"result": {"value": {"x": 20, "y": 20}}}] +
                       [{"result": {"value": True}}] * guard_index + [response])
    cdp = Mock(side_effect=lambda method, **_params: next(evaluations) if method == "Runtime.evaluate" else {})
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Fill execution uncertain") as caught:
        browser.browser_operation({
            "operation": "act", "session": "owned-session", "text": "private text",
            "action": {"id": "e1", "kind": "fill", "node": 1},
        })
    assert not isinstance(caught.value, browser.StalePage)
    assert all(call.args[0] != "Input.insertText" for call in cdp.call_args_list)


def test_fill_with_unchanged_focus_keeps_native_input_sequence(monkeypatch):
    evaluations = iter([{"x": 20, "y": 20}, True, True])
    cdp = Mock(side_effect=lambda method, **_params: (
        {"result": {"value": next(evaluations)}} if method == "Runtime.evaluate" else {}
    ))
    monkeypatch.setattr(browser, "cdp", cdp)
    result = browser.browser_operation({
        "operation": "act", "session": "owned-session", "text": "replacement",
        "action": {"id": "e1", "kind": "fill", "node": 1},
    })
    assert result == {"executed": "e1"}
    assert [call.args[0] for call in cdp.call_args_list] == [
        "Runtime.evaluate", "Input.dispatchMouseEvent", "Input.dispatchMouseEvent", "Runtime.evaluate",
        "Input.dispatchKeyEvent", "Input.dispatchKeyEvent", "Runtime.evaluate", "Input.insertText",
    ]
    assert cdp.call_args.kwargs["text"] == "replacement"


@pytest.mark.parametrize("error_type", [RuntimeError, TimeoutError])
def test_fill_focus_transport_failure_is_explicitly_uncertain(monkeypatch, error_type):
    cdp = Mock(side_effect=[
        {"result": {"value": {"x": 20, "y": 20}}}, {}, {}, error_type("read interrupted"),
    ])
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Fill execution uncertain") as caught:
        browser.browser_operation({
            "operation": "act", "session": "owned-session", "text": "private text",
            "action": {"id": "e1", "kind": "fill", "node": 1},
        })
    assert isinstance(caught.value.__cause__, error_type)
    assert all(call.args[0] != "Input.insertText" for call in cdp.call_args_list)
