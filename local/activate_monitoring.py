"""Activate only the reviewed public monitors; the default is an offline plan."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

if __package__ in {None, ""}:
    import release
else:
    from . import release

WORKFLOWS = {
    "public-canary.yml": ("Browser Bolt canary ", "probe", "Check the verified public release"),
    "public-journey.yml": ("Browser Bolt journey ", "journey", "Verify rendered public pages and clean installation"),
}


class ActivationRemote(release.Remote):
    """Bounded GitHub adapter supporting 201/204 responses without JSON bodies."""

    def gh(self, endpoint, method="GET", body=None, missing=False):
        args = ["gh", "api", "--hostname", "github.com", endpoint, "--method", method, "--include"]
        if body is not None:
            args += ["--input", "-"]
        try:
            result = subprocess.run(args, input=release.canonical(body) if body is not None else None,
                                    capture_output=True, check=False, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            raise release.ReleaseError("GitHub request unavailable or timed out; raw details suppressed") from None
        if len(result.stdout) > 4 * 1024 * 1024:
            raise release.ReleaseError("GitHub response exceeds the bounded metadata limit")
        output = result.stdout.replace(b"\r\n", b"\n")
        headers, separator, content = output.partition(b"\n\n")
        match = re.match(rb"HTTP/\S+ (\d{3})", headers)
        status = int(match[1]) if match else None
        if missing and status == 404:
            return None
        if result.returncode or status is None or not 200 <= status < 300 or not separator:
            raise release.ReleaseError("GitHub request failed; raw response suppressed")
        try:
            return json.loads(content) if content.strip() else None
        except ValueError:
            raise release.ReleaseError("GitHub returned malformed metadata; raw response suppressed") from None

    def published_identity(self, state):
        repo = self.gh(f"repos/{self.repo}")
        if (repo.get("id") != state["repo_id"] or repo.get("full_name") != self.repo
                or repo.get("private") is not False or not repo.get("fork")
                or repo.get("parent", {}).get("full_name") != release.UPSTREAM
                or repo.get("default_branch") != "browser-bolt"):
            raise release.ReleaseError("Published repository identity or default branch differs")
        branch = self.gh(f"repos/{self.repo}/branches/browser-bolt")
        if branch.get("commit", {}).get("sha") != state["commit"]:
            raise release.ReleaseError("Default branch no longer matches the approved commit")
        self.check_release_tag(state, required=True)
        published = self.gh(f"repos/{self.repo}/releases/{state['release_id']}")
        if published.get("draft") is not False or published.get("tag_name") != self.config["tag"]:
            raise release.ReleaseError("Expected public release is not published")
        if self.deployment_matches(state) != state["deployment_id"]:
            raise release.ReleaseError("Current Cloudflare deployment differs from the verified publication")

    def variables(self):
        result = self.gh(f"repos/{self.repo}/actions/variables?per_page=100")
        if result.get("total_count", 101) > 100:
            raise release.ReleaseError("Repository variable inventory exceeds the bounded limit")
        values = result.get("variables", [])
        if len(values) != result["total_count"] or len({item["name"] for item in values}) != len(values):
            raise release.ReleaseError("Incomplete or duplicate repository variable inventory")
        return {item["name"]: item["value"] for item in values}

    def workflow(self, name):
        value = self.gh(f"repos/{self.repo}/actions/workflows/{name}")
        if value.get("path") != f".github/workflows/{name}" or type(value.get("id")) is not int:
            raise release.ReleaseError("Unexpected public workflow identity")
        return value


def desired_variables(manifest, bundle):
    config = manifest["config"]
    version = json.loads((bundle / "site/version.json").read_text())
    return {"BOLT_CANARY_REPOSITORY": config["github_repo"], "BOLT_CANARY_URL": release.public_url(config),
            "BOLT_CANARY_RELEASE": version["releaseId"], "BOLT_CANARY_TAG": config["tag"],
            "BOLT_CANARY_MANIFEST_SHA": release.digest((bundle / "public-manifest.json").read_bytes()),
            "BOLT_CANARY_ENABLED": "true"}


def journal_path(bundle):
    return bundle.with_name(bundle.name + ".publication.json")


def read_journal(bundle, identity, *, required=False):
    path = journal_path(bundle)
    if path.is_symlink():
        raise release.ReleaseError("Publication journal symlink refused")
    state = json.loads(path.read_text()) if path.exists() else {}
    if state and state.get("digest") != identity:
        raise release.ReleaseError("Publication journal belongs to another candidate")
    recovery = state.get("recovery", {})
    ready = (state.get("complete") is True and state.get("site_verified") is True
             and isinstance(recovery, dict) and recovery.get("exposure", "restored") == "restored"
             and all(state.get(key) for key in ("repo_id", "commit", "release_id", "deployment_id")))
    if required and not ready:
        raise release.ReleaseError("Matching completed and verified publication is required before activation")
    return state, bool(ready)


def verify_workflows(bundle):
    for name in WORKFLOWS:
        release.safe_file(bundle / "source/.github/workflows" / name, bundle / "source")


def configure(remote, state, desired, persist):
    activation = state.setdefault("monitoring_activation", {"variables": {}, "workflows": {}})
    existing = remote.variables()
    for name, value in desired.items():
        if name in existing and existing[name] != value:
            raise release.ReleaseError("Conflicting repository variable; no values were overwritten")
    if existing.get("BOLT_CANARY_ENABLED") == "true" and any(existing.get(k) != v for k, v in desired.items()):
        raise release.ReleaseError("Monitoring is already enabled with incomplete reviewed variables")

    def variable(name):
        value = desired[name]
        entry = activation["variables"].setdefault(name, {"value": value})
        if entry.get("value") != value:
            raise release.ReleaseError("Activation journal variable differs from this approved candidate")
        if name not in existing:
            if entry.get("create_started"):
                raise release.ReleaseError("Variable creation outcome is uncertain; inspect before reconciling")
            entry["create_started"] = True
            persist()
            remote.gh(f"repos/{remote.repo}/actions/variables", "POST", {"name": name, "value": value})
        actual = remote.gh(f"repos/{remote.repo}/actions/variables/{name}")
        if actual.get("name") != name or actual.get("value") != value:
            raise release.ReleaseError("Repository variable readback differs from reviewed value")
        entry["verified"] = True
        persist()

    for name in desired:
        if name != "BOLT_CANARY_ENABLED":
            variable(name)
    for name in WORKFLOWS:
        workflow = remote.workflow(name)
        entry = activation["workflows"].setdefault(name, {"id": workflow["id"]})
        if entry["id"] != workflow["id"]:
            raise release.ReleaseError("Workflow identity changed during activation")
        if workflow.get("state") != "active":
            entry["enable_started"] = True
            persist()
            remote.gh(f"repos/{remote.repo}/actions/workflows/{name}/enable", "PUT")
        verified = remote.workflow(name)
        if verified.get("state") != "active" or verified["id"] != entry["id"]:
            raise release.ReleaseError("Workflow enablement was not confirmed")
        entry["enabled"] = True
        persist()
    # Re-read every prerequisite immediately before exposing schedules to the enabled flag.
    actual = remote.variables()
    if any(actual.get(name) != value for name, value in desired.items() if name != "BOLT_CANARY_ENABLED"):
        raise release.ReleaseError("Monitoring prerequisite variables changed before enablement")
    remote.published_identity(state)
    variable("BOLT_CANARY_ENABLED")
    activation["configured"] = True
    persist()
    for name in WORKFLOWS:
        entry = activation["workflows"][name]
        if entry.get("dispatch_started"):
            expected = dispatch_inputs(desired, state, name, entry.get("launch_id"))
            if entry.get("dispatch_ref") != remote.config["tag"] or entry.get("inputs") != expected:
                raise release.ReleaseError("Dispatch journal differs from the approved input contract")
            continue  # A lost POST response must never create a second run.
        # The version tag is re-resolved immediately before dispatch. Manual jobs
        # also receive pinned source/artifact inputs and guard github.sha before checkout.
        remote.published_identity(state)
        entry.update({"launch_id": uuid.uuid4().hex, "dispatch_started": time.time(),
                      "dispatch_ref": remote.config["tag"]})
        inputs = dispatch_inputs(desired, state, name, entry["launch_id"])
        entry["inputs"] = inputs
        persist()
        remote.gh(f"repos/{remote.repo}/actions/workflows/{name}/dispatches", "POST",
                  {"ref": entry["dispatch_ref"], "inputs": inputs})
        entry["dispatch_confirmed"] = True
        persist()


def dispatch_inputs(desired, state, name, launch_id):
    if not isinstance(launch_id, str) or not re.fullmatch(r"[0-9a-f]{32}", launch_id):
        raise release.ReleaseError("Invalid launch correlation id")
    inputs = {"launch_id": launch_id, "repository": desired["BOLT_CANARY_REPOSITORY"],
              "url": desired["BOLT_CANARY_URL"], "release_id": desired["BOLT_CANARY_RELEASE"],
              "release_tag": desired["BOLT_CANARY_TAG"], "manifest_sha": desired["BOLT_CANARY_MANIFEST_SHA"],
              "expected_sha": state["commit"]}
    if name == "public-canary.yml":
        inputs["drill"] = "none"
    return inputs


def check_runs(remote, state):
    activation = state.get("monitoring_activation", {})
    outcomes = {}
    for name, (prefix, job_name, step_name) in WORKFLOWS.items():
        entry = activation.get("workflows", {}).get(name, {})
        if not entry.get("dispatch_started"):
            outcomes[name] = {"status": "not_dispatched"}
            continue
        workflow = remote.workflow(name)
        if workflow["id"] != entry["id"] or workflow.get("state") != "active":
            raise release.ReleaseError("Configured workflow identity or enablement changed")
        if entry.get("dispatch_ref") != remote.config["tag"]:
            raise release.ReleaseError("Dispatch reference differs from the approved release tag")
        result = remote.gh(f"repos/{remote.repo}/actions/workflows/{name}/runs?event=workflow_dispatch&per_page=100")
        runs = result.get("workflow_runs", [])
        if len(runs) > 100:
            raise release.ReleaseError("Workflow run inventory exceeds the bounded limit")
        matches = [run for run in runs if run.get("display_title") == prefix + entry["launch_id"]]
        if not matches:
            status = "pending" if entry.get("dispatch_confirmed") else "dispatch_unconfirmed"
            if time.time() - entry["dispatch_started"] > 900:
                status = "missing_run"
            outcomes[name] = {"status": status}
            continue
        if len(matches) != 1:
            raise release.ReleaseError("Duplicate workflow correlation; inspect the actual runs")
        run = matches[0]
        try:
            created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")).timestamp()
        except (KeyError, TypeError, ValueError):
            raise release.ReleaseError("Workflow run timestamp is unavailable") from None
        if (run.get("event") != "workflow_dispatch" or run.get("workflow_id") != entry["id"]
                or run.get("head_sha") != state["commit"] or run.get("head_branch") != entry["dispatch_ref"]
                or run.get("repository", {}).get("id") != state["repo_id"]
                or run.get("head_repository", {}).get("id") != state["repo_id"]
                or run.get("path") not in {f".github/workflows/{name}",
                                           f".github/workflows/{name}@refs/tags/{entry['dispatch_ref']}"}
                or created < entry["dispatch_started"] - 5 or created > time.time() + 30
                or type(run.get("id")) is not int):
            raise release.ReleaseError("Workflow run does not match this publication and launch correlation")
        outcome = {"run_id": run["id"], "status": "pending"}
        if run.get("status") != "completed":
            outcomes[name] = outcome
            continue
        jobs = remote.gh(f"repos/{remote.repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100")
        items = jobs.get("jobs", [])
        selected = [job for job in items if job.get("name") == job_name]
        if jobs.get("total_count", 101) > 100 or len(items) != jobs.get("total_count") or len(selected) != 1:
            raise release.ReleaseError("Expected workflow job is missing or ambiguous")
        job = selected[0]
        steps = [step for step in job.get("steps", []) if step.get("name") == step_name]
        passed = (job.get("run_id") == run["id"] and job.get("head_sha") == state["commit"]
                  and run.get("conclusion") == "success" and job.get("status") == "completed"
                  and job.get("conclusion") == "success" and len(steps) == 1
                  and steps[0].get("status") == "completed" and steps[0].get("conclusion") == "success")
        outcome["status"] = "succeeded" if passed else "failed_or_skipped"
        outcomes[name] = outcome
    return {"configured": bool(activation.get("configured")), "runs": outcomes,
            "manual_runs_verified": all(v["status"] == "succeeded" for v in outcomes.values()),
            "scheduled_coverage_verified": False, "notification_delivery_verified": False}


def activation(root, bundle, *, execute=False, approval=None, check=False, remote=None):
    manifest, identity = release.verify(root, bundle)
    desired = None if execute else desired_variables(manifest, bundle)
    state, ready = read_journal(bundle, identity, required=execute or check)
    plan = {"digest": identity, "repository": manifest["config"]["github_repo"], "variables": desired,
            "workflows": list(WORKFLOWS), "publication_verified_in_journal": ready}
    if not execute and not check:
        return plan | {"mode": "plan", "network_used": False, "monitoring_verified": False}
    if execute and approval != identity:
        raise release.ReleaseError("Monitoring activation requires the exact approved digest")
    service = remote or ActivationRemote(manifest["config"])
    if check:
        service.published_identity(state)
        current_variables = service.variables()
        if any(current_variables.get(name) != value for name, value in desired.items()):
            raise release.ReleaseError("Monitoring variables no longer match the reviewed values")
        return plan | {"mode": "check", **check_runs(service, state)}
    lock = bundle.with_name(bundle.name + ".publication.lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise release.ReleaseError("Publication/recovery lock exists; inspect before activating") from None
    os.close(descriptor)
    try:
        state, _ = read_journal(bundle, identity, required=True)
        with tempfile.TemporaryDirectory(prefix="bolt-monitor-activation-") as temporary:
            frozen = Path(temporary) / "candidate"
            shutil.copytree(bundle, frozen, symlinks=True)
            if release.verify(root, frozen)[1] != identity:
                raise release.ReleaseError("Activation copy differs from the approved digest")
            verify_workflows(frozen)
            desired = desired_variables(manifest, frozen)
            plan["variables"] = desired

            def persist():
                release.save(journal_path(bundle), state)

            service.published_identity(state)
            service.verify_site(frozen, state, persist)
            # Catch valid candidate replacement or mutation during remote preflight as well.
            if release.verify(root, frozen)[1] != identity or release.verify(root, bundle)[1] != identity:
                raise release.ReleaseError("Activation candidate changed from the approved digest before mutation")
            configure(service, state, desired, persist)
            return plan | {"mode": "activation_dispatched", **check_runs(service, state)}
    finally:
        lock.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "status", "check"), nargs="?", default="plan")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approve")
    args = parser.parse_args(argv)
    try:
        if args.execute and args.command != "plan":
            raise release.ReleaseError("status/check are read-only; omit their command name for execution")
        result = activation(release.ROOT, args.bundle.absolute(), execute=args.execute, approval=args.approve,
                            check=args.command in {"status", "check"})
        print(json.dumps(result, indent=2))
        return 0
    except (release.ReleaseError, OSError, ValueError, KeyError, TypeError, AttributeError):
        # Deliberately omit raw API bodies, variable values, and exception strings.
        print(json.dumps({"ok": False, "code": "activation_not_confirmed", "details_suppressed": True}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
