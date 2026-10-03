"""Step (c): backup-then-PUT ordering and failure handling, with a fake Meetily client."""
import json

import pytest

from core.meetily_client import MeetilyError
from core.writeback import WritebackError, publish, published_meetings, split_title, summary_text


class FakeClient:
    def __init__(self, summary=None, can_write=True, get_error=None, put_error=None,
                 title="Weekly sync"):
        self.summary, self.can_write, self.title = summary, can_write, title
        self.get_error, self.put_error = get_error, put_error
        self.calls = []

    def require_write(self):
        self.calls.append("require_write")
        if not self.can_write:
            raise MeetilyError(None, None, "Set MEETILY_PRO_TOKEN")

    def get_summary(self, mid):
        self.calls.append("get")
        if self.get_error:
            raise self.get_error
        return self.summary

    def get_meeting(self, mid):
        return {"id": mid, "title": self.title}

    def rename_meeting(self, mid, title):
        self.calls.append("rename")
        self.title = title

    def put_summary(self, mid, text):
        self.calls.append("put")
        if self.put_error:
            raise self.put_error
        self.summary = {"status": "completed", "result": {"markdown": text}}


OURS = "# Ours" + chr(10) + chr(10) + "body"
OLD = {"status": "completed", "result": {"markdown": "# Meetily's own"}, "updated_at": "t"}


def test_backup_happens_before_put(tmp_path):
    c = FakeClient(OLD)
    run = tmp_path / "runA"
    run.mkdir()
    rec = publish(c, "m1", OURS, run, log=lambda *_: None)
    tmp_path = run
    assert c.calls == ["require_write", "get", "put", "get"]
    backup = tmp_path / rec["backup"]
    assert json.loads(backup.read_text())["result"]["markdown"] == "# Meetily's own"
    assert rec["read_back_ok"] is True and (tmp_path / "published.json").exists()
    assert published_meetings(run.parent) == {"m1"}


def test_no_write_key_touches_nothing(tmp_path):
    c = FakeClient(OLD, can_write=False)
    with pytest.raises(WritebackError, match="MEETILY_PRO_TOKEN"):
        publish(c, "m1", OURS, tmp_path, log=lambda *_: None)
    assert c.calls == ["require_write"] and not (tmp_path / "backups").exists()


def test_backup_failure_aborts_before_put(tmp_path):
    c = FakeClient(get_error=MeetilyError(500, "boom"))
    with pytest.raises(WritebackError, match="nothing was written"):
        publish(c, "m1", OURS, tmp_path, log=lambda *_: None)
    assert "put" not in c.calls


def test_in_progress_summary_refused(tmp_path):
    c = FakeClient({"status": "processing", "result": None})
    with pytest.raises(WritebackError, match="still generating"):
        publish(c, "m1", OURS, tmp_path, log=lambda *_: None)
    assert "put" not in c.calls


def test_no_existing_summary_is_ok(tmp_path):
    c = FakeClient(get_error=MeetilyError(404, {"error": "not found"}))
    c.get_error = MeetilyError(404, {})
    # the post-PUT read-back also 404s in this fake; that must not fail the write
    rec = publish(c, "m1", OURS, tmp_path, log=lambda *_: None)
    assert rec["backup"] is None and "put" in c.calls and rec["read_back_ok"] is None


def test_put_failure_keeps_backup_and_says_unchanged(tmp_path):
    c = FakeClient(OLD, put_error=MeetilyError(409, "recording_in_progress"))
    with pytest.raises(WritebackError, match="unchanged"):
        publish(c, "m1", OURS, tmp_path, log=lambda *_: None)
    assert list((tmp_path / "backups").glob("*.json"))
    assert not (tmp_path / "published.json").exists()


def test_empty_summary_rejected(tmp_path):
    with pytest.raises(WritebackError):
        publish(FakeClient(OLD), "m1", "  \n", tmp_path, log=lambda *_: None)


def test_delete_screenshots_option(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.jpg").write_bytes(b"x")
    publish(FakeClient(OLD), "m1", OURS, tmp_path, delete_screenshots=True, log=lambda *_: None)
    assert not (tmp_path / "images").exists()


def test_fingerprint_ignores_meetily_reformatting():
    from core.writeback import fingerprint
    nl = chr(10)
    sent = nl.join(["**Action Items**", "", "| **Owner** | Task |", "| --- | --- |", "| **Sara** | Shortlist |",
                    "", "*   **Topic:** one", "*   **Other:** two"])
    stored = nl.join(["**Action Items**", "", "| **Owner** | Task      |", "| --------- | --------- |",
                      "| **Sara**  | Shortlist |", "", "* **Topic:** one", "", "* **Other:** two"])
    assert fingerprint(sent) == fingerprint(stored)
    assert fingerprint(sent) != fingerprint(sent.replace("Shortlist", "Budget"))


def test_summary_text_shapes():
    assert summary_text({"markdown": "a"}) == "a"
    assert summary_text("b") == "b"
    assert '"x"' in summary_text({"x": 1})
    assert summary_text(None) == ""


def test_split_title_matches_meetily_storage():
    nl = chr(10)
    doc = "# Atlas Kickoff" + nl + nl + "**Summary**" + nl + nl + "x"
    body = "**Summary**" + nl + nl + "x"
    assert split_title(doc) == ("Atlas Kickoff", body)
    assert split_title(body) == (None, body)
    assert split_title("# Only a title") == ("Only a title", "")


def test_publish_sends_body_without_title(tmp_path):
    nl = chr(10)
    c = FakeClient(OLD)
    rec = publish(c, "m1", "# Atlas Kickoff" + nl + nl + "**Summary**" + nl + nl + "x", tmp_path,
                  log=lambda *_: None)
    assert c.summary["result"]["markdown"] == "**Summary**" + nl + nl + "x"
    assert rec["title"] == "Atlas Kickoff"
    with pytest.raises(WritebackError):
        publish(FakeClient(OLD), "m1", "# Only a title", tmp_path, log=lambda *_: None)


def test_default_meeting_name_is_replaced_by_summary_title(tmp_path):
    c = FakeClient(OLD, title="New Meeting – 4:36 PM")
    rec = publish(c, "m1", OURS, tmp_path, log=lambda *_: None)
    assert c.title == "Ours" and rec["renamed_to"] == "Ours" and c.calls[-1] == "rename"


def test_custom_meeting_name_is_kept(tmp_path):
    c = FakeClient(OLD, title="Design review")
    rec = publish(c, "m1", OURS, tmp_path, log=lambda *_: None)
    assert c.title == "Design review" and rec["renamed_to"] is None and "rename" not in c.calls
