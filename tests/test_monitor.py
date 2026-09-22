"""Offline failure drills for public monitoring; no GitHub writes or provider calls."""

import json
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from local import monitor, static_canary

REPOSITORY = "example/browser-bolt"
BASE = "https://bolt.example.com"
RELEASE = "a" * 64
NOW = 1800000000


def stamp(seconds):
    return datetime.fromtimestamp(NOW - seconds, timezone.utc).isoformat()


def run(age=60, status="completed", conclusion="success"):
    return {"id": 456, "repository": {"full_name": REPOSITORY}, "head_repository": {"full_name": REPOSITORY},
            "workflow_id": 123, "path": monitor.WORKFLOW_PATH, "event": "schedule", "head_branch": "main",
            "created_at": stamp(age), "updated_at": stamp(max(0, age - 5)),
            "status": status, "conclusion": conclusion}


def probe_job():
    return {"name": "probe", "conclusion": "success", "status": "completed", "run_id": 456,
            "steps": [{"name": "Check the verified public release", "status": "completed", "conclusion": "success"}]}


def api(runs=None, **workflow):
    responses = iter([
        {"full_name": REPOSITORY, "private": False, "default_branch": "main"},
        {"id": 123, "path": monitor.WORKFLOW_PATH, "state": "active", **workflow},
        {"workflow_runs": [run()] if runs is None else runs},
        {"jobs": [probe_job()]},
    ])

    def get(endpoint):
        assert endpoint.startswith(f"repos/{REPOSITORY}")
        if "/runs?" in endpoint:
            assert "event=schedule" in endpoint and "branch=main" in endpoint and "per_page=10" in endpoint
        return next(responses)

    return get


@pytest.mark.parametrize("values,code", [
    (("", "", ""), "invalid_repository"),
    (("../private", BASE, RELEASE), "invalid_repository"),
    ((REPOSITORY, "https://secret@bolt.example.com", RELEASE), "invalid_origin"),
    ((REPOSITORY, BASE + "?sentinel-secret", RELEASE), "invalid_origin"),
    ((REPOSITORY, BASE, "sentinel-secret"), "invalid_release"),
])
def test_configuration_rejects_missing_or_unsafe_values(values, code):
    with pytest.raises(monitor.MonitorError, match=code):
        monitor.configuration(*values)


def test_wrong_repository_stops_before_network():
    with pytest.raises(monitor.MonitorError, match="repository_mismatch"):
        monitor.canary(REPOSITORY, BASE, RELEASE, "other/fork", probe=lambda *a, **k: pytest.fail("network"))


def test_legacy_activation_helper_returns_only_nonexecutable_metadata():
    with pytest.raises(monitor.MonitorError, match="publish_not_verified"):
        monitor.activation_plan(REPOSITORY, BASE, RELEASE)
    plan = monitor.activation_plan(REPOSITORY, BASE, RELEASE, verified=True)
    assert plan["variables"]["BOLT_CANARY_ENABLED"] == "true"
    assert plan["mode"] == "metadata_only" and plan["executable"] is False
    assert plan["activation_command"] == "python3 -m local.activate_monitoring"
    assert plan["workflows"] == ["public-canary.yml"]
    assert not ({"enable", "dispatch", "set_last"} & plan.keys())
    assert "DEFAULT_BRANCH" not in json.dumps(plan)


def test_three_failures_and_recovery_are_bounded():
    calls = []

    def probe(*args, **kwargs):
        calls.append(kwargs)
        return {"ok": False, "checks": []}

    result = monitor.canary(REPOSITORY, BASE, RELEASE, REPOSITORY, probe=probe, sleep=lambda _: None)
    assert result["event"] == "incident" and result["attempts"] == 3
    assert calls == [{"timeout": 3}] * 3
    outcomes = iter([False, True])
    result = monitor.canary(REPOSITORY, BASE, RELEASE, REPOSITORY,
                           probe=lambda *a, **k: {"ok": next(outcomes), "checks": []}, sleep=lambda _: None)
    assert result["ok"] and result["attempts"] == 2
    assert monitor.canary(REPOSITORY, BASE, RELEASE, REPOSITORY, drill="failure")["attempts"] == 0


