"""Live: every meeting being captured or processed, as a task list, with its stages, a live log
and the summary as the model writes it. Controls: cancel, retry (from any step), reorder, run
next, remove, pause the whole queue."""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QTextCursor
from PyQt6.QtWidgets import (QGridLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
                             QPlainTextEdit, QProgressBar, QSplitter, QTabWidget, QTextBrowser,
                             QToolButton, QWidget)

from core.jobs import JobQueue
from ui.common import (button, card, chip, confirm, duration, error_box, hbox, label,
                       page_header, pixmap_from_jpeg, restyle, run_async, set_chip, vbox)

STATUS = {"capturing": ("Capturing", "info"), "queued": ("Queued", "muted"),
          "running": ("Running", "info"), "waiting": ("Needs you", "warn"),
          "done": ("Done", "ok"), "failed": ("Failed", "bad"), "canceled": ("Canceled", "muted")}
STAGE_ICON = {"pending": ("○", "muted"), "running": ("●", "info"), "done": ("✓", "ok"),
              "skipped": ("–", "muted"), "failed": ("✕", "bad"), "canceled": ("✕", "muted"),
              "waiting": ("!", "warn")}


def current_stage(job: dict) -> dict | None:
    for st in job["stages"]:
        if st["status"] in ("running", "waiting", "failed"):
            return st
    return next((st for st in job["stages"] if st["status"] == "pending"), None)


def set_bar(bar: QProgressBar, value: float, state: str) -> None:
    bar.setValue(int(value * 1000))
    if bar.property("state") != state:
        bar.setProperty("state", state)
        restyle(bar)


class JobRow(QWidget):
    def __init__(self):
        super().__init__()
        self.title = label("", "h2")
        self.title.setMinimumWidth(80)
        self.status = chip("")
        self.stage = label("", "small")
        self.elapsed = label("", "small")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.setLayout(vbox(hbox(self.title, None, self.status),
                            hbox(self.stage, None, self.elapsed), self.bar,
                            spacing=4, margins=(10, 8, 10, 9)))

    def update_job(self, job: dict, position: int | None) -> None:
        title = job.get("title") or job["run"]
        self.title.setText(title if len(title) < 46 else title[:44] + "…")
        self.title.setToolTip(title)
        text, kind = STATUS.get(job["status"], (job["status"], "muted"))
        if job["status"] == "queued" and position:
            text = f"Queued #{position}"
        set_chip(self.status, text, kind)
        st = current_stage(job)
        if job["status"] == "done":
            self.stage.setText("Finished")
        elif job["status"] == "failed":
            self.stage.setText((job.get("error") or "failed")[:80])
        elif st:
            self.stage.setText(st["label"] + (f" · {st['detail']}" if st.get("detail") else ""))
        else:
            self.stage.setText("")
        start = next((s["started"] for s in job["stages"] if s.get("started")), None)
        end = None if job["status"] in ("running", "capturing") else job.get("updated")
        self.elapsed.setText(duration(start, end) if start else "")
        if job["status"] == "capturing":
            self.bar.setRange(0, 0)                    # busy indicator while recording
        else:
            self.bar.setRange(0, 1000)
            state = {"failed": "failed", "done": "done", "waiting": "waiting"}.get(job["status"], "")
            set_bar(self.bar, 1.0 if job["status"] == "done" else JobQueue.overall(job), state)


