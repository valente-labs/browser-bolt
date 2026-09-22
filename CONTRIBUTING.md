# Contributing to Browser Bolt

Help improve the local decision MCP, its reference host, or a reproducible synthetic test. Keep each change small enough to review with its evidence.

Browser Bolt is a BYOK developer preview derived from [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) at commit `452c1ad2dd628008f1d5608f28158d76e49e6cc0`. The original [MIT license](LICENSE) and Browser Use attribution remain in place. Keep those notices and identify any third-party code you introduce. This project does not require a CLA or DCO sign-off.

## Set up and check a source checkout

Use Python 3.12 or newer and uv. CI pins uv 0.11.16 and uses Node 22 for JavaScript checks. Run these commands from the repository root:

```sh
uv sync --locked --extra mcp
uv run --frozen --extra mcp pytest
uv run --frozen ruff check jev_ultrafast tests local examples
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
node --check local/browser_driver.js
uv build
```

Run the relevant test file while editing, then the full checks before submitting. Add a regression test for a behavior change, especially a failure path. There is no configured static Python type checker.

The test suite uses provider doubles and makes no paid model calls. Some tests start local fixture servers or isolated Chrome processes when Chrome is available. Dependency installation can access package indexes.

Verify the MCP connection without keys or a browser:

```sh
uv run --frozen --extra mcp jev-qwerebras-host --preflight
```

For packaging changes, follow the clean wheel installation in [MCP setup](docs/MCP.md), then run the installed host's preflight outside the source checkout. A source import is not evidence that the wheel contains the required files.

## Report a bug or propose a change

Search existing issues first. A useful bug report includes the release or commit, OS and Python version, host/browser versions when relevant, selected profile, a synthetic reproduction, and expected versus observed behavior. Report timeouts and cleanup failures as failures. Preserve unknown cost as unknown.

Do not post keys, tokens, account identifiers, environment files, browser profiles, customer content, or recordings of authenticated sessions. Create a synthetic reproduction instead of sharing the original page. Review even synthetic logs before attaching them. Follow [SECURITY.md](SECURITY.md) for vulnerabilities; public bug reports are not a place for sensitive exploit details.

For a pull request, explain the problem, the resulting behavior, and the checks you ran. Name checks you could not run. Keep unrelated cleanup separate. Changes to action validation, provider routing, cancellation, cost reporting, or supported browser surfaces need tests at those boundaries.

Maintainers review changes to the fork and decide what to merge and release. Support is best effort, with no response-time commitment. Use the [code of conduct](CODE_OF_CONDUCT.md) in issues and reviews.

## Keep live evaluation explicit

The reference host's `--live` mode incurs provider charges. Contributors do not need to spend money to submit a fix. Use doubles for routine tests. If you choose to run live acceptance, use a dedicated Chrome profile with synthetic data, your own provider account, and the bounded commands in [HOST.md](docs/HOST.md).

The host observes actions, executes them, and verifies the final fixture state. A model's `DONE` response does not establish success. Do not add automatic retries for browser mutations. Keep the decision MCP separate from the host's authorization and execution duties.

The [support boundary and live acceptance record](docs/LIVE_HOST.md) identify the tested macOS setup. Broader websites and named desktop clients need their own evidence. When reporting a measurement, include the profile, provider route, sample size, failed attempts, timing boundaries, and whether cost was reported, estimated, or unknown.
