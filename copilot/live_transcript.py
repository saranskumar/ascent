"""
Live transcript viewer for Meetily Pro (Agent API).

Polls the local API while a recording is running and prints new transcript
segments as they appear, plus an approximate lag so we can judge whether
Meetily's live transcript is fast enough for the co-pilot.

Setup (one time): Meetily Pro > Settings > Integrations > turn on the
Automation API and "Allow the CLI on this computer". That writes the
read-only loopback token this script uses. No pip installs needed.

Run:   python live_transcript.py            (waits for a recording)
       python live_transcript.py --raw      (also dumps the first raw JSON)
       python live_transcript.py --meeting <id>   (watch a specific meeting)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("MEETILY_API", "http://127.0.0.1:8420")

# Enable ANSI colours on Windows terminals
if os.name == "nt":
    os.system("")
DIM, BOLD, CYAN, YEL, GRN, RED, RST = (
    "\033[2m", "\033[1m", "\033[36m", "\033[33m", "\033[32m", "\033[31m", "\033[0m")


# ---------------------------------------------------------------- auth / http
def find_token() -> str:
    tok = os.environ.get("MEETILY_PRO_TOKEN", "").strip()
    if tok:
        return tok
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    f = base / "pro.meetily.ai" / "gateway-token"
    if f.exists() and f.read_text(encoding="utf-8").strip():
        return f.read_text(encoding="utf-8").strip()
    sys.exit(f"{RED}No token. Turn on Settings > Integrations > Automation API and "
             f"'Allow the CLI on this computer' (expected {f}), "
             f"or set MEETILY_PRO_TOKEN.{RST}")


TOKEN = find_token()


def api(path: str):
    req = urllib.request.Request(BASE + path,
                                 headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body
    except urllib.error.URLError as e:
        return None, str(e)


# ------------------------------------------------------- segment field helpers
# TranscriptSegmentDto fields aren't documented yet, so read them defensively.
def seg_text(s: dict) -> str:
    return (s.get("text") or s.get("transcript") or "").strip()


def seg_time(s: dict, which: str = "start") -> float | None:
    keys = {
        "start": ["start", "start_time", "audio_start_time", "start_sec", "offset"],
        "end": ["end", "end_time", "audio_end_time", "end_sec"],
    }[which]
    for k in keys:
        v = s.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    for k in (f"{which}_ms", f"{which}Ms"):
        v = s.get(k)
        if isinstance(v, (int, float)):
            return v / 1000.0
    ts = s.get("timestamps") or s.get("timestamp")
    if isinstance(ts, dict):
        return seg_time(ts, which)
    return None


def seg_key(s: dict, idx: int):
    return s.get("id") or s.get("segment_id") or (seg_time(s) if seg_time(s) is not None else idx)


def mmss(t: float | None) -> str:
    if t is None:
        return "--:--"
    t = int(t)
    return f"{t // 60:02d}:{t % 60:02d}"


# ------------------------------------------------------------------- main loop
def wait_for_meeting() -> str:
    print(f"{DIM}Waiting for a recording to start in Meetily...{RST}")
    last_err = None
    while True:
        code, body = api("/v1/recording")
        if code == 200 and isinstance(body, dict):
            mid = body.get("active_meeting_id")
            if mid:
                print(f"{GRN}Recording detected (state={body.get('state')}), "
                      f"meeting {mid}{RST}\n")
                return mid
        elif str(body) != last_err:
            last_err = str(body)
            print(f"{YEL}GET /v1/recording -> {code}: {body}{RST}")
        time.sleep(1)


def watch(meeting_id: str, interval: float, raw: bool):
    seen: dict = {}          # key -> text already printed
    t0 = time.monotonic()    # ~recording start, for lag estimate
    dumped = False
    polls = 0
    while True:
        code, body = api(f"/v1/meetings/{meeting_id}/transcript")
        polls += 1
        now = time.monotonic() - t0

        if code != 200 or not isinstance(body, dict):
            print(f"{YEL}[{mmss(now)}] transcript -> {code}: {body}{RST}")
        else:
            segs = body.get("segments") or []
            if raw and not dumped and segs:
                print(f"{DIM}--- raw first segment ---\n"
                      f"{json.dumps(segs[0], indent=2)}\n-------------------------{RST}")
                dumped = True
            for i, s in enumerate(segs):
                text = seg_text(s)
                if not text:
                    continue
                k = seg_key(s, i)
                if seen.get(k) == text:
                    continue
                revised = k in seen
                seen[k] = text
                start, end = seg_time(s, "start"), seg_time(s, "end")
                lag = f"lag≈{now - end:4.1f}s" if end is not None else "lag n/a"
                spk = s.get("speaker") or s.get("speaker_label") or ""
                tag = f"{YEL}(revised){RST} " if revised else ""
                print(f"{CYAN}[{mmss(start)}]{RST} {BOLD}{spk + ': ' if spk else ''}{RST}"
                      f"{tag}{text}  {DIM}{lag}{RST}")

        # stop when recording ends
        if polls % max(1, int(2 / interval)) == 0:
            c, st = api("/v1/recording")
            if c == 200 and isinstance(st, dict) and st.get("active_meeting_id") != meeting_id:
                print(f"\n{GRN}Recording stopped. {len(seen)} segments seen.{RST}")
                return
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description="Live Meetily Pro transcript viewer")
    ap.add_argument("--meeting", help="meeting id to watch (default: active recording)")
    ap.add_argument("--interval", type=float, default=1.0, help="poll seconds (default 1)")
    ap.add_argument("--raw", action="store_true", help="print the first raw segment JSON")
    a = ap.parse_args()

    code, who = api("/v1/recording")
    if code is None:
        sys.exit(f"{RED}Can't reach {BASE}: {who}\nIs Meetily Pro running with the "
                 f"Automation API on?{RST}")
    try:
        while True:
            mid = a.meeting or wait_for_meeting()
            watch(mid, a.interval, a.raw)
            if a.meeting:
                break
    except KeyboardInterrupt:
        print(f"\n{DIM}bye{RST}")


if __name__ == "__main__":
    main()
