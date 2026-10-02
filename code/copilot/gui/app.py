"""Two windows: an always-on-top overlay for the host, and a canvas to share."""

import sys

from PySide6.QtWidgets import QApplication

from gui.canvas import Canvas
from gui.images import ImageLoader
from gui.overlay import Overlay
from gui.client import SuggestionClient

WDA_EXCLUDEFROMCAPTURE = 0x00000011


def exclude_from_capture(widget) -> None:
    """Hide the overlay from screen capture on Windows. The canvas stays shareable."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    hwnd = int(widget.winId())
    user32 = ctypes.windll.user32
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)


def main() -> None:
    app = QApplication(sys.argv)
    canvas = Canvas()
    loader = ImageLoader()
    client = SuggestionClient()
    overlay = Overlay(canvas, client, loader)

    screen = app.primaryScreen().availableGeometry()
    canvas.move(screen.x() + 40, screen.y() + 40)
    overlay.move(screen.right() - overlay.width() - 12, screen.bottom() - overlay.height() - 12)

    canvas.show()
    overlay.show()
    exclude_from_capture(overlay)
    overlay.raise_()
    overlay.activateWindow()
    client.open()
    raise SystemExit(app.exec())
