"""Offline release transaction checks. No provider or publication calls are made."""

import importlib.util
import io
import json
import subprocess
import tarfile
import time
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("release", Path(__file__).parents[1] / "local/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    files = {"README.md": b"Browser Bolt", "LICENSE": b"MIT", "requirements-mcp.txt": b"hashes",
             "sbom-mcp.cdx.json": b"{}", "site/build-static.mjs": b"// static builder"}
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    monkeypatch.setattr(release, "PUBLIC_FILES", tuple(files))
    monkeypatch.setattr(release, "PUBLIC_GLOBS", ())
    bundle = tmp_path / "candidate"
    calls = []

    def builder(args, **kwargs):
        calls.append(args)
        if args[0] == "uv":
            output = Path(args[3])
            output.mkdir()
            (output / ".gitignore").write_bytes(b"*")
            with zipfile.ZipFile(output / "jev_qwerebras_ultrafast-0.1.0-py3-none-any.whl", "w") as archive:
                archive.writestr("jev_ultrafast/__init__.py", b"")
            with tarfile.open(output / "jev_qwerebras_ultrafast-0.1.0.tar.gz", "w:gz") as archive:
                item = tarfile.TarInfo("jev_qwerebras_ultrafast-0.1.0/README.md")
                item.size = len(files["README.md"])
                archive.addfile(item, io.BytesIO(files["README.md"]))
        else:
            site = bundle / "site"
            site.mkdir()
            (site / "index.html").write_text("Browser Bolt")
            (site / "_headers").write_text("headers")
            (site / "version.json").write_text(json.dumps({"releaseId": "a" * 64}))
        return b""

    config = {"cloudflare_account_id": "a" * 32, "workers_subdomain": "example"}
    identity = release.prepare(root, bundle, config, builder)
    return root, bundle, identity, calls


def test_prepare_fresh_artifacts_config_and_exact_hashes(candidate):
    root, bundle, identity, calls = candidate
    manifest, actual = release.verify(root, bundle)
    assert actual == identity
    assert calls[0][:2] == ["uv", "build"]
    assert calls[1][0] == "node"
    assert manifest["upstream_commit"] == release.UPSTREAM_COMMIT
    assert manifest["config"]["github_repo"] == "valente-labs/browser-bolt"
    assert "wrangler.json" in manifest["files"]
    with pytest.raises(release.ReleaseError, match="already exists"):
        release.prepare(root, bundle, {})


@pytest.mark.parametrize("mutation", ["source", "artifact", "extra", "symlink", "config"])
def test_verify_refuses_drift_and_unexpected_files(candidate, mutation):
    root, bundle, _, _ = candidate
    if mutation == "source":
        (root / "README.md").write_text("changed")
    elif mutation == "artifact":
        (bundle / "site/index.html").write_text("changed")
    elif mutation == "extra":
        (bundle / "site/private.json").write_text("unexpected")
    elif mutation == "symlink":
        (bundle / "site/alias").symlink_to(root / "README.md")
    else:
        (bundle / "wrangler.json").write_text('{"routes": ["unexpected.example"]}')
    with pytest.raises(release.ReleaseError):
        release.verify(root, bundle)


@pytest.mark.parametrize("path,content", [("docs/launch/notes.md", b"private"),
                                          (".env.local", b"secret"),
                                          ("config.txt", b"ghp_" + b"x" * 40),
                                          ("notes.md", b"/" + b"Users/private/client/")])
def test_public_guard_rejects_internal_paths_and_credentials(path, content):
    with pytest.raises(release.ReleaseError):
        release.check_public(path, content)


def test_default_and_wrong_approval_never_contact_remote(candidate):
    root, bundle, _, _ = candidate

    class Never:
        def preflight(self, state):
            pytest.fail("Unauthorized remote contact")

    result = release.publish(root, bundle, remote=Never())
    assert result["mode"] == "preview" and result["published"] is False
    with pytest.raises(release.ReleaseError, match="Approval digest"):
        release.publish(root, bundle, "wrong", Never())
    assert not bundle.with_name(bundle.name + ".publication.json").exists()


