"""Step (e): Subscribe -> Verify -> Deduplicate -> Fetch -> Act, with fakes (no Meetily needed)."""
import hashlib
import hmac
import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from vcs.meetily_client import MeetilyError, MeetilyOffline
from vcs.webhooks import EVENTS, Automation, EventStore, Subscription, meeting_id_of, verify_signature

SECRET = "s3cr3t"


def sign(body: bytes, ts: int | None = None, secret: str = SECRET):
    ts = str(ts if ts is not None else int(time.time()))
    sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Meetily-Timestamp": ts, "X-Meetily-Signature": "sha256=" + sig}


def event(eid="e1", kind="summary.completed", mid="m1"):
    return {"schema_version": 1, "event_id": eid, "event": kind,
            "occurred_at": "2026-10-02T11:00:00.000000000+00:00",
            "resource": {"kind": "meeting", "id": mid}, "delivery_id": "d1"}


# ---------------------------------------------------------------- verify
def test_signature_matches_meetily_reference():
    body = b'{"event_id":"e1"}'
    h = sign(body, 1790000000)
    # the reference helper's construction: hex HMAC-SHA256 over b"{ts}." + body
    assert h["X-Meetily-Signature"] == "sha256=" + hmac.new(
        b"s3cr3t", b"1790000000." + body, hashlib.sha256).hexdigest()
    assert verify_signature(SECRET, h["X-Meetily-Timestamp"], body, h["X-Meetily-Signature"],
                            now=1790000010)


def test_signature_rejections():
    body = b'{"event_id":"e1"}'
    h = sign(body, 1790000000)
    ts, sig = h["X-Meetily-Timestamp"], h["X-Meetily-Signature"]
    assert not verify_signature("wrong", ts, body, sig, now=1790000000)
    assert not verify_signature(SECRET, ts, body + b" ", sig, now=1790000000)     # tampered
    assert not verify_signature(SECRET, ts, body, sig, now=1790000000 + 3600)     # replayed
    assert not verify_signature(SECRET, "", body, sig, now=1790000000)
    assert not verify_signature(SECRET, ts, body, "", now=1790000000)
    assert not verify_signature(SECRET, "abc", body, sig, now=1790000000)
    assert verify_signature(SECRET, ts, body, sig[len("sha256="):], now=1790000000)  # bare hex


def test_meeting_id_from_payload():
    assert meeting_id_of(event(mid="m9")) == "m9"
    assert meeting_id_of({"meeting_id": "m2", "resource": {"id": "x"}}) == "m2"
    assert meeting_id_of({"resource": {}}) is None


# ---------------------------------------------------------------- dedup
def test_event_store_dedups_across_restarts(tmp_path):
    s = EventStore(tmp_path)
    assert s.accept(event("e1")) and not s.accept(event("e1"))
    s.update("e1", "done", "ok")
    s2 = EventStore(tmp_path)                       # a new process
    assert not s2.accept(event("e1"))
    assert s2.records()[0]["status"] == "done" and s2.records()[0]["log"][0].endswith("ok")
    with pytest.raises(ValueError):
        s.accept({"event_id": "../x"})


# ---------------------------------------------------------------- fakes
class FakeClient:
    def __init__(self, state):
        self.s = state

    def create_webhook(self, url, events, delivery_mode="at-least-once"):
        if self.s.get("create_error"):
            raise self.s["create_error"]
        self.s["created"] = self.s.get("created", 0) + 1
        wid = f"w{self.s['created']}"
        self.s["hooks"][wid] = {"id": wid, "approval_state": "pending"}
        return {"id": wid, "hmac_secret": SECRET, "url": url, "events": events}

    def get_webhook(self, wid):
        if wid not in self.s["hooks"]:
            raise MeetilyError(404, {"error": {"code": "not_found"}})
        return self.s["hooks"][wid]

    def delete_webhook(self, wid):
        self.s["hooks"].pop(wid, None)
        self.s["deleted"] = self.s.get("deleted", 0) + 1

    def recording(self):
        return {"state": "recording", "active_meeting_id": "m1"}

    def write_status(self):
        return {"ok": self.s.get("write_ok", True)}

    def get_transcript(self, mid):
        self.s["transcript_calls"] = self.s.get("transcript_calls", 0) + 1
        if self.s.get("empty_until", 0) >= self.s["transcript_calls"]:
            return {"segments": []}
        return {"segments": [{"text": "hello", "audio_start_time": 1.0}]}

    def get_summary(self, mid):
        return {"status": "completed", "result": {"markdown": self.s.get("meetily_summary", "")}}


