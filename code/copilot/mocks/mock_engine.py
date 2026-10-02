"""Stand-in for part 2: sends scripted suggestions on ws /suggestions and serves placeholder
images on /images, timed to match contracts/samples/transcript.jsonl.

    python -m mocks.mock_engine
    python -m mocks.mock_engine --speed 4 --loop

Select / dismiss messages from the GUI are printed, so the GUI can check it sends them right.
Placeholder images are drawn with Pillow into .cache/mock_images on first run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from aiohttp import web

from contracts.config import ENGINE_PORT, HOST
from contracts.messages import Image, Suggestion, to_dict
from contracts.stream import StreamServer, run_app

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "contracts" / "samples" / "suggestions.jsonl"
IMAGES = ROOT / ".cache" / "mock_images"
COLORS = ["#3b6ea5", "#a5503b", "#3ba56e", "#7a3ba5", "#a5913b", "#3b9aa5"]


def draw_placeholder(img_id: str, caption: str, i: int) -> None:
    from PIL import Image as PImage, ImageDraw, ImageFont

    full, thumb = IMAGES / f"{img_id}.png", IMAGES / f"{img_id}_thumb.png"
    if full.exists() and thumb.exists():
        return
    im = PImage.new("RGB", (1280, 720), COLORS[i % len(COLORS)])
    d = ImageDraw.Draw(im)
    try:
        font, small = ImageFont.truetype("arial.ttf", 64), ImageFont.truetype("arial.ttf", 32)
    except OSError:
        font = small = ImageFont.load_default()
    d.rectangle([40, 40, 1240, 680], outline="white", width=6)
    d.text((640, 330), caption, fill="white", font=font, anchor="mm")
    d.text((640, 420), f"placeholder {img_id}", fill="#ffffffaa", font=small, anchor="mm")
    im.save(full)
    im.resize((320, 180)).save(thumb)


def build(rows: list[dict], base_url: str) -> list[tuple[float, Suggestion]]:
    out, n = [], 0
    for r in rows:
        imgs = []
        for im in r["images"]:
            draw_placeholder(im["id"], im["caption"], n)
            n += 1
            imgs.append(Image(id=im["id"], url=f"{base_url}/{im['id']}.png",
                              thumb_url=f"{base_url}/{im['id']}_thumb.png", width=1280,
                              height=720, source="local", source_ref=f"mock:{im['id']}",
                              caption=im["caption"]))
        out.append((r["at"], Suggestion(id=r["id"], topic=r["topic"], images=imgs,
                                        reason=r.get("reason", ""),
                                        priority=r.get("priority", "ambient"),
                                        source_segment_ids=r.get("source_segment_ids", []))))
    return out


async def replay(stream: StreamServer, items, speed: float, loop: bool, gap: float):
    await asyncio.sleep(1)
    n = 0
    while True:
        clock = 0.0
        for at, sug in items:
            await asyncio.sleep(max(0, at - clock) / speed)
            clock = at
            msg = to_dict(sug)
            if n:
                msg["id"] = f"{sug.id}-r{n}"
            d = await stream.publish(msg)
            print(f"#{d['seq']:<4} [{at:6.1f}] {sug.priority:8} {sug.topic} ({len(sug.images)} images)")
        if not loop:
            print("suggestions finished (server stays up; use --loop to repeat)")
            return
        n += 1
        await asyncio.sleep(gap / speed)


async def on_feedback(msg: dict):
    if msg.get("type") == "select":
        print(f"  <- SELECT  {msg.get('suggestion_id')} / {msg.get('image_id')}")
    elif msg.get("type") == "dismiss":
        print(f"  <- DISMISS {msg.get('suggestion_id')}")
    else:
        print(f"  <- unknown {msg}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=SAMPLE)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--port", type=int, default=ENGINE_PORT)
    a = ap.parse_args()

    IMAGES.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in a.file.read_text(encoding="utf-8").splitlines() if l.strip()]
    items = build(rows, f"http://{HOST}:{a.port}/images")

    stream = StreamServer("suggestions", replay_seconds=120, on_message=on_feedback)
    app = web.Application()
    stream.attach(app)
    app.router.add_static("/images", IMAGES)

    async def start(_):
        app["replay"] = asyncio.create_task(replay(stream, items, a.speed, a.loop, gap=101))

    app.on_startup.append(start)
    run_app(app, a.port)


if __name__ == "__main__":
    main()
