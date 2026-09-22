"""Reference host transport and ownership checks. No provider calls or real browser."""

import asyncio
import json
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jev_ultrafast import mcp_host as host


@pytest.fixture
def args():
    return host.parser().parse_args(["--live", "--task", "choice"])


def response(result):
    return SimpleNamespace(is_error=False, structured_content=result)


class ClientDouble:
    def __init__(self, parameters, **kwargs):
        self.parameters, self.kwargs = parameters, kwargs
        self.closed = False
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in host.SCHEMAS])

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        assert name == "list_profiles"
        return response(host.list_profiles())


def test_real_stdio_preflight_without_credentials(monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    result = asyncio.run(host.run(host.parser().parse_args([])))
    assert result["ok"] and result["provider_calls"] == 0
    assert result["browser"] == "not contacted"
    assert result["tools"] == sorted(host.SCHEMAS)
    assert "onboarding" in result["fixtures"]


def test_launch_parameters_and_minimum_environment():
    pytest.importorskip("mcp")
    instances = []

    def factory(*a, **kw):
        instances.append(ClientDouble(*a, **kw))
        return instances[-1]

    result = asyncio.run(host.run(host.parser().parse_args(["--list-profiles"]), client_factory=factory,
                                  environ={"OPENROUTER_API_KEY": "never-used", "UNRELATED_SECRET": "never-used"}))
    client = instances[0]
    assert result["ok"] and client.closed
    assert client.parameters.command == sys.executable
    assert client.parameters.args == ["-m", "jev_ultrafast.mcp_server"]
    assert client.parameters.env == {}
    assert client.calls == [("list_profiles", {})]


def test_only_selected_keys_are_forwarded():
    env = {"OPENROUTER_API_KEY": "test-openrouter", "CEREBRAS_API_KEY": "test-cerebras", "OTHER_KEY": "test-other"}
    assert host.child_environment("jev_qwen_openrouter", env) == {"OPENROUTER_API_KEY": "test-openrouter"}
    assert host.child_environment("qwen", env) == {"CEREBRAS_API_KEY": "test-cerebras"}
    assert set(host.child_environment("jev_qwen", env)) == {"OPENROUTER_API_KEY", "CEREBRAS_API_KEY"}


def test_missing_key_fails_before_launch(args):
    factory = Mock()
    with pytest.raises(host.HostStopped, match="missing_environment:OPENROUTER_API_KEY"):
        asyncio.run(host.run(args, client_factory=factory, environ={}))
    factory.assert_not_called()


def test_jev_typing_task_fails_before_launch(args):
    args.profile, args.task = "jev", "onboarding"
    factory = Mock()
    with pytest.raises(host.HostStopped, match="jev_requires_choice_task"):
        asyncio.run(host.run(args, client_factory=factory, environ={"OPENROUTER_API_KEY": "test"}))
    factory.assert_not_called()


@pytest.mark.parametrize("arguments", [["--url", "https://example.com"], ["--task", "arbitrary"],
                                      ["--goal", "do anything"], ["--max-steps", "0"],
                                      ["--timeout", "nan"], ["--timeout", "301"]])
def test_cli_rejects_unscoped_or_unbounded_input(arguments):
    with pytest.raises(SystemExit):
        host.parser().parse_args(arguments)


class BrowserDouble:
    def __init__(self, url):
        self.url, self.closed, self.mutations = url, False, 0
        self.result = {"heading": "Preference saved", "paragraphs": ["Express"]}

    def evaluate(self, expression):
        return self.url if expression == "location.href" else self.result

    def observe(self, **_):
        return {"url": self.url, "fingerprint": "fixture"}

    def close(self):
        self.closed = True


class AgentDouble:
    instances = []

    def __init__(self, url, goal, **kwargs):
        self.__class__.instances.append(self)
        self.browser = BrowserDouble(url)
        self.goal, self.kwargs = goal, kwargs
        self.state = {"status": "ready", "page": {"url": url}, "history": [], "decisions": [], "text_calls": []}
        self.cancelled = False

    def _check_running(self):
        if self.cancelled:
            raise host.HostStopped("cancelled")

    def run(self):
        self.browser.before_action()
        self.browser.mutations += 1
        self.state["history"].append({"kind": "click"})
        self.state["status"] = "done"
        yield self.state

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.cancel()
        self.browser.close()


def trial(args, factory=AgentDouble, control=None):
    return host.run_fixture(args, "http://127.0.0.1:9999", Mock(), control or host.Control(10), factory)


def test_native_agent_ownership_bounds_and_independent_outcome(args):
    result = trial(args)
    agent = AgentDouble.instances[-1]
    assert result["success"] and result["verified"]
    assert agent.browser.closed and agent.browser.mutations == 1
    assert agent.goal == host.GOALS["choice"]
    assert agent.kwargs["max_ticks"] == args.max_steps * 4
    assert 0 < agent.kwargs["timeout"] <= 10


def test_done_alone_is_not_success(args):
    class FalseDone(AgentDouble):
        def run(self):
            self.browser.result = {"heading": "Not saved", "paragraphs": []}
            yield from super().run()

    result = trial(args, FalseDone)
    assert result["status"] == "done" and not result["success"] and not result["verified"]


def test_mutation_failure_is_never_retried_and_tab_is_closed(args):
    class Failed(AgentDouble):
        def run(self):
            self.browser.before_action()
            self.browser.mutations += 1
            raise RuntimeError("sensitive provider or page content")
            yield

    result = trial(args, Failed)
    agent = Failed.instances[-1]
    assert result["error"] == "RuntimeError" and not result["success"]
    assert agent.browser.closed and agent.browser.mutations == 1
    assert "sensitive" not in json.dumps(result)


def test_scope_is_checked_immediately_before_mutation(args):
    class Escaped(AgentDouble):
        def run(self):
            self.browser.url = "https://example.com/private"
            yield from super().run()

    result = trial(args, Escaped)
    assert result["error"] == "outside_fixture_scope"
    assert Escaped.instances[-1].browser.mutations == 0
    assert Escaped.instances[-1].browser.closed


def test_step_budget_stops_before_next_mutation(args):
    class Repeated(AgentDouble):
        def run(self):
            yield from super().run()
            yield from super().run()

    args.max_steps = 1
    result = trial(args, Repeated)
    assert result["error"] == "step_budget_exhausted"
    assert Repeated.instances[-1].browser.mutations == 1


def test_cancellation_during_setup_prevents_mutation_and_cleans_up(args):
    control = host.Control(10)

    class Cancelled(AgentDouble):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            control.cancel()

    result = trial(args, Cancelled, control)
    assert result["error"] == "cancelled"
    assert Cancelled.instances[-1].browser.closed
    assert Cancelled.instances[-1].browser.mutations == 0


def test_deadline_prevents_setup(args):
    control = host.Control(-1)
    factory = Mock()
    result = trial(args, factory, control)
    factory.assert_not_called()
    assert result["error"] == "deadline_exceeded"


def test_adapter_correlates_response_and_rejects_unobserved_choice():
    state = {"url": "http://127.0.0.1:9999/choice.html", "fingerprint": "page", "title": "", "text": "",
             "actions": [{"id": "save", "node": 1, "label": "Save", "kind": "click"}]}
    adapter = host.HostAdapter(None, None, "jev", state["url"], host.Control(10))
    adapter.call = Mock(return_value={"operation": "CLICK", "choice": "save", "target": "1",
                                     "observation_fingerprint": "old"})
    with pytest.raises(host.HostStopped, match="mismatched_observation"):
        adapter.choose(state, "goal", [])
    adapter.call.return_value.update(observation_fingerprint="page", choice="unobserved")
    with pytest.raises(host.HostStopped, match="invalid_observed_choice"):
        adapter.choose(state, "goal", [])
    adapter.call.return_value.update(choice="save")
    assert adapter.choose(state, "goal", [])["choice"] == "save"


def test_adapter_does_not_send_out_of_scope_pages():
    adapter = host.HostAdapter(None, None, "jev", "http://127.0.0.1:9999/choice.html", host.Control(10))
    adapter.call = Mock()
    with pytest.raises(host.HostStopped, match="outside_fixture_scope"):
        adapter.choose({"url": "https://example.com/private"}, "goal", [])
    adapter.call.assert_not_called()


def test_mcp_error_accounting_is_preserved_without_raw_error():
    calls = [{"model": "test", "usage": {"total_tokens": 1}}]
    with pytest.raises(host.HostStopped) as caught:
        host.tool_result(response({"ok": False, "error": "private value", "model_calls": calls}))
    assert str(caught.value) == "mcp_tool_failed"
    assert caught.value.routing["model_calls"] == calls


def test_async_cancellation_joins_worker_before_closing_transport(args):
    pytest.importorskip("mcp")
    started = threading.Event()
    instances = []
    control = host.Control(10)

    class SlowAgent(AgentDouble):
        def run(self):
            started.set()
            assert control.cancelled.wait(3)
            self.browser.before_action()
            self.browser.mutations += 1
            yield self.state

    class CheckedClient(ClientDouble):
        async def __aexit__(self, *_):
            assert SlowAgent.instances[-1].browser.closed
            await super().__aexit__()

    def factory(*a, **kw):
        instances.append(CheckedClient(*a, **kw))
        return instances[-1]

    async def run():
        task = asyncio.create_task(host.run(args, client_factory=factory, agent_factory=SlowAgent,
                                           control=control, environ={"OPENROUTER_API_KEY": "offline"}))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert instances[0].closed
    assert SlowAgent.instances[-1].browser.mutations == 0


def test_output_existing_file_is_preserved(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("original")
    assert host.main(["--output", str(output)]) == 2
    assert output.read_text() == "original"


def test_cancellation_of_inflight_mcp_call():
    class PendingClient:
        def __init__(self):
            self.started, self.stopped = asyncio.Event(), asyncio.Event()

        async def call_tool(self, *_):
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.stopped.set()

    async def run():
        control, client = host.Control(10), PendingClient()
        adapter = host.HostAdapter(client, asyncio.get_running_loop(), "jev", "local", control)
        task = asyncio.create_task(asyncio.to_thread(adapter.call, "choose_browser_action", {}))
        await client.started.wait()
        control.cancel()
        with pytest.raises(host.HostStopped, match="cancelled"):
            await task
        await asyncio.wait_for(client.stopped.wait(), 1)

    asyncio.run(run())


def test_cleanup_failure_prevents_success(args):
    class BrokenClose(AgentDouble):
        def close(self):
            raise RuntimeError("private cleanup details")

    result = trial(args, BrokenClose)
    assert result["verified"] and not result["success"]
    assert result["cleanup_error"] == "RuntimeError"
