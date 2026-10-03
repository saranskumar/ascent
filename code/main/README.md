# Meeting Summaries

A Windows desktop app that writes Meetily meeting summaries which also know what was **on screen**. It captures the window being presented while Meetily records, then:

1. extracts the distinct screens and reads their text
2. describes each screen with a local vision model
3. merges all of that with Meetily's transcript by time
4. writes a detailed summary with the same local model
5. puts the summary back into Meetily

Everything runs **offline**: Meetily's local API, local OCR and one local Ollama model. No cloud services, no API keys for models.

```
Meetily: recording starts ──► the app comes to the front: pick the window to capture
                              (1 frame per second, even when the window is covered)
Meetily: recording stops  ──► Live queue: Extract screens ─► Describe screens ─► Summarize ─► Write to Meetily
                                          OCR, dedup,        vision model:       same model,     Meetily's own
                                          content area       what each screen    timeline of     summary backed
                                                             shows               speech+screens  up first
```

## Setup (once)

```bash
pip install -r requirements.txt
ollama pull qwen3-vl:2b-instruct
```

1. Copy `.env.example` to `.env` and set `MEETILY_PRO_TOKEN`. This is a Meetily key with the `write` scope and its Allow switch on. Without it, summaries stay in this app.
2. In Meetily, go to Settings > Integrations:
   - Turn on **Allow the CLI on this computer** (the local API on 127.0.0.1:8420).
   - Under Advanced > **Outgoing (webhooks)**, turn webhooks on and add **`127.0.0.1:8766`** under Local targets.
3. Start the app. When Meetily shows "Waiting for you", click **Allow**. The Overview tab ticks off each of these steps live.

Ollama doesn't need starting by hand. The app starts it when it opens, and again if it stops; you can turn that off in Settings.

## Run

```bash
python run.py
```

- Closing the window keeps the app in the tray, still listening for Meetily. Quit it from the tray menu.
- Settings > App > **Start with Windows** starts it in the tray at login (`run.py --minimized`).
- Starting a second copy brings the first one's window to the front.
- `python run.py --no-webhooks` runs without registering anything in Meetily. Only manual runs work in that mode.
- When started without a console (tray, autostart), errors go to `data/app.log`.

## Tabs

