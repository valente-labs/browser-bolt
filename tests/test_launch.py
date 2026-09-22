"""The wrapper delegates only approved, ordered stages; all boundaries are fake."""

import json

import pytest

from local import launch


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "approved.txt").write_text("reviewed artifact")
    config = launch.release.config_values({"workers_subdomain": "example"})

    def verify(root, path):
        return {"config": config}, launch.release.digest((path / "approved.txt").read_bytes())

    monkeypatch.setattr(launch.release, "verify", verify)
    identity = verify(tmp_path, bundle)[1]
    calls = []

    def publish(root, path, *, approval):
        calls.append(("publish", approval))
        state = {"digest": identity, "complete": True, "site_verified": True, "repo_id": 12, "commit": "b" * 40,
                 "release_id": 34, "deployment_id": "deployment"}
        launch.activate_monitoring.journal_path(path).write_text(json.dumps(state))
        return {"digest": identity, "published": True}

    def activate(root, path, *, execute=False, approval=None, check=False):
        calls.append(("check" if check else "activate", approval))
        assert check or (execute and approval == identity)
        return {"digest": identity, "configured": True, "manual_runs_verified": False,
                "runs": {"canary": {"status": "pending"}, "journey": {"status": "pending"}}}

    monkeypatch.setattr(launch.release, "publish", publish)
    monkeypatch.setattr(launch.activate_monitoring, "activation", activate)
    return tmp_path, bundle, identity, calls, publish


@pytest.mark.parametrize("mode", ["plan", "status"])
def test_default_and_status_are_offline(candidate, mode):
    root, bundle, identity, calls, _ = candidate
    result = launch.launch(root, bundle, mode=mode)
    assert result["network_used"] is False and result["digest"] == identity
    assert result["publication_recorded"] is False
    assert calls == []


def test_approval_without_execute_still_only_plans(candidate):
    root, bundle, identity, calls, _ = candidate
    assert launch.launch(root, bundle, approval=identity)["mode"] == "plan"
    assert calls == []


@pytest.mark.parametrize("approval", [None, "wrong"])
def test_execute_requires_exact_approval_before_boundaries(candidate, approval):
    root, bundle, _, calls, _ = candidate
    with pytest.raises(launch.LaunchError, match="exact approved"):
        launch.launch(root, bundle, execute=True, approval=approval)
    assert calls == []


def test_same_approval_is_passed_to_ordered_stages_and_pending_is_not_completion(candidate):
    root, bundle, identity, calls, _ = candidate
    result = launch.launch(root, bundle, execute=True, approval=identity)
    assert calls == [("publish", identity), ("activate", identity)]
    assert result["publication_verified"] is True
    assert result["monitoring_configured"] is True
    assert result["manual_runs_verified"] is False
    assert result["scheduled_coverage_verified"] is False
    assert result["notification_delivery_verified"] is False
    assert "complete" not in result


def test_failed_publication_prevents_activation(candidate, monkeypatch):
    root, bundle, identity, calls, _ = candidate

    def fail(*args, **kwargs):
        calls.append(("publish", kwargs["approval"]))
        raise launch.release.ReleaseError("publication remains uncertain")

    monkeypatch.setattr(launch.release, "publish", fail)
    with pytest.raises(launch.LaunchError) as error:
        launch.launch(root, bundle, execute=True, approval=identity)
    assert error.value.stage == "publication"
    assert calls == [("publish", identity)]


def test_artifact_drift_after_publication_prevents_activation(candidate, monkeypatch):
    root, bundle, identity, calls, publish = candidate

    def drifting(*args, **kwargs):
        result = publish(*args, **kwargs)
        (bundle / "approved.txt").write_text("changed candidate")
        return result

    monkeypatch.setattr(launch.release, "publish", drifting)
    with pytest.raises(launch.LaunchError, match="approved digest") as error:
        launch.launch(root, bundle, execute=True, approval=identity)
    assert error.value.stage == "after_publication"
    assert calls == [("publish", identity)]


def test_unconfirmed_publication_result_or_journal_prevents_activation(candidate, monkeypatch):
    root, bundle, identity, calls, _ = candidate
    monkeypatch.setattr(launch.release, "publish", lambda *args, **kwargs: {"digest": identity, "published": False})
    with pytest.raises(launch.LaunchError, match="did not confirm"):
        launch.launch(root, bundle, execute=True, approval=identity)
    monkeypatch.setattr(launch.release, "publish", lambda *args, **kwargs: {"digest": identity, "published": True})
    with pytest.raises(launch.LaunchError, match="completed and verified"):
        launch.launch(root, bundle, execute=True, approval=identity)
    assert calls == []


def test_activation_failure_preserves_publication_and_reports_stage(candidate, monkeypatch):
    root, bundle, identity, calls, _ = candidate

    def fail(*args, **kwargs):
        calls.append(("activate", kwargs["approval"]))
        raise launch.release.ReleaseError("dispatch outcome uncertain")

    monkeypatch.setattr(launch.activate_monitoring, "activation", fail)
    with pytest.raises(launch.LaunchError) as error:
        launch.launch(root, bundle, execute=True, approval=identity)
    assert error.value.stage == "monitoring_activation"
    journal = json.loads(launch.activate_monitoring.journal_path(bundle).read_text())
    assert journal["complete"] is True
    assert calls == [("publish", identity), ("activate", identity)]


def test_check_only_delegates_to_read_only_monitoring_check(candidate):
    root, bundle, _, calls, _ = candidate
    result = launch.launch(root, bundle, mode="check")
    assert calls == [("check", None)]
    assert result["mode"] == "check" and result["remote_writes"] is False


def test_no_execution_allowed_in_status_or_check(candidate):
    root, bundle, identity, calls, _ = candidate
    for mode in ("status", "check"):
        with pytest.raises(launch.LaunchError, match="read-only"):
            launch.launch(root, bundle, mode=mode, execute=True, approval=identity)
    assert calls == []
