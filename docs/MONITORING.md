# Public static monitoring

Prepared September 21, 2026. This implementation is not activated. It covers the public static Browser Bolt site and its expected release digest. The separate prepared nightly journey below covers rendered setup pages and a fresh package installation. Neither runner monitors managed inference, customer accounts, payments, or a general browser task.

## Prepared runner

`.github/workflows/public-canary.yml` uses the existing pinned checkout action, a standard Ubuntu runner, and Python's standard library. It has only `contents: read`, does not persist checkout credentials, and receives no provider or deployment credentials. There is no model API, notification integration, paid runner, cache, artifact upload, or persistent external state.

The workflow schedules at minutes 2, 7, 12, and every five minutes thereafter. It is serialized, has a three-minute job ceiling and a 100-second command ceiling. Each execution reuses `local/static_canary.py` for seven GET checks. An unhealthy result gets at most two retries separated by five seconds. Each request has a three-second socket timeout. Three consecutive failed probes within that execution fail the workflow; a healthy attempt succeeds. This is not a persistent counter across separate scheduled runs. It does not promise incident deduplication or a recovery notification across runs. The existing local stateful canary remains available when those events are needed.

The job is skipped until `BOLT_CANARY_ENABLED=true` and the repository is public. Once enabled, missing URL, digest, or repository identity fails before HTTP requests. `BOLT_CANARY_REPOSITORY` must exactly equal GitHub's current repository. Redirects, wrong release or static-mode identity, bad content, missing security headers, and unexpected HTTP statuses fail the checks. Output contains enumerated codes, check names, statuses, attempt counts, and durations. It excludes response bodies, raw errors, and full target URLs.

GitHub documents free standard hosted Actions usage for public repositories. This workflow uses that arrangement, subject to the account's availability and GitHub terms; no paid plan or larger runner is requested. Schedules may be delayed or dropped under load and public schedules may be disabled after inactivity. They are not a five-minute delivery guarantee. Fork schedules may require explicit enablement, and the workflow must be on the default branch. [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Activate the approved publication

Use `local.activate_monitoring` from the reviewed release checkout. The command verifies the exact bundle and its publication journal before configuring monitoring. Include both public workflows, `local/activate_monitoring.py`, `local/monitor.py`, `local/static_canary.py`, `local/public_journey.py`, and this document in the public export.

Inspect the local plan without network access:

```sh
python3 -m local.activate_monitoring plan --bundle /path/to/approved-bundle
```

After the approved publication is complete and verified, activate against that same bundle and approval digest:

```sh
python3 -m local.activate_monitoring --bundle /path/to/approved-bundle \
  --execute --approve APPROVED_MANIFEST_SHA256
python3 -m local.activate_monitoring check --bundle /path/to/approved-bundle
```

`APPROVED_MANIFEST_SHA256` is the exact release approval digest. It is distinct from the static release ID and the SHA-256 of the uploaded public manifest. Use the reviewed value; do not substitute a new build's digest. `check` is read-only and correlates the recorded runs. The activation command verifies publication identity and live static content, configures and reads back the full repository-variable set, explicitly enables both workflows, and dispatches against the approved version tag. It retains dispatch identity in the publication journal so an ambiguous response does not trigger an untracked repeat. Configuration or a successful dispatch response alone does not prove that a job ran.

Both manual workflows require the six reviewed inputs below. Before checkout, they validate these values and require the approved source SHA to equal GitHub's run SHA. Manual checks use the supplied values throughout the run; schedules use repository variables. All scheduled identity variables, including tag and public-manifest digest, must be configured before enabling.

| Manual input | Source |
| --- | --- |
| `repository` | Approved public GitHub owner/repository |
| `url` | Verified static HTTPS origin |
| `release_id` | Reviewed static `site/version.json` release ID |
| `release_tag` | Approved package version tag |
| `manifest_sha` | SHA-256 of the exact uploaded `public-manifest.json` bytes |
| `expected_sha` | Approved source commit recorded in the completed publication journal |

