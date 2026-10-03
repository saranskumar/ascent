# Architecture: Meeting Summaries (`code/main`)

A single-process Windows desktop app (PyQt6) that listens to Meetily's webhooks, records the shared window during a meeting, and afterwards writes a summary that knows what was said **and** shown. Everything is local: Meetily's loopback API, local OCR, one local Ollama model. For setup and behaviour see [README.md](README.md); this file explains how the code is put together.

## Big picture

```text
                       Meetily Pro (127.0.0.1:8420)
                         │ webhooks (HMAC)        ▲ REST: transcript, summary, PUT summary, rename
                         ▼                        │
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ core/  (no Qt imports)                                                       │
 │   webserver ─► Controller.handle_webhook ─► EventStore ─► event worker      │
 │                      │                                      │               │
 │                      │            on_started / on_ended / on_summary        │
 │                      ▼                                      ▼               │
 │   WindowRecorder (1 fps) ◄── start/stop capture ──── Controller             │
 │                      │ video                                │ create job     │
 │                      ▼                                      ▼               │
 │   JobQueue (one worker) ──► Controller._run_stage ──► extract → describe     │
 │        data/jobs/*.json                                 → summarize → publish│
 │                      │                                                      │
 │   Store (data/runs/*)   Settings (data/settings.json)   Ollama   writeback   │
 └───────────────▲──────────────────────────────────────────────────────────────┘
                 │ UiHooks (core → UI, via Bridge signals)   direct calls (UI → core)
 ┌───────────────┴──────────────────────────────────────────────────────────────┐
 │ ui/  PyQt6: MainWindow (sidebar + tray), pages/*, picker, theme, bridge      │
 └──────────────────────────────────────────────────────────────────────────────┘
```

Two rules hold the design together:

- **`core/` never imports Qt.** It reaches the UI only through the `UiHooks` interface in `controller.py`; `ui/bridge.py` implements it and turns each call into a Qt signal, so worker threads never touch widgets. The UI calls `Controller` methods directly.
- **The controller owns policy; everything else is mechanism.** The queue doesn't know what a stage does, the extractor doesn't know about Meetily, and the Meetily client doesn't know about jobs.

## Process and threads

`run.py` creates the `QApplication`, makes the app **single-instance** (a `QLocalServer`; a second launch sends `show` to the first and exits), builds `Controller` with a `Bridge`, builds `MainWindow`, then calls `ctl.start()`. Closing the window hides it to the tray; `aboutToQuit` calls `ctl.shutdown()`, which finalizes a running capture and queues it for the next start. Under `pythonw` (no console) stdout/stderr go to `data/app.log`.

| Thread | Started in | Job |
| --- | --- | --- |
| UI (Qt main) | `run.py` | widgets only |
| `webhook-http` | `webserver.serve_in_thread` | `POST /webhook` on `127.0.0.1:<port>` (default 8766), `GET /health` |
| `events` | `Controller._event_worker` | processes accepted webhook events one by one |
| `subscribe` | `Controller._subscriber` | registers/verifies our webhook in Meetily, retrying while Meetily is offline |
| job worker | `JobQueue.start` | runs one stage of one job at a time |
| `capture-tick` | `Controller._capture_ticker` | notices a dead capture, refreshes state |
| `ocr-warmup`, `ollama-start` | `Controller.start` | preload OCR; start `ollama serve` if enabled |
| recorder threads | `WindowRecorder` | Windows Graphics Capture callback plus a video-writer loop |

## Event flow (what happens in a meeting)

