"""Speaker names per meeting ({cluster id -> name}), kept locally in data/speakers/<meeting>.json.

Meetily Pro's API exposes only diarization cluster ids per segment, not the names assigned in
the app, so the names used in our summary are entered in our UI.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_ID = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


def _file(data_dir: Path, meeting_id: str) -> Path:
    if not _ID.match(meeting_id or ""):
        raise ValueError("bad meeting id")
    return Path(data_dir) / "speakers" / f"{meeting_id}.json"


def load(data_dir: Path, meeting_id: str | None) -> dict[str, str]:
    if not meeting_id or meeting_id == "fixture":
        return {}
    f = _file(data_dir, meeting_id)
    try:
        d = json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in d.items() if str(v).strip()}


def save(data_dir: Path, meeting_id: str, names: dict) -> dict[str, str]:
    clean = {str(k)[:40]: str(v).strip()[:80] for k, v in (names or {}).items() if str(v).strip()}
    f = _file(data_dir, meeting_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(clean, indent=2, ensure_ascii=False), "utf-8")
    return clean
