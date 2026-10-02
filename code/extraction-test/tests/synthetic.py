"""Generate a synthetic 1 fps screen recording with known ground truth.

  python -m tests.synthetic data/synthetic.mp4     (also writes synthetic.expected.json)

Frames look like a slide inside a fake meeting window (top bar, bottom controls,
a participant tile that changes every second), with sensor-ish noise and a moving
cursor, so the pHash thresholds are exercised against something non-trivial.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
FONTS = Path("C:/Windows/Fonts")


def font(size, bold=False):
    for name in (("arialbd.ttf" if bold else "arial.ttf"), "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(str(FONTS / name) if (FONTS / name).exists() else name, size)
        except OSError:
            continue
    return ImageFont.load_default()


# t ranges are [start, end) in seconds
TITLE_A = "Project Atlas Kickoff"
TITLE_B = "Quarterly Results"
BULLETS_B = ["Revenue grew twelve percent", "Churn dropped to three percent",
             "Hiring four new engineers"]
TITLE_C = "Quick Reminder"
TITLE_D = "Operations Handbook"
LINES_D = [f"Policy {i}: {w}" for i, w in enumerate(
    "backups run nightly|passwords rotate quarterly|laptops are encrypted|"
    "visitors sign the log|incidents get a postmortem|vendors need approval|"
    "releases happen on Tuesdays|oncall rotates weekly|budgets close monthly|"
    "travel needs a manager|badges expire yearly|logs are kept ninety days|"
    "interns get a mentor|docs live in the wiki|tickets need an owner|"
    "standups stay short|retros happen biweekly|demos are on Fridays|"
    "invoices go to finance|spares are in room four|keys are signed out|"
    "alerts page the owner|access is least privilege|data stays on shore|"
    "contracts need legal review|hardware is tagged|secrets live in the vault|"
    "tests gate every merge|rollbacks are rehearsed|status pages are public".split("|"), 1)]

EXPECTED = [
    {"name": "A", "start": 0, "end": 12, "type": "slide", "contains": [TITLE_A]},
    {"name": "B", "start": 12, "end": 24, "type": "slide", "contains": BULLETS_B},
    # C: 24-26 s (2 s) -> must be dropped
    {"name": "diagram", "start": 26, "end": 40, "type": "diagram", "contains": []},
    {"name": "D", "start": 40, "end": 60, "type": "slide", "contains": [], "min_parts": 2},
    {"name": "A again", "start": 60, "end": 65, "type": "slide", "contains": [TITLE_A]},
]
DURATION = 65


def chrome(img: Image.Image, t: int):
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 40], fill=(40, 40, 46))
    d.text((16, 9), "Weekly Sync - Meeting", font=font(20), fill=(230, 230, 230))
    d.ellipse([W - 40, 12, W - 24, 28], fill=(220, 40, 40) if t % 2 else (90, 20, 20))
    d.rectangle([0, H - 60, W, H], fill=(32, 33, 36))
    d.text((420, H - 42), "Mute      Camera      Share      Leave", font=font(22),
           fill=(235, 235, 235))
    # participant tile: bottom-right, outside the centre crop, changes every second
    rng = np.random.default_rng(t)
    c = tuple(int(x) for x in rng.integers(40, 200, 3))
    d.rectangle([W - 220, H - 200, W - 10, H - 70], fill=c)
    d.text((W - 200, H - 190), "Participant", font=font(18), fill=(255, 255, 255))


def slide(title, bullets=(), lines=None, scroll=0):
    img = Image.new("RGB", (W, H - 100), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((260, 60), title, font=font(54, True), fill=(20, 30, 90))
    y = 190
    for b in bullets:
        d.text((260, y), "-  " + b, font=font(36), fill=(30, 30, 30))
        y += 70
    if lines:
        for i, ln in enumerate(lines):
            d.text((260, 170 + i * 40 - scroll), ln, font=font(30), fill=(30, 30, 30))
        d.rectangle([0, 0, W, 150], fill=(255, 255, 255))  # header stays put
        d.text((260, 60), title, font=font(54, True), fill=(20, 30, 90))
    return img


def diagram():
    img = Image.new("RGB", (W, H - 100), (250, 250, 250))
    d = ImageDraw.Draw(img)
    boxes = [(300, 120, 480, 200), (560, 120, 740, 200), (820, 120, 1000, 200),
             (430, 330, 610, 410), (700, 330, 880, 410), (560, 470, 740, 540)]
    cols = [(120, 170, 230), (230, 160, 90), (120, 200, 140), (200, 120, 190),
            (240, 210, 90), (150, 150, 150)]
    for b, c in zip(boxes, cols):
        d.rectangle(b, fill=c, outline=(20, 20, 20), width=3)
    for (a, b) in [(0, 1), (1, 2), (0, 3), (1, 4), (2, 4), (3, 5), (4, 5)]:
        ax, ay = (boxes[a][0] + boxes[a][2]) // 2, (boxes[a][1] + boxes[a][3]) // 2
        bx, by = (boxes[b][0] + boxes[b][2]) // 2, (boxes[b][1] + boxes[b][3]) // 2
        d.line([ax, ay, bx, by], fill=(20, 20, 20), width=4)
    return img


def frame_at(t: int) -> np.ndarray:
    if t < 12:
        s = slide(TITLE_A, ["Welcome and agenda"] if False else [])
    elif t < 24:
        n = 1 if t < 16 else 2 if t < 20 else 3
        s = slide(TITLE_B, BULLETS_B[:n])
    elif t < 26:
        s = slide(TITLE_C, ["Please submit timesheets"])
    elif t < 40:
        s = diagram()
    elif t < 60:
        s = slide(TITLE_D, lines=LINES_D, scroll=int((t - 40) * 8))
    else:
        s = slide(TITLE_A)
    full = Image.new("RGB", (W, H), (255, 255, 255))
    full.paste(s, (0, 40))
    chrome(full, t)
    arr = np.array(full)
    # cursor wandering around the content area
    cx, cy = 300 + (t * 37) % 600, 250 + (t * 53) % 300
    d = ImageDraw.Draw(full)
    d.polygon([(cx, cy), (cx + 14, cy + 22), (cx + 5, cy + 20)], fill=(0, 0, 0))
    arr = np.array(full).astype(np.int16)
    arr += np.random.default_rng(1000 + t).normal(0, 2.5, arr.shape).astype(np.int16)
    return cv2.cvtColor(np.clip(arr, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)


def build(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 1.0, (W, H))
    for t in range(DURATION):
        vw.write(frame_at(t))
    vw.release()
    path.with_suffix(".expected.json").write_text(
        json.dumps({"duration": DURATION, "expected": EXPECTED}, indent=2))
    return path


if __name__ == "__main__":
    out = build(Path(sys.argv[1] if len(sys.argv) > 1 else "data/synthetic.mp4"))
    print("wrote", out)
