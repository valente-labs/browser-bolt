# Browser Bolt demo transcript

83 seconds. This video is silent; the captions describe the screen.

**0:00–0:06. Earlier benchmark.** The 5× Astra / 7× Opus headline comes from Jev + OpenRouter Qwen's mean task time in the earlier native benchmark: six attempts per route across three synthetic tasks, with no decision fallback. The 7× figure is rounded. This card is a visualization, separate from the new live footage.

**0:06–0:15. The host's role.** Your host observes the page and checks the actual result. Browser Bolt chooses an action or writes field text. Your host performs the browser action.

**0:15–0:45. Setup.** Choose a provider route and add keys in your local host, outside the website. The direct Cerebras route uses two keys; OpenRouter uses one. Copy the configuration and follow the reference host guide. Copying does not connect a host. Goals, selected page context and action history can go to your provider. Start with synthetic data.

**0:45–1:09. Four live routes.** Each route completes one synthetic checkout. The recordings were made sequentially and aligned at the first visible fixture. Playback uses original capture speed, with the initial blank frame omitted. The clocks show time from that first visible frame. Separately reported task time includes browser setup and verification and excludes starting the CLI/MCP process. All four runs independently verified; no purchase was made.

**1:09–1:18. API costs.** The chart shows model API cost per checkout for these single runs. OpenRouter usage is reported; the Cerebras Qwen portion is estimated. Hosting, service overhead, support and payment costs are excluded.

**1:18–1:23. Get the code.** Get the code at github.com/valente-labs/browser-bolt. Follow docs/HOST.md for setup and start with synthetic data.
