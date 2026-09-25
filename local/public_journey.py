"""Opt-in public rendered-page and hash-locked installation journey. No provider calls."""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from local.monitor import MonitorError, configuration, deadline

LIMIT = 4 * 1024 * 1024
UV_VERSION = "0.11.16"


class AssetRedirect(urllib.request.HTTPRedirectHandler):
    """Permit only GitHub's one-hop public release asset CDN, without auth."""

    def __init__(self):
        super().__init__()
        self.redirects = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if (self.redirects or urllib.parse.urlsplit(req.full_url).hostname != "github.com"
                or target.scheme != "https" or target.hostname != "release-assets.githubusercontent.com"
                or target.username is not None or target.password is not None or target.port not in {None, 443}):
            raise MonitorError("download_redirect")
        self.redirects += 1
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, expected_sha, *, limit=LIMIT, allow_loopback=False):
    parsed = urllib.parse.urlsplit(url)
    local = allow_loopback and parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
    if (not local and (parsed.scheme != "https" or parsed.hostname != "github.com")
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
        raise MonitorError("invalid_download_url")
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha):
        raise MonitorError("invalid_download_digest")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), AssetRedirect())
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": "BrowserBolt-PublicJourney/1"}),
                         timeout=10) as response:
            if response.status != 200:
                raise MonitorError("download_http_status")
            content = response.read(limit + 1)
    except (OSError, ValueError, urllib.error.URLError):
        raise MonitorError("download_transport") from None
    if len(content) > limit:
        raise MonitorError("download_body_limit")
    if hashlib.sha256(content).hexdigest() != expected_sha:
        raise MonitorError("download_hash_mismatch")
    return content


