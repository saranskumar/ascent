"""The job queue: order, stages, cancel, retry, reorder, pause, waiting, restart recovery."""
import threading
import time

import pytest

from core.errors import Canceled
from core.jobs import JobQueue


def wait_for(cond, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    raise AssertionError("timed out")


class Runner:
    def __init__(self):
        self.calls = []
        self.fail = set()
        self.block = threading.Event()
        self.block.set()
        self.wait_stage = None

    def __call__(self, name, job, ctx):
        self.calls.append((job["title"], name))
        if name == "extract":
            while not self.block.is_set():
                ctx.check()
                time.sleep(0.01)
            ctx.progress(0.5, "half")
            ctx.log("extracted")
        if name == "summarize":
            ctx.token("Hello ")
            ctx.token("world")
        if (job["title"], name) in self.fail:
            raise RuntimeError("boom")
        if name == self.wait_stage:
            return ("waiting", {"type": "overwrite"})
        return "skipped" if name == "describe" else None


@pytest.fixture
def q(tmp_path):
    r = Runner()
    q = JobQueue(tmp_path, r)
    q.runner_obj = r
    yield q
    q.stop()


def test_runs_in_order_and_finishes(q):
    a = q.create(run="r1", title="A")
    b = q.create(run="r2", title="B")
    q.start()
    wait_for(lambda: q.get(b["id"])["status"] == "done")
    titles = [t for t, _ in q.runner_obj.calls]
    assert titles.index("B") > max(i for i, t in enumerate(titles) if t == "A")
    st = {s["name"]: s["status"] for s in q.get(a["id"])["stages"]}
    assert st == {"extract": "done", "describe": "skipped", "summarize": "done", "publish": "done"}
    assert q.stream_text(a["id"]) == "Hello world"
    assert any("extracted" in ln for ln in q.get(a["id"])["log"])


def test_failure_then_retry_resumes_at_failed_stage(q):
    q.runner_obj.fail = {("A", "summarize")}
    a = q.create(run="r1", title="A")
    q.start()
    wait_for(lambda: q.get(a["id"])["status"] == "failed")
    assert "Summarize: boom" in q.get(a["id"])["error"]
    q.runner_obj.fail = set()
    q.runner_obj.calls.clear()
    q.retry(a["id"])
    wait_for(lambda: q.get(a["id"])["status"] == "done")
    assert [n for _, n in q.runner_obj.calls] == ["summarize", "publish"]


def test_cancel_running_job(q):
    q.runner_obj.block.clear()
    a = q.create(run="r1", title="A")
    b = q.create(run="r2", title="B")
    q.start()
    wait_for(lambda: q.get(a["id"])["status"] == "running")
    assert q.cancel(a["id"]) == "cancelling"
    wait_for(lambda: q.get(a["id"])["status"] == "canceled")
    q.runner_obj.block.set()
    wait_for(lambda: q.get(b["id"])["status"] == "done")


def test_pause_reorder_and_run_next(q):
    q.set_paused(True)
    q.start()
    a = q.create(run="r1", title="A")
    b = q.create(run="r2", title="B")
    c = q.create(run="r3", title="C")
    time.sleep(0.2)
    assert q.runner_obj.calls == []                 # paused: nothing runs
    q.run_next(c["id"])
    q.move(a["id"], +1)                              # A after B
    q.set_paused(False)
    wait_for(lambda: all(q.get(j["id"])["status"] == "done" for j in (a, b, c)))
    firsts = []
    for t, _ in q.runner_obj.calls:
        if t not in firsts:
            firsts.append(t)
    assert firsts == ["C", "B", "A"]


def test_waiting_then_resolve(q):
    q.runner_obj.wait_stage = "publish"
    a = q.create(run="r1", title="A")
    q.start()
    wait_for(lambda: q.get(a["id"])["status"] == "waiting")
    assert q.get(a["id"])["decision"]["type"] == "overwrite"
    q.runner_obj.wait_stage = None
    q.resolve(a["id"], retry_stage="publish", options={"force_replace": True})
    wait_for(lambda: q.get(a["id"])["status"] == "done")
    assert q.get(a["id"])["options"]["force_replace"] is True


def test_capturing_job_waits_for_capture_done(q):
    q.start()
    a = q.create(run="r1", title="A", capturing=True)
    time.sleep(0.2)
    assert q.get(a["id"])["status"] == "capturing"
    q.capture_done(a["id"])
    wait_for(lambda: q.get(a["id"])["status"] == "done")
    assert q.get(a["id"])["stages"][0]["status"] == "done"


def test_restart_requeues_running_and_fails_capturing(tmp_path):
    q1 = JobQueue(tmp_path, Runner())
    a = q1.create(run="r1", title="A")
    b = q1.create(run="r2", title="B", capturing=True)
    q1.jobs[a["id"]]["status"] = "running"
    q1.jobs[a["id"]]["stages"][0]["status"] = "running"
    q1._save(q1.jobs[a["id"]])
    q2 = JobQueue(tmp_path, Runner())
    assert q2.get(a["id"])["status"] == "queued"
    assert q2.get(a["id"])["stages"][0]["status"] == "pending"
    assert q2.get(b["id"])["status"] == "failed"
    q2.retry(b["id"])                                # capture can't rerun: skipped, rest queued
    assert q2.get(b["id"])["stages"][0]["status"] == "skipped"
    assert q2.get(b["id"])["status"] == "queued"


def test_remove_and_clear(q):
    a = q.create(run="r1", title="A")
    q.start()
    wait_for(lambda: q.get(a["id"])["status"] == "done")
    assert q.clear_finished() == 1
    assert q.get(a["id"]) is None


def test_overall_progress():
    job = {"stages": [{"name": "extract", "status": "done", "progress": 1},
                      {"name": "describe", "status": "running", "progress": 0.5},
                      {"name": "summarize", "status": "pending", "progress": 0},
                      {"name": "publish", "status": "pending", "progress": 0}]}
    assert JobQueue.overall(job) == pytest.approx(0.35 + 0.125)


def test_canceled_import():
    assert issubclass(Canceled, Exception)