class FakeRemote:
    def __init__(self):
        self.calls = []
        self.fail = True
        self.creates = 0

    def preflight(self, state):
        self.calls.append("preflight")

    def ensure_repo(self, state, persist):
        if not state.get("repo_id"):
            self.creates += 1
            state["repo_id"] = 12
            persist()

    def ensure_commit(self, bundle, identity, state, persist):
        state["commit"] = "b" * 40
        persist()

    def ensure_release(self, bundle, identity, state, persist):
        state["release_id"] = 34
        persist()

    def deploy(self, bundle, state, persist):
        self.calls.append("deploy")
        if self.fail:
            self.fail = False
            raise release.ReleaseError("synthetic partial failure")
        state["site_verified"] = True
        persist()
        return "https://browser-bolt.example.workers.dev"

    def check_release_tag(self, state, *, required=False):
        assert state["commit"] == "b" * 40

    def gh(self, endpoint, method, body):
        assert body == {"draft": False}
        self.calls.append("publish_release")
        return {"id": 34, "draft": False}


def test_partial_failure_resumes_known_repo_and_never_publishes_early(candidate):
    root, bundle, identity, _ = candidate
    service = FakeRemote()
    with pytest.raises(release.ReleaseError, match="partial failure"):
        release.publish(root, bundle, identity, service)
    assert "publish_release" not in service.calls
    assert service.creates == 1
    result = release.publish(root, bundle, identity, service)
    assert result["published"] is True
    assert service.creates == 1
    assert service.calls[-1] == "publish_release"
    release.verify(root, bundle)  # Journal/canary/temp outputs do not alter approved bytes.


def test_concurrent_launch_and_wrong_journal_refused(candidate):
    root, bundle, identity, _ = candidate
    lock = bundle.with_name(bundle.name + ".publication.lock")
    lock.write_text("running")
    with pytest.raises(release.ReleaseError, match="lock exists"):
        release.publish(root, bundle, identity, FakeRemote())
    lock.unlink()
    journal = bundle.with_name(bundle.name + ".publication.json")
    journal.write_text('{"digest":"other"}')
    with pytest.raises(release.ReleaseError, match="another candidate"):
        release.publish(root, bundle, identity, FakeRemote())


def test_subprocess_failure_output_is_not_exposed(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 1, b"secret value", b"secret value"))
    with pytest.raises(release.ReleaseError) as failure:
        release.run(["fake-command", "status"])
    assert "secret" not in str(failure.value)


def test_preflight_refuses_unknown_repository(monkeypatch):
    service = release.Remote(release.config_values({"cloudflare_account_id": "a" * 32,
                                                    "workers_subdomain": "example"}))
    monkeypatch.setattr(release, "run", lambda *args, **kwargs: b"version")
    monkeypatch.setattr(service, "gh", lambda endpoint, **kwargs: {"id": 55})
    with pytest.raises(release.ReleaseError, match="already exists"):
        service.preflight({})


def test_commit_preserves_upstream_parent_and_does_not_update_existing_branch(candidate, monkeypatch):
    _, bundle, identity, _ = candidate
    service = release.Remote(release.DEFAULT_CONFIG)
    calls = []

    def gh(endpoint, method="GET", body=None, **kwargs):
        calls.append((endpoint, method, body))
        if method == "GET":
            return None
        return {"sha": "b" * 40}

    monkeypatch.setattr(service, "gh", gh)
    state = {}
    service.ensure_commit(bundle, identity, state, lambda: None)
    trees = [body for endpoint, _, body in calls if endpoint.endswith("/git/trees")]
    commits = [body for endpoint, _, body in calls if endpoint.endswith("/git/commits")]
    assert "base_tree" not in trees[0]
    assert commits[0]["parents"] == [release.UPSTREAM_COMMIT]
    assert not any("force" in (body or {}) for _, _, body in calls)
    monkeypatch.setattr(service, "gh", lambda *args, **kwargs: {"object": {"sha": "unknown"}})
    with pytest.raises(release.ReleaseError, match="unknown or changed"):
        service.ensure_commit(bundle, identity, state, lambda: None)


