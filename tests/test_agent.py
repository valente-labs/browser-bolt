"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(
        0,
        {
            "id": "toggle",
            "kind": "click",
            "label": "Free cancellation",
            "node": 30,
            "role": "checkbox",
            "checked": "true",
            "selected": False,
        },
    )

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


@pytest.mark.parametrize("kind", ["click", "select"])
@pytest.mark.parametrize("wait_between", [False, True])
def test_new_decision_cannot_repeat_mutation_on_unchanged_page(runner, kind, wait_between):
    p = runner.state["page"]
    p["actions"][2].update(kind=kind, value="confirmed" if kind == "select" else "")
    p["fingerprint"] = fingerprint(p)
    runner.decision_fn = Mock(return_value=decision("e3"))
    runner.command("tick")
    if wait_between:
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": p["fingerprint"]})
    snapshot = runner.command("tick")
    mutations = [c for c in runner.state["browser"].act.call_args_list if c.args[0]["kind"] != "wait"]
    assert len(mutations) == 1
    assert len(snapshot["history"]) == 1 + int(wait_between)
    assert snapshot["status"] == "blocked"
    assert snapshot["stop_reason"] == "duplicate_mutation"
    assert snapshot["decision"] is None
    assert list(runner.run()) == []
    assert runner.decision_fn.call_count == 2


def test_duplicate_guard_survives_failed_post_action_observation(runner):
    runner.decision_fn = Mock(return_value=decision("e3"))
    runner.state["browser"].observe.side_effect = StalePage("Observation unavailable")
    runner.command("tick")
    assert runner.state["history"][0]["page_changed"] is None
    runner.state["browser"].observe.side_effect = None
    snapshot = runner.command("tick")
    runner.state["browser"].act.assert_called_once()
    assert len(snapshot["history"]) == 1
    assert snapshot["stop_reason"] == "duplicate_mutation"


@pytest.mark.parametrize("change_after_decision", [False, True])
def test_duplicate_guard_allows_action_after_changed_observation(runner, change_after_decision):
    runner.decision_fn = Mock(return_value=decision("e3"))
    runner.command("tick")
    changed = deepcopy(runner.state["page"])
    changed["text"] = "A new confirmation is ready"
    changed["fingerprint"] = fingerprint(changed)
    runner.state["browser"].observe.return_value = changed
    runner.state["browser"].fresh.side_effect = [True, False] if change_after_decision else [False]
    snapshot = runner.command("tick")
    if change_after_decision:
        assert snapshot["status"] == "ready"
        assert snapshot["decision"] is None
        assert snapshot["page"] == changed
        runner.state["browser"].act.assert_called_once()
        runner.state["browser"].fresh.side_effect = None
        snapshot = runner.command("tick")
    assert snapshot["status"] == "ready"
    assert runner.state["browser"].act.call_count == 2
    assert len(snapshot["history"]) == 2


def test_duplicate_guard_allows_different_target_on_unchanged_page(runner):
    runner.decision_fn = Mock(side_effect=[decision("e3"), decision("e2")])
    runner.command("tick")
    snapshot = runner.command("tick")
    assert runner.state["browser"].act.call_count == 2
    assert snapshot["status"] == "ready"


def test_duplicate_guard_does_not_record_rejected_stale_action(runner):
    runner.decision_fn = Mock(return_value=decision("e3"))
    runner.state["browser"].act.side_effect = [StalePage("Before input"), None]
    runner.command("tick")
    assert runner.state["history"] == []
    snapshot = runner.command("tick")
    assert len(snapshot["history"]) == 1
    assert snapshot["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation(
            {
                "operation": "act",
                "session": "test",
                "action": {
                    "id": "e1",
                    "kind": "select",
                    "node": 1,
                    "value": "Design",
                },
            }
        )
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def test_fallback_inline_text_avoids_second_model_call(runner, monkeypatch):
    helper = Mock(side_effect=AssertionError("Must reuse validated fallback text"))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["decision"].update(inline_text="book", model="qwen-3.8-27b", confidence=None, probabilities={})
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    helper.assert_not_called()
    runner.state["browser"].act.assert_called_once_with(
        runner.state["page"]["actions"][0], runner.state["page"], text="book"
    )
    assert runner.state["history"][0]["confidence"] is None
    assert runner.state["history"][0]["probability"] is None
    assert runner.state["text_calls"] == []  # Already billed/logged in the decision call.


