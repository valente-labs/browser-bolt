#!/usr/bin/env python3
"""Prepare an immutable Browser Bolt candidate; publication requires its exact digest."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "browser-use/jev-ultrafast"
UPSTREAM_COMMIT = "452c1ad2dd628008f1d5608f28158d76e49e6cc0"
PUBLIC_FILES = (
    "CONTRIBUTING.md", "CHANGELOG.md", "CODE_OF_CONDUCT.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml", ".github/ISSUE_TEMPLATE/config.yml",
    "README.md", "LICENSE", "SECURITY.md", "BENCHMARK.md", "HYBRID.md", "pyproject.toml", "uv.lock",
    ".env.example", ".gitignore", "requirements-mcp.txt", "sbom-mcp.cdx.json",
    ".github/workflows/public-ci.yml", ".github/workflows/public-journey.yml",
    "local/public_journey.py", "local/activate_monitoring.py", "docs/MONITOR_ACTIVATION.md",
    ".github/workflows/public-canary.yml",
    "docs/MCP.md", "docs/HOST.md", "docs/LIVE_HOST.md", "docs/MONITORING.md", "docs/RECOVERY.md", "docs/COMPARISON.md",
    "docs/NATIVE_COMPARISON.md", "docs/SPRINTS.md",
    "docs/UPSTREAM-README.md", "docs/RELEASE.md", "local/comparison_probe.py", "local/static_canary.py",
    "local/run_hybrid.py", "local/wheel_smoke.py", "local/monitor.py", "local/recovery.py",
    "local/browser_driver.js", "local/release.py", "local/launch.py", "docs/LAUNCH_COMMAND.md",
    "examples/flights.py", "site/build-static.mjs", "site/public/index.html", "site/public/assets/public-app.js",
    "site/public/assets/demo/browser-bolt-demo-mobile.mp4",
    "site/public/assets/demo/poster-mobile.png",
    "site/public/assets/demo/captions.mobile.en.vtt",
    "site/public/assets/demo/transcript-mobile.html",
    "site/public/assets/demo/transcript-mobile.md",
    "site/public/assets/demo/browser-bolt-demo.mp4",
    "site/public/assets/demo/poster.jpg",
    "site/public/assets/demo/captions.en.vtt",
    "site/public/assets/demo/transcript.html",
    "site/public/assets/demo/transcript.md",
    "site/public/assets/brand.js", "site/public/assets/styles.css", "site/public/assets/brand/browser-bolt.png",
)
PUBLIC_GLOBS = ("jev_ultrafast/**/*.py", "jev_ultrafast/**/*.js", "jev_ultrafast/**/*.html",
                "jev_ultrafast/**/*.css", "tests/*.py", "local/fixtures/*.html", "docs/benchmarks/*.json")
DEFAULT_CONFIG = {"github_repo": "valente-labs/browser-bolt", "tag": "v0.1.0", "cloudflare_account_id": "",
                  "cloudflare_script": "browser-bolt", "workers_subdomain": "", "wrangler": "wrangler"}
SECRET = re.compile(rb"(?:sk-(?:or-v1-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
                    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|/(?:Users|home)/[^/\s]+/)")


class ReleaseError(RuntimeError):
    """An actionable failure with no provider response or credential details."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    if path.is_symlink():
        raise ReleaseError("Refusing a symlink state file")
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    temporary.replace(path)


def run(args, *, cwd=None, env=None, data=None, allow_failure=False):
    """Never forward raw subprocess output on failure; it may contain credentials."""
    try:
        result = subprocess.run(args, cwd=cwd, env=env, input=data, capture_output=True, check=False)
    except OSError:
        raise ReleaseError(f"Required executable unavailable: {Path(args[0]).name}") from None
    if allow_failure:
        return result.returncode, result.stdout
    if result.returncode:
        raise ReleaseError(f"{Path(args[0]).name} failed (exit {result.returncode}); raw output suppressed")
    return result.stdout


