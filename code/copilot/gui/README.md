# Part 3: GUI (Mahreen)

One app with two windows. The tech stack is your choice: web/Electron/Tauri, PySide, or whatever is quickest for you. The only requirement is that it can open a WebSocket and load images from a URL.

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

## Things to watch out for

- **Canvas while shared:** Meet/Zoom/Teams can capture a window that's *behind* other windows, but not a *minimized* one. The host should keep it open, just not in front.
- **Overlay if the host shares the whole screen:** on Windows, `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)` hides a window from screen capture. Electron has `win.setContentProtection(true)`, which does the same thing.
- **Option for web stacks:** the canvas could be a browser tab that the host shares with Meet's "share a tab" option. Tab sharing gives sharper output than window sharing.
- **Canvas behaviour to decide:** replace the image or stack images, captions on or off, a "clear canvas" button, and fit vs fill. All of this happens inside the GUI; the engine doesn't need to know about it.
