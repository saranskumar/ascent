"""New run: capture a window by hand, or process a screen-recording video, outside of Meetily's
automation. Link the result to a Meetily meeting on the Meetings tab to summarise it."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFileDialog, QLabel, QWidget

from ui.common import (button, card, chip, error_box, hbox, label, mmss, page_header,
                       pixmap_from_jpeg, run_async, set_chip, vbox)
from ui.picker import WindowPicker

VIDEO_FILTER = "Videos (*.mp4 *.mkv *.mov *.avi *.webm *.m4v);;All files (*)"


class NewRunPage(QWidget):
    def __init__(self, ctl, bridge):
        super().__init__()
        self.setObjectName("page")
        self.ctl = ctl

        # ---- watch a window
        self.picker = WindowPicker()
        self.start_btn = button("Start capture", "primary", self.start_capture)
        self.picker.selection_changed.connect(lambda h: self._update())
        self.cap_chip = chip("Not capturing")
        self.cap_text = label("", "small", wrap=True)
        self.stop_btn = button("Stop and process", "primary", self.stop_capture)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(160)
        self.running_box = QWidget()
        self.running_box.setLayout(vbox(hbox(self.cap_chip, self.cap_text, None, self.stop_btn),
                                        self.preview))
        watch = card(vbox(hbox(label("Watch a window", "h2"), None, self.start_btn),
                          label("Records the chosen window at 1 frame per second (even when it's "
                                "covered), then extracts the distinct screens. The capture is "
                                "processed on the Live tab; link it to a Meetily meeting on the "
                                "Meetings tab to get a summary.", "small", wrap=True),
                          self.running_box, self.picker, spacing=8))

        # ---- upload
        self.file_lbl = label("No file chosen", "small")
        upload = card(vbox(hbox(label("Process a video", "h2"), None,
                                button("Choose video…", "primary", self.choose_video)),
                           label("A screen recording of a meeting (any length). It's copied into "
                                 "this app's data folder, screens are extracted and diagrams "
                                 "described, all on this computer.", "small", wrap=True),
                           self.file_lbl, spacing=8))

        self.setLayout(vbox(page_header("New run", "Capture or process something by hand. Meetily "
                                        "recordings are handled automatically; you don't need this "
                                        "page for them."),
                            watch, upload, spacing=14, margins=(22, 18, 22, 18)))
        self.layout().setStretch(1, 1)
        bridge.capture.connect(self._update)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self._update()

    def showEvent(self, e):
        super().showEvent(e)
        if self.ctl.recorder is None:
            self.picker.refresh(self.ctl.settings["watch"] or {})
        self._update()

    def _update(self) -> None:
        capturing = self.ctl.recorder is not None
        self.running_box.setVisible(capturing)
        self.picker.setVisible(not capturing)
        self.start_btn.setVisible(not capturing)
        self.start_btn.setEnabled(self.picker.selected_hwnd() is not None)
        if capturing:
            self._tick()

    def _tick(self) -> None:
        info = self.ctl.capture_info()
        if not info or not self.isVisible():
            return
        set_chip(self.cap_chip, "Capturing", "info")
        self.cap_text.setText(f"{info['title'][:70]} · {mmss(info['frames'] / max(info['fps'], 0.001))}"
                              + (" · for a Meetily recording" if self.ctl.store.meta(info["run"]).get("meeting_id") else ""))

        def got(data):
            pm = pixmap_from_jpeg(data)
            if pm is not None:
                self.preview.setPixmap(pm.scaledToHeight(min(240, pm.height()),
                                                         Qt.TransformationMode.SmoothTransformation))
        run_async(lambda: self.ctl.capture_preview(560), got)

    def start_capture(self) -> None:
        hwnd = self.picker.selected_hwnd()
        if not hwnd:
            return
        self.start_btn.setEnabled(False)
        run_async(lambda: self.ctl.start_capture(hwnd), lambda _r: self._update(),
                  lambda e: (error_box(self, "Start capture", e), self._update()))

    def stop_capture(self) -> None:
        self.stop_btn.setEnabled(False)

        def done(res):
            self.stop_btn.setEnabled(True)
            self._update()
            self.window().show_live(res.get("job"))

        def failed(e):
            self.stop_btn.setEnabled(True)
            error_box(self, "Stop capture", e)
        run_async(lambda: self.ctl.stop_capture(reason="stopped on the New run page"), done, failed)

    def choose_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a screen recording", "", VIDEO_FILTER)
        if not path:
            return
        self.file_lbl.setText(f"Copying {Path(path).name}…")

        def done(job):
            self.file_lbl.setText(f"Queued {Path(path).name}")
            self.window().show_live(job["id"])
        run_async(lambda: self.ctl.add_video(Path(path)), done,
                  lambda e: (self.file_lbl.setText("No file chosen"), error_box(self, "Process a video", e)))