def test_deploy_records_intent_and_does_not_repeat_unknown_outcome(candidate, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "synthetic-test-token")
    _, bundle, identity, _ = candidate
    service = release.Remote(release.DEFAULT_CONFIG)
    monkeypatch.setattr(service, "deployment_matches", lambda state: None)
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise release.ReleaseError("lost response")

    monkeypatch.setattr(release, "run", fail)
    state = {"digest": identity}
    persisted = []
    with pytest.raises(release.ReleaseError, match="lost response"):
        service.deploy(bundle, state, lambda: persisted.append(dict(state)))
    assert persisted[0]["deploy_started"] is True
    with pytest.raises(release.ReleaseError, match="uncertain"):
        service.deploy(bundle, state, lambda: None)
    assert len(calls) == 1


def test_release_hashes_checked_after_upload_and_on_resume(candidate, monkeypatch):
    _, bundle, identity, _ = candidate
    service = release.Remote(release.DEFAULT_CONFIG)
    state = {"commit": "b" * 40}
    remote_release = {"id": 7, "draft": True, "assets": []}
    existing = []

    def gh(endpoint, method="GET", body=None, **kwargs):
        if "releases?" in endpoint:
            return existing
        if "/git/ref/tags/" in endpoint:
            return None
        if method == "POST":
            remote_release.update(body)
            existing.append(remote_release)
        return remote_release

    def upload(args, **kwargs):
        path = Path(args[4])
        remote_release["assets"].append({"name": path.name, "digest": "sha256:" + release.digest(path.read_bytes())})
        return b""

    monkeypatch.setattr(service, "gh", gh)
    monkeypatch.setattr(release, "run", upload)
    service.ensure_release(bundle, identity, state, lambda: None)
    service.ensure_release(bundle, identity, state, lambda: None)
    assert len(remote_release["assets"]) == 6
    remote_release["assets"][0]["digest"] = "sha256:wrong"
    with pytest.raises(release.ReleaseError, match="differs"):
        service.ensure_release(bundle, identity, state, lambda: None)


