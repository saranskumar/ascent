"""Host-only overlay: suggestion cards, history reel, keys, and local paste/drop."""

from pathlib import Path

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.canvas import Canvas
from gui.images import ImageLoader


class ThumbButton(QToolButton):
    def __init__(self, index: int, on_enter, parent=None):
        super().__init__(parent)
        self._index = index
        self._on_enter = on_enter
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(96, 52)
        self.setIconSize(self.size())

    def enterEvent(self, event) -> None:
        self._on_enter(self._index)
        super().enterEvent(event)


class Card(QFrame):
    def __init__(self, suggestion: dict, loader: ImageLoader, on_choose, on_dismiss, on_highlight):
        super().__init__()
        self.suggestion_id = str(suggestion.get("id") or "")
        self.images: list[dict] = []
        self._on_choose = on_choose
        self._on_dismiss = on_dismiss
        self._on_highlight = on_highlight
        self._loader = loader
        self._hovering = False
        self._expired = False
        self._highlight = -1
        self._thumbs: list[ThumbButton] = []
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 2, 4, 2)
        self._layout.setSpacing(2)
        header = QHBoxLayout()
        self._topic = QLabel()
        self._topic.setWordWrap(False)
        self._topic.setStyleSheet("font-weight: 600;")
        dismiss = QToolButton()
        dismiss.setText("✕")
        dismiss.setToolTip("Dismiss")
        dismiss.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        dismiss.setAutoRaise(True)
        dismiss.clicked.connect(lambda: self._on_dismiss(self.suggestion_id))
        header.addWidget(self._topic, 1)
        header.addWidget(dismiss, 0, Qt.AlignmentFlag.AlignTop)
        self._reason = QLabel()
        self._reason.setWordWrap(False)
        self._reason.setMaximumHeight(16)
        self._reason.setStyleSheet("color: #94a3b8; font-size: 11px;")
        self._thumbs_row = QHBoxLayout()
        self._thumbs_row.addStretch(1)
        self._layout.addLayout(header)
        self._layout.addWidget(self._reason)
        self._layout.addLayout(self._thumbs_row)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_expire)
        self.apply(suggestion)

    def apply(self, suggestion: dict) -> None:
        self.images = [image for image in suggestion.get("images") or [] if isinstance(image, dict) and image.get("url")]
        self._topic.setText(str(suggestion.get("topic") or ""))
        self._reason.setText(str(suggestion.get("reason") or ""))
        self._set_priority(suggestion.get("priority"))
        while self._thumbs_row.count() > 1:
            item = self._thumbs_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._thumbs = []
        for index, image in enumerate(self.images):
            button = ThumbButton(index, self._hover_thumb)
            button.setToolTip(str(image.get("caption") or ""))
            button.clicked.connect(lambda _checked=False, i=index: self._choose(i))
            self._thumbs_row.insertWidget(index, button)
            self._thumbs.append(button)
            thumb_url = image.get("thumb_url") or image.get("url")
            self._loader.fetch(thumb_url, lambda loaded, b=button: self._set_thumb(b, loaded))
        self._paint_highlight()
        self._expired = False
        expires = suggestion.get("expires_in")
        seconds = 45 if expires is None else _as_float(expires)
        self._timer.stop()
        if seconds and seconds > 0:
            self._timer.start(int(seconds * 1000))

    def set_highlight(self, index: int) -> None:
        self._highlight = index
        self._paint_highlight()

    def enterEvent(self, event) -> None:
        self._hovering = True
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            return
        self._hovering = False
        if self._expired:
            self._queue_quiet()

    def _hover_thumb(self, index: int) -> None:
        self._on_highlight(self.suggestion_id, index)

    def _choose(self, index: int) -> None:
        if 0 <= index < len(self.images):
            self._on_choose(self.suggestion_id, self.images[index])

    def _on_expire(self) -> None:
        self._expired = True
        if not self._hovering:
            self._queue_quiet()

    def _queue_quiet(self) -> None:
        suggestion_id = self.suggestion_id
        QTimer.singleShot(0, lambda: self._on_dismiss(suggestion_id, quiet=True))

    def _set_thumb(self, button: ThumbButton, image: QImage) -> None:
        from shiboken6 import isValid
        if isValid(button) and button in self._thumbs:
            button.setIcon(QIcon(QPixmap.fromImage(image)))

    def _paint_highlight(self) -> None:
        for index, button in enumerate(self._thumbs):
            border = "#38bdf8" if index == self._highlight else "transparent"
            button.setStyleSheet(
                f"QToolButton {{ border: 2px solid {border}; border-radius: 4px; background: #0f172a; }}"
            )

    def _set_priority(self, priority) -> None:
        elevated = priority == "elevated"
        border = "1px solid #e0a030" if elevated else "none"
        self.setStyleSheet(
            f"QFrame#card {{ background: #1e293b; border-radius: 8px; border: {border}; color: #e5e7eb; }}"
            "QLabel { color: #e5e7eb; background: transparent; }"
            "QToolButton { color: #94a3b8; background: transparent; border: none; }"
        )
        if elevated:
            glow = QGraphicsDropShadowEffect(self)
            glow.setBlurRadius(18)
            glow.setOffset(0, 0)
            glow.setColor(QColor(224, 160, 48, 180))
            self.setGraphicsEffect(glow)
        else:
            ghost = QGraphicsOpacityEffect(self)
            ghost.setOpacity(0.3)
            self.setGraphicsEffect(ghost)


