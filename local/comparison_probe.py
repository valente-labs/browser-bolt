"""Authenticated loopback adapter for testing packaged profiles in an external Chrome driver.

Explicitly running this server enables paid model calls on synthetic fixture observations.
The native packaged benchmark CLI remains the reproducible Browser Harness entry point.
"""

import argparse
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from jev_ultrafast.comparators import get_profile
from jev_ultrafast.model import field_context
from local.run_hybrid import launch_environment

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("qwen", "astra", "opus", "jev_qwen", "jev_astra", "jev_opus", "jev")


def decide(profile, state, goal, history):
    """Retain all paid call metadata even if the following field request fails."""
    result = {}
    try:
        decision = profile.choose(state, goal, history)
        result["decision"] = decision
        if decision["operation"] == "TYPE_TEXT":
            if decision.get("inline_text") is not None:
                result["text"] = decision["inline_text"]
            else:
                action = next(a for a in state["actions"] if a["id"] == decision["choice"])
                result["text"], result["text_meta"] = profile.field_text(field_context(goal, action, state, history))
    except Exception as error:
        result.update(
            error=type(error).__name__,
            routing=getattr(error, "routing", {}),
            model_call=getattr(error, "model_call", None),
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=os.environ.get("HYBRID_KEYS_FILE"))
    parser.add_argument("--port", type=int, default=18774)
    args = parser.parse_args()
    env = launch_environment(args.env_file, os.environ)
    token = secrets.token_urlsafe(32)
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    fd = os.open(artifacts / "probe-token", os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(token)
    with httpx.Client(http2=True, timeout=httpx.Timeout(30, connect=5)) as client:
        profiles = {name: get_profile(name, environ=env, client=client) for name in NAMES}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                task = parse_qs(urlparse(self.path).query).get("task", ["note"])[0]
                if task not in {"note", "review", "choice"}:
                    self.send_error(404)
                    return
                body = (ROOT / "local/fixtures" / (task + ".html")).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.headers.get("X-Demo-Token") != token or self.headers.get("Origin"):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 50000:
                        raise ValueError()
                    req = json.loads(self.rfile.read(length))
                    profile = profiles[req["arm"]]
                    result = decide(profile, req["state"], req["goal"], req["history"])
                except (ValueError, KeyError, TypeError):
                    self.send_error(400)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

        with HTTPServer(("127.0.0.1", args.port), Handler) as server:
            print("Synthetic comparison endpoint ready on loopback.", flush=True)
            server.serve_forever()


if __name__ == "__main__":
    main()
