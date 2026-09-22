# One approved launch command

Prepare and review the exact candidate using [RELEASE.md](RELEASE.md), including its public monitoring workflows and activation command. The wrapper requires an existing candidate. It never rebuilds or changes artifacts after approval.

Preview the one-command sequence without network access:

```sh
python3 -m local.launch --bundle dist/release-001
```

After Rich approves that exact candidate digest and its prepared monitoring, run one invocation:

```sh
python3 -m local.launch --bundle dist/release-001 --execute --approve EXACT_APPROVED_DIGEST
```

The wrapper passes that same digest first to `release.publish`, then to monitoring activation. Publication must confirm the exact approved candidate, the bundle must still verify, and its journal must record completed publication and a verified site before activation begins. If publication fails or artifacts change, activation does not run. No second approval is requested for these already approved stages.

The existing publisher and activation code own their locks, exact candidate copies, journals, retry safeguards, and remote operations. The wrapper adds no remote write operations and never resets journal state. If activation stops after successful publication, preserve the candidate and journal and retry the same approved command. Unknown request outcomes retain their existing fail-closed behavior; the wrapper does not invent new correlation ids or clear pending operation markers.

Successful publication and dispatch are distinct from successful monitoring jobs. The response reports the publication, monitoring configuration, and actual manual job status returned by activation. It does not wait for jobs or claim scheduled coverage or notification delivery. Those remain separate observations described in [MONITOR_ACTIVATION.md](MONITOR_ACTIVATION.md) and [MONITORING.md](MONITORING.md).

Read local journal status without network access:

```sh
python3 -m local.launch status --bundle dist/release-001
```

Observe actual correlated manual jobs with read-only remote checks:

```sh
python3 -m local.launch check --bundle dist/release-001
```

Neither status nor check permits `--execute`. Offline status labels its publication and configuration fields as journal evidence; it cannot establish fresh remote job success. The check delegates to the existing monitoring verifier and performs no publication, configuration, dispatch, or journal writes. The wrapper does not poll or sleep while waiting for workflow completion.

Direct file invocation (`python3 local/launch.py`) is also supported. The command uses existing authentication and dependencies through the underlying modules; it does not obtain credentials, contact providers, purchase services, or send notifications itself.