def safe_file(path, root):
    relative = path.relative_to(root)
    current = root
    if root.is_symlink():
        raise ReleaseError("Symlink root refused")
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ReleaseError(f"Symlink refused: {relative}")
    if not path.is_file():
        raise ReleaseError(f"Missing regular file: {relative}")
    return path.read_bytes()


def public_files(root):
    paths = set(PUBLIC_FILES)
    for pattern in PUBLIC_GLOBS:
        paths.update(str(p.relative_to(root)) for p in root.glob(pattern))
    return sorted(paths)


def inventory(root):
    result = {}
    if root.is_symlink() or not root.is_dir():
        raise ReleaseError("Expected a regular artifact directory")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ReleaseError("Artifact symlink refused")
        if path.is_dir():
            continue
        result[path.relative_to(root).as_posix()] = digest(safe_file(path, root))
    return result


def check_public(path, data):
    parts = Path(path).parts
    if any(part in {"launch", "artifacts", ".git", "node_modules", "__pycache__"} for part in parts):
        raise ReleaseError(f"Internal path refused: {path}")
    if any(part.startswith(".env") and part != ".env.example" for part in parts):
        raise ReleaseError("Environment file refused")
    if SECRET.search(data):
        raise ReleaseError(f"Credential or private path pattern found in: {path}")


def public_url(config):
    if not config["workers_subdomain"]:
        return None
    return f"https://{config['cloudflare_script']}.{config['workers_subdomain']}.workers.dev"


def config_values(value):
    if set(value) - set(DEFAULT_CONFIG):
        raise ReleaseError("Unknown release configuration key")
    config = DEFAULT_CONFIG | value
    patterns = {"github_repo": r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", "tag": r"v[0-9]+\.[0-9]+\.[0-9]+",
                "cloudflare_account_id": r"(?:[a-fA-F0-9]{32})?", "cloudflare_script": r"[a-z0-9][a-z0-9-]{0,57}",
                "workers_subdomain": r"[a-z0-9-]*", "wrangler": r"[A-Za-z0-9_./-]+"}
    for key, pattern in patterns.items():
        if not isinstance(config[key], str) or not re.fullmatch(pattern, config[key]):
            raise ReleaseError(f"Invalid configuration: {key}")
    if config["github_repo"] == UPSTREAM:
        raise ReleaseError("Publishing into upstream is forbidden")
    return config


