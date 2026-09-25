"""No public endpoints: real local HTTP/browser drills and isolated command contracts."""

import hashlib
import json
import shutil
import sys
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from local import public_journey as journey
from local.monitor import MonitorError

ROOT = Path(__file__).parents[1]
REPO = "example/browser-bolt"
BASE = "https://bolt.example.com"
SHA = "a" * 64


def plan():
    return journey.release_plan(REPO, BASE, SHA, "v0.1.0", SHA)


def manifest():
    return {"schema": 1, "product": "Browser Bolt", "github_repo": REPO, "site": BASE, "tag": "v0.1.0",
            "upstream": "browser-use/jev-ultrafast", "upstream_commit": "452c1ad2dd628008f1d5608f28158d76e49e6cc0",
            "files": {"packages/" + plan()["wheel"]: SHA, "packages/requirements-mcp.txt": SHA}}


@pytest.fixture
def endpoint():
    state = {"body": b"sentinel-secret", "status": 200, "redirect": False}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302 if state["redirect"] else state["status"])
            if state["redirect"]:
                self.send_header("Location", "https://secret.example/path?sentinel-secret")
            self.end_headers()
            self.wfile.write(state["body"])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/asset", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_http_hash_limit_redirect_and_status_drills(endpoint):
    url, state = endpoint
    sha = hashlib.sha256(state["body"]).hexdigest()
    assert journey.download(url, sha, allow_loopback=True) == state["body"]
    with pytest.raises(MonitorError, match="download_hash_mismatch"):
        journey.download(url, SHA, allow_loopback=True)
    with pytest.raises(MonitorError, match="download_body_limit"):
        journey.download(url, sha, allow_loopback=True, limit=2)
    state["redirect"] = True
    with pytest.raises(MonitorError, match="download_redirect") as error:
        journey.download(url, sha, allow_loopback=True)
    assert "sentinel" not in str(error.value)
    state.update(redirect=False, status=503)
    with pytest.raises(MonitorError, match="download_transport"):
        journey.download(url, sha, allow_loopback=True)


@pytest.mark.parametrize("repository,tag", [("../private", "v0.1.0"), (REPO, "latest"),
                                           (REPO, "v0.1.0?token=secret"), (REPO, "../v0.1.0")])
def test_invalid_release_identity(repository, tag):
    with pytest.raises(MonitorError):
        journey.release_plan(repository, BASE, SHA, tag, SHA)


def test_manifest_identity_and_package_hashes_are_required():
    assert journey.manifest_files(json.dumps(manifest()), plan())
    for field, wrong in [("github_repo", "other/fork"), ("tag", "v0.2.0"), ("site", "https://other.example"),
                         ("files", {}), ("files", None)]:
        with pytest.raises(MonitorError):
            journey.manifest_files(json.dumps({**manifest(), field: wrong}), plan())


def install_line(wheel_url):
    return "uv tool install --python 3.12 'jev-qwerebras-ultrafast[mcp] @ " + wheel_url + "#sha256=" + SHA + "'"


def install_command(wheel_url):
    return '<pre id="install-command"><code>' + install_line(wheel_url) + "</code></pre>"


def live_start_page(wheel_url):
    # Live /start/ shape: the install command element, plus the embedded AGENT.md prompt repeating the same URL.
    return ('<html><head><title>Get Browser Bolt | Browser Bolt</title></head><body>'
            '<h1>Let your agent install Browser Bolt.</h1>'
            '<div>' + install_command(wheel_url) + '<button type="button" data-copy-target="install-command">'
            'Copy install command</button></div>'
            '<textarea id="setup-prompt" readonly># Install Browser Bolt for this project\n'
            + install_line(wheel_url) + "\n</textarea></body></html>")


