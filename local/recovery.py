#!/usr/bin/env python3
"""Plan or explicitly pause/resume one approved Browser Bolt workers.dev deployment."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from local import release
except ModuleNotFoundError:
    import release


class RecoveryRemote(release.Remote):
    def owned_deployment(self, state):
        """A local journal alone is insufficient authority over an existing script."""
        if self.cf("/subdomain").get("subdomain") != self.config["workers_subdomain"]:
            raise release.ReleaseError("Cloudflare account subdomain differs from the approved destination")
        actual = self.deployment_matches(state)
        if not actual or actual != state["deployment_id"]:
            raise release.ReleaseError("Current Workers deployment differs from the publication journal")
        return actual

    def exposure(self):
        value = self.cf(f"/scripts/{self.config['cloudflare_script']}/subdomain")
        if not isinstance(value, dict) or any(type(value.get(key)) is not bool for key in
                                              ("enabled", "previews_enabled")):
            raise release.ReleaseError("Cloudflare exposure response is incomplete")
        return {key: value[key] for key in ("enabled", "previews_enabled")}

    def set_exposure(self, target):
        return self.cf(f"/scripts/{self.config['cloudflare_script']}/subdomain", "POST", target)


def journal(bundle, identity):
    path = bundle.with_name(bundle.name + ".publication.json")
    if path.is_symlink() or not path.is_file():
        raise release.ReleaseError("Recovery requires an existing regular publication journal")
    state = json.loads(path.read_text())
    if state.get("digest") != identity:
        raise release.ReleaseError("Publication journal belongs to another candidate")
    if not isinstance(state.get("deployment_id"), str) or not state["deployment_id"]:
        raise release.ReleaseError("Publication journal has no confirmed deployment to recover")
    return path, state


def recover(root, bundle, action="pause", *, execute=False, approval=None, remote=None):
    if action not in {"pause", "resume"}:
        raise release.ReleaseError("Unknown recovery action")
    manifest, identity = release.verify(root, bundle)
    if approval is not None and approval != identity:
        raise release.ReleaseError("Approval digest does not match this exact candidate")
    target = {"enabled": action == "resume", "previews_enabled": False}
    plan = {"mode": "plan", "action": action, "digest": identity,
            "site": release.public_url(manifest["config"]), "target": target,
            "scope": "workers.dev exposure only; GitHub source and release remain public",
            "executed": False, "network_checked": False}
    if not execute:
        return plan
    if approval != identity:
        raise release.ReleaseError("Execution requires the exact approved candidate digest")
    if not manifest["config"]["cloudflare_account_id"] or not plan["site"]:
        raise release.ReleaseError("Approved candidate lacks a complete Cloudflare destination")
    lock = bundle.with_name(bundle.name + ".publication.lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise release.ReleaseError("Publication lock exists; another publication or recovery may be running") from None
    os.close(descriptor)
    try:
        state_path, state = journal(bundle, identity)
        previous = state.get("recovery", {})
        if not isinstance(previous, dict) or not isinstance(previous.get("attempts", []), list):
            raise release.ReleaseError("Invalid recovery evidence in publication journal")
        if action == "resume" and previous.get("exposure") not in {
                "paused", "changing", "unknown", "enabled_unverified", "restored"}:
            raise release.ReleaseError("Resume requires an earlier recovery attempt for this candidate")
        service = remote or RecoveryRemote(manifest["config"])
        with tempfile.TemporaryDirectory(prefix="browser-bolt-recovery-") as temporary:
            frozen = Path(temporary) / "candidate"
            shutil.copytree(bundle, frozen, symlinks=True)
            _, frozen_identity = release.verify(root, frozen)
            if frozen_identity != identity:
                raise release.ReleaseError("Copied candidate differs from the approved digest")
            service.owned_deployment(state)
            before = service.exposure()
            attempt = {"action": action, "started_at": datetime.now(timezone.utc).isoformat(),
                       "deployment_id": state["deployment_id"], "before": before,
                       "target": target, "result": "pending"}
            recovery = {"exposure": "changing", "attempts": [*previous.get("attempts", []), attempt]}
            state["recovery"] = recovery
            state["site_verified"] = False

            def persist():
                release.save(state_path, state)

            persist()  # Write intent before any mutation, including an uncertain response.
            try:
                if before != target:
                    try:
                        response = service.set_exposure(target)
                        attempt["update_response_matches"] = response == target
                    except release.ReleaseError:
                        # POST may have succeeded despite a timeout. Re-read fixed state,
                        # never invert a toggle or retry a mutation on an assumed outcome.
                        attempt["update_response_matches"] = False
                        attempt["update_response_uncertain"] = True
                service.owned_deployment(state)
                after = service.exposure()
                attempt["after"] = after
                if after != target:
                    raise release.ReleaseError("Workers exposure did not reach the requested fixed state")
                recovery["exposure"] = "enabled_unverified" if action == "resume" else "paused"
                persist()
                if action == "resume":
                    service.verify_site(frozen, state, persist)
                    service.owned_deployment(state)
                    if service.exposure() != target:
                        raise release.ReleaseError("Workers exposure changed during public verification")
                    recovery["exposure"] = "restored"
                attempt["result"] = "confirmed"
                attempt["finished_at"] = datetime.now(timezone.utc).isoformat()
                persist()
            except Exception:
                if recovery["exposure"] == "changing":
                    recovery["exposure"] = "unknown"
                attempt["result"] = "failed"
                attempt["finished_at"] = datetime.now(timezone.utc).isoformat()
                persist()
                raise
        return plan | {"mode": recovery["exposure"], "executed": True, "network_checked": True,
                       "deployment_id": state["deployment_id"], "public_site_verified": action == "resume"}
    finally:
        lock.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pause", "resume"), nargs="?", default="pause")
    parser.add_argument("--bundle", type=Path, default=release.ROOT / "dist/release-candidate")
    parser.add_argument("--execute", action="store_true", help="Apply the explicit fixed state after approval")
    parser.add_argument("--approve", help="Exact reviewed candidate manifest SHA-256")
    args = parser.parse_args(argv)
    try:
        result = recover(release.ROOT, args.bundle.absolute(), args.action,
                         execute=args.execute, approval=args.approve)
        print(json.dumps(result, indent=2))
        return 0
    except (release.ReleaseError, ValueError, OSError, KeyError, TypeError,
            tarfile.TarError, zipfile.BadZipFile) as error:
        detail = str(error) if isinstance(error, release.ReleaseError) else "Invalid or unreadable recovery state"
        print(json.dumps({"ok": False, "error": detail}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
