# Browser Bolt MCP

A local, open-source BYOK MCP server that helps a browser-capable host choose its next action. The host supplies an observed DOM action table. Jev selects an operation and target; a paired chat model can supply field text. The server returns a decision, usage, and latency. Your MCP host performs the browser action and verifies the outcome.

This is decision assistance, not a browser driver or an autonomous browser service. It does not open tabs, fetch credentials, accept screenshots, run selectors or JavaScript, or execute mutations. Managed accounts, subscription billing, and hosted execution are not implemented.

## Install a release or source checkout

For a release, download its wheel, `requirements-mcp.txt` and `SHA256SUMS` from the same versioned GitHub release. Place them in one directory and verify the downloaded wheel and requirements against the published checksums. The website setup guide links the exact reviewed release files. Release artifacts do not require a PyPI package.

For a source checkout, use the commands below to build a wheel. Then use the hash-enforced installation section, adjusting the wheel path to the file you downloaded or built.

Python 3.12+ and uv are required. The MCP SDK is optional:

```sh
uv sync --locked --extra mcp
uv run --extra mcp jev-qwerebras-mcp
```

The process waits for MCP messages on stdin. Stdout carries protocol traffic only. There is no web port or inspector page. The existing browser demo and benchmark entrypoints remain available separately.

Build a local wheel and launch that wheel with uvx:

```sh
uv build
uvx --from './dist/jev_qwerebras_ultrafast-0.1.0-py3-none-any.whl[mcp]' jev-qwerebras-mcp
```

The wheel filename follows the current package version. For a downloaded wheel, replace the `./dist/` path with its actual location.

### Reproduce the tested dependency set

The standalone `uvx --from` command resolves the wheel's compatible dependency ranges anew. It does not consume this checkout's `uv.lock`. To retain the tested versions and validate downloaded dependency hashes, use the release's `requirements-mcp.txt` alongside its wheel:

```sh
uv venv --python 3.12 .browser-bolt-venv
uv pip install --python .browser-bolt-venv/bin/python --require-hashes -r requirements-mcp.txt
uv pip install --python .browser-bolt-venv/bin/python --no-deps ./dist/jev_qwerebras_ultrafast-0.1.0-py3-none-any.whl
.browser-bolt-venv/bin/jev-qwerebras-mcp
```

Verify the wheel against the reviewed release's SHA256SUMS before installing. Configure your host's `command` to the absolute path of `.browser-bolt-venv/bin/jev-qwerebras-mcp`, with an empty `args` array. The dependency file contains transitive runtime versions and distribution hashes; `sbom-mcp.cdx.json` inventories the locked dependency graph. Neither is a guarantee that a package has no vulnerabilities. These shell paths are for macOS/Linux; Windows installation has not been verified.

Uninstall by first removing only the Browser Bolt entry from your host configuration, then stopping its server process. Preserve other MCP entries. The dedicated environment can be moved to your local archive if it is no longer needed. The server does not install a daemon, edit your host configuration, or modify your browser profile.

## Host configuration

Example for hosts using `mcpServers`; adapt the enclosing configuration to your host. Replace the wheel placeholder with its absolute location. Supply the key through the host's local environment or secret settings; do not commit a real key to source control.

```json
{
  "mcpServers": {
    "jev-qwerebras": {
      "command": "uvx",
      "args": [
        "--from",
        "/path/to/jev_qwerebras_ultrafast-0.1.0-py3-none-any.whl[mcp]",
        "jev-qwerebras-mcp"
      ],
      "env": {
        "OPENROUTER_API_KEY": "REPLACE_WITH_YOUR_OPENROUTER_KEY"
      }
    }
  }
}
```

The default recommendation is `jev_qwen_openrouter`, which needs one OpenRouter key. The server does not read `.env` files. Provider URLs and model IDs are pinned in code; tool arguments cannot replace them. Generic `TEXT_MODEL_*` overrides are ignored. Direct Cerebras profiles use `CEREBRAS_API_KEY` in the server environment.

| Profile | Decision | Field text | Required environment |
| --- | --- | --- | --- |
| `jev_qwen_openrouter` | Jev through OpenRouter | Qwen through OpenRouter, separate call | `OPENROUTER_API_KEY` |
| `qwen_openrouter` | Qwen through OpenRouter | Inline with a typing decision, or separate helper | `OPENROUTER_API_KEY` |
| `jev` | Jev through OpenRouter | Unsupported | `OPENROUTER_API_KEY` |
| `jev_qwen` | Jev through OpenRouter | Qwen through direct Cerebras, separate call | Both keys |
| `qwen` | Qwen through direct Cerebras | Inline or separate helper | `CEREBRAS_API_KEY` |
| `jev_astra`, `jev_opus` | Jev through OpenRouter | Named partner through OpenRouter, separate call | `OPENROUTER_API_KEY` |
| `astra`, `opus` | Named model through OpenRouter | Inline or separate helper | `OPENROUTER_API_KEY` |

These profiles perform no decision fallback. OpenRouter Qwen latency has not been established by the direct Cerebras measurements. Calling either decision or writing tools can incur provider charges, including when a response is rejected.

## Tools and DOM contract

`list_profiles()` lists fixed profiles, key variable names, and capabilities without inspecting credentials or calling providers.

`choose_browser_action(profile, state, goal, history=[])` takes a current page and observed actions. For example:

```json
{
  "profile": "jev_qwen_openrouter",
  "goal": "Enter Trip in the title field",
  "state": {
    "url": "https://example.test/form",
    "title": "Form",
    "text": "Title",
    "fingerprint": "host-observation-001",
    "actions": [
      {"id": "title", "node": 1, "kind": "fill", "label": "Title", "value": ""},
      {"id": "submit", "node": 2, "kind": "click", "label": "Submit"}
    ]
  },
  "history": []
}
```

