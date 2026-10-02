"""A real on-screen window that plays the synthetic slide timeline (tests/synthetic.py) in real
time, for testing live capture end to end:

  python -m tests.slide_window [--lead 4] [--clock data/slide_clock.json]

Shows a "get ready" frame for --lead seconds (so capture can start), then slide t=0..65 s,
and writes the wall-clock UTC time of slide t=0 to --clock so the test can align times.
"""
from __future__ import annotations

import argparse
import json
import time
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path

import cv2
from PIL import Image, ImageTk

from tests import synthetic

TITLE = "VCS Slide Demo"
W, H = 960, 540


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lead", type=float, default=4.0)
    ap.add_argument("--clock", type=Path, default=Path("data/slide_clock.json"))
    a = ap.parse_args()

    frames = []
    for t in range(synthetic.DURATION):
        rgb = cv2.cvtColor(synthetic.frame_at(t), cv2.COLOR_BGR2RGB)
        frames.append(Image.fromarray(cv2.resize(rgb, (W, H), interpolation=cv2.INTER_AREA)))
    ready = Image.new("RGB", (W, H), (240, 240, 240))

    root = tk.Tk()
    root.title(TITLE)
    root.geometry(f"{W}x{H}+80+80")
    root.resizable(False, False)
    label = tk.Label(root, bd=0)
    label.pack()
    photo = {"img": ImageTk.PhotoImage(ready)}
    label.configure(image=photo["img"])
    state = {"t0": None}

    def tick():
        now = time.monotonic()
        if state["t0"] is None:
            if now - start >= a.lead:
                state["t0"] = now
                a.clock.parent.mkdir(parents=True, exist_ok=True)
                a.clock.write_text(json.dumps({"slide_t0": datetime.now(timezone.utc).isoformat()}))
            root.after(50, tick)
            return
        t = int(now - state["t0"])
        if t >= len(frames):
            root.destroy()
            return
        photo["img"] = ImageTk.PhotoImage(frames[t])
        label.configure(image=photo["img"])
        root.after(100, tick)

    start = time.monotonic()
    root.after(50, tick)
    root.mainloop()


if __name__ == "__main__":
    main()
