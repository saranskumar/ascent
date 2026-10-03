"""Meetings: every run (one captured meeting or uploaded video) with its summary (view, edit,
send to Meetily), transcript, screens (full-size viewer), timeline, speaker names and the exact
model input. Link it to a Meetily meeting, regenerate, answer Replace/Keep."""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDoubleSpinBox,
                             QFileDialog, QHeaderView, QLineEdit, QListView, QListWidget,
                             QListWidgetItem, QMessageBox, QPlainTextEdit, QSplitter,
                             QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget,
                             QTextBrowser, QWidget)

from core.prompts import build_system_prompt
from core.store import read_json
from core.transcript import build_input
from core.writeback import summary_text
from ui.common import (button, card, chip, colored_item, confirm, error_box, hbox, label, mmss, open_path,
                       page_header, run_async, vbox, when)
from ui.image_viewer import ImageViewer

TABS = ["summary", "transcript", "screens", "timeline", "speakers", "input"]


class MeetingsPage(QWidget):
    def __init__(self, ctl, bridge):
        super().__init__()
        self.setObjectName("page")
        self.ctl, self.store = ctl, ctl.store
        self.run_id: str | None = None
        self.detail_data: dict | None = None
        self.meetings: list[dict] = []

        # ---- list
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by title, run or meeting id")
        self.search.textChanged.connect(self._filter)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["When", "Meeting", "State"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_select)
        left = QWidget()
        left.setMinimumWidth(260)
        left.setLayout(vbox(self.search, self.table))

        # ---- detail header + actions
        self.d_title = label("", "h1", wrap=True, select=True)
        self.d_meta = label("", "small", wrap=True, select=True)
        self.d_chips = hbox(spacing=6)
        self.b_generate = button("Generate summary", "primary", lambda: self.regenerate(False),
                                 tip="Queue a new summary (shows on the Live tab).")
        self.b_describe = button("Describe + summarize", on_click=lambda: self.regenerate(True),
                                 tip="Describe diagram screens that have no description yet, then summarize.")
        self.b_publish = button("Send to Meetily", on_click=self.send,
                                tip="Back up Meetily's summary, then write ours (asks if Meetily has one).")
        self.b_folder = button("Open folder", "ghost", self.open_folder)
        self.b_delete = button("Delete", "danger", self.delete)
        actions = hbox(self.b_generate, self.b_describe, self.b_publish, None, self.b_folder,
                       self.b_delete, spacing=6)

        # link row
        self.meeting = QComboBox()
        self.meeting.setEditable(True)
        self.meeting.setToolTip("The Meetily meeting whose transcript goes with these screens.")
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-36000, 36000)
        self.offset.setDecimals(1)
        self.offset.setSingleStep(0.5)
        self.offset.setSuffix(" s")
        self.offset.setToolTip("Seconds from the start of Meetily's recording to the start of the "
                               "screen capture.")
        self.offset_basis = label("", "small", wrap=True)
        self.meeting.setMinimumWidth(200)
        self.offset.setFixedWidth(110)
        row1 = hbox(label("Meetily meeting"), self.meeting, spacing=8)
        row1.setStretch(1, 1)
        link = card(vbox(row1, hbox(label("Screen offset"), self.offset,
                                    button("Suggest", "ghost", self.suggest_offset), self.offset_basis,
                                    None, button("Save link", on_click=self.save_link)),
                         spacing=6), margins=(12, 8, 12, 8))

        self.banner_text = label("", wrap=True)
        self.banner = card(vbox(self.banner_text, hbox(
            button("Replace with ours", "primary", lambda: self.decide("replace")),
            button("Keep Meetily's", on_click=lambda: self.decide("keep")), None)))
        self.banner.setProperty("banner", "warn")

        # ---- tabs
        # Summary: view (rendered) or edit (Markdown left, live preview right), then send.
        self.summary = QTextBrowser()
        self.summary.setOpenExternalLinks(True)
        self.sum_meta = label("", "small", wrap=True)
        self.b_copy = button("Copy", "ghost", self.copy_summary, tip="Copy the summary as Markdown")
        self.b_backup = button("Show Meetily's backup", "ghost", self.toggle_backup)
        self.b_edit = button("Edit", on_click=self.start_edit, tip="Change the summary by hand")
        self.b_send = button("Send to Meetily", "primary", self.send,
                             tip="Write this summary into Meetily (its own is backed up first)")
        self.showing_backup = False
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Consolas", 10))
        self.editor.setPlaceholderText("# Title\n\n**Summary**\n\n…")
        self.editor.textChanged.connect(self._preview_edit)
        self.preview = QTextBrowser()
        self.edit_note = label("Markdown on the left, preview on the right. The first line "
                               "“# Title” becomes the meeting's name in Meetily if it still has a "
                               "default name.", "small", wrap=True)
        ed_split = QSplitter()
        ed_split.addWidget(self.editor)
        ed_split.addWidget(self.preview)
        ed_split.setSizes([500, 400])
        edit_box = QWidget()
        edit_box.setLayout(vbox(self.edit_note, ed_split, hbox(
            None, button("Cancel", "ghost", self.cancel_edit),
            button("Save", on_click=lambda: self.save_edit()),
            button("Save and send to Meetily", "primary", self.save_and_send)), spacing=6))
        edit_box.layout().setStretch(1, 1)             # the editor takes the height, not the hint
        self.sum_stack = QStackedWidget()
        self.sum_stack.addWidget(self.summary)
        self.sum_stack.addWidget(edit_box)
        self.sum_bar = hbox(self.sum_meta, None, self.b_backup, self.b_copy, self.b_edit, self.b_send)
        self.sum_bar.setStretch(0, 1)
        sum_tab = QWidget()
        sum_tab.setLayout(vbox(self.sum_bar, self.sum_stack, margins=(8, 8, 8, 8)))

        # Transcript: the speech, by speaker.
        self.transcript = QTextBrowser()
        self.tr_status = label("", "small")
        self._tr_lines: list[dict] = []
        tr_tab = QWidget()
        tr_tab.setLayout(vbox(hbox(self.tr_status, None,
                                   button("Copy", "ghost", self.copy_transcript),
                                   button("Save as text…", "ghost", self.save_transcript),
                                   button("Reload", "ghost", lambda: self._tab_transcript())),
                              self.transcript, margins=(8, 8, 8, 8)))

        self.screens = QListWidget()
        self.screens.setViewMode(QListView.ViewMode.IconMode)
        self.screens.setIconSize(QSize(200, 120))
        self.screens.setGridSize(QSize(220, 168))
        self.screens.setResizeMode(QListView.ResizeMode.Adjust)
        self.screens.setMovement(QListView.Movement.Static)
        self.screens.setWordWrap(True)
        self.screens.currentItemChanged.connect(self._show_screen)
        self.screens.itemDoubleClicked.connect(lambda _it: self.view_screen())
        self.screen_img = label("")
        self.screen_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screen_img.setCursor(Qt.CursorShape.PointingHandCursor)
        self.screen_img.mousePressEvent = lambda _e: self.view_screen()
        self.screen_text = QPlainTextEdit()
        self.screen_text.setReadOnly(True)
        self.sc_status = label("Double-click a screen to see it full size (← → to step through).", "small")
        side = QWidget()
        side.setLayout(vbox(self.screen_img, self.screen_text))
        sc_split = QSplitter(Qt.Orientation.Vertical)
        sc_split.addWidget(self.screens)
        sc_split.addWidget(side)
        sc_split.setSizes([300, 300])
        sc_tab = QWidget()
        sc_tab.setLayout(vbox(hbox(self.sc_status, None,
                                   button("View full size", on_click=self.view_screen),
                                   button("Describe screens again", on_click=self.redescribe,
                                          tip="Ask the vision model about every screen again (uses the "
                                              "screen prompt in Settings), then you can regenerate"),
                                   button("Open images folder", "ghost", self.open_images)),
                              sc_split, margins=(8, 8, 8, 8)))

        self.timeline = QTextBrowser()
        self.speakers = QTableWidget(0, 3)
        self.speakers.setHorizontalHeaderLabels(["Speaker", "Says (samples)", "Name"])
        self.speakers.verticalHeader().setVisible(False)
        sh = self.speakers.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        sh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        sh.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.speakers.setColumnWidth(2, 180)
        self.speakers.setWordWrap(True)
        self.sp_status = label("Meetily's API gives cluster ids, not the names you set in Meetily; "
                               "name them here, then Generate summary.", "small", wrap=True)
        sp_tab = QWidget()
        sp_tab.setLayout(vbox(self.sp_status, self.speakers,
                              hbox(None, button("Save names", "primary", self.save_speakers)),
                              margins=(8, 8, 8, 8)))
        self.model_input = QPlainTextEdit()
        self.model_input.setReadOnly(True)
        self.model_input.setProperty("role", "log")

        self.tabs = QTabWidget()
        self.tabs.addTab(sum_tab, "Summary")
        self.tabs.addTab(tr_tab, "Transcript")
        self.tabs.addTab(sc_tab, "Screens")
        self.tabs.addTab(self.timeline, "Timeline")
        self.tabs.addTab(sp_tab, "Speakers")
        self.tabs.addTab(self.model_input, "Model input")
        self.tabs.currentChanged.connect(lambda _i: self._load_tab())

        self.detail = QWidget()
        head = hbox(vbox(self.d_title, self.d_meta, spacing=2), 12, self.d_chips)
        head.setStretch(0, 1)
        self.detail.setLayout(vbox(head, actions, link, self.banner, self.tabs, spacing=10))
        self.detail.layout().setStretch(4, 1)
        self.placeholder = label("Select a meeting.", "muted")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right = QWidget()
        right.setLayout(vbox(self.detail, self.placeholder))

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([340, 800])
        split.setChildrenCollapsible(False)
        self.setLayout(vbox(page_header("Meetings", "Everything captured so far. Summaries, screens "
                                        "and speaker names live in main/data/runs.",
                                        button("Refresh", "ghost", self.refresh)),
                            split, spacing=14, margins=(22, 18, 22, 18)))
        self.detail.setVisible(False)
        bridge.job_finished.connect(self._job_finished)
        self._loaded_tabs: set[int] = set()
        self._edit_base: str | None = None

    def showEvent(self, e):
        super().showEvent(e)
        if not self.editing():
            self.refresh()

    # ---------------------------------------------------------------- list
    def refresh(self) -> None:
        runs = self.store.runs()
        self.table.blockSignals(True)
        self.table.setRowCount(len(runs))
        sel_row = None
        for r, run in enumerate(runs):
            title = run.get("meeting_title") or run["source"]
            it0 = QTableWidgetItem(when(run["created"]))
            it0.setData(Qt.ItemDataRole.UserRole, run["id"])
            it1 = QTableWidgetItem(title)
            it1.setToolTip(f"{title}\nrun {run['id']}\nmeeting {run.get('meeting_id') or '-'}")
            self.table.setItem(r, 0, it0)
            self.table.setItem(r, 1, it1)
            if run["pending"]:
                state, kind = "Needs you", "warn"
            elif run["published"]:
                state, kind = "In Meetily", "ok"
            elif run["has_summary"]:
                state, kind = "Summary", "info"
            elif run["extracted"]:
                state, kind = f"{run['screens']} screens", "muted"
            else:
                state, kind = "No screens", "muted"
            self.table.setItem(r, 2, colored_item(state, kind))
            self.table.setRowHidden(r, not self._match(run, self.search.text()))
            if run["id"] == self.run_id:
                sel_row = r
        self.table.blockSignals(False)
        self._runs = runs
        if sel_row is not None:
            self.table.selectRow(sel_row)
        if self.run_id:
            self.load(self.run_id)
        self._load_meetings()

    @staticmethod
    def _match(run: dict, q: str) -> bool:
        q = q.strip().lower()
        return not q or any(q in str(run.get(k) or "").lower()
                            for k in ("meeting_title", "source", "id", "meeting_id"))

    def _filter(self, q: str) -> None:
        for r, run in enumerate(getattr(self, "_runs", [])):
            self.table.setRowHidden(r, not self._match(run, q))

    def _on_select(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            rid = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
            if rid != self.run_id:
                if self._leave_edit():
                    self.load(rid)
                else:                                   # stay on the run being edited
                    self.table.blockSignals(True)
                    for r in range(self.table.rowCount()):
                        if self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == self.run_id:
                            self.table.selectRow(r)
                    self.table.blockSignals(False)

    def select_run(self, run_id: str, tab: str | None = None, edit: bool = False) -> None:
        """Show a run, optionally on a tab ("summary", "transcript", "screens", ...)."""
        if not self._leave_edit():
            return
        self.run_id = run_id
        self.refresh()
        if tab in TABS:
            self.tabs.setCurrentIndex(TABS.index(tab))
        if edit and self.detail_data is not None:
            self.tabs.setCurrentIndex(0)
            self.start_edit()

    def _job_finished(self, jid: str, _status: str) -> None:
        job = self.ctl.queue.get(jid)
        if self.editing():
            return                                      # never reload under the user's edits
        if self.isVisible():
            self.refresh()
        elif job and job["run"] == self.run_id:
            self._loaded_tabs.clear()

    def _load_meetings(self) -> None:
        def got(res):
            self.meetings = res
            self._fill_meeting_combo()
        def failed(_e):
            self.meetings = []
            self._fill_meeting_combo()
        run_async(lambda: (self.ctl.client_factory(timeout=4).list_meetings(50) or {}).get("meetings") or [],
                  got, failed)

    def _fill_meeting_combo(self) -> None:
        cur = (self.detail_data or {}).get("meta", {}).get("meeting_id") or ""
        self.meeting.blockSignals(True)
        self.meeting.clear()
        self.meeting.addItem("— none —", "")
        ids = set()
        for m in self.meetings:
            mid = m.get("id")
            ids.add(mid)
            self.meeting.addItem(f"{m.get('title') or mid} · {(m.get('created_at') or '')[:10]}", mid)
        self.meeting.addItem("Demo transcript (fixture)", "fixture")
        if cur and cur not in ids and cur != "fixture":
            self.meeting.addItem(cur, cur)
        idx = self.meeting.findData(cur)
        self.meeting.setCurrentIndex(max(0, idx))
        self.meeting.blockSignals(False)

    def _meeting_id(self) -> str | None:
        data = self.meeting.currentData()
        text = self.meeting.currentText().strip()
        if data is None and text.startswith("meeting-"):
            return text                                        # typed by hand
        return data or None

    # ---------------------------------------------------------------- detail
    def load(self, run_id: str) -> None:
        self.run_id = run_id
        try:
            d = self.detail_data = self.store.detail(run_id)
        except (FileNotFoundError, ValueError):
            self.run_id = self.detail_data = None
            self.detail.setVisible(False)
            self.placeholder.setVisible(True)
            return
        self.detail.setVisible(True)
        self.placeholder.setVisible(False)
        m = d["meta"]
        self.d_title.setText(m.get("meeting_title") or m.get("source_name") or run_id)
        bits = [f"run {run_id}", when(m.get("created"))]
        if d.get("duration"):
            bits.append(f"{mmss(d['duration'])} of screen")
        n = len(d["screenshots"])
        bits.append(f"{n} screens ({sum(1 for s in d['screenshots'] if s.get('type') == 'diagram')} diagrams)")
        if m.get("window"):
            bits.append(f"window: {m['window'].get('process')}")
        self.d_meta.setText(" · ".join(b for b in bits if b))
        while self.d_chips.count():
            w = self.d_chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        if d["published"]:
            self.d_chips.addWidget(chip("Written to Meetily", "ok"))
        if d["kept"]:
            self.d_chips.addWidget(chip("Kept Meetily's", "muted"))
        jobs = [j for j in self.ctl.queue.for_run(run_id) if j["status"] in ("queued", "running", "capturing")]
        if jobs:
            self.d_chips.addWidget(chip(f"Job {jobs[0]['status']}", "info"))
        self._fill_meeting_combo()
        self.offset.setValue(float(m.get("offset") or 0.0))
        self.offset_basis.setText("set by you" if m.get("offset_manual") else
                                  "worked out automatically when the summary is made")
        has_summary = bool(d["summary"])
        real_meeting = bool(m.get("meeting_id")) and m.get("meeting_id") != "fixture"
        self.b_generate.setEnabled(not jobs)            # no screens: transcript-only summary
        self.b_describe.setEnabled(d["extracted"] and not jobs)
        self.b_publish.setEnabled(has_summary and real_meeting)
        self.b_send.setEnabled(has_summary and real_meeting)
        self.b_send.setToolTip("Write this summary into Meetily (its own is backed up first)"
                               if real_meeting else "Link a Meetily meeting above first")
        self.b_edit.setText("Edit" if has_summary else "Write a summary")
        self.b_edit.setEnabled(not any(j["status"] == "running" and
                                       any(s["name"] == "summarize" and s["status"] == "running"
                                           for s in j["stages"]) for j in jobs))
        self.banner.setVisible(bool(d["pending"]))
        if d["pending"]:
            self.banner_text.setText("Meetily already has a different summary for this meeting. "
                                     "Replace it with ours? Meetily's is saved to backups/ first.")
        self.showing_backup = False
        self.b_backup.setVisible(bool(d["backups"]))
        self.b_backup.setText("Show Meetily's backup")
        self._loaded_tabs = set()
        self._load_tab()

    def _load_tab(self) -> None:
        if self.detail_data is None:
            return
        i = self.tabs.currentIndex()
        if i in self._loaded_tabs:
            return
        self._loaded_tabs.add(i)
        getattr(self, f"_tab_{TABS[i]}")()

    def _tab_summary(self) -> None:
        d = self.detail_data
        self.sum_stack.setCurrentIndex(0)
        self._set_summary_bar(editing=False)
        if d["summary"]:
            self.summary.setMarkdown(d["summary"])
            self.summary.verticalScrollBar().setValue(0)
            sm = d["summary_meta"] or {}
            pub = d["published"] or {}
            self.sum_meta.setText(" · ".join(x for x in (
                f"by {sm.get('model')}" if sm.get("model") else "",
                f"{sm.get('eval_count')} tokens in {sm.get('seconds')}s" if sm.get("eval_count") else "",
                f"for {sm.get('meeting_id')}" if sm.get("meeting_id") else "",
                "edited by you " + (sm.get("edited_at") or "")[:16].replace("T", " ") if sm.get("edited") else "",
                "written to Meetily " + (pub.get("at") or "")[:16].replace("T", " ") if pub else "") if x))
        else:
            self.summary.setPlainText("No summary yet. Link a Meetily meeting above and press "
                                      "Generate summary, or write one with “Write a summary”.")
            self.sum_meta.setText("")

    def _tab_transcript(self) -> None:
        d = self.detail_data
        mid = d["meta"].get("meeting_id")
        self._tr_lines = []
        if not mid:
            self.transcript.setPlainText("Link a Meetily meeting above to see its transcript.")
            self.tr_status.setText("")
            return
        self.transcript.setPlainText("Loading the transcript from Meetily…")

        def show(lines):
            self._tr_lines = lines
            html, prev = [], None
            for x in lines:
                who = x.get("who") or ""
                if who and who != prev:                     # speaker changes: a new block
                    html.append(f"<p style='margin:10px 0 2px 0'><b>{_esc(who)}</b></p>")
                prev = who or prev
                html.append(f"<p style='margin:0 0 2px 0'><span style='color:gray'>{mmss(x['t'])}"
                            f"</span>&nbsp;&nbsp;{_esc(x['text'])}</p>")
            self.transcript.setHtml("".join(html) or "<p>The transcript is empty.</p>")
            speakers = len({x.get('who') for x in lines if x.get('who')})
            end = max((x.get("end") or x["t"] for x in lines), default=0)
            self.tr_status.setText(f"{len(lines)} lines · {mmss(end)}"
                                   + (f" · {speakers} speakers (rename them on the Speakers tab)"
                                      if speakers else ""))
        run_async(lambda: self.store.transcript_lines(mid), show,
                  lambda e: (self.transcript.setPlainText(f"Couldn't load the transcript: {e}"),
                             self.tr_status.setText("")))

    def _transcript_text(self) -> str:
        return "\n".join(f"[{mmss(x['t'])}] {x['who'] + ': ' if x.get('who') else ''}{x['text']}"
                         for x in self._tr_lines)

    def copy_transcript(self) -> None:
        if self._tr_lines:
            QApplication.clipboard().setText(self._transcript_text())

    def save_transcript(self) -> None:
        if not self._tr_lines:
            return
        name = (self.detail_data["meta"].get("meeting_title") or self.run_id).strip()
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)[:60] or "transcript"
        path, _ = QFileDialog.getSaveFileName(self, "Save transcript", f"{safe}.txt", "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._transcript_text() + "\n")

    def _tab_screens(self) -> None:
        self.screens.clear()
        d = self.detail_data
        root = self.store.run_dir(d["id"])
        offset = float(d["meta"].get("offset") or 0)
        for s in d["screenshots"]:
            pm = QPixmap(str(root / s["image"]))
            icon = QIcon(pm.scaled(200, 120, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)) if not pm.isNull() else QIcon()
            kind = {"diagram": "diagram" + (" ✓" if s.get("description") else ""),
                    "picture": "picture (not in summary)"}.get(s.get("type"), "slide")
            it = QListWidgetItem(icon, f"#{s['id']} · {mmss(s['start'] + offset)}–{mmss(s['end'] + offset)}\n{kind}")
            it.setData(Qt.ItemDataRole.UserRole, s)
            self.screens.addItem(it)
        if not d["screenshots"]:
            self.screen_text.setPlainText("No screens for this meeting (no window was captured).")
        else:
            self.screen_text.clear()
        self.sc_status.setText(f"{len(d['screenshots'])} screens · double-click one to see it full "
                               f"size (← → to step through)" if d["screenshots"] else "")
        self.screen_img.clear()
        if d["screenshots"]:
            self.screens.setCurrentRow(0)

    def redescribe(self) -> None:
        d = self.detail_data
        if not d or not d["screenshots"]:
            return
        r = QMessageBox.question(self, "Describe screens again",
                                 f"Describe all {len(d['screenshots'])} screens again with the current "
                                 "screen prompt?\n\nYes = describe, then write a new summary\n"
                                 "No = only describe",
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                 | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Cancel:
            return
        job = self.ctl.redescribe(self.run_id, summarize=r == QMessageBox.StandardButton.Yes)
        self.window().show_live(job["id"])

    def view_screen(self) -> None:
        d = self.detail_data
        if not d or not d["screenshots"]:
            return
        ImageViewer(self, self.store.run_dir(d["id"]), d["screenshots"],
                    max(0, self.screens.currentRow()), float(d["meta"].get("offset") or 0)).exec()

    def open_images(self) -> None:
        if self.run_id:
            d = self.store.run_dir(self.run_id) / "images"
            open_path(d if d.is_dir() else d.parent)

    def _show_screen(self, item, _prev=None) -> None:
        if item is None:
            return
        s = item.data(Qt.ItemDataRole.UserRole)
        pm = QPixmap(str(self.store.run_dir(self.run_id) / s["image"]))
        if not pm.isNull():
            self.screen_img.setPixmap(pm.scaledToHeight(min(260, pm.height()),
                                                        Qt.TransformationMode.SmoothTransformation))
        parts = []
        if s.get("description"):
            parts.append(f"Description ({s.get('described_by') or 'VLM'}):\n{s['description']}")
        parts.append("OCR text:\n" + (s.get("text") or "(none)"))
        parts.append(f"merged from {s.get('merged_from', 1)} capture(s)")
        self.screen_text.setPlainText("\n\n".join(parts))

    def _tab_timeline(self) -> None:
        d = self.detail_data
        mid = d["meta"].get("meeting_id")
        if not mid:
            self.timeline.setPlainText("Link a Meetily meeting to see speech and screens together.")
            return
        self.timeline.setPlainText("Loading the transcript…")
        offset = float(d["meta"].get("offset") or 0)

        def show(tl):
            html = []
            for x in tl["items"]:
                t = mmss(x["t"])
                if x["kind"] == "speech":
                    who = f"<b>{x['who']}:</b> " if x.get("who") else ""
                    html.append(f"<p><span style='color:gray'>{t}</span> {who}{_esc(x['text'])}</p>")
                else:
                    html.append(f"<p style='margin-left:12px'><span style='color:gray'>{t}</span> "
                                f"<i>[screen #{x['id']} {x['type']}]</i> {_esc(x['text'][:300])}</p>")
            self.timeline.setHtml("".join(html) or "<p>Nothing to show.</p>")
        run_async(lambda: self.store.timeline(d["id"], mid, offset), show,
                  lambda e: self.timeline.setPlainText(f"Couldn't load the transcript: {e}"))

    def _tab_speakers(self) -> None:
        mid = (self.detail_data["meta"] or {}).get("meeting_id")
        self.speakers.setRowCount(0)
        if not mid:
            self.sp_status.setText("Link a Meetily meeting first.")
            return

        def show(sp):
            self.speakers.setRowCount(len(sp))
            for r, x in enumerate(sp):
                a = QTableWidgetItem(f"Speaker {x['id']}\n{x['segments']} lines")
                a.setFlags(a.flags() & ~Qt.ItemFlag.ItemIsEditable)
                a.setData(Qt.ItemDataRole.UserRole, x["id"])
                b = QTableWidgetItem("\n".join(f"{mmss(s['time'])} {s['text']}" for s in x["samples"]))
                b.setFlags(b.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.speakers.setItem(r, 0, a)
                self.speakers.setItem(r, 1, b)
                self.speakers.setItem(r, 2, QTableWidgetItem(x.get("name") or ""))
            self.speakers.resizeRowsToContents()
            self.sp_status.setText("Meetily's API gives cluster ids, not the names you set in Meetily; "
                                   "name them here (double-click a name), save, then Generate summary."
                                   if sp else "No diarized speakers in this transcript.")
        run_async(lambda: self.store.speakers(mid), show,
                  lambda e: self.sp_status.setText(f"Couldn't load speakers: {e}"))

    def _tab_input(self) -> None:
        d = self.detail_data
        mid = d["meta"].get("meeting_id")
        offset = float(d["meta"].get("offset") or 0)

        def work():
            segs = (self.store.transcript(mid) or {}).get("segments") or [] if mid else []
            text = build_input(segs, d["screenshots"], offset, self.store.speaker_names(mid))
            return f"=== SYSTEM ===\n{build_system_prompt()}\n\n=== USER ===\n<transcript_chunks>\n{text}\n</transcript_chunks>"
        self.model_input.setPlainText("Building…")
        run_async(work, self.model_input.setPlainText,
                  lambda e: self.model_input.setPlainText(f"Couldn't build the input: {e}"))

    # ---------------------------------------------------------------- actions
    def save_link(self) -> dict | None:
        if not self.run_id:
            return None
        mid = self._meeting_id()
        m = self.store.update_meta(self.run_id, {"meeting_id": mid, "offset": self.offset.value(),
                                                 "offset_manual": True})
        self.load(self.run_id)
        return m

    def suggest_offset(self) -> None:
        mid = self._meeting_id()
        if not self.run_id or not mid:
            return
        rid = self.run_id

        def show(res):
            if res.get("offset") is None:
                self.offset_basis.setText(f"Can't suggest an offset: {res.get('basis')}")
                return
            self.offset.setValue(res["offset"])
            self.offset_basis.setText(f"suggested from {res['basis']}; press Save to keep it")
        run_async(lambda: self.store.suggest_offset(rid, mid), show,
                  lambda e: self.offset_basis.setText(f"Couldn't suggest: {e}"))

    def regenerate(self, describe: bool) -> None:
        mid = self._meeting_id()
        if not mid:
            error_box(self, "Generate summary", "Pick the Meetily meeting this capture belongs to first.")
            return
        m = self.store.meta(self.run_id)
        manual = m.get("offset_manual") or abs(self.offset.value() - float(m.get("offset") or 0)) > 0.01
        job = self.ctl.regenerate(self.run_id, mid, self.offset.value() if manual else None, describe)
        self.load(self.run_id)
        self.window().show_live(job["id"])

    # ---------------------------------------------------------------- edit + send
    def editing(self) -> bool:
        return self.sum_stack.currentIndex() == 1

    def _dirty(self) -> bool:
        return self.editing() and self.editor.toPlainText().strip() != (self._edit_base or "").strip()

    def _set_summary_bar(self, editing: bool) -> None:
        for b in (self.b_edit, self.b_send, self.b_backup, self.b_copy):
            b.setVisible(not editing)
        if not editing:
            self.b_backup.setVisible(bool((self.detail_data or {}).get("backups")))
        self.sum_meta.setText("Editing the summary" if editing else self.sum_meta.text())

    def start_edit(self) -> None:
        d = self.detail_data
        if not d:
            return
        if self.showing_backup:
            self.toggle_backup()
        self._edit_base = d.get("summary") or ""
        self.editor.setPlainText(self._edit_base or f"# {d['meta'].get('meeting_title') or 'Meeting'}\n\n"
                                 "**Summary**\n\n\n\n**Key Decisions**\n\n\n\n**Action Items**\n\n"
                                 "| **Owner** | Task | Due | Reference Transcript Segment | "
                                 "Segment Time stamp |\n| --- | --- | --- | --- | --- |\n\n"
                                 "**Discussion Highlights**\n\n")
        self.tabs.setCurrentIndex(0)
        self.sum_stack.setCurrentIndex(1)
        self._set_summary_bar(editing=True)
        self._preview_edit()
        self.editor.setFocus()

    def _preview_edit(self) -> None:
        if self.editing():
            bar = self.preview.verticalScrollBar().value()
            self.preview.setMarkdown(self.editor.toPlainText())
            self.preview.verticalScrollBar().setValue(bar)

    def cancel_edit(self) -> None:
        if self._dirty() and not confirm(self, "Discard changes", "Throw away your changes to the summary?"):
            return
        self._edit_base = None
        self._loaded_tabs.discard(0)
        self.sum_stack.setCurrentIndex(0)
        self._tab_summary()

    def _leave_edit(self) -> bool:
        """Before switching runs: ask about unsaved edits. False = stay."""
        if not self.editing():
            return True
        if self._dirty():
            r = QMessageBox.question(self, "Unsaved summary", "Save your changes to the summary first?",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                     | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return False
            if r == QMessageBox.StandardButton.Save and not self.save_edit():
                return False
        self.sum_stack.setCurrentIndex(0)
        self._edit_base = None
        return True

    def save_edit(self) -> bool:
        try:
            self.ctl.save_summary(self.run_id, self.editor.toPlainText())
        except ValueError as e:
            error_box(self, "Save summary", e)
            return False
        self._edit_base = None
        self.sum_stack.setCurrentIndex(0)
        self.load(self.run_id)
        self.refresh()
        return True

    def save_and_send(self) -> None:
        if self.save_edit():
            self.send()

    def send(self) -> None:
        """Send the summary to Meetily now. If Meetily already has a different one, ask first."""
        d = self.detail_data or {}
        mid = d.get("meta", {}).get("meeting_id")
        if not mid or mid == "fixture":
            error_box(self, "Send to Meetily", "Link a real Meetily meeting above first.")
            return
        if self.editing():
            if not self.save_edit():
                return
            d = self.detail_data
        rid = self.run_id
        self.b_send.setEnabled(False)
        self.b_publish.setEnabled(False)
        self.sum_meta.setText("Sending to Meetily…")

        def done(res):
            self.b_send.setEnabled(True)
            self.b_publish.setEnabled(True)
            if res["status"] == "exists":
                if confirm(self, "Replace Meetily's summary?",
                           f"Meetily already has a different summary for this meeting "
                           f"({res['chars']} characters). Replace it with this one?\n\n"
                           "Meetily's version is saved to this run's backups first."):
                    self._send(rid, mid, replace=True)
                else:
                    self._loaded_tabs.discard(0)
                    self._tab_summary()
                return
            self._sent(rid, res)

        def failed(e):
            self.b_send.setEnabled(True)
            self.b_publish.setEnabled(True)
            self._loaded_tabs.discard(0)
            self._tab_summary()
            error_box(self, "Send to Meetily", e)
        run_async(lambda: self.ctl.send_summary(rid, mid), done, failed)

    def _send(self, rid: str, mid: str, replace: bool) -> None:
        self.sum_meta.setText("Sending to Meetily…")
        run_async(lambda: self.ctl.send_summary(rid, mid, replace=replace),
                  lambda res: self._sent(rid, res), lambda e: error_box(self, "Send to Meetily", e))

    def _sent(self, rid: str, res: dict) -> None:
        if rid == self.run_id:
            self.load(rid)
            self.refresh()
        msg = ("Meetily already shows this summary." if res["status"] == "same" else
               "Sent. The summary is in Meetily now" + (" (its previous one is in backups/)."
                                                         if (res.get("record") or {}).get("backup") else "."))
        self.sum_meta.setText(msg)
        QMessageBox.information(self, "Send to Meetily", msg)

    def decide(self, action: str) -> None:
        rid = self.run_id
        run_async(lambda: self.ctl.resolve_overwrite(rid, action), lambda _r: self.load(rid),
                  lambda e: error_box(self, "Decision", e))

    def save_speakers(self) -> None:
        mid = (self.detail_data or {}).get("meta", {}).get("meeting_id")
        if not mid:
            return
        names = {}
        for r in range(self.speakers.rowCount()):
            key = self.speakers.item(r, 0).data(Qt.ItemDataRole.UserRole)
            names[key] = (self.speakers.item(r, 2).text() if self.speakers.item(r, 2) else "").strip()
        saved = self.store.save_speakers(mid, names)
        self.sp_status.setText(f"Saved {len(saved)} name(s). Generate summary to use them.")
        for t in ("transcript", "timeline", "input"):            # they show the names too
            self._loaded_tabs.discard(TABS.index(t))

    def copy_summary(self) -> None:
        d = self.detail_data or {}
        if self.showing_backup:
            QApplication.clipboard().setText(self.summary.toPlainText())
        elif d.get("summary"):
            QApplication.clipboard().setText(d["summary"])

    def toggle_backup(self) -> None:
        d = self.detail_data
        if not d or not d["backups"]:
            return
        self.showing_backup = not self.showing_backup
        if self.showing_backup:
            raw = read_json(self.store.run_dir(d["id"]) / "backups" / d["backups"][-1], {}) or {}
            self.summary.setMarkdown(summary_text(raw.get("result")) or "(empty)")
            self.sum_meta.setText(f"Meetily's summary before ours was written ({d['backups'][-1]})")
            self.b_backup.setText("Show ours")
        else:
            self._tab_summary()
            self.b_backup.setText("Show Meetily's backup")

    def open_folder(self) -> None:
        if self.run_id:
            open_path(self.store.run_dir(self.run_id))

    def delete(self) -> None:
        if not self.run_id:
            return
        if any(j["status"] in ("running", "capturing") for j in self.ctl.queue.for_run(self.run_id)):
            error_box(self, "Delete", "This meeting is still being captured or processed.")
            return
        if not confirm(self, "Delete", "Delete this run's screens and summary from this app? "
                       "(Meetily isn't touched.)"):
            return
        for j in self.ctl.queue.for_run(self.run_id):
            try:
                self.ctl.queue.remove(j["id"])
            except ValueError:
                pass
        self.store.delete(self.run_id)
        self.run_id = self.detail_data = None
        self.detail.setVisible(False)
        self.placeholder.setVisible(True)
        self.refresh()


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