class FakeRecorder:
    def __init__(self, title="Slides"):
        self.title, self.running = title, True


class FakeApp:
    def __init__(self, data: Path):
        self.data = data
        self.settings_ = {"auto_capture": True, "auto_publish": True}
        self.recorder, self.capture_run = None, None
        self.metas, self.calls, self.watch = {}, [], 111
        self.summary_ok, self.publish_error = True, None

    def settings(self):
        return self.settings_

    def find_watch_window(self):
        return self.watch

    def start_capture(self, hwnd):
        self.calls.append(("start_capture", hwnd))
        self.recorder, self.capture_run = FakeRecorder(), "capture-1"
        (self.data / "capture-1").mkdir(exist_ok=True)

    def attach_recording(self, run, mid, at):
        self.metas[run] = {"recording_meeting_id": mid, "recording_started_at": at}

    def run_dir(self, run):
        return self.data / run

    def meta(self, d):
        return self.metas.get(Path(d).name, {})

    def stop_capture(self):
        self.calls.append(("stop_capture",))
        self.recorder = None
        (self.data / "capture-1" / "screenshots.json").write_text('{"screenshots": []}')
        return {"run": "capture-1", "job": "j-extract", "seconds": 42.0}

    def run_for_meeting(self, mid):
        return next((r for r, m in self.metas.items() if m.get("recording_meeting_id") == mid), None)

    def suggest_offset(self, run, mid):
        return {"offset": 12.5}

    def start_summarize(self, run, mid, offset, model):
        self.calls.append(("summarize", run, mid, offset))
        if self.summary_ok:
            (self.data / run / "summary.md").write_text("# T\n\nOURS")
            (self.data / run / "summary.meta.json").write_text(json.dumps({"meeting_id": mid}))
        return {"id": "j-sum"}

    def start_publish(self, run, mid):
        self.calls.append(("publish", run, mid))
        if not self.publish_error:
            from vcs.writeback import fingerprint
            (self.data / run / "published.json").write_text(json.dumps(
                {"meeting_id": mid, "fingerprint": fingerprint("OURS")}))
        return {"id": "j-pub"}

    def wait_job(self, job):
        jid = job["id"] if isinstance(job, dict) else job
        if jid == "j-sum" and not self.summary_ok:
            return {"status": "failed", "error": "boom", "log": []}
        if jid == "j-pub" and self.publish_error:
            return {"status": "failed", "error": self.publish_error, "log": []}
        return {"status": "done", "error": None, "log": ["...", "kept 3 screenshots", "x"]}


def make(tmp_path, **state):
    st = {"hooks": {}, **state}
    app = FakeApp(tmp_path)
    auto = Automation(app, "http://127.0.0.1:8765/webhook", client_factory=lambda: FakeClient(st),
                      sleep=lambda s: None)
    return app, auto, st


# ---------------------------------------------------------------- subscribe
def test_subscription_registers_once_and_reuses(tmp_path):
    app, auto, st = make(tmp_path)
    s1 = auto.sub.ensure()
    assert s1["state"] == "pending" and st["created"] == 1 and auto.sub.secret == SECRET
    st["hooks"]["w1"]["approval_state"] = "allowed"
    assert auto.sub.ensure()["state"] == "active" and st["created"] == 1      # reused, no new approval
    saved = json.loads((tmp_path / "webhook.json").read_text())
    assert sorted(saved["events"]) == sorted(EVENTS)


