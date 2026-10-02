"""Web UI backend: the safety properties (no Meetily, no Gemini needed)."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from vcs.server import App, Handler


@pytest.fixture()
def srv(tmp_path):
    (tmp_path / "r1" / "images").mkdir(parents=True)
    (tmp_path / "r1" / "screenshots.json").write_text(
        json.dumps({"duration": 10, "screenshots": [{"id": 1, "start": 0, "end": 5, "type": "slide",
                                                      "text": "x", "image": "images/a.jpg"}]}))
    (tmp_path / "secret.txt").write_text("nope")
    Handler.app = App(tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], tmp_path
    httpd.shutdown()


def call(port, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    return r.status, r.read()


def test_runs_listed_and_page_served(srv):
    port, _ = srv
    s, b = call(port, "GET", "/api/runs")
    assert s == 200 and json.loads(b)["runs"][0]["id"] == "r1"
    s, b = call(port, "GET", "/")
    assert s == 200 and b"Visual Context Summary" in b


def test_writes_need_custom_header(srv):
    port, tmp = srv
    assert call(port, "DELETE", "/api/runs/r1")[0] == 403
    assert (tmp / "r1").exists()
    assert call(port, "DELETE", "/api/runs/r1", {"X-VCS": "1"})[0] == 200
    assert not (tmp / "r1").exists()


def test_foreign_host_rejected(srv):
    port, _ = srv
    assert call(port, "GET", "/api/runs", {"Host": "evil.example"})[0] == 403


def test_no_path_traversal(srv):
    port, _ = srv
    assert call(port, "GET", "/api/runs/..%2f..%2fx")[0] in (400, 404)
    assert call(port, "GET", "/api/runs/r1/images/..%2f..%2fsecret.txt")[0] in (400, 404)
    assert call(port, "GET", "/..%2f..%2fcli.py")[0] == 404
    assert call(port, "GET", "/%2e%2e/cli.py")[0] == 404


def test_publish_guards(srv):
    port, tmp = srv
    H = {"X-VCS": "1"}
    body = lambda d: json.dumps(d)
    # no confirmation
    assert call(port, "POST", "/api/runs/r1/publish", H, body({"meeting_id": "m1"}))[0] == 400
    # demo transcript can't be written back
    assert call(port, "POST", "/api/runs/r1/publish", H, body({"meeting_id": "fixture", "confirm": True}))[0] == 400
    # no summary yet
    assert call(port, "POST", "/api/runs/r1/publish", H, body({"meeting_id": "m1", "confirm": True}))[0] == 400
    # summary generated for a different meeting
    (tmp / "r1" / "summary.md").write_text("# x")
    (tmp / "r1" / "summary.meta.json").write_text(json.dumps({"meeting_id": "other"}))
    s, b = call(port, "POST", "/api/runs/r1/publish", H, body({"meeting_id": "m1", "confirm": True}))
    assert s == 400 and b"different transcript" in b


def test_pending_overwrite_listed_and_resolved(srv, monkeypatch):
    port, tmp = srv
    H = {"X-VCS": "1"}
    assert json.loads(call(port, "GET", "/api/pending")[1]) == {"pending": []}
    assert Handler.app.ui_recently_seen()                       # the poll marks the UI as open
    (tmp / "r1" / "pending_overwrite.json").write_text(json.dumps(
        {"run": "r1", "meeting_id": "m1", "reason": "existing", "at": "2026-10-02T10:00:00+00:00"}))
    pend = json.loads(call(port, "GET", "/api/pending")[1])["pending"]
    assert [p["run"] for p in pend] == ["r1"]
    # needs the custom header and a valid action
    assert call(port, "POST", "/api/runs/r1/overwrite", body=b'{"action":"keep"}')[0] == 403
    assert call(port, "POST", "/api/runs/r1/overwrite", H, b'{"action":"nope"}')[0] == 400
    assert (tmp / "r1" / "pending_overwrite.json").exists()

    class FakeMeetily:
        def get_summary(self, mid):
            return {"result": {"markdown": "Meetily's own"}}
    monkeypatch.setattr("vcs.server.MeetilyClient", FakeMeetily)
    assert call(port, "POST", "/api/runs/r1/overwrite", H, b'{"action":"keep"}')[0] == 200
    assert not (tmp / "r1" / "pending_overwrite.json").exists()
    from vcs.writeback import fingerprint
    kept = json.loads((tmp / "r1" / "kept_meetily.json").read_text())
    assert kept["fingerprint"] == fingerprint("Meetily's own")
    assert call(port, "POST", "/api/runs/r1/overwrite", H, b'{"action":"keep"}')[0] == 404   # nothing pending


def test_overwrite_runs_publish_and_clears_pending_only_on_success(srv, monkeypatch):
    import time
    port, tmp = srv
    app = Handler.app
    (tmp / "r1" / "pending_overwrite.json").write_text(json.dumps(
        {"run": "r1", "meeting_id": "m1", "at": "x"}))
    outcome = {"status": "failed"}
    monkeypatch.setattr(app, "start_publish", lambda run, mid: {"id": "j1"})
    monkeypatch.setattr(app, "wait_job", lambda job: dict(outcome))
    assert call(port, "POST", "/api/runs/r1/overwrite", {"X-VCS": "1"}, b'{"action":"overwrite"}')[0] == 200
    for _ in range(50):
        if "r1" not in app._resolving:
            break
        time.sleep(0.05)
    assert (tmp / "r1" / "pending_overwrite.json").exists()     # failed: the question stays
    outcome["status"] = "done"
    call(port, "POST", "/api/runs/r1/overwrite", {"X-VCS": "1"}, b'{"action":"overwrite"}')
    for _ in range(50):
        if not (tmp / "r1" / "pending_overwrite.json").exists():
            break
        time.sleep(0.05)
    assert not (tmp / "r1" / "pending_overwrite.json").exists()
