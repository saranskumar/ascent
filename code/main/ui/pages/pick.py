"""Shown when a Meetily recording starts: which window should be captured? (Skip = none.)"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

from ui.common import button, card, hbox, label, vbox
from ui.picker import WindowPicker


class PickPage(QWidget):
    finished = pyqtSignal()

    def __init__(self, ctl):
        super().__init__()
        self.setObjectName("page")
        self.ctl = ctl
        self.req = None
        self.title = label("Recording started", "h1")
        self.sub = label("", "muted", wrap=True)
        self.picker = WindowPicker()
        self.start_btn = button("Start capture", "primary", self.start)
        self.skip_btn = button("Skip this recording", on_click=self.skip,
                               tip="No screen capture for this meeting (the audio summary is Meetily's).")
        self.picker.selection_changed.connect(lambda h: self.start_btn.setEnabled(h is not None))
        self.picker.activated.connect(lambda _h: self.start())
        hint = label("Pick the window you're presenting (slides, document, browser tab), not the "
                     "meeting window itself. It's recorded at 1 frame per second even when covered; "
                     "nothing leaves this computer.", "small", wrap=True)
        self.setLayout(vbox(self.title, self.sub,
                            card(vbox(self.picker, hint, spacing=8)),
                            hbox(None, self.skip_btn, self.start_btn),
                            spacing=12, margins=(22, 18, 22, 18)))

    def show_request(self, req) -> None:
        self.req = req
        name = req.title or req.meeting_id or "this meeting"
        timeout = int(self.ctl.settings["pick_timeout"] or 0)
        w = req.watch or {}
        auto = (f" If you don't answer within {timeout} s, “{w.get('title', '')[:60]}” "
                f"(used last time) is captured." if timeout and w else "")
        self.sub.setText(f"Meetily is recording “{name}”. Which window should be captured so the "
                         f"summary knows what was on screen?{auto}")
        self.picker.refresh(req.watch)

    def start(self) -> None:
        hwnd = self.picker.selected_hwnd()
        if self.req is not None and hwnd:
            self.req.choose(hwnd)
            self.finished.emit()

    def skip(self) -> None:
        if self.req is not None:
            self.req.skip()
        self.finished.emit()

    def closed(self, req) -> None:
        """The request ended elsewhere (recording stopped, or answered)."""
        if req is self.req:
            self.req = None
