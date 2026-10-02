"""Shareable canvas. The image is fitted, centred, and crossfaded. The shutter is instant."""

from PySide6.QtCore import QParallelAnimationGroup, QPropertyAnimation, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QGridLayout, QLabel, QMainWindow, QWidget

FADE_MS = 200


class FitLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = QImage()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_image(self, image: QImage) -> None:
        self._image = image
        self.update()

    def paintEvent(self, _event) -> None:
        if self._image.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scaled = self._image.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawImage(x, y, scaled)


class Canvas(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Co-pilot canvas")
        self.resize(1280, 720)
        self._shuttered = False
        self._top_is_a = False
        self._fade = None

        central = QWidget()
        central.setStyleSheet("background: #0f172a;")
        self.setCentralWidget(central)
        grid = QGridLayout(central)
        grid.setContentsMargins(0, 0, 0, 0)

        self._a = FitLabel()
        self._b = FitLabel()
        self._fx_a = QGraphicsOpacityEffect(self._a)
        self._fx_b = QGraphicsOpacityEffect(self._b)
        self._fx_a.setOpacity(0)
        self._fx_b.setOpacity(0)
        self._a.setGraphicsEffect(self._fx_a)
        self._b.setGraphicsEffect(self._fx_b)

        self._shutter = QWidget()
        self._shutter.setStyleSheet("background: #0f172a;")
        self._shutter.hide()

        grid.addWidget(self._a, 0, 0)
        grid.addWidget(self._b, 0, 0)
        grid.addWidget(self._shutter, 0, 0)

    def is_blank(self) -> bool:
        return self._shuttered

    def show_image(self, image: QImage) -> None:
        if image.isNull():
            return
        incoming = self._b if self._top_is_a else self._a
        incoming_fx = self._fx_b if self._top_is_a else self._fx_a
        outgoing_fx = self._fx_a if self._top_is_a else self._fx_b
        incoming.set_image(image)
        self._top_is_a = not self._top_is_a
        if self._fade is not None:
            self._fade.stop()
            self._fade = None
        if self._shuttered:
            incoming_fx.setOpacity(1)
            outgoing_fx.setOpacity(0)
            return
        fade_in = QPropertyAnimation(incoming_fx, b"opacity", self)
        fade_in.setDuration(FADE_MS)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_out = QPropertyAnimation(outgoing_fx, b"opacity", self)
        fade_out.setDuration(FADE_MS)
        fade_out.setStartValue(float(outgoing_fx.opacity()))
        fade_out.setEndValue(0.0)
        group = QParallelAnimationGroup(self)
        group.addAnimation(fade_in)
        group.addAnimation(fade_out)
        group.start()
        self._fade = group

    def toggle_shutter(self) -> bool:
        self._shuttered = not self._shuttered
        self._shutter.setVisible(self._shuttered)
        if self._shuttered:
            self._shutter.raise_()
        return self._shuttered

    def closeEvent(self, event) -> None:
        QApplication.quit()
        super().closeEvent(event)