```text
recording.started ─► on_started
    auto_capture off? stop.   ask_window on? ─► PickRequest ─► ui.pick_window (picker page)
                                                   │ choose(hwnd) / skip / timeout→last window
                                                   ▼
                                             start_capture ─► WindowRecorder + job(status=capturing)

recording.stopped ─► on_ended
    recorder running? ─► stop_capture ─► job joins the queue (extract, describe, summarize, publish)
    no capture?       ─► transcript_only: run with stages [summarize, publish]
recording.failed/error/stop_failed ─► same stop, but no summary (screens still extracted)

summary.completed / .failed ─► on_summary   (Meetily finished its own summary)
    our job still active?  leave it.
    we already published and Meetily still shows ours? nothing.
    summary ready? ─► queue a [publish] job (write-back check)
    screens only?  ─► queue [describe, summarize, publish]
```

Webhook handling in `Controller.handle_webhook`:

1. Reject if not subscribed (503), if the HMAC (`X-Meetily-Signature` over `X-Meetily-Timestamp` + body, max skew 300 s) is wrong (401), or if the body isn't JSON with a valid `event_id` (400).
2. `EventStore.accept` persists the event under `data/events/` and returns False for a duplicate (we still answer 200). New events go on an in-memory queue; unfinished ones (`queued`/`running`) are re-queued at startup.
3. The handler acks immediately, inside Meetily's 5 s budget. The `events` thread does the work, with `_retry` backoff (3, 10, 30 s) for Meetily being offline or answering `409`.

`Subscription` (`webhooks.py`) stores the webhook id and HMAC secret in `data/webhook.json`, re-registers if the URL or the event list changed, and replaces the webhook if Meetily lost it (404).

## Job queue (`core/jobs.py`)

A **job** is one meeting (a *run*) going through these stages:

```text
capture ─► extract ─► describe ─► summarize ─► publish
(live,     (video →     (VLM on     (timeline of    (back up Meetily's
 outside    screens +    every       speech +        summary, then PUT ours)
 queue)     OCR)         screen)     screens →
                                     Ollama, in
                                     parts if long)
```

- One worker, oldest first. Only one model fits in 4 GB of VRAM, and stages depend on each other. A new meeting can still be *captured* while an older one is processed.
- The queue calls `runner(stage, job, ctx)`. The runner returns `None` (done), `"skipped"`, or `("waiting", decision)` when the user must choose (the overwrite prompt). An exception marks the stage failed; `Canceled` marks it cancelled.
- `Ctx` is what a stage gets: `log`, `progress`, `token` (streams the summary as it is written), `cancelled()` / `check()`.
- Each job is persisted atomically in `data/jobs/<id>.json`. On start, a job that was `running` goes back to `queued` and resumes at its unfinished stage; a job that was `capturing` is marked failed (Retry extracts whatever was recorded).
- Controls: cancel, retry (or *retry from* a stage), run next, move up/down, remove, pause queue (the pause flag is saved in settings). The queue emits `added / changed / log / token / finished / removed / queue` events; `Bridge.on_queue` turns them into signals.
- `on_idle` fires when the queue empties; the controller then unloads the Ollama model. While jobs are waiting the model stays loaded (`keep_alive`).

## Stages in the controller

| Stage | Method | What it does |
| --- | --- | --- |
| extract | `_stage_extract` | Waits for OCR warm-up, then `extractor.extract(video)`. Skips if the video is gone but screens exist. Deletes the video afterwards unless *keep capture video* is on. |
| describe | `_stage_describe` | Asks the vision model about **every** screen (Settings > Describe screens: all / only text-light / none): kind of screen, all text, objects with counts and marks, chart values. An empty answer or an Ollama error gets one more try (Ollama is restarted if it crashed); an empty answer is never saved. Job option `redescribe` redoes all screens. |
| summarize | `_stage_summarize` | Skips if no meeting is linked or auto-summaries are off, and ends as done when there is neither speech nor screens. Fetches the transcript (`_wait_transcript` retries while Meetily still returns 409), works out the **screen offset** (video time vs recording time; a manual value wins), logs the screen context, and calls `summarizer.summarize` with the chosen template. Long meetings go in parts (see below). Writes `summary.md`, `summary.meta.json` and, for split meetings, `summary_parts.json`. |
| publish | `_stage_publish` | Needs a meeting, a summary made for *that* meeting, and a write-scope key. Backs up Meetily's summary, then writes ours; if Meetily already has a different one, the job goes to **waiting** for Replace / Keep / Decide later. |

