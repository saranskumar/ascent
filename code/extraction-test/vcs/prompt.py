"""Small question popups shown after a Meetily recording ends (same look as vcs/picker.py).

    python -m vcs.prompt generate  --title T --screens N     -> {"action": "generate" | "skip"}
    python -m vcs.prompt overwrite --title T                 -> {"action": "replace" | "keep" | "later"}
    python -m vcs.prompt generate --live ...                 -> same answer, printed the moment
        "Generate summary" is clicked; the window then STAYS OPEN as a progress view and reads
        JSON lines from stdin: {"text": "...", "state": "working" | "done" | "error"}.

Runs as its own process (Tk wants the main thread). Closing the window or pressing Escape picks the
last, least committal answer (skip / later). Prints one JSON line.
"""
from __future__ import annotations

import argparse
import json
import queue
import sys
import threading

from .picker import DARK, LIGHT

BAD = {"light": "#dc2626", "dark": "#f87171"}

KINDS = {
    "generate": dict(
        kicker="Recording ended",
        heading="Generate the summary?",
        body="{name} has ended.{screens} Generate a summary of what was said and what was shown? "
             "Meetily's own summary is not touched until you confirm.",
        buttons=[("Generate summary", "generate", "primary"), ("Not now", "skip", "ghost")],
        default="skip"),
    "overwrite": dict(
        kicker="Meetily already has a summary",
        heading="Replace it with ours?",
        body="Our summary of {name} is ready (it includes what was on screen), but Meetily already has "
             "its own. If you replace it, Meetily's is saved to this run's backups folder first.",
        buttons=[("Replace with ours", "replace", "primary"), ("Keep Meetily's", "keep", "secondary"),
                 ("Decide later", "later", "ghost")],
        default="later"),
}