def test_subscription_recreated_when_deleted_or_changed(tmp_path):
    app, auto, st = make(tmp_path)
    auto.sub.ensure()
    st["hooks"].clear()                                   # deleted in Meetily
    auto.sub.ensure()
    assert st["created"] == 2
    auto2 = Subscription(tmp_path, "http://127.0.0.1:9999/webhook", lambda: FakeClient(st))
    auto2.ensure()                                        # URL changed: old one removed
    assert st["created"] == 3 and st["deleted"] == 1


@pytest.mark.parametrize("err,state", [
    (MeetilyError(400, {"error": {"code": "bad_request",
                                  "message": "url host is not allowed (loopback/private)"}}),
     "local_target_needed"),
    (MeetilyError(403, {"error": {"code": "webhooks_disabled", "message": "off"}}), "disabled"),
])
def test_subscription_setup_errors_are_explained(tmp_path, err, state):
    app, auto, st = make(tmp_path, create_error=err)
    out = auto.sub.ensure()
    assert out["state"] == state and "Settings > Integrations" in out["message"]


def test_subscription_offline_propagates(tmp_path):
    app, auto, st = make(tmp_path, create_error=MeetilyOffline(None, "refused"))
    with pytest.raises(MeetilyOffline):
        auto.sub.ensure()


# ---------------------------------------------------------------- verify + dedup at the door
def test_http_entry_verifies_and_dedups(tmp_path):
    app, auto, st = make(tmp_path)
    assert auto.handle_http(b"{}", {})[0] == 503           # not subscribed yet
    auto.sub.ensure()
    body = json.dumps(event("e7")).encode()
    assert auto.handle_http(body, {})[0] == 401
    assert auto.handle_http(body, sign(body, secret="nope"))[0] == 401
    assert auto.handle_http(b"not json", sign(b"not json"))[0] == 400
    assert auto.handle_http(body, sign(body)) == (200, "accepted")
    assert auto.handle_http(body, sign(body)) == (200, "duplicate")   # Meetily retry
    assert auto.q.qsize() == 1


# ---------------------------------------------------------------- act
LOG = lambda m: None  # noqa: E731


def meeting(app, auto):
    assert "capture started" in auto.process(event("a", "recording.started"), LOG)
    return auto.process(event("b", "recording.stopped"), LOG)


def test_full_meeting_flow_writes_on_stop(tmp_path):
    app, auto, st = make(tmp_path)
    out = meeting(app, auto)
    assert app.calls[0] == ("start_capture", 111)
    assert app.metas["capture-1"]["recording_started_at"].startswith("2026-10-02T11:00")
    assert "written to Meetily" in out
    assert ("summarize", "capture-1", "m1", 12.5) in app.calls and ("publish", "capture-1", "m1") in app.calls
    # Meetily's later summary event: it still shows ours (reformatted) -> nothing to do
    st["meetily_summary"] = "  ours  \n"
    assert "shows our summary" in auto.process(event("c", "summary.completed"), LOG)
    assert sum(c[0] == "publish" for c in app.calls) == 1


def test_meetily_replacing_ours_gets_ours_back_without_regenerating(tmp_path):
    app, auto, st = make(tmp_path)
    meeting(app, auto)
    st["meetily_summary"] = "**Summary**\n\nMeetily's own"
    assert "written to Meetily" in auto.process(event("c", "summary.completed"), LOG)
    assert sum(c[0] == "publish" for c in app.calls) == 2
    assert sum(c[0] == "summarize" for c in app.calls) == 1          # no second Gemini call


def test_meetily_busy_at_stop_then_written_on_its_summary_event(tmp_path):
    app, auto, st = make(tmp_path)
    app.publish_error = "Meetily is still generating a summary (status 'processing')"
    assert "ours goes in when its summary event arrives" in meeting(app, auto)
    app.publish_error = None
    assert "written to Meetily" in auto.process(event("c", "summary.completed"), LOG)
    assert sum(c[0] == "summarize" for c in app.calls) == 1


