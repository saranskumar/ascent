"""Meetily's look: neutral palette, 8 px radius, light and dark. One stylesheet, two token sets."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPalette

LIGHT = dict(bg="#ffffff", panel="#fafafa", card="#ffffff", fg="#0a0a0a", muted="#737373",
             border="#e5e5e5", accent="#f5f5f5", hover="#f0f0f0", primary="#171717",
             primary_fg="#fafafa", ok="#16a34a", ok_bg="#dcfce7", warn="#b45309", warn_bg="#fef3c7",
             bad="#dc2626", bad_bg="#fee2e2", info="#2563eb", info_bg="#dbeafe", sel="#e9e9eb",
             code="#f5f5f5")
DARK = dict(bg="#0a0a0a", panel="#111111", card="#171717", fg="#fafafa", muted="#a3a3a3",
            border="#262626", accent="#262626", hover="#1f1f1f", primary="#fafafa",
            primary_fg="#171717", ok="#4ade80", ok_bg="#14532d", warn="#fbbf24", warn_bg="#451a03",
            bad="#f87171", bad_bg="#450a0a", info="#60a5fa", info_bg="#172554", sel="#262626",
            code="#111111")

current = dict(LIGHT)


def is_dark(pref: str) -> bool:
    if pref == "dark":
        return True
    if pref == "light":
        return False
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def _chevron(color: str, up: bool = False) -> str:
    """A small chevron PNG for combo/spin arrows (Qt stylesheets need an image file)."""
    import tempfile
    from pathlib import Path

    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QPainter, QPen, QPixmap
    f = Path(tempfile.gettempdir()) / f"vcs-main-chevron-{'up' if up else 'down'}-{color.lstrip('#')}.png"
    if not f.exists():
        pm = QPixmap(20, 20)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidthF(2.0)
        p.setPen(pen)
        ys = (12.5, 7.5) if up else (7.5, 12.5)
        p.drawPolyline([QPointF(5.5, ys[0]), QPointF(10, ys[1]), QPointF(14.5, ys[0])])
        p.end()
        pm.save(str(f))
    return f.as_posix()


def qss(t: dict) -> str:
    down, up = _chevron(t["muted"]), _chevron(t["muted"], up=True)
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 10pt; color: {t['fg']}; }}
QMainWindow, QDialog, QWidget#page, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {t['bg']}; }}
QWidget#sidebar {{ background: {t['panel']}; border-right: 1px solid {t['border']}; }}
QLabel {{ background: transparent; }}
QLabel[role="brand"] {{ font-size: 12pt; font-weight: 600; padding: 4px 8px; }}
QLabel[role="h1"] {{ font-size: 16pt; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 11.5pt; font-weight: 600; }}
QLabel[role="muted"] {{ color: {t['muted']}; }}
QLabel[role="small"] {{ color: {t['muted']}; font-size: 9pt; }}
QLabel[role="mono"] {{ font-family: Consolas, "Cascadia Mono", monospace; font-size: 9pt; color: {t['muted']}; }}
QLabel[chip] {{ border-radius: 9px; padding: 1px 8px; font-size: 8.5pt; font-weight: 600; }}
QLabel[chip="ok"] {{ color: {t['ok']}; background: {t['ok_bg']}; }}
QLabel[chip="warn"] {{ color: {t['warn']}; background: {t['warn_bg']}; }}
QLabel[chip="bad"] {{ color: {t['bad']}; background: {t['bad_bg']}; }}
QLabel[chip="info"] {{ color: {t['info']}; background: {t['info_bg']}; }}
QLabel[chip="muted"] {{ color: {t['muted']}; background: {t['accent']}; }}
QFrame[card="true"] {{ background: {t['card']}; border: 1px solid {t['border']}; border-radius: 8px; }}
QFrame[banner="warn"] {{ background: {t['warn_bg']}; border: 1px solid {t['warn']}; border-radius: 8px; }}
QFrame[banner="info"] {{ background: {t['info_bg']}; border: 1px solid {t['info']}; border-radius: 8px; }}
QFrame[sep="true"] {{ background: {t['border']}; max-height: 1px; min-height: 1px; border: none; }}

QListWidget#nav {{ background: transparent; border: none; outline: none; }}
QListWidget#nav::item {{ padding: 7px 10px; border-radius: 6px; margin: 1px 6px; color: {t['muted']}; }}
QListWidget#nav::item:hover {{ background: {t['hover']}; color: {t['fg']}; }}
QListWidget#nav::item:selected {{ background: {t['sel']}; color: {t['fg']}; font-weight: 600; }}

QPushButton {{ background: {t['card']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 5px 12px; }}
QPushButton:hover {{ background: {t['hover']}; }}
QPushButton:disabled {{ color: {t['muted']}; background: {t['accent']}; }}
QPushButton[kind="primary"] {{ background: {t['primary']}; color: {t['primary_fg']}; border-color: {t['primary']}; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {t['muted']}; border-color: {t['muted']}; }}
QPushButton[kind="primary"]:disabled {{ background: {t['accent']}; color: {t['muted']}; border-color: {t['border']}; }}
QPushButton[kind="danger"] {{ color: {t['bad']}; }}
QPushButton[kind="ghost"] {{ background: transparent; border-color: transparent; color: {t['muted']}; }}
QPushButton[kind="ghost"]:hover {{ background: {t['hover']}; color: {t['fg']}; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 3px 6px; }}
QToolButton:hover {{ background: {t['hover']}; border-color: {t['border']}; }}
QToolButton:checked {{ background: {t['sel']}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QTextBrowser {{
  background: {t['bg']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 4px 6px;
  selection-background-color: {t['info']}; selection-color: #ffffff; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {t['muted']}; }}
QPlainTextEdit[role="log"] {{ font-family: Consolas, "Cascadia Mono", monospace; font-size: 9pt; background: {t['code']}; }}
QComboBox::drop-down {{ border: none; width: 22px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}
QAbstractSpinBox {{ padding-right: 18px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ border: none; background: transparent; width: 16px; subcontrol-origin: border; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; margin-top: 2px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; margin-bottom: 2px; }}
QAbstractSpinBox::up-arrow {{ image: url({up}); width: 9px; height: 9px; }}
QAbstractSpinBox::down-arrow {{ image: url({down}); width: 9px; height: 9px; }}
QComboBox QAbstractItemView {{ background: {t['card']}; border: 1px solid {t['border']}; selection-background-color: {t['sel']}; selection-color: {t['fg']}; }}
QCheckBox {{ spacing: 8px; }}

QProgressBar {{ background: {t['accent']}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {t['primary']}; border-radius: 3px; }}
QProgressBar[state="failed"]::chunk {{ background: {t['bad']}; }}
QProgressBar[state="done"]::chunk {{ background: {t['ok']}; }}
QProgressBar[state="waiting"]::chunk {{ background: {t['warn']}; }}

QTableWidget, QListWidget, QTreeWidget {{ background: {t['card']}; border: 1px solid {t['border']}; border-radius: 8px; gridline-color: {t['border']}; outline: none; }}
QTableWidget::item, QListWidget::item {{ padding: 4px; }}
QTableWidget::item:selected, QListWidget::item:selected {{ background: {t['sel']}; color: {t['fg']}; }}
QListWidget#jobs::item {{ border-bottom: 1px solid {t['border']}; padding: 0px; }}
QListWidget#jobs::item:selected {{ background: {t['sel']}; }}
QHeaderView::section {{ background: {t['panel']}; color: {t['muted']}; border: none; border-bottom: 1px solid {t['border']}; padding: 5px 6px; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 8px; top: -1px; background: {t['card']}; }}
QTabBar::tab {{ background: transparent; padding: 6px 12px; color: {t['muted']}; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t['fg']}; border-bottom: 2px solid {t['primary']}; font-weight: 600; }}
QGroupBox {{ border: 1px solid {t['border']}; border-radius: 8px; margin-top: 14px; padding: 10px; background: {t['card']}; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QSplitter::handle {{ background: {t['border']}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {t['border']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QToolTip {{ background: {t['card']}; color: {t['fg']}; border: 1px solid {t['border']}; padding: 4px; }}
QMenu {{ background: {t['card']}; border: 1px solid {t['border']}; padding: 4px; }}
QMenu::item {{ padding: 5px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t['sel']}; }}
"""


def apply(app, pref: str = "system") -> dict:
    t = DARK if is_dark(pref) else LIGHT
    current.clear()
    current.update(t)
    app.setStyle("Fusion")
    pal = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.Base, "bg"),
                      (QPalette.ColorRole.AlternateBase, "panel"), (QPalette.ColorRole.Text, "fg"),
                      (QPalette.ColorRole.WindowText, "fg"), (QPalette.ColorRole.Button, "card"),
                      (QPalette.ColorRole.ButtonText, "fg"), (QPalette.ColorRole.Highlight, "sel"),
                      (QPalette.ColorRole.HighlightedText, "fg"), (QPalette.ColorRole.ToolTipBase, "card"),
                      (QPalette.ColorRole.ToolTipText, "fg"), (QPalette.ColorRole.PlaceholderText, "muted")):
        pal.setColor(role, QColor(t[key]))
    app.setPalette(pal)
    app.setStyleSheet(qss(t))
    return t
