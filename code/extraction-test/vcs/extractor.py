"""Step (a): screen-recording video -> distinct screenshots (screenshots.json + images).

Approach adapted from lecture-to-notes (https://github.com/drpwchen/lecture-to-notes,
MIT License, (c) drpwchen): `scripts/extract_slides.py` (interval sampling, centre-crop
perceptual hash, two thresholds: step + drift) and `scripts/dedup_semantic.py`
(merge bullet-by-bullet builds by text containment, keep the longest text).

Differences from the original, for meetings:
  * sampling at 1-2 s instead of 15 s;
  * every screenshot keeps its start AND end time;
  * layout (SSIM) merging is left out, as upstream warns that a wrong merge silently
    deletes content; text containment only, and only when both texts are long enough;
  * screens shown < min_dwell seconds are dropped AFTER merging;
  * screens with very little OCR text are marked "diagram".
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .ocr import ocr_image


@dataclass
class Params:
    interval: float = 1.0          # seconds between sampled frames
    hash_threshold: int = 40       # step: hamming distance vs previous frame (of 1024 bits)
    drift_threshold: int = 80      # drift: hamming distance vs the group's first frame
    crop_ratio: float = 0.65       # centre crop used for hashing
    text_threshold: int = 88       # rapidfuzz partial_ratio for "text contained"
    min_text_chars: int = 20       # both texts need this many chars to be merged on text
    max_merge_gap: float = 60.0    # seconds
    min_dwell: float = 3.0         # drop screens shown for less than this
    diagram_chars: int = 40        # fewer alphanumeric OCR chars than this -> candidate diagram
    diagram_ink: float = 0.08      # ...and at least this fraction of "ink" (title slides have ~none)
    chrome_share: float = 0.6      # OCR lines present in this share of groups are window chrome
    chrome_min_groups: int = 4


@dataclass
class _Group:
    start: float
    first_hash: np.ndarray
    last_hash: np.ndarray
    last_t: float
    image: Path
    end: float = 0.0
    text: str = ""


# ------------------------------------------------------------------ hashing
def phash_cropped(frame_bgr, size: int = 32, crop_ratio: float = 0.65) -> np.ndarray:
    """Average-hash of the centre crop (ignores window borders / app chrome)."""
    h, w = frame_bgr.shape[:2]
    x0, x1 = int(w * (1 - crop_ratio) / 2), int(w * (1 + crop_ratio) / 2)
    y0, y1 = int(h * (1 - crop_ratio) / 2), int(h * (1 + crop_ratio) / 2)
    g = cv2.cvtColor(frame_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (size, size), interpolation=cv2.INTER_AREA)
    return (g > g.mean()).ravel()


def hamming(a: np.ndarray, b: np.ndarray) -> int:
    return int(np.count_nonzero(a != b))


# ------------------------------------------------------------------ grouping
def group_frames(video: Path, cand_dir: Path, p: Params) -> tuple[list[_Group], float]:
    """Sample the video and group consecutive similar frames (step + drift test).
    The LAST frame of each group is saved as its candidate image (a bullet-by-bullet
    slide is fully revealed by then). Returns (groups, video_duration)."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 1.0
    nframes = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = nframes / fps if nframes else 0.0

    cand_dir.mkdir(parents=True, exist_ok=True)
    groups: list[_Group] = []
    cur: _Group | None = None
    prev_frame = None
    next_t = 0.0
    idx = 0
    last_t = 0.0

    def close(g: _Group, frame):
        cv2.imwrite(str(g.image), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        groups.append(g)

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = idx / fps
        idx += 1
        if t + 1e-6 < next_t:
            continue
        next_t = t + p.interval
        last_t = t
        h = phash_cropped(frame, crop_ratio=p.crop_ratio)
        if cur is None:
            cur = _Group(t, h, h, t, cand_dir / f"cand_{len(groups):04d}.jpg")
        elif (hamming(cur.last_hash, h) < p.hash_threshold
              and hamming(cur.first_hash, h) < p.drift_threshold):
            cur.last_hash, cur.last_t = h, t
        else:
            cur.end = t
            close(cur, prev_frame)
            cur = _Group(t, h, h, t, cand_dir / f"cand_{len(groups):04d}.jpg")
        prev_frame = frame
    cap.release()

    if cur is not None:
        cur.end = max(duration, last_t + p.interval)
        close(cur, prev_frame)
    return groups, max(duration, last_t + p.interval)


# ------------------------------------------------------------------ helpers
def ink_fraction(frame_bgr, crop_ratio: float = 0.65) -> float:
    """Share of the centre crop that differs clearly from its dominant (background) grey."""
    h, w = frame_bgr.shape[:2]
    x0, x1 = int(w * (1 - crop_ratio) / 2), int(w * (1 + crop_ratio) / 2)
    y0, y1 = int(h * (1 - crop_ratio) / 2), int(h * (1 + crop_ratio) / 2)
    g = cv2.cvtColor(frame_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    bg = np.bincount(g.ravel(), minlength=256).argmax()
    return float(np.mean(np.abs(g.astype(np.int16) - int(bg)) > 30))


def _key(line: str) -> str:
    return re.sub(r"[\W_]+", "", line.lower())


def strip_static_chrome(groups: list[_Group], p: Params) -> None:
    """Remove OCR lines that recur in most groups (window title, tab bar, footer).
    They carry no per-screen information and would make every pair of screens look
    'contained' in each other. Lines are compared fuzzily because OCR wobbles."""
    from rapidfuzz import fuzz
    if len(groups) < p.chrome_min_groups:
        return
    clusters: list[list] = []          # [representative key, groups-seen set]
    for gi, g in enumerate(groups):
        for k in {_key(l) for l in g.text.splitlines() if _key(l)}:
            for c in clusters:
                if fuzz.ratio(k, c[0]) >= 85:
                    c[1].add(gi)
                    break
            else:
                clusters.append([k, {gi}])
    chrome = [c[0] for c in clusters if len(c[1]) >= p.chrome_share * len(groups)]
    for g in groups:
        g.text = "\n".join(
            l for l in g.text.splitlines()
            if not any(fuzz.ratio(_key(l), c) >= 85 for c in chrome))


# ------------------------------------------------------------------ merging
def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t.lower()).strip()


def text_contained(a: str, b: str, p: Params) -> bool:
    from rapidfuzz import fuzz
    a, b = _norm(a), _norm(b)
    if len(a) < p.min_text_chars or len(b) < p.min_text_chars:
        return False
    return fuzz.partial_ratio(a, b) >= p.text_threshold


def merge_builds(groups: list[_Group], p: Params) -> list[list[_Group]]:
    """Merge neighbouring groups when one's text contains the other's."""
    merged: list[list[_Group]] = []
    for g in groups:
        if (merged
                and g.start - merged[-1][-1].end < p.max_merge_gap
                and text_contained(merged[-1][-1].text, g.text, p)):
            merged[-1].append(g)
        else:
            merged.append([g])
    return merged


# ------------------------------------------------------------------ main
def extract(video: Path, out_dir: Path, p: Params | None = None,
            log=print) -> dict:
    p = p or Params()
    video, out_dir = Path(video), Path(out_dir)
    img_dir = out_dir / "images"
    cand_dir = out_dir / "_candidates"
    for d in (img_dir, cand_dir):
        if d.exists():
            shutil.rmtree(d)
    out_dir.mkdir(parents=True, exist_ok=True)

    groups, duration = group_frames(video, cand_dir, p)
    log(f"{len(groups)} candidate groups from {duration:.0f}s of video; running OCR...")
    for g in groups:
        g.text = ocr_image(cv2.imread(str(g.image)))
    strip_static_chrome(groups, p)

    merged = merge_builds(groups, p)
    shots = []
    dropped = 0
    img_dir.mkdir(parents=True, exist_ok=True)
    for members in merged:
        start, end = members[0].start, members[-1].end
        if end - start < p.min_dwell:
            dropped += 1
            continue
        best = max(members, key=lambda g: (len(_norm(g.text)), g.start))  # ties: latest
        n = len(shots) + 1
        name = f"screen_{n:03d}.jpg"
        shutil.copy(best.image, img_dir / name)
        alnum = len(re.sub(r"\W", "", best.text))
        is_diagram = (alnum < p.diagram_chars
                      and ink_fraction(cv2.imread(str(img_dir / name)), p.crop_ratio) >= p.diagram_ink)
        shots.append({
            "id": n,
            "image": f"images/{name}",
            "start": round(start, 2),
            "end": round(end, 2),
            "type": "diagram" if is_diagram else "slide",
            "text": best.text,
            "merged_from": len(members),
        })
    shutil.rmtree(cand_dir, ignore_errors=True)

    doc = {
        "source": str(video),
        "duration": round(duration, 2),
        "params": p.__dict__,
        "screenshots": shots,
    }
    (out_dir / "screenshots.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"kept {len(shots)} screenshots, dropped {dropped} (< {p.min_dwell}s) -> "
        f"{out_dir / 'screenshots.json'}")
    return doc
