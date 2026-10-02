"""Step (b) checks that need no Gemini key and no running Meetily."""
import json
import re
from pathlib import Path

import pytest

from vcs.prompts import build_system_prompt
from vcs.summarizer import SummaryError, build_contents, clean_output
from vcs.transcript import build_input, mmss

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "transcript.json").read_text("utf-8"))


def test_prompt_matches_meetily_doc_verbatim():
    doc = (ROOT / "docs/tech/meetily-summary-prompts.md").read_text("utf-8")
    block = re.search(r"````text\n(.*?)\n````", doc, re.S).group(1)
    assert build_system_prompt(extra=False).strip() == block.strip()


def test_extra_rules_only_add():
    base, extra = build_system_prompt(extra=False), build_system_prompt(extra=True)
    assert len(extra) > len(base) and "SCREEN CONTEXT RULES" in extra
    assert "SCREEN CONTEXT RULES" not in base


def test_interleave_order_and_format():
    shots = [
        {"start": 12, "end": 24, "type": "slide", "text": "Quarterly Results\n- Revenue up", "image": "x"},
        {"start": 26, "end": 40, "type": "diagram", "text": "", "image": "images/d.jpg"},
    ]
    text, diagrams = build_input(FIXTURE["segments"], shots)
    lines = text.splitlines()
    assert lines[0].startswith("[00:01] Okay everyone")
    i_screen = next(i for i, l in enumerate(lines) if "Slide." in l)
    assert lines[i_screen].startswith("[00:12] [SCREEN] (on screen 00:12-00:24)")
    assert 'OCR: "Quarterly Results / - Revenue up"' in lines[i_screen]
    assert lines[i_screen - 1].startswith("[00:07]") and lines[i_screen + 1].startswith("[00:13]")
    assert len(diagrams) == 1 and "see image 1" in text


def test_offset_shifts_screens_only():
    shots = [{"start": 0, "end": 5, "type": "slide", "text": "Hello world slide", "image": "x"}]
    text, _ = build_input(FIXTURE["segments"], shots, offset=7.5)
    assert "[00:07] [SCREEN] (on screen 00:07-00:12)" in text
    assert text.count("[SCREEN]") == 1


def test_mmss():
    assert mmss(75.9) == "01:15" and mmss(-3) == "00:00" and mmss(3725) == "62:05"


def test_clean_output():
    assert clean_output("<think>hmm</think>\n# T\nbody") == "# T\n\nbody"
    assert clean_output("```markdown\n# T\nbody\n```") == "# T\n\nbody"
    with pytest.raises(SummaryError):
        clean_output("<think>only</think>  ")
    with pytest.raises(SummaryError):
        clean_output(None)


def test_diagram_images_attached(tmp_path):
    from vcs.transcript import DiagramRef
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "d.jpg").write_bytes(b"\xff\xd8fake")
    d = DiagramRef(1, {"image": "images/d.jpg"}, 26.0)
    parts = build_contents("[00:01] hi", [d], tmp_path)
    assert len(parts) == 3
    assert parts[2].inline_data.mime_type == "image/jpeg"
    assert "transcript_chunks" in parts[0].text and "Image 1" in parts[1].text


def test_zero_quota_detected_without_retry():
    from vcs.summarizer import _no_quota

    class E(Exception):
        code = 429
        details = {"error": {"details": [{"metadata": {"quota_limit_value": "0"}}]}}

    class Busy(E):
        details = {"error": {"details": [{"metadata": {"quota_limit_value": "15"}}]}}

    assert _no_quota(E()) and not _no_quota(Busy())


def test_spacing_matches_meetily():
    from vcs.summarizer import normalize_spacing
    nl = chr(10)
    raw = nl.join(["# T", "**Summary**", "Para.", "**Key Decisions**", "- a", "- b",
                   "**Action Items**", "| A | B |", "| --- | --- |", "| 1 | 2 |"])
    want = nl.join(["# T", "", "**Summary**", "", "Para.", "", "**Key Decisions**", "", "- a", "- b",
                    "", "**Action Items**", "", "| A | B |", "| --- | --- |", "| 1 | 2 |"])
    assert normalize_spacing(raw) == want
    assert normalize_spacing(want) == want            # idempotent
    # bold text inside a line is not a header
    assert normalize_spacing("- **Topic:** text") == "- **Topic:** text"


class _Resp:
    def __init__(self, text):
        self.text = text


def _api_error(code):
    from google.genai import errors
    return errors.APIError(code, {"error": {"code": code, "message": "x", "status": "X"}})


class _FakeModels:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return _Resp(step)


class _FakeClient:
    def __init__(self, script):
        self.models = _FakeModels(script)


SEGS = [{"text": "hello", "audio_start_time": 1.0}]


def test_retry_then_success():
    from vcs.summarizer import summarize
    c = _FakeClient([_api_error(503), "# T" + chr(10) + "**Summary**" + chr(10) + "ok"])
    info = {}
    out = summarize(SEGS, {"screenshots": []}, Path("."), client=c, model="main",
                    info=info, sleep=lambda s: None)
    assert out.endswith("ok") and info["model"] == "main" and c.models.calls == ["main", "main"]


def test_overloaded_falls_back(monkeypatch):
    import vcs.summarizer as S
    monkeypatch.setattr(S, "FALLBACK_MODEL", "backup")
    c = _FakeClient([_api_error(503)] * 2 + ["# T" + chr(10) + "x"])
    info = {}
    S.summarize(SEGS, {"screenshots": []}, Path("."), client=c, model="main",
                info=info, sleep=lambda s: None)
    assert c.models.calls == ["main"] * 2 + ["backup"] and info["model"] == "backup"


def test_bad_request_not_retried():
    from vcs.summarizer import summarize
    c = _FakeClient([_api_error(400)])
    with pytest.raises(SummaryError):
        summarize(SEGS, {"screenshots": []}, Path("."), client=c, model="main", sleep=lambda s: None)
    assert c.models.calls == ["main"]
