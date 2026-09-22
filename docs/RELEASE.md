# Browser Bolt release command

`python3 local/release.py` shows local status. It does not publish. `publish` without `--approve` also shows a preview without network access. There is no interactive default that can accidentally launch.

A candidate contains a restricted public repository tree, freshly built wheel and source distribution, hashed dependency inventory, and freshly built direct-static website. Package names remain `jev-qwerebras-ultrafast`; upstream fork provenance and the MIT license remain intact. The public branch has the pinned upstream commit as its parent. It does not upload working-repository history or the internal launch workspace.

## Prepare and review

Required local tools: Python 3.12+, uv, Node 22+. Preparation builds packages with the pinned backend and may download build dependencies. It does not run paid providers or publish. Install and authenticate GitHub CLI and an explicitly chosen Wrangler installation before preflight. The command never installs a CLI or logs in automatically.

Create an ignored local configuration file, for example `artifacts/release-config.json`:

```json
{
  "github_repo": "valente-labs/browser-bolt",
  "tag": "v0.1.0",
  "cloudflare_account_id": "REPLACE_WITH_ACCOUNT_ID",
  "cloudflare_script": "browser-bolt",
  "workers_subdomain": "REPLACE_WITH_SUBDOMAIN",
  "wrangler": "/absolute/path/to/wrangler"
}
```

Account and project identifiers are destination metadata, not credentials. Supply `CLOUDFLARE_API_TOKEN` only in the process environment, using an existing secret-management session. GitHub authentication uses the existing `gh` credential store. Tokens never appear in command arguments or normal/error output. Wrangler receives only a fixed system PATH, isolated temporary HOME/TMPDIR, the reviewed account id, the Cloudflare token, and fixed CI/metrics flags. Destination overrides, proxies, Node options, GitHub credentials, and provider credentials are not inherited. The token needs Workers Scripts edit access to the chosen account. A read-only preflight can establish access and inspect destinations, but cannot guarantee future write authorization or quota availability.

```sh
python3 local/release.py prepare --config artifacts/release-config.json --bundle dist/release-001
python3 local/release.py verify --bundle dist/release-001
python3 local/release.py preflight --bundle dist/release-001
python3 local/release.py publish --bundle dist/release-001
```

Review the private `dist/release-001/manifest.json` and every public artifact, and complete the project launch checks before signoff. The printed digest is the SHA-256 of canonical JSON for the full manifest. It covers public source inputs, every artifact byte, and destinations. This private approval manifest is never uploaded because it includes local executable and account configuration. The separate `public-manifest.json` contains public artifact hashes and destination URLs and is uploaded with the packages. The limited credential/private-path scan is a guard, not an exhaustive security audit. Any source, build, or destination change requires a new candidate and a new approval. Existing candidates are retained and never overwritten.

The site is a static BYOK preview. Preparation supplies the approved repository and tag to the static builder, producing exact versioned package links and public indexing settings before review. Publication does not edit those bytes. The package assets include SHA256SUMS. This command does not buy a domain, enable paid services, publish to PyPI, send messages, or publish marketing posts.

## One approval, one launch command

Use the [combined launcher](LAUNCH_COMMAND.md) to run publication and monitoring activation with the same exact approval. The lower-level publish command below is also available; it publishes artifacts but does not activate monitoring.

Only after Rich reviews the full packet and explicitly approves its exact digest, run:

```sh
python3 local/release.py publish --bundle dist/release-001 --approve EXACT_64_CHARACTER_DIGEST
```

This one command checks local drift and access, then rechecks that its private candidate copy still matches the originally approved digest before any external write. It creates a true public fork of `browser-use/jev-ultrafast`, creates the exact public tree on `browser-bolt`, sets it as the default branch, stages a prerelease with the exact package files, deploys Cloudflare Workers direct-static output, runs the existing live static canary, and publishes the GitHub prerelease. Creating the fork and branch is already public publication, which is why approval is checked before the first remote mutation. After recording the new fork id, the command polls repository and pinned-commit readiness with six read-only attempts, two seconds apart. A timeout preserves the journal for a later retry. A GitHub source download comes from this exact branch/tag and retains upstream ancestry. Resumed releases and final publication check the actual tag target, including annotated tags, against the recorded commit. The final public tag must also be readable before completion is recorded.

The Workers script must be new, or already carry this exact candidate digest from a prior launch attempt. Existing unrelated scripts are refused. The account’s existing workers.dev subdomain must match the reviewed configuration. No custom domain or billing plan is changed. The first launch requires a destination GitHub repository that does not already exist; an existing fork is accepted only when its id is recorded in this candidate's publication journal. Unknown branches, tags, releases and conflicting assets are refused, never overwritten.

## Partial failure and recovery

The sibling `release-001.publication.json` records the candidate digest, completed remote identities, and sanitized detailed canary results. Canary evidence survives removal of the temporary publication copy, including a failed probe. The same read-only `Remote.verify_site` check is reusable by recovery. If the journal records a recovery exposure state, publication is blocked until explicit recovery confirms it as `restored`. Keep it alongside the candidate. Rerun the same approved command to resume. A matching successful Cloudflare deployment is reused, and existing release assets must expose the expected GitHub SHA-256 digest. A deployment whose outcome is uncertain is not automatically repeated. Inspect Cloudflare and reconcile the recorded attempt before retrying. A completed deployment tagged with this exact candidate is reused. A failed live canary leaves the GitHub release draft, even though the fork/site may already be public.

The command takes a sibling `.publication.lock` to prevent concurrent launches. After a killed process, inspect for an active launch before removing a stale lock. Creation intent is saved before fork, commit, branch, release, asset upload, and deployment requests. If a request succeeded remotely but its response never reached the local journal and a later read cannot confirm the expected object, the tool fails closed instead of repeating the write. Content-addressed Git blobs and trees can be safely repeated; fixed-state updates to the known default branch or known release are idempotent. Inspect the actual remote object and reconcile its id with the journal only after confirming ownership; do not create a new journal or change a digest to bypass review. A retry never deletes remote resources or rolls back unrelated work. After a production failure, use the separately rehearsed rollback procedure and retain the candidate and journal as evidence.

The command is for the first approved launch and its recovery. A later version in an existing repository needs a separately reviewed update workflow. Do not reuse first-launch configuration to replace another release.

API references: [GitHub fork API](https://docs.github.com/en/rest/repos/forks), [Wrangler deploy commands](https://developers.cloudflare.com/workers/wrangler/commands/workers/), [Cloudflare Workers deployment API](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/subresources/deployments/).
