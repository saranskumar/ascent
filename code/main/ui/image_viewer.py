"""Full-size screen viewer: one screenshot at a time, ←/→ to step, with its time, OCR text and
description next to it."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QScrollArea, QSizePolicy, QSplitter, QWidget

from ui.common import button, hbox, label, mmss, open_path, vbox

KIND = {"slide": "Slide", "diagram": "Diagram", "picture": "Picture (left out of the summary)"}


class ImageViewer(QDialog):
    def __init__(self, parent, root: Path, shots: list[dict], index: int = 0, offset: float = 0.0):
        super().__init__(parent)
        self.root, self.shots, self.offset = Path(root), shots, offset
        self.i = max(0, min(index, len(shots) - 1))
        self.setWindowTitle("Screens")
        self.resize(1200, 760)

        self.img = QLabel()
        self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.img.setMinimumSize(200, 150)
        self.pix: QPixmap | None = None
        self.fit = True
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.img)
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.pos = label("", "muted")
        self.info = label("", "h2", wrap=True)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.b_prev = button("← Previous", on_click=lambda: self.go(-1))
        self.b_next = button("Next →", on_click=lambda: self.go(1))
        self.b_fit = button("Actual size", "ghost", self.toggle_fit)
        side = QWidget()
        side.setLayout(vbox(self.info, self.text, hbox(button("Open file", "ghost", self.open_file),
                                                        None), margins=(8, 0, 0, 0)))
        split = QSplitter()
        split.addWidget(self.scroll)
        split.addWidget(side)
        split.setSizes([880, 320])
        self.setLayout(vbox(split, hbox(self.b_prev, self.b_next, self.pos, None, self.b_fit,
                                        button("Close", on_click=self.accept)),
                            spacing=10, margins=(14, 14, 14, 14)))
        for key, d in ((Qt.Key.Key_Left, -1), (Qt.Key.Key_Right, 1), (Qt.Key.Key_PageUp, -1),
                       (Qt.Key.Key_PageDown, 1)):
            QShortcut(QKeySequence(key), self, activated=lambda d=d: self.go(d))
        self.show_current()

    def go(self, d: int) -> None:
        if self.shots:
            self.i = (self.i + d) % len(self.shots)
            self.show_current()

    def show_current(self) -> None:
        if not self.shots:
            self.info.setText("No screens")
            return
        s = self.shots[self.i]
        self.pix = QPixmap(str(self.root / s["image"]))
        self.pos.setText(f"{self.i + 1} / {len(self.shots)}")
        self.info.setText(f"#{s['id']} · {mmss(s['start'] + self.offset)}–{mmss(s['end'] + self.offset)}"
                          f" · {KIND.get(s.get('type'), s.get('type'))}")
        parts = []
        if s.get("description"):
            parts.append(f"Description ({s.get('described_by') or 'vision model'}):\n{s['description']}")
        parts.append("Text on screen (OCR):\n" + (s.get("text") or "(none)"))
        if s.get("merged_from", 1) > 1:
            parts.append(f"Merged from {s['merged_from']} captures (e.g. a slide revealed bullet by bullet).")
        self.text.setPlainText("\n\n".join(parts))
        self._render()

    def toggle_fit(self) -> None:
        self.fit = not self.fit
        self.b_fit.setText("Actual size" if self.fit else "Fit to window")
        self.scroll.setWidgetResizable(self.fit)
        self._render()

    def _render(self) -> None:
        if self.pix is None or self.pix.isNull():
            self.img.setText("Image not found (deleted after write-back?)")
            return
        if self.fit:
            vp = self.scroll.viewport().size()
            self.img.setPixmap(self.pix.scaled(vp, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
        else:
            self.img.setPixmap(self.pix)
            self.img.adjustSize()

    def showEvent(self, e):
        super().showEvent(e)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._render)          # sizes are known only once shown

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit:
            self._render()

    def open_file(self) -> None:
        if self.shots:
            open_path(self.root / self.shots[self.i]["image"])