def release_plan(repository, base, release, tag, manifest_sha):
    repository, base, release = configuration(repository, base, release)
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", tag):
        raise MonitorError("invalid_release_tag")
    if not isinstance(manifest_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", manifest_sha):
        raise MonitorError("invalid_manifest_digest")
    prefix = f"https://github.com/{repository}/releases/download/{tag}/"
    wheel = f"jev_qwerebras_ultrafast-{tag[1:]}-py3-none-any.whl"
    return {"repository": repository, "base": base, "release": release, "tag": tag,
            "manifest_sha": manifest_sha, "manifest_url": prefix + "public-manifest.json",
            "wheel": wheel, "wheel_url": prefix + wheel, "requirements_url": prefix + "requirements-mcp.txt"}


def manifest_files(raw, plan):
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise MonitorError("invalid_public_manifest") from None
    if not isinstance(data, dict) or any(data.get(key) != value for key, value in {
        "schema": 1, "product": "Browser Bolt", "github_repo": plan["repository"],
        "tag": plan["tag"], "site": plan["base"], "upstream": "browser-use/jev-ultrafast",
        "upstream_commit": "452c1ad2dd628008f1d5608f28158d76e49e6cc0",
    }.items()):
        raise MonitorError("manifest_identity_mismatch")
    files = data.get("files")
    required = ["packages/" + plan["wheel"], "packages/requirements-mcp.txt"]
    if not isinstance(files, dict) or any(
        not isinstance(files.get(name), str) or not re.fullmatch(r"[a-f0-9]{64}", files[name]) for name in required
    ):
        raise MonitorError("manifest_package_missing")
    return files


def clean_environment(home):
    return {"PATH": os.defpath + ":/usr/local/bin:/opt/homebrew/bin", "HOME": str(home), "TMPDIR": str(home),
            "LANG": "en_US.UTF-8", "CI": "true", "UV_NO_CONFIG": "1", "UV_NO_CACHE": "1",
            "UV_PYTHON_DOWNLOADS": "never", "PYTHONNOUSERSITE": "1"}


def command(args, *, cwd, env, timeout=45, limit=LIMIT, dom=False):
    """Suppress all raw child logs and kill the isolated process group on timeout."""
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(args, cwd=cwd, env=env, stdout=output, stderr=errors, start_new_session=True)
            rendered = False
            try:
                if dom:
                    end = time.monotonic() + timeout
                    while process.poll() is None:
                        captured = os.pread(output.fileno(), limit + 1, 0)
                        if len(captured) > limit:
                            raise MonitorError("journey_output_limit")
                        if captured.rstrip().endswith(b"</html>"):
                            rendered = True
                            os.killpg(process.pid, signal.SIGTERM)
                            try:
                                process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                os.killpg(process.pid, signal.SIGKILL)
                                process.wait()
                            break
                        if time.monotonic() >= end:
                            raise subprocess.TimeoutExpired(args[0], timeout)
                        time.sleep(0.05)
                else:
                    process.wait(timeout=timeout)
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
            if process.returncode and not rendered:
                raise MonitorError("journey_command_failed")
            output.seek(0)
            result = output.read(limit + 1)
            if len(result) > limit:
                raise MonitorError("journey_output_limit")
            return result
    except (OSError, subprocess.TimeoutExpired):
        raise MonitorError("journey_command_failed") from None


class RenderedPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forbidden = False
        self.headings = []
        self.install_command = []
        self.in_heading = False
        self.command_tag = None
        self.command_depth = 0

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        self.forbidden |= tag in {"form", "input"} or "data-billing" in attrs
        if tag == "a":
            self.forbidden |= urllib.parse.urlsplit(attrs.get("href", "")).path.rstrip("/") in {
                "/signup", "/login", "/dashboard", "/launch", "/api/config"
            }
        if tag == "h1":
            self.in_heading = True
        if self.command_depth:
            self.command_depth += tag == self.command_tag
        elif attrs.get("id") == "install-command":
            self.command_tag, self.command_depth = tag, 1

    def handle_endtag(self, tag):
        if tag == "h1":
            self.in_heading = False
        if self.command_depth and tag == self.command_tag:
            self.command_depth -= 1

    def handle_data(self, data):
        if self.command_depth:
            self.install_command.append(data)
        if self.in_heading:
            self.headings.append(data)


def check_page(raw, route, wheel_url):
    parser = RenderedPage()
    try:
        parser.feed(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise MonitorError("invalid_rendered_page") from None
    expected = {"start": "Let your agent install Browser Bolt.", "privacy": "Privacy",
                "terms": "BYOK preview terms"}
    heading = expected[route]
    # Only the install command counts: the embedded agent prompt repeats the URL and must not mask a lost command.
    shows_wheel = wheel_url in "".join(parser.install_command)
    if (parser.forbidden or heading not in "".join(parser.headings)
            or route == "start" and not shows_wheel):
        raise MonitorError("rendered_page_mismatch")


def browser_pages(base, wheel_url, chrome, directory, *, runner=command):
    env = clean_environment(directory)
    for route in ("start", "privacy", "terms"):
        result = runner([chrome, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                         "--disable-background-networking", "--disable-sync", "--disable-extensions",
                         "--disable-component-update", "--disable-domain-reliability", "--no-proxy-server",
                         "--password-store=basic", "--use-mock-keychain",
                         "--dump-dom", "--timeout=15000", "--virtual-time-budget=3000",
                         f"--user-data-dir={directory / ('chrome-' + route)}", f"{base}/{route}/"],
                        cwd=directory, env=env, timeout=30, dom=True)
        check_page(result, route, wheel_url)
    return {"rendered_setup_privacy_terms": "passed"}


def install_preflight(directory, wheel, requirements, uv, *, runner=command):
    env = clean_environment(directory)
    if runner([uv, "--version"], cwd=directory, env=env).decode().split()[:2] != ["uv", UV_VERSION]:
        raise MonitorError("unexpected_uv_version")
    lock = requirements.read_text()
    if any(value in lock for value in ("://", " @ ", "--index", "--extra", "--trusted", "--find", "-r ", "-e ")):
        raise MonitorError("unsafe_requirements")
    python = directory / "venv/bin/python"
    runner([uv, "venv", "--python", sys.executable, str(directory / "venv")], cwd=directory, env=env)
    runner([uv, "pip", "install", "--python", str(python), "--require-hashes", "--only-binary", ":all:",
            "--index-url", "https://pypi.org/simple", "-r", str(requirements)],
           cwd=directory, env=env, timeout=240)
    runner([uv, "pip", "install", "--python", str(python), "--no-deps", "--no-index", str(wheel)],
           cwd=directory, env=env, timeout=30)
    runner([uv, "pip", "check", "--python", str(python)], cwd=directory, env=env, timeout=30)
    try:
        result = json.loads(runner([str(python), "-I", "-m", "jev_ultrafast.mcp_host", "--preflight"],
                                  cwd=directory, env=env, timeout=60))
    except (ValueError, UnicodeError):
        raise MonitorError("invalid_preflight_output") from None
    if not isinstance(result, dict) or any(result.get(key) != value for key, value in {
        "mode": "preflight", "ok": True, "browser": "not contacted", "provider_calls": 0,
        "transport": "official MCP SDK / stdio",
    }.items()):
        raise MonitorError("installed_preflight_failed")
    return {"hash_locked_install": "passed", "installed_mcp_preflight": "passed", "provider_calls": 0}


def execute(plan):
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    uv = shutil.which("uv")
    if not chrome or not uv:
        raise MonitorError("journey_tool_missing")
    files = manifest_files(download(plan["manifest_url"], plan["manifest_sha"]), plan)
    with tempfile.TemporaryDirectory(prefix="bolt-public-journey-") as temporary:
        directory = Path(temporary)
        wheel = directory / plan["wheel"]
        requirements = directory / "requirements-mcp.txt"
        wheel.write_bytes(download(plan["wheel_url"], files["packages/" + plan["wheel"]], limit=16 * LIMIT))
        requirements.write_bytes(download(plan["requirements_url"], files["packages/requirements-mcp.txt"]))
        from local.static_canary import probe
        if not probe(plan["base"], plan["release"], timeout=3)["ok"]:
            raise MonitorError("static_release_failed")
        pages = browser_pages(plan["base"], plan["wheel_url"], chrome, directory)
        installed = install_preflight(directory, wheel, requirements, uv)
    return {"ok": True, **pages, **installed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="opt in to public downloads, browser and package install")
    args = parser.parse_args(argv)
    previous = signal.signal(signal.SIGALRM, deadline)
    signal.alarm(600)
    try:
        plan = release_plan(*(os.getenv("BOLT_CANARY_" + key, "") for key in
                              ["REPOSITORY", "URL", "RELEASE", "TAG", "MANIFEST_SHA"]))
        if args.run:
            configuration(plan["repository"], plan["base"], plan["release"], os.getenv("GITHUB_REPOSITORY", ""))
            result = execute(plan)
        else:
            result = {"ok": True, "code": "dry_run",
                      "checks": ["rendered_pages", "pinned_download", "installed_preflight"]}
    except MonitorError as error:
        result = {"ok": False, "code": str(error)}
    except (OSError, UnicodeError, ValueError):
        result = {"ok": False, "code": "journey_failed"}
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    print(json.dumps(result, sort_keys=True))
    return int(not result["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
