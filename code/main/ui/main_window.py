"""The main window (sidebar + pages) and the tray icon. Closing the window keeps the app running
in the tray, listening for Meetily; Quit is in the tray menu."""
from __future__ import annotations

import sys

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow,
                             QMenu, QStackedWidget, QSystemTrayIcon, QWidget)

from ui import theme
from ui.common import label, vbox
from ui.pages.live import LivePage
from ui.pages.meetings import MeetingsPage
from ui.pages.new_run import NewRunPage
from ui.pages.overview import OverviewPage
from ui.pages.pick import PickPage
from ui.pages.settings import SettingsPage
from ui.pages.vlm import VlmPage

NAV = [("overview", "Overview"), ("live", "Live"), ("meetings", "Meetings"),
       ("new", "New run"), ("vlm", "VLM test"), ("settings", "Settings")]


def make_icon(recording: bool = False, busy: bool = False) -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(size * 0.06, size * 0.06, size * 0.88, size * 0.88)
        p.setBrush(QColor("#171717"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, size * 0.22, size * 0.22)
        # a "screen" with two text lines
        s = QRectF(size * 0.22, size * 0.27, size * 0.56, size * 0.38)
        p.setPen(QColor("#fafafa"))
        pen = p.pen()
        pen.setWidthF(max(1.0, size * 0.06))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(s, size * 0.05, size * 0.05)
        p.drawLine(int(size * 0.36), int(size * 0.78), int(size * 0.64), int(size * 0.78))
        if recording or busy:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#ef4444" if recording else "#3b82f6"))
            d = size * 0.34
            p.drawEllipse(QRectF(size - d - size * 0.02, size * 0.02, d, d))
        p.end()
        icon.addPixmap(pm)
    return icon


class MainWindow(QMainWindow):
    def __init__(self, ctl, bridge, app: QApplication):
        super().__init__()
        self.ctl, self.bridge, self.app = ctl, bridge, app
        self.quitting = False
        self.told_tray = False
        self.setWindowTitle("Meeting Summaries")
        self.setWindowIcon(make_icon())
        self.resize(1240, 820)
        self.setMinimumSize(1150, 640)

        # ---- pages
        self.pages = {
            "overview": OverviewPage(ctl, bridge), "live": LivePage(ctl, bridge),
            "meetings": MeetingsPage(ctl, bridge), "new": NewRunPage(ctl, bridge),
            "vlm": VlmPage(ctl), "settings": SettingsPage(ctl), "pick": PickPage(ctl),
        }
        self.stack = QStackedWidget()
        for p in self.pages.values():
            self.stack.addWidget(p)

        # ---- sidebar
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        for key, text in NAV:
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(it)
        self.nav.currentItemChanged.connect(self._nav_changed)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setFixedHeight(len(NAV) * 36 + 8)
        self.side_status = label("", "small", wrap=True)
        self.side_capture = label("", "small", wrap=True)
        brand = label("Meeting Summaries", "brand")
        sub = label("Meetily + screens · offline", "small")
        sub.setContentsMargins(8, 0, 0, 0)
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(210)
        side.setLayout(vbox(brand, sub, 10, self.nav, None, self.side_capture, self.side_status,
                            spacing=4, margins=(8, 14, 8, 14)))
        self.side_capture.setContentsMargins(8, 0, 8, 0)
        self.side_status.setContentsMargins(8, 0, 8, 0)
        root = QWidget()
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(side)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        # ---- tray
        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip("Meeting Summaries")
        menu = QMenu()
        menu.addAction("Open", lambda: self.go("overview", show=True))
        menu.addAction("Live", lambda: self.go("live", show=True))
        self.pause_act = QAction("Pause queue", menu)
        self.pause_act.triggered.connect(lambda: ctl.queue.set_paused(not ctl.queue.paused))
        menu.addAction(self.pause_act)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.messageClicked.connect(lambda: self.show_window())
        self.tray.show()
        self._tray_menu = menu

        # ---- wiring
        self.pages["live"].open_run.connect(self.open_run)
        self.pages["live"].open_run_tab.connect(lambda r, t: self.open_run(r, t))
        self.pages["overview"].open_run.connect(self.open_run)
        self.pages["pick"].finished.connect(lambda: self.go("live"))
        self.pages["settings"].theme_changed.connect(lambda t: theme.apply(self.app, t))
        bridge.pick_requested.connect(self._on_pick)
        bridge.pick_finished.connect(self._on_pick_closed)
        bridge.show_live_requested.connect(self.show_live)
        bridge.notified.connect(self._notify)
        bridge.job_finished.connect(self._job_finished)
        bridge.capture.connect(self._update_status)
        bridge.job_changed.connect(lambda _j: self._schedule_status())
        bridge.queue_state.connect(lambda _p: self._update_status())
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._update_status)
        try:
            app.styleHints().colorSchemeChanged.connect(
                lambda _s: theme.apply(app, ctl.settings["theme"]) if ctl.settings["theme"] == "system" else None)
        except AttributeError:
            pass
        self.nav.setCurrentRow(0)
        self._update_status()
        if ctl.pick is not None and ctl.pick.open:          # a recording started before the UI was up
            self._on_pick(ctl.pick)

    # ---------------------------------------------------------------- navigation
    def _nav_changed(self, item, _prev=None) -> None:
        if item is not None:
            self.stack.setCurrentWidget(self.pages[item.data(Qt.ItemDataRole.UserRole)])

    def _nav_row(self, key: str) -> int:
        for r in range(self.nav.count()):
            if self.nav.item(r).data(Qt.ItemDataRole.UserRole) == key:
                return r
        return -1

    def _set_pick_entry(self, on: bool) -> None:
        """While a recording waits for a window, the picker has its own sidebar entry. (Without
        one, the sidebar had no current item, and when the window got focus Qt selected its first
        item, so the window opened on Overview instead of the question.)"""
        row = self._nav_row("pick")
        if on and row < 0:
            it = QListWidgetItem("●  Pick a window")
            it.setData(Qt.ItemDataRole.UserRole, "pick")
            it.setForeground(QColor(theme.current.get("bad", "#dc2626")))
            self.nav.insertItem(0, it)
        elif not on and row >= 0:
            self.nav.blockSignals(True)
            self.nav.takeItem(row)
            self.nav.blockSignals(False)
        self.nav.setFixedHeight(self.nav.count() * 36 + 8)

    def go(self, key: str, show: bool = False) -> None:
        if key == "pick":
            self._set_pick_entry(True)
        row = self._nav_row(key)
        if self.nav.currentRow() == row:
            self.stack.setCurrentWidget(self.pages[key])
        self.nav.setCurrentRow(row)
        if show:
            self.show_window()

    def show_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def show_live(self, job_id=None) -> None:
        self.go("live", show=True)
        if job_id:
            self.pages["live"].select_job(job_id)

    def open_run(self, run_id: str, tab: str | None = None) -> None:
        self.go("meetings", show=True)
        if tab == "edit":
            self.pages["meetings"].select_run(run_id, edit=True)
        else:
            self.pages["meetings"].select_run(run_id, tab)

    # ---------------------------------------------------------------- recording started
    def _on_pick(self, req) -> None:
        self.pages["pick"].show_request(req)
        self._set_pick_entry(True)                # reachable from the sidebar even when hidden
        if self.ctl.settings["show_on_start"] or self.isVisible():
            self.go("pick", show=self.ctl.settings["show_on_start"])
            if self.ctl.settings["show_on_start"]:
                self._pin_on_top(True)            # the question must not hide behind the meeting
                self.force_front()
        self._notify("Recording started", "Pick the window to capture (or skip).", "decision")

    def _on_pick_closed(self, req) -> None:
        self.pages["pick"].closed(req)
        if self.pages["pick"].req is not None:      # a newer question is still open
            return
        if self.stack.currentWidget() is self.pages["pick"]:
            self.go("live")
        self._set_pick_entry(False)
        self._pin_on_top(False)

    def _pin_on_top(self, on: bool) -> None:
        flags = self.windowFlags()
        want = (flags | Qt.WindowType.WindowStaysOnTopHint) if on else \
            (flags & ~Qt.WindowType.WindowStaysOnTopHint)
        if want != flags:
            visible = self.isVisible()
            self.setWindowFlags(want)            # re-creates the native window: show it again
            if visible or on:
                self.show()

    def force_front(self) -> None:
        """Windows doesn't let a background app take the foreground (the window only flashes in
        the taskbar). A synthetic Alt press makes this process the last input source, which
        lets SetForegroundWindow through."""
        self.show_window()
        if not sys.platform.startswith("win"):
            return
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = int(self.winId())
        try:
            user32.keybd_event(0x12, 0, 0, 0)        # Alt down
            user32.keybd_event(0x12, 0, 2, 0)        # Alt up
            user32.ShowWindow(hwnd, 9)               # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
        except (AttributeError, OSError):
            pass

    # ---------------------------------------------------------------- status / notifications
    def _schedule_status(self) -> None:
        if not self._status_timer.isActive():
            self._status_timer.start(500)

    def _update_status(self) -> None:
        q = self.ctl.queue
        c = q.counts()
        rec = self.ctl.capture_info()
        self.side_capture.setText(f"● Capturing {rec['title'][:40]}" if rec else "")
        bits = []
        if c["running"]:
            bits.append(f"{c['running']} running")
        if c["queued"]:
            bits.append(f"{c['queued']} queued")
        if c["waiting"]:
            bits.append(f"{c['waiting']} need you")
        if q.paused:
            bits.append("queue paused")
        self.side_status.setText(" · ".join(bits))
        live = self.nav.item(self._nav_row("live"))
        n = c["running"] + c["queued"] + c["capturing"] + c["waiting"]
        live.setText(f"Live  ({n})" if n else "Live")
        self.tray.setIcon(make_icon(recording=bool(rec), busy=bool(c["running"])))
        self.tray.setToolTip("Meeting Summaries" + (f"\n{', '.join(bits)}" if bits else "")
                             + (f"\nCapturing {rec['title'][:50]}" if rec else ""))
        self.pause_act.setText("Resume queue" if q.paused else "Pause queue")

    def _notify(self, title: str, text: str, level: str = "info") -> None:
        if not self.ctl.settings["notify"]:
            return
        icon = {"error": QSystemTrayIcon.MessageIcon.Warning,
                "decision": QSystemTrayIcon.MessageIcon.Information}.get(level, QSystemTrayIcon.MessageIcon.Information)
        self.tray.showMessage(title, text, icon, 6000)

    def _job_finished(self, jid: str, status: str) -> None:
        self._update_status()
        job = self.ctl.queue.get(jid)
        if not job:
            return
        name = job.get("title") or job["run"]
        if status == "done":
            made = any(s["name"] == "summarize" and s["status"] == "done" for s in job["stages"])
            if made or job.get("source") == "publish":
                self._notify("Summary ready", name)
        elif status == "failed":
            self._notify("Processing failed", f"{name}: {job.get('error')}", "error")

    # ---------------------------------------------------------------- window / tray
    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
                self.hide()
            else:
                self.show_window()

    def closeEvent(self, e) -> None:
        if self.quitting:
            e.accept()
            return
        e.ignore()
        self.hide()
        if not self.told_tray:
            self.told_tray = True
            self._notify("Still running", "Meeting Summaries keeps listening for Meetily in the tray. "
                         "Right-click the tray icon to quit.")

    def quit(self) -> None:
        rec = self.ctl.capture_info()
        busy = self.ctl.queue.counts()["running"]
        if rec or busy:
            from ui.common import confirm
            what = "a window is being captured" if rec else "a job is running"
            if not confirm(self, "Quit", f"Quit now? {what.capitalize()}; it will be finished or "
                           "resumed the next time the app starts."):
                return
        self.quitting = True
        self.tray.hide()
        self.app.quit()
