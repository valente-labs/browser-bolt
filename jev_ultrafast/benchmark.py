"""Reproducible synthetic-fixture comparisons through the production Agent loop."""

import argparse
import hashlib
import json
import math
import os
import random
import re
import statistics
import threading
import time
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

import httpx

PROFILES = (
    "qwen",
    "astra",
    "opus",
    "jev_qwen",
    "jev_astra",
    "jev_opus",
    "jev",
    "qwen_openrouter",
    "jev_qwen_openrouter",
)
DEFAULT_PROFILES = PROFILES[:6]
GOALS = {
    "note": "Create and save a travel note titled Lisbon weekend. Write one short sentence suggesting "
    "a relaxed weekend with a tram ride and pastries. Stop when the note is saved.",
    "review": "Create and save a travel note titled Lisbon weekend. Write one short sentence suggesting "
    "a relaxed weekend with a tram ride and pastries. Complete the review and confirmation "
    "steps. Stop when the note is saved.",
    "choice": "Select Express delivery and save the preference. Stop when the saved preference is visible.",
}
GOALS.update(
    {
        "checkout": (
            "Review the demo backpack order for Taylor Example at 12 Demo Lane. "
            "Choose Express shipping and the demo card ending 4242. "
            "Stop at the completed order review. Do not make a purchase."
        ),
        "onboarding": (
            "Set up the synthetic profile with display name Casey Demo. "
            "Write one short sentence about enjoying design and travel. "
            "Choose exactly Design and Travel as interests, review, and finish setup. Stop when the profile is ready."
        ),
        "recovery": (
            "Replace the expired discount with DEMO10, apply it, and review the discounted total. "
            "Stop when the discount review shows 10% and $36.00."
        ),
    }
)
RATES = {"prompt_usd_per_million": 0.99, "completion_usd_per_million": 1.49}


def load_environment(source):
    """Read literal assignments, without shell expansion or overwriting inherited values."""
    if source is None:
        return
    for line in source.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if separator and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)


def schedule(profiles, tasks, runs, seed):
    """Rotate seeded profile order across task/run blocks, balancing early positions."""
    rng = random.Random(seed)
    order = list(profiles)
    rng.shuffle(order)
    rows = []
    block = 0
    for run in range(1, runs + 1):
        task_order = list(tasks)
        rng.shuffle(task_order)
        for task in task_order:
            shift = block % len(order)
            for profile in order[shift:] + order[:shift]:
                rows.append({"trial_id": f"{run}:{task}:{profile}", "run": run, "task": task, "profile": profile})
            block += 1
    return rows


def verify(task, page):
    """Verify final fixture fields separately; body words cannot satisfy the title."""
    result = page.get("fixture_result", {})
    heading, paragraphs = result.get("heading"), result.get("paragraphs", [])
    if task == "choice":
        return heading == "Preference saved" and paragraphs == ["Express"]
    if task == "checkout":
        return heading == "Order reviewed" and paragraphs == [
            "Taylor Example",
            "12 Demo Lane",
            "Express",
            "Demo card 4242",
        ]
    if task == "recovery":
        return heading == "Discount reviewed" and paragraphs == ["DEMO10", "10%", "$36.00"]
    if task == "onboarding":
        return bool(
            heading == "Profile ready"
            and len(paragraphs) == 3
            and paragraphs[0] == "Casey Demo"
            and "design" in paragraphs[1].lower()
            and "travel" in paragraphs[1].lower()
            and set(paragraphs[2].split(", ")) == {"Design", "Travel"}
        )
    return bool(
        heading == "Note saved"
        and len(paragraphs) == 2
        and paragraphs[0] == "Lisbon weekend"
        and "tram" in paragraphs[1].lower()
        and re.search(r"pastr(?:y|ies)", paragraphs[1], re.I)
    )


def observe_result(browser):
    page = browser.observe(screenshot=False)
    page["fixture_result"] = browser.evaluate(
        "({heading:document.querySelector('main h1')?.textContent,"
        "paragraphs:Array.from(document.querySelectorAll('main p')).map(e=>e.textContent)})"
    )
    return page


def fixture_bytes():
    root = files("jev_ultrafast").joinpath("fixtures")
    return {f"/{task}.html": root.joinpath(f"{task}.html").read_bytes() for task in GOALS}