The same stage code backs the manual actions on the Meetings tab (`regenerate`, `send_summary`, `publish_run`, `resolve_overwrite`), so manual and automatic runs behave identically.

## Screen extraction (`core/extractor.py`, `capture.py`, `ocr.py`)

Adapted from lecture-to-notes (MIT). The recorder writes a 1 fps video of one window. `extract` then:

1. **Activity analysis** (`Activity`): cuts the capture into **layouts** wherever the window's edges change (going full screen, switching tabs). Per layout, pixels that differ from their median in more than 6% of frames are "active", and their rows/columns give the content box (none when < 12% or > 90% moves). Browser tabs, address bar, bookmarks and side panels stay out. With `Params.browser` (the captured process is a browser), a layout where nothing moved drops OCR text in the tab strip (top 9%) and side tabs (left 16%).
2. **Grouping** (`group_frames`): samples frames, compares a centre-crop perceptual hash with two thresholds (step and drift), and opens a new group when the screen changes. Every group keeps its start and end time.
3. **Chrome stripping** (`strip_static_chrome`): drops lines that are the same on nearly every screen (titles, URLs).
4. **OCR** of each group's content area (`ocr.py`; URLs and meeting-app UI words dropped). Screens with almost no text are marked `diagram`. Pictures are content too, so every screen is later described.
5. **Merge builds** (`merge_builds`): bullet-by-bullet slide builds are merged by text containment, keeping the longest text. There is no layout/SSIM merge, because a wrong merge silently deletes content.
6. Screens shown for less than `min_dwell` are dropped; the rest (each cropped to its content area) are written to `screenshots.json` (with `layouts`) and `images/`.

## Summarizing (`core/summarizer.py`, `transcript.py`, `prompts.py`, `ollama.py`)

