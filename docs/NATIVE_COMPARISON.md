# Native browser comparison, September 18, 2026

This cohort uses the packaged Agent and native Browser Harness with an isolated local Chrome profile. It is separate from the earlier Codex Chrome-adapter pilot. The 48 attempts cover checkout review, profile onboarding, and discount-validation recovery. They are original synthetic flows, not an official Mobbin or MiniWoB benchmark.

Each configuration ran each task twice. All attempts, including failures, remain in timing and cost averages.

| Configuration | Verified | Mean task time | Median task time | Mean API cost/attempt | API cost/verified success |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cerebras Qwen | 6/6 | 3.47s | 3.08s | $0.005923 | $0.005923 |
| Jev + Cerebras Qwen | 6/6 | 1.97s | 2.25s | $0.001239 | $0.001239 |
| Astra | 6/6 | 15.67s | 16.46s | $0.050060 | $0.050060 |
| Jev + Astra | 6/6 | 4.97s | 5.17s | $0.006990 | $0.006990 |
| Opus 5 | 6/6 | 18.80s | 20.98s | $0.044327 | $0.044327 |
| Jev + Opus 5 | 6/6 | 5.48s | 6.17s | $0.006093 | $0.006093 |
| OpenRouter Qwen | 5/6 | 10.77s | 9.30s | $0.001367 | $0.001640 |
| Jev + OpenRouter Qwen | 6/6 | 2.69s | 2.83s | $0.000640 | $0.000640 |

| Baseline | Paired configuration | Speedup by mean time | Less time | Lower mean API cost |
| --- | --- | ---: | ---: | ---: |
| Astra | Jev + Cerebras Qwen | 7.96x | 87.4% | 97.5% |
| Opus 5 | Jev + Cerebras Qwen | 9.56x | 89.5% | 97.2% |
| Astra | Jev + OpenRouter Qwen | 5.81x | 82.8% | 98.7% |
| Opus 5 | Jev + OpenRouter Qwen | 6.98x | 85.7% | 98.6% |
| Astra | Jev + Astra | 3.15x | 68.3% | 86.0% |
| Opus 5 | Jev + Opus 5 | 3.43x | 70.9% | 86.3% |
| Cerebras Qwen | Jev + Cerebras Qwen | 1.76x | 43.3% | 79.1% |
| OpenRouter Qwen | Jev + OpenRouter Qwen | 4.00x | 75.0% | 53.2% |

The Qwen-through-OpenRouter failure reached a model DONE response but failed the independent final-state check on discount recovery. That is a task failure despite valid model response schemas. The code does not retry or hide it.

Jev alone cannot generate field values in this implementation. It passed the separate choice-only native smoke, but is not presented as capable of these writing workflows. Known values can eventually be handled by a local authorized resolver, rather than an LLM; that credential boundary is not implemented here.

The paired profiles use Jev for decisions and the named partner for field text. They do not invoke decision fallback. These results therefore do not measure the production hybrid fallback policy on difficult decisions.

OpenRouter charges use reported usage.cost; direct Cerebras is estimated at $0.99/M prompt and $1.49/M completion tokens. No browser hosting, service overhead, support, or payment costs are included. All costs were known for this cohort. The complete cohort cost approximately $0.70.

The measured runtime used a shared HTTP/2 pool, no excluded warmup, 30-second provider phase timeouts, Astra/Opus low reasoning, and Qwen reasoning disabled. Browser setup and cleanup are separately recorded; task time includes execution and independent verification. Code, fixture, dependency, and model configuration fingerprints are retained in [per-attempt evidence](benchmarks/native-sprints-2026-09-18.json).

This is a small controlled sample. It does not establish equivalent complex-task capability, production reliability, p95 service latency, or real-world subscription economics. The one-key OpenRouter route and direct Cerebras route are distinct products of model plus provider configuration.

Flow references and implementation steps are recorded in [the five-sprint report](SPRINTS.md). The host-executed MCP boundary is documented in [MCP setup](MCP.md).

## Real MCP transport checks

The one-key `jev_qwen_openrouter` profile completed checkout through actual stdio MCP messages and the host-owned native browser executor in 2.72 seconds, costing $0.000751162 across 10 model calls. True Jev-only completed the separate choice task through the same MCP boundary in 1.15 seconds, costing $0.000138978 across three model calls. These are individual integration checks, excluded from the 48-run comparison. [Checkout evidence](benchmarks/mcp-native-smoke-2026-09-18.json), [Jev-only choice evidence](benchmarks/mcp-jev-choice-2026-09-18.json).
