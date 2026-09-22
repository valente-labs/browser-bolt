"""Packaged, synthetic-only reference browser host using the official MCP stdio client."""

import argparse
import asyncio
import concurrent.futures
import json
import math
import os
import signal
import sys
import threading
import time
from pathlib import Path

from .benchmark import GOALS, fixture_bytes, fixture_server, observe_result, state_metrics, verify
from .mcp_server import PROFILE_NAMES, SCHEMAS, list_profiles, normalize_native_field_context, normalize_native_state


class HostStopped(RuntimeError):
    """A fixed host failure code, safe to include in local reports."""


class Control:
    def __init__(self, timeout):
        self.cancelled = threading.Event()
        self.deadline = time.monotonic() + timeout
        self.agent = None

    def cancel(self):
        self.cancelled.set()
        if self.agent is not None:
            self.agent.cancel()

    def check(self):
        if self.cancelled.is_set():
            raise HostStopped("cancelled")
        if time.monotonic() >= self.deadline:
            raise HostStopped("deadline_exceeded")


def child_environment(profile=None, environ=None):
    """Only selected provider credentials supplement the SDK's safe system environment."""
    environ = os.environ if environ is None else environ
    if profile is None:
        return {}
    required = next(p for p in list_profiles()["profiles"] if p["profile"] == profile)["required_environment"]
    missing = [key for key in required if not environ.get(key, "").strip()]
    if missing:
        raise HostStopped("missing_environment:" + ",".join(missing))
    return {key: environ[key] for key in required}


def check_url(url, allowed_url):
    if url != allowed_url:
        raise HostStopped("outside_fixture_scope")


def reject_result(code, result):
    error = HostStopped(code)
    calls = result.get("model_calls", []) if isinstance(result, dict) else []
    error.routing = {"model_calls": calls}
    if calls:
        error.model_call = calls[-1]
    raise error


def tool_result(response):
    result = response.structured_content
    if response.is_error or not isinstance(result, dict) or result.get("ok") is not True:
        reject_result("mcp_tool_failed", result)
    return result


class HostAdapter:
    """Synchronous Agent callbacks bridged to one live asynchronous MCP session."""

    def __init__(self, client, loop, profile, allowed_url, control):
        self.client, self.loop, self.profile = client, loop, profile
        self.allowed_url, self.control = allowed_url, control

    def call(self, name, arguments):
        self.control.check()
        future = asyncio.run_coroutine_threadsafe(self.client.call_tool(name, arguments), self.loop)
        expires = min(self.control.deadline, time.monotonic() + 45)
        try:
            while True:
                self.control.check()
                remaining = expires - time.monotonic()
                if remaining <= 0:
                    raise HostStopped("mcp_timeout")
                try:
                    response = future.result(timeout=min(0.1, remaining))
                    break
                except concurrent.futures.TimeoutError:
                    if future.done():
                        raise HostStopped("mcp_timeout") from None
                    continue
            self.control.check()
            return tool_result(response)
        finally:
            if not future.done():
                future.cancel()

    def choose(self, state, goal, history):
        check_url(state["url"], self.allowed_url)
        result = self.call("choose_browser_action", {
            "profile": self.profile, "state": normalize_native_state(state), "goal": goal,
            "history": [
                {k: v for k, v in item.items() if k in {"action", "kind", "text", "page_changed"} and v is not None}
                for item in history[-10:]
            ],
        })
        if result.get("observation_fingerprint") != state["fingerprint"]:
            reject_result("mismatched_observation", result)
        selected, operation = result.get("choice"), result.get("operation")
        if selected in {"DONE", "BLOCKED"}:
            valid = selected == operation
        else:
            action = next((a for a in state["actions"] if a["id"] == selected), None)
            valid = action is not None and operation == {
                "click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT", "wait": "WAIT", "scroll": selected.upper(),
            }.get(action["kind"])
        if not valid:
            reject_result("invalid_observed_choice", result)
        calls = result.get("model_calls", [])
        return {
            **{k: result[k] for k in ("operation", "choice", "target", "inline_text") if k in result},
            "model": calls[-1].get("model", "unknown") if calls else "unknown",
            "latency_ms": result.get("latency_ms", 0), "confidence": None, "probabilities": {},
            "usage": {}, "routing": {"model_calls": calls},
        }

    def field_text(self, context):
        check_url(context["page"]["url"], self.allowed_url)
        result = self.call("write_browser_field", {
            "profile": self.profile, "context": normalize_native_field_context(context),
        })
        if (result.get("observation_fingerprint") != context["page"]["fingerprint"]
                or result.get("field_id") != context["field"]["id"]):
            reject_result("mismatched_field", result)
        return result["text"], result["model_calls"][0]