def check_archives(bundle):
    for path in (bundle / "packages").iterdir():
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as archive:
                for item in archive.infolist():
                    if item.filename.startswith("/") or ".." in Path(item.filename).parts:
                        raise ReleaseError("Unsafe wheel path")
                    if (item.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ReleaseError("Wheel symlink refused")
                    check_public(item.filename, archive.read(item))
        elif path.name.endswith(".tar.gz"):
            with tarfile.open(path) as archive:
                for item in archive.getmembers():
                    if item.issym() or item.islnk() or not (item.isfile() or item.isdir()):
                        raise ReleaseError("Source archive contains a non-regular entry")
                    if item.name.startswith("/") or ".." in Path(item.name).parts:
                        raise ReleaseError("Unsafe archive path")
                    if item.isfile():
                        check_public(item.name, archive.extractfile(item).read())


def prepare(root, destination, config, runner=run):
    config = config_values(config)
    if destination.exists() or destination.is_symlink():
        raise ReleaseError("Candidate already exists; choose a new directory (old candidates are retained)")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    source = destination / "source"
    source.mkdir()
    inputs = {}
    for relative in public_files(root):
        data = safe_file(root / relative, root)
        check_public(relative, data)
        inputs[relative] = digest(data)
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    packages = destination / "packages"
    packages.mkdir()
    with tempfile.TemporaryDirectory(prefix="browser-bolt-build-") as temporary:
        output = Path(temporary) / "packages"
        runner(["uv", "build", "--out-dir", str(output), str(source)], cwd=destination)
        for path in output.iterdir():
            if path.name == ".gitignore" and path.read_bytes() == b"*":
                continue  # uv owns this build-directory marker, not a package artifact.
            if not path.is_file() or path.is_symlink() or not path.name.endswith((".whl", ".tar.gz")):
                raise ReleaseError("Build produced an unexpected file")
            shutil.copyfile(path, packages / path.name)
    for name in ("requirements-mcp.txt", "sbom-mcp.cdx.json"):
        shutil.copyfile(source / name, packages / name)
    checksums = "".join(f"{value}  {name}\n" for name, value in inventory(packages).items())
    (packages / "SHA256SUMS").write_text(checksums)
    env = os.environ.copy()
    env.update({"STATIC_OUT": str(destination / "site"), "BROWSER_BOLT_REPOSITORY": config["github_repo"],
                "BROWSER_BOLT_RELEASE_TAG": config["tag"]})
    runner(["node", str(source / "site/build-static.mjs")], cwd=source, env=env)
    # Backend caches or generated files in the source are not part of the reviewed repository.
    if inventory(source) != inputs:
        raise ReleaseError("Build changed staged public source; candidate refused")
    check_archives(destination)
    save(destination / "wrangler.json", {"name": config["cloudflare_script"],
         "compatibility_date": "2026-09-21", "workers_dev": True, "preview_urls": False,
         "assets": {"directory": "./site", "html_handling": "auto-trailing-slash",
                    "not_found_handling": "404-page"}})
    public_manifest = {"schema": 1, "product": "Browser Bolt", "upstream": UPSTREAM,
                       "upstream_commit": UPSTREAM_COMMIT, "github_repo": config["github_repo"],
                       "tag": config["tag"], "site": public_url(config),
                       "files": {p: value for p, value in inventory(destination).items()
                                 if p.startswith(("source/", "packages/", "site/"))}}
    save(destination / "public-manifest.json", public_manifest)
    artifacts = inventory(destination)
    manifest = {"schema": 1, "product": "Browser Bolt", "upstream": UPSTREAM,
                "upstream_commit": UPSTREAM_COMMIT, "config": config, "inputs": inputs, "files": artifacts}
    save(destination / "manifest.json", manifest)
    verify(root, destination)
    return digest(canonical(manifest))


def verify(root, bundle):
    manifest_path = bundle / "manifest.json"
    safe_file(manifest_path, bundle)
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get("schema") != 1 or manifest.get("product") != "Browser Bolt"
            or manifest.get("upstream") != UPSTREAM or manifest.get("upstream_commit") != UPSTREAM_COMMIT):
        raise ReleaseError("Invalid release manifest")
    config_values(manifest["config"])
    actual = inventory(bundle)
    del actual["manifest.json"]
    if actual != manifest["files"]:
        raise ReleaseError("Artifact drift or unexpected files; prepare and review a new candidate")
    expected_inputs = {p: digest(safe_file(root / p, root)) for p in public_files(root)}
    if expected_inputs != manifest["inputs"]:
        raise ReleaseError("Working source drift; prepare and review a new candidate")
    if inventory(bundle / "source") != expected_inputs:
        raise ReleaseError("Staged public source mismatch")
    for relative in expected_inputs:
        check_public(relative, safe_file(bundle / "source" / relative, bundle / "source"))
    for relative in inventory(bundle / "site"):
        check_public(relative, safe_file(bundle / "site" / relative, bundle / "site"))
    package_names = set(p.name for p in (bundle / "packages").iterdir())
    version = manifest["config"]["tag"][1:]
    expected_packages = {f"jev_qwerebras_ultrafast-{version}-py3-none-any.whl",
                         f"jev_qwerebras_ultrafast-{version}.tar.gz", "requirements-mcp.txt",
                         "sbom-mcp.cdx.json", "SHA256SUMS"}
    if package_names != expected_packages:
        raise ReleaseError("Unexpected or missing package artifacts / incompatible package version")
    check_archives(bundle)
    return manifest, digest(canonical(manifest))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def wrangler_environment(config, home, *, authenticated=True):
    # An allowlist blocks CLI destination overrides, proxy injection, Node options,
    # unrelated credentials, and auth/config discovery in the user's real home.
    env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(home),
           "CLOUDFLARE_ACCOUNT_ID": config["cloudflare_account_id"], "CI": "true", "WRANGLER_SEND_METRICS": "false"}
    if authenticated:
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not token:
            raise ReleaseError("CLOUDFLARE_API_TOKEN is missing from the process environment")
        env["CLOUDFLARE_API_TOKEN"] = token
    return env


