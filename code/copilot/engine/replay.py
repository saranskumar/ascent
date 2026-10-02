"""Run the engine on a transcript file without any servers, then write a contact sheet.

    python -m engine.replay                      # contracts/samples/transcript.jsonl, real time
    python -m engine.replay --speed 2
    python -m engine.replay --file some_recording.jsonl

Writes .cache/replay.html (open it in a browser) showing each suggestion with its images, timing,
query and path, next to the target suggestions in contracts/samples/suggestions.jsonl.
LLM and search calls are real, so timings are real (only the speech is sped up).
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import time
from pathlib import Path

from contracts.messages import Segment, to_dict

from . import main as em

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "contracts" / "samples"


async def run(path: Path, speed: float) -> list[dict]:
    out: list[dict] = []
    t0 = time.monotonic()

    async def publish(sug):
        d = to_dict(sug)
        d["seq"] = len(out) + 1
        d["at"] = round((time.monotonic() - t0) * speed, 1)   # in transcript seconds
        out.append(d)
        return d

    em.IMAGES.mkdir(parents=True, exist_ok=True)
    eng = em.Engine(publish)
    segs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    async def ticker():
        while True:
            await asyncio.sleep(em.CHECK_EVERY / speed)
            await eng.tick()

    tick = asyncio.create_task(ticker())
    for s in segs:
        await asyncio.sleep(max(0, s["end"] / speed - (time.monotonic() - t0)))
        print(f"   [{s['end']:6.1f}] {s['text']}")
        await eng.on_segment(Segment(id=s["id"], text=s["text"], start=s["start"], end=s["end"],
                                     speaker=s.get("speaker")))
    await asyncio.sleep(em.CHECK_EVERY / speed + 1)
    await eng.tick()
    while eng.tasks or eng.slow_busy:
        await asyncio.sleep(0.5)
    tick.cancel()
    return out


def contact_sheet(sugs: list[dict], target: list[dict], path: Path) -> None:
    def card(s, real=True):
        imgs = "".join(
            f'<a href="images/{html.escape(i["url"].split("/images/")[-1])}">'
            f'<img src="images/{html.escape(i["thumb_url"].split("/images/")[-1])}" '
            f'title="{html.escape(i.get("caption", ""))}"></a>'
            for i in s.get("images", [])) if real else ""
        refs = "".join(f'<div class="ref">{html.escape(i.get("source_ref", "")[:90])}</div>'
                       for i in s.get("images", [])) if real else ""
        return (f'<div class="card {s.get("priority")}"><div class="at">{s.get("at")}s</div>'
                f'<b>{html.escape(s["topic"])}</b> <span class="pri">{s.get("priority")}</span>'
                f'<div class="reason">{html.escape(s.get("reason", ""))}</div>{imgs}{refs}</div>')

    body = (f'<div class="col"><h2>Engine ({len(sugs)})</h2>{"".join(card(s) for s in sugs)}</div>'
            f'<div class="col"><h2>Target ({len(target)})</h2>'
            f'{"".join(card(t, real=False) for t in target)}</div>')
    path.write_text(f"""<!doctype html><meta charset="utf-8"><title>Engine replay</title>
<style>body{{font:14px system-ui;background:#111;color:#eee;display:flex;gap:24px;padding:16px}}
.col{{flex:1}} .card{{background:#1d1d1d;border-radius:8px;padding:10px;margin-bottom:10px}}
.card.elevated{{outline:2px solid #e0a030}} .at{{float:right;color:#888}} .pri{{color:#888;font-size:12px}}
.reason{{color:#aaa;font-size:12px;margin:4px 0}} img{{height:120px;margin:4px 4px 0 0;border-radius:4px}}
.ref{{color:#666;font-size:11px}}</style>{body}""", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=SAMPLES / "transcript.jsonl")
    ap.add_argument("--target", type=Path, default=SAMPLES / "suggestions.jsonl")
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()
    sugs = asyncio.run(run(a.file, a.speed))
    target = ([json.loads(l) for l in a.target.read_text(encoding="utf-8").splitlines() if l.strip()]
              if a.target.exists() else [])
    out = em.CACHE / "replay.html"
    contact_sheet(sugs, target, out)
    (em.CACHE / "replay.json").write_text(json.dumps(sugs, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{len(sugs)} suggestions -> {out}")


if __name__ == "__main__":
    main()
