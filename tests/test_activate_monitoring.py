"""Monitoring activation contract tests, with no network or external mutations."""

import json
import subprocess
from datetime import datetime, timezone

import pytest

from local import activate_monitoring as activation


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    (bundle / "site").mkdir(parents=True)
    (bundle / "site/version.json").write_text(json.dumps({"releaseId": "a" * 64}))
    (bundle / "public-manifest.json").write_text('{"product":"Browser Bolt"}')
    for name in activation.WORKFLOWS:
        path = bundle / "source/.github/workflows" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("reviewed workflow")
    manifest = {"config": activation.release.config_values({"workers_subdomain": "example"})}

    def verify(root, path):
        return manifest, activation.release.digest(activation.release.canonical(activation.release.inventory(path)))

    monkeypatch.setattr(activation.release, "verify", verify)
    identity = verify(tmp_path, bundle)[1]
    state = {"digest": identity, "complete": True, "site_verified": True, "repo_id": 12, "release_id": 34,
             "deployment_id": "deployment", "commit": "b" * 40}
    activation.journal_path(bundle).write_text(json.dumps(state))
    return tmp_path, bundle, identity, manifest


class FakeRemote(activation.ActivationRemote):
    def __init__(self, config):
        super().__init__(config)
        self.values = {}
        self.workflow_values = {name: {"id": index, "path": f".github/workflows/{name}", "state": "disabled_fork"}
                                for index, name in enumerate(activation.WORKFLOWS, 1)}
        self.calls = []
        self.runs = {}
        self.jobs = {}
        self.lose_dispatch = False
        self.lose_variable = False
        self.on_identity = None
        self.identity_calls = 0

    def published_identity(self, state):
        self.identity_calls += 1
        if self.on_identity:
            self.on_identity()

    def verify_site(self, bundle, state, persist):
        state["site_verified"] = True
        persist()

    def gh(self, endpoint, method="GET", body=None, missing=False):
        self.calls.append((endpoint, method, body))
        if endpoint.endswith("/actions/variables?per_page=100"):
            return {"total_count": len(self.values), "variables": [
                {"name": name, "value": value} for name, value in self.values.items()]}
        if endpoint.endswith("/actions/variables") and method == "POST":
            if self.lose_variable:
                self.lose_variable = False
                raise activation.release.ReleaseError("lost variable response")
            self.values[body["name"]] = body["value"]
            return None
        if "/actions/variables/" in endpoint:
            name = endpoint.rsplit("/", 1)[1]
            return {"name": name, "value": self.values.get(name)}
        if "/jobs?" in endpoint:
            run_id = int(endpoint.split("/runs/")[1].split("/")[0])
            return {"total_count": len(self.jobs[run_id]), "jobs": self.jobs[run_id]}
        for name in activation.WORKFLOWS:
            if f"/workflows/{name}/runs?" in endpoint:
                runs = self.runs.get(name, [])
                return {"total_count": len(runs), "workflow_runs": runs}
            if endpoint.endswith(f"/workflows/{name}/enable"):
                assert method == "PUT" and body is None
                self.workflow_values[name]["state"] = "active"
                return None
            if endpoint.endswith(f"/workflows/{name}/dispatches"):
                assert method == "POST"
                if self.lose_dispatch:
                    self.lose_dispatch = False
                    raise activation.release.ReleaseError("lost dispatch response")
                return None
            if endpoint.endswith(f"/workflows/{name}"):
                return dict(self.workflow_values[name])
        pytest.fail("Unexpected API request")


def test_default_plan_and_wrong_approval_use_no_network(candidate):
    root, bundle, identity, manifest = candidate
    service = FakeRemote(manifest["config"])
    result = activation.activation(root, bundle, remote=service)
    assert result["mode"] == "plan" and result["network_used"] is False
    with pytest.raises(activation.release.ReleaseError, match="exact approved digest"):
        activation.activation(root, bundle, execute=True, approval="wrong", remote=service)
    assert service.calls == [] and service.identity_calls == 0
    assert result["variables"]["BOLT_CANARY_MANIFEST_SHA"] == activation.release.digest(
        (bundle / "public-manifest.json").read_bytes())