def test_rendered_setup_page_requires_the_pinned_wheel_command_and_rejects_account_forms():
    wheel_url = plan()["wheel_url"]
    good = live_start_page(wheel_url)
    journey.check_page(good.encode(), "start", wheel_url)
    for extra in ["<form></form>", '<input type="password">', '<a href="/signup">Sign up</a>',
                  '<button data-billing="checkout">Pay</button>']:
        with pytest.raises(MonitorError, match="rendered_page_mismatch"):
            journey.check_page(good.replace("</body>", extra + "</body>").encode(), "start", wheel_url)
    for broken in [good.replace(wheel_url, wheel_url.replace("v0.1.0", "v0.0.9")),
                   good.replace("Let your agent install Browser Bolt.", "Welcome")]:
        with pytest.raises(MonitorError, match="rendered_page_mismatch"):
            journey.check_page(broken.encode(), "start", wheel_url)


def test_install_command_element_may_nest_same_named_tags():
    wheel_url = plan()["wheel_url"]
    nested = ('<div id="install-command"><div>Run this:</div><div><code>' + install_line(wheel_url)
              + "</code></div></div>")
    page = live_start_page(wheel_url).replace(install_command(wheel_url), nested)
    journey.check_page(page.encode(), "start", wheel_url)


@pytest.mark.parametrize("replacement", [
    pytest.param(lambda url: "", id="command-missing-prompt-still-has-url"),
    pytest.param(lambda url: install_command(url.replace("v0.1.0", "v0.0.9")), id="stale-command-current-prompt"),
    pytest.param(lambda url: install_command(url).replace(' id="install-command"', ""), id="command-lost-its-id"),
    pytest.param(lambda url: '<a href="' + url + '">Wheel</a>', id="bare-link-is-not-a-command"),
    pytest.param(lambda url: '<pre id="install-command"><code>uv tool install</code></pre><p>' + url + "</p>",
                 id="url-after-the-command-closes"),
])
def test_wheel_url_counts_only_inside_the_install_command(replacement):
    wheel_url = plan()["wheel_url"]
    good = live_start_page(wheel_url)
    broken = good.replace(install_command(wheel_url), replacement(wheel_url))
    assert broken != good and install_line(wheel_url) in broken  # the agent prompt still carries the pinned URL
    with pytest.raises(MonitorError, match="rendered_page_mismatch"):
        journey.check_page(broken.encode(), "start", wheel_url)


def test_rendered_legal_pages_need_their_live_headings():
    journey.check_page(b"<html><h1>Privacy</h1><p>Shelfwater LLC</p></html>", "privacy", plan()["wheel_url"])
    journey.check_page(b"<html><h1>BYOK preview terms</h1></html>", "terms", plan()["wheel_url"])
    with pytest.raises(MonitorError, match="rendered_page_mismatch"):
        journey.check_page(b"<html><h1>Bring your key. Pay your provider.</h1></html>", "terms", plan()["wheel_url"])
    with pytest.raises(KeyError):
        journey.check_page(b"<html><h1>Pricing</h1></html>", "pricing", plan()["wheel_url"])


def test_install_commands_pin_hashes_no_deps_and_remove_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sentinel-secret")
    monkeypatch.setenv("PYTHONPATH", "sentinel-secret")
    monkeypatch.setenv("UV_INDEX_URL", "https://sentinel-secret")
    wheel = tmp_path / plan()["wheel"]
    wheel.write_bytes(b"fixture")
    requirements = tmp_path / "requirements-mcp.txt"
    requirements.write_text("mcp==2.2.0 --hash=sha256:" + SHA)
    commands = []

    def runner(args, **kwargs):
        commands.append(args)
        assert kwargs["cwd"] == tmp_path
        assert "sentinel" not in json.dumps(kwargs["env"])
        if args[-1] == "--version":
            return b"uv 0.11.16"
        if args[-1] == "--preflight":
            return json.dumps({"ok": True, "mode": "preflight", "browser": "not contacted",
                               "provider_calls": 0, "transport": "official MCP SDK / stdio"}).encode()
        return b""

    assert journey.install_preflight(tmp_path, wheel, requirements, "uv", runner=runner)["provider_calls"] == 0
    assert "--require-hashes" in commands[2] and "--only-binary" in commands[2]
    assert "--no-deps" in commands[3] and "--no-index" in commands[3]
    assert commands[-1][1:] == ["-I", "-m", "jev_ultrafast.mcp_host", "--preflight"]


