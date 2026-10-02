"""Fetch engine image URLs on the Qt network stack. Results are cached by URL."""

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


class ImageLoader(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._cache: dict[str, QImage] = {}
        self._waiters: dict[str, list] = {}

    def fetch(self, url: str, callback) -> None:
        if not url:
            return
        cached = self._cache.get(url)
        if cached is not None:
            callback(cached)
            return
        self._waiters.setdefault(url, []).append(callback)
        if len(self._waiters[url]) > 1:
            return
        reply = self._nam.get(QNetworkRequest(QUrl(url)))
        reply.finished.connect(lambda r=reply, u=url: self._finish(u, r))

    def _finish(self, url: str, reply: QNetworkReply) -> None:
        image = QImage()
        if reply.error() == QNetworkReply.NetworkError.NoError:
            image.loadFromData(reply.readAll())
        reply.deleteLater()
        if not image.isNull():
            self._cache[url] = image
        waiters = self._waiters.pop(url, [])
        if image.isNull():
            return
        for callback in waiters:
            callback(image)
