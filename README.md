# Browser Bolt

<img src="site/public/assets/brand/browser-bolt.png" alt="Browser Bolt" width="104">

**Add model-guided browser decisions to a host that already controls the browser.** Browser Bolt is a local, open-source MCP server. It chooses from actions your host observed on the page, and can provide a proposed field value. Your host remains responsible for authorizing and executing each action and checking the result.

[Get started](https://browserbolt.com/start/) · [Website](https://browserbolt.com/) · [v0.1.0 release](https://github.com/valente-labs/browser-bolt/releases/tag/v0.1.0)

## Start here

The public v0.1.0 prerelease is a bring-your-own-key developer preview. The recommended `jev_qwen_openrouter` profile needs one OpenRouter key for Jev decisions and Qwen field text. Provider calls can incur charges. There is no Browser Bolt account or subscription. The package retains its `jev-qwerebras` command names for compatibility.

Follow the [setup guide](https://browserbolt.com/start/) to install and connect the MCP server. The [MCP documentation](docs/MCP.md) covers other profiles, release verification, and the host contract.

## How it works

Your browser-capable host supplies a goal and a current list of observed page actions. Browser Bolt returns an operation and an action ID; for a text field, it can also return the proposed value. The host checks that the page and target are still current, decides whether to allow the action, executes it, and verifies the outcome. The MCP server does not accept screenshots or control the browser itself.

The [reference host](docs/HOST.md) demonstrates that loop with Browser Harness on packaged synthetic tasks. Named desktop-host integrations have not been verified end to end. For measurements and their limits, see the [benchmark notes](BENCHMARK.md) and [native browser results](docs/NATIVE_COMPARISON.md).

Goals, page text, field values, and action history may be sent to model providers and recorded in local traces. Use synthetic data while evaluating. Read the [security and data boundary](SECURITY.md) before connecting real pages.

## Source and attribution

Browser Bolt is built by [Valente Labs](https://valentelabs.ai/) and released under the [MIT license](LICENSE). It derives from [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) at commit `452c1ad2dd628008f1d5608f28158d76e49e6cc0`. The [upstream README](docs/UPSTREAM-README.md) is retained for attribution, and Python imports remain `jev_ultrafast` for compatibility. See [CONTRIBUTING.md](CONTRIBUTING.md) for source setup and tests.
