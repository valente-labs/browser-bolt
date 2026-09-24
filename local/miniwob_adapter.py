"""Serve pinned MiniWoB++ HTML with the same seeded episode for any controller.

This is a local smoke-benchmark adapter, not the official MiniWoB++ evaluator.
It changes the startup boundary and episode timer, but never supplies answers.
"""

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

TASKS = frozenset({"click-test", "click-test-2", "enter-text", "choose-list"})


def handler_for(root: Path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def do_GET(self):
            parsed = urlsplit(self.path)
            stem = Path(parsed.path).stem
            if parsed.path != f"/miniwob/{stem}.html" or stem not in TASKS:
                return super().do_GET()
            values = parse_qs(parsed.query).get("seed", [])
            if len(values) != 1 or not values[0].isdigit() or not 0 <= int(values[0]) <= 1000:
                self.send_error(400, "seed_required")
                return
            seed = int(values[0])
            page = (root / "miniwob" / f"{stem}.html").read_text(encoding="utf-8")
            prepare = f"""<script id="controller-neutral-episode-adapter">
const originalOnload = window.onload;
window.onload = function(event) {{
  originalOnload?.call(this, event);
  Math.seedrandom({seed});
  core.EPISODE_MAX_TIME = 1000000;
  core.startEpisodeReal();
  document.getElementById('timer-countdown')?.parentElement?.style.setProperty('display','none');
  document.getElementById('click-canvas')?.style.setProperty('display','none');
  window.WOB_CONTROLLER_ADAPTER = {{seed:{seed},episodeMaxTime:core.EPISODE_MAX_TIME}};
}};
</script>"""
            body = page.replace("</body>", prepare + "</body>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html-root", type=Path, required=True, help="Pinned MiniWoB++ miniwob/html directory")
    parser.add_argument("--port", type=int, default=8945)
    args = parser.parse_args()
    root = args.html_root.resolve(strict=True)
    if not (root / "miniwob" / "click-test.html").is_file():
        parser.error("--html-root must point to MiniWoB++ miniwob/html")
    ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(root)).serve_forever()


if __name__ == "__main__":
    main()