| Tab | What |
| --- | --- |
| Overview | Status of Meetily, the write key, the local model, and the webhook subscription. Also the connection guide with live checkmarks, decisions waiting for you, and every Meetily event with what happened. |
| **Live** | The task list. Each meeting being captured or processed shows its steps with progress and a live log, including what each screen shows and the exact screen context given to the model. The summary appears as it's written. Controls: Cancel, Retry (or *Retry from* any step), Run next, ↑/↓, Remove, Pause queue. **Screens / Transcript / Edit & send** buttons jump to the meeting. Jobs run **one at a time**, oldest first (one model fits in 4 GB). A new meeting can be captured while an older one is processed. |
| Meetings | Every meeting, with these tabs: **Summary** (view, **Edit** with live preview, **Send to Meetily**, Meetily's backed-up version, and **Show parts** for long meetings), **Transcript** (by speaker; copy or save as .txt), **Screens** (a list with a large preview and a full-size viewer, ← → to step; **Describe screens again**), Timeline, Speakers (name the diarization speakers) and Model input. You can also link a capture to a Meetily meeting, set the screen offset, and Generate summary. |
| New run | Capture a window by hand, or process a screen-recording video. |
| VLM test | Compare installed models on one image (speed and output). |
| Settings | Model (any installed Ollama model; default `qwen3-vl:2b-instruct`), CPU/GPU, context size, summary template, long-meeting splitting, screen description prompt, extraction tuning, automation, theme, start with Windows, webhook port. Saved in `data/settings.json`. |

## How it works

### Recording start and stop
- **Start:** the app comes to the front over every other window with a window picker. It stays on top until you answer. The last-used window is preselected, and you get a warning if a Meet tab only shows "You are presenting". **Skip** means no capture. With no answer within 45 s (configurable), the last-used window is captured.
- **Stop:** the job joins the queue straight away and the window opens on Live. A recording with no capture still shows up and gets a transcript-only summary. One with no speech and nothing on screen ends as done, with nothing to summarize.

### Screens
- **Distinct screens:** frames are sampled every second, grouped by perceptual hash, and slides revealed bullet by bullet are merged. Screens shown for under 3 s are dropped.
- **Content area only:** the capture is split wherever the window's edges change (going full screen, switching tabs). In each part, only the area that actually changes (the slide or the video) is OCRed and kept. Browser tabs, the address bar, bookmarks and side panels stay out. When nothing moves in a browser window, the tab strip and side tabs are dropped from the OCR. URLs are never kept.
- **Descriptions:** every screen, pictures included, is described by the local vision model: what kind of screen, all the text exactly as written, every object with counts and marks (ticks, circles, arrows), and chart values. An empty answer or an Ollama error gets one more try.

### Summary
- **One timeline:** speech lines and `[SCREEN]` lines (`Shows:` description, `OCR:` text) are merged by time, aligned with Meetily's recording start. The model uses a screen to make speech clear ("find the biggest fruit" → the watermelon with the tick). It never treats the screen as something that was said.
- **Templates** (`core/templates/`): the default is **Detailed**:
  - Summary
  - Key Points: each point with its answer or result
  - Questions & Answers: question, answer, who asked, time
  - Steps / Demo: numbered
  - Work Flow: who hands what to whom
  - Key Decisions: with reason and time
  - Action Items: owner, task, due date, where it was said

  Meetily's own templates can be picked in Settings: Standard, Project Sync, Daily Standup, Retrospective, Client / Sales.
- **Long meetings** (the same method as Meetily's `summary/processor.rs`):
  1. When the timeline doesn't fit the model's context (tokens ≈ characters × 0.35), it's split into parts of about 100 tokens' overlap, on whole lines. A screen still showing when a part starts is repeated at the top of that part.
  2. Each part is summarised, keeping times, names, questions with answers, steps, and tasks with owners.
  3. The part summaries are combined, in rounds if still too long.
  4. The template is filled from the combined text. The part summaries are saved (Meetings > Summary > Show parts).
- **Guards for small models:**
  - Repeated sentences, a cut-off last sentence and closing "Note:" lines are removed, and a repeat penalty is applied.
  - Decisions and Action Items are cleared when nobody actually said anything like a decision or a task.
  - Placeholder titles ("AI-Generated Title", "Meeting Summary") are never used to rename a meeting.

### Write-back
- **Meetily has no summary:** ours is written.
- **Meetily already has a different one:** the job waits on **Replace with ours / Keep Meetily's / Decide later**, on Live, Overview and Meetings.
- **Backup:** Meetily's version is always saved to the run's `backups/` first.
- **Renaming:** a meeting with Meetily's default name ("New Meeting …", "[Recording] …") is renamed to the summary's title.

### Reliability
- **Restarts:** jobs are kept in `data/jobs/`. A job that was running resumes at its unfinished step after a restart. Quitting during a capture finalizes the video and queues it.
- **Model memory:** the model stays loaded while jobs are queued and is unloaded when the queue is empty.
- **Webhooks:** Subscribe, Verify HMAC, Deduplicate, Fetch, Act. Events are stored in `data/events/` before being acknowledged, and handled in order. If Meetily is offline they're parked and retried.

## Files

How the pieces fit together (threads, event flow, job queue, persistence): [ARCHITECTURE.md](ARCHITECTURE.md).

```
run.py              entry point (tray app, single instance)
core/               no UI
  controller.py       stages (extract, describe, summarize, publish), decisions, webhook automation
  jobs.py             the processing queue behind Live
  extractor.py        screens from the capture video (sampling, dedup, layouts, content area)
  capture.py          1 fps window recording (Windows Graphics Capture)
  ocr.py              local OCR (RapidOCR), UI/URL filtering
  ollama.py           local model calls (streaming, vision, start/unload)
  summarizer.py       timeline input, chunking, combining, clean-up, guards
  prompts.py          Meetily's prompts + our screen rules; templates/ (ours + Meetily's)
  transcript.py       speech + [SCREEN] lines by time
  meetily_client.py   Meetily's local API; writeback.py: backup + PUT + rename
  store.py, config.py, webhooks.py, webserver.py, windows.py, autostart.py
ui/                 PyQt6: main_window (sidebar + tray), pages/*, picker, image_viewer, theme
data/               (gitignored) settings.json, runs/<run>/, jobs/, events/, speakers/, app.log
tests/              python -m pytest -q tests   (offline: fake Ollama and fake Meetily)
```

One extractor test is an expected failure (`xfail`): a scrolling slide is split into more parts than the test expects. That only affects the screen count.

## Credits

- Screenshot extraction is adapted from [lecture-to-notes](https://github.com/drpwchen/lecture-to-notes) by drpwchen (MIT).
- The summary prompts, chunking method and the Standard / Project Sync / Daily Standup / Retrospective / Client templates come from [Meetily](https://github.com/Zackriya-Solutions/meetily) (MIT, (c) 2024 Zackriya Solutions).
