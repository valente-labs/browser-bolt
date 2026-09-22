"""Offline rehearsal: ownership, approval, concurrency, and uncertain recovery outcomes."""

import io
import json
import tarfile
import zipfile

import pytest

from local import recovery

release = recovery.release


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "README.md").write_text("Browser Bolt fixture\n")
    monkeypatch.setattr(release, "PUBLIC_FILES", ("README.md",))
    monkeypatch.setattr(release, "PUBLIC_GLOBS", ())
    bundle = tmp_path / "candidate"
    (bundle / "source").mkdir(parents=True)
    (bundle / "source/README.md").write_bytes((root / "README.md").read_bytes())
    (bundle / "site").mkdir()
    (bundle / "site/index.html").write_text("approved site")
    (bundle / "site/version.json").write_text('{"releaseId":"candidate"}')
    (bundle / "packages").mkdir()
    with zipfile.ZipFile(bundle / "packages/jev_qwerebras_ultrafast-0.1.0-py3-none-any.whl", "w") as archive:
        archive.writestr("fixture.py", "pass\n")
    with tarfile.open(bundle / "packages/jev_qwerebras_ultrafast-0.1.0.tar.gz", "w:gz"):
        pass
    for name in ("requirements-mcp.txt", "sbom-mcp.cdx.json", "SHA256SUMS"):
        (bundle / "packages" / name).write_text("fixture")
    config = release.config_values({"cloudflare_account_id": "a" * 32, "workers_subdomain": "approved"})
    manifest = {"schema": 1, "product": "Browser Bolt", "upstream": release.UPSTREAM,
                "upstream_commit": release.UPSTREAM_COMMIT, "config": config,
                "inputs": release.inventory(root), "files": release.inventory(bundle)}
    release.save(bundle / "manifest.json", manifest)
    _, identity = release.verify(root, bundle)
    state = {"digest": identity, "deployment_id": "deployment-1", "complete": True, "repo_id": 123}
    release.save(bundle.with_name("candidate.publication.json"), state)
    # Every actual HTTP attempt is an error, including unexpected calls in plan mode.
    monkeypatch.setattr(release.Remote, "cf", lambda *a, **k: pytest.fail("Unexpected live Cloudflare call"))
    return root, bundle, identity


class FakeRemote(recovery.RecoveryRemote):
    """Exercise the real ownership and fixed-state helpers through a fake CF boundary."""

    def __init__(self, candidate):
        root, bundle, self.identity = candidate
        manifest, _ = release.verify(root, bundle)
        super().__init__(manifest["config"])
        self.flags = {"enabled": True, "previews_enabled": False}
        self.deployment = "deployment-1"
        self.tag = self.identity
        self.calls = []
        self.uncertain = False
        self.apply = True
        self.bad_response = False
        self.canary_failure = False
        self.changed_after_update = False
        self.verified = False

    def cf(self, suffix="", method="GET", body=None, missing=False):
        self.calls.append((method, suffix, body))
        if suffix == "/subdomain":
            return {"subdomain": "approved"}
        if suffix.endswith("/deployments"):
            return {"deployments": [{"id": self.deployment,
                     "versions": [{"version_id": "version-1", "percentage": 100}]}]}
        if suffix.endswith("/versions/version-1"):
            return {"annotations": {"workers/tag": self.tag}}
        if suffix == "/scripts/browser-bolt/subdomain":
            if method == "GET":
                return dict(self.flags)
            assert method == "POST"
            if self.apply:
                self.flags = dict(body)
            if self.changed_after_update:
                self.deployment = "other-deployment"
            if self.uncertain:
                raise release.ReleaseError("Cloudflare transport failed; details suppressed")
            return {} if self.bad_response else dict(self.flags)
        pytest.fail(f"Unexpected operation: {method} {suffix}")

    def verify_site(self, bundle, state, persist):
        assert self.flags == {"enabled": True, "previews_enabled": False}
        self.verified = True
        if self.canary_failure:
            raise release.ReleaseError("Static canary failed")
        state["site_verified"] = True
        persist()


def state(candidate):
    return json.loads(candidate[1].with_name("candidate.publication.json").read_text())


def invoke(candidate, service, action="pause", **kwargs):
    root, bundle, identity = candidate
    return recovery.recover(root, bundle, action, execute=True, approval=identity, remote=service, **kwargs)


