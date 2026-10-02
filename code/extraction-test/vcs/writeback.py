"""Step (c): write our summary into Meetily, after backing up what is there.

`PUT /v1/meetings/{id}/summary` overwrites outright and has no undo, so the order is fixed:
check write access -> fetch Meetily's current summary -> save it to disk -> PUT -> read back.
If anything before the PUT fails, nothing is written.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .meetily_client import MeetilyClient, MeetilyError

# Summary statuses that mean Meetily is still generating; overwriting then would race it.
IN_PROGRESS = {"processing", "generating", "running", "pending", "in_progress", "started", "queued"}


class WritebackError(Exception):
    pass


def summary_text(result) -> str:
    """Best-effort plain text of a Meetily summary `result` (its stored JSON is not documented)."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for k in ("markdown", "text", "content", "summary"):
            if isinstance(result.get(k), str):
                return result[k]
        return json.dumps(result, indent=2, ensure_ascii=False)
    return "" if result is None else str(result)


def split_title(text: str) -> tuple[str | None, str]:
    """Meetily stores the summary body WITHOUT the leading `# Title` (it uses the title as the
    meeting name), as seen in a real Pro summary. Return (title, body without that line)."""
    lines = text.strip().splitlines()
    if lines and re.match(r"#\s+\S", lines[0]):
        return lines[0].lstrip("#").strip(), "\n".join(lines[1:]).strip()
    return None, text.strip()


def fingerprint(text: str) -> str:
    """Content hash that ignores formatting. Meetily reformats Markdown when it stores a summary
    (pads table columns, rewrites bullets, adds blank lines; seen live Oct 2), so a byte hash of
    what we PUT never matches what GET returns. Letters and digits only, lowercased."""
    return hashlib.sha256(re.sub(r"[\W_]+", "", (text or "").lower()).encode()).hexdigest()


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)[:60]


def backup_summary(client: MeetilyClient, meeting_id: str, backup_dir: Path) -> Path | None:
    """Save Meetily's current summary (raw API response) and return the file, or None when the
    meeting has no summary yet (404). Any other failure aborts: we don't overwrite blind."""
    try:
        current = client.get_summary(meeting_id)
    except MeetilyError as e:
        if e.status == 404:
            return None
        raise WritebackError(f"couldn't back up Meetily's current summary, so nothing was "
                             f"written: {e}") from e
    if str(current.get("status", "")).lower() in IN_PROGRESS:
        raise WritebackError(f"Meetily is still generating a summary (status "
                             f"{current.get('status')!r}); try again when it finishes.")
    backup_dir.mkdir(parents=True, exist_ok=True)
    f = backup_dir / (f"meetily-summary-{_safe(meeting_id)}-"
                      f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    f.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    return f


DEFAULT_TITLE = re.compile(r"^\s*(new meeting|untitled)\b", re.I)


def maybe_rename(client: MeetilyClient, meeting_id: str, title: str | None, log=print) -> str | None:
    """Give a meeting that still has Meetily's default name ("New Meeting 4:36 PM") the
    summary's title, as Meetily does with its own summaries. Never fails the write."""
    if not title:
        return None
    try:
        current = (client.get_meeting(meeting_id) or {}).get("title") or ""
        if not DEFAULT_TITLE.match(current):
            return None
        client.rename_meeting(meeting_id, title[:200])
        log(f"renamed meeting: {current!r} -> {title!r}")
        return title
    except MeetilyError as e:
        log(f"couldn't rename the meeting ({e}); the summary was written")
        return None


def publish(client: MeetilyClient, meeting_id: str, text: str, run_dir: Path, *,
            delete_screenshots: bool = False, rename: bool = True, log=print) -> dict:
    """Back up, PUT, verify. Returns the record also saved as <run_dir>/published.json."""
    title, text = split_title((text or "").strip())
    if not text:
        raise WritebackError("the summary is empty")
    try:
        client.require_write()
    except MeetilyError as e:
        raise WritebackError(str(e)) from e

    run_dir = Path(run_dir)
    log("backing up Meetily's current summary...")
    backup = backup_summary(client, meeting_id, run_dir / "backups")
    log(f"backup: {backup}" if backup else "Meetily has no summary for this meeting yet; nothing to back up")

    log("writing summary to Meetily...")
    try:
        client.put_summary(meeting_id, text)
    except MeetilyError as e:
        raise WritebackError(f"Meetily rejected the summary ({e}). Meetily's own summary is "
                             f"unchanged" + (f"; backup kept at {backup}" if backup else "")) from e

    verified = None
    try:
        after = client.get_summary(meeting_id)
        verified = bool(summary_text(after.get("result")).strip())
    except MeetilyError:
        pass
    record = {
        "meeting_id": meeting_id,
        "at": datetime.now(timezone.utc).isoformat(),
        "backup": backup.relative_to(run_dir).as_posix() if backup else None,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "fingerprint": fingerprint(text),
        "chars": len(text),
        "title": title,
        "read_back_ok": verified,
    }
    if rename:
        record["renamed_to"] = maybe_rename(client, meeting_id, title, log)
    if delete_screenshots:
        shutil.rmtree(run_dir / "images", ignore_errors=True)
        record["screenshots_deleted"] = True
        log("screenshots deleted (setting)")
    (run_dir / "published.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    log("done" + ("" if verified is not False else " (warning: read-back came back empty)"))
    return record


def published_meetings(data_dir: Path) -> set[str]:
    """Meeting ids we've already written to. Step (e) uses this so the summary.completed that
    our own PUT may trigger doesn't start another round."""
    out = set()
    for f in Path(data_dir).glob("*/published.json"):
        try:
            out.add(json.loads(f.read_text("utf-8"))["meeting_id"])
        except (ValueError, KeyError, OSError):
            pass
    return out