- `transcript.screen_lines` turns screens into `[SCREEN]` lines (`Shows:` description, `OCR:` text) placed in the transcript by time (plus the offset); speaker ids become names from `data/speakers/`.
- `prompts.py` holds Meetily's final-report prompt plus our screen rules (*summarise what was said; use the screen only to clarify; an on-screen question read out takes the answer marked on screen*), Meetily's chunk and combine prompts, and the template loader. Templates are JSON files in `core/templates/`: `detailed.json` (default: key points with answers, Q&A, steps / demo, work flow, decisions, action items) and Meetily's own.
- **Long meetings** (`summarize`, as Meetily's `processor.rs`): tokens ≈ chars × 0.35. When the merged timeline is larger than the budget (context − answer − system prompt − 300), `chunk_text` splits it on whole lines with ~100 tokens of overlap. `carry_screens` repeats a screen still showing at the top of the next part. Each part is summarised, and the summaries are combined (in rounds if needed). The template is filled from the combined text; the parts and the combined text are returned in `info` and saved by the controller.
- `Ollama.chat_stream` streams tokens (CPU or GPU via `num_gpu`, context and token limits, `repeat_penalty`). `Ollama.ensure_running` can start `ollama serve`.
- `clean_output` post-processes the model's text: normalises spacing, drops repeated sentences (small models loop), trims a cut-off last sentence and removes closing "Note:" lines. `clear_unspoken_commitments` empties Decisions / Action Items when nobody said anything like a decision or task. Placeholder titles ("AI-Generated Title") are never used to rename a meeting (`writeback.is_placeholder_title`).

## Write-back (`core/writeback.py`, `meetily_client.py`)

`writeback.publish` backs up Meetily's current summary into the run's `backups/`, sends `PUT /v1/meetings/{id}/summary`, optionally renames the meeting if it still has a placeholder title, and records `published.json` with a fingerprint of the text. That fingerprint is how `on_summary` later tells whether Meetily still shows our summary or replaced it.

`MeetilyClient` is a thin loopback REST client. Reads use Meetily's read-only loopback token; writes use `MEETILY_PRO_TOKEN` from `.env` (needs `write` scope with the Allow switch on) and are checked with `write_status()` first. `MeetilyOffline` is distinct from other `MeetilyError`s so callers can retry or give up appropriately.

## Persistence (`data/`, gitignored)

```text
data/
  settings.json         Settings (core/config.py: defaults, validation, extract params, num_gpu)
  webhook.json          our webhook id, url, events, HMAC secret
  jobs/<id>.json        one file per job (stages, progress, log, decision)
  events/<id>.json      one file per webhook event (status, outcome log)
  speakers/<meeting>.json   names typed in the Speakers panel
  runs/<run>/           meta.json, capture video (optional), screenshots.json, images/,
                        summary.md, summary.meta.json, summary_parts.json (long meetings),
                        published.json, backups/, pending_overwrite.json / kept_meetily.json
  app.log               stderr when running without a console
```

`Store` (`core/store.py`) is the only code that knows this layout. It also lists runs for the Meetings tab, finds the run for a meeting, builds the merged timeline, suggests the screen offset, and imports runs from `code/extraction-test`. JSON is written atomically.

## UI layer (`ui/`)

| File | Role |
| --- | --- |
| `main_window.py` | Sidebar navigation, tray icon and menu, window show/hide, notifications |
| `bridge.py` | `UiHooks` implementation: core callbacks and queue events become Qt signals |
| `common.py` | Shared widgets; `init_invoker` lets worker threads run a callable on the UI thread |
| `pages/overview.py` | Live status of Meetily, write key, Ollama/model, webhook; connection guide; pending decisions; recent events |
| `pages/live.py` | The job list with stages, progress, log and streaming summary, plus all job controls |
| `pages/meetings.py` | Per-run detail: summary (edit with preview, send to Meetily, Meetily's backup, parts), transcript, screens (list + preview + full-size viewer, describe again), timeline, speakers, model input, link/offset/generate |
| `pages/new_run.py`, `pages/pick.py`, `picker.py` | Manual capture or video import; the window picker with thumbnails |
| `pages/vlm.py` | Compare installed vision models on one image |
| `pages/settings.py` | Edits `Settings`; the controller reads it live on each use |
| `theme.py`, `image_viewer.py` | Light/dark theme; full-size screen viewer |

Pages read `Controller.status()` and react to `Bridge` signals; they hold no pipeline logic.

## Tests (`tests/`, `python -m pytest -q tests`)

| File | Covers |
| --- | --- |
| `test_extractor.py` | Extraction on a synthetic video from `synthetic.py`; content area, layout switches, browser edges (one screen-count case is `xfail`) |
| `test_jobs.py` | Queue behaviour: ordering, persistence, restart recovery |
| `test_pipeline.py` | Controller end to end with fake Meetily and Ollama: stages, decisions, transcript-only, chunking and combining, templates, guards, settings validation |
| `test_writeback.py` | Backup, overwrite decision, fingerprint |
| `test_speakers.py` | Speaker name handling |
| `test_ui_smoke.py` | Windows build and basic interaction |

`Controller` takes `client_factory` and `sleep` as constructor arguments so tests can inject a fake Meetily client and skip retry waits.

## Extending

- **New stage:** add it to `STAGES` / `WEIGHTS` in `jobs.py`, add `_stage_<name>` in the controller and dispatch to it from `_run_stage`.
- **New Meetily event:** add it to `EVENTS` in `webhooks.py` and a branch in `Controller.process_event`. The subscription is re-registered automatically when the list changes.
- **New setting:** add a default in `core/config.py`, then a control in `ui/pages/settings.py`.
- **Different model backend:** everything model-specific is in `ollama.py` and the two call sites (`_stage_describe`, `_stage_summarize`).
