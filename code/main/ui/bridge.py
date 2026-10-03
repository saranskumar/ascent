"""Core -> UI: the controller calls these hooks from worker threads; they become Qt signals,
delivered on the UI thread."""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from core.controller import UiHooks


class Bridge(QObject, UiHooks):
    pick_requested = pyqtSignal(object)        # PickRequest
    pick_finished = pyqtSignal(object)
    show_live_requested = pyqtSignal(object)   # job id or None
    notified = pyqtSignal(str, str, str)       # title, text, level
    automation = pyqtSignal()
    capture = pyqtSignal()
    job_changed = pyqtSignal(str)              # job id
    job_added = pyqtSignal(str)
    job_removed = pyqtSignal(str)
    job_log = pyqtSignal(str, str)             # job id, line
    job_token = pyqtSignal(str, str)           # job id, piece
    job_finished = pyqtSignal(str, str)        # job id, status
    queue_state = pyqtSignal(bool)             # paused

    # ---- UiHooks
    def pick_window(self, request):
        self.pick_requested.emit(request)

    def pick_closed(self, request):
        self.pick_finished.emit(request)

    def show_live(self, job_id):
        self.show_live_requested.emit(job_id)

    def notify(self, title, text, level="info"):
        self.notified.emit(title, text, level)

    def automation_changed(self):
        self.automation.emit()

    def capture_changed(self):
        self.capture.emit()

    # ---- job queue listener
    def on_queue(self, kind: str, jid: str, payload=None):
        if kind == "changed":
            self.job_changed.emit(jid)
        elif kind == "added":
            self.job_added.emit(jid)
        elif kind == "removed":
            self.job_removed.emit(jid)
        elif kind == "log":
            self.job_log.emit(jid, payload)
        elif kind == "token":
            self.job_token.emit(jid, payload)
        elif kind == "finished":
            self.job_finished.emit(jid, payload or "")
        elif kind == "queue":
            self.queue_state.emit(bool((payload or {}).get("paused")))
