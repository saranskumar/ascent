"""Build the whole window offscreen, visit every page, and drive the Live and pick flows.
No Meetily, Ollama or network needed (services aren't started)."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6 import QtCore  # noqa: E402

from core.controller import Controller, PickRequest  # noqa: E402
from tests.test_pipeline import FakeMeetily, make_run  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ui.common import init_invoker
    init_invoker()
    return a


def pump(app, secs=0.3):
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        app.processEvents()
        time.sleep(0.01)


def test_window_pages_and_flows(app, tmp_path):
    from ui import theme
    from ui.bridge import Bridge
    from ui.main_window import NAV, MainWindow

    bridge = Bridge()
    ctl = Controller(tmp_path, ui=bridge, client_factory=FakeMeetily(), start_services=False)
    ctl.settings.update({"ollama_url": "http://127.0.0.1:9", "notify": False})
    ctl.queue.subscribe(bridge.on_queue)
    theme.apply(app, "light")
    run = make_run(ctl)
    (ctl.store.run_dir(run) / "summary.md").write_text("# T\n\n**Summary**\n\nHello", "utf-8")
    done = ctl.queue.create(run=run, title="Done meeting", meeting_id="meeting-1", stages=["publish"])
    done["status"] = "done"
    waiting = ctl.queue.create(run=run, title="Waiting meeting", meeting_id="meeting-1",
                               stages=["summarize", "publish"])
    waiting["status"] = "waiting"
    waiting["decision"] = {"type": "overwrite", "title": "Budget", "meetily_chars": 10, "ours_chars": 20}
    queued = ctl.queue.create(run=run, title="Queued meeting", stages=["extract"])

    win = MainWindow(ctl, bridge, app)
    win.show()
    pump(app)
    for key, _ in NAV:
        win.go(key)
        pump(app, 0.4)
        assert win.stack.currentWidget() is win.pages[key]

    # Live: select each job, the waiting one shows its decision banner
    live = win.pages["live"]
    win.show_live(waiting["id"])
    pump(app)
    assert live.selected == waiting["id"] and not live.banner.isHidden()
    win.show_live(queued["id"])
    pump(app)
    assert live.b_next.isVisibleTo(live) and live.banner.isHidden()
    live.cancel()
    pump(app)
    assert ctl.queue.get(queued["id"])["status"] == "canceled"
    ctl.queue.log(queued["id"], "hello from the test")
    pump(app)
    assert "hello from the test" in live.log.toPlainText()
    # the race seen live: a line is logged, the view fully reloads (status change) before the
    # line's notification arrives -> it must still be shown once, not twice
    ctl.queue.log(queued["id"], "only once please")
    live._show_detail(full=True)
    pump(app)
    assert live.log.toPlainText().count("only once please") == 1

    # Meetings: the run is listed and its summary renders
    win.open_run(run)
    pump(app, 0.6)
    mp = win.pages["meetings"]
    assert mp.run_id == run and "Hello" in mp.summary.toPlainText()
    for i in range(mp.tabs.count()):
        mp.tabs.setCurrentIndex(i)
        pump(app, 0.3)

    # Transcript tab shows the speech; the screen viewer opens and steps
    mp.select_run(run, "transcript")
    pump(app, 0.8)
    assert "Q3 budget" in mp.transcript.toPlainText()
    # Screens tab: rows are selectable, the preview follows, Enter/double-click opens the viewer
    mp.select_run(run, "screens")
    pump(app, 0.5)
    assert mp.screens.count() == 2
    mp.screens.setCurrentRow(1)
    pump(app)
    assert mp.screen_img._pix is not None and "Q3 budget" in mp.screen_text.toPlainText()
    opened = []
    mp.view_screen = lambda: opened.append(mp.screens.currentRow())
    mp.screens.itemActivated.emit(mp.screens.item(1))
    assert opened == [1]
    from ui.image_viewer import ImageViewer
    v = ImageViewer(mp, ctl.store.run_dir(run), ctl.store.screenshots(run)["screenshots"])
    v.go(1)
    assert v.i == 1 and "Slide" in v.info.text()
    v.close()

    # Edit the summary, save, send to Meetily (FakeMeetily has none -> written)
    mp.select_run(run, edit=True)
    pump(app)
    assert mp.editing()
    mp.editor.setPlainText("# Budget Review\n\n**Summary**\n\nEdited by hand.")
    assert mp.save_edit() and not mp.editing()
    assert "Edited by hand" in (ctl.store.run_dir(run) / "summary.md").read_text("utf-8")
    from PyQt6.QtWidgets import QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    mp.send()
    pump(app, 1.0)
    assert ctl.client_factory.puts and "Edited by hand" in ctl.client_factory.puts[-1]

    # Recording started -> pick page; recording ended -> back to Live
    req = PickRequest("meeting-2", "Standup", {})
    win.go("overview")
    bridge.pick_window(req)
    pump(app)
    assert win.stack.currentWidget() is win.pages["pick"]
    # the bug seen live: focusing the sidebar must not jump to its first item (Overview)
    win.nav.setFocus()
    win.activateWindow()
    pump(app)
    assert win.stack.currentWidget() is win.pages["pick"]
    assert win.nav.currentItem().data(QtCore.Qt.ItemDataRole.UserRole) == "pick"
    req.cancel()
    bridge.pick_closed(req)
    pump(app)
    assert win.stack.currentWidget() is win.pages["live"]
    assert win._nav_row("pick") == -1                 # the entry goes away with the question

    # Settings round-trip
    sp = win.pages["settings"]
    win.go("settings")
    pump(app)
    sp.fields["max_tokens"][0].setValue(1500)
    sp.save()
    assert ctl.settings["max_tokens"] == 1500

    win.quitting = True
    win.close()
    win.tray.hide()