def canary_evidence(output, exit_code):
    """Persist only the probe's finite public status vocabulary, never arbitrary output."""
    fallback = {"ok": False, "code": "invalid_canary_output", "checked_at": time.time()}
    try:
        value = json.loads(output)
        names = {"home", "setup", "brand", "methods", "version", "missing", "private"}
        codes = {"ok", "body_limit", "status_mismatch", "content_mismatch", "release_mismatch",
                 "invalid_version", "security_headers", "transport_failure"}
        checks = value["checks"]
        if (type(value["ok"]) is not bool or not isinstance(checks, list) or len(checks) != len(names)
                or {item["check"] for item in checks} != names or exit_code not in (0, 1)):
            return fallback
        clean = []
        for item in checks:
            if (type(item["ok"]) is not bool or item["code"] not in codes
                    or (item["status"] is not None and (type(item["status"]) is not int
                                                       or not 100 <= item["status"] <= 599))
                    or type(item["duration_ms"]) not in (int, float) or not 0 <= item["duration_ms"] <= 60000):
                return fallback
            clean.append({key: item[key] for key in ("check", "ok", "code", "status", "duration_ms")})
        return {"ok": exit_code == 0 and value["ok"] and all(item["ok"] for item in clean),
                "checks": clean, "checked_at": time.time(), "exit_code": exit_code}
    except (KeyError, TypeError, ValueError):
        return fallback