def evidence_metadata(profiles, fixtures):
    """Fingerprint fixed code, fixtures and non-secret provider configuration."""
    root = files("jev_ultrafast")
    source_names = (
        "agent.py",
        "browser.py",
        "snapshot.js",
        "model.py",
        "policy.py",
        "questions.py",
        "comparators.py",
        "benchmark.py",
    )
    sources = {}
    for name in source_names:
        source = root.joinpath(name)
        sources[name] = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
    configurations = {}
    for name, profile in profiles.items():
        configurations[name] = {}
        for role in ("decision", "writer"):
            config = getattr(profile, role, None)
            configurations[name][role] = (
                {field: getattr(config, field) for field in ("provider", "endpoint", "model", "reasoning")}
                if config is not None
                else None
            )
    versions = {}
    for package in ("jev-qwerebras-ultrafast", "browser-harness", "httpx"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    metadata = {
        "transport": "production Agent / Browser Harness",
        "versions": versions,
        "source_sha256": sources,
        "profiles": configurations,
        "fixture_sha256": {name: hashlib.sha256(body).hexdigest() for name, body in fixtures.items()},
        "cerebras_rates": RATES,
    }
    metadata["fingerprint"] = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
    return metadata


@contextmanager
def fixture_server(fixtures):
    """Expose only the packaged documents, never a filesystem directory."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = fixtures.get(urlsplit(self.path).path)
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def clean_call(call):
    """Allowlist accounting fields; provider payloads, prompts and exception text stay out."""
    usage = call.get("usage") or {}
    clean = {key: call[key] for key in ("provider", "model", "success") if key in call}
    clean["latency_ms"] = number(call.get("latency_ms"))
    clean["usage"] = {
        key: number(usage[key])
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")
        if key in usage and number(usage[key]) is not None
    }
    cost = number(usage.get("cost"))
    if cost is not None:
        clean.update(cost_usd=cost, cost_basis="reported")
    elif call.get("provider") in {"cerebras", "qwen"} and call.get("model") == "qwen-3.8-27b":
        prompt, completion = number(usage.get("prompt_tokens")), number(usage.get("completion_tokens"))
        if prompt is not None and completion is not None:
            clean.update(cost_usd=(prompt * 0.99 + completion * 1.49) / 1_000_000, cost_basis="estimated_cerebras")
        else:
            clean.update(cost_usd=None, cost_basis="unknown")
    else:
        clean.update(cost_usd=None, cost_basis="unknown")
    return clean


def state_metrics(state):
    decisions, calls = [], []
    for decision in state.get("decisions", []):
        routed = decision.get("routing", {}).get("model_calls")
        decision_calls = routed if routed is not None else [decision]
        cleaned = [clean_call(call) for call in decision_calls if not call.get("included_in_decision")]
        calls.extend(cleaned)
        decisions.append(
            {
                **{
                    key: decision[key]
                    for key in ("choice", "operation", "target", "confidence", "elapsed_ms")
                    if key in decision
                },
                "model_calls": cleaned,
            }
        )
    text_calls = [clean_call(call) for call in state.get("text_calls", []) if not call.get("included_in_decision")]
    calls.extend(text_calls)
    unknown = sum(call["cost_usd"] is None for call in calls)
    return {
        "decisions": decisions,
        "text_calls": text_calls,
        "model_calls": calls,
        "model_call_count": len(calls),
        "action_count": len(state.get("history", [])),
        "unknown_cost_call_count": unknown,
        "reported_cost_usd": sum(call["cost_usd"] for call in calls if call["cost_basis"] == "reported"),
        "estimated_cerebras_cost_usd": sum(
            call["cost_usd"] for call in calls if call["cost_basis"] == "estimated_cerebras"
        ),
        "total_cost_usd": None if unknown else sum(call["cost_usd"] for call in calls),
    }


def run_trial(trial, base_url, profile, agent_factory):
    row = {**trial, "status": "setup_failed", "success": False, "verified": False}
    agent = None
    setup_started = time.perf_counter()
    loop_started = None
    try:
        agent = agent_factory(
            f"{base_url}/{trial['task']}.html",
            GOALS[trial["task"]],
            decision_fn=profile.choose,
            text_fn=profile.field_text,
        )
        row["setup_seconds"] = time.perf_counter() - setup_started
        loop_started = time.perf_counter()
        try:
            for _ in agent.run():
                pass
        except Exception as error:
            row["error_type"] = type(error).__name__
        # Even blocked/failed attempts get an independent read, without any mutation retry.
        try:
            row["verified"] = verify(trial["task"], observe_result(agent.browser))
        except Exception as error:
            row["verification_error_type"] = type(error).__name__
        row["status"] = agent.state.get("status", "unknown")
        row["success"] = row["status"] == "done" and row["verified"] and "error_type" not in row
    except Exception as error:
        row["error_type"] = type(error).__name__
    finally:
        stopped = time.perf_counter()
        row.setdefault("setup_seconds", stopped - setup_started)
        row["task_seconds"] = stopped - loop_started if loop_started is not None else None
        row.update(state_metrics(agent.state if agent is not None else {}))
        if agent is not None:
            try:
                agent.close()
            except Exception as error:
                row["cleanup_error_type"] = type(error).__name__
        row["wall_seconds"] = time.perf_counter() - setup_started
    return row


def summarize(rows):
    summaries = []
    groups = sorted({(row["profile"], row["task"]) for row in rows})
    for profile, task in groups:
        attempts = [row for row in rows if row["profile"] == profile and row["task"] == task]
        successes = [row for row in attempts if row.get("success")]
        costs = [row.get("total_cost_usd") for row in attempts]
        total = sum(costs) if all(cost is not None for cost in costs) else None
        summary = {
            "profile": profile,
            "task": task,
            "sample_count": len(attempts),
            "success_count": len(successes),
            "success_rate": len(successes) / len(attempts),
            "total_cost_usd": total,
            "known_cost_subtotal_usd": sum(
                (row.get("reported_cost_usd", 0) + row.get("estimated_cerebras_cost_usd", 0))
                if "reported_cost_usd" in row or "estimated_cerebras_cost_usd" in row
                else (row.get("total_cost_usd") or 0)
                for row in attempts
            ),
            "unknown_cost_attempt_count": sum(cost is None for cost in costs),
            "cost_per_attempt_usd": total / len(attempts) if total is not None else None,
            "cost_per_verified_success_usd": total / len(successes) if total is not None and successes else None,
        }
        for metric in ("setup_seconds", "task_seconds", "wall_seconds"):
            for label, group in (("all", attempts), ("successful", successes)):
                values = [row[metric] for row in group if number(row.get(metric)) is not None]
                summary[f"median_{metric}_{label}"] = statistics.median(values) if values else None
                summary[f"{metric}_{label}_sample_count"] = len(values)
        summaries.append(summary)
    return summaries


def save_results(path, result):
    result["summaries"] = summarize(result["rows"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def selected_list(value, choices):
    selected = value.split(",")
    if not selected or len(selected) != len(set(selected)) or any(item not in choices for item in selected):
        raise argparse.ArgumentTypeError("Choose unique comma-separated values from: " + ",".join(choices))
    return selected


def get_profile(name, **kwargs):
    # Import lazily: listing and dry runs need neither providers nor a browser connection.
    from .comparators import get_profile as factory

    return factory(name, **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=lambda value: selected_list(value, PROFILES), default=list(DEFAULT_PROFILES))
    parser.add_argument("--tasks", type=lambda value: selected_list(value, GOALS), default=list(GOALS))
    parser.add_argument("--runs", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--output", type=Path, default=None, help="JSON evidence file; reuse to resume unattempted trials"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the schedule without browser or provider access")
    parser.add_argument("--list-profiles", action="store_true")
    args = parser.parse_args(argv)
    if args.list_profiles:
        print("\n".join(PROFILES))
        return 0
    config = {"profiles": args.profiles, "tasks": args.tasks, "runs": args.runs, "seed": args.seed}
    plan = schedule(**config)
    if args.dry_run:
        print(json.dumps({"config": config, "schedule": plan, "trial_count": len(plan)}, indent=2))
        return 0
    path = args.output or Path("artifacts") / f"benchmark-{time.time_ns()}.json"
    result = {"schema_version": 1, "config": config, "schedule": plan, "rows": [], "cerebras_rates": RATES}
    with ExitStack() as stack:
        try:
            if path.exists():
                result = json.loads(path.read_text())
                if (
                    result.get("schema_version") != 1
                    or result.get("config") != config
                    or result.get("schedule") != plan
                ):
                    parser.error("Output file does not match this benchmark configuration.")
                ids = [row["trial_id"] for row in result["rows"]]
                if len(ids) != len(set(ids)) or any(trial_id not in {t["trial_id"] for t in plan} for trial_id in ids):
                    parser.error("Output file has invalid or duplicate trial records.")
            load_environment(args.env_file.expanduser() if args.env_file else None)
            client = stack.enter_context(
                httpx.Client(
                    http2=True,
                    timeout=httpx.Timeout(30, connect=5),
                    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4, keepalive_expiry=120),
                )
            )
            profiles = {name: get_profile(name, client=client) for name in args.profiles}
            for profile in profiles.values():
                profile.preflight()
            fixtures = fixture_bytes()
            metadata = evidence_metadata(profiles, fixtures)
            if path.exists() and result.get("metadata", {}).get("fingerprint") != metadata["fingerprint"]:
                parser.error("Output fingerprint differs from current sources, fixtures, models or dependencies.")
            result["metadata"] = metadata
        except Exception as error:
            parser.error(f"Benchmark preflight failed ({type(error).__name__}); no browser or model requests started.")
        for row in result["rows"]:
            if row.get("status") == "running":
                row.update(status="interrupted", success=False, verified=False, total_cost_usd=None)
        save_results(path, result)
        attempted = {row["trial_id"] for row in result["rows"]}
        pending = [trial for trial in plan if trial["trial_id"] not in attempted]
        if pending:
            from .agent import Agent

            with fixture_server(fixtures) as base_url:
                for trial in pending:
                    result["rows"].append({**trial, "status": "running", "success": False, "total_cost_usd": None})
                    save_results(path, result)
                    row = run_trial(trial, base_url, profiles[trial["profile"]], Agent)
                    result["rows"][-1] = row
                    save_results(path, result)
                    fields = ("trial_id", "success", "task_seconds", "total_cost_usd")
                    print(json.dumps({key: row[key] for key in fields}), flush=True)
        print(f"Results: {path.resolve()}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