def test_activation_exact_payloads_enable_last_and_pending_is_not_verified(candidate):
    root, bundle, identity, manifest = candidate
    service = FakeRemote(manifest["config"])
    result = activation.activation(root, bundle, execute=True, approval=identity, remote=service)
    writes = [call for call in service.calls if call[1] != "GET"]
    variable_writes = [call for call in writes if call[0].endswith("/actions/variables")]
    assert [call[2]["name"] for call in variable_writes][-1] == "BOLT_CANARY_ENABLED"
    assert variable_writes[-1][2] == {"name": "BOLT_CANARY_ENABLED", "value": "true"}
    assert len(variable_writes) == 6
    for name in activation.WORKFLOWS:
        assert (f"repos/{service.repo}/actions/workflows/{name}/enable", "PUT", None) in writes
        call = next(call for call in writes if call[0].endswith(f"/{name}/dispatches"))
        assert call[1] == "POST" and call[2]["ref"] == manifest["config"]["tag"]
        assert len(call[2]["inputs"]["launch_id"]) == 32
        expected = {"launch_id": call[2]["inputs"]["launch_id"], "repository": service.repo,
                    "url": "https://browser-bolt.example.workers.dev", "release_id": "a" * 64,
                    "release_tag": "v0.1.0", "expected_sha": "b" * 40,
                    "manifest_sha": activation.release.digest((bundle / "public-manifest.json").read_bytes())}
        if name == "public-canary.yml":
            expected["drill"] = "none"
        assert call[2]["inputs"] == expected
    enable_index = next(i for i, call in enumerate(writes) if call[2] == variable_writes[-1][2])
    assert all(i < enable_index for i, call in enumerate(writes) if call[1] == "PUT")
    assert result["configured"] is True and result["manual_runs_verified"] is False
    assert all(item["status"] == "pending" for item in result["runs"].values())


def test_conflicting_variable_never_overwritten(candidate):
    root, bundle, identity, manifest = candidate
    service = FakeRemote(manifest["config"])
    service.values["BOLT_CANARY_URL"] = "https://unrelated.invalid"
    with pytest.raises(activation.release.ReleaseError, match="Conflicting"):
        activation.activation(root, bundle, execute=True, approval=identity, remote=service)
    assert not any(method != "GET" for _, method, _ in service.calls)


def test_late_artifact_swap_stops_before_any_mutation(candidate):
    root, bundle, identity, manifest = candidate
    service = FakeRemote(manifest["config"])
    service.on_identity = lambda: (bundle / "public-manifest.json").write_text("different valid candidate")
    with pytest.raises(activation.release.ReleaseError, match="approved digest"):
        activation.activation(root, bundle, execute=True, approval=identity, remote=service)
    assert not any(method != "GET" for _, method, _ in service.calls)


def test_ambiguous_dispatch_is_never_repeated(candidate):
    root, bundle, identity, manifest = candidate
    service = FakeRemote(manifest["config"])
    service.lose_dispatch = True
    with pytest.raises(activation.release.ReleaseError, match="lost dispatch"):
        activation.activation(root, bundle, execute=True, approval=identity, remote=service)
    first = json.loads(activation.journal_path(bundle).read_text())["monitoring_activation"]["workflows"][
        "public-canary.yml"]["launch_id"]
    result = activation.activation(root, bundle, execute=True, approval=identity, remote=service)
    dispatches = [call for call in service.calls if call[0].endswith("public-canary.yml/dispatches")]
    assert len(dispatches) == 1 and dispatches[0][2]["inputs"]["launch_id"] == first
    assert result["runs"]["public-canary.yml"]["status"] == "dispatch_unconfirmed"
    assert result["manual_runs_verified"] is False


