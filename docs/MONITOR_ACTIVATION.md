# Activate the reviewed public monitors

The activation command applies only to the exact approved Browser Bolt bundle after its publication journal records a completed release and verified site. It enables the existing public static canary and public setup/install journey. It does not buy services, change billing, add notification integrations, or perform a failure-notification drill.

From the repository root, preview the configuration without network access:

```sh
python3 -m local.activate_monitoring --bundle dist/release-001
```

After the same exact release digest has been approved and publication has completed, one command configures and dispatches both monitors:

```sh
python3 -m local.activate_monitoring --bundle dist/release-001 --execute --approve EXACT_APPROVED_DIGEST
```

No additional approval is implied after Rich has approved the release and its monitoring activation. The command requires the original approved bundle and publication journal. It uses the shared publication/recovery lock. A paused, uncertain, incomplete, or unverified publication is refused.

Before any external write, activation makes a private copy, verifies its exact digest, and derives every written variable from that verified copy. It checks the actual public fork id, default branch and commit, published tag, Cloudflare deployment, and fresh static site verification. It rechecks the artifacts after those reads. The command never rebuilds or changes the approved bytes.

The configured repository variables are:

- `BOLT_CANARY_REPOSITORY`: approved owner/repository.
- `BOLT_CANARY_URL`: approved Workers static URL.
- `BOLT_CANARY_RELEASE`: exact static `version.json` releaseId.
- `BOLT_CANARY_TAG`: approved version tag.
- `BOLT_CANARY_MANIFEST_SHA`: SHA-256 of the uploaded public-manifest.json bytes.
- `BOLT_CANARY_ENABLED`: `true`, set last after other values and workflow enablement are read back.

Conflicting existing variables are refused. Exact matching values can be reused. Missing variables are created with persisted intent; an uncertain create result is never automatically repeated. The command enables only `.github/workflows/public-canary.yml` and `.github/workflows/public-journey.yml`, verifying their actual identities and active state. Workflow enablement is an idempotent fixed-state operation.

Each manual dispatch uses the reviewed version tag after checking that it still resolves to the approved commit. Its saved input payload includes a unique `launch_id`, the repository, URL, static releaseId, version tag, public manifest hash, and expected source commit. Canary dispatches explicitly use `drill: none`. The approved workflows must expose those inputs, check `github.sha` against the expected commit before checkout, check out that commit, and use the supplied values for manual validation. Scheduled runs continue using repository variables. This prevents queued manual checks from silently consuming later variable changes.

The publication journal retains the exact variable values, workflow ids, enablement intent, dispatch reference, unique correlation ids, and dispatch input payloads. If a dispatch response is lost, rerunning activation does not dispatch that workflow again. It reports an unconfirmed or pending run while the read-only check reconciles the original request. Do not remove intent fields or invent a new launch id to bypass an uncertain request.

Check the actual jobs without changing the journal or making remote writes:

```sh
python3 -m local.activate_monitoring check --bundle dist/release-001
```

`status` is an alias for this read-only check. It verifies the published identity and current variables, then correlates each manual run by the saved launch id, workflow id/path, source commit, version-tag ref, repository identities, and creation time. A successful overall workflow is insufficient: its expected `probe` or `journey` job and actual probe/install step must both have run successfully. Wrong runs, skipped jobs, and skipped critical steps cannot satisfy verification. Missing runs are reported as pending or unconfirmed initially and as missing after 15 minutes; the command does not automatically redispatch.

The result distinguishes configuration, pending jobs, and verified manual jobs. It never treats those results as proof of scheduled execution, independent stale detection, or notification delivery. Those remain separate observed checks in [MONITORING.md](MONITORING.md). Native failure delivery and acknowledgement can be drilled explicitly later without adding an external notification service.

All GitHub CLI calls use argument arrays, GitHub.com explicitly, captured output, a 30-second timeout and a 4 MiB metadata limit. Empty successful API responses, including HTTP 204 from enable/dispatch, are supported. Untrusted response bodies and credentials are never printed.