def run_fixture(args, base, adapter, control, agent_factory=None):
    """Own and close one tab; never retry a failed mutation or cancelled run."""
    if agent_factory is None:
        from .agent import Agent
        agent_factory = Agent
    url = f"{base}/{args.task}.html"
    row = {"task": args.task, "profile": args.profile, "success": False, "verified": False, "status": "setup_failed"}
    agent = None
    started = time.monotonic()
    try:
        control.check()
        agent = agent_factory(
            url, GOALS[args.task], decision_fn=adapter.choose, text_fn=adapter.field_text,
            timeout=max(0.001, control.deadline - time.monotonic()), max_ticks=args.max_steps * 4,
        )
        control.agent = agent

        def guard():
            control.check()
            agent._check_running()
            check_url(agent.browser.evaluate("location.href"), url)
            if len(agent.state["history"]) >= args.max_steps:
                raise HostStopped("step_budget_exhausted")

        agent.browser.before_action = guard
        control.check()
        check_url(agent.state["page"]["url"], url)
        row["setup_seconds"] = time.monotonic() - started
        for _ in agent.run():
            control.check()
        row["status"] = agent.state["status"]
        if agent.state.get("stop_reason"):
            row["stop_reason"] = agent.state["stop_reason"]
        control.check()
        # Fixed fixture DOM outcome, independent of the model's DONE decision.
        check_url(agent.browser.evaluate("location.href"), url)
        final_page = observe_result(agent.browser)
        check_url(final_page["url"], url)
        control.check()
        row["verified"] = verify(args.task, final_page)
        row["success"] = row["status"] == "done" and row["verified"]
    except Exception as error:
        row["status"] = "stopped"
        row["error"] = str(error) if isinstance(error, HostStopped) else type(error).__name__
    finally:
        if agent is not None:
            row.update(state_metrics(agent.state))
            try:
                agent.close()
            except Exception as error:
                row["cleanup_error"] = type(error).__name__
                row["success"] = False
        control.agent = None
        row["wall_seconds"] = time.monotonic() - started
    return row


async def run(args, *, client_factory=None, environ=None, agent_factory=None, control=None):
    # Missing keys fail before starting a subprocess, web server or browser.
    env = child_environment(args.profile if args.live else None, environ)
    if args.live and args.profile == "jev" and args.task != "choice":
        raise HostStopped("jev_requires_choice_task")
    from mcp import Client, StdioServerParameters

    factory = client_factory or Client
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "jev_ultrafast.mcp_server"], env=env)
    async with factory(parameters, read_timeout_seconds=45) as client:
        tools = await client.list_tools()
        if {tool.name for tool in tools.tools} != set(SCHEMAS):
            raise HostStopped("unexpected_mcp_tools")
        profiles = tool_result(await client.call_tool("list_profiles", {}))
        if not args.live:
            return {
                "mode": "preflight", "ok": True, "transport": "official MCP SDK / stdio",
                "tools": sorted(SCHEMAS), "profiles": profiles["profiles"],
                "fixtures": sorted(GOALS), "browser": "not contacted", "provider_calls": 0,
                "next": "Connect Browser Harness to a dedicated synthetic Chrome profile; then use --live.",
            }
        control = control or Control(args.timeout)
        loop = asyncio.get_running_loop()
        installed_signals = []
        # Signal cancellation rather than abandoning a thread that can still mutate a tab.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                previous_handler = signal.getsignal(sig)
                loop.add_signal_handler(sig, control.cancel)
                installed_signals.append((sig, previous_handler))
            except (NotImplementedError, RuntimeError):
                pass
        try:
            with fixture_server(fixture_bytes()) as base:
                adapter = HostAdapter(client, loop, args.profile, f"{base}/{args.task}.html", control)
                worker = asyncio.create_task(
                    asyncio.to_thread(run_fixture, args, base, adapter, control, agent_factory)
                )
                try:
                    row = await asyncio.shield(worker)
                except asyncio.CancelledError:
                    control.cancel()
                    # Keep the MCP session and fixture alive until the browser thread has closed its tab.
                    await asyncio.shield(worker)
                    raise
        finally:
            for sig, previous_handler in installed_signals:
                loop.remove_signal_handler(sig)
                signal.signal(sig, previous_handler)
        return {"mode": "live", "transport": "official MCP SDK / stdio + native Browser Harness", "row": row}


def bounded_steps(value):
    number = int(value)
    if not 1 <= number <= 30:
        raise argparse.ArgumentTypeError("max-steps must be between 1 and 30")
    return number


def bounded_timeout(value):
    number = float(value)
    if not math.isfinite(number) or not 1 <= number <= 300:
        raise argparse.ArgumentTypeError("timeout must be between 1 and 300 seconds")
    return number


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    modes = result.add_mutually_exclusive_group()
    modes.add_argument(
        "--preflight", action="store_true", help="default: verify real stdio, tools and profiles, no keys"
    )
    modes.add_argument("--list-profiles", action="store_true", help="same credential-free MCP preflight")
    modes.add_argument("--live", action="store_true", help="run one synthetic local fixture; incurs provider usage")
    result.add_argument("--profile", choices=PROFILE_NAMES, default="jev_qwen_openrouter")
    result.add_argument("--task", choices=GOALS, default="choice")
    result.add_argument("--max-steps", type=bounded_steps, default=20)
    result.add_argument("--timeout", type=bounded_timeout, default=120.0, help="cooperative run deadline in seconds")
    result.add_argument("--output", type=Path, help="optional local JSON report; existing files are not overwritten")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.output and args.output.exists():
        print(json.dumps({"ok": False, "error": "output_exists"}))
        return 2
    if args.live:
        print("Live synthetic run: provider requests incur usage charges. Ctrl-C cancels safely.", file=sys.stderr)
    try:
        result = asyncio.run(run(args))
    except ImportError:
        result = {"ok": False, "error": "missing_dependency", "next": "Install this package with the [mcp] extra."}
    except Exception as error:
        result = {"ok": False, "error": str(error) if isinstance(error, HostStopped) else type(error).__name__}
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as output:
            output.write(rendered)
    print(rendered, end="")
    if not args.live or "row" not in result:
        return 0 if result.get("ok") else 2
    row = result["row"]
    return 0 if row.get("success") else 130 if row.get("error") == "cancelled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