class StageRow:
    def __init__(self, grid: QGridLayout, row: int):
        self.icon = chip("")
        self.icon.setFixedWidth(26)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name = label("")
        self.detail = label("", "small", wrap=True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedWidth(140)
        self.time = label("", "small")
        self.time.setFixedWidth(52)
        self.time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for c, w in enumerate((self.icon, self.name, self.detail, self.bar, self.time)):
            grid.addWidget(w, row, c)
        self.widgets = (self.icon, self.name, self.detail, self.bar, self.time)

    def show(self, st: dict | None) -> None:
        for w in self.widgets:
            w.setVisible(st is not None)
        if st is None:
            return
        icon, kind = STAGE_ICON.get(st["status"], ("?", "muted"))
        set_chip(self.icon, icon, kind)
        self.name.setText(st["label"])
        self.detail.setText(st.get("error") or st.get("detail") or
                            {"pending": "waiting", "skipped": "skipped"}.get(st["status"], ""))
        if st["name"] == "capture" and st["status"] == "running":
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            set_bar(self.bar, 1.0 if st["status"] == "done" else st.get("progress") or 0.0,
                    {"failed": "failed", "done": "done", "waiting": "waiting"}.get(st["status"], ""))
        self.bar.setVisible(st["status"] not in ("pending", "skipped"))
        self.time.setText(duration(st.get("started"), st.get("ended")) if st.get("started") else "")


class LivePage(QWidget):
    open_run = pyqtSignal(str)
    open_run_tab = pyqtSignal(str, str)        # run, tab ("screens", "transcript", "edit")

    def __init__(self, ctl, bridge):
        super().__init__()
        self.setObjectName("page")
        self.ctl, self.q, self.bridge = ctl, ctl.queue, bridge
        self.rows: dict[str, tuple[QListWidgetItem, JobRow]] = {}
        self.selected: str | None = None
        self.dirty: set[str] = set()
        self.structure_dirty = True

        # ---- header
        self.queue_chip = chip("")
        self.pause_btn = button("Pause queue", on_click=self.toggle_pause,
                                tip="Hold all processing (the running step finishes first).")
        clear_btn = button("Clear finished", "ghost", self.clear_finished)
        header = page_header("Live", "Meetings being captured and processed, one at a time "
                             "(one model fits in memory).", self.queue_chip, self.pause_btn, clear_btn)

        # ---- list
        self.list = QListWidget()
        self.list.setObjectName("jobs")
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.currentItemChanged.connect(self._on_select)
        self.empty = label("Nothing here yet. When a Meetily recording starts, pick the window to "
                           "capture; when it stops, the meeting shows up here and is processed "
                           "automatically.", "muted", wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left = QWidget()
        left.setMinimumWidth(300)
        left.setLayout(vbox(self.list, self.empty, margins=(0, 0, 0, 0)))

        # ---- detail
        self.d_title = label("", "h1", wrap=True, select=True)
        self.d_meta = label("", "small", select=True)
        self.d_status = chip("")
        self.b_cancel = button("Cancel", "danger", self.cancel)
        self.b_stop = button("Stop capture", "primary", self.stop_capture,
                             tip="Stop recording the window now and process what was captured.")
        self.b_retry = button("Retry", "primary", lambda: self.retry(None))
        self.b_retry_from = QToolButton()
        self.b_retry_from.setText("Retry from ▾")
        self.b_retry_from.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.retry_menu = QMenu(self)
        self.b_retry_from.setMenu(self.retry_menu)
        self.b_next = button("Run next", on_click=self.run_next, tip="Process this one before the others")
        self.b_up = button("↑", on_click=lambda: self.move(-1), tip="Earlier in the queue")
        self.b_down = button("↓", on_click=lambda: self.move(1), tip="Later in the queue")
        self.b_open = button("Open meeting", "ghost", self.open_selected_run)
        self.b_screens = button("Screens", on_click=lambda: self._open_tab("screens"),
                                tip="See the captured screens full size")
        self.b_transcript = button("Transcript", on_click=lambda: self._open_tab("transcript"))
        self.b_edit = button("Edit & send", on_click=lambda: self._open_tab("edit"),
                             tip="Edit the summary and send it to Meetily")
        self.b_remove = button("Remove", "ghost", self.remove, tip="Remove from this list (files are kept)")
        controls = hbox(self.b_stop, self.b_cancel, self.b_retry, self.b_retry_from, self.b_next,
                        self.b_up, self.b_down, None, self.b_screens, self.b_transcript, self.b_edit,
                        self.b_open, self.b_remove, spacing=6)

        self.banner_text = label("", wrap=True)
        self.banner = card(vbox(self.banner_text, hbox(
            button("Replace with ours", "primary", lambda: self.decide("replace")),
            button("Keep Meetily's", on_click=lambda: self.decide("keep")),
            button("Decide later", "ghost", lambda: self.decide("later")), None)))
        self.banner.setProperty("banner", "warn")

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(2, 1)
        self.stage_rows = [StageRow(grid, i) for i in range(5)]
        self.stages_card = card(vbox(label("Steps", "h2"), grid))

        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(120)
        self.preview_card = card(vbox(label("Window being captured", "h2"), self.preview))

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setProperty("role", "log")
        self.log.setMaximumBlockCount(5000)
        self.output = QTextBrowser()
        self.output.setOpenExternalLinks(False)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.log, "Log")
        self.tabs.addTab(self.output, "Summary output")

        self.detail = QWidget()
        head = hbox(vbox(self.d_title, self.d_meta, spacing=2), 12, self.d_status)
        head.setStretch(0, 1)
        head.setAlignment(self.d_status, Qt.AlignmentFlag.AlignTop)
        self.detail.setLayout(vbox(head,
                                   controls, self.banner, self.stages_card, self.preview_card,
                                   self.tabs, spacing=10))
        self.detail.layout().setStretch(5, 1)
        self.placeholder = label("Select a job to see its steps and log.", "muted")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right = QWidget()
        right.setLayout(vbox(self.detail, self.placeholder))

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([360, 760])
        split.setChildrenCollapsible(False)
        self.setLayout(vbox(header, split, spacing=14, margins=(22, 18, 22, 18)))

        # ---- wiring
        bridge.job_changed.connect(self._mark)
        bridge.job_added.connect(self._mark_structure)
        bridge.job_removed.connect(self._mark_structure)
        bridge.job_finished.connect(lambda jid, _s: self._mark_structure(jid))
        bridge.job_log.connect(self._on_log)
        bridge.job_token.connect(self._on_token)
        bridge.queue_state.connect(lambda _p: self._update_queue_state())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(250)
        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self._update_preview)
        self.preview_timer.start(1000)
        self._tick()
        self._show_detail()

    # ---------------------------------------------------------------- refresh
    def _mark(self, jid: str) -> None:
        self.dirty.add(jid)
        job = self.q.get(jid)
        item = self.rows.get(jid)
        if job and item and item[1].status.text().startswith("Queued") != (job["status"] == "queued"):
            self.structure_dirty = True

    def _mark_structure(self, jid: str = "") -> None:
        self.structure_dirty = True
        self.dirty.add(jid)

    def _tick(self) -> None:
        if self.structure_dirty:
            self.structure_dirty = False
            self._rebuild()
        else:
            for jid in list(self.dirty):
                self._update_row(jid)
            # elapsed time ticks for active jobs
            for jid, (_item, row) in self.rows.items():
                job = self.q.get(jid)
                if job and job["status"] in ("running", "capturing"):
                    row.update_job(job, None)
        if self.selected and (self.selected in self.dirty or self._active(self.selected)):
            self._show_detail(full=False)
        self.dirty.clear()
        self._update_queue_state()

    def _active(self, jid: str) -> bool:
        job = self.q.get(jid)
        return bool(job and job["status"] in ("running", "capturing"))

    def _positions(self) -> dict[str, int]:
        queued = sorted([j for j in self.q.ordered() if j["status"] == "queued"], key=lambda j: j["order"])
        return {j["id"]: i + 1 for i, j in enumerate(queued)}

    def _rebuild(self) -> None:
        jobs = self.q.ordered()
        pos = self._positions()
        keep = self.selected
        self.list.blockSignals(True)
        self.list.clear()
        self.rows.clear()
        for job in jobs:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, job["id"])
            row = JobRow()
            row.update_job(job, pos.get(job["id"]))
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            self.rows[job["id"]] = (item, row)
        self.list.blockSignals(False)
        self.empty.setVisible(not jobs)
        self.list.setVisible(bool(jobs))
        if keep in self.rows:
            self.list.setCurrentItem(self.rows[keep][0])
        elif jobs and self.selected is None:
            self.list.setCurrentRow(0)
        elif keep and keep not in self.rows:
            self.selected = None
            self._show_detail()

    def _update_row(self, jid: str) -> None:
        job = self.q.get(jid)
        if job is None or jid not in self.rows:
            self.structure_dirty = bool(job)
            return
        self.rows[jid][1].update_job(job, self._positions().get(jid))

    def _update_queue_state(self) -> None:
        c = self.q.counts()
        if self.q.paused:
            set_chip(self.queue_chip, f"Paused · {c['queued']} waiting", "warn")
            self.pause_btn.setText("Resume queue")
        else:
            busy = c["running"] + c["capturing"]
            set_chip(self.queue_chip, f"{c['running']} running · {c['queued']} queued"
                     if busy or c["queued"] else "Idle", "info" if busy else "muted")
            self.pause_btn.setText("Pause queue")

    # ---------------------------------------------------------------- selection / detail
    def select_job(self, jid: str | None) -> None:
        if jid and jid not in self.rows:
            self._rebuild()
        if jid in self.rows:
            self.list.setCurrentItem(self.rows[jid][0])

    def _on_select(self, item, _prev=None) -> None:
        self.selected = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._show_detail()

    def _show_detail(self, full: bool = True) -> None:
        job = self.q.get(self.selected) if self.selected else None
        self.detail.setVisible(job is not None)
        self.placeholder.setVisible(job is None)
        if job is None:
            return
        s = job["status"]
        self.d_title.setText(job.get("title") or job["run"])
        bits = [f"run {job['run']}"]
        if job.get("meeting_id"):
            bits.append(f"meeting {job['meeting_id']}")
        bits.append({"meeting": "from a Meetily recording", "upload": "uploaded video",
                     "manual": "manual capture", "regenerate": "regenerate",
                     "publish": "write to Meetily"}.get(job.get("source"), job.get("source") or ""))
        bits.append(f"added {time.strftime('%H:%M', time.localtime(job['created']))}")
        self.d_meta.setText(" · ".join(bits))
        text, kind = STATUS.get(s, (s, "muted"))
        set_chip(self.d_status, text, kind)
        # controls
        queued = s == "queued"
        self.b_stop.setVisible(s == "capturing")
        self.b_cancel.setVisible(s in ("queued", "running", "waiting", "capturing"))
        self.b_cancel.setText("Cancel and discard" if s == "capturing" else "Cancel")
        self.b_retry.setVisible(s in ("failed", "canceled"))
        self.b_retry_from.setVisible(s in ("failed", "canceled", "done", "waiting"))
        for b in (self.b_next, self.b_up, self.b_down):
            b.setVisible(queued)
        self.b_remove.setVisible(s not in ("running", "capturing"))
        rd = self.ctl.store.run_dir(job["run"])
        self.b_screens.setVisible((rd / "screenshots.json").exists())
        self.b_transcript.setVisible(bool(job.get("meeting_id")))
        self.b_edit.setVisible((rd / "summary.md").exists() and s not in ("running", "capturing"))
        self.retry_menu.clear()
        for st in job["stages"]:
            if st["name"] == "capture":
                continue
            act = QAction(f"{st['label']}", self.retry_menu)
            act.triggered.connect(lambda _c=False, n=st["name"]: self.retry(n))
            self.retry_menu.addAction(act)
        # decision
        dec = job.get("decision") or {}
        self.banner.setVisible(s == "waiting" and dec.get("type") == "overwrite")
        if self.banner.isVisible():
            self.banner_text.setText(
                f"Meetily already has a summary for “{dec.get('title') or dec.get('meeting_id')}” "
                f"({dec.get('meetily_chars', 0)} characters). Replace it with ours "
                f"({dec.get('ours_chars', 0)} characters, includes what was on screen)? "
                "Meetily's version is saved to this run's backups first.")
        # stages
        for i, row in enumerate(self.stage_rows):
            row.show(job["stages"][i] if i < len(job["stages"]) else None)
        self.preview_card.setVisible(s == "capturing" and self.ctl.capture_job == job["id"])
        if full:
            self.log.setPlainText("\n".join(job.get("log") or []))
            self.log.moveCursor(QTextCursor.MoveOperation.End)
            self._load_output(job)

    def _load_output(self, job: dict) -> None:
        streamed = self.q.stream_text(job["id"])
        if streamed and job["status"] in ("running", "failed", "canceled"):
            self.output.setPlainText(streamed)
            return
        f = self.ctl.store.run_dir(job["run"]) / "summary.md"
        if f.exists() and any(s["name"] == "summarize" and s["status"] == "done" for s in job["stages"]):
            self.output.setMarkdown(f.read_text("utf-8"))
        else:
            self.output.setPlainText(streamed or "The summary appears here while the model writes it.")

    def _on_log(self, jid: str, line: str) -> None:
        if jid == self.selected:
            self.log.appendPlainText(line)

    def _on_token(self, jid: str, piece: str) -> None:
        if jid != self.selected:
            return
        if self.output.toPlainText().startswith("The summary appears here"):
            self.output.clear()
        if self.tabs.currentIndex() == 0 and not self.output.toPlainText():
            self.tabs.setCurrentIndex(1)
        cur = self.output.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertText(piece)
        self.output.setTextCursor(cur)
        self.output.ensureCursorVisible()

    def _update_preview(self) -> None:
        if not self.preview_card.isVisible():
            return
        def got(data):
            pm = pixmap_from_jpeg(data)
            if pm is not None:
                self.preview.setPixmap(pm.scaledToHeight(min(220, pm.height()),
                                                         Qt.TransformationMode.SmoothTransformation))
        run_async(lambda: self.ctl.capture_preview(480), got)

    # ---------------------------------------------------------------- actions
    def toggle_pause(self) -> None:
        self.q.set_paused(not self.q.paused)

    def clear_finished(self) -> None:
        self.q.clear_finished()

    def cancel(self) -> None:
        job = self.q.get(self.selected or "")
        if not job:
            return
        if job["status"] == "capturing":
            if not confirm(self, "Cancel capture", "Stop recording this window and discard the job? "
                           "(The recorded video is kept; Retry processes it.)"):
                return
            run_async(lambda: self.ctl.stop_capture(reason="capture canceled from the Live tab",
                                                    discard=True),
                      on_error=lambda e: error_box(self, "Cancel", e))
            return
        self.q.cancel(job["id"])

    def stop_capture(self) -> None:
        run_async(lambda: self.ctl.stop_capture(reason="capture stopped from the Live tab"),
                  on_error=lambda e: error_box(self, "Stop capture", e))

    def retry(self, stage: str | None) -> None:
        try:
            self.q.retry(self.selected, stage)
        except ValueError as e:
            error_box(self, "Retry", e)

    def run_next(self) -> None:
        self.q.run_next(self.selected)
        self.structure_dirty = True

    def move(self, delta: int) -> None:
        self.q.move(self.selected, delta)
        self.structure_dirty = True

    def remove(self) -> None:
        try:
            self.q.remove(self.selected)
        except ValueError as e:
            error_box(self, "Remove", e)

    def decide(self, action: str) -> None:
        job = self.q.get(self.selected or "")
        if job:
            run_async(lambda: self.ctl.resolve_overwrite(job["run"], action, job["id"]),
                      on_error=lambda e: error_box(self, "Decision", e))

    def _open_tab(self, tab: str) -> None:
        job = self.q.get(self.selected or "")
        if job:
            self.open_run_tab.emit(job["run"], tab)

    def open_selected_run(self) -> None:
        job = self.q.get(self.selected or "")
        if job:
            self.open_run.emit(job["run"])
