"""Small shared pieces: run work off the UI thread, post back to it, and a few widget helpers."""
from __future__ import annotations

import os
import threading
import time
import traceback
from datetime import datetime

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)


class _Invoker(QObject):
    call = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @staticmethod
    def _run(fn):
        try:
            fn()
        except RuntimeError as e:          # the widget was closed meanwhile
            if "deleted" not in str(e):
                traceback.print_exc()
        except Exception:  # noqa: BLE001
            traceback.print_exc()


_invoker: _Invoker | None = None


def init_invoker() -> None:
    global _invoker
    _invoker = _Invoker()


def ui_call(fn) -> None:
    """Run fn on the UI thread (safe from any thread)."""
    _invoker.call.emit(fn)


def run_async(fn, on_done=None, on_error=None) -> None:
    """fn() on a thread; on_done(result) / on_error(exc) back on the UI thread."""
    def work():
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001
            if on_error:
                err = e                     # `e` is cleared when the except block ends
                ui_call(lambda: on_error(err))
            else:
                traceback.print_exc()
            return
        if on_done:
            ui_call(lambda: on_done(res))
    threading.Thread(target=work, daemon=True).start()


# ------------------------------------------------------------------ widgets
def label(text: str = "", role: str | None = None, wrap: bool = False, select: bool = False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    if wrap:
        lb.setWordWrap(True)
    if select:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def chip(text: str, kind: str = "muted") -> QLabel:
    lb = QLabel(text)
    lb.setProperty("chip", kind)
    lb.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return lb


def colored_item(text: str, kind: str = "muted"):
    """A table cell coloured like a chip (cell widgets drift when tables are rebuilt)."""
    from PyQt6.QtGui import QColor, QFont
    from PyQt6.QtWidgets import QTableWidgetItem
    from ui import theme
    it = QTableWidgetItem(text)
    it.setForeground(QColor(theme.current.get(kind if kind != "muted" else "muted", "#737373")))
    f = QFont()
    f.setBold(kind != "muted")
    it.setFont(f)
    return it


def set_chip(lb: QLabel, text: str, kind: str) -> None:
    lb.setText(text)
    if lb.property("chip") != kind:
        lb.setProperty("chip", kind)
        restyle(lb)


def restyle(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def button(text: str, kind: str | None = None, on_click=None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if on_click:
        b.clicked.connect(lambda *_: on_click())
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def card(layout=None, margins=(14, 12, 14, 12)) -> QFrame:
    f = QFrame()
    f.setProperty("card", True)
    lay = layout or QVBoxLayout()
    lay.setContentsMargins(*margins)
    f.setLayout(lay)
    return f


def sep() -> QFrame:
    f = QFrame()
    f.setProperty("sep", True)
    return f


def hbox(*items, spacing=8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        if it is None:
            lay.addStretch(1)
        elif isinstance(it, int):
            lay.addSpacing(it)
        elif isinstance(it, QWidget):
            lay.addWidget(it)
        else:
            lay.addLayout(it)
    return lay


def vbox(*items, spacing=8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        if it is None:
            lay.addStretch(1)
        elif isinstance(it, int):
            lay.addSpacing(it)
        elif isinstance(it, QWidget):
            lay.addWidget(it)
        else:
            lay.addLayout(it)
    return lay


def page_header(title: str, subtitle: str = "", *right) -> QWidget:
    """Title + subtitle on the left, actions top-right; never grows taller than its content."""
    w = QWidget()
    w.setLayout(_header_layout(title, subtitle, *right))
    w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    return w


def _header_layout(title: str, subtitle: str = "", *right) -> QHBoxLayout:
    left = vbox(label(title, "h1"), spacing=2)
    if subtitle:
        left.addWidget(label(subtitle, "muted", wrap=True))
    lay = hbox(left, 24, *right)
    lay.setStretch(0, 1)
    for w in right:                           # text takes the free width; buttons sit top-right
        if isinstance(w, QWidget):
            lay.setAlignment(w, Qt.AlignmentFlag.AlignTop)
    return lay


def pixmap_from_jpeg(data: bytes | None) -> QPixmap | None:
    if not data:
        return None
    pm = QPixmap()
    return pm if pm.loadFromData(data, "JPG") else None


def error_box(parent, title: str, e) -> None:
    QMessageBox.warning(parent, title, str(e))


def confirm(parent, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text) == QMessageBox.StandardButton.Yes


def open_path(path) -> None:
    try:
        os.startfile(str(path))  # noqa: S606 - Windows: open in Explorer / default app
    except OSError:
        pass


# ------------------------------------------------------------------ formatting
def ago(ts: float | None) -> str:
    if not ts:
        return ""
    d = time.time() - ts
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    return datetime.fromtimestamp(ts).strftime("%b %d, %H:%M")


def when(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%b %d, %H:%M") if ts else ""


def mmss(sec: float | None) -> str:
    if sec is None:
        return ""
    s = int(max(0, sec))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def duration(a: float | None, b: float | None = None) -> str:
    if not a:
        return ""
    return mmss((b or time.time()) - a)