def test_default_cli_never_runs_network_and_wrong_fork_fails_before_execute(monkeypatch, capsys):
    for key, value in {"REPOSITORY": REPO, "URL": BASE, "RELEASE": SHA, "TAG": "v0.1.0", "MANIFEST_SHA": SHA}.items():
        monkeypatch.setenv("BOLT_CANARY_" + key, value)
    monkeypatch.setattr(journey, "execute", lambda _: pytest.fail("unexpected external request"))
    assert journey.main([]) == 0
    assert json.loads(capsys.readouterr().out)["code"] == "dry_run"
    monkeypatch.setenv("GITHUB_REPOSITORY", "other/fork")
    assert journey.main(["--run"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "repository_mismatch"
    monkeypatch.delenv("BOLT_CANARY_MANIFEST_SHA")
    assert journey.main([]) == 1


def test_real_child_timeout_and_output_are_redacted(tmp_path):
    with pytest.raises(MonitorError, match="journey_command_failed") as error:
        journey.command([sys.executable, "-c", "import time; print('sentinel-secret'); time.sleep(3)"],
                        cwd=tmp_path, env=journey.clean_environment(tmp_path), timeout=0.1)
    assert "sentinel" not in str(error.value)


def test_actual_chrome_renders_live_shaped_setup_privacy_terms(tmp_path):
    chrome = shutil.which("google-chrome")
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome and mac_chrome.is_file():
        chrome = str(mac_chrome)
    if not chrome:
        pytest.skip("Local browser unavailable; production command fails closed in this condition")
    out = tmp_path / "site"
    pages = {"start": live_start_page(plan()["wheel_url"]),
             "privacy": "<!doctype html><html><head><title>Privacy</title></head><body><h1>Privacy</h1></body></html>",
             "terms": ("<!doctype html><html><head><title>Terms</title></head>"
                       "<body><h1>BYOK preview terms</h1></body></html>")}
    for route, html in pages.items():
        (out / route).mkdir(parents=True)
        (out / route / "index.html").write_text(html)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(out)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = journey.browser_pages(f"http://127.0.0.1:{server.server_port}", plan()["wheel_url"], chrome, tmp_path)
        assert result == {"rendered_setup_privacy_terms": "passed"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_activation_plan_binds_journey_to_same_approved_tag_and_manifest():
    from local.monitor import activation_plan

    result = activation_plan(REPO, BASE, SHA, verified=True, tag="v0.1.0", manifest_sha=SHA)
    assert result["variables"]["BOLT_CANARY_TAG"] == "v0.1.0"
    assert result["variables"]["BOLT_CANARY_MANIFEST_SHA"] == SHA
    assert result["workflows"] == ["public-canary.yml", "public-journey.yml"]
    assert result["executable"] is False and "journey" not in result
    with pytest.raises(MonitorError):
        activation_plan(REPO, BASE, SHA, verified=True, tag="v0.1.0")


def test_missing_chrome_fails_before_download(monkeypatch):
    monkeypatch.setattr(journey.shutil, "which", lambda _: None)
    monkeypatch.setattr(journey, "download", lambda *a, **k: pytest.fail("unexpected download"))
    with pytest.raises(MonitorError, match="journey_tool_missing"):
        journey.execute(plan())


def test_public_journey_workflow_has_nightly_bounds_and_no_credentials():
    workflow = (ROOT / ".github/workflows/public-journey.yml").read_text()
    assert "17 10 * * *" in workflow and "workflow_dispatch:" in workflow
    assert "timeout-minutes: 12" in workflow and "600s" in workflow
    permissions = workflow.split("permissions:\n", 1)[1].split("\n\n", 1)[0]
    assert permissions.strip() == "contents: read" and "secrets." not in workflow
    assert "vars.BOLT_CANARY_ENABLED == 'true'" in workflow
    assert "setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e" in workflow
    assert "enable-cache: false" in workflow
