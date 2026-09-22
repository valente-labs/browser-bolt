# Browser Bolt

A local BYOK decision MCP for browser-capable hosts. Your host supplies observed page actions; Jev chooses an operation and target, and a paired Qwen model supplies field text when needed. Your host executes the action and checks the result. The MCP does not accept screenshots or operate the browser itself.

Start with [MCP installation and the tested dependency set](docs/MCP.md). The recommended route needs one OpenRouter key. No account or subscription is required. The package and commands retain their `jev-qwerebras` names for compatibility.

The native browser demo below additionally supports Cerebras Qwen decision fallback. MCP comparison profiles do not enable that fallback. This is an experimental developer preview; named desktop-host integrations remain unverified.

This fork derives from [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) at commit `452c1ad2dd628008f1d5608f28158d76e49e6cc0`. The original [MIT license](LICENSE) and Browser Use attribution are retained. Python imports remain `jev_ultrafast` for compatibility.

## Try the packaged host

The [reference host](docs/HOST.md) connects the actual MCP server to Browser Harness. It runs six packaged synthetic tasks, executes observed actions, and checks the final page independently. Start with a check that needs no key or browser:

```sh
uv sync --locked --extra mcp
uv run --frozen --extra mcp jev-qwerebras-host --preflight
```

After connecting a dedicated Chrome profile and supplying your provider key, run a fixture explicitly:

```sh
uv run --frozen --extra mcp jev-qwerebras-host --live --task choice --output choice-result.json
```

Live mode incurs provider usage. It only accepts packaged synthetic fixtures. The decision MCP can support broader workflows through your own browser-capable host and authorization rules. See the [installed-wheel live acceptance results](docs/LIVE_HOST.md) for the tested macOS setup and timing boundaries.

## Native browser demo

