import json
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from jev_ultrafast import benchmark


def test_schedule_reproducible_balanced_and_complete():
    profiles = benchmark.DEFAULT_PROFILES
    plan = benchmark.schedule(profiles, list(benchmark.GOALS), 3, 42)
    assert plan == benchmark.schedule(profiles, list(benchmark.GOALS), 3, 42)
    assert plan != benchmark.schedule(profiles, list(benchmark.GOALS), 3, 43)
    assert len({row["trial_id"] for row in plan}) == 108
    blocks = [plan[start : start + 6] for start in range(0, len(plan), 6)]
    assert all({row["profile"] for row in block} == set(profiles) for block in blocks)
    assert len({block[0]["profile"] for block in blocks[:6]}) == 6


def test_dry_run_and_list_need_no_credentials_or_runtime(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Dry run touched a runtime dependency")

    monkeypatch.setattr(benchmark, "get_profile", forbidden)
    monkeypatch.setattr(benchmark, "fixture_server", forbidden)
    assert benchmark.main(["--dry-run", "--env-file", "/does/not/exist"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["trial_count"] == 108
    assert result["config"]["profiles"] == list(benchmark.DEFAULT_PROFILES)
    assert benchmark.main(["--list-profiles"]) == 0
    assert capsys.readouterr().out.splitlines() == list(benchmark.PROFILES)


@pytest.mark.parametrize(
    "arguments",
    [["--runs", "0"], ["--runs", "11"], ["--profiles", "qwen,qwen"], ["--tasks", "private"], ["--profiles", "unknown"]],
)
def test_invalid_arguments_fail(arguments):
    with pytest.raises(SystemExit):
        benchmark.main(arguments)


def test_literal_environment_preserves_inherited_values(tmp_path, monkeypatch):
    monkeypatch.setenv("EXISTING", "keep")
    source = tmp_path / "environment"
    source.write_text("export EXISTING=replace\nBENCH_LITERAL='$(touch forbidden)'\nBAD KEY=value\n# comment\n")
    benchmark.load_environment(source)
    import os

    assert os.environ["EXISTING"] == "keep"
    assert os.environ["BENCH_LITERAL"] == "$(touch forbidden)"
    assert "BAD KEY" not in os.environ
    monkeypatch.delenv("BENCH_LITERAL")


def test_only_packaged_fixtures_are_served():
    fixtures = benchmark.fixture_bytes()
    assert set(fixtures) == {f"/{task}.html" for task in benchmark.GOALS}
    with benchmark.fixture_server(fixtures) as base:
        assert base.startswith("http://127.0.0.1:")
        with urlopen(base + "/note.html") as response:
            assert response.read() == fixtures["/note.html"]
        for path in ("/", "/../pyproject.toml", "/.env", "/fixtures/note.html"):
            with pytest.raises(HTTPError) as error:
                urlopen(base + path)
            assert error.value.code == 404


def call(provider="openrouter", model="openai/gpt-6-astra", usage=None):
    return {
        "provider": provider,
        "model": model,
        "latency_ms": 12,
        "success": True,
        "usage": usage if usage is not None else {"cost": 0.01},
    }


def test_call_accounting_preserves_unknowns_and_excludes_inline_helpers():
    state = {
        "decisions": [{"request": "secret", "error": "secret", "routing": {"model_calls": [call()]}}],
        "text_calls": [
            call("cerebras", "qwen-3.8-27b", {"prompt_tokens": 100, "completion_tokens": 10}),
            {"included_in_decision": True, "usage": {"cost": 999}},
        ],
        "history": [{"text": "secret"}],
    }
    metrics = benchmark.state_metrics(state)
    assert metrics["model_call_count"] == 2
    assert metrics["action_count"] == 1
    assert metrics["total_cost_usd"] == pytest.approx(0.0101139)
    assert metrics["reported_cost_usd"] == 0.01
    assert metrics["estimated_cerebras_cost_usd"] == pytest.approx(0.0001139)
    assert "secret" not in json.dumps(metrics)
    state["decisions"].append({"routing": {"model_calls": [call(usage={})]}})
    assert benchmark.state_metrics(state)["total_cost_usd"] is None
    assert benchmark.state_metrics(state)["unknown_cost_call_count"] == 1
    assert benchmark.clean_call(call("cerebras", "another-qwen", {"prompt_tokens": 100}))["cost_usd"] is None
    assert benchmark.clean_call(call(usage={"cost": 0}))["cost_usd"] == 0
    assert benchmark.clean_call(call(usage={"cost": float("nan")}))["cost_usd"] is None


def test_raw_payload_fields_and_usage_extensions_are_removed():
    raw = {**call(), "request": {"key": "secret"}, "response": "secret", "error": "secret"}
    raw["usage"]["upstream_body"] = "secret"
    assert "secret" not in json.dumps(benchmark.clean_call(raw))


class FakeAgent:
    failed = False
    final_text = "Preference saved Express"

    def __init__(self, url, goal, *, decision_fn, text_fn):
        self.state = {"status": "ready", "decisions": [], "text_calls": [], "history": []}
        self.browser = SimpleNamespace(
            observe=lambda **kwargs: {"text": self.final_text},
            evaluate=lambda _: {
                "heading": "Preference saved"
                if self.final_text.startswith("Preference saved")
                else "Choose a delivery speed",
                "paragraphs": ["Express"],
            },
        )
        self.closed = False
        self.url = url
        self.decision_fn, self.text_fn = decision_fn, text_fn

    def run(self):
        self.state["decisions"].append({"request": "secret", "routing": {"model_calls": [call()]}})
        self.state["history"].append({"text": "secret"})
        if self.failed:
            self.state["status"] = "blocked"
            raise RuntimeError("private provider response with secret")
        self.state["status"] = "done"
        yield self.state

    def close(self):
        self.closed = True


def test_failed_attempt_retains_metrics_and_verifies_without_retry(monkeypatch):
    created = []

    def factory(*args, **kwargs):
        agent = FakeAgent(*args, **kwargs)
        agent.failed = True
        created.append(agent)
        return agent

    clock = iter([0, 2, 2, 7, 8])
    monkeypatch.setattr(benchmark.time, "perf_counter", lambda: next(clock))
    profile = SimpleNamespace(choose=object(), field_text=object())
    row = benchmark.run_trial(
        {"trial_id": "1:choice:qwen", "task": "choice", "profile": "qwen"}, "http://127.0.0.1:9", profile, factory
    )
    assert row["error_type"] == "RuntimeError"
    assert not row["success"] and row["verified"]
    assert row["total_cost_usd"] == 0.01 and row["model_call_count"] == 1
    assert row["setup_seconds"] == 2 and row["task_seconds"] == 5 and row["wall_seconds"] == 8
    assert created[0].closed and len(created[0].state["history"]) == 1
    assert "secret" not in json.dumps(row)


def test_done_without_final_outcome_fails():
    class Unverified(FakeAgent):
        final_text = "Choose a delivery speed Express"

    row = benchmark.run_trial(
        {"task": "choice"}, "http://127.0.0.1:9", SimpleNamespace(choose=None, field_text=None), Unverified
    )
    assert row["status"] == "done" and not row["success"]


def test_summary_includes_failed_cost_and_labels_timing_denominators():
    rows = [
        {"profile": "qwen", "task": "choice", "success": True, "total_cost_usd": 1, "task_seconds": 2},
        {"profile": "qwen", "task": "choice", "success": False, "total_cost_usd": 3, "task_seconds": 10},
    ]
    result = benchmark.summarize(rows)[0]
    assert result["sample_count"] == 2 and result["success_count"] == 1
    assert result["cost_per_attempt_usd"] == 2
    assert result["cost_per_verified_success_usd"] == 4
    assert result["median_task_seconds_all"] == 6 and result["median_task_seconds_successful"] == 2
    rows[1]["total_cost_usd"] = None
    assert benchmark.summarize(rows)[0]["cost_per_verified_success_usd"] is None
    assert benchmark.summarize([rows[1]])[0]["cost_per_verified_success_usd"] is None


def test_preflight_fails_before_browser_or_evidence(monkeypatch, tmp_path, capsys):
    def reject(*args, **kwargs):
        raise ValueError("secret missing key material")

    monkeypatch.setattr(benchmark, "get_profile", reject)
    monkeypatch.setattr(benchmark, "fixture_server", lambda _: pytest.fail("Browser setup began"))
    output = tmp_path / "results.json"
    with pytest.raises(SystemExit):
        benchmark.main(["--output", str(output)])
    assert not output.exists()
    assert "secret" not in capsys.readouterr().err


def test_cli_flushes_each_trial_and_resumes_without_repeating(monkeypatch, tmp_path):
    from jev_ultrafast import agent

    profiles = SimpleNamespace(choose=None, field_text=None, preflight=lambda: None)
    monkeypatch.setattr(benchmark, "get_profile", lambda *args, **kwargs: profiles)
    output = tmp_path / "results.json"
    seen = []

    class InspectAgent(FakeAgent):
        def __init__(self, *args, **kwargs):
            evidence = json.loads(output.read_text())
            assert evidence["rows"][-1]["status"] == "running"
            seen.append(evidence)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(agent, "Agent", InspectAgent)

    @contextmanager
    def server(_):
        yield "http://127.0.0.1:9"

    monkeypatch.setattr(benchmark, "fixture_server", server)
    args = ["--profiles", "qwen,astra", "--tasks", "choice", "--runs", "1", "--output", str(output)]
    assert benchmark.main(args) == 0
    result = json.loads(output.read_text())
    assert len(seen) == 2 and all(row["success"] for row in result["rows"])
    result["rows"][-1] = {**result["schedule"][-1], "status": "running", "success": False, "total_cost_usd": None}
    output.write_text(json.dumps(result))
    assert benchmark.main(args) == 0
    assert len(seen) == 2
    resumed = json.loads(output.read_text())
    assert resumed["rows"][-1]["status"] == "interrupted"
    assert resumed["rows"][-1]["total_cost_usd"] is None


def test_metadata_has_pinned_config_and_fixture_source_fingerprints():
    config = SimpleNamespace(
        provider="openrouter",
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        model="openai/gpt-6-astra",
        reasoning={"effort": "low"},
        key="secret",
    )
    profiles = {"astra": SimpleNamespace(decision=config, writer=None)}
    metadata = benchmark.evidence_metadata(profiles, benchmark.fixture_bytes())
    assert metadata["profiles"]["astra"]["decision"]["model"] == "openai/gpt-6-astra"
    assert metadata["profiles"]["astra"]["decision"]["reasoning"] == {"effort": "low"}
    assert len(metadata["source_sha256"]["agent.py"]) == 64
    assert len(metadata["fixture_sha256"]) == 6
    assert "secret" not in json.dumps(metadata)
    changed = benchmark.fixture_bytes()
    changed["/note.html"] += b"changed"
    assert benchmark.evidence_metadata(profiles, changed)["fingerprint"] != metadata["fingerprint"]


def test_resume_rejects_changed_fingerprint(monkeypatch, tmp_path):
    profile = SimpleNamespace(choose=None, field_text=None, preflight=lambda: None)
    monkeypatch.setattr(benchmark, "get_profile", lambda *args, **kwargs: profile)
    config = {"profiles": ["qwen"], "tasks": ["choice"], "runs": 1, "seed": 0}
    output = tmp_path / "evidence.json"
    output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "config": config,
                "schedule": benchmark.schedule(**config),
                "rows": [],
                "metadata": {"fingerprint": "old-code"},
            }
        )
    )
    monkeypatch.setattr(benchmark, "fixture_server", lambda _: pytest.fail("Browser setup began"))
    with pytest.raises(SystemExit):
        benchmark.main(["--profiles", "qwen", "--tasks", "choice", "--runs", "1", "--output", str(output)])
    assert json.loads(output.read_text())["metadata"]["fingerprint"] == "old-code"