@pytest.mark.parametrize("runs,code", [
    ([], "missing_scheduled_run"),
    ([run(901)], "stale_schedule"),
    ([run(301, "in_progress", None), run(400)], "hung_workflow"),
    ([run(301, "queued", None), run(400)], "hung_workflow"),
    ([run(60, "completed", "failure")], "workflow_failed"),
    ([run(60, "completed", "skipped")], "workflow_failed"),
    ([run(60, "in_progress", None), run(901)], "no_recent_success"),
    ([run(-1)], "invalid_run_timestamp"),
])
def test_stopped_hung_failed_and_future_workflows_fail_closed(runs, code):
    with pytest.raises(monitor.MonitorError, match=code):
        monitor.schedule_status(REPOSITORY, get=api(runs), now=NOW)


@pytest.mark.parametrize("key,value", [
    ("repository", {"full_name": "other/fork"}), ("head_repository", None),
    ("workflow_id", 999), ("path", ".github/workflows/other.yml"),
    ("event", "workflow_dispatch"), ("head_branch", "other"),
])
def test_wrong_run_identity_fails_closed(key, value):
    wrong = {**run(), key: value}
    with pytest.raises(monitor.MonitorError, match="run_identity_mismatch"):
        monitor.schedule_status(REPOSITORY, get=api([wrong]), now=NOW)


def test_active_schedule_with_recent_success():
    assert monitor.schedule_status(REPOSITORY, get=api(), now=NOW)["ok"]
    assert monitor.schedule_status(REPOSITORY, get=api([run(60, "in_progress", None), run(360)]), now=NOW)["ok"]
    with pytest.raises(monitor.MonitorError, match="workflow_disabled"):
        monitor.schedule_status(REPOSITORY, get=api(state="disabled_inactivity"), now=NOW)


@pytest.mark.parametrize("failure", ["timeout", "denied", "malformed"])
def test_github_errors_redact_raw_output(monkeypatch, failure):
    def execute(command, **kwargs):
        assert command[:7] == ["gh", "api", "--hostname", "github.com", "--method", "GET", "repos/example/repo"]
        assert kwargs["timeout"] == 15
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 15, output=b"sentinel-secret")
        return subprocess.CompletedProcess(command, int(failure == "denied"), b"sentinel-secret", b"sentinel-secret")

    monkeypatch.setattr(monitor.subprocess, "run", execute)
    with pytest.raises(monitor.MonitorError) as error:
        monitor.gh_get("repos/example/repo")
    assert str(error.value) == "github_read_failed"


def test_watcher_checks_site_even_when_github_fails():
    def unavailable(_):
        raise monitor.MonitorError("github_read_failed")

    result = monitor.watch(REPOSITORY, BASE, RELEASE, get=unavailable,
                           probe=lambda *a, **k: {"ok": True, "checks": []})
    assert not result["ok"] and result["site"]["ok"]


