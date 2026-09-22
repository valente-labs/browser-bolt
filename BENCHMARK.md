# Local benchmark, September 17, 2026

These measurements cover three synthetic Chrome workflows and actual Jev/OpenRouter and Qwen/Cerebras requests. They are a smoke test, not a general browser benchmark. No Astra comparison was part of this September 17 cohort. Later Astra/Opus and native results are linked below.

## Completion results

Three trials per task and policy, 36 runs total. Policy order changed between trials. Provider connections were reused; there was no excluded browser warmup. Timing starts after navigation to the local fixture and ends after an independent DOM outcome check. It includes decisions, generated text, local request handling, extension/browser operations and verification. It excludes initial navigation, Chrome startup and credentials setup.

| Task | Jev + Qwen writing, no fallback | Hybrid policy | Qwen alone | Hybrid with injected Jev outage |
| --- | ---: | ---: | ---: | ---: |
| Save a generated note | 2.506 s, 2/3 | 1.702 s, 3/3 | 1.801 s, 3/3 | 1.537 s, 3/3 |
| Generate, review and confirm a note | 2.882 s, 3/3 | 2.136 s, 3/3 | 2.280 s, 3/3 | 2.159 s, 3/3 |
| Select and save a radio preference | 1.258 s, 3/3 | 1.276 s, 3/3 | 1.379 s, 3/3 | 1.371 s, 3/3 |

Times are median attempt durations, including failed attempts. Success requires both the model's DONE decision and the fixture-specific outcome. The note check preserves the requested title's case, checks the saved heading and looks for tram and pastry/pastries. It does not establish general prose quality or full semantic compliance. The failed Jev-only note run saved `Lisbon Weekend` rather than the requested `Lisbon weekend`. It remains a failure in these results.

Hybrid completed 9/9, Qwen-only 9/9, injected-outage 9/9 and Jev-only 8/9. Three repeats per cell cannot establish production reliability or a useful tail-latency percentile. Longest observed hybrid attempt was 3.396 s; longest Jev-only attempt was 5.108 s.

No normal hybrid trial triggered fallback. Its smaller medians than Jev-only are sampling/provider/browser variation, not evidence that the fallback implementation accelerated successful Jev decisions. The outage case raises a local provider error immediately on every Jev decision; it verifies takeover, not the latency of a real timeout. Actual provider timeouts add their elapsed wait. Offline tests cover failure statuses and timeout handling.

## Request count and cost

| Task | Hybrid calls | Qwen-only calls | Hybrid mean $/attempt | Qwen-only mean $/attempt |
| --- | ---: | ---: | ---: | ---: |
| Note | 4 Jev + 2 Qwen | 4 Qwen | $0.001017 | $0.002505 |
| Review | 5 Jev + 2 Qwen | 5 Qwen | $0.001098 | $0.003207 |
| Radio choice | 3 Jev | 3 Qwen | $0.000138 | $0.001663 |

Jev cost is provider-reported. Qwen cost is estimated from actual input/output usage at $0.99/$1.49 per million tokens. Costs include all calls in each final attempt, but exclude local compute and separate development probes. Recheck pricing before extrapolating. Qwen uses reasoning_effort=none and strict JSON Schema output; Jev uses typesafe/jev-1.13 through the native OpenRouter Decisions endpoint. Calls in development that were rejected still cost money and were not included in this final-candidate table.

## Connection reuse

A separate fixed-action experiment alternated a new HTTP client per call with a shared pool. Five measured requests per model/mode, after one warmup each. Actual httpcore connection events and response protocols were recorded, not inferred from latency.

| Provider | Fresh client median | Pooled median | New TCP connections, fresh / pooled |
| --- | ---: | ---: | ---: |
| Jev through OpenRouter | 265 ms | 197 ms | 5 / 0 |
| Qwen through Cerebras | 265 ms | 203 ms | 5 / 0 |

All 20 measured requests passed the fixed-action check and used HTTP/2. These medians suggest about 60–70 ms saved per small request under these conditions. Upstream already reused a client; this does not claim a new architectural invention or a universal speedup. The fork adds explicit pool limits, longer keepalive and bounded failure behavior.

## Tested layers and reproduction

The completed browser runs used the approved Chrome extension, local/browser_driver.js and local/browser_probe.py. They exercise the production model/policy code and real DOM interactions, but bypass Agent's native Browser Harness transport, DOM snapshot and executor. These September 17 measurements do not verify that native path; subsequent native and installed-host evidence is linked below. Do not present these measurements as a native Browser Harness end-to-end result.

The original adapter and connection probes are retained as historical development evidence, not shipped as supported public commands. Reproduce a new native comparison with the packaged benchmark runner described in [NATIVE_COMPARISON.md](docs/NATIVE_COMPARISON.md), or test the installed reference host using [HOST.md](docs/HOST.md).

Preview a current synthetic experiment without browser or provider access:

```sh
uv sync --locked
uv run jev-qwerebras-benchmark --profiles jev_qwen_openrouter,astra,opus --tasks checkout --runs 3 --dry-run
```

Removing `--dry-run` makes paid provider requests and requires the documented dedicated browser setup. The native runner uses different tasks and timing boundaries from this historical extension cohort; label its results separately. Failed attempts remain in the output.

[Per-run synthetic browser evidence](docs/benchmarks/browser-2026-09-17.json) retains final DOM observations, decisions, usage, timings and source hashes; repeated request prompts are omitted. [Connection evidence](docs/benchmarks/connections-2026-09-17.json) retains warmup and measured calls.

## Development failures retained locally

The initial Qwen pilot added an extra `type` key under legacy JSON-object mode. Strict provider schema fixed that shape failure while local validation remained in place. A later DONE response carried empty text instead of null; non-typing operations now canonicalize exactly the empty string, while nonempty text and incompatible targets still fail closed. A fixture verifier initially rejected the singular word pastry; the rule was corrected for every run, and original verdicts are retained. The case-sensitive title failure in the final run was not relaxed or discarded.

Real websites, long sessions, rate-limit recovery, frames, shadow roots, uploads, arbitrary keyboard controls and malicious-page robustness are not established by these tests. A local benchmark success does not authorize a consequential action or prove an arbitrary task complete.

After timing, independent review found an oversized-number validation edge case in malformed Jev responses. The final code checks numeric bounds before conversion, preserving fallback and usage accounting. Four added offline regressions pass; the public evidence retains the hashes of the code actually timed. This defensive change was verified offline, not claimed as a new live timing run.

## Astra and Opus comparison

The [September 18 six-configuration pilot](docs/COMPARISON.md) adds solo Astra and Claude Opus 5 and each paired with Jev. It reports all 36 attempts, including the strict-response failure, and distinguishes true Jev-only selection from Jev with a field writer. The pilot uses a Chrome adapter and does not establish native Browser Harness end-to-end performance.

## Native multi-step cohort

The [native Browser Harness comparison](docs/NATIVE_COMPARISON.md) adds original checkout, onboarding, and validation-recovery flows, including explicit one-key OpenRouter Qwen routes. Its 48 attempts are separate from the earlier Chrome-adapter measurements. All failures and their costs are retained.