def test_fallback_inline_text_still_checks_freshness(runner, monkeypatch):
    helper = Mock()
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["decision"].update(inline_text="book", model="qwen-3.8-27b")
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    helper.assert_not_called()
    runner.state["browser"].act.assert_not_called()


def test_mutation_failure_is_consumed_and_stops_run(runner, monkeypatch):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].act.side_effect = RuntimeError("Mutation may have executed")
    with pytest.raises(RuntimeError):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["decision"] is None
    assert runner.state["status"] == "blocked"
    assert list(runner.run()) == []
    runner.state["browser"].act.assert_called_once()


def test_qwen_inline_text_not_cached_as_a_field_helper(runner, monkeypatch):
    runner.state["decision"].update(inline_text="first", model="qwen")
    runner.state["browser"].act.side_effect = [StalePage("Before any input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.pending_text is None
    helper = Mock(return_value=("second", {"model": "text", "latency_ms": 5, "usage": {}}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    helper.assert_called_once()
    assert runner.state["browser"].act.call_args.kwargs["text"] == "second"


def test_failed_decision_retains_call_telemetry(runner, monkeypatch):
    error = ValueError("Invalid response")
    error.routing = {"model_calls": [{"provider": "qwen", "success": False, "usage": {"total_tokens": 15}}]}
    monkeypatch.setattr(loop, "choose", Mock(side_effect=error))
    with pytest.raises(ValueError):
        runner.command("predict")
    assert runner.state["status"] == "blocked"
    assert runner.state["decision"] is None
    assert runner.state["decisions"][0]["routing"] == error.routing
    runner.state["browser"].act.assert_not_called()


def test_failed_field_retains_call_telemetry(runner, monkeypatch):
    call = {"provider": "text", "success": False, "usage": {"total_tokens": 15}}
    error = model.InvalidModelResponse("Invalid text", call)
    monkeypatch.setattr(loop, "field_text", Mock(side_effect=error))
    with pytest.raises(ValueError):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "blocked"
    assert runner.state["text_calls"][0]["usage"] == {"total_tokens": 15}
    runner.state["browser"].act.assert_not_called()


def test_repeated_predecision_staleness_has_independent_budget(runner, monkeypatch):
    runner.max_ticks = 3
    runner.state["browser"].fresh.side_effect = StalePage("navigating")
    runner.state["browser"].observe.side_effect = StalePage("still navigating")
    provider = Mock()
    monkeypatch.setattr(loop, "choose", provider)
    snapshots = list(runner.run())
    assert len(snapshots) == 4
    assert snapshots[-1]["stop_reason"] == "tick_budget_exhausted"
    assert runner.state["status"] == "blocked"
    assert runner.state["decisions"] == runner.state["history"] == []
    provider.assert_not_called()
    runner.state["browser"].act.assert_not_called()


def test_cancel_during_synchronous_provider_prevents_mutation(runner):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    entered, release = Event(), Event()

    def provider(*_args):
        entered.set()
        assert release.wait(2)
        return decision("e3")

    runner.decision_fn = provider
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.command, "tick")
        try:
            assert entered.wait(2)
            runner.cancel()
            runner.cancel()
            assert not future.done()  # Cancellation does not claim to interrupt synchronous I/O.
        finally:
            release.set()
        snapshot = future.result(timeout=2)
    assert snapshot["stop_reason"] == "cancelled"
    assert len(snapshot["decisions"]) == 1  # Retain the completed provider call for accounting.
    runner.state["browser"].act.assert_not_called()


@pytest.mark.parametrize("boundary", ["decision", "freshness", "text"])
def test_deadline_prevents_next_provider_or_mutation(runner, monkeypatch, boundary):
    now = [10.0]
    runner._deadline = 11.0
    monkeypatch.setattr(loop.time, "monotonic", lambda: now[0])
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 1}))
    runner.text_fn = helper

    def expire(result):
        now[0] = 12.0
        return result

    if boundary == "decision":
        runner.decision_fn = lambda *_args: expire(decision())
        snapshot = runner.command("tick")
    elif boundary == "freshness":
        runner.state["browser"].fresh.side_effect = lambda *_args: expire(True)
        snapshot = runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    else:
        helper.side_effect = lambda *_args: expire(("book", {"model": "test", "latency_ms": 1}))
        snapshot = runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert snapshot["stop_reason"] == "deadline_exceeded"
    assert helper.call_count == int(boundary == "text")
    runner.state["browser"].act.assert_not_called()