class Remote:
    def __init__(self, config):
        self.config = config
        self.repo = config["github_repo"]
        self.cf_base = f"https://api.cloudflare.com/client/v4/accounts/{config['cloudflare_account_id']}/workers"

    def gh(self, endpoint, method="GET", body=None, missing=False):
        # --silent is not used: structured response is inspected but never logged.
        args = ["gh", "api", "--hostname", "github.com", endpoint, "--method", method]
        if body is not None:
            args += ["--input", "-"]
        try:
            return json.loads(run(args, data=canonical(body) if body is not None else None))
        except ReleaseError:
            if missing:
                # Distinguish 404 from transport/auth errors without printing response bodies.
                result = subprocess.run(["gh", "api", "--hostname", "github.com", endpoint, "--include"],
                                        capture_output=True, check=False)
                if result.stdout.startswith(b"HTTP/2.0 404") or result.stdout.startswith(b"HTTP/1.1 404"):
                    return None
            raise

    def cf(self, suffix="", method="GET", body=None, missing=False):
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not token:
            raise ReleaseError("CLOUDFLARE_API_TOKEN is missing from the process environment")
        request = Request(self.cf_base + suffix, data=canonical(body) if body is not None else None,
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                          method=method)
        try:
            with build_opener(NoRedirect).open(request, timeout=30) as response:
                value = json.load(response)
        except HTTPError as error:
            if missing and error.code == 404:
                return None
            raise ReleaseError(f"Cloudflare request failed (HTTP {error.code}); details suppressed") from None
        except (URLError, TimeoutError):
            raise ReleaseError("Cloudflare transport failed; details suppressed") from None
        if not value.get("success"):
            raise ReleaseError("Cloudflare rejected the request; details suppressed")
        return value["result"]

    def preflight(self, state):
        if not self.config["cloudflare_account_id"]:
            raise ReleaseError("cloudflare_account_id must be configured before approval")
        with tempfile.TemporaryDirectory(prefix="browser-bolt-preflight-") as temporary:
            run([self.config["wrangler"], "--version"], cwd=temporary,
                env=wrangler_environment(self.config, temporary, authenticated=False))
        self.gh("user")
        repo = self.gh(f"repos/{self.repo}", missing=True)
        if repo and (not state.get("repo_id") or repo["id"] != state["repo_id"]):
            raise ReleaseError("GitHub repository already exists and is not owned by this publication journal")
        if repo and repo.get("parent") and repo["parent"].get("full_name") != UPSTREAM:
            raise ReleaseError("GitHub repository has unexpected fork provenance")
        subdomain = self.cf("/subdomain")
        if not self.config["workers_subdomain"] or subdomain.get("subdomain") != self.config["workers_subdomain"]:
            raise ReleaseError("Configured workers_subdomain does not match the Cloudflare account")
        scripts = self.cf("/scripts")
        existing = next((item for item in scripts if item["id"] == self.config["cloudflare_script"]), None)
        if existing:
            settings = self.cf(f"/scripts/{self.config['cloudflare_script']}/settings")
            if settings.get("annotations", {}).get("workers/tag") != state.get("digest"):
                raise ReleaseError("Refusing to replace an unknown existing Workers script")
        return repo, existing

    def ensure_repo(self, state, persist):
        if not state.get("repo_id"):
            if state.get("repo_create_started"):
                raise ReleaseError("Fork creation outcome is uncertain; inspect it before reconciling the journal")
            state["repo_create_started"] = True
            persist()
            owner, name = self.repo.split("/")
            repo = self.gh(f"repos/{UPSTREAM}/forks", "POST", {"organization": owner, "name": name,
                                                              "default_branch_only": True})
            state["repo_id"] = repo["id"]
            persist()
        # GitHub fork creation is asynchronous. Only read while waiting for this
        # recorded repository and its pinned upstream commit to become available.
        for attempt in range(6):
            repo = self.gh(f"repos/{self.repo}", missing=True)
            if repo:
                if repo.get("id") != state["repo_id"] or repo.get("private"):
                    raise ReleaseError("Fork readiness found an unexpected repository identity or visibility")
                parent = repo.get("parent", {}).get("full_name")
                if parent and parent != UPSTREAM:
                    raise ReleaseError("Fork readiness found unexpected upstream provenance")
                if repo.get("fork") and parent == UPSTREAM:
                    commit = self.gh(f"repos/{self.repo}/git/commits/{UPSTREAM_COMMIT}", missing=True)
                    if commit and commit.get("sha") == UPSTREAM_COMMIT:
                        return
            if attempt < 5:
                time.sleep(2)
        raise ReleaseError("Fork readiness timed out; the recorded repository is retained for a later retry")

    def ensure_commit(self, bundle, identity, state, persist):
        branch = "browser-bolt"
        endpoint = f"repos/{self.repo}/git/ref/heads/{branch}"
        existing = self.gh(endpoint, missing=True)
        if existing:
            if not state.get("commit") or existing["object"]["sha"] != state["commit"]:
                raise ReleaseError("Refusing to replace an unknown or changed GitHub branch")
        else:
            if state.get("branch_create_started"):
                raise ReleaseError("Branch creation outcome is uncertain; inspect before reconciling the journal")
            if not state.get("commit"):
                if state.get("commit_create_started"):
                    raise ReleaseError("Commit creation outcome is uncertain; inspect before reconciling the journal")
                entries = []
                for relative in inventory(bundle / "source"):
                    blob = self.gh(f"repos/{self.repo}/git/blobs", "POST", {
                        "content": base64.b64encode((bundle / "source" / relative).read_bytes()).decode(),
                        "encoding": "base64"})
                    entries.append({"path": relative, "mode": "100644", "type": "blob", "sha": blob["sha"]})
                tree = self.gh(f"repos/{self.repo}/git/trees", "POST", {"tree": entries})
                state["commit_create_started"] = True
                persist()
                commit = self.gh(f"repos/{self.repo}/git/commits", "POST", {
                    "message": f"Browser Bolt reviewed candidate {identity}", "tree": tree["sha"],
                    "parents": [UPSTREAM_COMMIT]})
                state["commit"] = commit["sha"]
                persist()
            state["branch_create_started"] = True
            persist()
            self.gh(f"repos/{self.repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": state["commit"]})
        self.gh(f"repos/{self.repo}", "PATCH", {"default_branch": branch})

    def check_release_tag(self, state, *, required=False):
        reference = self.gh(f"repos/{self.repo}/git/ref/tags/{self.config['tag']}", missing=True)
        if reference is None:
            if required:
                raise ReleaseError("Published release tag is not yet readable; inspect and resume")
            return  # A draft can defer creation of its tag until publication.
        target = reference.get("object", {})
        for _ in range(8):
            if target.get("type") != "tag":
                break
            annotated = self.gh(f"repos/{self.repo}/git/tags/{target['sha']}")
            target = annotated.get("object", {})
        if target.get("type") != "commit" or target.get("sha") != state["commit"]:
            raise ReleaseError("Release tag does not resolve to the approved commit")

    def ensure_release(self, bundle, identity, state, persist):
        tag = self.config["tag"]
        releases = self.gh(f"repos/{self.repo}/releases?per_page=100")
        if len(releases) >= 100:
            raise ReleaseError("Unexpected release count; inspect repository before continuing")
        release = next((item for item in releases if item.get("tag_name") == tag), None)
        marker = f"Approved artifact manifest SHA-256: {identity}"
        if release and (not state.get("release_create_started") or release.get("body") != marker
                        or release.get("target_commitish") != state["commit"]
                        or state.get("release_id", release["id"]) != release["id"]):
            raise ReleaseError("Refusing to replace an unknown existing GitHub release")
        if not release:
            if state.get("release_create_started"):
                raise ReleaseError("Release creation outcome is uncertain; inspect before reconciling the journal")
            tag_ref = self.gh(f"repos/{self.repo}/git/ref/tags/{tag}", missing=True)
            if tag_ref:
                raise ReleaseError("Refusing to reuse an existing tag")
            state["release_create_started"] = True
            persist()
            release = self.gh(f"repos/{self.repo}/releases", "POST", {
                "tag_name": tag, "target_commitish": state["commit"], "name": f"Browser Bolt {tag}",
                "body": marker, "draft": True, "prerelease": True})
        state["release_id"] = release["id"]
        persist()
        self.check_release_tag(state, required=not release["draft"])
        assets = {asset["name"]: asset for asset in release.get("assets", [])}
        uploads = [bundle / "public-manifest.json", *sorted((bundle / "packages").iterdir())]
        if set(assets) - {path.name for path in uploads}:
            raise ReleaseError("Release contains unexpected assets")
        for path in uploads:
            if path.name in assets:
                expected = "sha256:" + digest(path.read_bytes())
                if assets[path.name].get("digest") != expected:
                    raise ReleaseError("Existing release asset differs or lacks a verifiable SHA-256 digest")
            else:
                if not release["draft"]:
                    raise ReleaseError("Published release is missing assets; refusing incremental repair")
                attempted = state.setdefault("asset_upload_started", [])
                if path.name in attempted:
                    raise ReleaseError("Asset upload outcome is uncertain; inspect before reconciling the journal")
                attempted.append(path.name)
                persist()
                run(["gh", "release", "upload", tag, str(path), "--repo", "github.com/" + self.repo])
        confirmed = self.gh(f"repos/{self.repo}/releases/{state['release_id']}")
        uploaded = {asset["name"]: asset.get("digest") for asset in confirmed.get("assets", [])}
        expected = {path.name: "sha256:" + digest(path.read_bytes()) for path in uploads}
        if uploaded != expected:
            raise ReleaseError("Release upload verification failed; exact asset hashes are required")

    def deployment_matches(self, state):
        name = self.config["cloudflare_script"]
        result = self.cf(f"/scripts/{name}/deployments", missing=True)
        if result is None:
            return None
        deployments = result.get("deployments", [])
        if not deployments:
            return None
        latest = deployments[0]
        versions = latest.get("versions", [])
        if len(versions) != 1 or versions[0].get("percentage") != 100:
            raise ReleaseError("Existing Workers deployment has unexpected traffic allocation")
        version = self.cf(f"/scripts/{name}/versions/{versions[0]['version_id']}")
        if version.get("annotations", {}).get("workers/tag") != state["digest"]:
            raise ReleaseError("Existing Workers deployment is not this approved candidate")
        return latest["id"]

    def deploy(self, bundle, state, persist):
        deployment = self.deployment_matches(state)
        if deployment is None:
            if state.get("deploy_started"):
                raise ReleaseError("Prior deployment outcome is uncertain; inspect Cloudflare before retrying")
            # Work outside the immutable bundle: Wrangler may generate local state.
            with tempfile.TemporaryDirectory(prefix="browser-bolt-deploy-") as temporary:
                env = wrangler_environment(self.config, temporary)
                state["deploy_started"] = True
                persist()
                run([self.config["wrangler"], "deploy", "--config", str(bundle / "wrangler.json"),
                     "--tag", state["digest"], "--message", "Approved Browser Bolt preview"],
                    cwd=temporary, env=env)
            deployment = self.deployment_matches(state)
            if deployment is None:
                raise ReleaseError("Deployment not yet confirmed; retry to reconcile without replacing release")
        state["deployment_id"] = deployment
        persist()
        return self.verify_site(bundle, state, persist)

    def verify_site(self, bundle, state, persist):
        state["site_verified"] = False
        state["canary"] = {"ok": False, "code": "verification_pending", "checked_at": time.time()}
        persist()  # A failed recheck must never leave an earlier success as current evidence.
        expected = json.loads((bundle / "site/version.json").read_text())
        url = public_url(self.config)
        # Check deployed public bytes independently, including the provider's HTML route mapping.
        for relative, expected_hash in inventory(bundle / "site").items():
            if relative == "_headers":
                continue  # Provider configuration; effective headers are checked by the canary.
            route = "/" + relative
            if relative == "index.html":
                route = "/"
            elif relative.endswith("/index.html"):
                route = "/" + relative[:-len("index.html")]
            elif relative == "404.html":
                route = "/__browser_bolt_release_missing__"
            try:
                with build_opener(NoRedirect).open(url + route, timeout=30) as response:
                    content = response.read()
            except HTTPError as error:
                if relative != "404.html" or error.code != 404:
                    raise ReleaseError("Published static file could not be fetched; response suppressed") from None
                content = error.read()
            except (URLError, TimeoutError):
                raise ReleaseError("Published static file could not be fetched; response suppressed") from None
            if digest(content) != expected_hash:
                raise ReleaseError("Published static file hash differs from the approved artifact")
        try:
            exit_code, output = run(["python3", str(bundle / "source/local/static_canary.py"), "--base", url,
                                     "--release", expected["releaseId"], "--state",
                                     str(bundle.with_name(bundle.name + ".canary.json"))],
                                    cwd=bundle.parent, allow_failure=True)
            state["canary"] = canary_evidence(output, exit_code)
        except ReleaseError:
            state["canary"] = {"ok": False, "code": "canary_execution_failed", "checked_at": time.time()}
        state["canary"]["release_id"] = expected["releaseId"]
        state["site_verified"] = state["canary"]["ok"]
        persist()
        if not state["site_verified"]:
            raise ReleaseError("Published static canary failed; sanitized evidence is saved in the publication journal")
        return url


