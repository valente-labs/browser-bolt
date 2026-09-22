"""Launch the inspector with optional local environment configuration."""

import argparse
import os
from pathlib import Path

DEFAULTS = {
    "TYPESAFE_PROVIDER": "openrouter",
    "TYPESAFE_MODEL": "typesafe/jev-1.13",
    "TEXT_MODEL_BASE_URL": "https://api.cerebras.ai/v1",
    "TEXT_MODEL": "qwen-3.8-27b",
    "TEXT_MODEL_REASONING": "none",
    "QWEV_POLICY": "hybrid",
}


def launch_environment(source, environment):
    """Read literal KEY=value lines; inherited environment values take precedence."""
    env = dict(environment)
    if source is not None:
        for line in source.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.removeprefix("export ").partition("=")
            if sep and key.strip():
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                env.setdefault(key.strip(), value)
    for key, value in DEFAULTS.items():
        env.setdefault(key, value)
    return env


def main():
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=os.environ.get("HYBRID_KEYS_FILE"),
        help="optional environment file (default: HYBRID_KEYS_FILE, then checkout .env)",
    )
    args = parser.parse_args()
    source = args.env_file.expanduser() if args.env_file else repo / ".env"
    if not source.is_file():
        if args.env_file:
            parser.error("The selected environment file does not exist or is not a file.")
        source = None
    try:
        env = launch_environment(source, os.environ)
    except (OSError, UnicodeError):
        parser.error("The selected environment file could not be read as text.")
    os.chdir(repo)
    try:
        os.execvpe("uv", ["uv", "run", "--frozen", "jev-qwerebras"], env)
    except FileNotFoundError:
        parser.error("Install uv and run uv sync --locked before launching.")


if __name__ == "__main__":
    main()
