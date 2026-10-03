"""Choose a window to capture: thumbnails of the open windows, the last one preselected.

Used by the "Recording started" panel (Meetily webhook) and by New run > Watch a window.
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QListView, QListWidget, QListWidgetItem, QWidget

from core import windows
from ui.common import button, hbox, label, pixmap_from_jpeg, run_async, vbox

THUMB = QSize(240, 140)


class WindowPicker(QWidget):
    selection_changed = pyqtSignal(object)       # hwnd or None
    activated = pyqtSignal(int)                  # double-click

    def __init__(self, show_refresh: bool = True):
        super().__init__()
        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setIconSize(THUMB)
        self.list.setGridSize(QSize(THUMB.width() + 24, THUMB.height() + 54))
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        self.list.setSpacing(4)
        self.list.currentItemChanged.connect(self._changed)
        self.list.itemDoubleClicked.connect(lambda it: self.activated.emit(it.data(Qt.ItemDataRole.UserRole)))
        self.warning = label("", wrap=True)
        self.warning.setProperty("role", "muted")
        self.warning.setVisible(False)
        self.status = label("", "small")
        refresh = button("Refresh", "ghost", self.refresh)
        refresh.setVisible(show_refresh)
        self.setLayout(vbox(hbox(self.status, None, refresh), self.list, self.warning, spacing=6))
        self.watch: dict = {}
        self._gen = 0
        self._check_gen = 0

    def selected_hwnd(self) -> int | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def selected_info(self) -> dict | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole + 1) if it else None

    def refresh(self, watch: dict | None = None) -> None:
        if watch is not None:
            self.watch = watch or {}
        self._gen += 1
        gen = self._gen
        self.status.setText("Looking for windows…")

        def work():
            wins = windows.list_windows()
            thumbs = {w["hwnd"]: windows.thumbnail_jpeg(w["hwnd"], 360) for w in wins}
            return wins, thumbs
        run_async(work, lambda res: self._fill(gen, *res),
                  lambda e: self.status.setText(f"Couldn't list windows: {e}"))

    def _fill(self, gen: int, wins: list[dict], thumbs: dict) -> None:
        if gen != self._gen:
            return
        prev = self.selected_hwnd()
        self.list.clear()
        blank = QPixmap(THUMB)
        blank.fill(Qt.GlobalColor.transparent)
        for w in wins:
            pm = pixmap_from_jpeg(thumbs.get(w["hwnd"]))
            if pm is not None:
                pm = pm.scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            title = w["title"] if len(w["title"]) < 60 else w["title"][:58] + "…"
            it = QListWidgetItem(QIcon(pm or blank), f"{title}\n{w['process']}"
                                 + (" · minimized" if w["minimized"] else ""))
            it.setToolTip(f"{w['title']}\n{w['process']} · {w['width']}×{w['height']}")
            it.setData(Qt.ItemDataRole.UserRole, w["hwnd"])
            it.setData(Qt.ItemDataRole.UserRole + 1, w)
            self.list.addItem(it)
        idx = next((i for i, w in enumerate(wins) if w["hwnd"] == prev), None)
        if idx is None:
            idx = windows.preselect(wins, self.watch.get("title", ""), self.watch.get("process", ""))
        if idx is not None:
            self.list.setCurrentRow(idx)
        self.status.setText(f"{len(wins)} windows" + (" · last used is selected" if idx is not None and
                                                      prev is None and self.watch else ""))
        if not wins:
            self.status.setText("No capturable windows found.")
        self._changed(self.list.currentItem())

    def _changed(self, item, _prev=None) -> None:
        hwnd = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.selection_changed.emit(hwnd)
        self.warning.setVisible(False)
        if hwnd is None:
            return
        self._check_gen += 1
        gen = self._check_gen

        def show(msg):
            if gen == self._check_gen and msg:
                self.warning.setText("⚠ " + msg)
                self.warning.setVisible(True)
        run_async(lambda: windows.presenting_warning(hwnd), show, lambda e: None)
