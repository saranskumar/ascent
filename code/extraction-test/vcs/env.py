"""Load KEY=VALUE lines from a gitignored `.env` next to cli.py into os.environ.

Real environment variables win over the file, so a key set in the shell is never overridden.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_env(path: Path = ENV_FILE) -> list[str]:
    """Return the names loaded (never the values)."""
    loaded = []
    if not path.is_file():
        return loaded
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip().removeprefix("export ").strip(), val.strip().strip('"').strip("'")
        if key and val and not os.environ.get(key):
            os.environ[key] = val
            loaded.append(key)
    return loaded
