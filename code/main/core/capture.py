"""Step (d): record one window with Windows Graphics Capture into a steady 1 fps video.

WGC only delivers a frame when the window changes, so a writer thread writes the *latest*
frame every 1/fps seconds. Video time therefore equals wall-clock time since `started_at`,
which is what lines screenshots up with Meetily's `audio_start_time`.
The video is then fed to the step (a) extractor unchanged.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

MAX_WIDTH = 1920          # downscale very large windows; text stays readable for OCR


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CaptureError(Exception):
    pass


def _fit(frame: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Letterbox `frame` into `size` (w, h) so a resized window doesn't break the video."""
    w, h = size
    fh, fw = frame.shape[:2]
    if (fw, fh) == (w, h):
        return frame
    s = min(w / fw, h / fh)
    nw, nh = max(1, int(fw * s)), max(1, int(fh * s))
    out = np.zeros((h, w, 3), dtype=np.uint8)
    x, y = (w - nw) // 2, (h - nh) // 2
    out[y:y + nh, x:x + nw] = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
    return out


class WindowRecorder:
    def __init__(self, hwnd: int, out_dir: Path, fps: float = 1.0, title: str = "",
                 process: str = ""):
        self.hwnd, self.fps = int(hwnd), float(fps)
        self.title, self.process = title, process
        self.out_dir = Path(out_dir)
        self.video = self.out_dir / "capture.mp4"
        self._latest: np.ndarray | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._first = threading.Event()
        self._control = None
        self._writer_thread: threading.Thread | None = None
        self.started_at: str | None = None     # wall-clock time of video frame 0 (UTC)
        self.ended_at: str | None = None
        self.frames = 0
        self.size: tuple[int, int] | None = None
        self.error: str | None = None
        self.window_closed = False

    # ---- WGC callbacks
    def _on_frame(self, frame, control):
        if self._stop.is_set():
            control.stop()
            return
        img = np.ascontiguousarray(frame.frame_buffer[:, :, :3])   # copy out of the mapped buffer
        with self._lock:
            self._latest = img
        self._first.set()

    def _on_closed(self):
        self.window_closed = True
        self._stop.set()

    # ---- writer
    def _write_loop(self):
        if not self._first.wait(timeout=8):
            self.error = ("no frames from the window within 8 s (minimized, closed, or "
                          "capture not allowed)")
            self._stop.set()
            return
        with self._lock:
            first = self._latest
        fh, fw = first.shape[:2]
        if fw > MAX_WIDTH:
            fw, fh = MAX_WIDTH, int(fh * MAX_WIDTH / fw)
        fw, fh = fw - fw % 2, fh - fh % 2                              # even dims for codecs
        self.size = (fw, fh)
        vw = cv2.VideoWriter(str(self.video), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (fw, fh))
        if not vw.isOpened():
            self.error = "couldn't open the video writer"
            self._stop.set()
            return
        period = 1.0 / self.fps
        t0 = time.monotonic()
        self.started_at = utc_now_iso()
        try:
            while True:
                with self._lock:
                    img = self._latest
                vw.write(_fit(img, (fw, fh)))
                self.frames += 1
                # sleep until the next tick; stop promptly when asked
                next_t = t0 + self.frames * period
                if self._stop.wait(max(0.0, next_t - time.monotonic())):
                    break
        finally:
            vw.release()
            self.ended_at = utc_now_iso()

    # ---- control
    def start(self):
        from windows_capture import WindowsCapture
        from .windows import is_window
        if not is_window(self.hwnd):
            raise CaptureError("that window no longer exists; refresh the list")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=self.hwnd,
                             minimum_update_interval=int(500 / self.fps))
        cap.frame_handler = self._on_frame
        cap.closed_handler = self._on_closed
        try:
            self._control = cap.start_free_threaded()
        except Exception as e:  # noqa: BLE001 - native errors surface as plain exceptions
            raise CaptureError(f"Windows Graphics Capture failed to start: {e}") from e
        self._writer_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._writer_thread.start()

    @property
    def running(self) -> bool:
        return bool(self._writer_thread and self._writer_thread.is_alive())

    def latest_jpeg(self, max_w: int = 640) -> bytes | None:
        with self._lock:
            img = self._latest
        if img is None:
            return None
        h, w = img.shape[:2]
        if w > max_w:
            img = cv2.resize(img, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return jpg.tobytes() if ok else None

    def stop(self) -> dict:
        self._stop.set()
        if self._writer_thread:
            self._writer_thread.join(timeout=10)
        if self._control is not None:
            try:
                self._control.stop()
            except Exception:  # noqa: BLE001 - already stopped (e.g. window closed)
                pass
        info = self.info()
        (self.out_dir / "capture.json").write_text(json.dumps(info, indent=2), "utf-8")
        return info

    def info(self) -> dict:
        return {"hwnd": self.hwnd, "title": self.title, "process": self.process,
                "fps": self.fps, "started_at": self.started_at, "ended_at": self.ended_at,
                "frames": self.frames, "seconds": round(self.frames / self.fps, 1),
                "size": self.size, "error": self.error, "window_closed": self.window_closed,
                "video": self.video.name, "running": self.running}