def test_waits_for_transcript_text(tmp_path):
    app, auto, st = make(tmp_path, empty_until=2)
    meeting(app, auto)
    assert st["transcript_calls"] == 3


def test_summary_event_without_capture_is_left_alone(tmp_path):
    app, auto, st = make(tmp_path)
    assert "no screen capture" in auto.process(event("x", "summary.completed", "other"), LOG)
    assert not app.calls


def test_auto_publish_off_and_no_write_key(tmp_path):
    app, auto, st = make(tmp_path)
    app.settings_["auto_publish"] = False
    assert "auto write-back is off" in meeting(app, auto)
    app.settings_["auto_publish"] = True
    st["write_ok"] = False
    assert "no usable write key" in auto.process(event("d", "summary.failed"), LOG)
    assert not any(c[0] == "publish" for c in app.calls)


def test_failed_recording_keeps_screens_without_summary(tmp_path):
    app, auto, st = make(tmp_path)
    auto.process(event("a", "recording.started"), LOG)
    assert "no summary" in auto.process(event("b", "recording.failed"), LOG)
    assert not any(c[0] == "summarize" for c in app.calls)


def test_no_window_or_capture_off(tmp_path):
    app, auto, st = make(tmp_path)
    app.watch = None
    assert "no window to watch" in auto.process(event("a", "recording.started"), LOG)
    app.settings_["auto_capture"] = False
    assert "auto capture is off" in auto.process(event("b", "recording.started"), LOG)
    assert "no capture running" in auto.process(event("c", "recording.stopped"), LOG)


def test_offline_event_is_parked_then_requeued(tmp_path):
    app, auto, st = make(tmp_path)
    auto.store.accept(event("z", "summary.completed"))

    def boom(ev, log):
        raise MeetilyOffline(None, "refused")
    auto.process = boom
    auto.handle_one(event("z", "summary.completed"))
    assert auto.store.with_status("offline")[0]["event_id"] == "z"
    # what the subscriber does when Meetily is reachable again
    auto.meetily_online = False
    st["hooks"] = {}
    for ev in auto.store.with_status("offline"):
        auto.store.update(ev["event_id"], "queued", "Meetily is back; retrying")
        auto.q.put(ev)
    assert auto.q.qsize() == 1


def test_failed_summary_marks_event_failed(tmp_path):
    app, auto, st = make(tmp_path)
    auto.process(event("a", "recording.started"), LOG)
    app.summary_ok = False
    auto.store.accept(event("b", "recording.stopped"))
    auto.handle_one(event("b", "recording.stopped"))
    rec = [r for r in auto.store.records() if r["event"]["event_id"] == "b"][0]
    assert rec["status"] == "failed" and "summary failed" in rec["log"][-1]


# ---------------------------------------------------------------- the real HTTP route
def test_server_webhook_route_uses_hmac_not_xvcs(tmp_path):
    from vcs.server import App, Handler
    app = App(tmp_path)
    st = {"hooks": {}}
    app.automation = Automation(app, "http://127.0.0.1/webhook", client_factory=lambda: FakeClient(st),
                                sleep=lambda s: None)
    app.automation.sub.ensure()
    Handler.app = app
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        port = httpd.server_address[1]
        body = json.dumps(event("srv1", "recording.paused")).encode()

        def post(headers):
            c = http.client.HTTPConnection("127.0.0.1", port)
            c.request("POST", "/webhook", body=body, headers=headers)
            r = c.getresponse()
            return r.status, json.loads(r.read())

        assert post({"Content-Type": "application/json"})[0] == 401
        assert post(sign(body)) == (200, {"result": "accepted"})
        assert post(sign(body)) == (200, {"result": "duplicate"})
    finally:
        httpd.shutdown()
