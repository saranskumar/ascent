# Screen Capture & Screenshot Selection (Oct 2)

## Decision for the MVP

**Use the approach from [lecture-to-notes](https://github.com/drpwchen/lecture-to-notes) (MIT) as-is; tune cropping and the rest later.** Credit it in our README and keep its copyright notice in any code we adapt.

Our own design (below, from GD 4 and later discussion) is the target. It's better for meetings, but theirs has been tuned on real recordings and is good enough to get the pipeline working end to end.

## What we take from lecture-to-notes

| Piece | Their file | What it does | Our change |
| --- | --- | --- | --- |
| Interval sampling | `scripts/extract_slides.py` | one frame every N s (default 15 s) | **1–2 s** for meetings |
| Center-crop pHash | `extract_slides.py` (`phash_cropped`, `crop_ratio=0.65`) | hashes only the centre 65% to ignore borders: a crude content area | later replaced by real content-area detection |
| Two dedup thresholds | `extract_slides.py` (`dedup_frames`) | each frame vs previous (`threshold=40`) **and** vs the group's first frame (drift = 2×), so slow scrolling doesn't merge into one "slide" | keep |
| Quick OCR triage | `scripts/quick_ocr.py` | cheap OCR on candidates | keep |
| Merging bullet-by-bullet slides | `scripts/dedup_semantic.py` | merge neighbours if text is contained (`rapidfuzz partial_ratio ≥ 88`) **or** layout similar (`0.6·SSIM + 0.4·(1−Bhattacharyya) > 0.85`) and < 60 s apart; keep the one with the **longest text** (fully revealed) | keep |
| UI-word stripping | `dedup_semantic.py` (`_load_ui_tokens`) | removes meeting-app UI words from OCR text before comparing | add Google Meet / Zoom words |
| Slide → transcript grounding | `ground_slides.py` | each slide + "transcript segments spoken while it was on screen" | basis for our transcript window (see [summary-pipeline.md](summary-pipeline.md)) |
| VLM for signals only | `vlm_signals.py` (`minicpm-v:8b` via Ollama) | the vision model classifies slides; text comes from OCR | same role for our diagram descriptions |

What it does **not** do: live capture, real content-area detection inside a meeting window, camera tiles. It works on a finished video file.

## Capture

- **Start/stop** on Meetily's `recording.started` / `recording.stopped` webhooks. Use the events' `occurred_at` so screenshot times line up with Meetily's `audio_start_time`.
- **Window picker (decided):** a **small GUI** listing open windows with thumbnails; the user picks once per meeting.
- **Window capture:** Windows Graphics Capture (`windows-capture` Python package) captures **one chosen window** even when covered, never notifications or other apps.
- **Suggested option:** record the chosen window as a **low-frame-rate video (1 fps)** during the meeting (a few MB per minute), then run the lecture-to-notes-style extractor on it after `recording.stopped`. This lets us reuse their code almost directly and tune offline. Delete the video after processing. *(Not yet decided.)*

### Which window to pick

The user may be **presenting or watching**.

| User is… | Pick | Content area |
| --- | --- | --- |
| **Presenting** | the window being shared (PowerPoint, the browser tab with the slides) | mostly the whole window, minus the browser tab and address bars |
| **Watching** | the Meet tab | the shared-content box, without participant tiles, chat or controls |

Gotcha: when **presenting in Google Meet**, the Meet tab shows a "You are presenting" placeholder, **not** your slides. The picker should warn (cheap OCR check) if the user picks a Meet tab that says "You are presenting".

## Selection rules (decided)

- **Minimum dwell ~3 s:** drop screens shown for less than that, **after** merging bullet-by-bullet slides (so a slide that builds over 20 s counts as one 20 s slide).
- **Diagrams:** if OCR finds very little text, mark it as a diagram. Diagram images are sent to **Gemini** (decided Oct 2); everything else goes as OCR text only.
- **Video / animation on screen:** if the content area changes every second for more than ~5 s, keep one representative frame and mark it as `video`.
- **Every screenshot records start AND end time** (needed for the transcript window).

**Output record per distinct screen:** image path, `start`, `end`, OCR text, content-area box, type (`slide` / `diagram` / `video` / `demo`).

## Later: our target design (post-MVP)

### Change detection cascade (GD 4, Hari)
Low-res layout check → content area (recompute on layout change) → pHash → OCR similarity vs previous → keep or discard. Ordered from cheap to expensive so OCR only runs on real candidates. Sample cheaply at ~1 fps; save at full resolution only on change.

### Content-area detection (any app; at least Meet and Zoom)
No off-the-shelf solution exists for this. Existing tools either crop the centre (lecture-to-notes), crop camera footage of a lecture hall (lecture2notes, **AGPL, don't copy code**), or use the platform's separate screen-share stream (Recall.ai bots, Google Meet Media API), which is cloud-only and conflicts with local-first.

Our plan, two cheap OpenCV methods together:
1. **Temporal behaviour (works on any app):** over ~10 s at 1 fps, per small block of the window: toolbars almost never change; camera tiles change constantly but slightly; **shared content is static, then changes all at once**. Take the largest rectangle with that pattern. Also ignores a floating self-view. Needs a few seconds and one slide change to warm up.
2. **Big rectangle (instant, one frame):** edge detection + contours → the largest roughly screen-shaped box (16:9 / 16:10, > ~30% of the window), confirmed by OCR text density. Can mistake a big speaker tile in speaker view.

Use method 2 for the first guess, method 1 to confirm or correct it, rerun on a layout change, and fall back to the whole window (correct for presenting anyway).

**Develop offline:** record 2–3 min clips (OBS / Game Bar) of Meet while watching a share, Zoom sharing, and speaker view. Tune on those; reuse them as demo material.

### Deferred
- **Cursor / click tracking for product demos** (GD 4, Saran): out of scope for 30 h. Cheap alternative later: compare consecutive frames to localise where interaction happened, no model.
- **Demos in general:** stretch goal. The hackathon demo targets **slides + diagrams**.
