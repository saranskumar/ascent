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
  * screens with very little OCR text are marked "diagram" (described later by the local VLM,
    in the job's Describe stage).
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .errors import Canceled
from .ocr import ocr_image


def _noop(*_):
    pass


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
    content_crop: bool = True      # OCR/describe only the part of the window that changes
    content_line_share: float = 0.04   # a row/column is content if this share of it changed
    content_active: float = 0.06       # a pixel is content if it differs from its median this often
    browser: bool = False              # the captured window is a web browser (set by the app)
    # Browser layouts where nothing moved (a quiz card, a paused video): no content box can be
    # found, so text in the browser's own edges (tab strip/toolbar, vertical tabs) is dropped.
    browser_edges: tuple = (("top", 0.09), ("left", 0.16))
    content_min_area: float = 0.12     # smaller changing area: keep the whole window


@dataclass
class _Group:
    start: float
    first_hash: np.ndarray
    last_hash: np.ndarray
    last_t: float
    image: Path
    end: float = 0.0
    text: str = ""
    box: tuple | None = None


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


# ------------------------------------------------------------------ content area
class Activity:
    """Which part of the window is content, per stretch of the capture.

    A browser's tabs, address bar, bookmarks and side panels stay still while the video or the
    slides change; their text (tab titles like "ChatGPT", URLs) must not reach the summary as if
    it had been shown in the meeting.

    The capture is first cut into *layouts* wherever the window's edges change a lot (going full
    screen, switching apps or tabs): one box can't fit both "page with tabs" and "video filling
    the window" (seen live). Within a layout, a pixel is "active" when it differs from its usual
    (median) look in a fair share of the frames; the box spans the rows/columns with enough
    active pixels. No box (whole window) when too little or nearly everything moves."""

    WIDTH = 160                     # low-res map; plenty for finding bands of chrome
    MAX_FRAMES = 1200               # keep a spread-out sample of long captures
    EDGE = 0.1                      # share of width/height that makes up the window's edges
    LAYOUT_CHANGE = 22              # mean grey difference of the edges that starts a new layout

    def __init__(self):
        self.frames: list[np.ndarray] = []
        self.times: list[float] = []
        self.stride = 1
        self._n = 0
        self.shape: tuple[int, int] | None = None
        self._segments = None

    def add(self, frame, t: float | None = None) -> None:
        h, w = frame.shape[:2]
        self._n += 1
        if (self._n - 1) % self.stride:
            return
        if self.shape is not None and self.shape != (h, w):
            return                                   # the capture is letterboxed: same size always
        self.shape = (h, w)
        sh = max(1, int(h * self.WIDTH / w))
        self.frames.append(cv2.cvtColor(cv2.resize(frame, (self.WIDTH, sh),
                                                   interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY))
        self.times.append(float(self._n - 1) if t is None else float(t))
        self._segments = None
        if len(self.frames) > self.MAX_FRAMES:      # thin out evenly; keeps memory flat
            self.frames, self.times = self.frames[::2], self.times[::2]
            self.stride *= 2

    # ---- layouts
    def _edges(self, g: np.ndarray) -> np.ndarray:
        sh, sw = g.shape
        ey, ex = max(1, int(sh * self.EDGE)), max(1, int(sw * self.EDGE))
        return np.concatenate([g[:ey].ravel(), g[:, :ex].ravel(), g[:, -ex:].ravel()]).astype(np.int16)

    def layouts(self) -> list[tuple[int, int]]:
        """[(first, last+1)] frame index ranges with the same window edges."""
        out, start, ref = [], 0, None
        for i, g in enumerate(self.frames):
            e = self._edges(g)
            if ref is not None and np.abs(e - ref).mean() > self.LAYOUT_CHANGE:
                out.append((start, i))
                start = i
            if i == start:
                ref = e
        if self.frames:
            out.append((start, len(self.frames)))
        return out

    # ---- boxes
    def _still(self, frames: list[np.ndarray], p: "Params") -> bool:
        """Nothing (or almost nothing) moved in these frames."""
        if len(frames) < 2:
            return True
        stack = np.stack(frames).astype(np.int16)
        active = (np.abs(stack - np.median(stack, axis=0)) > 20).mean(axis=0) > p.content_active
        return active.mean() < p.content_min_area

    def _box_for(self, frames: list[np.ndarray], p: "Params") -> tuple[int, int, int, int] | None:
        if len(frames) < 3:
            return None
        stack = np.stack(frames).astype(np.int16)
        active = (np.abs(stack - np.median(stack, axis=0)) > 20).mean(axis=0) > p.content_active
        rows = np.where(active.mean(axis=1) > p.content_line_share)[0]
        cols = np.where(active.mean(axis=0) > p.content_line_share)[0]
        if not len(rows) or not len(cols):
            return None
        sh, sw = active.shape
        y0, y1, x0, x1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
        share = (y1 - y0) * (x1 - x0) / (sh * sw)
        if share < p.content_min_area or share > 0.9:
            return None
        my, mx = max(1, int(sh * 0.015)), max(1, int(sw * 0.015))      # a little margin
        y0, x0 = max(0, y0 - my), max(0, x0 - mx)
        y1, x1 = min(sh, y1 + my), min(sw, x1 + mx)
        h, w = self.shape
        return (int(x0 * w / sw), int(y0 * h / sh), int(x1 * w / sw), int(y1 * h / sh))

    def segments(self, p: "Params") -> list[dict]:
        """[{start, end, box}] per layout, times in seconds of the video."""
        if self._segments is None:
            segs = []
            for a, b in self.layouts():
                box = self._box_for(self.frames[a:b], p) if p.content_crop else None
                end = self.times[b] if b < len(self.times) else float("inf")
                still = box is None and self._still(self.frames[a:b], p)
                segs.append({"start": self.times[a], "end": end, "box": box, "still": still})
            self._segments = segs
        return self._segments

    def segment_at(self, t: float, p: "Params") -> dict:
        for s in self.segments(p):
            if s["start"] <= t < s["end"]:
                return s
        return {"box": None, "still": False}

    def box_at(self, t: float, p: "Params") -> tuple[int, int, int, int] | None:
        return self.segment_at(t, p)["box"]

    def box(self, p: "Params") -> tuple[int, int, int, int] | None:
        """One box for the whole capture (ignores layout changes)."""
        return self._box_for(self.frames, p) if p.content_crop else None


def crop(img, box):
    if box is None or img is None:
        return img
    x0, y0, x1, y1 = box
    return img[y0:y1, x0:x1]


# ------------------------------------------------------------------ grouping
def group_frames(video: Path, cand_dir: Path, p: Params, progress=_noop,
                 cancel=lambda: False, activity: "Activity | None" = None
                 ) -> tuple[list[_Group], float]:
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
        if cancel():
            cap.release()
            raise Canceled()
        if nframes:
            progress(idx / nframes)
        next_t = t + p.interval
        last_t = t
        if activity is not None:
            activity.add(frame, t)
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
            log=print, progress=_noop, cancel=lambda: False) -> dict:
    """progress(fraction 0..1, label) is called often; cancel() is polled and raises Canceled."""
    p = p or Params()
    video, out_dir = Path(video), Path(out_dir)
    img_dir = out_dir / "images"
    cand_dir = out_dir / "_candidates"
    for d in (img_dir, cand_dir):
        if d.exists():
            shutil.rmtree(d)
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"sampling {video.name} every {p.interval:g}s...")
    activity = Activity()
    groups, duration = group_frames(video, cand_dir, p, cancel=cancel, activity=activity,
                                    progress=lambda f: progress(0.5 * f, "sampling frames"))
    segments = activity.segments(p)
    h, w = activity.shape or (0, 0)
    for sg in segments:
        span = f"{sg['start']:.0f}s-" + ("end" if sg["end"] == float("inf") else f"{sg['end']:.0f}s")
        if sg["box"] is not None:
            b = sg["box"]
            log(f"layout {span}: content area {b[2] - b[0]}x{b[3] - b[1]} of {w}x{h}; the window's "
                f"still parts (tabs, toolbars, side panels) are left out")
        elif sg.get("still") and p.browser and p.content_crop:
            log(f"layout {span}: nothing moved, so the content area is unknown; the browser's "
                f"tab strip, toolbar and side tabs are left out of OCR")
        else:
            log(f"layout {span}: whole window used (it all changes, e.g. full screen, or too "
                f"little moves to tell)")
    log(f"{len(groups)} candidate groups from {duration:.0f}s of video; running OCR...")
    for i, g in enumerate(groups):
        if cancel():
            raise Canceled()
        progress(0.5 + 0.5 * i / max(1, len(groups)), f"OCR {i + 1}/{len(groups)}")
        seg = activity.segment_at(g.last_t, p)
        g.box = seg["box"]
        edges = dict(p.browser_edges) if (p.browser and p.content_crop and seg.get("still")) else None
        g.text = ocr_image(crop(cv2.imread(str(g.image)), g.box), skip_edges=edges)
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
        if best.box is None:
            shutil.copy(best.image, img_dir / name)
        else:                                   # the screen image is the content area too
            cv2.imwrite(str(img_dir / name), crop(cv2.imread(str(best.image)), best.box),
                        [cv2.IMWRITE_JPEG_QUALITY, 90])
        alnum = len(re.sub(r"\W", "", best.text))
        is_diagram = (alnum < p.diagram_chars
                      and ink_fraction(cv2.imread(str(img_dir / name)), p.crop_ratio) >= p.diagram_ink)
        shot = {
            "id": n,
            "image": f"images/{name}",
            "start": round(start, 2),
            "end": round(end, 2),
            "type": "diagram" if is_diagram else "slide",
            "text": best.text,
            "merged_from": len(members),
            "box": list(best.box) if best.box else None,
        }
        shots.append(shot)
    shutil.rmtree(cand_dir, ignore_errors=True)

    doc = {
        "source": str(video),
        "duration": round(duration, 2),
        "params": p.__dict__,
        "layouts": [{"start": sg["start"], "end": None if sg["end"] == float("inf") else sg["end"],
                     "box": list(sg["box"]) if sg["box"] else None} for sg in segments],
        "screenshots": shots,
    }
    (out_dir / "screenshots.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    progress(1.0, "done")
    log(f"kept {len(shots)} screenshots ({sum(s['type'] == 'diagram' for s in shots)} diagrams), dropped {dropped} (< {p.min_dwell}s) -> "
        f"{out_dir / 'screenshots.json'}")
    return doc
