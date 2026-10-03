# Screen Capture & Screen Extraction (Oct 3)

As built in [`code/main`](../../code/main/README.md): `capture.py`, `windows.py`, `extractor.py`, `ocr.py`, and the Describe stage in `controller.py`.

## Capture

- **Trigger:** Meetily's `recording.started` webhook. The app comes to the front over every other window with a **window picker**: thumbnails of open windows, the last-used one preselected. It stays on top until answered. Windows normally keeps a background app behind the active window; the app works around that, and the picker has its own sidebar entry so focus can't jump away from it.
- **No answer:** after 45 s (Settings, 0 = keep waiting) the last-used window is captured. **Skip** means no capture; the meeting still gets a transcript-only summary.
- **Recording:** Windows Graphics Capture (`windows-capture`) records **one window**, even when it's covered, never notifications or other apps. A writer thread saves the latest frame at a steady **1 fps**, so video time equals wall-clock time since the capture started. That is what lines screens up with Meetily's `audio_start_time`.
- **Stop:** `recording.stopped` (or the window closing) stops the capture and queues extraction. The video is deleted afterwards unless Settings keeps it.

### Which window to pick

| User is… | Pick |
| --- | --- |
| **Presenting** | the window being shared (PowerPoint, the browser tab with the slides) |
| **Watching** | the meeting tab (Meet, Zoom) |

Gotcha: when **presenting in Google Meet**, the Meet tab shows a "You are presenting" placeholder, not your slides. The picker warns when the selected window says that (a quick OCR check).

## Distinct screens (adapted from [lecture-to-notes](https://github.com/drpwchen/lecture-to-notes), MIT)

| Step | How |
| --- | --- |
| Sampling | one frame per second |
| Grouping | perceptual hash of the centre 65%. A new screen starts when a frame differs from the previous one by more than 40 bits **or** from the group's first frame by more than 80 (drift, so slow scrolling doesn't stay one "slide"). The last frame of a group is kept, so a bullet-by-bullet slide is fully revealed |
| Window chrome | OCR lines found in most groups (window title, tab bar, footer) are removed |
| Bullet builds | neighbouring screens are merged when one's text contains the other's (`rapidfuzz partial_ratio ≥ 88`, < 60 s apart). The one with the longest text is kept |
| Short screens | under 3 s (after merging) are dropped |
| Output | `screenshots.json`: per screen `image`, `start`, `end`, `text`, `type`, `box`, `merged_from`; plus `layouts` |

## Content area (only what changes)

Browser tabs, bookmarks and side panels aren't meeting content. Read as text, they turned into summaries about "AI tools" (a vertical tab strip with ChatGPT, Gemini and so on). So only the content area is OCRed and kept:

1. **Layouts:** the capture is cut wherever the window's **edges** (top and side strips) change a lot, such as going full screen or switching apps or tabs. One box can't fit both "page with tabs" and "video filling the window".
2. **Per layout:** at low resolution (160 px wide), a pixel is **active** when it differs from its **median** in more than 6% of the frames. Rows and columns with enough active pixels give the box, plus a small margin.
   - Earlier we counted changes between neighbouring frames. A single scroll or a full-screen switch then made the whole window count; against the median, a few seconds don't matter.
3. **No box**, so the whole window is used, when less than 12% or more than 90% of it moves. That's correct for full screen, where everything is content.
4. **Browser layouts where nothing moved** (a quiz card that holds still, a paused video): text in the browser's tab strip (top 9%) and side tabs (left 16%) is dropped from the OCR.
5. **URLs** are never kept, and meeting-app UI words (Mute, Share, Leave, …) are dropped.

The screen image saved for each screen is its content area. The job log lists every layout and its box.

## Screen descriptions

The vision model (the same Ollama model that writes the summary) describes **every** screen, pictures included. For a quiz, a product photo or a diagram, the picture *is* the content. The default prompt (editable in Settings) asks for:

1. what kind of screen it is (slide, quiz question, chart, document, website, video frame) and its title or question
2. all the text, exactly as written, including blanks like `C__W`
3. every picture, object, animal and person (by role, not looks), with counts and any marks (ticks, crosses, circles, arrows) and what they point to
4. for charts and tables: labels, values, what is biggest or changing

It also says: only what's visible, nothing about what isn't there, and colours only when they carry meaning. Example: *"Find the biggest fruit? … a watermelon, a peach, a mango, an avocado, raspberries; a large green checkmark is over the watermelon."*

- **Answers are tidied:** repeated sentences and cut-off endings are removed, and at most 14 sentences are kept.
- **Failures are retried:** an empty answer or an Ollama error gets one more try (Ollama is restarted if it crashed). An empty answer is never saved.
- **Older meetings:** Meetings > Screens > **Describe screens again** redoes every screen with the current prompt.
- **Choosing what's described:** Settings > Describe screens: every screen / only screens with little text / none.
- **Speed:** about 4–11 s per screen on a 4 GB RTX 2050 (GPU) with `qwen3-vl:2b-instruct`; on CPU, about 30 s.

## Not done (ideas)

- Content area inside a meeting tab while **watching** (the shared-content box without participant tiles): the per-layout median finds the moving area, which can include camera tiles.
- Cursor and click tracking for product demos.
