"""Full live run of the workflow against the real Meetily (not run by pytest):

  python cli.py serve                    # webhooks active (Overview > Automation is green)
  python -m tests.live_meeting_check

1. opens the slide demo window and makes it the watched window,
2. starts a Meetily recording via the API (needs a key with `record` scope in MEETILY_PRO_TOKEN;
   the workflow itself only needs read + write) and narrates the slides with Windows
   text-to-speech, so Meetily has real speech to transcribe,
3. stops the recording when the slides end,
4. follows our webhook events: capture start -> stop + extract -> summary + write-back,
5. waits a little longer to see whether our own write triggers another summary event.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import cli  # noqa: F401  (loads .env)
from tests.slide_window import TITLE
from vcs.meetily_client import MeetilyClient, find_token

BASE = "http://127.0.0.1:8765/api/"
FIXTURE = Path(__file__).parent / "fixtures" / "transcript.json"


def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers={"X-VCS": "1"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def narration_script(path: Path) -> Path:
    """PowerShell script that speaks the fixture lines at their timestamps (from its start)."""
    segs = json.loads(FIXTURE.read_text("utf-8"))["segments"]
    items = ",\n".join(f"  @({s['audio_start_time']}, '{s['text'].replace(chr(39), chr(39) * 2)}')"
                       for s in segs)
    path.write_text(f"""Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$start = Get-Date
$lines = @(
{items}
)
foreach ($l in $lines) {{
  $wait = [double]$l[0] - ((Get-Date) - $start).TotalSeconds
  if ($wait -gt 0) {{ Start-Sleep -Milliseconds ([int]($wait * 1000)) }}
  $s.Speak($l[1])
}}
""", encoding="utf-8")
    return path


def main() -> int:
    auto = api("GET", "automation")
    if auto["state"] != "active":
        print("automation not active:", auto["message"])
        return 1
    before = {e["event_id"] for e in auto["events"]}
    rec_token = find_token(need_write=True)
    meetily = MeetilyClient()

    clock = Path("data/slide_clock.json")
    clock.unlink(missing_ok=True)
    demo = subprocess.Popen([sys.executable, "-m", "tests.slide_window", "--lead", "12",
                             "--clock", str(clock)])
    try:
        hwnd = None
        for _ in range(60):
            time.sleep(0.25)
            hits = [w for w in api("GET", "windows")["windows"] if w["title"] == TITLE]
            if hits:
                hwnd = hits[0]
                break
        if not hwnd:
            print("slide window didn't appear")
            return 1
        api("PUT", "settings", {"watch": {"hwnd": hwnd["hwnd"], "title": hwnd["title"],
                                          "process": hwnd["process"]}})
        print("watching:", hwnd["title"])

        r = meetily._request("POST", "/v1/recording/start",
                             {"meeting_name": "VCS live test", "consent_attested": True}, token=rec_token)
        print("recording started:", r)
        while not clock.exists() and demo.poll() is None:
            time.sleep(0.1)
        narr = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                 str(narration_script(Path("data/_narration.ps1")))])
        demo.wait(timeout=150)
        narr.wait(timeout=30)
        time.sleep(3)
        r = meetily._request("POST", "/v1/recording/stop", {}, token=rec_token)
        print("recording stopped:", r)
    finally:
        if demo.poll() is None:
            demo.kill()

    seen: dict[str, str] = {}
    t0 = time.time()
    written_at = None
    while time.time() - t0 < 600:
        time.sleep(3)
        for e in api("GET", "automation")["events"]:
            if e["event_id"] in before:
                continue
            line = f"{e['event']:<20} {e['status']:<8} {(e['log'] or [''])[-1]}"
            if seen.get(e["event_id"]) != line:
                seen[e["event_id"]] = line
                print(f"[{time.time() - t0:5.0f}s] {line}", flush=True)
            if e["event"] == "recording.stopped" and e["status"] in ("done", "failed") and not written_at:
                written_at = time.time()
        if written_at and time.time() - written_at > 60:
            break                       # watched 60 s more for Meetily summary events / loops
    stopped = [l for l in seen.values() if l.startswith("recording.stopped")]
    ok = bool(stopped) and "written to Meetily" in stopped[0]
    print("\nRESULT:", "summary written on stop" if ok else "NOT written", "|",
          f"{sum(l.startswith('summary.') for l in seen.values())} summary event(s) afterwards")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
