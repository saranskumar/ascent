"""Step (e): Meetily webhooks -> the Visual Context Summary workflow.

The pattern the catalog requires: Subscribe -> Verify (HMAC) -> Deduplicate (event_id) ->
Fetch -> Act, and keep working when Meetily is offline.

* Subscribe: one webhook for our events, kept in data/webhook.json (id + hmac_secret, which
  Meetily shows only once), so a restart reuses it instead of needing a new approval.
* Verify: `X-Meetily-Signature: sha256=<hex HMAC-SHA256 of "{timestamp}.{raw body}">` with the
  registration's hmac_secret, plus a timestamp window against replays.
* Deduplicate: every accepted event is written to data/events/<event_id>.json before we ack,
  so at-least-once redelivery (up to 6 attempts) and restarts never run an event twice.
* Fetch/Act: payloads are thin; a single worker thread fetches what it needs and acts, in
  delivery order. The HTTP handler acks within Meetily's 5 s budget and never does the work.
* Offline: subscription retries until Meetily is reachable; an event whose handler hits
  "Meetily offline" is parked and re-queued when Meetily is back.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import queue
import re
import threading
import time
import traceback
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


# ----------------------------------------------------------------------------- act
class Automation:
    """Owns the subscription, the HTTP entry point and the worker. `app` is server.App."""

    def __init__(self, app, url: str, client_factory=MeetilyClient, sleep=time.sleep):
        self.app = app
        self.store = EventStore(app.data / "events")
        self.sub = Subscription(app.data, url, client_factory)
        self.client_factory = client_factory
        self.sleep = sleep
        self.q: queue.Queue = queue.Queue()
        self._started = False
        self.meetily_online = False

    # ---- lifecycle
    def start(self):
        if self._started:
            return
        self._started = True
        for ev in self.store.with_status("queued", "running"):   # unfinished before a restart
            self.q.put(ev)
        threading.Thread(target=self._worker, daemon=True, name="vcs-events").start()
        threading.Thread(target=self._subscriber, daemon=True, name="vcs-subscribe").start()

    def _subscriber(self):
        """Keep the subscription alive; when Meetily comes back, re-queue parked events."""
        delay = 5
        while True:
            try:
                st = self.sub.ensure()
                if not self.meetily_online:
                    for ev in self.store.with_status("offline"):
                        self.store.update(ev["event_id"], "queued", "Meetily is back; retrying")
                        self.q.put(ev)
                self.meetily_online = True
                # setup steps pending in Meetily (allowlist/approval): check again soon
                delay = 60 if st.get("state") == "active" else 10
            except MeetilyOffline:
                self.meetily_online = False
                self.sub.state = {"state": "offline", "url": self.sub.url,
                                  "message": "Meetily isn't running; will subscribe when it is."}
                delay = 10
            except Exception as e:  # noqa: BLE001 - never let this thread die
                self.sub.state = {"state": "error", "message": str(e), "url": self.sub.url}
                delay = 30
            self.sleep(delay)

    def status(self) -> dict:
        return {**self.sub.state, "auto_capture": self.app.settings()["auto_capture"],
                "auto_publish": self.app.settings()["auto_publish"],
                "events": [{"event_id": r["event"].get("event_id"), "event": r["event"].get("event"),
                            "meeting_id": meeting_id_of(r["event"]), "status": r.get("status"),
                            "received_at": r.get("received_at"), "log": r.get("log", [])[-6:]}
                           for r in self.store.recent(15)]}

    # ---- HTTP (called by the server for POST /webhook)
    def handle_http(self, body: bytes, headers) -> tuple[int, str]:
        secret = self.sub.secret
        if not secret:
            return 503, "not subscribed"
        if not verify_signature(secret, headers.get(TIMESTAMP_HEADER, ""), body,
                                headers.get(SIGNATURE_HEADER, "")):
            return 401, "invalid signature"
        try:
            event = json.loads(body.decode("utf-8"))
            if not isinstance(event, dict) or not _EVENT_ID.match(str(event.get("event_id") or "")):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return 400, "invalid body"
        if self.store.accept(event):
            self.q.put(event)
            return 200, "accepted"
        return 200, "duplicate"                 # ack so Meetily stops retrying

    # ---- worker
    def _worker(self):
        while True:
            self.handle_one(self.q.get())

    def handle_one(self, event: dict) -> None:
        eid = event.get("event_id")
        self.store.update(eid, "running")
        try:
            outcome = self.process(event, lambda m: self.store.update(eid, log=m))
            self.store.update(eid, "done", outcome)
        except MeetilyOffline:
            self.meetily_online = False          # the subscriber re-queues it when Meetily is back
            self.store.update(eid, "offline", "Meetily went offline; will retry when it's back")
        except Exception as e:  # noqa: BLE001 - one bad event must not stop the worker
            traceback.print_exc()
            self.store.update(eid, "failed", f"{type(e).__name__}: {e}")

    def process(self, event: dict, log) -> str:
        kind, mid = event.get("event"), meeting_id_of(event)
        if kind == "recording.started":
            return self.on_started(mid, event.get("occurred_at"), log)
        if kind in END_EVENTS:
            return self.on_ended(mid, kind, log)
        if kind in SUMMARY_EVENTS:
            return self.on_summary(mid, kind, log)
        return f"ignored {kind}"

    def _retry(self, fn, log, what: str, attempts: int = 4):
        """Retry transient Meetily states (409 while still recording/persisting, offline)."""
        for i in range(attempts):
            try:
                return fn()
            except MeetilyError as e:
                transient = isinstance(e, MeetilyOffline) or e.status == 409
                if not transient or i == attempts - 1:
                    raise
                wait = (3, 10, 30)[min(i, 2)]
                log(f"{what}: {e.code or e.status or 'offline'}, retrying in {wait}s")
                self.sleep(wait)

    # recording.started -> start capturing the remembered window
    def on_started(self, mid: str | None, occurred_at: str | None, log) -> str:
        app = self.app
        if not mid:
            st = self._retry(lambda: self.client_factory().recording(), log, "recording state")
            mid = (st or {}).get("active_meeting_id")
        if app.recorder is not None and app.recorder.running:
            app.attach_recording(app.capture_run, mid, occurred_at)
            return f"already capturing ({app.capture_run}); linked it to {mid}"
        if not app.settings()["auto_capture"]:
            return "auto capture is off in Settings"
        hwnd = app.find_watch_window()
        if hwnd is None:
            return "no window to watch: pick one once on the New run page"
        app.start_capture(hwnd)
        app.attach_recording(app.capture_run, mid, occurred_at)
        log(f"capturing '{app.recorder.title}' for {mid}")
        return f"capture started ({app.capture_run})"


    # recording.stopped (or failed) -> stop capture, extract, then summarize + write back
    def on_ended(self, mid: str | None, kind: str, log) -> str:
        app = self.app
        if app.recorder is None:
            return "no capture running"
        run = app.capture_run
        linked = app.meta(app.run_dir(run)).get("recording_meeting_id")
        if mid and linked and linked != mid:
            return f"capture {run} belongs to {linked}, not {mid}; left running"
        res = app.stop_capture()
        log(f"stopped capture after {res['seconds']:.0f}s; extracting screenshots")
        job = app.wait_job(res["job"])
        if job["status"] != "done":
            raise RuntimeError(f"extraction failed: {job['error']}")
        log(job["log"][-2] if len(job["log"]) > 1 else "screenshots extracted")
        mid = mid or linked
        if kind != "recording.stopped":
            return f"{run}: recording ended with {kind}; screenshots kept, no summary"
        if not mid:
            return f"{run}: screenshots extracted; no meeting id to summarise"
        # Decided Oct 2 (live test): Meetily only sends summary.completed when it generates its
        # own summary, which hands-off recordings don't get, so we write on stop and guard after.
        return self.summarize_and_publish(run, mid, log)

    # summary.completed / summary.failed -> keep our summary in place
    def on_summary(self, mid: str | None, kind: str, log) -> str:
        app = self.app
        if not mid:
            return "event has no meeting id"
        run = app.run_for_meeting(mid)
        if run is None:
            return "no screen capture for this meeting; Meetily's summary left as is"
        pub = self._published(run, mid)
        if pub:
            if self._ours_is_current(mid, pub):
                return "Meetily shows our summary; nothing to do"
            log("Meetily replaced our summary; putting ours back (its version is backed up)")
            return self._publish(run, mid, log)
        if self._summary_ready(run, mid):
            log("our summary is ready but wasn't written yet; writing it now")
            return self._publish(run, mid, log)
        return self.summarize_and_publish(run, mid, log)

    # ---- shared steps
    def summarize_and_publish(self, run: str, mid: str, log) -> str:
        app = self.app
        if not (app.run_dir(run) / "screenshots.json").exists():
            return f"{run} has no screenshots; nothing to add"
        self._wait_transcript(mid, log)
        offset = app.suggest_offset(run, mid).get("offset") or 0.0
        log(f"summarising {mid} with screens from {run} (offset {offset}s)")
        job = app.wait_job(app.start_summarize(run, mid, offset, None))
        if job["status"] != "done":
            err = job["error"] or ""
            if "offline" in err.lower() or "reach meetily" in err.lower():
                raise MeetilyOffline(None, err, err)
            raise RuntimeError(f"summary failed: {err}")
        return self._publish(run, mid, log)

    def _publish(self, run: str, mid: str, log) -> str:
        app = self.app
        if not app.settings()["auto_publish"]:
            return f"summary ready in {run}; auto write-back is off, write it from the run page"
        if not self.client_factory().write_status()["ok"]:
            return f"summary ready in {run}; no usable write key, so not written to Meetily"
        job = app.wait_job(app.start_publish(run, mid))
        if job["status"] != "done":
            err = job["error"] or ""
            if "still generating" in err:
                return (f"summary ready in {run}; Meetily is generating its own, ours goes in "
                        f"when its summary event arrives")
            raise RuntimeError(f"write-back failed: {err}")
        return f"summary written to Meetily (backup in {run}/backups)"

    def _wait_transcript(self, mid: str, log, attempts: int = 4):
        """The transcript is persisted just after recording.stopped; wait until it has text."""
        for i in range(attempts):
            t = self._retry(lambda: self.client_factory().get_transcript(mid), log, "transcript")
            if any((s.get("text") or "").strip() for s in (t or {}).get("segments") or []):
                return
            if i < attempts - 1:
                wait = (3, 10, 20)[min(i, 2)]
                log(f"transcript has no text yet; checking again in {wait}s")
                self.sleep(wait)
        log("transcript is still empty; summarising from the screens alone")

    def _published(self, run: str, mid: str) -> dict | None:
        f = self.app.run_dir(run) / "published.json"
        try:
            rec = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        return rec if rec.get("meeting_id") == mid else None

    def _summary_ready(self, run: str, mid: str) -> bool:
        d = self.app.run_dir(run)
        try:
            meta = json.loads((d / "summary.meta.json").read_text("utf-8"))
        except (OSError, ValueError):
            return False
        return (d / "summary.md").exists() and meta.get("meeting_id") == mid

    def _ours_is_current(self, mid: str, pub: dict) -> bool:
        """Does Meetily still show the summary we wrote? Compared by content fingerprint,
        because Meetily reformats the Markdown it stores."""
        from .writeback import fingerprint, split_title, summary_text
        cur = self._retry(lambda: self.client_factory().get_summary(mid), self._nolog, "summary")
        ours = pub.get("fingerprint")
        if not ours:                                   # record from before fingerprints
            run = self.app.run_for_meeting(mid)
            md = self.app.run_dir(run) / "summary.md" if run else None
            if not md or not md.exists():
                return False
            ours = fingerprint(split_title(md.read_text("utf-8"))[1])
        return fingerprint(summary_text((cur or {}).get("result"))) == ours

    @staticmethod
    def _nolog(_msg):
        pass
