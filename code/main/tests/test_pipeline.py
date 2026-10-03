"""Controller stages end to end with a fake Ollama (HTTP, streaming) and a fake Meetily client."""
import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import pytest

from core.controller import Controller
from core.meetily_client import MeetilyError

SUMMARY = "# Budget Review\n\n**Summary**\n\nWe agreed the Q3 budget.\n\n**Key Decisions**\n\n- Approve it"


class FakeOllama:
    def __init__(self):
        self.requests = []
        self.unloaded = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({"models": [{"name": "qwen3-vl:2b-instruct"}]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/generate":
                    outer.unloaded.append(req["model"])
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"{}")
                    return
                outer.requests.append(req)
                is_image = any(m.get("images") for m in req["messages"])
                text = "A bar chart of Q3 costs by team." if is_image else SUMMARY
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for i in range(0, len(text), 7):
                    self.wfile.write((json.dumps({"message": {"content": text[i:i + 7]},
                                                  "done": False}) + "\n").encode())
                self.wfile.write((json.dumps({"done": True, "eval_count": 42,
                                              "prompt_eval_count": 300}) + "\n").encode())

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"


class FakeMeetily:
    def __init__(self, current: str = ""):
        self.current = current
        self.puts = []

    def __call__(self, *a, **kw):
        return self

    def write_status(self):
        return {"ok": True, "reason": "ok", "scopes": ["read", "write"], "message": ""}

    def require_write(self):
        pass

    def get_transcript(self, mid):
        return {"segments": [{"text": "Let's look at the Q3 budget.", "audio_start_time": 1.0},
                             {"text": "As you can see costs went up.", "audio_start_time": 6.0}]}

    def get_summary(self, mid):
        if not self.current:
            raise MeetilyError(404, {"error": {"code": "not_found"}})
        return {"status": "completed", "result": {"markdown": self.current}}

    def put_summary(self, mid, text):
        self.puts.append(text)
        self.current = text

    def get_meeting(self, mid):
        return {"title": "New Meeting 4:36 PM", "created_at": "2026-10-03T10:00:30Z"}

    def rename_meeting(self, mid, title):
        self.renamed = title


def wait_for(cond, timeout=10.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def make_run(ctl: Controller, mid="meeting-1") -> str:
    run = ctl.store.create_run("capture", "test", meeting_id=mid, recording_meeting_id=mid,
                               offset=0.0, offset_manual=True)
    d = ctl.store.run_dir(run)
    (d / "images").mkdir()
    img = np.full((200, 300, 3), 255, np.uint8)
    cv2.rectangle(img, (40, 40), (260, 160), (0, 0, 255), -1)
    cv2.imwrite(str(d / "images" / "screen_001.jpg"), img)
    ctl.store.save_screenshots(run, {"duration": 20, "screenshots": [
        {"id": 1, "image": "images/screen_001.jpg", "start": 0, "end": 10, "type": "diagram", "text": ""},
        {"id": 2, "image": "images/screen_001.jpg", "start": 10, "end": 20, "type": "slide",
         "text": "Q3 budget\nCosts +12%"}]})
    return run


@pytest.fixture
def setup(tmp_path):
    oll = FakeOllama()
    def make(current=""):
        meet = FakeMeetily(current)
        ctl = Controller(tmp_path, client_factory=meet, sleep=lambda s: None, start_services=False)
        ctl.settings.update({"ollama_url": oll.url})
        ctl.queue.start()
        return ctl, meet
    yield make, oll
    oll.httpd.shutdown()


def test_describe_summarize_publish(setup):
    make, oll = setup
    ctl, meet = make()
    run = make_run(ctl)
    tokens = []
    ctl.queue.subscribe(lambda kind, jid, p: tokens.append(p) if kind == "token" else None)
    job = ctl.queue.create(run=run, title="t", meeting_id="meeting-1",
                           stages=["describe", "summarize", "publish"])
    wait_for(lambda: ctl.queue.get(job["id"])["status"] in ("done", "failed"))
    j = ctl.queue.get(job["id"])
    assert j["status"] == "done", j["log"]
    shots = ctl.store.screenshots(run)["screenshots"]
    assert shots[0]["description"] == "A bar chart of Q3 costs by team."
    # the summary prompt carries the description and the speech, all text (no images)
    summ_req = [r for r in oll.requests if not any(m.get("images") for m in r["messages"])][0]
    user = summ_req["messages"][1]["content"]
    assert "Shows: \"A bar chart" in user and "Q3 budget" in user
    assert all(s.get("description") for s in shots)           # every screen is described
    # the log shows what each screen showed and the screen context given to the model
    log = "\n".join(j["log"])
    assert "shows: A bar chart of Q3 costs by team." in log
    assert "screen context given to the model (2 screens):" in log and 'OCR: "Q3 budget' in log
    assert summ_req["options"]["num_gpu"] == 0 and summ_req["options"]["num_ctx"] == 16384
    assert "".join(tokens).startswith("# Budget Review")
    assert meet.puts and meet.puts[0].startswith("**Summary**")      # title line stripped
    assert ctl.store.meta(run)["meeting_title"] == "Budget Review"
    assert (ctl.store.run_dir(run) / "published.json").exists()
    # queue empty -> model unloaded
    wait_for(lambda: oll.unloaded == ["qwen3-vl:2b-instruct"])


def test_existing_meetily_summary_asks_then_replace(setup):
    make, _ = setup
    ctl, meet = make(current="Meetily's own summary text")
    run = make_run(ctl)
    job = ctl.queue.create(run=run, title="t", meeting_id="meeting-1",
                           stages=["summarize", "publish"])
    wait_for(lambda: ctl.queue.get(job["id"])["status"] == "waiting")
    assert meet.puts == []
    assert ctl.pending_decisions()[0]["meeting_id"] == "meeting-1"
    ctl.resolve_overwrite(run, "replace", job["id"])
    wait_for(lambda: ctl.queue.get(job["id"])["status"] == "done")
    assert len(meet.puts) == 1
    assert ctl.pending_decisions() == []
    backups = list((ctl.store.run_dir(run) / "backups").glob("*.json"))
    assert backups and "Meetily's own" in backups[0].read_text("utf-8")


def test_keep_meetily_summary(setup):
    make, _ = setup
    ctl, meet = make(current="Meetily's own summary text")
    run = make_run(ctl)
    job = ctl.queue.create(run=run, title="t", meeting_id="meeting-1", stages=["summarize", "publish"])
    wait_for(lambda: ctl.queue.get(job["id"])["status"] == "waiting")
    ctl.resolve_overwrite(run, "keep", job["id"])
    assert ctl.queue.get(job["id"])["status"] == "done"
    assert meet.puts == []
    # asked again later (e.g. summary.completed): the kept fingerprint means no new question
    j2 = ctl.queue.create(run=run, title="t", meeting_id="meeting-1", stages=["publish"])
    wait_for(lambda: ctl.queue.get(j2["id"])["status"] == "done")
    assert meet.puts == []


def test_no_meeting_skips_summary(setup):
    make, _ = setup
    ctl, _ = make()
    run = make_run(ctl, mid=None)
    job = ctl.queue.create(run=run, title="t", stages=["summarize", "publish"])
    wait_for(lambda: ctl.queue.get(job["id"])["status"] == "done")
    assert [s["status"] for s in ctl.queue.get(job["id"])["stages"]] == ["skipped", "skipped"]


def test_webhook_verify_dedup_and_ignore(tmp_path):
    ctl = Controller(tmp_path, client_factory=FakeMeetily(), start_services=False)
    ctl.sub.file.write_text(json.dumps({"hmac_secret": "s3cret"}), "utf-8")
    body = json.dumps({"event_id": "evt_1", "event": "something.else"}).encode()
    ts = str(int(time.time()))
    sig = "sha256=" + hmac.new(b"s3cret", ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    h = {"X-Meetily-Timestamp": ts, "X-Meetily-Signature": sig}
    assert ctl.handle_webhook(body, h) == (200, "accepted")
    assert ctl.handle_webhook(body, h) == (200, "duplicate")
    assert ctl.handle_webhook(body, {**h, "X-Meetily-Signature": "sha256=00"})[0] == 401
    ctl.handle_event(ctl._evq.get_nowait())
    assert ctl.recent_events()[0]["status"] == "done"


def test_recording_started_asks_in_window(tmp_path):
    class Ui:
        req = None
        def pick_window(self, r): Ui.req = r
        def pick_closed(self, r): pass
        def notify(self, *a, **k): pass
        def automation_changed(self): pass
        def show_live(self, j): pass
        def capture_changed(self): pass
    ctl = Controller(tmp_path, ui=Ui(), client_factory=FakeMeetily(), start_services=False)
    out = ctl.on_started("meeting-9", "2026-10-03T10:00:00Z", lambda m: None)
    assert "asked" in out and Ui.req.meeting_id == "meeting-9"
    # recording ends before anyone picks: the request is cancelled, no capture, but the meeting
    # still shows up with a transcript-only summary job
    out = ctl.on_ended("meeting-9", "recording.stopped", lambda m: None)
    assert "transcript-only" in out
    assert Ui.req.wait(1) == {"cancelled": True}
    job = ctl.queue.ordered()[0]
    assert job["meeting_id"] == "meeting-9" and [s["name"] for s in job["stages"]] == ["summarize", "publish"]
    assert ctl.store.run_for_meeting("meeting-9") == job["run"]
    # a duplicate stop event doesn't add it twice; a failed recording adds nothing
    assert ctl.on_ended("meeting-9", "recording.stopped", lambda m: None) == "this meeting is already here"
    assert "nothing to do" in ctl.on_ended("meeting-10", "recording.failed", lambda m: None)


def test_transcript_only_summary(setup):
    make, oll = setup
    ctl, meet = make()
    ctl.transcript_only("meeting-7", "recording.stopped", lambda m: None)
    job = ctl.queue.ordered()[0]
    wait_for(lambda: ctl.queue.get(job["id"])["status"] in ("done", "failed"))
    j = ctl.queue.get(job["id"])
    assert j["status"] == "done", j["log"]
    user = oll.requests[-1]["messages"][1]["content"]
    assert "] [SCREEN] (" not in user and "Q3 budget" in user
    assert meet.puts                                     # written to Meetily (it had none)


def test_pictures_reach_the_summary_input():
    """A quiz's pictures are the content ("find the biggest fruit"): described, not dropped."""
    from core.transcript import build_input
    shots = [{"id": 1, "start": 0, "end": 5, "type": "diagram", "text": "Find the biggest fruit?",
              "description": "A watermelon, an apple and a grape."},
             {"id": 2, "start": 5, "end": 9, "type": "slide", "text": "Q3 budget"}]
    text = build_input([{"text": "find the biggest fruit", "audio_start_time": 0}], shots)
    assert 'Shows: "A watermelon, an apple and a grape." OCR: "Find the biggest fruit?"' in text
    assert 'OCR: "Q3 budget"' in text


def test_old_vlm_prompt_is_upgraded(tmp_path):
    import json
    from core.config import OLD_VLM_PROMPTS, VLM_PROMPT, Settings
    (tmp_path / "settings.json").write_text(json.dumps({"vlm_prompt": next(iter(OLD_VLM_PROMPTS)),
                                                        "device": "gpu"}), "utf-8")
    s = Settings(tmp_path)
    assert s["vlm_prompt"] == VLM_PROMPT and s["device"] == "gpu"


def test_cleanup_drops_loops_and_notes():
    from core.summarizer import clean_output
    loop = "The meeting was not structured as a formal meeting. " * 30
    t = clean_output(f"# T\n\n**Summary**\n\nIt was a clip. {loop}\n\nNote: based on speech only.")
    assert t.count("not structured") == 1 and "Note:" not in t




def test_ollama_is_started_when_down(monkeypatch):
    import core.ollama as ol
    started = []
    monkeypatch.setattr(ol, "find_executable", lambda: ("C:/x/ollama app.exe", []))
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: started.append(cmd))
    state = {"up": False}
    monkeypatch.setattr(ol.Ollama, "up", lambda self, timeout=2: state["up"] or bool(started))
    monkeypatch.setattr(ol.time, "sleep", lambda s: None)
    assert ol.Ollama("http://127.0.0.1:11434").ensure_running(wait=5)
    assert started == [["C:/x/ollama app.exe"]]
    # already up: nothing started; a remote Ollama is never started from here
    started.clear()
    state["up"] = True
    assert ol.Ollama("http://127.0.0.1:11434").ensure_running() and started == []
    monkeypatch.setattr(ol.Ollama, "up", lambda self, timeout=2: False)
    assert not ol.Ollama("http://10.0.0.5:11434").ensure_running() and started == []


def test_silent_recording_is_done_not_failed(setup):
    make, _ = setup
    ctl, meet = make()
    meet.get_transcript = lambda mid: {"segments": []}
    ctl.transcript_only("meeting-silent", "recording.stopped", lambda m: None)
    job = ctl.queue.ordered()[0]
    wait_for(lambda: ctl.queue.get(job["id"])["status"] in ("done", "failed"))
    j = ctl.queue.get(job["id"])
    assert j["status"] == "done", j["log"]
    assert [s["status"] for s in j["stages"]] == ["skipped", "skipped"] and meet.puts == []


def test_made_up_decisions_are_cleared_when_nobody_committed_to_anything():
    from core.summarizer import clear_unspoken_commitments, has_commitments
    report = ("# Quiz\n\n**Summary**\n\nA kids' quiz was played.\n\n**Key Decisions**\n\n"
              "- Team agreed to improve videos.\n\n**Action Items**\n\n| **Owner** | Task | Due |\n"
              "| --- | --- | --- |\n| Speaker 70 | Develop a new quiz | 48 hours |\n\n"
              "**Discussion Highlights**\n\n- Find the biggest fruit.")
    quiz = [{"text": "Find the biggest fruit."}, {"text": "Okay, I'm gonna get it."}]
    out = clear_unspoken_commitments(report, quiz)
    assert "Develop a new quiz" not in out and "improve videos" not in out
    assert out.count("None noted in this section.") == 2 and "Find the biggest fruit" in out
    # a real commitment keeps them
    meeting = quiz + [{"text": "Can you send the deck by Friday?"}]
    assert has_commitments(meeting) and clear_unspoken_commitments(report, meeting) == report


def test_bad_saved_settings_fall_back_to_defaults(tmp_path):
    """Seen live: describe_screens = null in settings.json made Describe skip every screen."""
    import json
    from core.config import Settings
    (tmp_path / "settings.json").write_text(json.dumps(
        {"describe_screens": None, "device": "gpu", "vlm_max_tokens": 250, "theme": "purple",
         "max_tokens": "lots"}), "utf-8")
    s = Settings(tmp_path)
    assert s["describe_screens"] == "all" and s["device"] == "gpu" and s["vlm_max_tokens"] == 500
    assert s["theme"] == "system" and s["max_tokens"] == 2000
    s.update({"describe_screens": "everything"})                # not a valid choice: ignored
    assert s["describe_screens"] == "all"


def test_describe_again_redoes_every_screen(setup):
    make, oll = setup
    ctl, _ = make()
    run = make_run(ctl)
    doc = ctl.store.screenshots(run)
    for sh in doc["screenshots"]:
        sh["description"] = "old description"
    ctl.store.save_screenshots(run, doc)
    job = ctl.redescribe(run)
    wait_for(lambda: ctl.queue.get(job["id"])["status"] in ("done", "failed"))
    assert ctl.queue.get(job["id"])["status"] == "done"
    assert all(s["description"] == "A bar chart of Q3 costs by team."
               for s in ctl.store.screenshots(run)["screenshots"])
    assert [s["name"] for s in ctl.queue.get(job["id"])["stages"]] == ["describe"]