def test_ambiguous_variable_creation_is_not_repeated(candidate):
    root, bundle, identity, manifest = candidate
    service = FakeRemote(manifest["config"])
    service.lose_variable = True
    with pytest.raises(activation.release.ReleaseError, match="lost variable"):
        activation.activation(root, bundle, execute=True, approval=identity, remote=service)
    with pytest.raises(activation.release.ReleaseError, match="uncertain"):
        activation.activation(root, bundle, execute=True, approval=identity, remote=service)
    assert len([call for call in service.calls if call[1] == "POST"]) == 1


def add_successful_runs(service, state):
    for name, (prefix, job_name, step_name) in activation.WORKFLOWS.items():
        entry = state["monitoring_activation"]["workflows"][name]
        run_id = entry["id"] * 10
        service.runs[name] = [{"id": run_id, "display_title": prefix + entry["launch_id"],
                               "created_at": datetime.now(timezone.utc).isoformat(),
                               "event": "workflow_dispatch", "workflow_id": entry["id"],
                               "head_sha": state["commit"], "head_branch": entry["dispatch_ref"],
                               "path": f".github/workflows/{name}", "head_repository": {"id": state["repo_id"]},
                               "repository": {"id": state["repo_id"]}, "status": "completed",
                               "conclusion": "success"}]
        service.jobs[run_id] = [{"name": job_name, "status": "completed", "conclusion": "success",
                                 "run_id": run_id, "head_sha": state["commit"],
                                 "steps": [{"name": step_name, "status": "completed", "conclusion": "success"}]}]


@pytest.mark.parametrize("defect", ["wrong_run", "skipped_job", "skipped_step", None])
def test_read_only_check_requires_correlated_successful_jobs(candidate, defect):
    root, bundle, identity, manifest = candidate
    service = FakeRemote(manifest["config"])
    activation.activation(root, bundle, execute=True, approval=identity, remote=service)
    state = json.loads(activation.journal_path(bundle).read_text())
    add_successful_runs(service, state)
    if defect == "wrong_run":
        service.runs["public-canary.yml"][0]["head_sha"] = "c" * 40
    elif defect == "skipped_job":
        service.jobs[10][0]["conclusion"] = "skipped"
    elif defect == "skipped_step":
        service.jobs[10][0]["steps"][0]["conclusion"] = "skipped"
    service.calls.clear()
    prior_journal = activation.journal_path(bundle).read_bytes()
    if defect == "wrong_run":
        with pytest.raises(activation.release.ReleaseError, match="does not match"):
            activation.activation(root, bundle, check=True, remote=service)
    else:
        result = activation.activation(root, bundle, check=True, remote=service)
        assert result["manual_runs_verified"] is (defect is None)
        assert result["scheduled_coverage_verified"] is False
        assert result["notification_delivery_verified"] is False
    assert all(method == "GET" for _, method, _ in service.calls)
    assert activation.journal_path(bundle).read_bytes() == prior_journal


@pytest.mark.parametrize("status,body,expected", [(204, b"", None), (201, b"", None), (200, b'{"id":1}', {"id": 1})])
def test_bounded_gh_adapter_handles_empty_successes(monkeypatch, status, body, expected):
    seen = []

    def command(args, **kwargs):
        seen.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, f"HTTP/2.0 {status} OK\r\n\r\n".encode() + body, b"")

    monkeypatch.setattr(activation.subprocess, "run", command)
    service = activation.ActivationRemote(activation.release.DEFAULT_CONFIG)
    assert service.gh("repos/owner/repo/actions/workflows/name/enable", "PUT") == expected
    args, options = seen[0]
    assert args == ["gh", "api", "--hostname", "github.com", "repos/owner/repo/actions/workflows/name/enable",
                    "--method", "PUT", "--include"]
    assert options["timeout"] == 30 and options["input"] is None


def test_adapter_failure_is_sanitized(monkeypatch):
    monkeypatch.setattr(activation.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 1, b"sensitive sentinel", b"sensitive sentinel"))
    service = activation.ActivationRemote(activation.release.DEFAULT_CONFIG)
    with pytest.raises(activation.release.ReleaseError) as error:
        service.gh("user")
    assert "sentinel" not in str(error.value)


