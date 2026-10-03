"""Runs on disk: data/runs/<run_id>/ holds one meeting's screen capture and everything made from it.

    meta.json             source, linked meeting, offset, capture window and times
    capture.mp4           the 1 fps window recording (deleted after extraction unless kept)
    screenshots.json      distinct screens (+ images/)
    summary.md            our summary; summary.meta.json says which meeting/model made it
    published.json        what was written to Meetily; backups/ = Meetily's summary before that
    pending_overwrite.json / kept_meetily.json   the Replace/Keep decision
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import speaker_names
from .meetily_client import MeetilyClient
from .transcript import segment_start, speaker_key, speaker_label, speakers
from .writeback import is_placeholder_title

SLUG = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "transcript.json"


def parse_ts(s: str | None) -> datetime | None:
    """ISO timestamp -> aware datetime. Meetily uses 9 fractional digits; Python takes 6."""
    if not s:
        return None
    m = re.match(r"^(.*?T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", s.strip())
    if not m:
        return None
    frac = (m.group(2) or "")[:7]
    tz = m.group(3) or "+00:00"
    tz = "+00:00" if tz == "Z" else tz
    try:
        dt = datetime.fromisoformat(m.group(1) + frac + tz)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def read_json(f: Path, default=None):
    try:
        return json.loads(Path(f).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def write_json(f: Path, obj) -> None:
    f = Path(f)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(f)


class Store:
    def __init__(self, data_dir: Path, client_factory=MeetilyClient):
        self.data = Path(data_dir).resolve()
        self.runs_dir = self.data / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.client_factory = client_factory
        self._lock = threading.Lock()
        self.recording_started_lookup = lambda meeting_id: None   # set by the automation

    # ---- paths
    def run_dir(self, run_id: str) -> Path:
        if not SLUG.match(run_id or ""):
            raise ValueError("bad run id")
        d = (self.runs_dir / run_id).resolve()
        if d.parent != self.runs_dir:
            raise ValueError("bad run id")
        return d

    def new_run_id(self, prefix: str) -> str:
        base = f"{prefix}-" + datetime.now().strftime("%m%d-%H%M%S")
        rid, n = base, 1
        while (self.runs_dir / rid).exists():
            n += 1
            rid = f"{base}-{n}"
        return rid

    # ---- meta
    def meta(self, run_id: str) -> dict:
        return read_json(self.run_dir(run_id) / "meta.json", {}) or {}

    def update_meta(self, run_id: str, patch: dict) -> dict:
        with self._lock:
            m = self.meta(run_id)
            m.update(patch)
            write_json(self.run_dir(run_id) / "meta.json", m)
            return m

    def create_run(self, prefix: str, source_name: str, **meta) -> str:
        rid = self.new_run_id(prefix)
        d = self.run_dir(rid)
        d.mkdir(parents=True)
        write_json(d / "meta.json", {"created": time.time(), "source_name": source_name,
                                     "offset": 0.0, "meeting_id": None, **meta})
        return rid

    # ---- listing
    def runs(self) -> list[dict]:
        out = []
        for d in self.runs_dir.iterdir():
            if not d.is_dir() or not SLUG.match(d.name):
                continue
            m = read_json(d / "meta.json", {}) or {}
            doc = read_json(d / "screenshots.json")
            shots = (doc or {}).get("screenshots", [])
            pub = read_json(d / "published.json")
            title = m.get("meeting_title")
            if not title and (d / "summary.md").exists():
                first = (d / "summary.md").read_text("utf-8").lstrip().split("\n", 1)[0]
                title = first.lstrip("#").strip() if first.startswith("#") else None
            if title and is_placeholder_title(title):
                title = None
            out.append({
                "id": d.name, "created": m.get("created") or d.stat().st_mtime,
                "source": m.get("source_name") or d.name, "meeting_id": m.get("meeting_id"),
                "meeting_title": title,
                "duration": (doc or {}).get("duration"), "screens": len(shots),
                "diagrams": sum(1 for s in shots if s.get("type") == "diagram"),
                "described": sum(1 for s in shots if s.get("description")),
                "extracted": doc is not None,
                "has_summary": (d / "summary.md").exists(),
                "published": bool(pub), "pending": (d / "pending_overwrite.json").exists(),
            })
        return sorted(out, key=lambda r: r["created"], reverse=True)

    def detail(self, run_id: str) -> dict:
        d = self.run_dir(run_id)
        if not d.is_dir():
            raise FileNotFoundError("run not found")
        doc = read_json(d / "screenshots.json") or {}
        backups = sorted(p.name for p in (d / "backups").glob("*.json")) if (d / "backups").is_dir() else []
        return {"id": run_id, "dir": str(d), "meta": self.meta(run_id),
                "duration": doc.get("duration"), "screenshots": doc.get("screenshots", []),
                "extracted": bool(doc),
                "summary": (d / "summary.md").read_text("utf-8") if (d / "summary.md").exists() else None,
                "summary_meta": read_json(d / "summary.meta.json"),
                "published": read_json(d / "published.json"),
                "pending": read_json(d / "pending_overwrite.json"),
                "kept": read_json(d / "kept_meetily.json"),
                "backups": backups}

    def screenshots(self, run_id: str) -> dict:
        doc = read_json(self.run_dir(run_id) / "screenshots.json")
        if doc is None:
            raise FileNotFoundError("screens not extracted yet")
        return doc

    def save_screenshots(self, run_id: str, doc: dict) -> None:
        write_json(self.run_dir(run_id) / "screenshots.json", doc)

    def delete(self, run_id: str) -> None:
        d = self.run_dir(run_id)
        if d.is_dir():
            shutil.rmtree(d)

    def run_for_meeting(self, meeting_id: str) -> str | None:
        """Newest run linked to this Meetily recording."""
        for r in self.runs():
            m = self.meta(r["id"])
            if meeting_id in (m.get("recording_meeting_id"), m.get("meeting_id")):
                return r["id"]
        return None

    # ---- meetily data
    def transcript(self, meeting_id: str | None) -> dict:
        if not meeting_id or meeting_id == "fixture":
            return read_json(FIXTURE, {"segments": []})
        return self.client_factory().get_transcript(meeting_id) or {}

    def speakers(self, meeting_id: str) -> list[dict]:
        segs = self.transcript(meeting_id).get("segments") or []
        names = speaker_names.load(self.data, meeting_id)
        sp = speakers(segs)
        for x in sp:
            x["name"] = names.get(x["id"], "")
        return sp

    def save_speakers(self, meeting_id: str, names: dict) -> dict:
        return speaker_names.save(self.data, meeting_id, names)

    def speaker_names(self, meeting_id: str | None) -> dict:
        return speaker_names.load(self.data, meeting_id)

    def transcript_lines(self, meeting_id: str | None) -> list[dict]:
        """The meeting's speech as [{t, end, who, text}], with the speaker names set here."""
        segs = self.transcript(meeting_id).get("segments") or []
        names = speaker_names.load(self.data, meeting_id)
        multi = len({speaker_key(x) for x in segs if speaker_key(x) is not None}) > 1
        out, last = [], 0.0
        for seg in segs:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            t = segment_start(seg)
            t = last if t is None else t
            last = t
            end = seg.get("audio_end_time")
            out.append({"t": t, "end": float(end) if isinstance(end, (int, float)) else None,
                        "who": speaker_label(seg, names, multi), "text": text})
        return out

    def timeline(self, run_id: str, meeting_id: str | None, offset: float) -> dict:
        """Speech segments and screens on one clock (the meeting audio's)."""
        doc = read_json(self.run_dir(run_id) / "screenshots.json", {}) or {}
        segs = self.transcript(meeting_id).get("segments") or []
        names = speaker_names.load(self.data, meeting_id)
        multi = len({speaker_key(x) for x in segs if speaker_key(x) is not None}) > 1
        items, last = [], 0.0
        for seg in segs:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            t = segment_start(seg)
            t = last if t is None else t
            last = t
            items.append({"t": t, "kind": "speech", "who": speaker_label(seg, names, multi),
                          "text": text})
        for s in doc.get("screenshots", []):
            items.append({"t": s["start"] + offset, "end": s["end"] + offset, "kind": "screen",
                          "type": s.get("type"), "image": s["image"], "id": s["id"],
                          "text": s.get("description") or s.get("text") or ""})
        return {"items": sorted(items, key=lambda x: (x["t"], x["kind"] != "speech"))}

    def suggest_offset(self, run_id: str, meeting_id: str | None) -> dict:
        """Seconds from the meeting's recording start (Meetily's audio t=0) to the capture start.
        Exact when we have recording.started's occurred_at; otherwise estimated: Meetily's
        meeting created_at is the STOP time, so start ~ created_at minus the transcript length."""
        m = self.meta(run_id)
        cap = parse_ts(m.get("capture_started_at"))
        if not cap or not meeting_id or meeting_id == "fixture":
            return {"offset": None, "basis": "no capture start time"}
        start, basis = None, "recording.started"
        if m.get("recording_meeting_id") == meeting_id:
            start = parse_ts(m.get("recording_started_at"))
        if start is None:
            start = parse_ts(self.recording_started_lookup(meeting_id))
        if start is None:
            c = self.client_factory()
            end = parse_ts((c.get_meeting(meeting_id) or {}).get("created_at"))
            segs = (c.get_transcript(meeting_id) or {}).get("segments") or []
            length = max((s.get("audio_end_time") or 0 for s in segs), default=0)
            if end is None:
                return {"offset": None, "basis": "unknown"}
            start, basis = end - timedelta(seconds=float(length)), "estimated (meeting end - transcript length)"
        return {"offset": round((cap - start).total_seconds(), 1), "basis": basis}

    # ---- import from the test version
    def import_from(self, other_data: Path) -> dict:
        """Copy runs (and speaker names) from extraction-test/data. Existing runs are skipped."""
        other = Path(other_data)
        copied, skipped = [], 0
        for d in other.iterdir() if other.is_dir() else []:
            if not d.is_dir() or not SLUG.match(d.name) or d.name in ("events", "speakers", "_cand"):
                continue
            if not ((d / "screenshots.json").exists() or (d / "meta.json").exists()):
                continue
            dest = self.runs_dir / d.name
            if dest.exists():
                skipped += 1
                continue
            shutil.copytree(d, dest, ignore=shutil.ignore_patterns("_candidates", "*.mp4"))
            m = read_json(dest / "meta.json", {}) or {}
            m.setdefault("created", d.stat().st_mtime)
            m["imported_from"] = str(d)
            write_json(dest / "meta.json", m)
            copied.append(d.name)
        sp = other / "speakers"
        if sp.is_dir():
            (self.data / "speakers").mkdir(exist_ok=True)
            for f in sp.glob("*.json"):
                if not (self.data / "speakers" / f.name).exists():
                    shutil.copy(f, self.data / "speakers" / f.name)
        return {"copied": copied, "skipped": skipped}
