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