def test_preflight_command_is_read_only(candidate, monkeypatch, capsys):
    root, bundle, _, _ = candidate
    monkeypatch.setattr(release, "ROOT", root)
    monkeypatch.setattr(release.Remote, "preflight", lambda self, state: (None, None))
    assert release.main(["preflight", "--bundle", str(bundle)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]
    assert not bundle.with_name(bundle.name + ".publication.json").exists()


def test_known_deployment_reentry_verifies_each_file_without_deploying(candidate, monkeypatch):
    _, bundle, identity, _ = candidate
    config = release.config_values({"workers_subdomain": "example"})
    service = release.Remote(config)
    monkeypatch.setattr(service, "deployment_matches", lambda state: "deployment-1")
    fetched = []
    commands = []

    class Opener:
        def open(self, url, timeout):
            route = url.removeprefix(release.public_url(config))
            fetched.append(route)
            return io.BytesIO((bundle / "site" / ("index.html" if route == "/" else route[1:])).read_bytes())

    monkeypatch.setattr(release, "build_opener", lambda *args: Opener())
    def canary_run(args, **kwargs):
        commands.append(args)
        assert kwargs["allow_failure"] is True
        return 0, release.canonical(sample_canary())

    monkeypatch.setattr(release, "run", canary_run)
    state = {"digest": identity, "deploy_started": True}
    assert service.deploy(bundle, state, lambda: None) == release.public_url(config)
    assert fetched == ["/", "/version.json"]
    assert all(args[0] == "python3" for args in commands)
    assert state["site_verified"] is True


def test_public_manifest_excludes_private_local_configuration(candidate):
    _, bundle, _, _ = candidate
    public = json.loads((bundle / "public-manifest.json").read_text())
    assert "cloudflare_account_id" not in json.dumps(public)
    assert "wrangler" not in json.dumps(public)
    assert public["site"] == "https://browser-bolt.example.workers.dev"


@pytest.mark.parametrize("becomes_visible", [False, True])
def test_lost_release_create_response_never_repeats_post(candidate, monkeypatch, becomes_visible):
    _, bundle, identity, _ = candidate
    service = release.Remote(release.DEFAULT_CONFIG)
    state = {"commit": "b" * 40}
    saved = []
    posts = []
    visible = []
    created = {}
    assets = [bundle / "public-manifest.json", *sorted((bundle / "packages").iterdir())]

    def gh(endpoint, method="GET", body=None, **kwargs):
        if "releases?" in endpoint:
            return visible
        if "/git/ref/tags/" in endpoint:
            return None
        if method == "POST":
            posts.append(body)
            assert saved[-1]["release_create_started"] is True
            created.update(body | {"id": 7, "assets": [
                {"name": path.name, "digest": "sha256:" + release.digest(path.read_bytes())} for path in assets]})
            raise release.ReleaseError("response lost after remote release creation")
        return created

    monkeypatch.setattr(service, "gh", gh)
    with pytest.raises(release.ReleaseError, match="response lost"):
        service.ensure_release(bundle, identity, state, lambda: saved.append(dict(state)))
    if becomes_visible:
        visible.append(created)
        service.ensure_release(bundle, identity, state, lambda: saved.append(dict(state)))
        assert state["release_id"] == 7
    else:
        with pytest.raises(release.ReleaseError, match="uncertain"):
            service.ensure_release(bundle, identity, state, lambda: saved.append(dict(state)))
    assert len(posts) == 1


@pytest.mark.parametrize("endpoint_suffix,state_key", [("/git/commits", "commit_create_started"),
                                                       ("/git/refs", "branch_create_started")])
def test_lost_commit_or_branch_create_response_is_not_repeated(candidate, monkeypatch, endpoint_suffix, state_key):
    _, bundle, identity, _ = candidate
    service = release.Remote(release.DEFAULT_CONFIG)
    state = {}
    posts = []
    saved = []

    def gh(endpoint, method="GET", body=None, **kwargs):
        if method == "GET":
            return None
        if endpoint.endswith(endpoint_suffix):
            posts.append(body)
            assert saved[-1][state_key] is True
            raise release.ReleaseError("response lost after create")
        return {"sha": "b" * 40}

    monkeypatch.setattr(service, "gh", gh)
    with pytest.raises(release.ReleaseError, match="response lost"):
        service.ensure_commit(bundle, identity, state, lambda: saved.append(dict(state)))
    with pytest.raises(release.ReleaseError, match="uncertain"):
        service.ensure_commit(bundle, identity, state, lambda: saved.append(dict(state)))
    assert len(posts) == 1


def test_lost_asset_upload_response_is_not_repeated_if_asset_is_not_visible(candidate, monkeypatch):
    _, bundle, identity, _ = candidate
    service = release.Remote(release.DEFAULT_CONFIG)
    state = {"commit": "b" * 40, "release_create_started": True, "release_id": 7}
    existing = {"id": 7, "body": f"Approved artifact manifest SHA-256: {identity}", "tag_name": "v0.1.0",
                "target_commitish": state["commit"], "draft": True, "assets": []}
    monkeypatch.setattr(service, "gh", lambda endpoint, **kwargs: [existing] if "releases?" in endpoint else None)
    uploads = []

    def upload(args, **kwargs):
        uploads.append(args)
        raise release.ReleaseError("response lost after upload")

    monkeypatch.setattr(release, "run", upload)
    with pytest.raises(release.ReleaseError, match="response lost"):
        service.ensure_release(bundle, identity, state, lambda: None)
    with pytest.raises(release.ReleaseError, match="uncertain"):
        service.ensure_release(bundle, identity, state, lambda: None)
    assert len(uploads) == 1


def test_candidate_replaced_during_preflight_cannot_use_old_approval(candidate):
    root, bundle, identity, _ = candidate

    class SwappingRemote(FakeRemote):
        def preflight(self, state):
            self.calls.append("preflight")
            (bundle / "site/index.html").write_text("Different reviewed candidate")
            manifest = json.loads((bundle / "manifest.json").read_text())
            manifest["files"]["site/index.html"] = release.digest((bundle / "site/index.html").read_bytes())
            (bundle / "manifest.json").write_text(json.dumps(manifest))
            assert release.verify(root, bundle)[1] != identity

    service = SwappingRemote()
    service.fail = False
    with pytest.raises(release.ReleaseError, match="approved digest"):
        release.publish(root, bundle, identity, service)
    assert service.creates == 0
    assert service.calls == ["preflight"]


def test_wrangler_environment_excludes_destination_overrides_and_unrelated_credentials(candidate, monkeypatch):
    _, bundle, identity, _ = candidate
    config = release.config_values({"cloudflare_account_id": "a" * 32, "workers_subdomain": "example"})
    service = release.Remote(config)
    monkeypatch.setattr(service, "deployment_matches", lambda state: None)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "synthetic-test-token")
    for name in ("CLOUDFLARE_ENV", "CLOUDFLARE_API_BASE_URL", "CLOUDFLARE_BASE_URL", "CLOUDFLARE_API_KEY",
                 "GH_TOKEN", "OPENROUTER_API_KEY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NODE_OPTIONS",
                 "WRANGLER_LOG_PATH", "CLOUDFLARE_ACCOUNT_ID"):
        monkeypatch.setenv(name, "unexpected-override")
    captured = []

    def dispatch(args, **kwargs):
        captured.append(kwargs["env"])
        raise release.ReleaseError("stop after inspecting dispatch")

    monkeypatch.setattr(release, "run", dispatch)
    with pytest.raises(release.ReleaseError, match="inspecting dispatch"):
        service.deploy(bundle, {"digest": identity}, lambda: None)
    env = captured[0]
    assert set(env) == {"PATH", "HOME", "TMPDIR", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CI",
                        "WRANGLER_SEND_METRICS"}
    assert env["CLOUDFLARE_API_TOKEN"] == "synthetic-test-token"
    assert env["CLOUDFLARE_ACCOUNT_ID"] == config["cloudflare_account_id"]
    assert env["CI"] == "true"
    assert env["WRANGLER_SEND_METRICS"] == "false"
    assert "unexpected-override" not in env.values()


