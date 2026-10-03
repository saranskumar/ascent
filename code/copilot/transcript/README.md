# Part 1: Live Transcript Service (Hari)

Serves live `segment` messages on `ws://127.0.0.1:8771/transcript` (+ `GET /transcript/history`) following the contract in [contracts/README.md](../contracts/README.md).

The text is always **Meetily Pro's own live transcript** (its Parakeet model, mic + system audio). There is no microphone fallback and no second transcription: if Meetily isn't recording, nothing is published.

---

## How It Works

**Default: follow Meetily's files.** While recording, Meetily rewrites `transcripts.json` in the meeting's folder (`~/Music/meetily-recordings/Meeting <date>/`) every time it finalises a segment (its log says "wrote transcripts.json with 14 / 15 / 16 segments"). `main.py` watches those files and publishes each new segment:

- no debug port, no restart of Meetily, no API call or key;
- `speaker`: `host` = your microphone, `guest` = system audio (the other people);
- `start` / `end` = Meetily's audio clock (seconds from the recording start);
- segments are final only. Meetily writes a segment once it is finished, so there are no partials and a few seconds of lag;
- meetings that existed before the service started are skipped. If you start the service in the middle of a recording, it catches up on that one recording.

The recordings folder is found from `--dir`, `$MEETILY_RECORDINGS_DIR`, the default `~/Music/meetily-recordings`, or the path in Meetily's log.

**Only source.** Nothing else produces transcript text: no microphone, no second model, no debug port. (`POST /transcript/say` still exists because the engine console's typing box posts to it; it is a dev-only injection, not used when you run with Meetily.)

---

## How to Run

```powershell
python -m transcript.main
```
Expected: `Following Meetily Pro's live transcript in <your Music>\meetily-recordings`, then `[#n] [  8.9s -  12.7s] (mic) Hello, hello.` lines as you record in Meetily Pro.

Verify the stream:
- **Terminal tap**: `python -m mocks.tap transcript`
- **Web visualizer**: open `code/copilot/mocks/live_transcript.html`
- **REST history**: `GET http://127.0.0.1:8771/transcript/history`
