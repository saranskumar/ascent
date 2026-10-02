"""Suggestions WebSocket. Remembers seq and resumes with ?since= after a drop."""

import json

from PySide6.QtCore import QObject, QSettings, QTimer, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QAbstractSocket, QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

from contracts.config import ENGINE_PORT, HOST


class SuggestionClient(QObject):
    suggestion = Signal(object)
    connection_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("Ascent", "CopilotGui")
        self._seq = _as_int(self._settings.value("lastSeq"))
        self._gen = 0
        self._since = None
        self._await_close = False
        self._nam = QNetworkAccessManager(self)
        self._ws = QWebSocket()
        self._ws.textMessageReceived.connect(self._on_text)
        self._ws.connected.connect(lambda: self.connection_changed.emit(True))
        self._ws.disconnected.connect(self._on_disconnected)
        self._reconnect = QTimer(self)
        self._reconnect.setSingleShot(True)
        self._reconnect.timeout.connect(self.open)

    def open(self) -> None:
        self._gen += 1
        gen = self._gen
        self._reconnect.stop()
        if self._seq is None:
            self._connect(None, gen)
            return
        reply = self._nam.get(QNetworkRequest(QUrl(f"http://{HOST}:{ENGINE_PORT}/suggestions/history")))
        reply.finished.connect(lambda r=reply, g=gen: self._history_done(r, g))

    def is_connected(self) -> bool:
        return self._ws.state() == QAbstractSocket.SocketState.ConnectedState

    def send(self, payload: dict) -> None:
        if self._ws.state() == QAbstractSocket.SocketState.ConnectedState:
            self._ws.sendTextMessage(json.dumps(payload))

    def _history_done(self, reply: QNetworkReply, gen: int) -> None:
        max_seq = None
        if reply.error() == QNetworkReply.NetworkError.NoError:
            try:
                rows = json.loads(bytes(reply.readAll()).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                rows = None
            if isinstance(rows, list):
                max_seq = 0
                for row in rows:
                    if isinstance(row, dict):
                        seq = _as_int(row.get("seq"))
                        if seq is not None and seq > max_seq:
                            max_seq = seq
        reply.deleteLater()
        if gen != self._gen:
            return
        if max_seq is not None and self._seq is not None and max_seq < self._seq:
            self._seq = None
            self._settings.remove("lastSeq")
        self._connect(self._seq, gen)

    def _connect(self, since, gen: int) -> None:
        if gen != self._gen:
            return
        self._since = since
        if self._ws.state() != QAbstractSocket.SocketState.UnconnectedState:
            self._await_close = True
            self._ws.close()
            return
        self._open_socket(since)

    def _open_socket(self, since) -> None:
        url = QUrl(f"ws://{HOST}:{ENGINE_PORT}/suggestions")
        if since is not None:
            query = QUrlQuery()
            query.addQueryItem("since", str(since))
            url.setQuery(query)
        self._ws.open(url)

    def _on_disconnected(self) -> None:
        self.connection_changed.emit(False)
        if self._await_close:
            self._await_close = False
            self._open_socket(self._since)
            return
        self._reconnect.start(1000)

    def _on_text(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(message, dict):
            return
        seq = _as_int(message.get("seq"))
        if seq is not None:
            self._seq = seq
            self._settings.setValue("lastSeq", seq)
        if message.get("type") == "suggestion":
            self.suggestion.emit(message)


def _as_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