def test_worker_version_reconciliation_uses_provider_top_level_annotations(monkeypatch):
    service = release.Remote(release.DEFAULT_CONFIG)
    identity = "a" * 64
    responses = {
        "/scripts/browser-bolt/deployments": {"deployments": [
            {"id": "deployment-id", "versions": [{"version_id": "version-id", "percentage": 100}]}]},
        "/scripts/browser-bolt/versions/version-id": {
            "id": "version-id", "metadata": {"created_on": "2026-09-21T12:00:00Z"},
            "annotations": {"workers/tag": identity, "workers/message": "Approved Browser Bolt preview"}},
    }
    monkeypatch.setattr(service, "cf", lambda suffix, **kwargs: responses[suffix])
    assert service.deployment_matches({"digest": identity}) == "deployment-id"
    responses["/scripts/browser-bolt/versions/version-id"]["annotations"]["workers/tag"] = "different"
    with pytest.raises(release.ReleaseError, match="not this approved candidate"):
        service.deployment_matches({"digest": identity})


@pytest.mark.parametrize("ready", [False, True])
def test_fork_creation_waits_for_readiness_without_recreating(monkeypatch, ready):
    service = release.Remote(release.DEFAULT_CONFIG)
    state = {}
    posts = []
    reads = []
    sleeps = []

    def gh(endpoint, method="GET", body=None, **kwargs):
        if method == "POST":
            posts.append(body)
            return {"id": 12}
        reads.append(endpoint)
        if not ready or len(reads) < 3:
            return None
        if "/git/commits/" in endpoint:
            return {"sha": release.UPSTREAM_COMMIT}
        return {"id": 12, "fork": True, "private": False, "parent": {"full_name": release.UPSTREAM}}

    monkeypatch.setattr(service, "gh", gh)
    # Patch the imported time module; this test must never actually wait.
    monkeypatch.setattr(time, "sleep", lambda delay: sleeps.append(delay))
    if ready:
        service.ensure_repo(state, lambda: None)
        assert len(reads) >= 3
        assert sleeps
    else:
        for _ in range(2):
            with pytest.raises(release.ReleaseError, match="readiness timed out"):
                service.ensure_repo(state, lambda: None)
    assert state["repo_id"] == 12
    assert len(posts) == 1


def test_resumed_release_rejects_actual_tag_pointing_at_another_commit(candidate, monkeypatch):
    _, bundle, identity, _ = candidate
    service = release.Remote(release.DEFAULT_CONFIG)
    state = {"commit": "b" * 40, "release_create_started": True, "release_id": 7}
    existing = {"id": 7, "body": f"Approved artifact manifest SHA-256: {identity}", "tag_name": "v0.1.0",
                "target_commitish": state["commit"], "draft": True, "assets": []}

    def gh(endpoint, method="GET", body=None, **kwargs):
        assert method == "GET"
        if "releases?" in endpoint:
            return [existing]
        return {"object": {"type": "commit", "sha": "c" * 40}}

    monkeypatch.setattr(service, "gh", gh)
    monkeypatch.setattr(release, "run", lambda *args, **kwargs: pytest.fail("Conflicting tag must stop before upload"))
    with pytest.raises(release.ReleaseError, match="tag does not resolve"):
        service.ensure_release(bundle, identity, state, lambda: None)


