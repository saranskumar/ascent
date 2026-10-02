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
        self.setFixedSize(104, 58)
        self.setIconSize(self.size())
        self.setCursor(Qt.CursorShape.PointingHandCursor)

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

        self._elevated = False
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 6, 8, 8)
        self._layout.setSpacing(4)
        header = QHBoxLayout()
        self._topic = QLabel()
        self._topic.setWordWrap(False)
        self._topic.setStyleSheet("font-size: 13px; font-weight: 600; color: #f8fafc;")
        dismiss = QToolButton()
        dismiss.setObjectName("dismiss")
        dismiss.setText("✕")
        dismiss.setToolTip("Dismiss")
        dismiss.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        dismiss.setFixedSize(18, 18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.clicked.connect(lambda: self._on_dismiss(self.suggestion_id))
        header.addWidget(self._topic, 1)
        header.addWidget(dismiss, 0, Qt.AlignmentFlag.AlignTop)
        self._reason = QLabel()
        self._reason.setWordWrap(False)
        self._reason.setMaximumHeight(16)
        self._reason.setStyleSheet("color: #94a3b8; font-size: 11px; background: transparent;")
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
        accent = "#fbbf24" if self._elevated else "#7dd3fc"
        for index, button in enumerate(self._thumbs):
            border = accent if index == self._highlight else "#1e293b"
            button.setStyleSheet(
                "QToolButton {"
                f"border: 2px solid {border}; border-radius: 8px; background: #0b1220; padding: 0;"
                "}"
                "QToolButton:hover { border-color: #e2e8f0; }"
            )

    def _set_priority(self, priority) -> None:
        self._elevated = priority == "elevated"
        if self._elevated:
            frame = "background: #172033; border: 1px solid #e0a030;"
        else:
            frame = "background: #141c2e; border: 1px solid #243049;"
        self.setStyleSheet(
            f"QFrame#card {{ {frame} border-radius: 12px; }}"
            "QLabel { background: transparent; }"
            "QToolButton#dismiss { color: #94a3b8; background: transparent; border: none; border-radius: 9px; font-size: 11px; }"
            "QToolButton#dismiss:hover { color: #f8fafc; background: #243049; }"
        )
        if self._elevated:
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
        self._full_size = (380, 236)
        self._pill_size = (156, 36)
        self._minimized = False
        self._chrome_open = True
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(150)
        self._collapse_timer.timeout.connect(self._collapse_if_outside)
        self.setFixedSize(*self._full_size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAcceptDrops(True)
        self._drag_origin = None
        self.setStyleSheet(
            "QMainWindow { background: transparent; }"
            "QWidget#panel {"
            "  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #141c2f, stop:1 #0b1020);"
            "  border: 1px solid #2a3854; border-radius: 16px; color: #e5e7eb;"
            "}"
            "QLabel#brand { color: #cbd5e1; font-size: 11px; font-weight: 600; background: transparent; }"
            "QLabel#hint { color: #64748b; font-size: 12px; background: transparent; }"
            "QScrollArea { border: none; background: transparent; }"
            "QToolButton#chrome {"
            "  color: #e2e8f0; background: #1c2740; border: none; border-radius: 10px; font-size: 14px;"
            "}"
            "QToolButton#chrome:hover { background: #2a3a58; }"
        )

        central = QWidget()
        central.setObjectName("panel")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 8, 12, 10)
        root.setSpacing(6)

        top = QHBoxLayout()
        self._brand = QLabel("ascent")
        self._brand.setObjectName("brand")
        top.addWidget(self._brand)
        top.addStretch(1)
        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        self._set_dot("off")
        self._min_button = QToolButton()
        self._min_button.setObjectName("chrome")
        self._min_button.setText("–")
        self._min_button.setToolTip("Minimize")
        self._min_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._min_button.setFixedSize(20, 20)
        self._min_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._min_button.clicked.connect(self._toggle_minimized)
        top.addWidget(self._dot)
        top.addWidget(self._min_button)
        root.addLayout(top)

        self._slot_host = QWidget()
        self._slot = QVBoxLayout(self._slot_host)
        self._slot.setContentsMargins(0, 0, 0, 0)
        self._empty = QLabel("Waiting for a picture")
        self._empty.setObjectName("hint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._slot.addWidget(self._empty)
        root.addWidget(self._slot_host, 1)

        self._reel_scroll = QScrollArea()
        self._reel_scroll.setWidgetResizable(True)
        self._reel_scroll.setFixedHeight(40)
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
        self._reel_scroll.hide()
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

    def enterEvent(self, event) -> None:
        self._collapse_timer.stop()
        if self._minimized:
            self._apply_chrome(expanded=True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self._minimized and self._drag_origin is None:
            self._collapse_timer.start()
        super().leaveEvent(event)

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
            button.setFixedSize(52, 30)
            button.setIconSize(button.size())
            button.setToolTip(caption)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIcon(QIcon(QPixmap.fromImage(thumb)))
            border = "#7dd3fc" if index == self._history_index else "#1e293b"
            button.setStyleSheet(
                "QToolButton {"
                f"border: 2px solid {border}; border-radius: 6px; background: #0b1220; padding: 0;"
                "}"
                "QToolButton:hover { border-color: #e2e8f0; }"
            )
            button.clicked.connect(lambda _checked=False, i=index: self._show_history(i))
            self._reel_layout.insertWidget(index, button)
            self._reel_buttons.append(button)
        self._sync_reel()

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
        else:
            self._slot.addWidget(self._empty)
            self._empty.show()

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
        self._collapse_timer.stop()
        self._minimized = not self._minimized
        self._apply_chrome(expanded=not self._minimized)

    def _apply_chrome(self, expanded: bool) -> None:
        self._chrome_open = expanded
        self._slot_host.setVisible(expanded)
        self._sync_reel()
        self.setFixedSize(*(self._full_size if expanded else self._pill_size))
        if self._minimized:
            self._min_button.setText("+")
            self._min_button.setToolTip("Restore")
        else:
            self._min_button.setText("–")
            self._min_button.setToolTip("Minimize")

    def _sync_reel(self) -> None:
        self._reel_scroll.setVisible(self._chrome_open and bool(self._history))

    def _collapse_if_outside(self) -> None:
        if not self._minimized or self._drag_origin is not None:
            return
        if self.frameGeometry().contains(QCursor.pos()):
            return
        self._apply_chrome(expanded=False)

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
            if self._minimized and not self.frameGeometry().contains(QCursor.pos()):
                self._collapse_timer.start()
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
