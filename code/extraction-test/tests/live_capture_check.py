"""Manual end-to-end check of live capture through the running UI server (not run by pytest):

  python cli.py serve            # in another terminal
  python -m tests.live_capture_check

Opens the slide window, starts a capture of it via the API, stops after the timeline, waits for
extraction and compares the screenshots (aligned by wall clock) with tests/synthetic.EXPECTED.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from tests import synthetic
from tests.slide_window import TITLE

BASE = "http://127.0.0.1:8765/api/"
TOL = 2.0


def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers={"X-VCS": "1"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    clock = Path("data/slide_clock.json")
    clock.unlink(missing_ok=True)
    demo = subprocess.Popen([sys.executable, "-m", "tests.slide_window", "--lead", "6",
                             "--clock", str(clock)])
    try:
        hwnd = None
        for _ in range(40):
            time.sleep(0.25)
            hits = [w for w in api("GET", "windows")["windows"] if w["title"] == TITLE]
            if hits:
                hwnd = hits[0]["hwnd"]
                break
        if not hwnd:
            print("slide window not found")
            return 1
        st = api("POST", "capture/start", {"hwnd": hwnd})
        print("capturing", st["title"], "->", st["run"])
        demo.wait(timeout=120)                      # window closes itself after the timeline
        time.sleep(1.5)
        stop = api("POST", "capture/stop")
        print(f"stopped: {stop['seconds']}s, window_closed={stop['window_closed']}")
        for _ in range(120):
            time.sleep(1)
            job = api("GET", "jobs/" + stop["job"])
            if job["status"] != "running":
                break
        print("extract:", job["status"], job["log"][-2:], job["error"] or "")
        if job["status"] != "done":
            return 1
        run = api("GET", "runs/" + stop["run"])
    finally:
        if demo.poll() is None:
            demo.kill()

    t0 = datetime.fromisoformat(json.loads(clock.read_text())["slide_t0"])
    cap = datetime.fromisoformat(run["meta"]["capture_started_at"])
    shift = (t0 - cap).total_seconds()           # slide time = video time - shift
    print(f"slide t=0 was {shift:.1f}s into the capture")
    shots = [dict(s, start=s["start"] - shift, end=s["end"] - shift) for s in run["screenshots"]
             if s["end"] - shift > 0.5]          # ignore the "get ready" lead-in
    for s in shots:
        print(f"  {s['start']:6.1f}-{s['end']:6.1f}  {s['type']:<8} {s['text'][:50]!r}")
    ok = True
    for e in synthetic.EXPECTED:
        hit = [s for s in shots if abs(s["start"] - e["start"]) <= TOL and s["type"] == e["type"]]
        good = bool(hit) and all(p.lower().replace(" ", "") in hit[0]["text"].lower().replace(" ", "")
                                 for p in e["contains"])
        ok &= good
        print(("OK  " if good else "MISS"), e["name"], e["start"], "-", e["end"])
    dropped = not any(23 < s["start"] < 26 and s["end"] - s["start"] < 3 for s in shots)
    print("OK   2 s slide dropped" if dropped else "MISS 2 s slide kept")

    # Capture -> OCR -> capture used to crash the process natively; do a second capture now.
    second = second_capture()
    print("OK   second capture after extraction" if second else "MISS second capture")
    return 0 if ok and dropped and second else 1


def second_capture() -> bool:
    target = next((w for w in api("GET", "windows")["windows"] if not w["minimized"]), None)
    if target is None:
        return False
    api("POST", "capture/start", {"hwnd": target["hwnd"]})
    time.sleep(4)
    stop = api("POST", "capture/stop")
    for _ in range(60):
        time.sleep(1)
        job = api("GET", "jobs/" + stop["job"])
        if job["status"] != "running":
            break
    try:
        alive = api("GET", "status") is not None
    except OSError:
        alive = False
    return alive and job["status"] == "done" and stop["frames"] > 0


if __name__ == "__main__":
    sys.exit(main())
