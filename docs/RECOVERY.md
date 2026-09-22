# Browser Bolt recovery

The first release has no earlier deployment to restore. Recovery pauses the approved Worker's `workers.dev` address and preview URLs while retaining its code, assets and deployment. GitHub source and release downloads remain public. The command does not change routes, custom domains, DNS or account subscriptions.

**Status: local rehearsal only.** Offline tests cover approval checks and failure handling. The production pause/resume switch still needs a live rehearsal after publication receives final signoff. Do not describe this as a tested production rollback until that evidence exists.

Run from the reviewed checkout. Start with a local plan, which needs no credentials and makes no network requests:

```sh
python3 local/recovery.py pause --bundle dist/release-candidate
```

Execution requires both `--execute` and the exact reviewed manifest SHA-256. The adjacent `release-candidate.publication.json` must already identify the same candidate and its confirmed deployment. Use the digest approved for that bundle; a source or artifact change requires a newly reviewed candidate.

```sh
python3 local/recovery.py pause --bundle dist/release-candidate \
  --execute --approve <approved-manifest-sha256>
```

Supply `CLOUDFLARE_API_TOKEN` through the process environment using the normal local credential flow. Never put it in the command, configuration file or journal. Pause reads the account subdomain and current deployment, verifies its version tag against the approval digest, then sets `enabled=false` and `previews_enabled=false`. A second read confirms those flags and the same deployment before it reports `paused`. This confirms the provider setting; it does not prove propagation to every edge or removal from browser caches.

The operation uses Cloudflare's [Worker Script Subdomain API](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/subresources/subdomain/). It sends one POST to the approved script's subdomain endpoint when its flags differ. A timeout is reconciled with GET requests. Repeating pause sets the same state and never toggles exposure back on.

To resume the exact deployment after investigating the failure:

```sh
python3 local/recovery.py resume --bundle dist/release-candidate
python3 local/recovery.py resume --bundle dist/release-candidate \
  --execute --approve <approved-manifest-sha256>
```

Resume requires an earlier recovery attempt in the same journal. It verifies the deployment again, enables its `workers.dev` address with preview URLs disabled, then checks every published static file hash and runs the static canary. It reports `restored` only when those checks pass and the deployment and flags still match. Resume creates no deployment and uploads no files.

A failed command exits nonzero. If resume enables exposure but its public checks fail, the journal records `enabled_unverified`; run the approved pause command to disable that exposure. An uncertain state is recorded as `unknown` and remains a failure until a later command confirms it. The publisher refuses to continue while recovery is paused, changing, unknown or unverified. Retrying publication cannot silently undo a pause.

Publication and recovery share `release-candidate.publication.lock`. If it exists, inspect the active process before treating it as stale. Recovery appends timestamped attempts to the publication journal, preserving earlier publication identifiers and evidence. Keep that journal and the exact bundle together.

For the live rehearsal after final publication signoff, record the published URL and deployment ID, execute pause, confirm the flags and check the public URL from a fresh client, then resume and retain the successful hash and canary evidence. Record elapsed recovery time and any propagation delay. Until then, the offline tests establish local behavior only.
