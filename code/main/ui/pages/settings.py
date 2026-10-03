"""Settings: every knob the app uses, editable by hand. Saved to data/settings.json."""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                             QLineEdit, QPlainTextEdit, QScrollArea, QSpinBox, QWidget)

from core import autostart
from core.config import DEFAULTS, NUMBER_LIMITS
from ui.common import (button, chip, confirm, error_box, hbox, label, open_path, page_header,
                       run_async, set_chip, vbox)

TEST_DATA = Path(__file__).resolve().parents[3] / "extraction-test" / "data"


class SettingsPage(QWidget):
    theme_changed = pyqtSignal(str)

    def __init__(self, ctl):
        super().__init__()
        self.setObjectName("page")
        self.ctl = ctl
        self.fields: dict[str, tuple] = {}         # key -> (widget, get, set)

        # ---- model
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setMinimumWidth(260)
        self.model_chip = chip("…")
        self._add("model", self.model, lambda: self.model.currentText().strip(),
                  lambda v: self.model.setCurrentText(v))
        self.model.currentTextChanged.connect(lambda _t: self._check_model())
        self.device = QComboBox()
        self.device.addItem("CPU (works on a 4 GB GPU machine)", "cpu")
        self.device.addItem("GPU (Ollama loads as many layers as fit)", "gpu")
        self._add("device", self.device, lambda: self.device.currentData(),
                  lambda v: self.device.setCurrentIndex(max(0, self.device.findData(v))))
        self.vlm_prompt = QPlainTextEdit()
        self.vlm_prompt.setMaximumHeight(80)
        self._add("vlm_prompt", self.vlm_prompt, lambda: self.vlm_prompt.toPlainText().strip(),
                  self.vlm_prompt.setPlainText)
        self.test_result = label("", "small", wrap=True)
        m = QFormLayout()
        m.addRow("Model", hbox(self.model, button("Refresh", "ghost", self.load_models),
                               self.model_chip, None))
        m.addRow("", label("One local model does both: describes diagram screens and writes the "
                           "summary. qwen3-vl:2b-instruct is the default; pull a 4B model with "
                           "Ollama and it appears here.", "small", wrap=True))
        m.addRow("Run on", self.device)
        m.addRow("Context size (tokens)", self._num("num_ctx"))
        m.addRow("Summary max tokens", self._num("max_tokens"))
        m.addRow("Temperature", self._num("temperature", decimals=2))
        m.addRow("Repeat penalty", self._num("repeat_penalty", decimals=2))
        self.describe = QComboBox()
        for v, t in (("all", "Every screen (pictures too)"), ("diagrams", "Only screens with little text"),
                     ("none", "None (screen text only)")):
            self.describe.addItem(t, v)
        self._add("describe_screens", self.describe, lambda: self.describe.currentData(),
                  lambda v: self.describe.setCurrentIndex(max(0, self.describe.findData(v))))
        m.addRow("Describe screens", self.describe)
        m.addRow("Description max tokens", self._num("vlm_max_tokens"))
        m.addRow("Image long side (px)", self._num("vlm_max_side"))
        m.addRow("Screen prompt", self.vlm_prompt)
        m.addRow("", self._check("start_ollama", "Start Ollama with this app, and again if it "
                                 "stops (only when it runs on this computer)"))
        m.addRow("", self._check("keep_loaded", "Keep the model in memory while jobs are queued "
                                 "(unloaded when the queue is empty)"))
        m.addRow("Timeout (s without output)", self._num("request_timeout"))
        self.url = QLineEdit()
        self._add("ollama_url", self.url, lambda: self.url.text().strip(), self.url.setText)
        m.addRow("Ollama URL", self.url)
        m.addRow("", hbox(button("Test model", on_click=self.test_model,
                                 tip="Load the model and ask for a one-word answer."),
                          self.test_result, None))

        # ---- extraction
        e = QFormLayout()
        e.addRow("Sample every (s)", self._num("interval", decimals=2))
        e.addRow("New screen when changed by (bits of 1024)", self._num("hash_threshold"))
        e.addRow("Drift limit (bits)", self._num("drift_threshold"))
        e.addRow("Drop screens shown under (s)", self._num("min_dwell", decimals=1))
        e.addRow("Diagram if fewer OCR characters than", self._num("diagram_chars"))
        e.addRow("", label("Applies to the next extraction.", "small"))

        # ---- automation
        a = vbox(spacing=6)
        for key, text in (
                ("auto_capture", "When a Meetily recording starts, capture a window"),
                ("ask_window", "…ask which window (off: use the remembered window without asking)"),
                ("show_on_start", "Bring this window up when a recording starts"),
                ("auto_summarize", "When it stops, extract screens and write the summary right away"),
                ("show_on_stop", "…and show it on the Live tab"),
                ("auto_publish", "Write our summary into Meetily (asks first if Meetily already has one)"),
                ("notify", "Tray notifications (done, failed, needs you)"),
                ("delete_screenshots_after_summary", "Delete screenshots after the summary is written to Meetily"),
                ("keep_capture_video", "Keep the capture video after extraction")):
            a.addWidget(self._check(key, text))
        self.watch_lbl = label("", "small", wrap=True)
        a.addLayout(hbox(label("If the question isn't answered in"), self._num("pick_timeout"),
                         label("s, capture the last-used window (0 = keep waiting)"), None))
        a.addLayout(hbox(self.watch_lbl, None, button("Forget", "ghost", self.forget_window)))

        # ---- app
        self.autostart = QCheckBox("Start with Windows (in the tray)")
        self.theme = QComboBox()
        for v, t in (("system", "Follow Windows"), ("light", "Light"), ("dark", "Dark")):
            self.theme.addItem(t, v)
        self._add("theme", self.theme, lambda: self.theme.currentData(),
                  lambda v: self.theme.setCurrentIndex(max(0, self.theme.findData(v))))
        ap = QFormLayout()
        ap.addRow("", self.autostart)
        ap.addRow("", self._check("start_minimized", "Start hidden in the tray"))
        ap.addRow("Theme", self.theme)
        ap.addRow("Webhook port", self._num("webhook_port"))
        ap.addRow("", label("Port changes apply after a restart; add 127.0.0.1:<port> to Meetily's "
                            "Local targets.", "small", wrap=True))

        # ---- data
        self.data_lbl = QLineEdit(str(ctl.data))
        self.data_lbl.setReadOnly(True)
        self.data_lbl.setMinimumWidth(120)
        self.import_lbl = label("", "small", wrap=True)
        dt = vbox(hbox(self.data_lbl, button("Open folder", "ghost", lambda: open_path(ctl.data)),
                       button("Open settings.json", "ghost", lambda: open_path(ctl.settings.file))),
                  hbox(button("Import runs from extraction-test", on_click=self.import_runs,
                              tip=str(TEST_DATA)), self.import_lbl, None))

        def group(title, lay):
            g = QGroupBox(title)
            g.setLayout(lay)
            return g

        self.save_btn = button("Save", "primary", self.save)
        self.saved_lbl = label("", "small")
        body = QWidget()
        body.setLayout(vbox(page_header("Settings", "Everything runs on this computer. Changes are "
                                        "saved to data/settings.json.", self.saved_lbl,
                                        button("Reset to defaults", "ghost", self.reset),
                                        button("Revert", "ghost", self.load), self.save_btn),
                            group("Model (Ollama, local)", m), group("Screen extraction", e),
                            group("Automation (Meetily)", a), group("App", ap), group("Data", dt),
                            None, spacing=14, margins=(22, 18, 22, 18)))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLayout(vbox(scroll))
        self.load()

    # ---------------------------------------------------------------- field helpers
    def _add(self, key, widget, get, set_):
        self.fields[key] = (widget, get, set_)

    def _num(self, key: str, decimals: int = 0):
        lo, hi, step = NUMBER_LIMITS[key]
        if decimals or isinstance(DEFAULTS[key], float):
            w = QDoubleSpinBox()
            w.setDecimals(decimals or 2)
        else:
            w = QSpinBox()
        w.setRange(lo, hi)
        w.setSingleStep(step)
        w.setMaximumWidth(140)
        self._add(key, w, w.value, w.setValue)
        return w

    def _check(self, key: str, text: str) -> QCheckBox:
        w = QCheckBox(text)
        self._add(key, w, w.isChecked, w.setChecked)
        return w

    # ---------------------------------------------------------------- load / save
    def showEvent(self, e):
        super().showEvent(e)
        self.load()
        self.load_models()

    def load(self) -> None:
        s = self.ctl.settings.all()
        for key, (_w, _get, set_) in self.fields.items():
            set_(s[key])
        self.autostart.setChecked(autostart.is_enabled())
        w = s.get("watch") or {}
        self.watch_lbl.setText(f"Remembered window: {w.get('title', '')[:60]} ({w.get('process')})"
                               if w else "No remembered window yet.")
        self.saved_lbl.setText("")

    def save(self) -> None:
        new = {key: get() for key, (_w, get, _s) in self.fields.items()}
        if not new["model"]:
            error_box(self, "Settings", "Pick a model.")
            return
        old = self.ctl.settings.all()
        self.ctl.settings.update(new)
        try:
            if self.autostart.isChecked() != autostart.is_enabled():
                autostart.set_enabled(self.autostart.isChecked())
        except OSError as e:
            error_box(self, "Start with Windows", e)
        if new["theme"] != old["theme"]:
            self.theme_changed.emit(new["theme"])
        if new["model"] != old["model"] and old["keep_loaded"]:
            run_async(lambda: self.ctl._ollama().unload(old["model"]))
        note = " · restart the app for the new port" if new["webhook_port"] != old["webhook_port"] else ""
        self.saved_lbl.setText(f"Saved {time.strftime('%H:%M:%S')}{note}")

    def reset(self) -> None:
        if confirm(self, "Reset settings", "Put every setting back to its default? (The remembered "
                   "window is kept.)"):
            self.ctl.settings.reset()
            self.load()
            self.saved_lbl.setText("Defaults restored")

    # ---------------------------------------------------------------- model
    def load_models(self) -> None:
        def show(models):
            cur = self.model.currentText()
            self.model.blockSignals(True)
            self.model.clear()
            self.model.addItems(models)
            self.model.setCurrentText(cur or self.ctl.settings["model"])
            self.model.blockSignals(False)
            self._models = models
            self._check_model()

        def failed(e):
            self._models = None
            set_chip(self.model_chip, "Ollama offline", "bad")
            self.model_chip.setToolTip(str(e))
        run_async(self.ctl.models, show, failed)

    def _check_model(self) -> None:
        models = getattr(self, "_models", None)
        if models is None:
            return
        name = self.model.currentText().strip()
        if name in models:
            set_chip(self.model_chip, "Installed", "ok")
            self.model_chip.setToolTip("")
        else:
            set_chip(self.model_chip, "Not installed", "warn")
            self.model_chip.setToolTip(f"ollama pull {name}")

    def test_model(self) -> None:
        model = self.model.currentText().strip()
        num_gpu = 0 if self.device.currentData() == "cpu" else None
        url = self.url.text().strip()
        self.test_result.setText(f"Loading {model}…")

        def work():
            from core.ollama import Ollama
            opts = {"num_predict": 8, "temperature": 0}
            if num_gpu is not None:
                opts["num_gpu"] = num_gpu
            out = Ollama(url).chat_stream(model, [{"role": "user", "content": "Reply with the single word OK."}],
                                          opts, keep_alive=0, timeout=300)
            return out
        run_async(work, lambda o: self.test_result.setText(
            f"Works: answered “{o['text'].strip()[:20]}” in {o['seconds']}s (includes loading)."),
            lambda e: self.test_result.setText(f"Failed: {e}"))

    # ---------------------------------------------------------------- misc
    def forget_window(self) -> None:
        self.ctl.settings.update({"watch": {}})
        self.load()

    def import_runs(self) -> None:
        if not TEST_DATA.is_dir():
            error_box(self, "Import", f"Not found: {TEST_DATA}")
            return
        self.import_lbl.setText("Copying…")
        run_async(lambda: self.ctl.store.import_from(TEST_DATA),
                  lambda r: self.import_lbl.setText(f"Imported {len(r['copied'])} run(s)"
                                                    + (f", {r['skipped']} already here" if r["skipped"] else "")),
                  lambda e: self.import_lbl.setText(f"Import failed: {e}"))