def test_default_is_offline_and_leaves_no_evidence(candidate):
    root, bundle, identity = candidate
    prior = state(candidate)
    result = recovery.recover(root, bundle)
    assert result["mode"] == "plan" and result["digest"] == identity
    assert result["network_checked"] is False
    assert result["executed"] is False
    assert state(candidate) == prior
    assert not bundle.with_name("candidate.publication.lock").exists()


@pytest.mark.parametrize("approval", [None, "f" * 64])
def test_execute_requires_exact_digest_before_network(candidate, approval):
    root, bundle, _ = candidate
    service = FakeRemote(candidate)
    with pytest.raises(release.ReleaseError, match="digest"):
        recovery.recover(root, bundle, execute=True, approval=approval, remote=service)
    assert service.calls == []


@pytest.mark.parametrize("change", ["missing", "wrong_digest", "no_deployment"])
def test_matching_existing_journal_required(candidate, change):
    root, bundle, identity = candidate
    path = bundle.with_name("candidate.publication.json")
    data = state(candidate)
    if change == "missing":
        path.rename(path.with_suffix(".preserved"))
    else:
        if change == "wrong_digest":
            data["digest"] = "0" * 64
        else:
            data.pop("deployment_id")
        release.save(path, data)
    service = FakeRemote(candidate)
    with pytest.raises(release.ReleaseError, match="journal"):
        invoke(candidate, service)
    assert service.calls == []


@pytest.mark.parametrize("change", ["tag", "deployment"])
def test_unknown_remote_ownership_cannot_pause(candidate, change):
    service = FakeRemote(candidate)
    setattr(service, change, "unrelated")
    with pytest.raises(release.ReleaseError, match="candidate|journal"):
        invoke(candidate, service)
    assert not any(method != "GET" for method, _, _ in service.calls)
    assert "recovery" not in state(candidate)


def test_shared_publisher_lock_excludes_recovery(candidate):
    service = FakeRemote(candidate)
    lock = candidate[1].with_name("candidate.publication.lock")
    lock.write_text("publisher owns this")
    with pytest.raises(release.ReleaseError, match="lock"):
        invoke(candidate, service)
    assert lock.read_text() == "publisher owns this"
    assert service.calls == []


@pytest.mark.parametrize("location", ["source", "artifact"])
def test_changed_bytes_do_not_silently_approve(candidate, location):
    service = FakeRemote(candidate)
    path = candidate[0] / "README.md" if location == "source" else candidate[1] / "site/index.html"
    path.write_text("changed after approval")
    with pytest.raises(release.ReleaseError, match="drift"):
        invoke(candidate, service)
    assert service.calls == []


@pytest.mark.parametrize("uncertain,bad_response", [(False, False), (True, False), (False, True)])
def test_pause_reconciles_fixed_state_and_preserves_journal(candidate, uncertain, bad_response):
    service = FakeRemote(candidate)
    service.uncertain, service.bad_response = uncertain, bad_response
    result = invoke(candidate, service)
    assert result["mode"] == "paused" and not result["public_site_verified"]
    assert state(candidate)["repo_id"] == 123
    assert state(candidate)["complete"] is True
    attempt = state(candidate)["recovery"]["attempts"][0]
    assert attempt["after"] == {"enabled": False, "previews_enabled": False}
    assert attempt["result"] == "confirmed"
    mutations = [(method, path, body) for method, path, body in service.calls if method != "GET"]
    assert mutations == [("POST", "/scripts/browser-bolt/subdomain",
                          {"enabled": False, "previews_enabled": False})]
    assert not candidate[1].with_name("candidate.publication.lock").exists()


def test_uncertain_pause_that_did_not_apply_fails_and_records_evidence(candidate):
    service = FakeRemote(candidate)
    service.uncertain, service.apply = True, False
    with pytest.raises(release.ReleaseError, match="fixed state"):
        invoke(candidate, service)
    evidence = state(candidate)["recovery"]
    assert evidence["exposure"] == "unknown"
    assert evidence["attempts"][0]["result"] == "failed"


def test_pause_retries_do_not_toggle_or_repeat_mutation(candidate):
    service = FakeRemote(candidate)
    invoke(candidate, service)
    invoke(candidate, service)
    assert sum(method == "POST" for method, _, _ in service.calls) == 1
    assert len(state(candidate)["recovery"]["attempts"]) == 2


