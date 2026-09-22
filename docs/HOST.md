# Browser Bolt reference host

The packaged `jev-qwerebras-host` command runs Browser Bolt's decision MCP with the official Python SDK over stdio. In live mode, the existing native Agent executes the selected actions through Browser Harness and checks the fixture's final DOM independently. The host opens and closes one owned tab.

Python 3.12+ and the package's `[mcp]` extra are required. Install the reviewed wheel and tested dependency set using [MCP installation](MCP.md). The command works outside the source checkout; its fixtures ship inside the wheel. `python -m jev_ultrafast.mcp_host` is an equivalent entrypoint in the installed environment.

## Check the installation without keys

```sh
jev-qwerebras-host --preflight
jev-qwerebras-host --list-profiles
```

Running the command without arguments also performs preflight. It starts the actual stdio MCP subprocess, initializes the SDK connection, checks the three expected tool names, and calls `list_profiles`. It prints a JSON report with profile requirements and the six available fixtures. No browser connection or provider request occurs, even if your shell already contains provider keys. Preflight does not establish browser readiness or validate credentials.

If the command is missing, use the absolute path of the installed environment's executable. A `missing_dependency` result means the environment needs the `[mcp]` extra.

## Run a synthetic fixture

Connect Browser Harness to a dedicated Chrome profile containing only synthetic test data. Browser Harness uses its connected Chrome profile; the host does not create or select that profile for you. Run the included `browser-harness --doctor` command for connection setup and diagnostics before the first live run.

For an isolated Browser Harness setup, the host preserves the parent process environment, including `BU_NAME`, `BH_HOME`, `BH_RUNTIME_DIR`, and `BU_CDP_URL`. These browser settings are not passed to the MCP child. If a long workspace path produces `AF_UNIX path too long`, point `BH_RUNTIME_DIR` to a short private directory under `/tmp`, while keeping `BH_HOME` in your chosen profile directory. Run `browser-harness --doctor` again with those same environment settings.

Supply the selected provider key through your local process environment or secret manager. The default `jev_qwen_openrouter` profile requires `OPENROUTER_API_KEY`. No environment file is searched or loaded. The MCP subprocess receives only the selected profile's provider keys in addition to the SDK's standard system environment; unrelated credentials and model endpoint overrides are not forwarded.

The following commands make provider requests and incur usage charges:

```sh
jev-qwerebras-host --live --task choice --output choice-result.json
jev-qwerebras-host --live --task onboarding --max-steps 20 --timeout 120 --output onboarding-result.json
```

Start with `choice`: select Express delivery and save the preference. `onboarding` creates the synthetic Casey Demo profile, writes a short bio, selects Design and Travel, and finishes setup. The model receives one natural-language goal and the observed page actions. The host supplies no action plan or prewritten field values.

Other packaged tasks are `note`, `review`, `checkout`, and `recovery`. The checkout fixture only reviews a synthetic order. No purchase occurs. Profile names are fixed; `jev` supports only `choice` because it has no field writer. Direct `qwen` requires `CEREBRAS_API_KEY`; `jev_qwen` requires both provider keys. The remaining profiles require `OPENROUTER_API_KEY`.

The host accepts no arbitrary URL, goal, selector, or browser profile path. It serves only the packaged HTML fixtures on an ephemeral `127.0.0.1` port. Only the exact selected fixture URL is allowed in model requests and immediately before browser input. Keep these fixtures synthetic. Page text and generated field text go to the selected providers during live runs.

## Results and stopping

A successful report requires both an Agent `done` status and a matching final fixture DOM. A model's `DONE` response alone fails verification. For onboarding, the verifier checks the final heading, Casey Demo's name, the bio's design/travel content, and the two selected interests. The JSON report includes model-call accounting, action count, elapsed time, and any failure or cleanup status. It omits page bodies and typed values. Unknown provider cost remains unknown.

The default run deadline is 120 seconds, configurable from 1 to 300. The default action limit is 20, configurable from 1 to 30; a separate tick budget bounds stale observations. The Agent's existing model-call limit also applies. Each MCP request has a 45-second wait ceiling. Browser setup, synchronous Browser Harness I/O, and cleanup can delay return beyond a cooperative deadline.

Press Ctrl-C to cancel. The host signals the Agent, cancels any pending client request, and waits for the browser worker to finish and close its tab before closing stdio and the fixture server. It checks cancellation before subsequent input. An input already dispatched cannot be undone, and an in-flight provider request may still incur charges that are not returned in the report. Failed mutations are never automatically retried. Inspect a cleanup error before starting another run.

Exit status is `0` for successful preflight or verified completion, `1` for a failed live run, `2` for preflight/configuration errors, and `130` for a live run cancelled with Ctrl-C. `--output` writes a local JSON report and refuses to overwrite an existing file. Use a new filename for each run.

The offline tests exercise real stdio initialization and profile discovery, with doubles for provider and browser behavior, including cancellation and cleanup. Separate [installed-wheel live acceptance](LIVE_HOST.md) completed four checkout runs through this host. The broader native measurements remain in [NATIVE_COMPARISON.md](NATIVE_COMPARISON.md); those use a different timing boundary.
