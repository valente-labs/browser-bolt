"""Owned-target lifecycle and cooperative input boundary tests. No browser access."""

from unittest.mock import Mock

import pytest

from jev_ultrafast import browser


@pytest.mark.parametrize("failed_method", [
    "Target.attachToTarget", "Emulation.setDeviceMetricsOverride", "Emulation.setFocusEmulationEnabled",
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