def sample_canary(ok=True):
    return {"ok": ok, "checks": [
        {"check": name, "ok": ok, "code": "ok" if ok else "transport_failure",
         "status": 200 if ok else None, "duration_ms": 1.0}
        for name in ("home", "setup", "brand", "methods", "version", "missing", "private")]}


@pytest.mark.parametrize("exposure", ["changing", "unknown", "enabled_unverified", "paused", "pending", "failed", None])
def test_publication_refuses_unrestored_recovery(candidate, exposure):
    root, bundle, identity, _ = candidate
    journal = bundle.with_name(bundle.name + ".publication.json")
    journal.write_text(json.dumps({"digest": identity, "recovery": {"exposure": exposure}}))
    service = FakeRemote()
    with pytest.raises(release.ReleaseError, match="Recovery exposure"):
        release.publish(root, bundle, identity, service)
    assert service.calls == []
    assert service.creates == 0


@pytest.mark.parametrize("healthy", [False, True])
def test_verify_site_persists_sanitized_canary_evidence(candidate, monkeypatch, healthy):
    _, bundle, _, _ = candidate
    service = release.Remote(release.config_values({"workers_subdomain": "example"}))

    class Opener:
        def open(self, url, timeout):
            route = url.removeprefix(release.public_url(service.config))
            return io.BytesIO((bundle / "site" / ("index.html" if route == "/" else route[1:])).read_bytes())

    monkeypatch.setattr(release, "build_opener", lambda *args: Opener())
    result = sample_canary(healthy)
    result["private_extra"] = "never persist provider or credential output"
    monkeypatch.setattr(release, "run", lambda *args, **kwargs: (0 if healthy else 1, release.canonical(result)))
    state = {}
    saved = []
    if healthy:
        service.verify_site(bundle, state, lambda: saved.append(json.loads(json.dumps(state))))
    else:
        with pytest.raises(release.ReleaseError, match="evidence is saved"):
            service.verify_site(bundle, state, lambda: saved.append(json.loads(json.dumps(state))))
    assert saved[-1]["canary"]["ok"] is healthy
    assert saved[-1]["canary"]["release_id"] == "a" * 64
    assert len(saved[-1]["canary"]["checks"]) == 7
    assert "private_extra" not in json.dumps(saved)
    assert "never persist" not in json.dumps(saved)


def test_required_publication_tag_must_exist_and_annotated_tag_is_peeled(monkeypatch):
    service = release.Remote(release.DEFAULT_CONFIG)
    state = {"commit": "b" * 40}
    monkeypatch.setattr(service, "gh", lambda *args, **kwargs: None)
    with pytest.raises(release.ReleaseError, match="not yet readable"):
        service.check_release_tag(state, required=True)
    responses = [{"object": {"type": "tag", "sha": "a" * 40}},
                 {"object": {"type": "commit", "sha": state["commit"]}}]
    monkeypatch.setattr(service, "gh", lambda *args, **kwargs: responses.pop(0))
    service.check_release_tag(state, required=True)
    assert not responses


def test_failed_public_hash_recheck_invalidates_prior_verification(candidate, monkeypatch):
    _, bundle, _, _ = candidate
    service = release.Remote(release.config_values({"workers_subdomain": "example"}))

    class Opener:
        def open(self, url, timeout):
            return io.BytesIO(b"changed public bytes")

    monkeypatch.setattr(release, "build_opener", lambda *args: Opener())
    state = {"site_verified": True, "complete": True, "canary": {"ok": True}}
    saved = []
    with pytest.raises(release.ReleaseError, match="hash differs"):
        service.verify_site(bundle, state, lambda: saved.append(json.loads(json.dumps(state))))
    assert saved and saved[-1]["site_verified"] is False
    assert saved[-1]["canary"]["ok"] is False
    assert saved[-1]["complete"] is True  # Historical publication is not undone by a failed observation.