@pytest.mark.parametrize("task", ["note", "review"])
def test_verifier_requires_exact_saved_title_and_note_body(task):
    def page(title, body):
        return {"fixture_result": {"heading": "Note saved", "paragraphs": [title, body]}}

    assert benchmark.verify(task, page("Lisbon weekend", "Enjoy a tram ride and pastries."))
    assert not benchmark.verify(task, page("Wrong title", "Lisbon weekend tram pastries"))
    assert not benchmark.verify(task, page("Lisbon weekend tram pastries", "Unrelated note"))
    assert not benchmark.verify(task, {"text": "Note saved Lisbon weekend tram pastries"})


def test_summary_keeps_known_part_of_partially_unknown_attempt():
    metrics = benchmark.state_metrics(
        {"decisions": [{"routing": {"model_calls": [call(usage={"cost": 0.1}), call(usage={})]}}]}
    )
    row = {"profile": "astra", "task": "choice", "success": False, **metrics}
    summary = benchmark.summarize([row])[0]
    assert summary["total_cost_usd"] is None
    assert summary["cost_per_attempt_usd"] is None
    assert summary["known_cost_subtotal_usd"] == 0.1


@pytest.mark.parametrize(
    "task,heading,paragraphs",
    [
        ("checkout", "Order reviewed", ["Taylor Example", "12 Demo Lane", "Express", "Demo card 4242"]),
        ("recovery", "Discount reviewed", ["DEMO10", "10%", "$36.00"]),
        ("onboarding", "Profile ready", ["Casey Demo", "I enjoy design and travel.", "Design, Travel"]),
    ],
)
def test_realistic_flow_verification(task, heading, paragraphs):
    assert benchmark.verify(task, {"fixture_result": {"heading": heading, "paragraphs": paragraphs}})
    wrong = list(paragraphs)
    wrong[0] = "Wrong value"
    assert not benchmark.verify(task, {"fixture_result": {"heading": heading, "paragraphs": wrong}})
    assert not benchmark.verify(task, {"fixture_result": {"heading": "Still editing", "paragraphs": paragraphs}})
