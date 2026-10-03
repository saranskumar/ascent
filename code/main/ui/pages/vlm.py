"""VLM test: try the installed models on one screen image (file, paste, or a screen from a run)
and compare their descriptions and speed before picking one in Settings."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QLabel, QListWidget, QListWidgetItem,
                             QPlainTextEdit, QSplitter, QTextBrowser, QWidget)

from ui.common import button, card, error_box, hbox, label, page_header, run_async, vbox


class VlmPage(QWidget):
    def __init__(self, ctl):
        super().__init__()
        self.setObjectName("page")
        self.ctl = ctl
        self.image: Path | None = None
        self._tmp = Path(tempfile.gettempdir()) / "vcs-main-vlm-paste.png"

        self.img = QLabel("Choose an image, or paste one (Ctrl+V)")
        self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img.setMinimumSize(320, 200)
        self.img.setProperty("role", "muted")
        self.models = QListWidget()
        self.models.setMaximumHeight(140)
        self.device = QComboBox()
        self.device.addItem("CPU", 0)
        self.device.addItem("GPU (Ollama decides)", None)
        self.prompt = QPlainTextEdit(ctl.settings["vlm_prompt"])
        self.prompt.setMaximumHeight(90)
        self.run_btn = button("Describe", "primary", self.run)
        self.results = QTextBrowser()
        self.status = label("", "small")

        left = QWidget()
        left.setLayout(vbox(card(vbox(hbox(label("Image", "h2"), None,
                                           button("Choose file…", on_click=self.choose),
                                           button("Paste", "ghost", self.paste)), self.img)),
                            card(vbox(hbox(label("Models", "h2"), None,
                                           button("Refresh", "ghost", self.load_models)),
                                      self.models, hbox(label("Run on"), self.device, None),
                                      label("Prompt", "small"), self.prompt,
                                      hbox(self.status, None, self.run_btn)))))
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(card(vbox(label("Results", "h2"), self.results)))
        split.setSizes([480, 600])
        self.setLayout(vbox(page_header("VLM test", "Diagram screens (almost no text) are described "
                                        "by the vision model before the summary. Compare models "
                                        "here; the first run of a model includes loading it."),
                            split, spacing=14, margins=(22, 18, 22, 18)))
        QShortcut(QKeySequence.StandardKey.Paste, self, activated=self.paste)

    def showEvent(self, e):
        super().showEvent(e)
        if self.models.count() == 0:
            self.load_models()
        self.device.setCurrentIndex(0 if self.ctl.settings["device"] == "cpu" else 1)

    def load_models(self) -> None:
        def show(models):
            self.models.clear()
            current = self.ctl.settings["model"]
            for m in models:
                it = QListWidgetItem(m)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked if m == current else Qt.CheckState.Unchecked)
                self.models.addItem(it)
            self.status.setText(f"{len(models)} model(s) in Ollama" if models else "No models installed")
        run_async(self.ctl.models, show, lambda e: self.status.setText(str(e)))

    def _set_image(self, path: Path) -> None:
        pm = QPixmap(str(path))
        if pm.isNull():
            error_box(self, "Image", f"Can't read {path}")
            return
        self.image = path
        self.img.setPixmap(pm.scaled(460, 300, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))

    def choose(self) -> None:
        start = str(self.ctl.store.runs_dir)
        path, _ = QFileDialog.getOpenFileName(self, "Choose a screen image", start,
                                              "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self._set_image(Path(path))

    def paste(self) -> None:
        img = QGuiApplication.clipboard().image()
        if img.isNull():
            return
        img.save(str(self._tmp))
        self._set_image(self._tmp)

    def run(self) -> None:
        if self.image is None:
            error_box(self, "VLM test", "Choose or paste an image first.")
            return
        models = [self.models.item(i).text() for i in range(self.models.count())
                  if self.models.item(i).checkState() == Qt.CheckState.Checked]
        if not models:
            error_box(self, "VLM test", "Tick at least one model.")
            return
        if self.ctl.queue.current:
            self.status.setText("Note: a job is running; both compete for the model and memory.")
        s = self.ctl.settings.all()
        image, num_gpu, prompt = self.image, self.device.currentData(), self.prompt.toPlainText().strip()
        self.run_btn.setEnabled(False)
        self.results.setHtml("<p>Running…</p>")

        def work():
            client = self.ctl._ollama()
            out = []
            for m in models:
                t = time.monotonic()
                try:
                    text = client.describe(image, m, prompt or s["vlm_prompt"], num_gpu=num_gpu,
                                           max_tokens=s["vlm_max_tokens"], max_side=s["vlm_max_side"],
                                           keep_alive=0, timeout=s["request_timeout"])
                    err = ""
                except Exception as e:  # noqa: BLE001
                    text, err = "", str(e)
                out.append((m, round(time.monotonic() - t, 1), text, err))
            return out

        def show(rows):
            self.run_btn.setEnabled(True)
            html = []
            for m, secs, text, err in rows:
                body = f"<p style='color:#dc2626'>{_esc(err)}</p>" if err else f"<p>{_esc(text)}</p>"
                html.append(f"<h3>{_esc(m)} <span style='color:gray;font-weight:normal'>· {secs}s</span></h3>{body}")
            self.results.setHtml("".join(html))

        def failed(e):
            self.run_btn.setEnabled(True)
            self.results.setPlainText(str(e))
        run_async(work, show, failed)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
