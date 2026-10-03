"""The app's core, without any UI: capture, the job queue's stages, Replace/Keep decisions and
the Meetily webhook automation. The Qt layer calls these methods and listens through `UiHooks`.

Thread model: Meetily events are handled one at a time on the "events" thread; jobs run on the
queue's worker thread; capture runs on Windows Graphics Capture's thread plus our writer. The UI
never blocks on any of them.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .errors import Canceled
from .extractor import extract
from .jobs import JobQueue
from .meetily_client import MeetilyClient, MeetilyError, MeetilyOffline, explain
from .ollama import Ollama, OllamaError
from .store import Store, read_json, write_json
from .summarizer import summarize
from .webhooks import EventStore, Subscription, meeting_id_of
from .writeback import IN_PROGRESS, fingerprint, is_placeholder_title, publish, split_title, summary_text

BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe",
            "arc.exe", "chromium.exe"}

END_EVENTS = {"recording.stopped", "recording.failed", "recording.error", "recording.stop_failed"}
SUMMARY_EVENTS = {"summary.completed", "summary.failed"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mmss(sec: float) -> str:
    s = int(max(0, sec))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


class UiHooks:
    """Overridden by the Qt bridge. Called from worker threads; must not block."""

    def pick_window(self, request: "PickRequest") -> None: ...
    def pick_closed(self, request: "PickRequest") -> None: ...
    def show_live(self, job_id: str | None) -> None: ...
    def notify(self, title: str, text: str, level: str = "info") -> None: ...
    def automation_changed(self) -> None: ...
    def capture_changed(self) -> None: ...


class PickRequest:
    """recording.started asked the user which window to capture (answered in the main window)."""

    def __init__(self, meeting_id: str | None, title: str, watch: dict):
        self.meeting_id, self.title, self.watch = meeting_id, title, watch
        self.created = time.time()
        self._done = threading.Event()
        self.result: dict | None = None

    def choose(self, hwnd: int) -> None:
        if not self._done.is_set():
            self.result = {"hwnd": int(hwnd)}
            self._done.set()

    def skip(self) -> None:
        if not self._done.is_set():
            self.result = {"skip": True}
            self._done.set()

    def cancel(self) -> None:
        if not self._done.is_set():
            self.result = {"cancelled": True}
            self._done.set()

    @property
    def open(self) -> bool:
        return not self._done.is_set()

    def wait(self, timeout: float | None = None) -> dict | None:
        self._done.wait(timeout)
        return self.result


class Controller:
    def __init__(self, data_dir: Path, ui: UiHooks | None = None, client_factory=MeetilyClient,
                 sleep=time.sleep, start_services: bool = True):
        self.data = Path(data_dir).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.ui = ui or UiHooks()
        self.client_factory = client_factory
        self.sleep = sleep
        self.settings = Settings(self.data)
        self.store = Store(self.data, client_factory)
        self.events = EventStore(self.data / "events")
        self.store.recording_started_lookup = self.recording_started_at
        self.queue = JobQueue(self.data, self._run_stage, on_idle=self._on_idle,
                              paused=self.settings["queue_paused"],
                              on_pause_change=lambda p: self.settings.update({"queue_paused": p}))
        self.sub = Subscription(self.data, self.webhook_url(), client_factory)
        self.meetily_online = False
        self.recorder = None
        self.capture_job: str | None = None
        self.capture_run: str | None = None
        self.capture_lock = threading.Lock()
        self.pick: PickRequest | None = None
        self.ocr_ready = threading.Event()
        self.httpd = None
        self.http_error: str | None = None
        self._evq: queue.Queue = queue.Queue()
        self._stopping = threading.Event()
        if start_services:
            self.start()

    # ================================================================== lifecycle
    def webhook_url(self) -> str:
        return f"http://127.0.0.1:{int(self.settings['webhook_port'])}/webhook"

    def start(self, webhooks: bool = True) -> None:
        """webhooks=False: no listener and no subscription in Meetily (manual use only)."""
        threading.Thread(target=self._warm_ocr, daemon=True, name="ocr-warmup").start()
        if self.settings["start_ollama"]:
            threading.Thread(target=lambda: self._ollama().ensure_running(log=print), daemon=True,
                             name="ollama-start").start()
        self.queue.start()
        from .webserver import serve_in_thread
        if not webhooks:
            self.http_error = "webhooks are off (started with --no-webhooks)"
            self.sub.state = {"state": "error", "message": self.http_error, "url": self.sub.url}
        else:
            try:
                self.httpd = serve_in_thread(self.settings["webhook_port"], self.handle_webhook)
            except OSError as e:
                self.http_error = (f"port {self.settings['webhook_port']} is busy ({e}); is another "
                                   f"copy running? Change it in Settings > App and restart")
        for ev in self.events.with_status("queued", "running"):      # unfinished before a restart
            self._evq.put(ev)
        threading.Thread(target=self._event_worker, daemon=True, name="events").start()
        if self.httpd is not None:
            threading.Thread(target=self._subscriber, daemon=True, name="subscribe").start()
        threading.Thread(target=self._capture_ticker, daemon=True, name="capture-tick").start()

    def shutdown(self) -> None:
        """Quit: finish a running capture (the video is finalized and queued for next start)."""
        self._stopping.set()
        if self.pick is not None:
            self.pick.cancel()
        if self.recorder is not None:
            try:
                self.stop_capture(reason="the app was closed; processing continues on next start")
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        self.queue.stop()
        if self.httpd is not None:
            self.httpd.shutdown()
        if self.settings["keep_loaded"]:
            self._ollama().unload(self.settings["model"])

    def _need_model(self, ctx) -> None:
        """Before a step that uses the model: make sure Ollama runs (it can stop mid-session,
        e.g. after running out of memory), starting it if the setting allows."""
        client = self._ollama()
        ok = client.ensure_running(log=ctx.log) if self.settings["start_ollama"] else client.up()
        if not ok:
            raise RuntimeError(f"can't reach Ollama at {client.base}; start Ollama and press Retry")

    def _warm_ocr(self) -> None:
        """OCR must be loaded before the first Windows Graphics Capture session (see ocr.warm_up)."""
        try:
            from .ocr import warm_up
            warm_up()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            self.ocr_ready.set()

    def _ollama(self) -> Ollama:
        return Ollama(self.settings["ollama_url"])

    def _keep_alive(self):
        return "10m" if self.settings["keep_loaded"] else 0

    def _on_idle(self) -> None:
        """Queue is empty: free the model's memory (it stayed loaded between jobs)."""
        if self.settings["keep_loaded"]:
            self._ollama().unload(self.settings["model"])

    # ================================================================== status (for the UI)
    def status(self) -> dict:
        """Everything the Overview needs. Does network calls: run it off the UI thread."""
        s = self.settings.all()
        out: dict = {"meetily": {"online": False}, "write": {"ok": False, "message": ""},
                     "ollama": {"online": False}, "subscription": dict(self.sub.state),
                     "webhook_url": self.webhook_url(), "http_error": self.http_error,
                     "write_key_set": bool(os.environ.get("MEETILY_PRO_TOKEN", "").strip())}
        try:
            c = self.client_factory(timeout=3)
            out["meetily"] = {"online": True, "recording": c.recording()}
            try:
                out["meetily"]["whoami"] = c.whoami()
            except MeetilyError:
                pass
            out["write"] = c.write_status()
        except MeetilyOffline:
            out["meetily"]["error"] = "Meetily isn't running, or 'Allow the CLI on this computer' is off."
        except MeetilyError as e:
            out["meetily"]["error"] = str(e)
        try:
            models = self._ollama().models()
            loaded = self._ollama().loaded()
            out["ollama"] = {"online": True, "models": models, "model": s["model"],
                             "installed": s["model"] in models,
                             "loaded": [m.get("name") for m in loaded]}
        except OllamaError as e:
            out["ollama"] = {"online": False, "error": str(e), "model": s["model"]}
        return out

    def models(self) -> list[str]:
        return self._ollama().models()

    # ================================================================== capture
    def capture_info(self) -> dict | None:
        r = self.recorder
        if r is None:
            return None
        return {"run": self.capture_run, "job": self.capture_job, **r.info()}

    def capture_preview(self, max_w: int = 640) -> bytes | None:
        r = self.recorder
        return r.latest_jpeg(max_w) if r is not None else None

    def start_capture(self, hwnd: int, meeting_id: str | None = None, started_at: str | None = None,
                      title: str | None = None) -> dict:
        from .capture import WindowRecorder
        from .windows import is_window, restore, window_info
        self.ocr_ready.wait(60)
        restore(hwnd)                       # a minimized window delivers no frames
        with self.capture_lock:
            if self.recorder is not None and self.recorder.running:
                raise ValueError("a capture is already running; stop it first")
            if not is_window(hwnd):
                raise ValueError("that window no longer exists; refresh the list")
            w = window_info(hwnd)
            run = self.store.create_run("capture", "Live capture: " + w["title"], window=w,
                                        meeting_id=meeting_id, recording_meeting_id=meeting_id,
                                        recording_started_at=started_at,
                                        meeting_title=title)
            rec = WindowRecorder(hwnd, self.store.run_dir(run), title=w["title"], process=w["process"])
            try:
                rec.start()
            except Exception:
                self.store.delete(run)
                raise
            self.recorder, self.capture_run = rec, run
            self.settings.update({"watch": {"hwnd": w["hwnd"], "title": w["title"],
                                            "process": w["process"]}})
            job = self.queue.create(run=run, title=title or w["title"], meeting_id=meeting_id,
                                    capturing=True, source="meeting" if meeting_id else "manual")
            self.capture_job = job["id"]
            self.queue.log(job["id"], f"capturing '{w['title']}' ({w['process']})"
                           + (f" for meeting {meeting_id}" if meeting_id else ""))
        self.ui.capture_changed()
        return {"run": run, "job": job["id"]}

    def stop_capture(self, reason: str = "", summarize_after: bool = True,
                     discard: bool = False) -> dict:
        """Stop recording and queue the job. discard=True cancels it instead (the video is kept,
        so Retry on the Live tab still processes it)."""
        with self.capture_lock:
            rec, run, jid = self.recorder, self.capture_run, self.capture_job
            if rec is None:
                raise ValueError("no capture is running")
            info = rec.stop()
            self.recorder = self.capture_run = self.capture_job = None
        self.store.update_meta(run, {"capture_started_at": info["started_at"],
                                     "capture_ended_at": info["ended_at"],
                                     "capture_seconds": info["seconds"], "video": "capture.mp4"})
        self.ui.capture_changed()
        if reason:
            self.queue.log(jid, reason)
        if not info["frames"]:
            err = info["error"] or "nothing was captured"
            self.queue.capture_done(jid, ok=False, error=err)
            return {"run": run, "job": jid, **info}
        self.queue.log(jid, f"captured {info['seconds']:.0f}s of '{info['title']}'"
                       + (" (the window was closed)" if info["window_closed"] else ""))
        if not summarize_after:
            self.skip_stages(jid, ["summarize", "publish"], "not summarised (see log)")
        self.queue.capture_done(jid, ok=True, detail=f"{info['seconds']:.0f}s recorded",
                                discard=discard)
        return {"run": run, "job": jid, **info}

    def skip_stages(self, jid: str, names: list[str], why: str) -> None:
        job = self.queue.get(jid)
        for st in job["stages"]:
            if st["name"] in names and st["status"] == "pending":
                st.update(status="skipped", detail=why)
        self.queue._changed(jid)

    def _capture_ticker(self) -> None:
        while not self._stopping.wait(1.0):
            r, jid = self.recorder, self.capture_job
            if r is None or jid is None:
                continue
            info = r.info()
            secs = info["frames"] / max(info["fps"], 0.001)
            self.queue.set_capture_detail(jid, f"recording {mmss(secs)}")
            if not r.running and (info["error"] or info["window_closed"]):
                # The window closed (or capture died) mid-meeting: keep what we have.
                try:
                    self.stop_capture(reason="capture ended: " + (info["error"] or "the window was closed"))
                except ValueError:
                    pass

    def find_watch_window(self) -> int | None:
        """The window picked last time: same handle if it's still that app, else the same
        title, else the only window of that app (browser tab titles change)."""
        from .windows import is_window, list_windows, window_info
        watch = self.settings["watch"] or {}
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

    # ================================================================== manual jobs
    def add_video(self, path: Path, meeting_id: str | None = None) -> dict:
        path = Path(path)
        run = self.store.create_run("upload", path.name, meeting_id=meeting_id)
        dest = self.store.run_dir(run) / ("upload" + path.suffix.lower())
        shutil.copy(path, dest)
        self.store.update_meta(run, {"video": dest.name})
        return self.queue.create(run=run, title=path.stem, meeting_id=meeting_id, source="upload")

    def regenerate(self, run: str, meeting_id: str | None, offset: float | None = None,
                   describe: bool = False) -> dict:
        patch = {"meeting_id": meeting_id or None}
        if offset is not None:
            patch.update(offset=float(offset), offset_manual=True)
        self.store.update_meta(run, patch)
        title = self.store.meta(run).get("meeting_title") or run
        stages = (["describe"] if describe else []) + ["summarize", "publish"]
        return self.queue.create(run=run, title=title, meeting_id=meeting_id, stages=stages,
                                 source="regenerate")

    def save_summary(self, run: str, text: str) -> None:
        """Save a summary edited by hand. It stays tied to the meeting it was made for."""
        text = (text or "").strip()
        if not text:
            raise ValueError("the summary is empty")
        d = self.store.run_dir(run)
        meta = read_json(d / "summary.meta.json", {}) or {}
        if not meta.get("meeting_id"):
            meta["meeting_id"] = self.store.meta(run).get("meeting_id")
            meta.setdefault("model", "written by hand")
        meta.update(edited=True, edited_at=now_iso())
        (d / "summary.md").write_text(text + "\n", "utf-8")
        write_json(d / "summary.meta.json", meta)
        title = split_title(text)[0]
        if title and not is_placeholder_title(title):
            self.store.update_meta(run, {"meeting_title": title})

    def send_summary(self, run: str, meeting_id: str, replace: bool = False, log=lambda *_: None) -> dict:
        """Write this run's summary into Meetily now (not through the queue).
        Returns {"status": "exists", "chars"} when Meetily has a different summary and replace is
        False (ask the user), {"status": "same"} when it already shows ours, {"status": "sent"}."""
        d = self.store.run_dir(run)
        if not meeting_id or meeting_id == "fixture":
            raise ValueError("link a real Meetily meeting first")
        if not (d / "summary.md").exists():
            raise ValueError("there is no summary to send yet")
        made_for = (read_json(d / "summary.meta.json", {}) or {}).get("meeting_id")
        if made_for and made_for != meeting_id:
            raise ValueError(f"this summary was made from another meeting ({made_for}); "
                             "generate it for this meeting first")
        text = (d / "summary.md").read_text("utf-8")
        client = self.client_factory()
        ws = client.write_status()
        if not ws["ok"]:
            raise ValueError(ws.get("message") or "no usable Meetily write key (main/.env)")
        status, current = self._meetily_summary(client, meeting_id)
        if status in IN_PROGRESS:
            raise ValueError("Meetily is generating its own summary right now; try again when it's done")
        ours = split_title(text)[1]
        if current.strip() and fingerprint(current) == fingerprint(ours):
            return {"status": "same"}
        if current.strip() and not replace:
            return {"status": "exists", "chars": len(current)}
        record = publish(client, meeting_id, text, d,
                         delete_screenshots=self.settings["delete_screenshots_after_summary"], log=log)
        (d / "pending_overwrite.json").unlink(missing_ok=True)
        if record.get("renamed_to"):
            self.store.update_meta(run, {"meeting_title": record["renamed_to"]})
        for j in self.queue.for_run(run):                 # a Replace/Keep question is answered now
            if j["status"] == "waiting":
                self.queue.resolve(j["id"], note="summary sent to Meetily from the Meetings tab")
        return {"status": "sent", "record": record}

    def redescribe(self, run: str, summarize: bool = False) -> dict:
        """Describe every screen of a run again (e.g. after changing the screen prompt)."""
        m = self.store.meta(run)
        stages = ["describe"] + (["summarize", "publish"] if summarize and m.get("meeting_id") else [])
        return self.queue.create(run=run, title=m.get("meeting_title") or run,
                                 meeting_id=m.get("meeting_id"), stages=stages, source="regenerate",
                                 options={"redescribe": True})

    def publish_run(self, run: str, meeting_id: str) -> dict:
        title = self.store.meta(run).get("meeting_title") or run
        return self.queue.create(run=run, title=title, meeting_id=meeting_id, stages=["publish"],
                                 source="publish", options={"force_publish": True})

    # ================================================================== stages
    def _run_stage(self, name: str, job: dict, ctx):
        return getattr(self, f"_stage_{name}")(job, ctx)

    def _stage_capture(self, job, ctx):
        return "skipped"           # only reached when a capture job is retried

    def _stage_extract(self, job, ctx):
        run = job["run"]
        d = self.store.run_dir(run)
        m = self.store.meta(run)
        video = d / (m.get("video") or "capture.mp4")
        if not video.exists():
            if (d / "screenshots.json").exists():
                ctx.log("no video left, but screens were already extracted; keeping them")
                return "skipped"
            raise FileNotFoundError(f"the video {video.name} is gone; nothing to extract")
        self.ocr_ready.wait(120)
        params = self.settings.extract_params()
        process = ((m.get("window") or {}).get("process") or "").lower()
        params.browser = process in BROWSERS
        extract(video, d, params, log=ctx.log, progress=ctx.progress, cancel=ctx.cancelled)
        if not self.settings["keep_capture_video"]:
            video.unlink(missing_ok=True)
            ctx.log("video deleted (screenshots kept; Settings > Automation keeps it)")
        return None

    def _stage_describe(self, job, ctx):
        run = job["run"]
        doc = self.store.screenshots(run)
        s = self.settings.all()
        mode = s["describe_screens"]
        if mode == "none":
            ctx.log("describing screens is off (Settings > Model)")
            return "skipped"
        again = bool(job["options"].get("redescribe"))
        todo = [x for x in doc["screenshots"] if (again or not x.get("description"))
                and (again or mode == "all" or x.get("type") in ("diagram", "picture"))]
        if not todo:
            ctx.log("every screen is already described" if doc["screenshots"] else "no screens")
            return "skipped"
        self._need_model(ctx)
        client, d = self._ollama(), self.store.run_dir(run)
        ctx.log(f"describing {len(todo)} screen(s) with {s['model']} "
                f"({'CPU' if s['device'] == 'cpu' else 'GPU'})")
        failed = 0
        for i, shot in enumerate(todo):
            ctx.check()
            ctx.progress(i / len(todo), f"screen {i + 1}/{len(todo)}")
            t0 = time.monotonic()
            try:
                text = client.describe(
                    d / shot["image"], s["model"], s["vlm_prompt"],
                    num_gpu=self.settings.num_gpu(), max_tokens=s["vlm_max_tokens"],
                    max_side=s["vlm_max_side"], keep_alive=self._keep_alive(),
                    cancel=ctx.cancelled, timeout=s["request_timeout"])
                shot["described_by"] = s["model"]
                shot["description"] = text
                if shot.get("type") == "picture":       # dropped by an older version: back in
                    shot["type"] = "diagram"
                ctx.log(f"screen {shot['id']} at {mmss(shot['start'])} ({time.monotonic() - t0:.0f}s) "
                        f"shows: {text}")
            except Canceled:
                raise
            except OllamaError as e:
                failed += 1
                ctx.log(f"screen {shot['id']}: {e}; using its OCR text only")
                if "can't reach" in str(e) or "isn't installed" in str(e):
                    raise RuntimeError(str(e)) from None
            self.store.save_screenshots(run, doc)
        if failed == len(todo):
            ctx.log("no screen could be described; the summary uses the screen text only")
        return None

    def _stage_summarize(self, job, ctx):
        run, mid = job["run"], job.get("meeting_id")
        if not mid:
            ctx.log("no Meetily meeting linked: screens are ready. Link a meeting on the Meetings "
                    "tab and use Generate summary.")
            return "skipped"
        if not self.settings["auto_summarize"] and job["source"] == "meeting" \
                and not job["options"].get("summarize"):
            ctx.log("automatic summaries are off (Settings > Automation)")
            return "skipped"
        doc = self.store.screenshots(run) if (self.store.run_dir(run) / "screenshots.json").exists() \
            else {"screenshots": []}
        segs = self._wait_transcript(mid, ctx)
        if not any((x.get("text") or "").strip() for x in segs) and \
                not any(sh.get("type") != "picture" for sh in doc["screenshots"]):
            ctx.log("nothing was said and nothing was on screen; no summary to write")
            return "skipped"
        m = self.store.meta(run)
        if m.get("offset_manual"):
            offset, basis = float(m.get("offset") or 0), "set by you"
        else:
            sug = self.store.suggest_offset(run, mid)
            offset, basis = float(sug.get("offset") or 0.0), sug.get("basis")
            self.store.update_meta(run, {"offset": offset})
        names = self.store.speaker_names(mid)
        s = self.settings.all()
        ctx.log(f"{len(segs)} transcript segments, {len(doc['screenshots'])} screens, screen offset "
                f"{offset:+.1f}s ({basis}){f', {len(names)} speaker name(s)' if names else ''}")
        from .transcript import screen_lines
        lines = screen_lines(doc["screenshots"], offset)
        if lines:
            ctx.log(f"screen context given to the model ({len(lines)} screens):")
            for _t, line in lines:
                ctx.log("    " + line.split("[SCREEN] ", 1)[-1][:400])
        else:
            ctx.log("no screen context (nothing was captured or read from the screen)")
        ctx.log(f"writing the summary with {s['model']} (context {s['num_ctx']}, up to "
                f"{s['max_tokens']} tokens, {'CPU' if s['device'] == 'cpu' else 'GPU'})")
        ctx.progress(0.02, "loading the model and reading the transcript")
        count = [0]

        def on_token(piece):
            count[0] += 1
            ctx.token(piece)
            if count[0] % 8 == 0:
                ctx.progress(0.05 + 0.95 * min(1.0, count[0] / s["max_tokens"]),
                             f"{count[0]} tokens")

        info: dict = {}
        self._need_model(ctx)
        text = summarize(self._ollama(), s["model"], segs, doc["screenshots"], offset=offset,
                         speaker_names=names, num_ctx=s["num_ctx"], max_tokens=s["max_tokens"],
                         temperature=s["temperature"], repeat_penalty=s["repeat_penalty"],
                         num_gpu=self.settings.num_gpu(),
                         keep_alive=self._keep_alive(), on_token=on_token, cancel=ctx.cancelled,
                         timeout=s["request_timeout"], info=info)
        d = self.store.run_dir(run)
        (d / "summary.md").write_text(text, "utf-8")
        write_json(d / "summary.meta.json", {"model": s["model"], "meeting_id": mid, "offset": offset,
                                             "at": now_iso(), **info})
        title = split_title(text)[0]
        if title and not is_placeholder_title(title):
            self.store.update_meta(run, {"meeting_title": title})
            job["title"] = title
        ctx.log(f"summary saved ({len(text)} chars, {info.get('eval_count') or '?'} tokens in "
                f"{info.get('seconds')}s)" + (" - hit the token limit, it may be cut short"
                                               if info.get("done_reason") == "length" else ""))
        return None

    def _wait_transcript(self, mid: str, ctx, attempts: int = 4) -> list[dict]:
        """Meetily persists the transcript just after recording.stopped; wait until it has text."""
        segs: list[dict] = []
        for i in range(attempts):
            ctx.check()
            try:
                segs = (self.store.transcript(mid) or {}).get("segments") or []
            except MeetilyOffline:
                raise RuntimeError("Meetily isn't reachable; start it and Retry") from None
            except MeetilyError as e:
                if e.status != 409 or i == attempts - 1:
                    raise RuntimeError(f"couldn't read the transcript: {explain(e)}") from None
            if any((x.get("text") or "").strip() for x in segs):
                return segs
            if i < attempts - 1:
                wait = (3, 10, 20)[min(i, 2)]
                ctx.log(f"transcript has no text yet; checking again in {wait}s")
                self.sleep(wait)
        ctx.log("transcript is still empty; summarising from the screens alone")
        return segs

    def _stage_publish(self, job, ctx):
        run, mid, opts = job["run"], job.get("meeting_id"), job["options"]
        d = self.store.run_dir(run)
        if not mid or mid == "fixture":
            return "skipped"
        if not (d / "summary.md").exists():
            ctx.log("no summary to write")
            return "skipped"
        if not (self.settings["auto_publish"] or opts.get("force_publish") or opts.get("force_replace")):
            ctx.log("automatic write-back is off; use Write to Meetily on the Meetings tab")
            return "skipped"
        made_for = (read_json(d / "summary.meta.json", {}) or {}).get("meeting_id")
        if made_for != mid:
            raise ValueError(f"this summary was made from a different meeting ({made_for}); "
                             f"regenerate it first")
        client = self.client_factory()
        try:
            ws = client.write_status()
        except MeetilyOffline:
            raise RuntimeError("Meetily isn't reachable; start it and Retry") from None
        if not ws["ok"]:
            ctx.log(f"not written to Meetily: {ws['message'] or ws['reason']} (key in main/.env)")
            return "skipped"
        status, current = self._meetily_summary(client, mid)
        ours = split_title((d / "summary.md").read_text("utf-8"))[1]
        if not opts.get("force_replace"):
            if status in IN_PROGRESS:
                ctx.log("Meetily is generating its own summary right now; ours is handled when "
                        "its summary event arrives")
                return "skipped"
            if current.strip():
                if fingerprint(current) == fingerprint(ours):
                    (d / "pending_overwrite.json").unlink(missing_ok=True)
                    ctx.log("Meetily already shows our summary")
                    return None
                kept = read_json(d / "kept_meetily.json", {}) or {}
                if kept.get("fingerprint") == fingerprint(current):
                    ctx.log("you chose to keep Meetily's summary; left as is")
                    return None
                title = self._meeting_title(client, mid)
                decision = {"type": "overwrite", "run": run, "meeting_id": mid, "title": title,
                            "meetily_chars": len(current), "ours_chars": len(ours), "at": now_iso()}
                write_json(d / "pending_overwrite.json", decision)
                self.ui.notify("Meetily already has a summary",
                               f"{title or mid}: replace it with ours, or keep Meetily's?", "decision")
                return ("waiting", decision)
        record = publish(client, mid, (d / "summary.md").read_text("utf-8"), d,
                         delete_screenshots=self.settings["delete_screenshots_after_summary"],
                         log=ctx.log)
        (d / "pending_overwrite.json").unlink(missing_ok=True)
        if record.get("renamed_to"):
            self.store.update_meta(run, {"meeting_title": record["renamed_to"]})
        return None

    @staticmethod
    def _meetily_summary(client, mid: str) -> tuple[str, str]:
        try:
            cur = client.get_summary(mid) or {}
        except MeetilyError as e:
            if e.status == 404:
                return "", ""
            raise
        return str(cur.get("status") or "").lower(), summary_text(cur.get("result"))

    @staticmethod
    def _meeting_title(client, mid: str) -> str:
        try:
            return (client.get_meeting(mid) or {}).get("title") or ""
        except Exception:  # noqa: BLE001
            return ""

    # ================================================================== decisions
    def resolve_overwrite(self, run: str, action: str, job_id: str | None = None) -> str:
        """Replace / keep / later for a run whose summary Meetily already has a different one of."""
        d = self.store.run_dir(run)
        pending = read_json(d / "pending_overwrite.json")
        if pending is None:
            return "nothing is waiting for a decision"
        mid = pending["meeting_id"]
        job = self.queue.get(job_id) if job_id else None
        if job is None:
            job = next((j for j in self.queue.for_run(run) if j["status"] == "waiting"), None)
        if action == "replace":
            if job is not None:
                self.queue.resolve(job["id"], retry_stage="publish", options={"force_replace": True},
                                   note="you chose: replace Meetily's summary with ours")
            else:
                self.queue.create(run=run, title=pending.get("title") or run, meeting_id=mid,
                                  stages=["publish"], source="publish",
                                  options={"force_replace": True})
            return "replacing (Meetily's version is backed up first)"
        if action == "keep":
            try:
                fp = fingerprint(self._meetily_summary(self.client_factory(), mid)[1])
            except MeetilyError:
                fp = None
            write_json(d / "kept_meetily.json", {"meeting_id": mid, "fingerprint": fp, "at": now_iso()})
            (d / "pending_overwrite.json").unlink(missing_ok=True)
            if job is not None:
                self.queue.resolve(job["id"], note="you chose: keep Meetily's summary")
            return "kept Meetily's summary"
        return "left for later (Meetings tab)"

    def pending_decisions(self) -> list[dict]:
        out = []
        for r in self.store.runs():
            if r["pending"]:
                p = read_json(self.store.run_dir(r["id"]) / "pending_overwrite.json")
                if p:
                    out.append(p)
        return out

    # ================================================================== webhooks
    def handle_webhook(self, body: bytes, headers) -> tuple[int, str]:
        from .webhooks import SIGNATURE_HEADER, TIMESTAMP_HEADER, _EVENT_ID, verify_signature
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
        if self.events.accept(event):
            self._evq.put(event)
            self.ui.automation_changed()
            return 200, "accepted"
        return 200, "duplicate"

    def _subscriber(self) -> None:
        delay = 5
        while not self._stopping.is_set():
            before = dict(self.sub.state)
            try:
                st = self.sub.ensure()
                if not self.meetily_online:
                    for ev in self.events.with_status("offline"):
                        self.events.update(ev["event_id"], "queued", "Meetily is back; retrying")
                        self._evq.put(ev)
                self.meetily_online = True
                delay = 60 if st.get("state") == "active" else 10
            except MeetilyOffline:
                self.meetily_online = False
                self.sub.state = {"state": "offline", "url": self.sub.url,
                                  "message": "Meetily isn't running; will subscribe when it is."}
                delay = 10
            except Exception as e:  # noqa: BLE001 - never let this thread die
                self.sub.state = {"state": "error", "message": str(e), "url": self.sub.url}
                delay = 30
            if self.sub.state != before:
                self.ui.automation_changed()
            if self._stopping.wait(delay):
                return

    def recent_events(self, n: int = 30) -> list[dict]:
        return [{"event_id": r["event"].get("event_id"), "event": r["event"].get("event"),
                 "meeting_id": meeting_id_of(r["event"]), "status": r.get("status"),
                 "received_at": r.get("received_at"), "log": r.get("log", [])}
                for r in self.events.recent(n)]

    def test_webhook(self) -> None:
        wid = self.sub.state.get("id")
        if not wid:
            raise ValueError("no webhook registered yet")
        self.client_factory().test_webhook(wid)

    def recording_started_at(self, meeting_id: str) -> str | None:
        for r in self.events.records():
            ev = r.get("event") or {}
            if ev.get("event") == "recording.started" and meeting_id_of(ev) == meeting_id:
                return ev.get("occurred_at")
        return None

    def _event_worker(self) -> None:
        while not self._stopping.is_set():
            try:
                event = self._evq.get(timeout=1)
            except queue.Empty:
                continue
            self.handle_event(event)

    def handle_event(self, event: dict) -> None:
        eid = event.get("event_id")
        self.events.update(eid, "running")
        try:
            outcome = self.process_event(event, lambda m: self.events.update(eid, log=m))
            self.events.update(eid, "done", outcome)
        except MeetilyOffline:
            self.meetily_online = False
            self.events.update(eid, "offline", "Meetily went offline; will retry when it's back")
        except Exception as e:  # noqa: BLE001 - one bad event must not stop the worker
            traceback.print_exc()
            self.events.update(eid, "failed", f"{type(e).__name__}: {e}")
        self.ui.automation_changed()

    def process_event(self, event: dict, log) -> str:
        kind, mid = event.get("event"), meeting_id_of(event)
        if kind == "recording.started":
            return self.on_started(mid, event.get("occurred_at"), log)
        if kind in END_EVENTS:
            return self.on_ended(mid, kind, log)
        if kind in SUMMARY_EVENTS:
            return self.on_summary(mid, kind, log)
        if kind == "webhook.test" or (kind or "").endswith(".test"):
            return "test event received"
        return f"ignored {kind}"

    def _retry(self, fn, log, what: str, attempts: int = 4):
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

    # recording.started -> ask in the main window which window to capture
    def on_started(self, mid: str | None, occurred_at: str | None, log) -> str:
        if not mid:
            st = self._retry(lambda: self.client_factory().recording(), log, "recording state")
            mid = (st or {}).get("active_meeting_id")
        if self.recorder is not None and self.recorder.running:
            self.store.update_meta(self.capture_run, {"recording_meeting_id": mid,
                                                      "recording_started_at": occurred_at,
                                                      "meeting_id": mid})
            job = self.queue.get(self.capture_job)
            if job is not None and not job.get("meeting_id"):
                job["meeting_id"] = mid
                self.queue._changed(job["id"])
            return f"already capturing ({self.capture_run}); linked it to {mid}"
        s = self.settings.all()
        if not s["auto_capture"]:
            return "auto capture is off in Settings"
        title = self._meeting_title(self.client_factory(), mid) if mid else ""
        if s["ask_window"]:
            if self.pick is not None:
                self.pick.cancel()
                self.ui.pick_closed(self.pick)
            req = self.pick = PickRequest(mid, title, s["watch"] or {})
            threading.Thread(target=self._await_pick, args=(req, occurred_at, log), daemon=True,
                             name="pick").start()
            self.ui.pick_window(req)
            return "asked which window to capture (main window)"
        hwnd = self.find_watch_window()
        if hwnd is None:
            self.ui.notify("Recording started", "No remembered window to capture; pick one on New run.")
            return "no remembered window to capture"
        self.start_capture(hwnd, mid, occurred_at, title)
        if s["show_on_start"]:
            self.ui.show_live(self.capture_job)
        return f"capture started ({self.capture_run})"

    def _await_pick(self, req: PickRequest, occurred_at, log) -> None:
        timeout = int(self.settings["pick_timeout"] or 0)
        choice = req.wait(timeout or None)
        if choice is None:                      # nobody answered in time
            hwnd = self.find_watch_window()
            if hwnd is not None:
                log(f"no answer after {timeout}s; capturing the last-used window")
                req.choose(hwnd)
            choice = req.wait()                 # else keep waiting (until the recording ends)
        choice = choice or {}
        if self.pick is req:
            self.pick = None
        self.ui.pick_closed(req)
        if choice.get("cancelled"):
            log("the recording ended before a window was picked; nothing captured")
            return
        if choice.get("skip"):
            log("skipped: this recording is not screen-captured")
            return
        try:
            self.start_capture(choice["hwnd"], req.meeting_id, occurred_at, req.title)
            log(f"capturing '{self.recorder.title}' ({self.capture_run})")
            self.ui.show_live(self.capture_job)
        except Exception as e:  # noqa: BLE001
            log(f"couldn't start the capture: {e}")
            self.ui.notify("Capture failed", str(e), "error")

    # recording.stopped / failed -> stop capture, queue extract + summary
    def on_ended(self, mid: str | None, kind: str, log) -> str:
        if self.pick is not None and self.pick.open and (not mid or self.pick.meeting_id in (None, mid)):
            self.pick.cancel()
        if self.recorder is None:
            return self.transcript_only(mid, kind, log)
        run = self.capture_run
        linked = self.store.meta(run).get("recording_meeting_id")
        if mid and linked and linked != mid:
            return f"capture {run} belongs to {linked}, not {mid}; left running"
        ok = kind == "recording.stopped"
        res = self.stop_capture(reason=f"Meetily: {kind}", summarize_after=ok)
        jid = res["job"]
        job = self.queue.get(jid)
        if job is not None:
            if mid and not job.get("meeting_id"):
                job["meeting_id"] = mid
            title = self._meeting_title(self.client_factory(), mid) if mid else ""
            if title:
                job["title"] = title
                self.store.update_meta(run, {"meeting_title": title})
            self.queue._changed(jid)
        if self.settings["show_on_stop"]:
            self.ui.show_live(jid)
        if not ok:
            return f"{run}: recording ended with {kind}; screens are extracted, no summary"
        return f"{run}: capture stopped after {res['seconds']:.0f}s; queued (job {jid})"

    def transcript_only(self, mid: str | None, kind: str, log) -> str:
        """A recording ended without a screen capture (window skipped, not picked in time, or
        capture off): the meeting still shows up here and gets a summary from its transcript."""
        if kind != "recording.stopped" or not mid:
            return "no capture running; nothing to do"
        if self.queue.active_for_meeting(mid) or self.store.run_for_meeting(mid):
            return "this meeting is already here"
        title = self._meeting_title(self.client_factory(), mid)
        run = self.store.create_run("meeting", "Meetily recording (no screen capture)",
                                    meeting_id=mid, recording_meeting_id=mid,
                                    meeting_title=title or None,
                                    recording_started_at=self.recording_started_at(mid))
        job = self.queue.create(run=run, title=title or "Meetily recording", meeting_id=mid,
                                stages=["summarize", "publish"], source="meeting")
        self.queue.log(job["id"], "no window was captured for this recording; summarising the "
                                  "transcript only")
        if self.settings["show_on_stop"]:
            self.ui.show_live(job["id"])
        log(f"no screen capture; queued a transcript-only summary (job {job['id']})")
        return f"{run}: transcript-only summary queued (job {job['id']})"

    # summary.completed / failed -> keep our summary in place
    def on_summary(self, mid: str | None, kind: str, log) -> str:
        if not mid:
            return "event has no meeting id"
        run = self.store.run_for_meeting(mid)
        if run is None:
            return "no screen capture for this meeting; Meetily's summary left as is"
        active = self.queue.active_for_meeting(mid)
        if active is not None:
            return f"job {active['id']} for this meeting is still {active['status']}; it handles it"
        d = self.store.run_dir(run)
        pub = read_json(d / "published.json")
        ready = (d / "summary.md").exists() and \
            (read_json(d / "summary.meta.json", {}) or {}).get("meeting_id") == mid
        if pub and pub.get("meeting_id") == mid:
            cur = self._retry(lambda: self.client_factory().get_summary(mid), log, "summary")
            if fingerprint(summary_text((cur or {}).get("result"))) == pub.get("fingerprint"):
                return "Meetily shows our summary; nothing to do"
            log("Meetily replaced our summary")
        if ready:
            job = self.queue.create(run=run, title=self.store.meta(run).get("meeting_title") or run,
                                    meeting_id=mid, stages=["publish"], source="publish")
            return f"queued a write-back check (job {job['id']})"
        if (d / "screenshots.json").exists():
            job = self.queue.create(run=run, title=self.store.meta(run).get("meeting_title") or run,
                                    meeting_id=mid, stages=["describe", "summarize", "publish"],
                                    source="meeting")
            return f"queued our summary (job {job['id']})"
        return "no screens for this meeting yet"