def test_real_http_failure_and_recovery_stay_redacted():
    state = {"bad": True}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            missing = self.path in {"/api/config", "/__browser_bolt_canary_missing__"}
            self.send_response(503 if state["bad"] else 404 if missing else 200)
            self.send_header("Content-Security-Policy", "connect-src 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            body = b'data-distribution="static" Browser Bolt Jev sentinel-secret'
            if self.path == "/version.json":
                body = json.dumps({"product": "Browser Bolt", "mode": "static-preview",
                                   "managedAvailable": False, "releaseId": RELEASE}).encode()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def probe(*args, **kwargs):
            return static_canary.probe(f"http://127.0.0.1:{server.server_port}", RELEASE, allow_loopback=True)

        failed = monitor.canary(REPOSITORY, BASE, RELEASE, REPOSITORY, probe=probe, sleep=lambda _: None)
        assert not failed["ok"] and failed["attempts"] == 3
        state["bad"] = False
        recovered = monitor.watch(REPOSITORY, BASE, RELEASE, get=api(), now=NOW, probe=probe)
        assert recovered["ok"]
        assert "sentinel" not in json.dumps([failed, recovered])
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_cli_missing_configuration_and_deadline_are_sanitized(monkeypatch, capsys):
    for key in ["BOLT_CANARY_REPOSITORY", "BOLT_CANARY_URL", "BOLT_CANARY_RELEASE", "GITHUB_REPOSITORY"]:
        monkeypatch.delenv(key, raising=False)
    assert monitor.main(["canary"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "invalid_repository"
    with pytest.raises(monitor.MonitorError, match="monitor_deadline"):
        monitor.deadline(None, None)


def test_workflow_security_and_inert_default():
    from pathlib import Path

    workflow = (Path(__file__).parents[1] / monitor.WORKFLOW_PATH).read_text()
    assert "vars.BOLT_CANARY_ENABLED == 'true'" in workflow
    assert "github.event.repository.private == false" in workflow
    permissions = workflow.split("permissions:\n", 1)[1].split("\n\n", 1)[0]
    assert permissions.strip() == "contents: read"
    assert "persist-credentials: false" in workflow and "secrets." not in workflow
    assert "timeout-minutes: 3" in workflow and "timeout 100s" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow
    assert "pull_request" not in workflow


def test_skipped_job_cannot_masquerade_as_healthy_schedule():
    real_get = api()

    def get(endpoint):
        if "/jobs?" in endpoint:
            return {"jobs": [{"name": "probe", "conclusion": "skipped"}]}
        return real_get(endpoint)

    with pytest.raises(monitor.MonitorError, match="workflow_not_probed"):
        monitor.schedule_status(REPOSITORY, get=get, now=NOW)


def test_real_process_deadline_has_sanitized_failure(monkeypatch, capsys):
    import signal
    import time

    def hang(*args):
        signal.alarm(1)
        time.sleep(3)
        pytest.fail("deadline did not fire")

    monkeypatch.setattr(monitor, "watch", hang)
    assert monitor.main(["watch", "--repository", REPOSITORY, "--base", BASE, "--release", RELEASE]) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "code": "monitor_deadline"}


@pytest.mark.parametrize("steps", [
    None,
    [],
    [{"name": "Check the verified public release", "status": "completed", "conclusion": "skipped"}],
    [{"name": "Check the verified public release", "status": "completed", "conclusion": "failure"}],
    [{"name": "Check the verified public release", "status": "in_progress", "conclusion": "success"}],
    [{"name": "A different check", "status": "completed", "conclusion": "success"}],
    [None],
])
def test_probe_step_must_exist_and_complete_successfully(steps):
    real_get = api()
    job = probe_job()
    if steps is None:
        del job["steps"]
    else:
        job["steps"] = steps

    def get(endpoint):
        return {"jobs": [job]} if "/jobs?" in endpoint else real_get(endpoint)

    with pytest.raises(monitor.MonitorError, match="workflow_not_probed"):
        monitor.schedule_status(REPOSITORY, get=get, now=NOW)


@pytest.mark.parametrize("field,value", [("name", "other"), ("run_id", 999), ("status", "in_progress")])
def test_successful_step_cannot_replace_matching_completed_job(field, value):
    real_get = api()
    job = {**probe_job(), field: value}

    def get(endpoint):
        return {"jobs": [job]} if "/jobs?" in endpoint else real_get(endpoint)

    with pytest.raises(monitor.MonitorError, match="workflow_not_probed"):
        monitor.schedule_status(REPOSITORY, get=get, now=NOW)



def test_legacy_activation_cli_is_explicitly_nonexecutable(capsys):
    assert monitor.main(["activation-plan", "--repository", REPOSITORY, "--base", BASE, "--release", RELEASE]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["code"] == "metadata_only"
    assert result["plan"]["executable"] is False
    assert result["plan"]["requires"] == ["approved_bundle", "approval_digest", "verified_publication_journal"]
    assert "/dispatches" not in json.dumps(result) and "/enable" not in json.dumps(result)
