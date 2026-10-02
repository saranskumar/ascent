"""Visual Context Summary workflow: command line entry point.

  python cli.py extract <video.mp4> --out data/run1                       (step a)
  python cli.py summarize --meeting <id> --screenshots data/run1/screenshots.json
  python cli.py publish --run data/run1 --meeting <id>                     (step c, needs MEETILY_PRO_TOKEN)
  python cli.py summarize --transcript-file tests/fixtures/transcript.json \\
                          --screenshots data/run1/screenshots.json         (step b, no Meetily needed)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vcs.env import load_env
from vcs.extractor import Params, extract

load_env()  # gitignored .env next to this file; real env vars take precedence
for _stream in (sys.stdout, sys.stderr):   # Windows consoles default to cp1252
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def cmd_summarize(a) -> int:
    from vcs.meetily_client import MeetilyClient, MeetilyError
    from vcs.prompts import build_system_prompt
    from vcs.summarizer import DEFAULT_MODEL, SummaryError, build_input, summarize

    if a.transcript_file:
        transcript = json.loads(a.transcript_file.read_text(encoding="utf-8"))
    elif a.meeting:
        try:
            transcript = MeetilyClient().get_transcript(a.meeting)
        except MeetilyError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    else:
        print("error: give --meeting or --transcript-file", file=sys.stderr)
        return 1
    segments = transcript.get("segments") or []
    doc = json.loads(a.screenshots.read_text(encoding="utf-8"))
    shots_dir = a.screenshots.parent

    if a.dry_run:
        from vcs import speaker_names
        text, diagrams = build_input(segments, doc["screenshots"], a.screen_offset,
                                     speaker_names.load(Path("data"), a.meeting))
        print("===== SYSTEM PROMPT =====\n" + build_system_prompt())
        print("===== USER INPUT =====\n" + text)
        print(f"\n===== {len(diagrams)} diagram image(s) would be attached =====")
        return 0
    try:
        from vcs import speaker_names
        print(summarize(segments, doc, shots_dir, offset=a.screen_offset,
                        model=a.model or DEFAULT_MODEL,
                        log=lambda m: print(m, file=sys.stderr),
                        speaker_names=speaker_names.load(Path("data"), a.meeting)))
    except SummaryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    return 0


def cmd_publish(a) -> int:
    from vcs.meetily_client import MeetilyClient, MeetilyError
    from vcs.writeback import WritebackError, publish

    f = a.run / "summary.md"
    if not f.is_file():
        print(f"error: {f} not found; run `summarize` first", file=sys.stderr)
        return 1
    text = f.read_text(encoding="utf-8")
    print(f"About to overwrite the summary of meeting {a.meeting} with {len(text)} chars from {f}.")
    print("Meetily's current summary is backed up first; the API has no undo.")
    if a.dry_run:
        return 0
    if not a.yes and input("Type 'yes' to continue: ").strip().lower() != "yes":
        print("cancelled")
        return 1
    try:
        rec = publish(MeetilyClient(), a.meeting, text, a.run, delete_screenshots=a.delete_screenshots)
    except (WritebackError, MeetilyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    print(json.dumps(rec, indent=2))
    return 0


def cmd_windows(a) -> int:
    from vcs.windows import list_windows, set_dpi_aware
    set_dpi_aware()
    for w in list_windows():
        flag = " (minimized)" if w["minimized"] else ""
        print(f"{w['hwnd']:>10}  {w['process']:<22} {w['title'][:70]}{flag}")
    return 0


def cmd_capture(a) -> int:
    import time
    from vcs.capture import CaptureError, WindowRecorder
    from vcs.windows import list_windows, set_dpi_aware, window_info
    set_dpi_aware()
    hwnd = a.hwnd
    if hwnd is None:
        hits = [w for w in list_windows() if a.title.lower() in w["title"].lower()]
        if not hits:
            print(f"no window title contains {a.title!r}; see `python cli.py windows`", file=sys.stderr)
            return 1
        hwnd = hits[0]["hwnd"]
    w = window_info(hwnd)
    if not a.no_extract:
        from vcs.ocr import warm_up
        warm_up()   # before capture; see vcs.ocr.warm_up
    rec = WindowRecorder(hwnd, a.out, title=w["title"], process=w["process"])
    try:
        rec.start()
    except CaptureError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"capturing '{w['title']}' at 1 fps -> {rec.video}  (Ctrl+C to stop)")
    try:
        t0 = time.monotonic()
        while rec.running and (a.seconds is None or time.monotonic() - t0 < a.seconds):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    info = rec.stop()
    print(json.dumps(info, indent=2))
    if info["frames"] and not a.no_extract:
        extract(rec.video, a.out, Params())
    return 0 if info["frames"] else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("extract", help="screen-recording video -> screenshots.json + images")
    ex.add_argument("video", type=Path)
    ex.add_argument("--out", type=Path, required=True)
    d = Params()
    ex.add_argument("--interval", type=float, default=d.interval, help="sample every N s")
    ex.add_argument("--hash-threshold", type=int, default=d.hash_threshold)
    ex.add_argument("--drift-threshold", type=int, default=d.drift_threshold)
    ex.add_argument("--min-dwell", type=float, default=d.min_dwell)

    su = sub.add_parser("summarize", help="transcript + screenshots.json -> Gemini summary (printed)")
    su.add_argument("--meeting", help="Meetily meeting id (fetches the transcript)")
    su.add_argument("--transcript-file", type=Path,
                    help="transcript JSON in the API's shape instead of a live meeting")
    su.add_argument("--screenshots", type=Path, required=True, help="screenshots.json from `extract`")
    su.add_argument("--screen-offset", type=float, default=0.0,
                    help="seconds between recording start and the screen video's start")
    su.add_argument("--model", help="Gemini model (default: GEMINI_MODEL or gemini-3.8-flash)")
    su.add_argument("--dry-run", action="store_true", help="print the prompt and input, no API call")

    pu = sub.add_parser("publish", help="write a run's summary.md into Meetily (backs up first)")
    pu.add_argument("--run", type=Path, required=True, help="run folder, e.g. data/run1")
    pu.add_argument("--meeting", required=True, help="Meetily meeting id")
    pu.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    pu.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    pu.add_argument("--delete-screenshots", action="store_true", help="remove images after a successful write")

    sub.add_parser("windows", help="list windows that can be captured")
    ca = sub.add_parser("capture", help="record one window at 1 fps, then extract screenshots")
    g = ca.add_mutually_exclusive_group(required=True)
    g.add_argument("--hwnd", type=int, help="window handle from `windows`")
    g.add_argument("--title", help="substring of the window title")
    ca.add_argument("--out", type=Path, required=True)
    ca.add_argument("--seconds", type=float, help="stop after N seconds (default: Ctrl+C)")
    ca.add_argument("--no-extract", action="store_true")

    sv = sub.add_parser("serve", help="local web UI")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--data", type=Path, default=Path("data"), help="folder holding runs")
    sv.add_argument("--no-webhooks", action="store_true",
                    help="don't subscribe to Meetily events (manual use only)")

    a = ap.parse_args(argv)
    if a.cmd == "publish":
        return cmd_publish(a)
    if a.cmd == "windows":
        return cmd_windows(a)
    if a.cmd == "capture":
        return cmd_capture(a)
    if a.cmd == "serve":
        from vcs.server import serve
        serve(a.data, a.port, webhooks=not a.no_webhooks)
        return 0
    if a.cmd == "extract":
        if not a.video.is_file():
            print(f"video not found: {a.video}", file=sys.stderr)
            return 1
        extract(a.video, a.out, Params(interval=a.interval,
                                       hash_threshold=a.hash_threshold,
                                       drift_threshold=a.drift_threshold,
                                       min_dwell=a.min_dwell))
        return 0
    return cmd_summarize(a)


if __name__ == "__main__":
    sys.exit(main())