def test_incomplete_journal_and_shared_lock_stop_activation(candidate):
    root, bundle, identity, manifest = candidate
    service = FakeRemote(manifest["config"])
    lock = bundle.with_name(bundle.name + ".publication.lock")
    lock.write_text("active")
    with pytest.raises(activation.release.ReleaseError, match="lock exists"):
        activation.activation(root, bundle, execute=True, approval=identity, remote=service)
    lock.unlink()
    state = json.loads(activation.journal_path(bundle).read_text())
    state["site_verified"] = False
    activation.journal_path(bundle).write_text(json.dumps(state))
    with pytest.raises(activation.release.ReleaseError, match="completed and verified"):
        activation.activation(root, bundle, execute=True, approval=identity, remote=service)
    assert service.identity_calls == 0


def test_mutable_original_variable_reads_cannot_supply_activation_values(candidate, monkeypatch):
    root, bundle, identity, manifest = candidate
    original = activation.desired_variables

    def poisoned(manifest_value, path):
        desired = original(manifest_value, path)
        if path == bundle:
            desired["BOLT_CANARY_RELEASE"] = "c" * 64
        return desired

    monkeypatch.setattr(activation, "desired_variables", poisoned)
    service = FakeRemote(manifest["config"])
    result = activation.activation(root, bundle, execute=True, approval=identity, remote=service)
    assert service.values["BOLT_CANARY_RELEASE"] == "a" * 64
    assert result["variables"]["BOLT_CANARY_RELEASE"] == "a" * 64


def test_default_branch_change_is_rechecked_before_enable_and_dispatch(candidate):
    root, bundle, identity, manifest = candidate
    service = FakeRemote(manifest["config"])

    def changed():
        if service.identity_calls >= 2:
            raise activation.release.ReleaseError("Default branch no longer matches")

    service.on_identity = changed
    with pytest.raises(activation.release.ReleaseError, match="Default branch"):
        activation.activation(root, bundle, execute=True, approval=identity, remote=service)
    assert "BOLT_CANARY_ENABLED" not in service.values
    assert not any(endpoint.endswith("/dispatches") for endpoint, _, _ in service.calls)


@pytest.mark.parametrize("defect", [None, "repository", "branch", "tag", "draft", "deployment"])
def test_actual_published_identity_contract_is_checked(candidate, monkeypatch, defect):
    _, bundle, identity, manifest = candidate
    state = json.loads(activation.journal_path(bundle).read_text())
    service = activation.ActivationRemote(manifest["config"])
    responses = {
        f"repos/{service.repo}": {"id": 12, "full_name": service.repo, "private": False, "fork": True,
                                 "parent": {"full_name": activation.release.UPSTREAM},
                                 "default_branch": "browser-bolt"},
        f"repos/{service.repo}/branches/browser-bolt": {"commit": {"sha": state["commit"]}},
        f"repos/{service.repo}/git/ref/tags/v0.1.0": {"object": {"type": "commit", "sha": state["commit"]}},
        f"repos/{service.repo}/releases/34": {"draft": False, "tag_name": "v0.1.0"},
    }
    if defect == "repository":
        responses[f"repos/{service.repo}"]["id"] = 99
    elif defect == "branch":
        responses[f"repos/{service.repo}/branches/browser-bolt"]["commit"]["sha"] = "c" * 40
    elif defect == "tag":
        responses[f"repos/{service.repo}/git/ref/tags/v0.1.0"]["object"]["sha"] = "c" * 40
    elif defect == "draft":
        responses[f"repos/{service.repo}/releases/34"]["draft"] = True
    monkeypatch.setattr(service, "gh", lambda endpoint, **kwargs: responses[endpoint])
    monkeypatch.setattr(service, "deployment_matches",
                        lambda state: "other" if defect == "deployment" else "deployment")
    if defect:
        with pytest.raises(activation.release.ReleaseError):
            service.published_identity(state)
    else:
        service.published_identity(state)
