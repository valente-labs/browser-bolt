"""Release probes must reject wrong content and stay quiet about response data."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("static_canary", Path(__file__).parents[1] / "local/static_canary.py")
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)
RELEASE = "a" * 64


LIVE_CSP = ("default-src 'none'; script-src 'self' https://static.cloudflareinsights.com; style-src 'self'; "
            "img-src 'self'; media-src 'self'; connect-src 'self'; font-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'; object-src 'none'")
BODIES = {"/": b"<title>Browser Bolt | Fast browser work through MCP</title>",
          "/start/": b"<code>uv tool install 'jev-qwerebras-ultrafast[mcp] @ https://github.com/o/r/releases/download/"
                     b"v0.1.0/jev_qwerebras_ultrafast-0.1.0-py3-none-any.whl'</code>",
          "/AGENT.md": b"# Set up Browser Bolt",
          "/docs/NATIVE_COMPARISON.md": b"Jev decisions"}


@pytest.fixture
def endpoint():
    config = {"version": RELEASE, "headers": True, "redirect": False, "csp": LIVE_CSP, "bodies": dict(BODIES),
              "paths": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            config["paths"].append(self.path)
            if config["redirect"]:
                self.send_response(302)
                self.send_header("Location", "https://example.invalid/private?sentinel-secret")
                self.end_headers()
                return
            body = config["bodies"].get(self.path)
            status = 200 if body is not None or self.path == "/version.json" else 404
            if self.path == "/version.json":
                body = json.dumps({"product": "Browser Bolt", "mode": "static-preview", "managedAvailable": False,
                                   "releaseId": config["version"]}).encode()
            self.send_response(status)
            if config["headers"]:
                self.send_header("Content-Security-Policy", config["csp"])
                self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body or b"not found")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", config
    server.shutdown()
    server.server_close()
    thread.join()


def test_actual_http_probe_checks_release_headers_and_unavailable_routes(endpoint):
    base, config = endpoint
    assert canary.probe(base, RELEASE, allow_loopback=True)["ok"]
    config["version"] = "b" * 64
    assert any(c["code"] == "release_mismatch" for c in canary.probe(base, RELEASE, allow_loopback=True)["checks"])
    config["version"] = RELEASE
    config["headers"] = False
    assert not canary.probe(base, RELEASE, allow_loopback=True)["ok"]


def test_probe_matches_the_live_site_shape_and_never_requests_retired_brand_script(endpoint):
    base, config = endpoint
    result = canary.probe(base, RELEASE, allow_loopback=True)
    assert result["ok"], result
    assert [c["check"] for c in result["checks"]] == ["home", "setup", "agent", "methods", "version",
                                                      "missing", "private"]
    assert "/assets/brand.js" not in config["paths"]


def test_csp_requires_locked_default_frames_and_self_or_none_connect(endpoint):
    base, config = endpoint
    config["csp"] = LIVE_CSP.replace("connect-src 'self'", "connect-src 'none'")
    assert canary.probe(base, RELEASE, allow_loopback=True)["ok"]
    for csp in (LIVE_CSP.replace("connect-src 'self'", "connect-src 'self' https://evil.example"),
                LIVE_CSP.replace("default-src 'none'", "default-src 'self'"),
                LIVE_CSP.replace("frame-ancestors 'none'", "frame-ancestors *"),
                LIVE_CSP.replace("connect-src 'self'; ", "")):
        config["csp"] = csp
        result = canary.probe(base, RELEASE, allow_loopback=True)
        assert not result["ok"]
        assert {c["code"] for c in result["checks"] if not c["ok"]} == {"security_headers"}


def test_missing_setup_wheel_marker_is_a_content_mismatch(endpoint):
    base, config = endpoint
    config["bodies"]["/start/"] = b"<h1>Setup</h1>"
    result = canary.probe(base, RELEASE, allow_loopback=True)
    assert [c["code"] for c in result["checks"] if not c["ok"]] == ["content_mismatch"]


def test_redirects_are_not_followed_or_logged(endpoint):
    base, config = endpoint
    config["redirect"] = True
    result = canary.probe(base, RELEASE, allow_loopback=True)
    assert not result["ok"]
    assert all(c["status"] == 302 for c in result["checks"])
    assert "sentinel" not in json.dumps(result)


@pytest.mark.parametrize("base", ["http://example.com", "https://key@example.com", "https://example.com/?key=x",
                                  "https://example.com/path", "https://example.com/#key", "file:///tmp/file"])
def test_reject_unsafe_configuration(base):
    with pytest.raises(ValueError):
        canary.validate_base(base, True)


def test_incident_dedup_recovery_new_target_and_stale_clock():
    state = {}
    events = []
    for now, healthy in enumerate([True, False, False, False, False, True, True], 1):
        state = canary.advance(state, healthy, now, "target")
        events.append(state["event"])
    assert events == [None, None, None, "incident", None, "recovery", None]
    assert not state["incident_open"]
    assert canary.heartbeat_stale(state, 908)
    assert canary.heartbeat_stale(state, 6)
    assert not canary.heartbeat_stale(state, 8)
    assert canary.advance(state, False, 9, "other")["consecutive_failures"] == 1
