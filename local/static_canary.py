"""Bounded, read-only static release probe. No model calls or external notifications."""

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_base(base, allow_loopback=False):
    parsed = urllib.parse.urlsplit(base)
    local = allow_loopback and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        parsed.scheme != "https" and not (local and parsed.scheme == "http")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base must be an HTTPS origin without credentials, path, query or fragment")
    return base.rstrip("/")


def probe(base, expected_release, *, allow_loopback=False, timeout=5):
    base = validate_base(base, allow_loopback)
    if len(expected_release) != 64 or any(c not in "0123456789abcdef" for c in expected_release):
        raise ValueError("expected release must be a SHA-256 digest")
    checks = [
        ("home", "/", 200, b'data-distribution="static"'),
        ("setup", "/start/", 200, b'data-distribution="static"'),
        ("brand", "/assets/brand.js", 200, b"Browser Bolt"),
        ("methods", "/docs/NATIVE_COMPARISON.md", 200, b"Jev"),
        ("version", "/version.json", 200, None),
        ("missing", "/__browser_bolt_canary_missing__", 404, None),
        ("private", "/api/config", 404, None),
    ]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    results = []
    for name, path, expected_status, marker in checks:
        started = time.monotonic()
        status, reason = None, "ok"
        try:
            request = urllib.request.Request(base + path, headers={"User-Agent": "BrowserBolt-StaticCanary/1"})
            try:
                response = opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = response.code
                body = response.read(1_048_577)
                if len(body) > 1_048_576:
                    reason = "body_limit"
                elif status != expected_status:
                    reason = "status_mismatch"
                elif marker is not None and marker not in body:
                    reason = "content_mismatch"
                elif name == "version":
                    try:
                        version = json.loads(body)
                        if not isinstance(version, dict) or any(
                            version.get(key) != value
                            for key, value in {
                                "product": "Browser Bolt",
                                "mode": "static-preview",
                                "managedAvailable": False,
                                "releaseId": expected_release,
                            }.items()
                        ):
                            reason = "release_mismatch"
                    except (ValueError, UnicodeError):
                        reason = "invalid_version"
                if reason == "ok" and expected_status == 200:
                    headers = response.headers
                    csp = headers.get("Content-Security-Policy", "")
                    if (
                        "connect-src 'none'" not in csp
                        or "frame-ancestors 'none'" not in csp
                        or headers.get("X-Content-Type-Options", "").lower() != "nosniff"
                    ):
                        reason = "security_headers"
        except (OSError, urllib.error.URLError, ValueError):
            reason = "transport_failure"
        results.append({"check": name, "ok": reason == "ok", "code": reason, "status": status,
                        "duration_ms": round((time.monotonic() - started) * 1000)})
    return {"ok": all(check["ok"] for check in results), "checks": results}


def advance(previous, healthy, now, identity):
    """One incident after three failures; one recovery. State contains no response data."""
    if previous.get("identity") != identity:
        previous = {}
    failures = 0 if healthy else previous.get("consecutive_failures", 0) + 1
    was_open = previous.get("incident_open", False)
    incident_open = False if healthy else was_open or failures >= 3
    event = "recovery" if healthy and was_open else "incident" if incident_open and not was_open else None
    return {"identity": identity, "checked_at": now, "last_ok_at": now if healthy else previous.get("last_ok_at"),
            "consecutive_failures": failures, "incident_open": incident_open, "event": event}


def heartbeat_stale(state, now, max_age=900):
    checked = state.get("checked_at")
    return not isinstance(checked, (int, float)) or not 0 <= now - checked <= max_age


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--release", required=True, help="releaseId from the reviewed static version.json")
    parser.add_argument("--state", type=Path, required=True, help="dedicated local state JSON; contains no URLs")
    parser.add_argument("--allow-loopback", action="store_true")
    parser.add_argument("--check-heartbeat", action="store_true", help="read state only; no network requests")
    args = parser.parse_args()
    base = validate_base(args.base, args.allow_loopback)
    identity = hashlib.sha256((base + "|" + args.release).encode()).hexdigest()
    previous = json.loads(args.state.read_text()) if args.state.exists() else {}
    if args.check_heartbeat:
        stale = previous.get("identity") != identity or heartbeat_stale(previous, time.time())
        print(json.dumps({"ok": not stale, "code": "stale_heartbeat" if stale else "fresh_heartbeat"}))
        return int(stale)
    result = probe(base, args.release, allow_loopback=args.allow_loopback)
    state = advance(previous, result["ok"], time.time(), identity)
    args.state.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.state.with_suffix(".tmp")
    temporary.write_text(json.dumps(state) + "\n")
    temporary.replace(args.state)
    print(json.dumps({**result, "event": state["event"], "consecutive_failures": state["consecutive_failures"]}))
    return int(not result["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
