"""Meetily webhooks: Subscribe -> Verify (HMAC) -> Deduplicate (event_id) -> Fetch -> Act.

* Subscribe: one webhook for our events, kept in data/webhook.json (id + hmac_secret, which
  Meetily shows only once), so a restart reuses it instead of needing a new approval.
* Verify: `X-Meetily-Signature: sha256=<hex HMAC-SHA256 of "{timestamp}.{raw body}">` with the
  registration's hmac_secret, plus a timestamp window against replays.
* Deduplicate: every accepted event is written to data/events/<event_id>.json before we ack,
  so at-least-once redelivery and restarts never run an event twice.
* The HTTP handler (webserver.py) only acks; automation.py acts on the events in order.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .meetily_client import MeetilyClient, MeetilyError, MeetilyOffline

SIGNATURE_HEADER = "X-Meetily-Signature"
TIMESTAMP_HEADER = "X-Meetily-Timestamp"
MAX_SKEW = 300                      # seconds between X-Meetily-Timestamp and our clock

EVENTS = ["recording.started", "recording.stopped", "recording.failed", "recording.error",
          "recording.stop_failed", "summary.completed", "summary.failed"]
END_EVENTS = {"recording.stopped", "recording.failed", "recording.error", "recording.stop_failed"}
SUMMARY_EVENTS = {"summary.completed", "summary.failed"}

_EVENT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------------- verify
def verify_signature(secret: str, timestamp: str, body: bytes, header: str, *,
                     now: float | None = None, max_skew: int = MAX_SKEW) -> bool:
    """HMAC-SHA256 over b"{timestamp}." + raw body, hex, sent as "sha256=<hex>".
    Constant-time compare; rejects missing parts and timestamps outside the window."""
    if not (secret and timestamp and header):
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((time.time() if now is None else now) - ts) > max_skew:
        return False
    sig = header[len("sha256="):] if header.startswith("sha256=") else header
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig.strip().lower())


def meeting_id_of(event: dict) -> str | None:
    if event.get("meeting_id"):
        return str(event["meeting_id"])
    res = event.get("resource") or {}
    return str(res["id"]) if res.get("id") else None


# ----------------------------------------------------------------------------- dedup/log
class EventStore:
    """data/events/<event_id>.json: the durable dedup set and the event log in one."""

    def __init__(self, folder: Path):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _file(self, event_id: str) -> Path:
        if not _EVENT_ID.match(event_id or ""):
            raise ValueError("bad event_id")
        return self.dir / f"{event_id.replace(':', '_')}.json"

    def accept(self, event: dict) -> bool:
        """Record a new event; False if this event_id was seen before (duplicate delivery)."""
        f = self._file(str(event.get("event_id") or ""))
        with self._lock:
            if f.exists():
                return False
            f.write_text(json.dumps({"event": event, "status": "queued", "received_at": now_iso(),
                                     "log": []}, indent=2), "utf-8")
            return True

    def update(self, event_id: str, status: str | None = None, log: str | None = None, **extra):
        f = self._file(event_id)
        with self._lock:
            try:
                rec = json.loads(f.read_text("utf-8"))
            except (OSError, ValueError):
                return
            if status:
                rec["status"] = status
            if log:
                rec["log"].append(f"{datetime.now().strftime('%H:%M:%S')} {log}")
            rec.update(extra, updated_at=now_iso())
            f.write_text(json.dumps(rec, indent=2), "utf-8")

    def records(self) -> list[dict]:
        out = []
        for f in self.dir.glob("*.json"):
            try:
                out.append(json.loads(f.read_text("utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda r: r.get("received_at", ""))

    def with_status(self, *statuses: str) -> list[dict]:
        return [r["event"] for r in self.records() if r.get("status") in statuses]

    def recent(self, n: int = 20) -> list[dict]:
        return list(reversed(self.records()))[:n]


# ----------------------------------------------------------------------------- subscribe
class Subscription:
    def __init__(self, data_dir: Path, url: str, client_factory=MeetilyClient):
        self.file = Path(data_dir) / "webhook.json"
        self.url = url
        self.client_factory = client_factory
        self.state = {"state": "starting", "message": "Connecting to Meetily…"}

    def _load(self) -> dict:
        try:
            return json.loads(self.file.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    @property
    def secret(self) -> str | None:
        return self._load().get("hmac_secret")

    def ensure(self) -> dict:
        """Verify the stored webhook or register a new one. Raises MeetilyOffline."""
        c = self.client_factory()
        saved = self._load()
        if saved.get("id") and saved.get("url") == self.url and \
                sorted(saved.get("events") or []) == sorted(EVENTS) and saved.get("hmac_secret"):
            try:
                return self._set(c.get_webhook(saved["id"]), saved["id"])
            except MeetilyOffline:
                raise
            except MeetilyError as e:
                if e.status != 404:
                    return self._error(e)
        elif saved.get("id"):
            try:
                c.delete_webhook(saved["id"])       # URL or events changed: replace it
            except MeetilyOffline:
                raise
            except MeetilyError:
                pass
        try:
            reg = c.create_webhook(self.url, EVENTS)
        except MeetilyOffline:
            raise
        except MeetilyError as e:
            return self._error(e)
        self.file.write_text(json.dumps({
            "id": reg["id"], "url": self.url, "events": EVENTS, "hmac_secret": reg["hmac_secret"],
            "created_at": now_iso()}, indent=2), "utf-8")
        try:
            info = c.get_webhook(reg["id"])
        except MeetilyError:
            info = reg
        return self._set(info, reg["id"])

    def _set(self, info: dict, wid: str) -> dict:
        approval = (info or {}).get("approval_state") or "unknown"
        if approval == "pending":
            msg = ("Waiting for approval: in Meetily open Settings > Integrations and Allow this "
                   "destination ('Waiting for you', or Advanced > Destinations).")
            state = "pending"
        elif approval in ("allowed", "approved"):
            msg, state = "Receiving Meetily events.", "active"
        else:
            msg, state = f"Webhook registered (approval: {approval}).", "registered"
        self.state = {"state": state, "message": msg, "id": wid, "approval_state": approval,
                      "url": self.url}
        return self.state

    def _error(self, e: MeetilyError) -> dict:
        text = str(e)
        if e.code == "webhooks_disabled":
            msg = "Turn on 'Outgoing (webhooks)' in Meetily: Settings > Integrations > Advanced."
            state = "disabled"
        elif "not allowed" in text.lower() or e.code == "destination_not_allowed":
            host = self.url.split("//", 1)[-1].split("/", 1)[0]
            msg = (f"Allow {host} in Meetily: Settings > Integrations > Advanced > Outgoing "
                   f"(webhooks) > Local targets.")
            state = "local_target_needed"
        else:
            msg, state = f"Couldn't register the webhook: {text}", "error"
        self.state = {"state": state, "message": msg, "url": self.url}
        return self.state

    def remove(self):
        saved = self._load()
        if saved.get("id"):
            try:
                self.client_factory().delete_webhook(saved["id"])
            except MeetilyError:
                pass
        self.file.unlink(missing_ok=True)
