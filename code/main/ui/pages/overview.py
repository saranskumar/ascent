"""Overview: is everything connected (Meetily, write key, webhooks, local model), the one-time
setup guide with live checkmarks, decisions waiting for you, and every Meetily event received."""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QGridLayout, QHeaderView, QTableWidget,
                             QTableWidgetItem, QWidget)

from ui.common import (button, card, chip, colored_item, error_box, hbox, label, page_header, run_async,
                       set_chip, vbox)

SUB_CHIP = {"active": ("Receiving events", "ok"), "pending": ("Waiting for approval", "warn"),
            "registered": ("Registered", "info"), "offline": ("Meetily offline", "bad"),
            "disabled": ("Webhooks off", "warn"), "local_target_needed": ("Target not allowed", "warn"),
            "starting": ("Connecting…", "muted"), "error": ("Error", "bad")}
EVENT_CHIP = {"done": "ok", "failed": "bad", "offline": "warn", "running": "info", "queued": "muted"}


class StatusCard:
    def __init__(self, title: str):
        self.chip = chip("…")
        self.text = label("", "small", wrap=True, select=True)
        self.widget = card(vbox(hbox(label(title, "h2"), None, self.chip), self.text, None, spacing=6))
        self.widget.setMinimumHeight(96)

    def set(self, chip_text: str, kind: str, text: str) -> None:
        set_chip(self.chip, chip_text, kind)
        self.text.setText(text)


class Step:
    def __init__(self, grid: QGridLayout, row: int, title: str, how: str):
        self.mark = chip("…")
        self.mark.setFixedWidth(26)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = label(title)
        self.how = label(how, "small", wrap=True, select=True)
        grid.addWidget(self.mark, row, 0, Qt.AlignmentFlag.AlignTop)
        grid.addLayout(vbox(self.title, self.how, spacing=1), row, 1)

    def set(self, ok: bool | None) -> None:
        set_chip(self.mark, "✓" if ok else ("…" if ok is None else "✕"),
                 "ok" if ok else ("muted" if ok is None else "warn"))


