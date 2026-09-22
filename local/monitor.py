"""Public static canary and independent, read-only GitHub schedule watcher."""

import argparse
import json
import os
import re
import signal
import subprocess
import time
from datetime import datetime
from urllib.parse import urlencode

from local import static_canary

WORKFLOW = "public-canary.yml"
WORKFLOW_PATH = ".github/workflows/" + WORKFLOW


class MonitorError(Exception):
    """Only enumerated error codes may reach output."""


def configuration(repository, base, release, expected_repository=None):
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repository):
        raise MonitorError("invalid_repository")
    if expected_repository is not None and repository != expected_repository:
        raise MonitorError("repository_mismatch")
    try:
        base = static_canary.validate_base(base)
    except (ValueError, TypeError, AttributeError):
        raise MonitorError("invalid_origin") from None
    if not isinstance(release, str) or not re.fullmatch(r"[a-f0-9]{64}", release):
        raise MonitorError("invalid_release")
    return repository, base, release


def activation_plan(repository, base, release, *, verified=False, tag=None, manifest_sha=None):
    """Legacy offline metadata only. Use local.activate_monitoring for approved activation."""
    repository, base, release = configuration(repository, base, release)
    if verified is not True:
        raise MonitorError("publish_not_verified")
    result = {
        "mode": "metadata_only", "executable": False,
        "activation_command": "python3 -m local.activate_monitoring",
        "requires": ["approved_bundle", "approval_digest", "verified_publication_journal"],
        "variables": {"BOLT_CANARY_REPOSITORY": repository, "BOLT_CANARY_URL": base,
                      "BOLT_CANARY_RELEASE": release, "BOLT_CANARY_ENABLED": "true"},
        "workflows": [WORKFLOW],
    }
    if tag is not None or manifest_sha is not None:
        from local.public_journey import release_plan
        release_plan(repository, base, release, tag, manifest_sha)
        result["variables"].update({"BOLT_CANARY_TAG": tag, "BOLT_CANARY_MANIFEST_SHA": manifest_sha})
        result["workflows"].append("public-journey.yml")
    return result


def canary(repository, base, release, expected_repository, *, drill="none", probe=None, sleep=time.sleep):
    configuration(repository, base, release, expected_repository)
    if drill not in {"none", "failure"}:
        raise MonitorError("invalid_drill")
    if drill == "failure":
        return {"ok": False, "code": "intentional_failure_drill", "attempts": 0}
    probe = probe or static_canary.probe
    state = {}
    for attempt in range(1, 4):
        result = probe(base, release, timeout=3)
        state = static_canary.advance(state, result["ok"], time.time(), "run")
        if result["ok"]:
            return {"ok": True, "code": "healthy", "attempts": attempt, "checks": result["checks"]}
        if attempt < 3:
            sleep(5)
    return {"ok": False, "code": "three_failed_probes", "attempts": 3,
            "event": state["event"], "checks": result["checks"]}


def gh_get(endpoint):
    """Read only; never echo gh stderr, raw API data, or authentication environment."""
    try:
        result = subprocess.run(
            ["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint,
             "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28"],
            capture_output=True, timeout=15, check=False,
        )
        if result.returncode or len(result.stdout) > 1_048_576:
            raise MonitorError("github_read_failed")
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise MonitorError("github_read_failed") from None


def age(timestamp, now):
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        value = now - parsed.timestamp()
        if value < 0:
            raise ValueError
        return value
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise MonitorError("invalid_run_timestamp") from None


def object_name(value):
    return value.get("full_name") if isinstance(value, dict) else None


