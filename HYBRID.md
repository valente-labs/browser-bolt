# Provider configuration

The recommended setup uses Jev 1.13 through OpenRouter's Decisions API and Qwen 3.8 27B through Cerebras. It uses the same browser controller for all three policies.

## Launch

```sh
uv sync --locked
cp .env.example .env
# Set local provider keys, then:
uv run jev-qwerebras
```

The console entrypoint reads `.env` from the working directory. Use literal, unquoted `KEY=value` lines there; it does not expand shell expressions. Run from the checkout so the intended file is loaded.

An optional launcher can read an existing file elsewhere:

```sh
python3 local/run_hybrid.py --env-file /path/to/provider.env
# Or configure the file through the environment:
HYBRID_KEYS_FILE=/path/to/provider.env python3 local/run_hybrid.py
```

`--env-file` takes precedence over `HYBRID_KEYS_FILE`. Without either, the launcher reads the checkout's `.env` if present. It accepts literal `KEY=value` lines, optional `export ` prefixes, and matching outer quotes; it performs no interpolation or shell execution. Values already in the process environment win over file values, then launcher defaults fill missing settings. Credentials stay in the process environment and are not copied into the checkout. Provider calls still transmit the relevant API key to the provider for authentication.

Open **http://127.0.0.1:8766**. Run `uv run browser-harness --doctor` for native Chrome connection diagnostics. The inspector uses Browser Harness; a separate Chrome-extension benchmark does not by itself verify this native integration.

## Settings

| Variable | Recommended value / behavior |
| --- | --- |
| `TYPESAFE_PROVIDER` | `openrouter`; `direct` uses the original TypeSafe API. |
| `OPENROUTER_API_KEY` | Required for Jev through OpenRouter. |
| `TYPESAFE_MODEL` | `typesafe/jev-1.13` in the example. The OpenRouter adapter pins this model; direct mode reads this variable. |
| `TYPESAFE_API_KEY` | Required only for `TYPESAFE_PROVIDER=direct`. |
| `CEREBRAS_API_KEY` | Required for Qwen decisions; also used for Cerebras field writing. |
| `TEXT_MODEL_BASE_URL` | `https://api.cerebras.ai/v1` for the field writer. |
| `TEXT_MODEL` | `qwen-3.8-27b` for the field writer. |
| `TEXT_MODEL_REASONING` | `none` for the Cerebras field writer. |
| `TEXT_MODEL_API_KEY` | Optional generic field-writer key; the Cerebras adapter prefers `CEREBRAS_API_KEY`. |
| `QWEV_POLICY` | `hybrid` (default), `jev`, or `qwen`. |
| `QWEV_CONFIDENCE_THRESHOLD` | `0.6` by default, finite number from 0 through 1. |
| `QWEV_FALLBACK_THRESHOLD` | Legacy alias used only when `QWEV_CONFIDENCE_THRESHOLD` is absent. |
| `QWEV_CEREBRAS_MODEL` | `qwen-3.8-27b` by default for Qwen decisions, independently of the field writer. |

`qwen` policy does not need a Jev provider key. `jev` policy needs the configured field-writer key when it selects `TYPE_TEXT`. `hybrid` needs both providers for its complete path. The supplied `.env.example` and launcher choose the recommended provider settings; the underlying library defaults to direct TypeSafe and Cerebras field writing when those settings are absent.

The shared HTTP/2 client reuses connections. The controller owns browser execution, target guards, budgets, and the field-text cache for identical stale-retry inputs. Qwen decisions with inline text avoid a separate field-writing request.

See [BENCHMARK.md](BENCHMARK.md) for measured behavior and [SECURITY.md](SECURITY.md) for the data boundary. Model names alone do not establish a speed improvement.