def publish(root, bundle, approval=None, remote=None):
    manifest, identity = verify(root, bundle)
    plan = {"digest": identity, "repository": manifest["config"]["github_repo"],
            "site": public_url(manifest["config"]), "tag": manifest["config"]["tag"]}
    if approval is None:
        return plan | {"mode": "preview", "published": False}
    if approval != identity:
        raise ReleaseError("Approval digest does not match this exact candidate")
    # Journal is adjacent, never inside the immutable artifact tree or public source.
    state_path = bundle.with_name(bundle.name + ".publication.json")
    lock = bundle.with_name(bundle.name + ".publication.lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ReleaseError("Publication lock exists; inspect running process before clearing a stale lock") from None
    os.close(descriptor)
    try:
        if state_path.is_symlink():
            raise ReleaseError("Publication journal symlink refused")
        state = json.loads(state_path.read_text()) if state_path.exists() else {"digest": identity}
        if state.get("digest") != identity:
            raise ReleaseError("Publication journal belongs to another candidate")
        recovery = state.get("recovery", {})
        if not isinstance(recovery, dict) or ("exposure" in recovery and recovery["exposure"] != "restored"):
            raise ReleaseError("Recovery exposure is not restored; use explicit recovery before publishing")
        def persist():
            save(state_path, state)

        service = remote or Remote(manifest["config"])
        service.preflight(state)
        with tempfile.TemporaryDirectory(prefix="browser-bolt-publish-") as temporary:
            frozen = Path(temporary) / "candidate"
            shutil.copytree(bundle, frozen, symlinks=True)
            _, frozen_identity = verify(root, frozen)
            if frozen_identity != identity:
                raise ReleaseError("Copied candidate no longer matches the approved digest")
            service.ensure_repo(state, persist)
            service.ensure_commit(frozen, identity, state, persist)
            service.ensure_release(frozen, identity, state, persist)
            site = service.deploy(frozen, state, persist)
            service.check_release_tag(state)
            confirmed = service.gh(f"repos/{manifest['config']['github_repo']}/releases/{state['release_id']}",
                                   "PATCH", {"draft": False})
            if confirmed.get("draft") is not False or confirmed.get("id") != state["release_id"]:
                raise ReleaseError("GitHub publication response was not confirmed; inspect and resume")
            service.check_release_tag(state, required=True)
        state["complete"] = True
        persist()
        return plan | {"mode": "published", "published": True, "site": site}
    finally:
        lock.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "prepare", "verify", "preflight", "publish"), nargs="?",
                        default="status")
    parser.add_argument("--bundle", type=Path, default=ROOT / "dist/release-candidate")
    parser.add_argument("--config", type=Path, help="Local JSON destination settings; never credentials")
    parser.add_argument("--approve", help="Exact manifest SHA-256, supplied only after Rich reviews it")
    args = parser.parse_args(argv)
    bundle = args.bundle.absolute()
    try:
        if args.command == "prepare":
            config = json.loads(args.config.read_text()) if args.config else {}
            identity = prepare(ROOT, bundle, config)
            result = {"mode": "prepared", "digest": identity, "published": False}
        elif args.command == "status" and not bundle.exists():
            result = {"mode": "not_prepared", "published": False,
                      "next": "prepare --config <local-config.json> --bundle <new-directory>"}
        elif args.command == "publish":
            result = publish(ROOT, bundle, args.approve)
        else:
            manifest, identity = verify(ROOT, bundle)
            if args.command == "preflight":
                state_path = bundle.with_name(bundle.name + ".publication.json")
                if state_path.is_symlink():
                    raise ReleaseError("Publication journal symlink refused")
                state = json.loads(state_path.read_text()) if state_path.exists() else {}
                Remote(manifest["config"]).preflight(state)
            state_path = bundle.with_name(bundle.name + ".publication.json")
            if state_path.is_symlink():
                raise ReleaseError("Publication journal symlink refused")
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
            if state and state.get("digest") != identity:
                raise ReleaseError("Publication journal belongs to another candidate")
            result = {"mode": args.command, "digest": identity, "valid": True,
                      "published": bool(state.get("complete")), "approval_required": not state.get("complete", False),
                      "site_verified": bool(state.get("site_verified")),
                      "site_exposure": state.get("recovery", {}).get("exposure", "not_recovery_checked"),
                      "access_checked": args.command == "preflight", "site": public_url(manifest["config"]),
                      "destination_configured": bool(manifest["config"]["cloudflare_account_id"]
                                                     and manifest["config"]["workers_subdomain"])}
        print(json.dumps(result, indent=2))
        return 0
    except (ReleaseError, ValueError, OSError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
        # Only our deliberately sanitized errors can be shown to the user.
        detail = str(error) if isinstance(error, ReleaseError) else "Invalid or unreadable candidate/configuration"
        print(json.dumps({"ok": False, "error": detail}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