For an intentional notification drill, recheck that the approved tag still resolves to `expected_sha`. Dispatch `public-canary.yml` with that fixed tag as `ref`, all six unchanged reviewed inputs, a fresh 32-character lowercase hexadecimal `launch_id`, and `drill: failure`. Do not reuse the activation run's correlation ID. The deliberate failure makes no site requests and changes no website state. Observe and record delivery and acknowledgement at the operator's existing GitHub notification destination. For recovery, use the same fixed tag and reviewed identity with another fresh `launch_id` and `drill: none`; verify the actual release-check step succeeded. Do not infer that a recovery message was delivered from a green run.

Observe real successful scheduled runs for both workflows and run the independent static watcher below. Retain run IDs, timestamps, actual critical-step results, and the observed delivery/acknowledgement evidence. The activation command leaves scheduled coverage and notification delivery unverified until those separate checks occur. Fork Actions policy or schedule enablement can still need attention.

The legacy `local.monitor.activation_plan` helper and `local.monitor activation-plan` CLI are deprecated for activation. They return offline configuration metadata marked `executable: false`, with no enable/dispatch operations. Their compatibility `verified` argument does not verify publication. Use the bundle-aware command above for activation.

[Repository variables](https://docs.github.com/en/rest/actions/variables), [enable and dispatch workflow APIs](https://docs.github.com/en/rest/actions/workflows).

## Notifications and independent stale detection

Native GitHub Actions failure notifications are the only prepared notification mechanism. Actual delivery depends on the operator's GitHub subscription, Actions notification settings, and scheduled workflow actor. A red workflow is not evidence someone was notified or paged. There is no external email, chat message, or webhook sending code. Verify delivery after publication using the failure drill above. [GitHub notification settings](https://docs.github.com/en/subscriptions-and-notifications/get-started/configuring-notifications).

Run this read-only command from a separate local Mac or Linux process, with the exact public release identity:

```sh
python3 -m local.monitor watch \
  --repository OWNER/REPO \
  --base HTTPS_ORIGIN \
  --release REVIEWED_SHA256
```

It uses existing `gh` read access to GitHub.com. It makes at most four bounded GET API calls and independently probes the site from the local machine. It requires a public matching repository, active matching workflow, matching scheduled run identities on the default branch, and a completed successful `probe` job for that run. The job must contain the exact `Check the verified public release` step with status `completed` and conclusion `success`. An absent, skipped, failed, malformed, or unfinished critical step fails the check. Manual dispatches cannot refresh the schedule heartbeat. Missing runs, future or inconsistent timestamps, a latest run older than 15 minutes, a queued/running run older than five minutes, failed completed runs, skipped probes, and no success within 15 minutes all fail closed. A GitHub read failure still permits a separate site observation, so the result distinguishes a runner problem from a site problem. The entire CLI has a 100-second deadline; local operation requires POSIX Python and `gh`.

Exit 0 means both observations passed. Exit 1 means action is required; output is sanitized JSON. The watcher neither starts a scheduler nor sends notifications. After publication, run it every five minutes from an existing independently supervised local runner, with one process at a time and seven-day sanitized log rotation. Verify a deliberately stale workflow response and the supervisor's own failure behavior. A sleeping or offline Mac provides no monitoring coverage. Until a supervisor is installed and its delivery/acknowledgement path is observed, this is an executable independent check, not an active second monitor or an on-call service.

[Workflow runs API](https://docs.github.com/en/rest/actions/workflow-runs), [workflow jobs API](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run).

## Local failure-drill evidence

```sh
uv run --frozen pytest tests/test_monitor.py tests/test_static_canary.py -q
uv run --frozen ruff check local/monitor.py tests/test_monitor.py
python3 -m py_compile local/monitor.py
```

September 21 local execution: 52 monitor/static-canary tests passed. The HTTP drill runs an actual loopback server, returns 503 across three seven-request attempts, then restores valid content and observes successful independent checks. A sentinel response string remains absent from output. Separate injected GitHub fixtures exercise missing configuration, wrong identities, disabled schedules, missing or stale runs, hung/queued runs, failed and skipped jobs, missing/skipped/failed critical steps, future clocks, API timeout/denial/malformed output, an actual process deadline, and recovery. These are genuine no-cost local drills, not evidence of GitHub delivery or live deployment. No workflow was activated and no public HTTP endpoint was contacted by the drills.

The broader launch operations documents retain general browser-task and managed-service gates. These prepared workflows do not satisfy those gates or establish a 24/7 response commitment.


## Prepared nightly public setup and install journey

`.github/workflows/public-journey.yml` runs nightly at 10:17 UTC and on explicit dispatch after the same public-repository/enabled gate. It pins the existing checkout and setup-uv actions, uses uv 0.11.16 without cache, and uses Chrome already installed on the standard Ubuntu runner. Missing Chrome or uv fails closed. It has a 12-minute job bound and a ten-minute command/process bound. There is no provider key, model call, customer browser, deployment token, or managed service request.

`python3 -m local.public_journey` validates configuration and emits only a dry-run result. `--run` explicitly enables the public checks and additionally requires the configured repository to match `GITHUB_REPOSITORY`. The bundle-aware activation command above supplies the reviewed package tag and public-manifest digest along with the other pinned inputs. The manifest digest covers the exact uploaded `public-manifest.json` bytes; recomputing JSON or using the release approval digest gives a different identity.

The executable journey:

1. Constructs release download URLs from the exact repository and version tag. Downloads `public-manifest.json`, verifies its pinned digest, and verifies its product, upstream, repository, version, and site identity. It never reads latest-release links or accepts artifact URLs from the manifest.
2. Downloads that version's wheel and `requirements-mcp.txt`, verifying each against the manifest's package digest. Downloads have a ten-second socket timeout and fixed body limits: 4 MiB manifest/requirements, 64 MiB wheel. It accepts only GitHub's single redirect to the HTTPS `release-assets.githubusercontent.com` CDN. Other redirects and userinfo are rejected. Signed CDN URLs and all response bodies stay out of logs.
3. Checks the exact static release using the seven existing probes. Starts isolated headless Chrome profiles for `/start/`, `/privacy/`, and `/terms/`, captures the rendered DOM with `--dump-dom`, and verifies the expected headings. The setup page's install command, the element with `id="install-command"`, must contain the exact versioned wheel URL. The same URL elsewhere on the page, such as in the embedded agent prompt, does not count. Forms, inputs, internal account/launch links, and billing actions are rejected. Each page is bounded to 30 seconds. A completed DOM ends the isolated browser process group; a missing or incomplete document fails. Profiles have a temporary home and no user extensions or credentials.
4. Creates a fresh environment outside the source checkout using the runner's Python. Installs the pinned requirements with uv's `--require-hashes`, permits only binary wheels from PyPI, then installs the already hash-verified local wheel with `--no-deps --no-index`. Runs `uv pip check` and invokes the installed `jev_ultrafast.mcp_host --preflight` in Python isolated mode from outside the checkout. The result must identify the official MCP stdio preflight, success, browser not contacted, and zero provider calls. This verifies package setup and transport; it does not prove a model-driven browsing task or a named desktop integration.

The child environment is an allowlist with a temporary home and no inherited provider keys, Python path, proxies, package-index overrides, or user configuration. Child output is captured and suppressed; sanitized pass/failure codes are the retained result. The package-install step may contact public PyPI for the exact locked dependencies after activation. No paid API is used. References: [Chrome headless](https://developer.chrome.com/docs/automation-and-testing/headless), [uv install controls](https://docs.astral.sh/uv/reference/cli/#uv-pip-install).

Local evidence: the regression suite starts a real loopback HTTP server and verifies digest mismatch, body limits, bad HTTP, rejected redirects, and redacted output. Actual Chrome rendered all three pages from a fresh build of the current static site and verified the release wheel link. Command-contract fixtures verify hash-enforced install, no-deps wheel installation, isolated installed-host invocation, and credential exclusion. The fresh public-download-to-install chain remains a postpublication check because its versioned public assets do not exist yet. No public endpoint, schedule, notification destination, or package index was activated or contacted by these tests.

After publication, record one actual successful manual journey run and one nightly scheduled run, including the installed preflight result. Test the workflow's native failure delivery as part of activation; a configured workflow is not delivery evidence. The separate five-minute watcher currently supervises the static workflow only. Nightly journey silence requires separate operator review of its scheduled run history until an independently supervised journey stale check is configured.