class OverviewPage(QWidget):
    open_run = pyqtSignal(str)

    def __init__(self, ctl, bridge):
        super().__init__()
        self.setObjectName("page")
        self.ctl = ctl
        self.busy = False

        self.c_meetily = StatusCard("Meetily")
        self.c_write = StatusCard("Write-back key")
        self.c_model = StatusCard("Local model")
        self.c_auto = StatusCard("Automation")
        cards = QGridLayout()
        cards.setSpacing(12)
        for i, c in enumerate((self.c_meetily, self.c_write, self.c_model, self.c_auto)):
            cards.addWidget(c.widget, i // 2, i % 2)

        # decisions
        self.dec_box = vbox(spacing=6)
        self.dec_card = card(vbox(label("Waiting for you", "h2"), self.dec_box))
        self.dec_card.setProperty("banner", "warn")
        self.dec_card.setVisible(False)

        # guide
        port = ctl.settings["webhook_port"]
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        g.setColumnStretch(1, 1)
        self.steps = [
            Step(g, 0, "Meetily Pro is running with its local API on",
                 "Meetily > Settings > Integrations > turn on “Allow the CLI on this computer” "
                 "(gateway on 127.0.0.1:8420)."),
            Step(g, 1, "Outgoing webhooks are on",
                 "Meetily > Settings > Integrations > Advanced > Outgoing (webhooks): on."),
            Step(g, 2, f"127.0.0.1:{port} is an allowed local target",
                 f"Same place: add 127.0.0.1:{port} under Local targets. (The test version used 8765; "
                 f"this app uses {port} so both can be installed.)"),
            Step(g, 3, "This app's webhook is approved",
                 "Meetily shows “Waiting for you” (or Advanced > Destinations): Allow it."),
            Step(g, 4, "A write key is set (to put summaries into Meetily)",
                 "Meetily > Settings > Integrations > Apps & scripts > Create key with the `write` "
                 "scope, switch its Allow toggle on, and put it in main/.env as MEETILY_PRO_TOKEN=… "
                 "(restart the app)."),
            Step(g, 5, "Ollama is running",
                 "Install Ollama and keep it running (it starts with Windows by default)."),
            Step(g, 6, "The model is installed",
                 f"In a terminal: ollama pull {ctl.settings['model']}  (pick the model in Settings)."),
        ]
        self.test_btn = button("Send test event", on_click=self.send_test,
                               tip="Ask Meetily to send a test webhook; it shows up below.")
        guide = card(vbox(hbox(label("Connection guide", "h2"), None, self.test_btn), g, spacing=10))

        # events
        self.events = QTableWidget(0, 5)
        self.events.setHorizontalHeaderLabels(["Time", "Event", "Meeting", "Status", "What happened"])
        self.events.verticalHeader().setVisible(False)
        self.events.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.events.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.events.setWordWrap(False)
        hh = self.events.horizontalHeader()
        for i, mode in enumerate((QHeaderView.ResizeMode.ResizeToContents,) * 4
                                 + (QHeaderView.ResizeMode.Stretch,)):
            hh.setSectionResizeMode(i, mode)
        self.events.setMinimumHeight(220)
        ev_card = card(vbox(hbox(label("Meetily events", "h2"), None,
                                 label("hover a row for its full log", "small")), self.events))

        refresh = button("Refresh", "ghost", self.refresh)
        body = QWidget()
        body.setLayout(vbox(page_header("Overview", "Screen-aware meeting summaries, fully on this "
                                        "computer: Meetily's transcript + what was on screen, "
                                        "summarised by a local model.", refresh),
                            self.dec_card, cards, guide, ev_card, None, spacing=14,
                            margins=(22, 18, 22, 18)))
        from PyQt6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLayout(vbox(scroll))

        bridge.automation.connect(self._refresh_events)
        bridge.job_finished.connect(lambda *_: self._refresh_decisions())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(6000)

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()

    # ---------------------------------------------------------------- refresh
    def refresh(self) -> None:
        if not self.isVisible() or self.busy:
            return
        self.busy = True
        run_async(self.ctl.status, self._show, lambda e: setattr(self, "busy", False))
        self._refresh_events()
        self._refresh_decisions()

    def _show(self, s: dict) -> None:
        self.busy = False
        m = s["meetily"]
        if m.get("online"):
            rec = m.get("recording") or {}
            who = (m.get("whoami") or {}).get("consumer") or (m.get("whoami") or {}).get("name") or ""
            state = rec.get("state") or ("recording" if rec.get("is_recording") else "idle")
            self.c_meetily.set("Connected", "ok", f"Recording: {state}"
                               + (f" · meeting {rec.get('active_meeting_id')}" if rec.get("active_meeting_id") else "")
                               + (f"\nAPI client: {who}" if who else ""))
        else:
            self.c_meetily.set("Offline", "bad", m.get("error") or "Can't reach Meetily.")
        w = s["write"]
        if w.get("ok"):
            self.c_write.set("Ready", "ok", "Summaries can be written into Meetily (Meetily's own is "
                             "backed up first).")
        elif not s["write_key_set"]:
            self.c_write.set("Not set", "warn", "No MEETILY_PRO_TOKEN in main/.env: summaries stay in "
                             "this app (see the guide below).")
        else:
            self.c_write.set("Problem", "bad", w.get("message") or w.get("reason") or "")
        o = s["ollama"]
        dev = "CPU" if self.ctl.settings["device"] == "cpu" else "GPU"
        if not o.get("online"):
            self.c_model.set("Ollama offline", "bad", o.get("error", ""))
        elif not o.get("installed"):
            self.c_model.set("Not installed", "warn", f"{o['model']} isn't in Ollama. Run: ollama pull "
                             f"{o['model']}, or pick another model in Settings.")
        else:
            loaded = "loaded in memory" if o["model"] in (o.get("loaded") or []) else "loads on first use"
            self.c_model.set("Ready", "ok", f"{o['model']} on {dev} · {loaded}\n"
                             f"{len(o.get('models') or [])} model(s) installed")
        sub = s["subscription"]
        text, kind = SUB_CHIP.get(sub.get("state"), (sub.get("state") or "?", "muted"))
        if s.get("http_error"):
            text, kind = "Not listening", "bad"
        st = self.ctl.settings.all()
        self.c_auto.set(text, kind, (s.get("http_error") or sub.get("message") or "")
                        + f"\nListening on {s['webhook_url']}\nCapture "
                        f"{'on' if st['auto_capture'] else 'off'} · auto summary "
                        f"{'on' if st['auto_summarize'] else 'off'} · write-back "
                        f"{'on' if st['auto_publish'] else 'off'}")
        state = sub.get("state")
        online = bool(m.get("online"))
        # Only Meetily's answer to our subscription tells which webhook step is missing.
        known = online and state in ("active", "pending", "registered", "disabled", "local_target_needed")
        self.steps[0].set(online)
        self.steps[1].set(None if not known else state != "disabled")
        self.steps[2].set(None if not known else state not in ("disabled", "local_target_needed"))
        self.steps[3].set(None if not known else state == "active")
        self.steps[4].set(None if not online else bool(w.get("ok")))
        self.steps[5].set(bool(o.get("online")))
        self.steps[6].set(None if not o.get("online") else bool(o.get("installed")))
        self.steps[6].how.setText(f"In a terminal: ollama pull {o.get('model')}  (pick the model in "
                                  f"Settings).")
        self.test_btn.setEnabled(state in ("active", "registered", "pending"))

    def _refresh_events(self) -> None:
        if not self.isVisible():
            return
        evs = self.ctl.recent_events(40)
        self.events.setRowCount(len(evs))
        for r, e in enumerate(evs):
            try:
                t = datetime.fromisoformat(e["received_at"]).astimezone().strftime("%b %d %H:%M:%S")
            except (TypeError, ValueError):
                t = ""
            last = (e.get("log") or [""])[-1]
            cells = [t, e.get("event") or "", (e.get("meeting_id") or "")[:28], "", last]
            for c, v in enumerate(cells):
                it = QTableWidgetItem(v)
                it.setToolTip("\n".join(e.get("log") or []) or v)
                self.events.setItem(r, c, it)
            self.events.setItem(r, 3, colored_item(e.get("status") or "", EVENT_CHIP.get(e.get("status"), "muted")))

    def _refresh_decisions(self) -> None:
        decisions = self.ctl.pending_decisions()
        while self.dec_box.count():
            it = self.dec_box.takeAt(0)
            lay = it.layout()
            if lay is not None:
                while lay.count():
                    w = lay.takeAt(0).widget()
                    if w:
                        w.deleteLater()
            elif it.widget():
                it.widget().deleteLater()
        for d in decisions:
            run = d["run"]
            row = hbox(label(f"“{d.get('title') or d['meeting_id']}”: Meetily already has a summary. "
                             f"Replace it with ours?", wrap=True), None,
                       button("Replace", "primary", lambda r=run: self._decide(r, "replace")),
                       button("Keep Meetily's", on_click=lambda r=run: self._decide(r, "keep")),
                       button("Open", "ghost", lambda r=run: self.open_run.emit(r)))
            self.dec_box.addLayout(row)
        self.dec_card.setVisible(bool(decisions))

    def _decide(self, run: str, action: str) -> None:
        run_async(lambda: self.ctl.resolve_overwrite(run, action), lambda _r: self._refresh_decisions(),
                  lambda e: error_box(self, "Decision", e))

    def send_test(self) -> None:
        run_async(self.ctl.test_webhook, lambda _r: None, lambda e: error_box(self, "Test event", e))
