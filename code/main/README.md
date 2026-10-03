# Meeting Summaries (main)

The production version of `../extraction-test`. It builds Meetily summaries that also know what was **on screen**, as one Windows desktop app. Everything runs **offline**: Meetily's local API, local OCR and one local Ollama model. No web UI and no cloud models.

```
Meetily recording starts ─► pick the window to capture (in this app's window)
                │                    1 fps window recording
Meetily recording stops ──► Live: Extract screens ─► Describe diagrams ─► Summarize ─► Write to Meetily
                                   (OCR, dedup)        (vision model)      (same model)   (backup first)
```

## Setup (once)

```bash
pip install -r requirements.txt
ollama pull qwen3-vl:2b-instruct
```

1. Copy `.env.example` to `.env` and set `MEETILY_PRO_TOKEN`. This is the Meetily key with `write` scope, with its Allow switch on. Without it, summaries stay in this app.
2. In Meetily, go to Settings > Integrations:
   - Turn on **Allow the CLI on this computer**.
   - Under Advanced > **Outgoing (webhooks)**, turn webhooks on and add **`127.0.0.1:8766`** under Local targets. The test version used 8765, so both can stay installed.
3. Start the app. When Meetily shows "Waiting for you", click **Allow**. The Overview tab ticks off each of these steps live.

## Run

```bash
python run.py
```

- Closing the window keeps the app in the tray, still listening. Quit it from the tray menu.
- Settings > App > **Start with Windows** starts it in the tray at login (`run.py --minimized`).
- Starting a second copy brings the first one's window to the front.
- `python run.py --no-webhooks` runs without registering anything in Meetily. Only manual runs work in that mode.

## Tabs

| Tab | What |
| --- | --- |
| Overview | Status of Meetily, the write key, Ollama/model and the webhook subscription. Also the connection guide with live checkmarks, decisions waiting for you, and every Meetily event with what happened. |
| **Live** | The task list. Each job shows the meeting being captured or processed, its steps with progress, a live log, and the summary as the model writes it. Controls: Cancel, Retry (or *Retry from* any step), Run next, ↑/↓, Remove, Pause queue. Jobs run **one at a time**, oldest first, because only one model fits in 4 GB. A new meeting can still be captured while an older one is processed. |
| Meetings | Every run, with summary (and Meetily's backed-up one), screens with OCR and descriptions, timeline, speaker names, and the exact model input. You can link a run to a Meetily meeting, set the screen offset, Generate summary, Write to Meetily, and answer Replace/Keep. |
| New run | Capture a window by hand, or process a screen-recording video. |
| VLM test | Compare installed models on one image (speed and output). |
| Settings | Model (dropdown of installed Ollama models; default `qwen3-vl:2b-instruct`), CPU/GPU, context, token limits, prompt, keep-loaded. Also extraction tuning, automation toggles, theme, start with Windows, webhook port, and importing runs from `extraction-test`. |

## Behaviour

- **Recording starts:** the window opens on a picker with thumbnails. The last window used is preselected, and you get a warning if a Meet tab only shows "You are presenting". **Skip** means no capture. Settings can switch this to "use the remembered window" or turn capture off.
- **Recording stops:** the capture stops and the job joins the queue right away, with no prompt. The window opens on Live. If no window was captured (skipped, not picked in time, or capture off), the meeting still shows up and gets a summary from its transcript alone.
- **Only the meeting's content reaches the model:**
  - Extraction finds the part of the window that changes (the slides or the video) and OCRs and keeps only that. Browser tabs, address bar, bookmarks and side panels are left out, along with their URLs.
  - Frames that are just pictures (a film, people, scenery) are marked as pictures and left out.
  - The prompt tells the model to summarise what was *said* and to use the screen only to clarify it.
  - Repeated sentences, a cut-off last sentence and closing "Note:" lines are removed. Small models loop, and `repeat_penalty` in Settings also counters that.
- **Write-back** works as in the test version. If Meetily has no summary, ours is written. If Meetily already has a different one, the job waits on **Replace with ours / Keep Meetily's / Decide later**, shown on Live, Overview and Meetings. Meetily's version is always saved to the run's `backups/` first.
- **Model memory:** the model stays loaded while jobs are queued (`keep_alive` 10 min) and is unloaded as soon as the queue is empty.
- **Restarts:** jobs are kept in `data/jobs/`. A job that was running is queued again and resumes at its unfinished step. Quitting during a capture finalizes the video and queues it.

## Files

```
run.py              entry point (tray app, single instance)
core/               no UI: controller (stages, decisions, webhook automation), jobs (queue),
                    store (runs on disk), ollama, summarizer, extractor, capture, windows,
                    ocr, meetily_client, writeback, webhooks, webserver, config, autostart
ui/                 PyQt6: main_window (sidebar + tray), pages/*, picker, theme, bridge
data/               (gitignored) settings.json, runs/<run>/, jobs/, events/, speakers/, webhook.json
tests/              python -m pytest -q tests
```

The two `xfail` extractor tests also fail in `extraction-test`. The scrolling-slide case isn't split at `drift_threshold` 80, and that predates this app.

Screenshot extraction is adapted from [lecture-to-notes](https://github.com/drpwchen/lecture-to-notes) by drpwchen (MIT). The summary prompt and template follow Meetily (MIT).
