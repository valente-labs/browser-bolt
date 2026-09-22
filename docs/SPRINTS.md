# Five-sprint release preparation

Scope: a local MCP browser tool with open-source bring-your-own-key installation, reproducible model comparisons, and a separate managed subscription launch plan. Publication requires Rich's signoff. Hosted billing and credential custody are not implemented by this work.

| Sprint | Deliverable | Acceptance evidence | Status |
| --- | --- | --- | --- |
| 1 | Product boundary, runtime audit, realistic flow references | Documented risks, selected original fixture patterns, baseline transport diagnostics | Complete: audit found three concrete gaps; native baseline now passes |
| 2 | MCP tool packaging and expanded synthetic flows | Official SDK protocol smoke, fixture validators, offline tests | Complete: six packaged fixtures, nine profiles, 25 MCP tests, SDK stdio and installed-wheel smoke pass |
| 3 | Native Browser Harness validation and reliability fixes | Real isolated Chrome runs, recorded failures and root causes | Complete: isolated native baseline and 12 flow smokes pass; bounded runtime/cleanup integrated |
| 4 | Distribution and benchmark reporting | Clean wheel installation, repeatable matrix, complete cost accounting | Complete: clean installed-wheel checks, 48 native attempts, per-attempt costs and failure retained |
| 5 | Independent release review and launch plan | Findings resolved, rerun checks, explicit remaining beta/paid-launch gates | Complete: independent review findings resolved; launch plan separates preview and paid gates |

Mobbin references are inspiration for original local interaction fixtures; no screenshot, branding, customer data, or code is redistributed. These are not official Mobbin or MiniWoB benchmark scores.

- [Checkout reference](https://mobbin.com/flows/0a9a45a0-7b9c-4e1c-b838-faa75bd1e3ed). Inspected cart summary, sequential checkout form, and payment choice screens.
- [Onboarding reference](https://mobbin.com/flows/6d86217f-51e7-4277-a871-3015005b118f). Inspected name entry, age entry, interest selection, and completed feed.

## Refinements and root causes

- Native startup initially failed because the long workspace-derived `BH_HOME` exceeded macOS Unix-domain socket path limits. A short dedicated `/tmp` runtime directory connected the same isolated Chrome successfully. No API calls occurred in the failed setup attempts.
- The first audit found that setup failures could leak owned tabs, stale ticks could run without an overall bound, and a partially unknown attempt could lose its known-cost subtotal. Lifecycle guards, cooperative stop checks/tick limits, and corrected subtotal aggregation now have regression coverage.
- The watcher found native DOM records and strict MCP inputs had different shapes. Explicit normalization helpers and a representative native-state/field roundtrip test fixed the integration boundary.
- The independent review found no implementation blocker for the restricted local BYOK preview. Its documentation-alignment findings are handled in this report and the launch plan.
- A natural model failure in the final cohort selected DONE without a verified final state. This is retained as a failed attempt, not patched away or relabeled successful.

This is release preparation for a local decision-service preview. It is not evidence of a production credential manager, general autonomous browser safety, a multi-tenant service, or working subscription billing.

## Final evidence

- Integrated offline suite: 278 passed with the optional MCP SDK enabled.
- Ruff, JavaScript syntax checks for the driver, inspector, snapshot, and six fixture scripts passed. Wheel and source distribution build passed.
- Clean wheel installed into a fresh environment and tested from `/tmp`: packaged fixtures/assets, benchmark dry run, real SDK stdio initialization, tool listing, and list_profiles passed without keys. CI now repeats the installed-wheel check.
- Native cohort: 48 attempts, 47 independently verified successes. Seven configurations passed 6/6; solo OpenRouter Qwen passed 5/6. Detailed [native comparison](NATIVE_COMPARISON.md) and [raw evidence](benchmarks/native-sprints-2026-09-18.json).
- A separate real stdio MCP + host-owned native Agent checkout completed in 2.72 seconds, with 10 model calls costing $0.000751162 as reported by OpenRouter. This is an integration smoke, not a cohort average.
- No commits, pushes, account creation, billing setup, public deployment, or publication were performed. GitHub release still requires Rich's signoff.

Independent review found no implementation blocker for the restricted local BYOK decision-service preview. The result is suitable for preview evaluation on approved non-sensitive observations. Broader production and paid-release gates include host support validation, credential boundaries, tenant isolation, billing/reconciliation, and sustained reliability. See the [current security boundary](../SECURITY.md) and [tested host support](LIVE_HOST.md).