Requires Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Chrome connected through [Browser Harness](https://github.com/browser-use/browser-harness).

```sh
uv sync --locked
cp .env.example .env
# Set OPENROUTER_API_KEY and CEREBRAS_API_KEY in .env.
uv run jev-qwerebras
```

Open **http://127.0.0.1:8766**. Use a local scenario for the first run. If Chrome is not connected, run `uv run browser-harness --doctor` and follow its setup instructions. Browser Harness is included in the project dependencies. Browser tabs share the connected Chrome profile.

For an existing environment file outside the checkout:

```sh
python3 local/run_hybrid.py --env-file /path/to/provider.env
```

The launcher reads the selected file into its process environment without copying it. It also accepts `HYBRID_KEYS_FILE`, otherwise uses the checkout's `.env` when present. Existing process variables take precedence. See [HYBRID.md](HYBRID.md) for configuration details.

## Policy

| `QWEV_POLICY` | Decision path |
| --- | --- |
| `hybrid` (default) | Jev first, with at most one Qwen decision fallback. |
| `jev` | Jev decisions with a Qwen field-writing request for `TYPE_TEXT`; no decision fallback. |
| `qwen` | Qwen selects an action and supplies typing text in one request. |

Hybrid mode routes to Qwen when Jev is unavailable, returns `BLOCKED`, falls below the configured confidence threshold, or recent actions repeatedly make no progress. `QWEV_CONFIDENCE_THRESHOLD` defaults to `0.6`; this is an experimental routing cutoff, not a measured success probability. `QWEV_CEREBRAS_MODEL` configures the Qwen decision model, independently of the `TEXT_MODEL` field writer.

Fallback output must identify an observed action with a compatible operation. Browser freshness, target identity, and occlusion checks still apply. Model-supplied selectors and code do not execute. Browser mutations are never automatically retried. A `DONE` decision requires an independent task-specific outcome check; the inspector's status is not proof of success.

## Connections and failure behavior

A process-wide HTTP/2 client reuses provider connections. It allows eight connections and retains four keepalive connections for up to 120 seconds. Connect timeout is 2 seconds, read/write timeouts are 5 seconds, and pool timeout is 1 second. These are phase/inactivity limits, not a total wall-clock deadline. The client closes on process exit.

There is no provider retry or sleep loop. In hybrid mode a Jev provider failure can move to Qwen immediately; a timeout still costs its elapsed wait. Qwen failure or invalid fallback output stops without an action. Fallback depth is bounded, rather than guaranteeing a successful decision.

## Evidence and limitations

See [BENCHMARK.md](BENCHMARK.md) for this fork's measurements, tested layers, and limitations. Preserved upstream documentation and media describe upstream experiments, not performance achieved by this fork. The [upstream README](docs/UPSTREAM-README.md) is retained for attribution and historical context.

Goals, page text, editable values, and action history can be sent to providers and appear in local traces. Use synthetic inputs while evaluating. This prototype has no credential broker, secret-reference boundary, or general redaction layer. See [SECURITY.md](SECURITY.md) for the data boundary and reporting guidance. Frames, shadow roots, canvas, and arbitrary keyboard widgets remain outside the DOM reader's supported surface.

## Development

```sh
uv sync --locked --extra mcp
uv run --frozen --extra mcp pytest
uv run --frozen --extra mcp ruff check .
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
node --check local/browser_driver.js
uv build
```

The test suite uses provider doubles and does not call paid APIs. Live probes under `local/` do call providers; their output belongs in ignored `artifacts/`. CI runs the offline suite, lint, JavaScript syntax checks, and package build. No static Python type checker is configured.

## Compare models

The packaged benchmark supports Cerebras Qwen, GPT-6 Astra, Claude Opus 5, each paired with Jev, and a true Jev-only profile. Paired comparison profiles use their partner for field text and do not enable decision fallback. The inspector retains its default Jev + Qwen hybrid policy.

Preview a balanced schedule without credentials or browser access:

```sh
uv run jev-qwerebras-benchmark --dry-run --runs 3
```

Run the synthetic suite explicitly after connecting Browser Harness and configuring provider keys:

```sh
uv run jev-qwerebras-benchmark --env-file /path/to/provider.env --runs 3 --output artifacts/comparison.json
```

This command makes paid API calls. Results separate browser setup from task time, verify visible outcomes independently, and retain costs from failed attempts. Use `--profiles jev --tasks choice` to measure Jev alone without a text writer. See the [measured pilot and comparison limits](docs/COMPARISON.md). The initial pilot used the Codex Chrome adapter. Subsequent native Browser Harness checks use a dedicated local Chrome profile; see the sprint report for the separate evidence.

## MCP and product direction

The optional MCP package exposes a fast decision service to an existing browser-capable host. It returns an observed action choice and, when needed, field text; the host executes and verifies the browser action. The standalone Agent and benchmark remain available for native Browser Harness execution.

See [MCP setup](docs/MCP.md) and the [five-sprint evidence](docs/SPRINTS.md). OpenRouter-only profiles `qwen_openrouter` and `jev_qwen_openrouter` need only `OPENROUTER_API_KEY`. Direct `qwen` and `jev_qwen` retain Cerebras and its separately measured performance. Do not treat provider routes as interchangeable speed claims.

The new `checkout`, `onboarding`, and `recovery` tasks are original synthetic fixtures based on common product interactions. They complement the initial three tasks; they are not an official external benchmark. Production managed accounts, payment collection, credits, and hosted inference remain planned work. A separate repository-only website preview exercises synthetic signup, account display and a test-only billing contract; it is not a managed service.

Verification includes offline failure and security tests, a clean installed-wheel MCP smoke, four verified live checkout runs through the packaged MCP host, and a separate 48-attempt native browser cohort with 47 verified successes. See [live host acceptance](docs/LIVE_HOST.md) and [native results](docs/NATIVE_COMPARISON.md) for their different timing boundaries and limits. This is a BYOK developer preview; managed subscriptions remain planned.

## Release integrity

Release downloads contain a wheel, source archive, hash-locked runtime requirements, a CycloneDX inventory, SHA256SUMS, and a public manifest. Verify checksums before installing. The [release procedure](docs/RELEASE.md) binds the reviewed source, downloads, website and destination to one approval digest. It stops on drift or an uncertain remote creation rather than assuming publication succeeded.

The public package excludes the internal launch board and managed billing prototype. Source builds and published release assets have different installation paths; follow [MCP installation](docs/MCP.md) for the path you chose.