Action IDs must be unique, nonempty strings. `node` is a positive integer identifying an observed DOM element. Actions for the same element may share its node. `click`, `fill`, and `select` are supported; each `select` action names one observed option and requires its string `value`. Controls must use exactly `id: wait, kind: wait`, `id: scroll_up, kind: scroll`, or `id: scroll_down, kind: scroll`. Optional action attributes are `role`, `value`, `current_value`, `checked`, `selected`, and `expanded`. State flags are booleans; `checked` also permits the ARIA value `mixed`. Unknown fields, selectors, image data, unsupported actions, and malformed identities are rejected before spending.

Native `Browser.observe()` output includes geometry and guard data that are intentionally outside this input schema. Python hosts can call `jev_ultrafast.mcp_server.normalize_native_state(observation)` before passing it to the tool. It projects the allowed page/action fields, removes `rect` and scroll `delta`, and converts string `true`/`false` flags to booleans while preserving checked `mixed`. Keep the original observation for execution and freshness checks. This adapter does not redact content or establish freshness.

For an editable field's existing `model.field_context(...)` result, `normalize_native_field_context(context)` adds the required `kind: fill`, drops null optional attributes/history text, and projects the documented page fields. Only use it for an observed editable field. Hosts in other languages should implement the same projection. Raw native snapshots and field contexts are not directly interchangeable with MCP arguments.

The result contains `ok`, `operation`, `choice` (the host's action ID), `target` (the model's internal observed index), `observation_fingerprint`, `requires_field_text`, optional `inline_text`, `model_calls`, and `latency_ms`. Execute by mapping `choice` back to the observed action. Do not interpret `target` as a selector. `DONE` and `BLOCKED` have no observed target. A `DONE` decision is not evidence that the user's goal was achieved.

`write_browser_field(profile, context)` generates text for one supplied fill action. Its name describes the proposed field value; it does not write into the browser. Context has required `goal`, `field` (a `kind: fill` action), and `page` (the same four page fields, without `actions`), plus optional `recent_actions`. The result contains `text`, `field_id`, `observation_fingerprint`, usage, and latency. Jev alone cannot produce text. A paired Jev typing decision needs this second tool call; chat-only typing decisions already include `inline_text`.

History entries accept `action`, `kind`, `text`, and `page_changed`. The published tool schemas include all size limits: 200 actions, 10 history entries, 40,000 page-text characters, 8,000 goal characters, and 96,000 UTF-8 bytes across the whole argument payload. Generated field text is limited to 2,000 characters. Fingerprints are required, supplied by the host, and echoed for correlation; the server cannot attest that an observation is current.

## Host responsibilities and failures

Treat page text, labels, history, and model output as untrusted. Before every mutation, the host must obtain a fresh observation, check target identity and compatibility, and apply its own authorization rules. Recompute a decision when the observation changes. Verify the final result independently, and never automatically retry a browser mutation. This server is not an authorization or prompt-injection security boundary.

Each tool response has `ok`. Failed tool responses also use MCP `isError`, a fixed error code, elapsed latency, and any available failed-call accounting. Input errors are `invalid_input` or `payload_too_large`; missing credentials are `profile_unavailable`; Jev writing is `profile_has_no_writer`; a full concurrency limit is `server_busy`; rejected provider responses are `provider_response_rejected`. An unexpected internal failure is `decision_failed`. Error text does not include raw provider messages, submitted page content, or requests. Only a fixed vocabulary of numeric usage fields is returned; omitted usage or cost means unavailable, not zero.

One HTTP/2 client is shared across calls and closed on server shutdown. It permits four connections, keeps four idle connections for 120 seconds, and admits at most four provider operations concurrently. There are no automatic provider retries. Provider calls use the comparator's 30-second read/write timeout and 5-second connect timeout; these phase timeouts are not a total deadline.

Cancelling an MCP request suppresses delivery of its result, but an already dispatched synchronous provider operation can continue and retain its concurrency slot until completion or timeout. It may still incur provider charges. Cancellation is not a spending rollback; do not automatically resubmit cancelled work.

Fresh September 21 verification covers Python 3.12.13 on macOS, the official MCP SDK 2.2.0 stdio client, the documented local-wheel launch, and protocol versions 2024-11-05 and 2025-11-25. Synthetic provider tests cover decisions, writing, scope rejection, concurrency and cancellation. Named desktop integrations such as Claude Desktop, Codex, Cursor and VS Code have not been validated by these protocol tests. Screenshot-only hosts require a DOM adapter. Native Browser Harness fixture evidence is documented separately in the benchmark methods.

The packaged [reference host](HOST.md) has also completed [four verified live checkout runs](LIVE_HOST.md) from a clean wheel installation. It connects these decision tools to native Browser Harness and verifies the fixture independently. This establishes that specific integration, not compatibility with every MCP client.

Page context and goals are sent to the selected external provider. Avoid confidential or credential-bearing context. Generated field text can reflect the supplied context. This implementation has no general content-redaction layer, credential broker, billing ledger, or guarantee of autonomous task completion.

## Offline verification

```sh
uv run --extra mcp --frozen pytest tests/test_mcp_server.py
```

The suite uses fake HTTP providers and the real SDK stdio client to initialize, list tools, call `list_profiles`, and check error handling without credentials or paid calls. It also checks strict preflight validation, rejected-response accounting, output filtering, pool lifecycle, and the writer boundary. Installing without the extra skips MCP-specific tests.

The implementation uses the official [Python SDK v2 low-level server API](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/), verified with SDK 2.2.0. Explicit schemas and callback results keep validation errors deterministic and prevent echoing rejected argument values.
