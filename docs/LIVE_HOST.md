# Installed MCP host acceptance

On September 21, 2026, the packaged reference host completed the synthetic checkout fixture with four provider routes. Each run used the installed wheel outside the source checkout, the official MCP SDK over stdio, Browser Harness, and a dedicated Chrome profile. Provider responses were live. The final fixture DOM was verified independently of the model's DONE response. No purchase occurred.

| Route | Verified | Native run | Fresh CLI process | Model calls | API cost |
| --- | --- | ---: | ---: | ---: | ---: |
| Jev + Qwen, OpenRouter | Yes | 3.43 s | 8.24 s | 10 | $0.000680 |
| Jev + Qwen, direct Cerebras writer | Yes | 1.79 s | 3.14 s | 10 | $0.001483* |
| GPT-6 Astra, OpenRouter | Yes | 15.53 s | 16.67 s | 8 | $0.059010 |
| Claude Opus 5, OpenRouter | Yes | 22.50 s | 23.80 s | 8 | $0.051900 |

*The direct Cerebras total includes an estimate based on reported token usage and the configured rate. Other totals use reported OpenRouter costs. These are API costs for these runs, not a Browser Bolt subscription price. Unknown costs are not treated as zero.

This is one sequential run per route, in the order shown, with screen capture active. It is release acceptance evidence, not a balanced performance benchmark or a reliability estimate. Native run time includes browser setup but excludes CLI/MCP initialization and teardown. The first CLI process had a larger startup overhead. Every route started a fresh host process; connections were reused within that process. A long-lived host avoids some startup work, but these results do not measure a production host's overall latency.

The historical **5× Astra / 7× Opus** headline comes from the separate [native cohort](NATIVE_COMPARISON.md), with six attempts per selected profile. Do not combine the cohorts or use this one-run result to promise speed on arbitrary websites.

## Reproduce

Install the reviewed wheel and hashed dependency set following [MCP setup](MCP.md). Connect Browser Harness to a dedicated synthetic Chrome profile. Supply the appropriate provider keys through the process environment, then run:

```sh
jev-qwerebras-host --preflight
jev-qwerebras-host --live --profile jev_qwen_openrouter --task checkout --max-steps 10 --timeout 90 --output checkout-openrouter.json
```

Repeat with `jev_qwen`, `astra`, or `opus` and a different output filename. Live runs incur provider charges. The direct Cerebras pair requires both keys; the other routes require one OpenRouter key. The [host guide](HOST.md) describes cancellation, scope restrictions and cleanup.

The tested environment was Python 3.12.13 on macOS with a hash-enforced runtime installation and a wheel installed using `--no-deps`. [Raw synthetic accounting and timings](benchmarks/installed-host-checkout-2026-09-21.json) retain source hashes and model-call costs. The paired routes used Jev for decisions and Qwen for field text, without decision fallback.

## Support boundary

| Surface | Evidence |
| --- | --- |
| Packaged reference host, macOS, synthetic checkout | Four verified live runs above |
| Official Python MCP stdio client | Initialization, tool discovery, decisions and writing exercised |
| Scope rejection, false DONE, cancellation ordering, no mutation retry | Offline regression tests with controlled failure conditions |
| Named desktop clients such as Codex, Claude Desktop, Cursor or VS Code | Not covered by this acceptance run |
| Arbitrary websites, credential entry, payments, screenshots, canvas apps | Not established by the synthetic fixture results |

The reference host is an executable example of the complete MCP/browser loop. Adapting the decision tools to another browser-capable host still requires that host's observation, authorization, execution and verification checks.
