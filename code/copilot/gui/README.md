# Part 3: GUI (Mahreen)

One PySide6 app with two windows. It only needs to open a WebSocket and load images from a URL. Architecture: [../ARCHITECTURE.md](../ARCHITECTURE.md#part-3-gui).

| Window | Who sees it | What it does |
| --- | --- | --- |
| **Overlay** | the host only | Small always-on-top panel. Shows incoming suggestions (topic, reason, 2–3 thumbnails). Clicking a thumbnail puts it on the canvas; ✕ dismisses the suggestion. |
| **Canvas** | everyone in the meeting | Blank window the host shares (as a window, not their whole screen) at the start of the meeting. Shows the selected image. |

## What you connect to

- `ws://127.0.0.1:8772/suggestions` receives `suggestion` messages and accepts `select` / `dismiss` messages back. The format is in [../contracts/README.md](../contracts/README.md).
- Images: just load `image.url` / `image.thumb_url`. They're plain HTTP URLs, so there are no files to handle.

Remember the last `seq` you received. When you reconnect, use `?since=<seq>` so you don't miss or duplicate anything. A first connection without `since` gets the last 2 minutes of suggestions.

## Develop without the engine

```bash
python -m mocks.mock_engine --speed 2 --loop
```
This sends scripted suggestions with placeholder images and prints every select/dismiss it receives, so you can confirm your messages are right. [`../mocks/preview.html`](../mocks/preview.html) is a ~80-line throwaway version of overlay + canvas. It's a reference for the connection code, not a design.

## Features (from drops/initial_idea.md §2.1–2.4)

Must have:
- [x] **Overlay** lists incoming suggestions: topic, reason, 2–3 thumbnails, ✕ to dismiss (sends `dismiss`).
- [x] **Click / select a thumbnail** puts it on the canvas and sends `select`.
- [x] **Canvas** shows the active image centred and fitted, on a neutral dark background (`#0f172a`), with a short crossfade between images.
- [x] **Two suggestion states** based on `priority`:
  - `ambient` ("ghost"): faint (~30% opacity), no sound or animation that pulls attention.
  - `elevated`: full opacity with a subtle border glow.
  - Hide a suggestion after `expires_in` seconds unless the host is hovering over it.

Should have:
- [x] **Keyboard controls** (when the overlay is focused):
  - `1` / `2` / `3`: put image 1/2/3 of the newest suggestion on the canvas.
  - `Space`: put the highlighted image on the canvas.
  - `←` / `→`: step back and forward through the history of what has been shown.
- [x] **History reel** in the overlay: past images shown on the canvas, dimmed (~40%), so the host can go back to one with a click or `←`.
- [x] **Panic shutter** (`B` or `Esc`): instantly blank the canvas to a neutral slate. The host keeps sharing the canvas window, but the audience sees nothing. Press again to bring the image back.

Nice to have:
- [x] **Paste / drop to stage:** dropping an image file on the overlay, or pressing `Ctrl+V` with an image on the clipboard, puts it on the canvas straight away. This is purely local and never touches the engine.
- [x] Overlay window hidden from screen capture (see below).

## How to run

The GUI is a PySide6 app. It does not talk to Meetily. It only needs the suggestions socket.

From `code/copilot`:

```bash
pip install -r requirements.txt
python -m mocks.mock_engine --speed 2 --loop
python -m gui
```

`python -m gui` opens **Co-pilot overlay** (always on top, excluded from screen capture) and **Co-pilot canvas** (share this window). The mock prints every `select` and `dismiss`.

`run_all.ps1` starts this GUI with `python -m gui` after the engine. The port follows `COPILOT_ENGINE_PORT` (default `8772`).

## Things to watch out for

- **Canvas while shared:** Meet/Zoom/Teams can capture a window that's *behind* other windows, but not a *minimized* one. The host should keep it open, just not in front.
- **Overlay if the host shares the whole screen:** on Windows, `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)` hides a window from screen capture. Electron has `win.setContentProtection(true)`, which does the same thing.
- **Option for web stacks:** the canvas could be a browser tab that the host shares with Meet's "share a tab" option. Tab sharing gives sharper output than window sharing.
- **Canvas behaviour to decide:** replace the image or stack images, captions on or off, a "clear canvas" button, and fit vs fill. All of this happens inside the GUI; the engine doesn't need to know about it.
