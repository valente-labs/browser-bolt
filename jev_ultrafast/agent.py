"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import math
import time
from pathlib import Path
from threading import Event

from .browser import Browser, StalePage
from .model import action_space, field_context, field_text
from .policy import choose
from .questions import MAX_STEPS


class RunStopped(ValueError):
    """Execution stopped at a cooperative boundary, not inside synchronous I/O."""


class Agent:
    def __init__(
        self, url, goals, *, record_dir=None, screenshots=False, decision_fn=None, text_fn=None,
        timeout=120.0, max_ticks=MAX_STEPS * 4,
    ):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        if (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        if type(max_ticks) is not int or max_ticks <= 0:
            raise ValueError("max_ticks must be a positive integer")
        self._cancelled = Event()
        self._deadline = time.monotonic() + timeout
        self.max_ticks = max_ticks
        self._closed = False
        self.cleanup_error = None
        plan = [task]
        self.pending_text = None
        self.decision_fn = decision_fn
        self.text_fn = text_fn
        self.browser = Browser(url)
        try:
            self.browser.before_action = self._check_running
            self.record_dir = Path(record_dir) if record_dir else None
            self.screenshots = screenshots or bool(record_dir)
            page = self.browser.observe(screenshot=self.screenshots)
            self.state = dict(
                browser=self.browser,
                goal="\n".join(plan),
                page=page,
                decision=None,
                history=[],
                status="ready",
                plan=plan,
                plan_index=0,
                decisions=[],
                text_calls=[],
                elapsed_ms=0,
                started_at=None,
                record=bool(self.record_dir),
                ticks=0,
                stop_reason=None,
            )
            if self.record_dir:
                self.record_dir.mkdir(parents=True, exist_ok=True)
                (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup_error:
                self.cleanup_error = str(cleanup_error)
                error.add_note(f"Agent cleanup failed: {cleanup_error}")
            raise

    def cancel(self):
        """Signal without the caller's command lock; in-flight I/O remains synchronous."""
        if not hasattr(self, "_cancelled"):
            self._cancelled = Event()
        self._cancelled.set()

    def _stop(self, reason):
        reason = self.state.get("stop_reason") or reason
        self.state.update(status="blocked", stop_reason=reason, decision=None)
        self.pending_text = None
        raise RunStopped(reason)

    def _check_running(self):
        if getattr(self, "_cancelled", None) is not None and self._cancelled.is_set():
            self._stop("cancelled")
        # Legacy test doubles built with __new__ get a bounded execution window too.
        if not hasattr(self, "_deadline"):
            self._deadline = time.monotonic() + 120.0
        if time.monotonic() >= self._deadline:
            self._stop("deadline_exceeded")

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def command(self, name, body=None):
        if name == "cancel":
            self.cancel()
            return {"cancel_requested": True}
        if name == "tick" and self.state["status"] in {"done", "blocked"}:
            return self.snapshot()
        try:
            self._check_running()
            return self._command(name, body)
        except RunStopped:
            started_at = self.state.get("started_at")
            if started_at is not None:
                self.state["elapsed_ms"] = round((time.perf_counter() - started_at) * 1000)
            return self.snapshot()

    def _command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            if state["status"] in {"done", "blocked"}:
                return self.snapshot()
            try:
                self._command("predict", {})
                return self._command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                self._check_running()
                state["decision"] = None
                state["status"] = "ready"
                try:
                    state["page"] = state["browser"].observe(screenshot=self.screenshots)
                except StalePage:
                    pass  # A later tick may observe again, within the independent tick budget.
                self._check_running()
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            state["ticks"] = state.get("ticks", 0) + 1
            if state["ticks"] > getattr(self, "max_ticks", MAX_STEPS * 4):
                self._stop("tick_budget_exhausted")
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            call_count = sum(len(d.get("routing", {}).get("model_calls", [d])) for d in state["decisions"])
            call_count += len(state["text_calls"])
            # Reserve a Jev call, fallback and field helper before starting a step.
            if call_count + 3 > MAX_STEPS * 2:
                state["status"] = "blocked"
                raise ValueError("Reached the demo's model-call budget")
            try:
                self._check_running()
                decision_fn = getattr(self, "decision_fn", None) or choose
                state["decision"] = decision_fn(state["page"], state["goal"], state["history"])
            except Exception as error:
                if getattr(error, "routing", None):
                    state["decisions"].append({"error": str(error), "routing": error.routing})
                state["status"] = "blocked"
                raise
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            self._check_running()
            state["status"] = "predicted"
        elif name == "act":
            self._check_running()
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                self._check_running()
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            # A new decision can repeat an already executed input without any visible change.
            # Keep this evidence even when the post-action observation failed or a WAIT intervened.
            if action["kind"] in {"click", "select"} and any(
                h.get("fingerprint") == page["fingerprint"] and h["choice"] == selected
                for h in state["history"]
            ):
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed before duplicate check. Choose again.")
                self._stop("duplicate_mutation")
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {MAX_STEPS}-action demo budget")
            text, helper = None, None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(state["goal"], action, page, state["history"])
                if decision.get("inline_text") is not None:
                    text = decision["inline_text"]
                    helper = {"model": decision["model"], "latency_ms": 0, "usage": {}, "included_in_decision": True}
                    # Inline text belongs to this whole decision, not a reusable helper request.
                    self.pending_text = None
                elif self.pending_text and self.pending_text[0] == context:
                    _, text, helper = self.pending_text
                else:
                    try:
                        self._check_running()
                        text_fn = getattr(self, "text_fn", None) or field_text
                        text, helper = text_fn(context)
                    except Exception as error:
                        call = getattr(error, "model_call", None)
                        if call:
                            state["text_calls"].append({**call, "field": action["label"], "error": str(error)})
                        state["status"] = "blocked"
                        raise
                    self.pending_text = (context, text, helper)
                    state["text_calls"].append({**helper, "field": action["label"], "value": text})
            # Browser.act checks freshness immediately before input, including after text generation.
            try:
                self._check_running()
                state["browser"].act(action, page, text=text)
            except StalePage:
                raise  # Browser guarantees this rejection happens before any input.
            except Exception:
                self.pending_text = None
                state["status"] = "blocked"  # Mutation may already have happened. Never retry it.
                raise
            self.pending_text = None
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "fingerprint": page["fingerprint"],
                    "probability": decision["probabilities"].get(selected),
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "routing": decision.get("routing"),
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            self._check_running()
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            self._check_running()
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            state["status"] = (
                "blocked"
                if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
                else "ready"
            )
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        self.cancel()
        if not getattr(self, "_closed", False):
            try:
                self.browser.close()
            except BaseException as error:
                self.cleanup_error = str(error)
                raise
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, _type, error, _traceback):
        try:
            self.close()
        except BaseException as cleanup_error:
            if error is None:
                raise
            error.add_note(f"Agent cleanup failed: {cleanup_error}")
