# Part 2: Image engine (Shreevardhan)

Read segments, decide when a visual would help, find 2–3 images, and publish `suggestion` messages on `ws://127.0.0.1:8772/suggestions`. Image files go in `.cache/images` and are served at `http://127.0.0.1:8772/images/`.

`main.py` already has the wiring: subscribe, rolling window, check loop, topic cooldown, and select/dismiss handling. The work left is `detect()` and `retrieve()` (and `on_select()` for ranking).

- Develop against the mock: `python -m mocks.mock_transcript --loop`, or type lines yourself with `python -m transcript.main`.
- Check your output: `python -m mocks.tap suggestions --auto-select` (it acts as a fake GUI).
- Target for the sample talk: `contracts/samples/suggestions.jsonl`.
