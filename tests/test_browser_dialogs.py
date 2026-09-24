"""Native dialog guards fail closed before any browser input."""

from unittest.mock import Mock

import pytest

from jev_ultrafast import browser


@pytest.mark.parametrize("kind", ["alert", "confirm", "prompt", "beforeunload"])
def test_pending_dialog_blocks_before_cdp_without_handling(monkeypatch, kind):
    monkeypatch.setattr(browser, "_send", Mock(return_value={"dialog": {"type": kind}}))
    cdp = Mock()
    monkeypatch.setattr(browser, "cdp", cdp)
    owned = browser.Browser.__new__(browser.Browser)
    owned.session = "owned"
    with pytest.raises(browser.DialogBlocked, match=kind):
        owned.call("Runtime.evaluate", expression="1")
    cdp.assert_not_called()


def test_dialog_during_timed_out_input_is_nonretryable(monkeypatch):
    monkeypatch.setattr(browser, "_send", Mock(side_effect=[
        {"dialog": None}, {"dialog": {"type": "confirm"}},
    ]))
    cdp = Mock(side_effect=TimeoutError("reply held by dialog"))
    monkeypatch.setattr(browser, "cdp", cdp)
    owned = browser.Browser.__new__(browser.Browser)
    owned.session = "owned"
    with pytest.raises(browser.DialogBlocked) as caught:
        owned.call("Input.dispatchMouseEvent", type="mouseReleased")
    assert not isinstance(caught.value, browser.StalePage)
    assert cdp.call_args.kwargs["_response_timeout"] == 5
    assert cdp.call_count == 1


def test_dialog_after_successful_reply_still_blocks(monkeypatch):
    monkeypatch.setattr(browser, "_send", Mock(side_effect=[
        {"dialog": None}, {"dialog": {"type": "alert"}},
    ]))
    cdp = Mock(return_value={})
    monkeypatch.setattr(browser, "cdp", cdp)
    owned = browser.Browser.__new__(browser.Browser)
    owned.session = "owned"
    with pytest.raises(browser.DialogBlocked):
        owned.call("Input.dispatchMouseEvent", type="mouseReleased")
    assert cdp.call_count == 1


def test_transport_timeout_without_dialog_is_not_retried(monkeypatch):
    monkeypatch.setattr(browser, "_send", Mock(return_value={"dialog": None}))
    error = TimeoutError("reply unavailable")
    cdp = Mock(side_effect=error)
    monkeypatch.setattr(browser, "cdp", cdp)
    owned = browser.Browser.__new__(browser.Browser)
    owned.session = "owned"
    with pytest.raises(TimeoutError) as caught:
        owned.call("Runtime.evaluate", expression="1")
    assert caught.value is error
    assert cdp.call_count == 1


def test_post_input_settle_does_not_swallow_dialog(monkeypatch):
    owned = browser.Browser.__new__(browser.Browser)
    owned.after_input = {"kind": "click", "node": 1}
    owned.call = Mock(side_effect=browser.DialogBlocked("inspect dialog"))
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(browser.DialogBlocked):
        owned.observe()
    operation.assert_not_called()


def test_dialog_probe_failure_prevents_input(monkeypatch):
    monkeypatch.setattr(browser, "_send", Mock(side_effect=TimeoutError("probe unavailable")))
    cdp = Mock()
    monkeypatch.setattr(browser, "cdp", cdp)
    owned = browser.Browser.__new__(browser.Browser)
    owned.session = "owned"
    with pytest.raises(TimeoutError):
        owned.call("Input.insertText", text="private")
    cdp.assert_not_called()
