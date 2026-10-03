"""Local web UI backend (stdlib only). Binds to 127.0.0.1; serves vcs/web and a small JSON API.

  python cli.py serve            ->  http://127.0.0.1:8765

Safety: loopback only; the Host header must be localhost; every write (POST/PUT/DELETE) needs
the custom `X-VCS: 1` header, so a web page on another origin can't drive it. Secrets
(GEMINI_API_KEY, MEETILY_PRO_TOKEN) are read from the environment and never sent to the browser.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .extractor import Params, extract
from .meetily_client import MeetilyClient, MeetilyError, MeetilyOffline
from .prompts import build_system_prompt
from .summarizer import DEFAULT_MODEL, SummaryError, summarize
from .writeback import WritebackError, fingerprint, publish, summary_text
from . import speaker_names
from .transcript import build_input, speakers

WEB = Path(__file__).parent / "web"
FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "transcript.json"
SLUG = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
MAX_UPLOAD = 2 * 1024**3

DEFAULT_SETTINGS = {
    "model": DEFAULT_MODEL,
    "interval": Params.interval,
    "hash_threshold": Params.hash_threshold,
    "drift_threshold": Params.drift_threshold,
    "min_dwell": Params.min_dwell,
    # Decided Oct 2: keep screenshots after the summary is written (this turns deletion on).
    "delete_screenshots_after_summary": False,
    # Live capture: the 1 fps video is deleted after screenshots are extracted unless kept.
    "keep_capture_video": False,
    # Last window picked for capture ({hwnd, title, process}); step (e) re-finds it by these.
    "watch": {},
    # Step (e) automation on Meetily webhooks.
    "auto_capture": True,       # recording.started -> capture a window
    "ask_window": True,         # ...chosen in a popup (Skip = no capture); off = the remembered window
    "ask_generate": True,       # recording ended: popup "generate the summary?" (off = generate at once)
    "auto_publish": True,       # ...write our summary into Meetily if it has none, else ask
    "open_web_prompt": True,    # open the web UI on the run when a decision is waiting there
}


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


class Jobs:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def start(self, kind: str, fn, run: str | None = None) -> dict:
        job = {"id": uuid.uuid4().hex[:10], "kind": kind, "run": run, "status": "running",
               "log": [], "error": None, "started": time.time()}
        with self.lock:
            self.jobs[job["id"]] = job

        def log(msg):
            job["log"].append(str(msg))

        def work():
            try:
                fn(log)
                job["status"] = "done"
            except (SummaryError, MeetilyError, WritebackError, FileNotFoundError, ValueError) as e:
                job["status"], job["error"] = "failed", str(e)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                job["status"], job["error"] = "failed", f"{type(e).__name__}: {e}"

        threading.Thread(target=work, daemon=True).start()
        return job


class App:
    def __init__(self, data_dir: Path):
        self.data = Path(data_dir).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.jobs = Jobs()
        self.automation = None          # webhooks.Automation when serving with webhooks
        self.recorder = None            # WindowRecorder while capturing
        self.capture_run: str | None = None
        self.capture_lock = threading.Lock()
        self.ui_seen = 0.0              # last time the web UI polled for decisions
        self._resolving: set[str] = set()

    # ---- settings
    def settings(self) -> dict:
        f = self.data / "settings.json"
        s = dict(DEFAULT_SETTINGS)
        if f.exists():
            try:
                s.update(json.loads(f.read_text("utf-8")))
            except ValueError:
                pass
        return s

    def save_settings(self, new: dict) -> dict:
        s = self.settings()
        for k, default in DEFAULT_SETTINGS.items():
            if k in new and isinstance(new[k], type(default)):
                s[k] = new[k]
        (self.data / "settings.json").write_text(json.dumps(s, indent=2), "utf-8")
        return s

    # ---- runs
    def run_dir(self, run_id: str) -> Path:
        if not SLUG.match(run_id):
            raise ValueError("bad run id")
        d = (self.data / run_id).resolve()
        if d.parent != self.data:
            raise ValueError("bad run id")
        return d

    def meta(self, d: Path) -> dict:
        f = d / "meta.json"
        try:
            return json.loads(f.read_text("utf-8")) if f.exists() else {}
        except ValueError:
            return {}

    def run_state(self, run_id: str) -> dict:
        """Where a run is in the pipeline: capturing -> extracting -> ready (or failed)."""
        d = self.run_dir(run_id)
        jobs = [j for j in list(self.jobs.jobs.values()) if j.get("run") == run_id]
        ext = [j for j in jobs if j["kind"] == "extract"]
        if (d / "screenshots.json").exists():
            state, error = "ready", None
        elif self.capture_run == run_id and self.recorder is not None:
            state, error = "capturing", None
        elif any(j["status"] == "running" for j in ext):
            state, error = "extracting", None
        elif ext and ext[-1]["status"] == "failed":
            state, error = "failed", ext[-1]["error"]
        else:
            state, error = "pending", None
        busy = [j for j in jobs if j["status"] == "running"]
        return {"state": state, "error": error, "busy": busy[-1]["kind"] if busy else None,
                "jobs": [{"id": j["id"], "kind": j["kind"], "status": j["status"], "error": j["error"],
                          "log": j["log"][-12:]} for j in jobs[-6:]]}

    def runs(self) -> list[dict]:
        out = []
        for d in self.data.iterdir():
            if not d.is_dir():
                continue
            f = d / "screenshots.json"
            m = self.meta(d)
            if not f.exists():                      # still capturing / extracting: show it anyway
                if not m and self.capture_run != d.name:
                    continue
                out.append({"id": d.name, "created": m.get("created") or d.stat().st_mtime, "duration": None,
                            "screens": 0, "diagrams": 0, "has_summary": False,
                            "meeting_id": m.get("meeting_id"), "source": m.get("source_name"),
                            "state": self.run_state(d.name)["state"]})
                continue
            try:
                doc = json.loads(f.read_text("utf-8"))
            except ValueError:
                continue
            shots = doc.get("screenshots", [])
            out.append({"id": d.name, "created": m.get("created") or f.stat().st_mtime,
                        "duration": doc.get("duration"), "screens": len(shots),
                        "diagrams": sum(1 for s in shots if s.get("type") == "diagram"),
                        "has_summary": (d / "summary.md").exists(),
                        "meeting_id": m.get("meeting_id"), "source": m.get("source_name"),
                        "state": "ready"})
        return sorted(out, key=lambda r: r["created"], reverse=True)

    def run_detail(self, run_id: str) -> dict:
        d = self.run_dir(run_id)
        f = d / "screenshots.json"
        if not f.exists():
            if not d.is_dir():
                raise FileNotFoundError("run not found")
            return {"id": run_id, "backups": [], "published": None, "has_images": False,
                    "meta": self.meta(d), "duration": None, "screenshots": [], "summary": None,
                    "summary_meta": None, **self.run_state(run_id)}
        doc = json.loads(f.read_text("utf-8"))
        summ = d / "summary.md"
        pub = d / "published.json"
        backups = sorted(p.name for p in (d / "backups").glob("*.json")) if (d / "backups").is_dir() else []
        return {"id": run_id, "backups": backups,
                "published": json.loads(pub.read_text("utf-8")) if pub.exists() else None,
                "has_images": (d / "images").is_dir(), "meta": self.meta(d), "duration": doc.get("duration"),
                "screenshots": doc.get("screenshots", []),
                "summary": summ.read_text("utf-8") if summ.exists() else None,
                "summary_meta": json.loads((d / "summary.meta.json").read_text("utf-8"))
                if (d / "summary.meta.json").exists() else None,
                **self.run_state(run_id)}

    def timeline(self, run_id: str, meeting_id: str | None, offset: float) -> dict:
        """Speech segments and screens on one clock (the meeting audio's). Screen times in a run
        are capture-relative, so `offset` (capture start - recording start) shifts them."""
        from .transcript import segment_start, speaker_key, speaker_label
        d = self.run_dir(run_id)
        doc = json.loads((d / "screenshots.json").read_text("utf-8"))
        segs = self.transcript(meeting_id).get("segments") or []
        names = speaker_names.load(self.data, meeting_id)
        multi = len({speaker_key(x) for x in segs if speaker_key(x) is not None}) > 1
        speech, last = [], 0.0
        for seg in segs:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            t = segment_start(seg)
            t = last if t is None else t
            last = t
            end = seg.get("audio_end_time")
            speech.append({"t": round(t, 2), "end": round(float(end), 2) if isinstance(end, (int, float)) else None,
                           "who": speaker_label(seg, names, multi), "text": text})
        screens = [{"id": s["id"], "t": round(s["start"] + offset, 2), "end": round(s["end"] + offset, 2),
                    "type": s.get("type"), "image": s["image"], "text": s.get("text") or "",
                    "description": s.get("description") or ""} for s in doc.get("screenshots", [])]
        ends = [x["end"] for x in speech if x["end"]] + [x["end"] for x in screens] + [x["t"] for x in speech]
        return {"run": run_id, "meeting_id": meeting_id or "fixture", "offset": offset,
                "duration": round(max(ends, default=0.0), 2), "speech": speech, "screens": screens}

    def set_meta(self, run_id: str, patch: dict) -> dict:
        d = self.run_dir(run_id)
        m = self.meta(d)
        if "meeting_id" in patch:
            m["meeting_id"] = patch["meeting_id"] or None
        if "offset" in patch:
            m["offset"] = float(patch["offset"] or 0)
        (d / "meta.json").write_text(json.dumps(m, indent=2), "utf-8")
        return m

    def delete_run(self, run_id: str):
        d = self.run_dir(run_id)
        if not d.is_dir():
            raise FileNotFoundError("run not found")
        if self.capture_run == run_id:
            raise ValueError("this run is still capturing; stop the capture first")
        shutil.rmtree(d)

    # ---- meetily
    def transcript(self, meeting_id: str | None) -> dict:
        if not meeting_id or meeting_id == "fixture":
            return json.loads(FIXTURE.read_text("utf-8"))
        return MeetilyClient().get_transcript(meeting_id)

    def status(self) -> dict:
        from .summarizer import local_model
        s = {"model": local_model() or self.settings()["model"], "summary_local": bool(local_model()),
             "gemini_key": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
             "write_key": bool(os.environ.get("MEETILY_PRO_TOKEN", "").strip()),
             "meetily": {"online": False}}
        try:
            c = MeetilyClient(timeout=3)
            s["meetily"] = {"online": True, "recording": c.recording()}
            try:
                s["meetily"]["whoami"] = c.whoami()
            except MeetilyError:
                pass
            s["write"] = c.write_status()
            s["write_key"] = s["write"]["ok"]
            if self.automation is not None:
                s["automation"] = {k: self.automation.sub.state.get(k) for k in ("state", "message")}
        except MeetilyOffline as e:
            s["meetily"]["error"] = "Meetily isn't running, or its Automation API is off."
            s["meetily"]["detail"] = str(e)
        except MeetilyError as e:
            s["meetily"]["error"] = str(e)
        return s

    # ---- actions
    def extract_params(self) -> Params:
        st = self.settings()
        return Params(interval=float(st["interval"]), hash_threshold=int(st["hash_threshold"]),
                      drift_threshold=int(st["drift_threshold"]), min_dwell=float(st["min_dwell"]))

    # ---- live capture (step d)
    def capture_status(self) -> dict:
        r = self.recorder
        watch = self.settings().get("watch") or {}
        if r is None:
            return {"active": False, "watch": watch}
        return {"active": r.running, "run": self.capture_run, **r.info(), "watch": watch}

    def start_capture(self, hwnd: int) -> dict:
        from .capture import WindowRecorder
        from .windows import is_window, window_info
        with self.capture_lock:
            if self.recorder is not None and self.recorder.running:
                raise ValueError("a capture is already running; stop it first")
            if not is_window(hwnd):
                raise ValueError("that window no longer exists; refresh the list")
            w = window_info(hwnd)
            run_id = "capture-" + datetime.now().strftime("%m%d-%H%M%S")
            d = self.run_dir(run_id)
            rec = WindowRecorder(hwnd, d, title=w["title"], process=w["process"])
            rec.start()
            self.recorder, self.capture_run = rec, run_id
            self.save_settings({"watch": {"hwnd": w["hwnd"], "title": w["title"],
                                          "process": w["process"]}})
            (d / "meta.json").write_text(json.dumps({
                "created": time.time(), "source_name": "Live capture: " + w["title"],
                "offset": 0.0, "meeting_id": None, "window": w}, indent=2), "utf-8")
            return self.capture_status()

    def stop_capture(self) -> dict:
        with self.capture_lock:
            rec, run_id = self.recorder, self.capture_run
            if rec is None:
                raise ValueError("no capture is running")
            info = rec.stop()
            self.recorder, self.capture_run = None, None
        d = self.run_dir(run_id)
        m = self.meta(d)
        m.update({"capture_started_at": info["started_at"], "capture_ended_at": info["ended_at"],
                  "capture_seconds": info["seconds"]})
        (d / "meta.json").write_text(json.dumps(m, indent=2), "utf-8")
        if not info["frames"]:
            shutil.rmtree(d, ignore_errors=True)
            raise ValueError(info["error"] or "nothing was captured")
        params, keep = self.extract_params(), bool(self.settings()["keep_capture_video"])

        def work(log):
            log(f"captured {info['seconds']:.0f}s of '{info['title']}'"
                + (" (window was closed)" if info["window_closed"] else ""))
            extract(rec.video, d, params, log=log)
            if not keep:
                rec.video.unlink(missing_ok=True)
                log("capture video deleted (screenshots kept)")

        job = self.jobs.start("extract", work, run_id)
        return {"run": run_id, "job": job["id"], **info}

    # ---- overwrite decisions (Meetily already has a summary when ours is ready)
    def ui_recently_seen(self, within: float = 15.0) -> bool:
        return time.time() - self.ui_seen < within

    def pending_overwrites(self) -> list[dict]:
        out = []
        for d in self.data.iterdir():
            f = d / "pending_overwrite.json"
            if d.is_dir() and f.exists() and d.name not in self._resolving:
                try:
                    out.append(json.loads(f.read_text("utf-8")))
                except ValueError:
                    continue
        return sorted(out, key=lambda r: r.get("at", ""))

    def resolve_overwrite(self, run_id: str, action: str) -> dict:
        d = self.run_dir(run_id)
        f = d / "pending_overwrite.json"
        if not f.exists():
            raise FileNotFoundError("nothing is waiting for a decision on this run")
        mid = json.loads(f.read_text("utf-8"))["meeting_id"]
        if action == "keep":
            try:
                cur = MeetilyClient().get_summary(mid)
                fp = fingerprint(summary_text(cur.get("result")))
            except MeetilyError:
                fp = None
            (d / "kept_meetily.json").write_text(json.dumps({
                "meeting_id": mid, "fingerprint": fp, "at": datetime.now(timezone.utc).isoformat()},
                indent=2), "utf-8")
            f.unlink()
            return {"ok": True}
        if action != "overwrite":
            raise ValueError("action must be overwrite or keep")
        job = self.start_publish(run_id, mid)          # backs up Meetily's summary first
        self._resolving.add(run_id)

        def settle():
            j = self.wait_job(job)
            if j["status"] == "done":
                f.unlink(missing_ok=True)
            self._resolving.discard(run_id)

        threading.Thread(target=settle, daemon=True).start()
        return {"job": job["id"]}

    # ---- helpers for the webhook automation (step e)
    def find_watch_window(self) -> int | None:
        """The window picked last time: same handle if it's still that app, else the same
        title, else the only window of that app. Browser tab titles change, hence the tiers."""
        from .windows import is_window, list_windows, window_info
        watch = self.settings().get("watch") or {}
        if not watch:
            return None
        hwnd = watch.get("hwnd")
        if hwnd and is_window(hwnd) and window_info(hwnd)["process"] == watch.get("process"):
            return int(hwnd)
        wins = [w for w in list_windows() if not w["minimized"]]
        for w in wins:
            if w["title"] == watch.get("title") and w["process"] == watch.get("process"):
                return w["hwnd"]
        same_app = [w for w in wins if w["process"] == watch.get("process")]
        return same_app[0]["hwnd"] if len(same_app) == 1 else None

    def attach_recording(self, run_id: str | None, meeting_id: str | None, started_at: str | None):
        """Link a capture run to the Meetily recording (and its exact start time)."""
        if not run_id:
            return
        d = self.run_dir(run_id)
        m = self.meta(d)
        m.update({"recording_meeting_id": meeting_id, "recording_started_at": started_at,
                  "meeting_id": meeting_id or m.get("meeting_id")})
        (d / "meta.json").write_text(json.dumps(m, indent=2), "utf-8")

    def run_for_meeting(self, meeting_id: str) -> str | None:
        for r in self.runs():                       # newest first
            m = self.meta(self.run_dir(r["id"]))
            if m.get("recording_meeting_id") == meeting_id:
                return r["id"]
        return None

    def wait_job(self, job, timeout: float = 1800) -> dict:
        jid = job["id"] if isinstance(job, dict) else job
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            j = self.jobs.jobs.get(jid)
            if j is None or j["status"] != "running":
                return j or {"status": "failed", "error": "job vanished", "log": []}
            time.sleep(0.5)
        return {"status": "failed", "error": "timed out", "log": []}

    def recording_started_at(self, meeting_id: str) -> str | None:
        """occurred_at of this meeting's recording.started webhook, if we received one."""
        if self.automation is None:
            return None
        from .webhooks import meeting_id_of
        for r in self.automation.store.records():
            ev = r.get("event") or {}
            if ev.get("event") == "recording.started" and meeting_id_of(ev) == meeting_id:
                return ev.get("occurred_at")
        return None

    def suggest_offset(self, run_id: str, meeting_id: str | None) -> dict:
        """Seconds from the meeting's recording start (Meetily's audio t=0) to the capture start.
        Exact when we have recording.started's occurred_at; otherwise estimated: Meetily's
        meeting created_at is the STOP time (seen live Oct 2), so start ~ created_at minus the
        transcript length (off by the trailing silence, a few seconds)."""
        m = self.meta(self.run_dir(run_id))
        cap = parse_ts(m.get("capture_started_at"))
        if not cap or not meeting_id or meeting_id == "fixture":
            return {"offset": None}
        start, basis = None, "recording.started"
        if m.get("recording_meeting_id") == meeting_id:
            start = parse_ts(m.get("recording_started_at"))
        if start is None:
            start = parse_ts(self.recording_started_at(meeting_id))
        if start is None:
            from datetime import timedelta
            c = MeetilyClient()
            end = parse_ts((c.get_meeting(meeting_id) or {}).get("created_at"))
            segs = (c.get_transcript(meeting_id) or {}).get("segments") or []
            length = max((s.get("audio_end_time") or 0 for s in segs), default=0)
            if end is None:
                return {"offset": None}
            start, basis = end - timedelta(seconds=float(length)), "estimated (meeting end - transcript length)"
        return {"offset": round((cap - start).total_seconds(), 1), "basis": basis}

    def start_extract(self, run_id: str, video: Path, source_name: str) -> dict:
        params = self.extract_params()
        d = self.run_dir(run_id)
        (d / "meta.json").write_text(json.dumps({
            "created": time.time(), "source_name": source_name, "offset": 0.0,
            "meeting_id": None}, indent=2), "utf-8")

        def work(log):
            extract(video, d, params, log=log)

        return self.jobs.start("extract", work, run_id)

    def start_summarize(self, run_id: str, meeting_id: str | None, offset: float,
                        model: str | None) -> dict:
        d = self.run_dir(run_id)
        doc = json.loads((d / "screenshots.json").read_text("utf-8"))
        model = model or self.settings()["model"]

        def work(log):
            log("fetching transcript...")
            segs = self.transcript(meeting_id).get("segments") or []
            from .summarizer import local_model
            if local_model():
                model = local_model()
            log(f"{len(segs)} segments; calling {model}...")
            info: dict = {}
            names = speaker_names.load(self.data, meeting_id)
            if names:
                log(f"using {len(names)} speaker name(s)")
            text = summarize(segs, doc, d, offset=offset, model=model, info=info, log=log,
                             speaker_names=names)
            (d / "summary.md").write_text(text, "utf-8")
            (d / "summary.meta.json").write_text(json.dumps({
                "model": info.get("model", model), "meeting_id": meeting_id or "fixture", "offset": offset,
                "at": datetime.now(timezone.utc).isoformat()}, indent=2), "utf-8")
            self.set_meta(run_id, {"meeting_id": None if meeting_id == "fixture" else meeting_id,
                                   "offset": offset})
            log("summary saved (not written to Meetily)")

        return self.jobs.start("summarize", work, run_id)

    def _publishable(self, run_id: str, meeting_id: str | None) -> tuple[Path, str]:
        d = self.run_dir(run_id)
        if not meeting_id or meeting_id == "fixture":
            raise ValueError("pick a real Meetily meeting; the demo transcript can't be written back")
        sm = d / "summary.meta.json"
        if not (d / "summary.md").exists() or not sm.exists():
            raise ValueError("generate a summary first")
        made_for = json.loads(sm.read_text("utf-8")).get("meeting_id")
        if made_for != meeting_id:
            raise ValueError(f"this summary was generated from a different transcript "
                             f"({made_for}); regenerate it for this meeting before writing")
        return d, (d / "summary.md").read_text("utf-8")

    def publish_info(self, run_id: str, meeting_id: str | None) -> dict:
        d, text = self._publishable(run_id, meeting_id)
        info = {"meeting_id": meeting_id, "chars": len(text),
                "write": MeetilyClient().write_status(),
                "current": None}
        try:
            cur = MeetilyClient().get_summary(meeting_id)
            info["current"] = {"status": cur.get("status"), "text": summary_text(cur.get("result"))}
        except MeetilyError as e:
            info["current_error"] = "none yet" if e.status == 404 else str(e)
        return info

    def start_publish(self, run_id: str, meeting_id: str | None) -> dict:
        d, text = self._publishable(run_id, meeting_id)
        client = MeetilyClient()
        delete = bool(self.settings()["delete_screenshots_after_summary"])
        return self.jobs.start("publish", lambda log: publish(
            client, meeting_id, text, d, delete_screenshots=delete, log=log), run_id)

    def backup_text(self, run_id: str) -> dict:
        d = self.run_dir(run_id)
        files = sorted((d / "backups").glob("*.json")) if (d / "backups").is_dir() else []
        if not files:
            raise FileNotFoundError("no backup for this run")
        raw = json.loads(files[-1].read_text("utf-8"))
        return {"file": files[-1].name, "status": raw.get("status"),
                "text": summary_text(raw.get("result")), "updated_at": raw.get("updated_at")}

    def meeting_speakers(self, meeting_id: str) -> dict:
        segs = self.transcript(meeting_id).get("segments") or []
        names = speaker_names.load(self.data, meeting_id)
        sp = speakers(segs)
        for x in sp:
            x["name"] = names.get(x["id"], "")
        return {"meeting_id": meeting_id, "speakers": sp}

    def input_preview(self, run_id: str, meeting_id: str | None, offset: float) -> dict:
        d = self.run_dir(run_id)
        doc = json.loads((d / "screenshots.json").read_text("utf-8"))
        segs = self.transcript(meeting_id).get("segments") or []
        text, diagrams = build_input(segs, doc["screenshots"], offset,
                                     speaker_names.load(self.data, meeting_id))
        return {"input": text, "diagrams": len(diagrams), "system": build_system_prompt()}


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "vcs"

    def log_message(self, fmt, *args):  # quieter
        pass

    # ---- helpers
    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path):
        if not path.is_file():
            return self.send_json({"error": "not found"}, 404)
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_jpeg(self, data: bytes | None):
        if not data:
            return self.send_json({"error": "no image"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost", "[::1]")

    def dispatch(self, method: str):
        if not self.host_ok():
            return self.send_json({"error": "bad host"}, 403)
        if method == "POST" and urlparse(self.path).path == "/webhook":
            return self.webhook()
        if method != "GET" and self.headers.get("X-VCS") != "1":
            return self.send_json({"error": "missing X-VCS header"}, 403)
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path.startswith("/api/"):
                return self.api(method, u.path[5:], q)
            if method == "GET":
                rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
                p = (WEB / rel).resolve()
                if WEB.resolve() not in p.parents and p != WEB.resolve():
                    return self.send_json({"error": "not found"}, 404)
                return self.send_file(p)
            self.send_json({"error": "not found"}, 404)
        except FileNotFoundError as e:
            self.send_json({"error": str(e)}, 404)
        except (ValueError, KeyError) as e:
            self.send_json({"error": str(e)}, 400)
        except MeetilyOffline as e:
            self.send_json({"error": str(e), "code": "meetily_offline"}, 502)
        except MeetilyError as e:
            self.send_json({"error": str(e), "code": e.code}, 502)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def webhook(self):
        """Meetily -> us. Authenticated by HMAC (not X-VCS); ack fast, work happens later."""
        a = self.app.automation
        n = int(self.headers.get("Content-Length") or 0)
        if a is None:
            return self.send_json({"error": "automation is off"}, 503)
        if n > 1_000_000:
            return self.send_json({"error": "too large"}, 413)
        status, msg = a.handle_http(self.rfile.read(n), self.headers)
        self.send_json({"result": msg}, status)

    def vlm_test(self, q):
        """Test page: one uploaded image -> OCR + each chosen local VLM (time and text)."""
        import cv2
        import numpy as np
        from . import vlm
        from .extractor import Params, ink_fraction
        from .ocr import ocr_image
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 15_000_000:
            raise ValueError("bad image size")
        img = cv2.imdecode(np.frombuffer(self.rfile.read(n), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("not an image (use PNG or JPG)")
        t = time.time()
        text = ocr_image(img)
        ocr_s = round(time.time() - t, 2)
        p = Params()
        alnum = len(re.sub(r"\W", "", text))
        ink = ink_fraction(img, p.crop_ratio)
        out = {"ocr": text, "ocr_seconds": ocr_s, "alnum": alnum, "ink": round(ink, 3),
               "diagram": alnum < p.diagram_chars and ink >= p.diagram_ink, "results": []}
        gpu = q.get("gpu", "")
        tmp = self.app.data / "_vlm_test.jpg"
        cv2.imwrite(str(tmp), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        try:
            for model in [m for m in q.get("models", "").split(",") if m.strip()][:4]:
                t = time.time()
                try:
                    desc = vlm.describe(tmp, timeout=300, model=model,
                                        num_gpu=int(gpu) if gpu != "" else None,
                                        prompt=(q.get("prompt") or "").strip() or None)
                    err = ""
                except Exception as e:  # noqa: BLE001
                    desc, err = "", f"{type(e).__name__}: {e}"
                out["results"].append({"model": model, "seconds": round(time.time() - t, 1),
                                       "text": desc, "error": err})
        finally:
            tmp.unlink(missing_ok=True)
        return out

    def api(self, method, path, q):
        a = self.app
        parts = path.strip("/").split("/")
        if method == "GET":
            if path == "status":
                return self.send_json(a.status())
            if path == "settings":
                return self.send_json(a.settings())
            if path == "runs":
                return self.send_json({"runs": a.runs()})
            if path == "windows":
                from .windows import list_windows
                return self.send_json({"windows": list_windows(), "capture": a.capture_status()})
            if len(parts) == 3 and parts[0] == "windows" and parts[2] == "thumb":
                from .windows import thumbnail_jpeg
                return self.send_jpeg(thumbnail_jpeg(int(parts[1])))
            if path == "capture":
                return self.send_json(a.capture_status())
            if path == "vlm/models":
                from . import vlm
                return self.send_json({"models": vlm.list_models(), "default": os.environ.get("VCS_VLM_MODEL", ""),
                                       "num_gpu": os.environ.get("VCS_VLM_NUM_GPU", ""), "prompt": vlm.PROMPT})
            if path == "pending":
                a.ui_seen = time.time()
                return self.send_json({"pending": a.pending_overwrites()})
            if path == "automation":
                if a.automation is None:
                    return self.send_json({"state": "off", "message": "Started with --no-webhooks."})
                return self.send_json(a.automation.status())
            if path == "capture/preview":
                r = a.recorder
                return self.send_jpeg(r.latest_jpeg() if r else None)
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "offset":
                return self.send_json(a.suggest_offset(parts[1], q.get("meeting_id")))
            if len(parts) == 3 and parts[0] == "meetings" and parts[2] == "speakers":
                return self.send_json(a.meeting_speakers(parts[1]))
            if path == "meetings":
                try:
                    return self.send_json(MeetilyClient().list_meetings())
                except MeetilyError as e:  # offline etc.: the UI shows the reason, not a failure
                    return self.send_json({"meetings": [], "error": str(e), "offline": isinstance(e, MeetilyOffline)})
            if len(parts) == 2 and parts[0] == "runs":
                return self.send_json(a.run_detail(parts[1]))
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "input":
                return self.send_json(a.input_preview(parts[1], q.get("meeting_id"),
                                                      float(q.get("offset") or 0)))
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "timeline":
                return self.send_json(a.timeline(parts[1], q.get("meeting_id"), float(q.get("offset") or 0)))
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "publish-info":
                return self.send_json(a.publish_info(parts[1], q.get("meeting_id")))
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "backup":
                return self.send_json(a.backup_text(parts[1]))
            if len(parts) >= 4 and parts[0] == "runs" and parts[2] == "images":
                d = a.run_dir(parts[1]) / "images"
                p = (d / parts[3]).resolve()
                if p.parent != d.resolve():
                    raise ValueError("bad image")
                return self.send_file(p)
            if len(parts) == 2 and parts[0] == "jobs":
                j = a.jobs.jobs.get(parts[1])
                return self.send_json(j) if j else self.send_json({"error": "no such job"}, 404)
        elif method == "POST":
            if path == "extract":
                name = q.get("name") or "recording.mp4"
                ext = Path(name).suffix.lower()
                if ext not in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
                    raise ValueError("unsupported video type")
                run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(name).stem)[:40] \
                    + "-" + datetime.now().strftime("%m%d-%H%M%S")
                d = a.run_dir(run_id)
                d.mkdir(parents=True)
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0 or n > MAX_UPLOAD:
                    raise ValueError("bad upload size")
                video = d / f"source{ext}"
                with open(video, "wb") as f:
                    left = n
                    while left:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                job = a.start_extract(run_id, video, name)
                return self.send_json({"job": job["id"], "run": run_id})
            if len(parts) == 3 and parts[0] == "windows" and parts[2] == "check":
                from .windows import presenting_warning
                return self.send_json({"warning": presenting_warning(int(parts[1]))})
            if path == "vlm/test":
                return self.send_json(self.vlm_test(q))
            if path == "automation/test":
                if a.automation is None or not a.automation.sub.state.get("id"):
                    raise ValueError("no webhook registered yet")
                MeetilyClient().test_webhook(a.automation.sub.state["id"])
                return self.send_json({"ok": True})
            if path == "capture/start":
                return self.send_json(a.start_capture(int(self.body_json().get("hwnd") or 0)))
            if path == "capture/stop":
                return self.send_json(a.stop_capture())
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "overwrite":
                return self.send_json(a.resolve_overwrite(parts[1], self.body_json().get("action")))
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "publish":
                b = self.body_json()
                if b.get("confirm") is not True:
                    raise ValueError("confirmation required")
                job = a.start_publish(parts[1], b.get("meeting_id"))
                return self.send_json({"job": job["id"]})
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "summarize":
                b = self.body_json()
                job = a.start_summarize(parts[1], b.get("meeting_id"),
                                        float(b.get("offset") or 0), b.get("model"))
                return self.send_json({"job": job["id"]})
        elif method == "PUT":
            if path == "settings":
                return self.send_json(a.save_settings(self.body_json()))
            if len(parts) == 3 and parts[0] == "meetings" and parts[2] == "speakers":
                if parts[1] == "fixture":
                    raise ValueError("the demo transcript has no speakers to name")
                names = self.body_json().get("names") or {}
                if not isinstance(names, dict):
                    raise ValueError("names must be an object")
                return self.send_json({"names": speaker_names.save(a.data, parts[1], names)})
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "meta":
                return self.send_json(a.set_meta(parts[1], self.body_json()))
        elif method == "DELETE":
            if len(parts) == 2 and parts[0] == "runs":
                a.delete_run(parts[1])
                return self.send_json({"ok": True})
        self.send_json({"error": "not found"}, 404)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")


def serve(data_dir: Path, port: int = 8765, webhooks: bool = True):
    from .ocr import warm_up
    from .windows import set_dpi_aware
    set_dpi_aware()
    warm_up()   # before any window capture; see vcs.ocr.warm_up for the native crash it avoids
    Handler.app = App(data_dir)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if webhooks:
        from .webhooks import Automation
        Handler.app.automation = Automation(Handler.app, f"http://127.0.0.1:{port}/webhook")
        Handler.app.automation.start()
    print(f"Visual Context UI: http://127.0.0.1:{port}  (data: {Handler.app.data})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        rec = Handler.app.recorder
        if rec is not None:
            rec.stop()                       # finalize the video so it isn't lost
            print(f"capture stopped; video kept in {rec.out_dir}")
        print("bye")
