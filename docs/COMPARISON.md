# Small browser comparison, September 18, 2026

Jev reduced the number of calls to Astra and Opus in this pilot, which reduced completion time and cost. Jev plus Qwen cost less than Qwen alone, but did not show a consistent speed advantage on the writing tasks.

These are 36 local Chrome runs: two attempts at each of three synthetic tasks for each of six configurations. They are a pilot, not a standard browser benchmark or a general model ranking.

| Configuration | Verified success | Choice, seconds | Note, seconds | Review, seconds | Mean API cost per attempt |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cerebras Qwen alone | 5/6 | 1.721 | 1.759 | 2.259 | $0.002460 |
| Jev + Cerebras Qwen | 6/6 | 1.161 | 1.947 | 2.267 | $0.000754 |
| Astra alone | 6/6 | 7.057 | 9.084 | 10.588 | $0.023043 |
| Jev + Astra | 6/6 | 1.701 | 5.104 | 5.104 | $0.004602 |
| Claude Opus 5 alone | 6/6 | 8.495 | 11.275 | 14.239 | $0.021828 |
| Jev + Claude Opus 5 | 6/6 | 1.453 | 6.824 | 6.354 | $0.003349 |

Times are medians of two attempts, including failures. Cost averages weight the three tasks equally and include the failed attempt. Qwen's failed run reached the correct visible delivery preference but returned the string `"null"` for its final non-typing payload. The strict response validator rejected it; this is retained as failure, with its API cost.

The three Jev-paired choice rows are all **Jev alone**: the field writer is never called. Their timing differences are run variation, not evidence about the named partner. Together those six Jev-only runs succeeded 6/6. Jev is a decision model selecting from supplied alternatives. In this agent, writing a new sentence requires another model. Known values, such as a locally retrieved credential, can instead be inserted by deterministic application code after selection and authorization; generating them with an LLM is unnecessary.

For the writing tasks, each pair uses Jev to select the operation and target, then its named partner to supply the selected field's text. There is no decision fallback in these comparison arms. Solo models return the operation, target, and any text together in one call. This compares complete configurations, including their different request counts and prompts.

## Interpretation

On the note task, Jev reduced Astra time by 44% and Opus time by 39%. On the review task, the reductions were 52% and 55%. The mean API cost per attempt fell by about 80% for Astra, 85% for Opus, and 69% for Qwen when paired with Jev across this task mix.

Qwen alone was slightly faster than Jev + Qwen on both writing tasks. Its fast generation can outweigh the extra serial Jev request. The paired architecture becomes more attractive when many actions require no text generation, or when an expensive writer is needed only occasionally.

Six successes do not establish production reliability, and the single Qwen validation failure does not establish a general quality disadvantage. Longer tasks, ambiguous choices, recovery, dynamic pages, and adversarial page content remain unmeasured here. These tasks also do not establish which frontier model is best at difficult planning.

## Conditions and evidence

- Models: `typesafe/jev-1.13`, `qwen-3.8-27b`, `openai/gpt-6-astra`, `anthropic/claude-opus-5`.
- Qwen used Cerebras directly with reasoning disabled; Astra and Opus used OpenRouter with low reasoning. OpenRouter provider routing was automatic.
- Shared HTTP/2 connection pooling; 30-second HTTPX phase timeout; no request retries. First requests are included, with no separate excluded warmup.
- Identical observed controls, goals, per-step browser execution, ten-step limit, and visible outcome checks. Initial navigation is excluded; subsequent browser actions and final verification are included.
- OpenRouter charges are reported `usage.cost`. Cerebras charges are estimates at $0.99/M input tokens and $1.49/M output tokens. These exclude hosting, browser infrastructure, subscription fees, and support.
- Browser control used Codex's Chrome adapter. This did not validate the package's native Browser Harness transport.
- [Per-run evidence](benchmarks/comparison-2026-09-18.json) includes all 36 attempts, call counts, usage, and visible final outcomes. Solo Astra/Opus raw provider bodies were not retained, but their response usage survives in the traces.
- After measurement, adapter hardening fixed credential selection when keys are missing and accounting when a field writer fails. Both dedicated keys were present and no field writer failed in this cohort, so those fixes do not alter these results.

## Product direction

Jev + Cerebras Qwen is a promising low-cost default for short structured browser tasks. A frontier model could help with planning and recovery, but this pilot only used it for writing and did not measure that benefit.

A subscription would need a service around the models: isolated browser sessions, credential boundaries, task budgets, cancellations, audit trails, concurrency limits, and reliable recovery. The API costs above are only one part of cost per successful task. Before pricing a service, measure a representative workload and include browser hosting, retries, verification, and support. Credential access should remain under an explicit local authorization boundary for a Keyring integration.

GitHub publication remains subject to Rich's signoff. No subscription service has been created or deployed.

## Relative speed and cost

Using average time and cost across the equally weighted three-task mix, including failed attempts:

| Baseline | Configuration | Speedup | Less time | Lower API cost |
| --- | --- | ---: | ---: | ---: |
| Astra alone | Qwen alone | 4.7x | 79% | 89% |
| Astra alone | Jev + Qwen | 5.0x | 80% | 97% |
| Astra alone | Jev + Astra | 2.2x | 55% | 80% |
| Opus 5 alone | Qwen alone | 5.9x | 83% | 89% |
| Opus 5 alone | Jev + Qwen | 6.3x | 84% | 97% |
| Opus 5 alone | Jev + Opus 5 | 2.3x | 57% | 85% |

Speedup is baseline mean duration divided by candidate mean duration. A 5x speedup means 80% less time. These ratios describe this simple task mix and do not establish equal capability on difficult workflows.

## Packaged integration check

After the pilot, the temporary adapter was replaced with explicit packaged profiles, dedicated credential routing, strict writer schemas, and Agent function injection. A separate live Chrome smoke check passed the note task for all six solo/paired configurations and the delivery task for pure Jev. Pure Jev stopped as expected when note writing required a field writer. These eight checks are [recorded separately](benchmarks/production-smoke-2026-09-18.json); they are not added to the pilot table.

The packaged benchmark now verifies saved title and note body separately. The retained pilot DOM snapshots were audited with the same field separation, with no outcome changes. The native Browser Harness path remains unmeasured. CLI scheduling, accounting, resume behavior, fixture serving, and provider contracts have offline coverage.