def test_cancellation_after_input_preserves_history_without_retry(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].act.side_effect = lambda *_args, **_kwargs: runner.cancel()
    snapshot = runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert snapshot["stop_reason"] == "cancelled"
    assert len(snapshot["history"]) == 1
    assert snapshot["history"][0]["page_changed"] is None
    assert list(runner.run()) == []
    runner.command("tick")
    runner.state["browser"].act.assert_called_once()
    runner.state["browser"].observe.assert_not_called()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "120"])
def test_invalid_timeout_rejected_before_browser_creation(monkeypatch, timeout):
    browser = Mock()
    monkeypatch.setattr(loop, "Browser", browser)
    with pytest.raises(ValueError, match="timeout"):
        loop.Agent("https://example.test", "Find a book", timeout=timeout)
    browser.assert_not_called()


@pytest.mark.parametrize("failure", ["observe", "mkdir", "write_bytes"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_constructor_cleanup_preserves_original_error(monkeypatch, tmp_path, failure, cleanup_fails):
    from pathlib import Path

    original = RuntimeError("original setup failure")
    browser = Mock(observe=Mock(return_value={**page(), "screenshot": ""}))
    if cleanup_fails:
        browser.close.side_effect = RuntimeError("cleanup failed")
    monkeypatch.setattr(loop, "Browser", Mock(return_value=browser))
    if failure == "observe":
        browser.observe.side_effect = original
    else:
        monkeypatch.setattr(Path, failure, Mock(side_effect=original))
    with pytest.raises(RuntimeError) as caught:
        loop.Agent("https://example.test", "Find a book", record_dir=tmp_path / "record")
    assert caught.value is original
    browser.close.assert_called_once()
    if cleanup_fails:
        assert "Agent cleanup failed: cleanup failed" in caught.value.__notes__


def test_close_is_idempotent_and_signals_cancellation(monkeypatch):
    browser = Mock(observe=Mock(return_value=page()))
    monkeypatch.setattr(loop, "Browser", Mock(return_value=browser))
    agent = loop.Agent("https://example.test", "Find a book")
    agent.close()
    agent.close()
    browser.close.assert_called_once()
    assert agent.command("tick")["stop_reason"] == "cancelled"


def test_context_cleanup_does_not_mask_execution_error(monkeypatch):
    browser = Mock(observe=Mock(return_value=page()), close=Mock(side_effect=RuntimeError("close failed")))
    monkeypatch.setattr(loop, "Browser", Mock(return_value=browser))
    original = RuntimeError("execution failed")
    with pytest.raises(RuntimeError) as caught:
        with loop.Agent("https://example.test", "Find a book") as agent:
            raise original
    assert caught.value is original
    assert agent.cleanup_error == "close failed"
    assert caught.value.__notes__ == ["Agent cleanup failed: close failed"]


def test_terminal_stop_reason_survives_close_and_repeated_tick(runner):
    runner._deadline = 0
    assert runner.command("tick")["stop_reason"] == "deadline_exceeded"
    runner.cancel()
    assert runner.command("tick")["stop_reason"] == "deadline_exceeded"
