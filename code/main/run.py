"""Meeting Summaries: Meetily transcript + what was on screen, summarised by a local model.

    python run.py              # open the window (and stay in the tray)
    python run.py --minimized  # start hidden in the tray (used by "Start with Windows")

Only one copy runs; starting it again brings the running window to the front.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

APP_ID = "ascent.meeting-summaries.main"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--minimized", action="store_true", help="start hidden in the tray")
    ap.add_argument("--data", type=Path, default=ROOT / "data", help="data folder (default: main/data)")
    ap.add_argument("--no-webhooks", action="store_true",
                    help="don't listen for or subscribe to Meetily events (manual use only)")
    args = ap.parse_args(argv)

    from core.env import load_env
    load_env()

    # pythonw (tray/autostart) has no console: keep errors in data/app.log instead of losing them.
    if sys.stderr is None or sys.stdout is None:
        args.data.mkdir(parents=True, exist_ok=True)
        log = open(args.data / "app.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log

    import time
    print(f"--- started {time.strftime('%Y-%m-%d %H:%M:%S')}", file=sys.stderr)

    if sys.platform.startswith("win"):
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass

    from PyQt6.QtNetwork import QLocalServer, QLocalSocket
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Meeting Summaries")
    app.setQuitOnLastWindowClosed(False)

    # ---- single instance: a second launch just shows the first one's window
    sock = QLocalSocket()
    sock.connectToServer(APP_ID)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(500)
        return 0
    QLocalServer.removeServer(APP_ID)
    server = QLocalServer()
    server.listen(APP_ID)

    from core.controller import Controller
    from ui import theme
    from ui.bridge import Bridge
    from ui.common import init_invoker
    from ui.main_window import MainWindow

    init_invoker()
    bridge = Bridge()
    ctl = Controller(args.data, ui=bridge, start_services=False)
    ctl.queue.subscribe(bridge.on_queue)
    theme.apply(app, ctl.settings["theme"])
    win = MainWindow(ctl, bridge, app)
    ctl.start(webhooks=not args.no_webhooks)

    def on_connection():
        c = server.nextPendingConnection()
        if c is not None:
            c.readyRead.connect(lambda: (c.readAll(), win.show_window()))
    server.newConnection.connect(on_connection)
    app.aboutToQuit.connect(ctl.shutdown)

    if not (args.minimized and ctl.settings["start_minimized"]):   # autostart: tray only
        win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