def run(kind: str, title: str = "", screens: int = 0, theme: str = "light",
        auto_close: float | None = None, live: bool = False) -> dict | None:
    import tkinter as tk
    from tkinter import font as tkfont

    spec = KINDS[kind]
    c = DARK if theme == "dark" else LIGHT
    root = tk.Tk()
    root.title("Meetily · " + spec["heading"])
    root.configure(bg=c["bg"])
    root.resizable(False, False)
    S = root.winfo_fpixels("1i") / 96
    sans = "Segoe UI" if "Segoe UI" in tkfont.families(root) else "TkDefaultFont"
    f_body, f_small, f_semi, f_h1 = (sans, 10), (sans, 9), (sans, 10, "bold"), (sans, 15, "bold")
    result = {"action": spec["default"]}
    q: queue.Queue = queue.Queue()          # JSON lines from the app (live mode)
    backlog: list[str] = []                 # progress lines that arrived while the question was open
    seen_eof = []
    started = []                            # set once the progress view has taken over the queue
    if live and kind == "generate":
        def reader():
            for line in sys.stdin:
                try:
                    q.put(json.loads(line))
                except ValueError:
                    continue
            q.put({"state": "eof"})
        threading.Thread(target=reader, daemon=True).start()

    def finish(action):
        result["action"] = action
        if live and action == "generate":
            sys.stdout.write(json.dumps(result) + chr(10))   # the app starts working right away
            sys.stdout.flush()
            result["printed"] = True
            show_progress()
            return
        root.destroy()

    def show_progress():
        """Same window, now the post-recording tracker: progress lines, the Replace/Keep choice and
        the finished summary's actions, all driven by JSON lines on stdin (replies go to stdout)."""
        for wdg in list(root.winfo_children()):
            wdg.destroy()
        root.title("Meetily · Summary")
        bad = BAD[theme]
        body = tk.Frame(root, bg=c["bg"])
        body.pack(fill="both", expand=True, padx=int(24 * S), pady=(int(22 * S), int(8 * S)))
        top = tk.Frame(body, bg=c["bg"])
        top.pack(anchor="w")
        dot = tk.Canvas(top, width=10, height=10, bg=c["bg"], highlightthickness=0)
        oval = dot.create_oval(1, 1, 9, 9, fill=c["muted"], outline=c["muted"])
        dot.pack(side="left", padx=(0, 8))
        kick = tk.Label(top, text="Working", bg=c["bg"], fg=c["muted"], font=f_small)
        kick.pack(side="left")
        head = tk.Label(body, text="Generating the summary…", bg=c["bg"], fg=c["fg"], font=f_h1, anchor="w")
        head.pack(anchor="w", pady=(6, 4))
        status = tk.Label(body, text="Starting…", bg=c["bg"], fg=c["muted"], font=f_body, justify="left",
                          wraplength=wrap, anchor="w")
        status.pack(anchor="w", fill="x")
        bar_w, bar_h = wrap, int(6 * S)
        bar = tk.Canvas(body, width=bar_w, height=bar_h, bg=c["accent"], highlightthickness=0)
        bar.pack(anchor="w", pady=(int(12 * S), int(10 * S)))
        seg = bar.create_rectangle(0, 0, 0, bar_h, fill=c["primary"], outline=c["primary"])
        log_box = tk.Text(body, width=1, height=7, bg=c["accent"], fg=c["muted"], font=(sans, 9), relief="flat",
                          bd=0, wrap="word", highlightthickness=0, padx=8, pady=6)
        log_box.pack(fill="x")
        log_box.configure(state="disabled")
        tk.Frame(root, bg=c["border"], height=1).pack(fill="x", pady=(int(16 * S), 0))
        foot = tk.Frame(root, bg=c["bg"])
        foot.pack(fill="x", padx=int(24 * S), pady=int(14 * S))

        started.append(True)
        state = {"phase": "working", "x": 0, "summary": "", "url": ""}

        def reply(action):
            """Answer the app (stdout), then go back to showing progress."""
            sys.stdout.write(json.dumps({"action": action}) + chr(10))
            sys.stdout.flush()
            working_footer()
            state["phase"] = "working"
            dot.itemconfig(oval, fill=c["muted"], outline=c["muted"])
            kick.config(text="Working")
            head.config(text="Generating the summary…" if action == "regenerate" else "Working on it…")

        def set_buttons(buttons):
            """buttons: (label, callback, style) left-to-right priority: first is rightmost."""
            for wdg in list(foot.winfo_children()):
                wdg.destroy()
            for label, cb, style in buttons:
                primary = style == "primary"
                b = tk.Button(foot, text=label, command=cb, relief="flat", bd=0, cursor="hand2",
                              font=f_semi if style != "ghost" else f_small,
                              bg=c["primary"] if primary else (c["accent"] if style == "secondary" else c["bg"]),
                              fg=c["primary_fg"] if primary else (c["fg"] if style == "secondary" else c["muted"]),
                              activebackground=c["primary"] if primary else c["accent"],
                              activeforeground=c["primary_fg"] if primary else c["fg"],
                              padx=18 if primary else 12, pady=7 if style != "ghost" else 4,
                              highlightthickness=0)
                b.pack(side="right", padx=(8, 0))
                if primary:
                    root.bind("<Return>", lambda e, f=cb: f())

        def working_footer():
            """No buttons while it works: the window stays until the summary is ready."""
            for wdg in list(foot.winfo_children()):
                wdg.destroy()
            tk.Label(foot, text="This window stays open until the summary is ready.", bg=c["bg"],
                     fg=c["muted"], font=f_small).pack(side="left")

        def copy_summary():
            root.clipboard_clear()
            root.clipboard_append(state["summary"])
            root.update()
            status.config(text="Summary copied to the clipboard.")

        def open_web():
            import webbrowser
            webbrowser.open(state["url"])

        def done_buttons():
            btns = [("Close", root.destroy, "primary")]
            if state["url"]:
                btns.append(("Open in web UI", open_web, "secondary"))
            if state["summary"]:
                btns.append(("Copy summary", copy_summary, "secondary"))
            btns.append(("Regenerate", lambda: reply("regenerate"), "ghost"))
            set_buttons(btns)

        for line_text in backlog:
            log_box.configure(state="normal")
            log_box.insert("end", line_text + chr(10))
            log_box.see("end")
            log_box.configure(state="disabled")
        if backlog:
            status.config(text=backlog[-1])
        if seen_eof:
            q.put({"state": "eof"})
        working_footer()

        def finish_ui(kind_text, heading, color, text):
            state["phase"] = "end"
            dot.itemconfig(oval, fill=color, outline=color)
            kick.config(text=kind_text)
            head.config(text=heading)
            status.config(text=text)
            bar.coords(seg, 0, 0, bar_w if color != bad else 0, bar_h)
            bar.itemconfig(seg, fill=color)

        def tick():
            if state["phase"] == "working":                 # indeterminate bar: a segment sweeping across
                state["x"] = (state["x"] + int(7 * S)) % (bar_w + int(90 * S))
                bar.coords(seg, state["x"] - int(90 * S), 0, state["x"], bar_h)
            try:
                while True:
                    m = q.get_nowait()
                    st, text = m.get("state", "working"), str(m.get("text") or "")
                    if text and st in ("working", "done", "error", "choose"):
                        log_box.configure(state="normal")
                        log_box.insert("end", text + chr(10))
                        log_box.see("end")
                        log_box.configure(state="disabled")
                    if st == "working":
                        state["phase"] = "working"
                        status.config(text=text or status.cget("text"))
                    elif st == "choose":                    # a decision is needed: buttons right here
                        state["phase"] = "end"
                        dot.itemconfig(oval, fill=c["ok"], outline=c["ok"])
                        kick.config(text="Your decision")
                        head.config(text="Replace Meetily's summary?")
                        status.config(text=text)
                        bar.coords(seg, 0, 0, bar_w, bar_h)
                        opts = m.get("options") or []
                        set_buttons([(o.get("label", "?"), (lambda a=o.get("action", ""): reply(a)),
                                      o.get("style", "secondary")) for o in opts])
                    elif st == "done":
                        state["summary"] = str(m.get("summary") or "")
                        state["url"] = str(m.get("url") or "")
                        finish_ui("Done", "Summary ready", c["ok"], text or "Finished.")
                        done_buttons()
                    elif st == "error":
                        finish_ui("Failed", "Summary failed", bad, text or "Something went wrong.")
                        set_buttons([("Close", root.destroy, "primary"),
                                     ("Try again", lambda: reply("regenerate"), "secondary")])
                    elif st == "eof" and state["phase"] == "working":
                        finish_ui("Stopped", "Lost contact with the app", bad,
                                  "The app stopped sending updates. Check the web UI for the result.")
                        set_buttons([("Close", root.destroy, "primary")])
            except queue.Empty:
                pass
            root.after(60, tick)
        tick()
        root.update_idletasks()
        root.geometry(f"{max(int(500 * S), root.winfo_reqwidth())}x{root.winfo_reqheight()}")

    wrap = int(440 * S)
    box = tk.Frame(root, bg=c["bg"])
    box.pack(fill="both", expand=True, padx=int(24 * S), pady=(int(22 * S), int(8 * S)))
    row = tk.Frame(box, bg=c["bg"])
    row.pack(anchor="w")
    dot = tk.Canvas(row, width=10, height=10, bg=c["bg"], highlightthickness=0)
    dot.create_oval(1, 1, 9, 9, fill=c["ok"], outline=c["ok"])
    dot.pack(side="left", padx=(0, 8))
    tk.Label(row, text=spec["kicker"], bg=c["bg"], fg=c["muted"], font=f_small).pack(side="left")
    tk.Label(box, text=spec["heading"], bg=c["bg"], fg=c["fg"], font=f_h1, anchor="w").pack(
        anchor="w", pady=(6, 4))
    name = f"“{title}”" if title else "the recording"
    shots = "" if screens < 0 else (f" {screens} screen{'s' if screens != 1 else ''} captured."
                                    if screens else " No screens captured.")
    tk.Label(box, text=spec["body"].format(name=name, screens=shots)[:1].upper() + spec["body"].format(name=name, screens=shots)[1:], bg=c["bg"], fg=c["muted"],
             font=f_body, justify="left", wraplength=wrap, anchor="w").pack(anchor="w")

    qstatus = tk.Label(box, text="", bg=c["bg"], fg=c["muted"], font=f_small, justify="left",
                       wraplength=wrap, anchor="w")
    if live and kind == "generate":
        qstatus.pack(anchor="w", pady=(int(10 * S), 0))

        def poll_question():
            if started:
                return
            try:
                while True:
                    m = q.get_nowait()
                    if m.get("state") == "eof":
                        seen_eof.append(True)
                    elif m.get("text"):
                        backlog.append(str(m["text"]))
                        qstatus.config(text=str(m["text"]))
            except queue.Empty:
                pass
            root.after(150, poll_question)
        poll_question()

    tk.Frame(root, bg=c["border"], height=1).pack(fill="x", pady=(int(16 * S), 0))
    foot = tk.Frame(root, bg=c["bg"])
    foot.pack(fill="x", padx=int(24 * S), pady=int(14 * S))
    for label, action, style in spec["buttons"]:        # packed right-to-left: first = rightmost
        primary = style == "primary"
        b = tk.Button(foot, text=label, command=lambda a=action: finish(a), relief="flat", bd=0,
                      cursor="hand2", font=f_semi if style != "ghost" else f_small,
                      bg=c["primary"] if primary else (c["accent"] if style == "secondary" else c["bg"]),
                      fg=c["primary_fg"] if primary else (c["fg"] if style == "secondary" else c["muted"]),
                      activebackground=c["primary"] if primary else c["accent"],
                      activeforeground=c["primary_fg"] if primary else c["fg"],
                      padx=18 if primary else 12, pady=7 if style != "ghost" else 4, highlightthickness=0)
        b.pack(side="right", padx=(8, 0))
        if primary:
            root.bind("<Return>", lambda e, a=action: finish(a))

    root.bind("<Escape>", lambda e: finish(spec["default"]))
    root.protocol("WM_DELETE_WINDOW", lambda: finish(spec["default"]))
    root.update_idletasks()
    w = max(int(500 * S), root.winfo_reqwidth())
    h = root.winfo_reqheight()
    x, y = (root.winfo_screenwidth() - w) // 2, (root.winfo_screenheight() - h) // 3
    root.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    if auto_close:
        root.after(int(auto_close * 1000), lambda: finish(spec["default"]))
    root.mainloop()
    return None if result.get("printed") else result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vcs.prompt")
    ap.add_argument("kind", choices=sorted(KINDS))
    ap.add_argument("--title", default="")
    ap.add_argument("--screens", type=int, default=0)
    ap.add_argument("--theme", choices=["light", "dark"], default="light")
    ap.add_argument("--live", action="store_true", help="generate: stay open as a progress view")
    ap.add_argument("--selftest", type=float)
    a = ap.parse_args(argv)
    try:                                    # sharp text; ctypes only, importing vcs.windows would pull in OpenCV
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001 - only affects sharpness
        pass
    sys.stdout.write(json.dumps(run(a.kind, a.title, a.screens, a.theme, a.selftest)) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
