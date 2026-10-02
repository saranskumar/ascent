# Visual Context Summary (Meetily workflow)

Summary of what was said **and** shown, built on Meetily Pro. See `docs/tech/` for the design.

## Setup
```
pip install -r requirements.txt
```
Secrets go in a gitignored `.env` next to `cli.py` (or in real environment variables, which win):
```
GEMINI_API_KEY=...        # Google AI Studio key from a project with Gemini quota
MEETILY_PRO_TOKEN=...     # Meetily key with `write` scope, Allow switch on (only used for writing)
```
Reads use Meetily's read-only loopback token; the `.env` key is used only for `PUT summary` / rename.

## Step (a): offline extractor
```
python -m tests.synthetic data/synthetic.mp4        # synthetic 1 fps screen recording + ground truth
python cli.py extract data/synthetic.mp4 --out data/run1
python -m pytest -q
```
Output: `data/run1/screenshots.json` + `images/`. Times are seconds from the start of the video.

## Step (b): summarizer (Gemini, nothing written back yet)
Needs `GEMINI_API_KEY` in the environment (never in a file). Default model `gemini-3.8-flash`
(override with `GEMINI_MODEL` or `--model`).
```
# no Meetily or key needed: show the exact prompt + interleaved input
python cli.py summarize --transcript-file tests/fixtures/transcript.json --screenshots data/run1/screenshots.json --dry-run
# real Gemini call on the fixture transcript
python cli.py summarize --transcript-file tests/fixtures/transcript.json --screenshots data/run1/screenshots.json
# a finished Meetily meeting (read-only loopback token is enough)
python cli.py summarize --meeting <id> --screenshots data/run1/screenshots.json --screen-offset <s>
```
Speakers: Meetily's API gives each segment a diarization cluster id but not the names assigned
in the app, so lines are labelled `Speaker 9:` (or a name you enter in the UI's Speakers panel,
saved in `data/speakers/<meeting>.json`). Gemini: 180 s timeout, retries 429/5xx, and if
`gemini-3.8-flash` stays overloaded (503) it falls back to `GEMINI_FALLBACK_MODEL`
(default `gemini-3.5-flash`, set it empty to disable); the model used is saved per run.

`--screen-offset` = seconds from the audio recording start to the screen video start
(later computed automatically from `recording.started`).

## Step (c): write-back with backup
Needs `MEETILY_PRO_TOKEN` = a key created in Meetily (Settings > Integrations > Apps & scripts) with
the `write` scope. The read-only loopback token cannot PUT.
```
python cli.py publish --run data/run1 --meeting <id> --dry-run   # show what would happen
python cli.py publish --run data/run1 --meeting <id>             # asks for confirmation
```
Order: check write key -> `GET` Meetily's current summary -> save it to `<run>/backups/` -> `PUT`
`{text}` -> read back -> `<run>/published.json`. If the backup fails or Meetily is still generating,
nothing is written. The leading `# Title` is removed from the body (Meetily stores summaries
without it). A meeting still named "New Meeting …" is renamed to the summary's title, as Meetily
does. The summary must have been generated from the same meeting it is written to.
Screenshots are kept (decided); `delete_screenshots_after_summary` in Settings turns deletion on.

## Step (d): window picker + live capture
In the UI: **New run → Watch a window**, pick a window (thumbnails; warns if a Google Meet tab
only shows "You are presenting"), **Start capture**, then **Stop and extract**. CLI:
```
python cli.py windows                                   # list capturable windows
python cli.py capture --title "PowerPoint" --out data/cap1   # Ctrl+C to stop, then extracts
python -m tests.live_capture_check                      # end-to-end check (UI server must be running)
```
Windows Graphics Capture records only the chosen window (even when covered, no cursor). It only
delivers frames on change, so a writer thread saves the latest frame at a steady 1 fps: video time
= wall-clock time since `capture_started_at`. On a run page the screen offset is filled from
`capture_started_at - meeting created_at` (step e will use `recording.started`'s `occurred_at`).
The capture video is deleted after extraction unless Settings keeps it.
Note: the OCR engine is loaded at startup, before any capture. Loading onnxruntime after a WGC
session crashes the next capture natively (see `vcs/ocr.py: warm_up`).

## Step (e): webhook automation (the Meetily workflow)
`python cli.py serve` also subscribes to Meetily events (`--no-webhooks` turns that off).

| Event | Action |
| --- | --- |
| `recording.started` | start capturing the window picked last time (Settings: auto capture) |
| `recording.stopped` | stop the capture, extract screenshots, wait for the transcript, Gemini summary, back up Meetily's, `PUT` ours (Settings: auto write-back) |
| `recording.failed` / `error` / `stop_failed` | stop the capture and keep the screenshots (no summary) |
| `summary.completed` / `summary.failed` | if Meetily replaced our summary, put ours back (its version backed up); if ours wasn't written yet (Meetily was busy), write it now |

Why we write on `recording.stopped` (decided Oct 2 after the live test): Meetily only sends
`summary.completed` when it generates its own summary, and hands-off recordings didn't get one.
The summary events are the guard: "is Meetily still showing ours?" is answered by a content
fingerprint, because Meetily reformats the Markdown it stores (table padding, bullets).

Pattern: **Subscribe** (one webhook, kept in `data/webhook.json` with its `hmac_secret`, reused
across restarts) → **Verify** (`X-Meetily-Signature: sha256=` HMAC-SHA256 of `"{X-Meetily-Timestamp}.{raw body}"`,
±5 min window) → **Deduplicate** (`event_id` persisted in `data/events/` before the 200, so
retries and restarts never double-run) → **Fetch** (payloads are thin; transcript/summary by id,
409 retried) → **Act** (one worker, delivery order; the HTTP handler only acks, within Meetily's 5 s).
Offline: subscription retries until Meetily is up; events that hit "offline" are parked and
re-queued when it's back; unfinished events resume after a restart. Our own `PUT` doesn't fire
`summary.completed` (checked live), and if it ever did, the fingerprint check makes it a no-op.
Webhooks need only the read-only loopback token.

Live check (records ~80 s with your mic and narrates the slides via text-to-speech):
`python -m tests.live_meeting_check` (needs a key with `record` scope for starting the test
recording; the workflow itself needs only read + write).

One-time setup in Meetily (Settings > Integrations):
1. Advanced > Outgoing (webhooks): on, and add `127.0.0.1:8765` under **Local targets**.
2. Allow the new destination ("Waiting for you", or Advanced > Destinations).
3. In our UI, pick the window to watch once (New run). The Overview "Automation" panel shows
   the subscription state and every event with what happened.

`manifest.yaml` is the meetily-workflows catalog entry (validated against the catalog schema in
`tests/test_manifest.py`). For submission it goes in `community-workflows/visual-context-summary/`.

## Web UI
```
python cli.py serve        # http://127.0.0.1:8765  (set GEMINI_API_KEY first to enable summaries)
```
Overview (Meetily / Gemini / write-key status), Runs (screens timeline, summary, exact model input),
New run (upload a recording), Settings. Styling uses Meetily's design tokens (neutral palette,
0.5rem radius, light/dark). Loopback only; writes need an `X-VCS` header; secrets stay in env vars.
Overview also shows the Automation panel (subscription state, setup steps, every event).

## Credits
Screenshot extraction (interval sampling, centre-crop pHash with step + drift thresholds,
text-containment merging of bullet builds) is adapted from
[lecture-to-notes](https://github.com/drpwchen/lecture-to-notes) by drpwchen, MIT License.
