"""The Chrome adapter retains paid decisions if a subsequent writer fails."""

from unittest.mock import Mock

from local.comparison_probe import decide


def test_writer_failure_keeps_decision_and_failed_call():
    decision = {"operation": "TYPE_TEXT", "choice": "title", "routing": {"model_calls": [{"model": "jev"}]}}
    error = ValueError("provider body must not escape")
    error.model_call = {"model": "writer", "success": False, "usage": {"cost": 0.01}}
    profile = Mock()
    profile.choose.return_value = decision
    profile.field_text.side_effect = error
    state = {
        "url": "http://example.test",
        "title": "Form",
        "text": "Title",
        "actions": [{"id": "title", "node": 1, "label": "Title", "role": "textbox", "value": ""}],
    }
    result = decide(profile, state, "Write a title", [])
    assert result["decision"] == decision
    assert result["model_call"] == error.model_call
    assert result["error"] == "ValueError"
    assert "provider body" not in str(result)


def test_inline_text_does_not_call_writer():
    profile = Mock()
    profile.choose.return_value = {"operation": "TYPE_TEXT", "choice": "title", "inline_text": "Example"}
    result = decide(profile, {}, "Write a title", [])
    assert result["text"] == "Example"
    profile.field_text.assert_not_called()