def test_resume_requires_prior_recovery(candidate):
    service = FakeRemote(candidate)
    with pytest.raises(release.ReleaseError, match="earlier recovery"):
        invoke(candidate, service, "resume")
    assert service.calls == []


@pytest.mark.parametrize("uncertain", [False, True])
def test_resume_only_same_candidate_runs_public_verification(candidate, uncertain):
    service = FakeRemote(candidate)
    invoke(candidate, service)
    service.uncertain = uncertain
    result = invoke(candidate, service, "resume")
    assert result["mode"] == "restored" and result["public_site_verified"]
    assert service.verified
    assert state(candidate)["recovery"]["exposure"] == "restored"
    assert state(candidate)["deployment_id"] == "deployment-1"


def test_failed_resume_canary_is_non_success_and_retryable(candidate):
    service = FakeRemote(candidate)
    invoke(candidate, service)
    service.canary_failure = True
    with pytest.raises(release.ReleaseError, match="canary"):
        invoke(candidate, service, "resume")
    assert state(candidate)["recovery"]["exposure"] == "enabled_unverified"
    assert state(candidate)["recovery"]["attempts"][-1]["result"] == "failed"
    invoke(candidate, service, "pause")
    assert service.flags == {"enabled": False, "previews_enabled": False}


def test_ownership_change_during_pause_fails(candidate):
    service = FakeRemote(candidate)
    service.changed_after_update = True
    with pytest.raises(release.ReleaseError, match="journal"):
        invoke(candidate, service)
    assert state(candidate)["recovery"]["exposure"] == "unknown"


def test_cli_failure_returns_nonzero(candidate, monkeypatch, capsys):
    monkeypatch.setattr(release, "ROOT", candidate[0])
    assert recovery.main(["pause", "--bundle", str(candidate[1]), "--execute", "--approve", "bad"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_uncertain_resume_that_did_not_apply_cannot_claim_restored(candidate):
    service = FakeRemote(candidate)
    invoke(candidate, service)
    service.uncertain, service.apply = True, False
    with pytest.raises(release.ReleaseError, match="fixed state"):
        invoke(candidate, service, "resume")
    assert service.verified is False
    assert state(candidate)["recovery"]["exposure"] == "unknown"
    assert service.flags == {"enabled": False, "previews_enabled": False}


@pytest.mark.parametrize("failure", ["hash", "canary"])
def test_real_resume_verifier_failure_retains_failure_evidence(candidate, monkeypatch, failure):
    service = FakeRemote(candidate)
    invoke(candidate, service)
    service.verify_site = release.Remote.verify_site.__get__(service)
    fetched = []

    class Opener:
        def open(self, url, timeout):
            fetched.append(url)
            relative = "index.html" if url.endswith("/") else "version.json"
            content = (candidate[1] / "site" / relative).read_bytes()
            return io.BytesIO(b"different published bytes" if failure == "hash" else content)

    monkeypatch.setattr(release, "build_opener", lambda *a: Opener())
    subprocesses = []

    def run_canary(args, **kwargs):
        subprocesses.append(args)
        return 1, b'{"ok":false,"checks":[{"check":"home","ok":false,"code":"status_mismatch"}]}'

    monkeypatch.setattr(release, "run", run_canary)
    with pytest.raises(release.ReleaseError, match="hash|canary"):
        invoke(candidate, service, "resume")
    evidence = state(candidate)
    assert evidence["recovery"]["exposure"] == "enabled_unverified"
    assert evidence["recovery"]["attempts"][-1]["result"] == "failed"
    assert evidence["site_verified"] is False
    assert fetched
    if failure == "hash":
        assert subprocesses == []
    else:
        assert evidence["canary"]["ok"] is False
        assert len(subprocesses) == 1
        assert "static_canary.py" in subprocesses[0][1]


def test_publisher_cannot_bypass_pause(candidate):
    service = FakeRemote(candidate)
    invoke(candidate, service)
    with pytest.raises(release.ReleaseError, match="Recovery exposure"):
        release.publish(candidate[0], candidate[1], candidate[2], remote=service)
    assert state(candidate)["recovery"]["exposure"] == "paused"