def schedule_status(repository, *, get=gh_get, now=None):
    """Require a current successful scheduled run, even when manual dispatches succeed."""
    now = time.time() if now is None else now
    metadata = get(f"repos/{repository}")
    if (not isinstance(metadata, dict) or metadata.get("full_name") != repository
            or metadata.get("private") is not False or not metadata.get("default_branch")):
        raise MonitorError("repository_mismatch")
    workflow = get(f"repos/{repository}/actions/workflows/{WORKFLOW}")
    if not isinstance(workflow, dict) or workflow.get("path") != WORKFLOW_PATH:
        raise MonitorError("workflow_mismatch")
    if workflow.get("state") != "active":
        raise MonitorError("workflow_disabled")
    query = urlencode({"event": "schedule", "branch": metadata["default_branch"], "per_page": 10})
    payload = get(f"repos/{repository}/actions/workflows/{WORKFLOW}/runs?{query}")
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list) or not runs:
        raise MonitorError("missing_scheduled_run")
    successful = None
    for index, run in enumerate(runs):
        if (not isinstance(run, dict) or object_name(run.get("repository")) != repository
                or object_name(run.get("head_repository")) != repository
                or run.get("workflow_id") != workflow.get("id") or not workflow.get("id")
                or run.get("path") != WORKFLOW_PATH or run.get("event") != "schedule"
                or run.get("head_branch") != metadata["default_branch"]):
            raise MonitorError("run_identity_mismatch")
        if not isinstance(run.get("status"), str) or run.get("status") not in {
            "completed", "queued", "in_progress", "waiting", "pending", "requested"
        }:
            raise MonitorError("invalid_run_status")
        created_age = age(run.get("created_at"), now)
        updated_age = age(run.get("updated_at"), now)
        if updated_age > created_age:
            raise MonitorError("invalid_run_timestamp")
        if index == 0:
            if created_age > 900:
                raise MonitorError("stale_schedule")
            if run.get("status") != "completed" and created_age > 300:
                raise MonitorError("hung_workflow")
            if run.get("status") == "completed" and run.get("conclusion") != "success":
                raise MonitorError("workflow_failed")
        if (run.get("status") == "completed" and run.get("conclusion") == "success"
                and created_age <= 900):
            if successful is None:
                successful = run.get("id")
    if not successful:
        raise MonitorError("no_recent_success")
    if not isinstance(successful, int) or isinstance(successful, bool) or successful < 1:
        raise MonitorError("run_identity_mismatch")
    jobs_payload = get(f"repos/{repository}/actions/runs/{successful}/jobs?per_page=10")
    jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else None
    if not isinstance(jobs, list) or not any(
        isinstance(job, dict) and job.get("name") == "probe" and job.get("conclusion") == "success"
        and job.get("status") == "completed" and job.get("run_id") == successful
        and isinstance(job.get("steps"), list) and any(
            isinstance(step, dict) and step.get("name") == "Check the verified public release"
            and step.get("status") == "completed" and step.get("conclusion") == "success"
            for step in job["steps"]
        )
        for job in jobs
    ):
        raise MonitorError("workflow_not_probed")
    return {"ok": True, "code": "fresh_schedule"}


def watch(repository, base, release, *, get=gh_get, now=None, probe=None):
    configuration(repository, base, release)
    try:
        schedule = schedule_status(repository, get=get, now=now)
    except MonitorError as error:
        if str(error) == "monitor_deadline":
            raise
        schedule = {"ok": False, "code": str(error)}
    # Still probe when GitHub is unavailable: the two observations distinguish causes.
    site = (probe or static_canary.probe)(base, release, timeout=3)
    return {"ok": schedule["ok"] and site["ok"], "schedule": schedule, "site": site}


def deadline(signum, frame):
    raise MonitorError("monitor_deadline")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["canary", "watch", "activation-plan"])
    parser.add_argument("--repository", default=os.getenv("BOLT_CANARY_REPOSITORY", ""))
    parser.add_argument("--base", default=os.getenv("BOLT_CANARY_URL", ""))
    parser.add_argument("--release", default=os.getenv("BOLT_CANARY_RELEASE", ""))
    parser.add_argument("--drill", default="none", choices=["none", "failure"])
    args = parser.parse_args(argv)
    previous_handler = signal.signal(signal.SIGALRM, deadline)
    signal.alarm(100)
    try:
        if args.command == "canary":
            expected = os.getenv("GITHUB_REPOSITORY", "")
            result = canary(args.repository, args.base, args.release, expected, drill=args.drill)
        elif args.command == "watch":
            result = watch(args.repository, args.base, args.release)
        else:
            # Legacy metadata has no executable API operations or publication-verification claim.
            configuration(args.repository, args.base, args.release)
            result = {"ok": True, "code": "metadata_only", "plan": activation_plan(
                args.repository, args.base, args.release, verified=True)}
    except MonitorError as error:
        result = {"ok": False, "code": str(error)}
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
    print(json.dumps(result, sort_keys=True))
    return int(not result["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
