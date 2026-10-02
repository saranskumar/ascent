# Part 1: Live Transcript Service (Hari)

Serves live `segment` messages on `ws://127.0.0.1:8771/transcript` (+ `GET /transcript/history`) following the contract in [contracts/README.md](../contracts/README.md).

---

## How It Works

`transcript/main.py` provides real-time speech transcription with smart auto-detection:

1. **Meetily Pro Bridge (Option 2 from docs)**:
   - When Meetily Pro is running with remote debugging enabled (`port 9222`), `main.py` connects to its WebView2 CDP instance and hooks the live `transcript-update` event bus.
   - It captures Meetily's native NVIDIA Parakeet v3 transcription segments in real time without having to build Meetily from source.
2. **Standalone Microphone Fallback (Option 1 from docs)**:
   - If Meetily Pro is **not** running, `main.py` automatically falls back to transcribing the default microphone locally using `faster-whisper` (`base.en` INT8 on CPU).
3. **Manual Typing Mode**:
   - For offline testing or scripted demos, pass `--typing`.
4. **HTTP Say Endpoint**:
   - `POST /transcript/say` with JSON `{"text": "..."}` to inject speech programmatically.

---

## How to Run

### Step 1: (Optional) Launch Meetily Pro with Debug Port
To feed live transcripts from your installed Meetily Pro:
```powershell
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-port=9222"
Start-Process "$env:LOCALAPPDATA\Meetily Pro\MeetilyPro.exe"
```
*(Or set `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` permanently in Windows User Environment Variables).*

### Step 2: Start the Live Transcript Service
```powershell
python -m transcript.main
```
- If Meetily Pro is active on port 9222:
  `Meetily Pro detected on port 9222! Using Meetily Pro transcription stream.`
- If Meetily Pro is not running:
  `Meetily Pro not detected on port 9222. Using standalone microphone transcription.`

### Step 3: Verify the Stream
- **Terminal tap**:
  ```powershell
  python -m mocks.tap transcript
  ```
- **Web visualizer**: Open `code/copilot/mocks/live_transcript.html` in any browser.
- **REST history**: `GET http://127.0.0.1:8771/transcript/history`
