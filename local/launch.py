"""One approved invocation to publish Browser Bolt and start its prepared monitors."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__ in {None, ""}:
    import activate_monitoring
    import release
else:
    from . import activate_monitoring, release


class LaunchError(release.ReleaseError):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def launch(root, bundle, *, execute=False, approval=None, mode="plan"):
    if mode not in {"plan", "status", "check"} or (execute and mode != "plan"):
        raise LaunchError("arguments", "status/check are read-only")
    manifest, identity = release.verify(root, bundle)
    state, ready = activate_monitoring.read_journal(bundle, identity)
    report = {"digest": identity, "repository": manifest["config"]["github_repo"],
              "site": release.public_url(manifest["config"]), "tag": manifest["config"]["tag"]}
    if mode == "check":
        result = activate_monitoring.activation(root, bundle, check=True)
        if result.get("digest") != identity:
            raise LaunchError("monitoring_check", "Monitoring check returned a different candidate identity")
        return report | {"mode": "check", "network_used": True, "remote_writes": False,
                         "monitoring": result}
    if not execute:
        activation = state.get("monitoring_activation", {})
        return report | {"mode": mode, "network_used": False,
                         "publication_recorded": bool(state.get("complete")),
                         "site_verified_in_journal": bool(state.get("site_verified")),
                         "ready_for_activation_in_journal": ready,
                         "monitoring_configured_in_journal": bool(activation.get("configured")),
                         "manual_runs_verified": False, "scheduled_coverage_verified": False,
                         "notification_delivery_verified": False,
                         "next": "Use --execute --approve with this exact reviewed digest to publish and activate"}
    if approval != identity:
        raise LaunchError("approval", "Launch requires the exact approved candidate digest")
    # Existing stages own their locks, frozen copies, journals and uncertain-write guards.
    # The wrapper never rebuilds artifacts, changes a journal, or performs remote writes itself.
    try:
        publication = release.publish(root, bundle, approval=identity)
    except release.ReleaseError as error:
        raise LaunchError("publication", str(error)) from None
    if publication.get("published") is not True or publication.get("digest") != identity:
        raise LaunchError("publication", "Publication did not confirm this exact approved candidate")
    try:
        if release.verify(root, bundle)[1] != identity:
            raise release.ReleaseError("Candidate differs from the approved digest after publication")
        activate_monitoring.read_journal(bundle, identity, required=True)
    except release.ReleaseError as error:
        raise LaunchError("after_publication", str(error)) from None
    try:
        monitoring = activate_monitoring.activation(root, bundle, execute=True, approval=identity)
    except release.ReleaseError as error:
        raise LaunchError("monitoring_activation", str(error)) from None
    if monitoring.get("digest") != identity or monitoring.get("configured") is not True:
        raise LaunchError("monitoring_activation", "Monitoring configuration was not confirmed for this candidate")
    # Dispatch can succeed while jobs are pending. Scheduled coverage and notification
    # delivery remain distinct observations and are never implied by this command.
    return report | {"mode": "publication_and_activation", "publication_verified": True,
                     "monitoring_configured": True, "monitoring": monitoring,
                     "manual_runs_verified": monitoring.get("manual_runs_verified") is True,
                     "scheduled_coverage_verified": False, "notification_delivery_verified": False,
                     "next": "Use check to observe actual manual jobs; scheduled and delivery checks remain separate"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "status", "check"), nargs="?", default="plan")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approve", help="The exact previously reviewed candidate digest")
    args = parser.parse_args(argv)
    try:
        result = launch(release.ROOT, args.bundle.absolute(), execute=args.execute, approval=args.approve,
                        mode=args.command)
        print(json.dumps(result, indent=2))
        return 0
    except LaunchError as error:
        print(json.dumps({"ok": False, "stage": error.stage, "error": str(error),
                          "next": "Inspect the existing publication journal; retry only the same approved candidate"}))
        return 1
    except (release.ReleaseError, OSError, ValueError, KeyError, TypeError, AttributeError):
        print(json.dumps({"ok": False, "stage": "verification_or_execution", "details_suppressed": True,
                          "next": "Inspect the existing publication journal before retrying"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