class Overlay(QMainWindow):
    def __init__(self, canvas: Canvas, client, loader: ImageLoader):
        super().__init__()
        self._canvas = canvas
        self._client = client
        self._loader = loader
        self._cards: dict[str, Card] = {}
        self._order: list[str] = []
        self._highlight = 0
        self._history: list[tuple[QImage, QImage, str]] = []
        self._history_index = -1
        self._reel_buttons: list[QToolButton] = []

        self.setWindowTitle("Co-pilot overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self._full_size = (360, 210)
        self._minimized = False
        self.setFixedSize(*self._full_size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAcceptDrops(True)
        self._drag_origin = None
        self.setStyleSheet(
            "QMainWindow { background: transparent; }"
            "QWidget#panel { background: #0b1220; border-radius: 12px; color: #e5e7eb; }"
            "QScrollArea { border: none; background: transparent; }"
        )

        central = QWidget()
        central.setObjectName("panel")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        top = QHBoxLayout()
        top.addStretch(1)
        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        self._set_dot("off")
        self._min_button = QToolButton()
        self._min_button.setText("–")
        self._min_button.setToolTip("Minimize")
        self._min_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._min_button.setFixedSize(18, 18)
        self._min_button.setStyleSheet(
            "QToolButton { color: #e5e7eb; background: transparent; border: none; font-size: 16px; }"
        )
        self._min_button.clicked.connect(self._toggle_minimized)
        top.addWidget(self._dot)
        top.addWidget(self._min_button)
        root.addLayout(top)

        self._slot_host = QWidget()
        self._slot = QVBoxLayout(self._slot_host)
        self._slot.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._slot_host, 1)

        self._reel_scroll = QScrollArea()
        self._reel_scroll.setWidgetResizable(True)
        self._reel_scroll.setFixedHeight(34)
        self._reel_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._reel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._reel_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._reel = QWidget()
        self._reel_layout = QHBoxLayout(self._reel)
        self._reel_layout.setContentsMargins(0, 2, 0, 2)
        self._reel_layout.addStretch(1)
        reel_opacity = QGraphicsOpacityEffect(self._reel)
        reel_opacity.setOpacity(0.4)
        self._reel.setGraphicsEffect(reel_opacity)
        self._reel_scroll.setWidget(self._reel)
        root.addWidget(self._reel_scroll)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self._bind_keys()
        client.suggestion.connect(self.upsert)
        client.connection_changed.connect(self._on_connection)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        from gui.app import exclude_from_capture
        exclude_from_capture(self)

    def upsert(self, suggestion: dict) -> None:
        suggestion_id = suggestion.get("id")
        if not suggestion_id:
            return
        suggestion_id = str(suggestion_id)
        card = self._cards.get(suggestion_id)
        if card is None:
            card = Card(suggestion, self._loader, self._choose, self._dismiss, self._hover_highlight)
            self._cards[suggestion_id] = card
            self._order.insert(0, suggestion_id)
            card.hide()
            self._highlight = 0
        else:
            card.apply(suggestion)
        self._show_newest()
        self._refresh_highlights()

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_image()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasImage() or mime.hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasImage():
            image = QImage(mime.imageData())
            if not image.isNull():
                self._stage_local(image, "dropped image")
                event.acceptProposedAction()
                return
        for url in mime.urls():
            path = url.toLocalFile()
            if not path:
                continue
            image = QImage(path)
            if not image.isNull():
                self._stage_local(image, Path(path).name)
                event.acceptProposedAction()
                return
        event.ignore()

    def paste_image(self) -> None:
        image = QApplication.clipboard().image()
        if not image.isNull():
            self._stage_local(image, "pasted image")

    def closeEvent(self, event) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        QApplication.quit()
        super().closeEvent(event)

    def _bind_keys(self) -> None:
        bindings = (
            ("1", lambda: self._select_newest(0)),
            ("2", lambda: self._select_newest(1)),
            ("3", lambda: self._select_newest(2)),
            ("Space", lambda: self._select_newest(self._highlight)),
            ("Left", lambda: self._step_history(-1)),
            ("Right", lambda: self._step_history(1)),
            ("B", self._toggle_shutter),
            ("Esc", self._toggle_shutter),
        )
        self._shortcuts = []
        for sequence, slot in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    def _newest(self) -> Card | None:
        if not self._order:
            return None
        return self._cards.get(self._order[0])

    def _refresh_highlights(self) -> None:
        newest = self._newest()
        for card in self._cards.values():
            if card is newest and card.images:
                self._highlight = max(0, min(self._highlight, len(card.images) - 1))
                card.set_highlight(self._highlight)
            else:
                card.set_highlight(-1)

    def _hover_highlight(self, suggestion_id: str, index: int) -> None:
        if self._order and self._order[0] == suggestion_id:
            self._highlight = index
            self._refresh_highlights()

    def _select_newest(self, index: int) -> None:
        card = self._newest()
        if card is None or index < 0 or index >= len(card.images):
            return
        self._highlight = index
        self._refresh_highlights()
        self._choose(card.suggestion_id, card.images[index])

    def _choose(self, suggestion_id: str, image: dict) -> None:
        image_id = image.get("id")
        if image_id:
            self._client.send({"type": "select", "suggestion_id": suggestion_id, "image_id": image_id})
        url = image.get("url")
        caption = str(image.get("caption") or "")

        def show(full: QImage) -> None:
            self._push_history(full, full, caption)
            self._canvas.show_image(full)

        self._loader.fetch(url, show)

    def _dismiss(self, suggestion_id: str, quiet: bool = False) -> None:
        card = self._cards.get(suggestion_id)
        if card is None:
            return
        if not quiet:
            self._client.send({"type": "dismiss", "suggestion_id": suggestion_id})
        was_newest = bool(self._order) and self._order[0] == suggestion_id
        self._slot.removeWidget(card)
        card.hide()
        card.deleteLater()
        self._cards.pop(suggestion_id, None)
        if suggestion_id in self._order:
            self._order.remove(suggestion_id)
        if was_newest:
            self._highlight = 0
        self._show_newest()
        self._refresh_highlights()

    def _push_history(self, full: QImage, thumb: QImage, caption: str) -> None:
        self._history.append((full, thumb, caption))
        self._history_index = len(self._history) - 1
        self._render_reel()

    def _step_history(self, delta: int) -> None:
        if not self._history:
            return
        index = self._history_index + delta
        if index < 0 or index >= len(self._history):
            return
        self._show_history(index)

    def _show_history(self, index: int) -> None:
        self._history_index = index
        full, _thumb, _caption = self._history[index]
        self._canvas.show_image(full)
        self._render_reel()

    def _render_reel(self) -> None:
        while self._reel_layout.count() > 1:
            item = self._reel_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._reel_buttons = []
        for index, (_full, thumb, caption) in enumerate(self._history):
            button = QToolButton()
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedSize(44, 26)
            button.setIconSize(button.size())
            button.setToolTip(caption)
            button.setIcon(QIcon(QPixmap.fromImage(thumb)))
            border = "#e2e8f0" if index == self._history_index else "transparent"
            button.setStyleSheet(
                f"QToolButton {{ border: 2px solid {border}; border-radius: 4px; background: #0f172a; }}"
            )
            button.clicked.connect(lambda _checked=False, i=index: self._show_history(i))
            self._reel_layout.insertWidget(index, button)
            self._reel_buttons.append(button)

    def _stage_local(self, image: QImage, caption: str) -> None:
        self._push_history(image, image, caption)
        self._canvas.show_image(image)

    def _show_newest(self) -> None:
        newest = self._newest()
        while self._slot.count():
            item = self._slot.takeAt(0)
            if item.widget():
                item.widget().hide()
        for card in self._cards.values():
            if card is not newest:
                card.hide()
        if newest is not None:
            self._slot.addWidget(newest)
            newest.show()

    def _toggle_shutter(self) -> None:
        blank = self._canvas.toggle_shutter()
        if blank:
            self._set_dot("blank")
        else:
            self._on_connection(self._client.is_connected())

    def _on_connection(self, connected: bool) -> None:
        if self._canvas.is_blank():
            return
        self._set_dot("on" if connected else "off")

    def _toggle_minimized(self) -> None:
        self._minimized = not self._minimized
        self._slot_host.setVisible(not self._minimized)
        self._reel_scroll.setVisible(not self._minimized)
        if self._minimized:
            self.setFixedSize(120, 28)
            self._min_button.setText("+")
            self._min_button.setToolTip("Restore")
        else:
            self.setFixedSize(*self._full_size)
            self._min_button.setText("–")
            self._min_button.setToolTip("Minimize")

    def _set_dot(self, state: str) -> None:
        color = {"on": "#34d399", "off": "#64748b", "blank": "#fbbf24"}[state]
        self._dot.setStyleSheet(f"background: {color}; border-radius: 4px;")

    def eventFilter(self, obj, event) -> bool:
        if not self._is_descendant(obj):
            return super().eventFilter(obj, event)
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = None if self._on_button(obj) else event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        elif (
            event.type() == QEvent.Type.MouseMove
            and self._drag_origin is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            return True
        elif event.type() == QEvent.Type.MouseButtonRelease:
            self._drag_origin = None
        return super().eventFilter(obj, event)

    def _is_descendant(self, obj) -> bool:
        while obj is not None:
            if obj is self:
                return True
            obj = obj.parent()
        return False

    def _on_button(self, obj) -> bool:
        while obj is not None and obj is not self:
            if isinstance(obj, QToolButton):
                return True
            obj = obj.parent()
        return False


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 45
