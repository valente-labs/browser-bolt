# Changelog

## 0.1.0

Initial Browser Bolt BYOK developer preview, based on `browser-use/jev-ultrafast` commit `452c1ad2dd628008f1d5608f28158d76e49e6cc0`.

- Added an optional stdio MCP service with `list_profiles`, `choose_browser_action`, and `write_browser_field`. The calling host supplies observed actions, executes the chosen action, and verifies the outcome.
- Added OpenRouter-only Jev/Qwen profiles alongside direct Cerebras routes. Provider requirements are explicit for each profile.
- Added the packaged reference host with a credential-free MCP preflight and six bounded synthetic fixtures: choice, note, review, checkout, onboarding, and recovery.
- Added independent final-DOM verification, fixture-scope checks, cancellation handling, and cleanup reporting to the reference host. Browser mutations are not automatically retried.
- Added the native Agent's bounded Jev-to-Qwen decision fallback and validation against observed action targets. Paired MCP comparison profiles keep field writing separate from decision fallback.
- Added reproducible comparison fixtures and results with failed attempts, timing boundaries, and reported or estimated provider costs. The installed-host acceptance record covers four verified synthetic checkout runs on macOS; it does not establish arbitrary-site or desktop-client compatibility.
- Added release packaging checks, hash-locked runtime requirements, dependency inventory, and installed-wheel MCP checks.

Python imports remain `jev_ultrafast`; package and command names retain `jev-qwerebras` for compatibility. The original MIT license and Browser Use attribution are retained.

See [MCP setup](docs/MCP.md), [reference host usage](docs/HOST.md), and [installed-host acceptance](docs/LIVE_HOST.md) for commands and limits. Live runs use the contributor's provider account and incur usage charges. This release provides no managed account, billing, or hosted inference service.
