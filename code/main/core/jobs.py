"""The processing queue behind the Live tab.

One worker runs one job at a time, oldest first: only one model fits in a 4 GB GPU, and the
stages of a meeting depend on each other anyway. A job is one meeting (run) going through

    capture -> extract -> describe -> summarize -> publish

`capture` happens live, outside the queue (status "capturing"); when the recording stops the
job joins the queue. Jobs are kept in data/jobs/<id>.json so the list, logs and a failed
step survive a restart (a job that was running is queued again; finished stages are skipped).

The queue knows nothing about what a stage does: `runner(stage_name, job, ctx)` does the work
and returns None (done), "skipped", or ("waiting", decision) when the user has to choose.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from .errors import Canceled

STAGES = [("capture", "Capture"), ("extract", "Extract screens"), ("describe", "Describe diagrams"),
          ("summarize", "Summarize"), ("publish", "Write to Meetily")]
LABELS = dict(STAGES)
# Share of the progress bar per stage (capture is open-ended, so it isn't counted).
WEIGHTS = {"extract": 0.35, "describe": 0.25, "summarize": 0.35, "publish": 0.05}
ACTIVE = {"capturing", "queued", "running"}
MAX_LOG = 2000


class Ctx:
    """What a stage gets: log, progress, token streaming, and the cancel flag."""

    def __init__(self, q: "JobQueue", job: dict, stage: dict, cancel: threading.Event):
        self.q, self.job, self.stage, self._cancel = q, job, stage, cancel

    def log(self, msg: str) -> None:
        self.q.log(self.job["id"], msg)

    def progress(self, frac: float, detail: str = "") -> None:
        frac = max(0.0, min(1.0, float(frac)))
        if abs(frac - self.stage["progress"]) < 0.005 and detail == self.stage.get("detail"):
            return
        self.stage["progress"], self.stage["detail"] = frac, detail
        self.q._changed(self.job["id"], persist=False)

    def token(self, piece: str) -> None:
        self.job.setdefault("_stream", []).append(piece)
        self.q._emit("token", self.job["id"], piece)

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check(self) -> None:
        if self._cancel.is_set():
            raise Canceled()


class JobQueue:
    def __init__(self, data_dir: Path, runner, *, on_idle=None, paused: bool = False,
                 on_pause_change=None):
        self.dir = Path(data_dir) / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.on_idle = on_idle or (lambda: None)
        self.on_pause_change = on_pause_change or (lambda paused: None)
        self.listeners: list = []                 # fn(kind, job_id, payload)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.RLock()
        self._save_lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.paused = paused
        self.current: str | None = None
        self._cancel: dict[str, threading.Event] = {}
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._load()

    # ---------------------------------------------------------------- persistence
    def _file(self, jid: str) -> Path:
        return self.dir / f"{jid}.json"

    def _save(self, job: dict) -> None:
        """Atomic write, one at a time (worker and UI threads both save; Windows refuses a
        replace while another handle has the file open, so retry briefly)."""
        with self._save_lock:
            clean = json.loads(json.dumps({k: v for k, v in job.items() if not k.startswith("_")}))
            f = self._file(job["id"])
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), "utf-8")
            for i in range(5):
                try:
                    tmp.replace(f)
                    return
                except PermissionError:
                    if i == 4:
                        raise
                    time.sleep(0.05)

    def _load(self) -> None:
        for f in self.dir.glob("*.json"):
            try:
                job = json.loads(f.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if job.get("status") == "running":            # interrupted by a restart
                for st in job["stages"]:
                    if st["status"] == "running":
                        st.update(status="pending", progress=0.0, detail="")
                job["status"] = "queued"
                job["log"].append(self._stamp("app restarted; this job was queued again"))
            elif job.get("status") == "capturing":
                for st in job["stages"]:
                    if st["name"] == "capture" and st["status"] == "running":
                        st.update(status="failed", error="the app closed during the capture")
                job.update(status="failed", error="the app closed during the capture; "
                                                  "Retry extracts whatever was recorded")
            self.jobs[job["id"]] = job
            self._save(job)

    # ---------------------------------------------------------------- events
    def subscribe(self, fn) -> None:
        self.listeners.append(fn)

    def _emit(self, kind: str, jid: str, payload=None) -> None:
        for fn in list(self.listeners):
            try:
                fn(kind, jid, payload)
            except Exception:  # noqa: BLE001 - a broken listener must not stop the queue
                traceback.print_exc()

    def _changed(self, jid: str, persist: bool = True) -> None:
        job = self.jobs.get(jid)
        if job is None:
            return
        job["updated"] = time.time()
        if persist:
            self._save(job)
        self._emit("changed", jid)

    @staticmethod
    def _stamp(msg: str) -> str:
        return f"{datetime.now().strftime('%H:%M:%S')}  {msg}"

    def log(self, jid: str, msg: str) -> None:
        job = self.jobs.get(jid)
        if job is None:
            return
        for line in str(msg).splitlines() or [""]:
            line = self._stamp(line)
            job["log"].append(line)
            self._emit("log", jid, line)
        del job["log"][:-MAX_LOG]
        self._save(job)

    # ---------------------------------------------------------------- create
    def create(self, *, run: str, title: str, meeting_id: str | None = None,
               stages: list[str] | None = None, capturing: bool = False, source: str = "meeting",
               options: dict | None = None) -> dict:
        names = stages or [n for n, _ in STAGES if n != "capture" or capturing]
        with self.lock:
            job = {
                "id": uuid.uuid4().hex[:10], "run": run, "title": title, "meeting_id": meeting_id,
                "source": source, "created": time.time(), "updated": time.time(),
                "status": "capturing" if capturing else "queued", "order": self._next_order(),
                "error": None, "decision": None, "options": options or {}, "log": [],
                "stages": [{"name": n, "label": LABELS[n], "status": "pending", "progress": 0.0,
                            "detail": "", "started": None, "ended": None, "error": None}
                           for n in names],
            }
            if capturing:
                st = job["stages"][0]
                st.update(status="running", started=time.time(), detail="recording")
            self.jobs[job["id"]] = job
            self._save(job)
            self.cv.notify_all()
        self._emit("added", job["id"])
        return job

    def _next_order(self) -> float:
        return max((j["order"] for j in self.jobs.values()), default=0.0) + 1.0

    # ---------------------------------------------------------------- queries
    def get(self, jid: str) -> dict | None:
        return self.jobs.get(jid)

    def ordered(self) -> list[dict]:
        """Active jobs first (running, capturing, queued by order), then the rest, newest first."""
        rank = {"running": 0, "capturing": 1, "waiting": 2, "queued": 3}
        with self.lock:
            jobs = list(self.jobs.values())
        active = sorted([j for j in jobs if j["status"] in rank],
                        key=lambda j: (rank[j["status"]], j["order"]))
        rest = sorted([j for j in jobs if j["status"] not in rank],
                      key=lambda j: j.get("updated", 0), reverse=True)
        return active + rest

    def for_run(self, run: str) -> list[dict]:
        return [j for j in self.ordered() if j["run"] == run]

    def active_for_meeting(self, meeting_id: str) -> dict | None:
        for j in self.ordered():
            if j.get("meeting_id") == meeting_id and j["status"] in ACTIVE | {"waiting"}:
                return j
        return None

    def stream_text(self, jid: str) -> str:
        job = self.jobs.get(jid) or {}
        return "".join(job.get("_stream") or [])

    @staticmethod
    def overall(job: dict) -> float:
        total = done = 0.0
        for st in job["stages"]:
            w = WEIGHTS.get(st["name"], 0.0)
            total += w
            if st["status"] in ("done", "skipped"):
                done += w
            elif st["status"] == "running":
                done += w * st["progress"]
        return done / total if total else 0.0

    def counts(self) -> dict:
        out = {"running": 0, "queued": 0, "capturing": 0, "waiting": 0, "failed": 0}
        for j in list(self.jobs.values()):
            if j["status"] in out:
                out[j["status"]] += 1
        return out

    # ---------------------------------------------------------------- controls
    def capture_done(self, jid: str, ok: bool = True, error: str | None = None,
                     detail: str = "", discard: bool = False) -> None:
        """The recording ended: queue the job (or fail it; or, with discard, cancel it)."""
        with self.lock:
            job = self.jobs[jid]
            st = self._stage(job, "capture")
            if st:
                st.update(status="done" if ok else "failed", ended=time.time(), progress=1.0,
                          detail=detail, error=error)
            job["status"] = ("canceled" if discard else "queued") if ok else "failed"
            job["error"] = None if ok else error
            self._changed(jid)
            self.cv.notify_all()

    def set_capture_detail(self, jid: str, detail: str) -> None:
        job = self.jobs.get(jid)
        st = self._stage(job, "capture") if job else None
        if st and st["status"] == "running" and st["detail"] != detail:
            st["detail"] = detail
            self._changed(jid, persist=False)

    def cancel(self, jid: str) -> str:
        """Queued -> canceled; running -> asks the stage to stop (it ends as canceled)."""
        with self.lock:
            job = self.jobs[jid]
            if job["status"] == "running":
                self._cancel.setdefault(jid, threading.Event()).set()
                self.log(jid, "cancel requested; stopping after the current step's next check")
                return "cancelling"
            if job["status"] in ("queued", "waiting", "capturing"):
                job["status"] = "canceled"
                job["decision"] = None
                for st in job["stages"]:
                    if st["status"] == "running":
                        st.update(status="canceled", ended=time.time())
                self.log(jid, "canceled")
                self._changed(jid)
                return "canceled"
            return job["status"]

    def retry(self, jid: str, from_stage: str | None = None) -> None:
        """Run again from the failed/canceled stage (or `from_stage`); finished stages stay done."""
        with self.lock:
            job = self.jobs[jid]
            if job["status"] in ("running", "capturing"):
                raise ValueError("this job is still running")
            names = [s["name"] for s in job["stages"]]
            start = names.index(from_stage) if from_stage in names else None
            for i, st in enumerate(job["stages"]):
                if st["name"] == "capture":
                    if st["status"] != "done":
                        st.update(status="skipped", detail="using what was recorded")
                    continue
                again = (start is not None and i >= start) or st["status"] in ("failed", "canceled", "pending")
                if again:
                    st.update(status="pending", progress=0.0, detail="", error=None,
                              started=None, ended=None)
            job.update(status="queued", error=None, decision=None, order=self._next_order())
            job.pop("_stream", None)
            self.log(jid, f"retry{' from ' + LABELS[from_stage] if start is not None else ''}")
            self._changed(jid)
            self.cv.notify_all()

    def remove(self, jid: str) -> None:
        with self.lock:
            job = self.jobs.get(jid)
            if job is None:
                return
            if job["status"] in ("running", "capturing"):
                raise ValueError("cancel the job first")
            del self.jobs[jid]
            self._file(jid).unlink(missing_ok=True)
        self._emit("removed", jid)

    def clear_finished(self) -> int:
        with self.lock:
            ids = [j["id"] for j in self.jobs.values() if j["status"] in ("done", "canceled")]
        for jid in ids:
            self.remove(jid)
        return len(ids)

    def move(self, jid: str, delta: int) -> None:
        """Swap with the neighbouring queued job (delta -1 = earlier, +1 = later)."""
        with self.lock:
            queued = sorted([j for j in self.jobs.values() if j["status"] == "queued"],
                            key=lambda j: j["order"])
            ids = [j["id"] for j in queued]
            if jid not in ids:
                return
            i = ids.index(jid)
            k = i + delta
            if not 0 <= k < len(queued):
                return
            queued[i]["order"], queued[k]["order"] = queued[k]["order"], queued[i]["order"]
            self._changed(queued[i]["id"])
            self._changed(queued[k]["id"])

    def run_next(self, jid: str) -> None:
        with self.lock:
            job = self.jobs[jid]
            if job["status"] != "queued":
                return
            job["order"] = min(j["order"] for j in self.jobs.values()) - 1.0
            self._changed(jid)

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            self.paused = bool(paused)
            self.cv.notify_all()
        self.on_pause_change(self.paused)
        self._emit("queue", "", {"paused": self.paused})

    def resolve(self, jid: str, retry_stage: str | None = None, note: str = "",
                options: dict | None = None) -> None:
        """Answer a waiting job: re-queue it from `retry_stage`, or finish it."""
        with self.lock:
            job = self.jobs[jid]
            job["decision"] = None
            if options:
                job["options"].update(options)
            if note:
                self.log(jid, note)
            if retry_stage:
                st = self._stage(job, retry_stage)
                st.update(status="pending", progress=0.0, detail="", error=None)
                job["status"] = "queued"
                job["order"] = min((j["order"] for j in self.jobs.values()), default=0.0) - 1.0
            else:
                st = self._stage(job, "publish")
                if st and st["status"] == "waiting":
                    st.update(status="done", ended=time.time(), detail=note)
                job["status"] = "done"
            self._changed(jid)
            self.cv.notify_all()

    @staticmethod
    def _stage(job: dict, name: str) -> dict | None:
        return next((s for s in job["stages"] if s["name"] == name), None)

    # ---------------------------------------------------------------- worker
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, daemon=True, name="job-queue")
            self._thread.start()

    def stop(self) -> None:
        with self.lock:
            self._stopping = True
            for ev in self._cancel.values():
                ev.set()
            self.cv.notify_all()

    def _pick(self) -> dict | None:
        queued = [j for j in self.jobs.values() if j["status"] == "queued"]
        return min(queued, key=lambda j: j["order"]) if queued else None

    def _worker(self) -> None:
        was_busy = False
        while True:
            with self.lock:
                while not self._stopping and (self.paused or self._pick() is None):
                    if was_busy and self._pick() is None:
                        was_busy = False
                        threading.Thread(target=self._idle, daemon=True).start()
                    self.cv.wait(timeout=5)
                if self._stopping:
                    return
                job = self._pick()
                job["status"] = "running"
                self.current = job["id"]
                cancel = self._cancel[job["id"]] = threading.Event()
                self._changed(job["id"])
            was_busy = True
            try:
                self._run(job, cancel)
            finally:
                with self.lock:
                    self.current = None
                    self._cancel.pop(job["id"], None)
                    self._changed(job["id"])
                self._emit("finished", job["id"], job["status"])

    def _idle(self) -> None:
        try:
            self.on_idle()
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _run(self, job: dict, cancel: threading.Event) -> None:
        jid = job["id"]
        job.pop("_stream", None)
        for st in job["stages"]:
            if st["status"] in ("done", "skipped"):
                continue
            st.update(status="running", started=time.time(), ended=None, progress=0.0, detail="",
                      error=None)
            self._changed(jid)
            ctx = Ctx(self, job, st, cancel)
            try:
                ctx.check()
                res = self.runner(st["name"], job, ctx)
            except Canceled:
                st.update(status="canceled", ended=time.time())
                job["status"] = "canceled"
                self.log(jid, f"{st['label']}: canceled")
                return
            except Exception as e:  # noqa: BLE001 - shown in the Live tab, Retry resumes here
                msg = str(e) or type(e).__name__
                st.update(status="failed", ended=time.time(), error=msg)
                job.update(status="failed", error=f"{st['label']}: {msg}")
                self.log(jid, f"{st['label']} failed: {msg}")
                if not isinstance(e, (ValueError, FileNotFoundError, RuntimeError)):
                    self.log(jid, traceback.format_exc().strip().splitlines()[-1])
                return
            if isinstance(res, tuple) and res and res[0] == "waiting":
                st.update(status="waiting", detail="waiting for you")
                job.update(status="waiting", decision=res[1])
                self.log(jid, "waiting for your decision")
                return
            st.update(status="skipped" if res == "skipped" else "done", ended=time.time(),
                      progress=1.0)
            self._changed(jid)
        job["status"] = "done"
        self.log(jid, "finished")
