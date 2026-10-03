"""Popup shown when a Meetily recording starts: which window should be captured?

Runs as its own process (Tk wants the main thread, and the server must stay free to receive the
`recording.stopped` event that cancels it):

    python -m vcs.picker [--last-title T] [--last-process P] [--theme light|dark]

Prints one JSON line and exits: {"hwnd": N, "title": ..., "process": ...} for a choice, or
{"skip": true} when the user skips or closes the window. Styling follows the web UI's tokens
(neutral palette, light/dark), see vcs/web/app.css.
"""
from __future__ import annotations

import argparse
import io
import json
import sys

LIGHT = dict(bg="#ffffff", fg="#0a0a0a", muted="#737373", border="#e5e5e5", accent="#f5f5f5",
             primary="#171717", primary_fg="#fafafa", ok="#239b6c", thumb="#f5f5f5")
DARK = dict(bg="#0a0a0a", fg="#fafafa", muted="#a3a3a3", border="#262626", accent="#262626",
            primary="#fafafa", primary_fg="#171717", ok="#2fb784", thumb="#262626")

COLS, CARD_W, THUMB_H = 3, 224, 132


def preselect(windows: list[dict], last_title: str = "", last_process: str = "") -> int | None:
    """Index of the window used last time: same title and app, else the only window of that app."""
    usable = [i for i, w in enumerate(windows) if not w.get("minimized")]
    for i in usable:
        if windows[i]["title"] == last_title and windows[i]["process"] == last_process:
            return i
    same = [i for i in usable if last_process and windows[i]["process"] == last_process]
    return same[0] if len(same) == 1 else None


def restore(hwnd: int) -> None:
    """Un-minimize a window so it can be captured (Windows Graphics Capture needs it shown)."""
    import ctypes
    import time
    ctypes.windll.user32.ShowWindow(hwnd, 9)           # SW_RESTORE
    time.sleep(0.7)


def run(windows: list[dict], thumbs: dict[int, bytes | None], *, last_title: str = "",
        last_process: str = "", theme: str = "light", auto_close: float | None = None) -> dict:
    import tkinter as tk
    from tkinter import font as tkfont

    from PIL import Image, ImageTk

    c = DARK if theme == "dark" else LIGHT
    root = tk.Tk()
    S = root.winfo_fpixels("1i") / 96                # pixel sizes below are at 96 dpi
    cw, th = int(CARD_W * S), int(THUMB_H * S)
    root.title("Meetily · choose a window to capture")
    root.configure(bg=c["bg"])
    root.minsize(520, 420)
    sans = "Segoe UI" if "Segoe UI" in tkfont.families(root) else "TkDefaultFont"
    f_body, f_small = (sans, 10), (sans, 9)
    f_h1, f_semi = (sans, 15, "bold"), (sans, 10, "bold")

    result: dict = {"skip": True}
    state = {"sel": None}
    cards: list[tuple[tk.Frame, dict]] = []
    refs: list = []                                   # keep PhotoImages alive

    def finish(res):
        result.clear()
        result.update(res)
        root.destroy()

    def start():
        w = state["sel"]
        if w is not None:
            if w.get("minimized"):
                restore(w["hwnd"])
            finish({"hwnd": w["hwnd"], "title": w["title"], "process": w["process"]})

    def btn(parent, text, command, primary=False, small=False):
        b = tk.Button(parent, text=text, command=command, relief="flat", bd=0, cursor="hand2",
                      font=f_small if small else f_semi,
                      bg=c["primary"] if primary else c["bg"], fg=c["primary_fg"] if primary else c["muted"],
                      activebackground=c["primary"] if primary else c["accent"],
                      activeforeground=c["primary_fg"] if primary else c["fg"],
                      padx=18 if primary else 10, pady=7 if primary else 4,
                      disabledforeground=c["muted"], highlightthickness=0)
        return b

    # ---- header
    head = tk.Frame(root, bg=c["bg"])
    head.pack(fill="x", padx=24, pady=(22, 10))
    row = tk.Frame(head, bg=c["bg"])
    row.pack(anchor="w")
    dot = tk.Canvas(row, width=10, height=10, bg=c["bg"], highlightthickness=0)
    dot.create_oval(1, 1, 9, 9, fill=c["ok"], outline=c["ok"])
    dot.pack(side="left", padx=(0, 8))
    tk.Label(row, text="Meetily is recording", bg=c["bg"], fg=c["muted"], font=f_small).pack(side="left")
    tk.Label(head, text="Which window should we capture?", bg=c["bg"], fg=c["fg"], font=f_h1,
             anchor="w").pack(anchor="w", pady=(6, 2))
    tk.Label(head, text="Pick the window with the shared content, such as your slides. Only this window "
                        "is captured, even if it is covered. Skip to record without screen capture.",
             bg=c["bg"], fg=c["muted"], font=f_body, justify="left", wraplength=int((CARD_W + 12) * COLS * S),
             anchor="w").pack(anchor="w")

    # ---- footer (packed before the list so it never scrolls away)
    tk.Frame(root, bg=c["border"], height=1).pack(side="bottom", fill="x")
    foot = tk.Frame(root, bg=c["bg"])
    foot.pack(side="bottom", fill="x", padx=24, pady=14)
    sel_label = tk.Label(foot, text="No window selected", bg=c["bg"], fg=c["muted"], font=f_small,
                         anchor="w")
    sel_label.pack(side="left", fill="x", expand=True)
    go = btn(foot, "Start capture", start, primary=True)
    go.pack(side="right")
    go.configure(state="disabled")
    btn(foot, "Skip", lambda: finish({"skip": True}), small=True).pack(side="right", padx=(0, 8))

    # ---- scrollable grid of windows
    body = tk.Frame(root, bg=c["bg"])
    body.pack(fill="both", expand=True, padx=(24, 12))
    canvas = tk.Canvas(body, bg=c["bg"], highlightthickness=0, bd=0)
    bar = tk.Scrollbar(body, orient="vertical", command=canvas.yview)
    grid = tk.Frame(canvas, bg=c["bg"])
    grid.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=grid, anchor="nw")
    canvas.configure(yscrollcommand=bar.set)
    canvas.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")
    root.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

    def select(card, w):
        state["sel"] = w
        for fr, _ in cards:
            fr.configure(highlightbackground=c["border"])
        card.configure(highlightbackground=c["primary"])
        sel_label.configure(text=w["title"][:70] + ("  (will be restored)" if w.get("minimized") else ""),
                            fg=c["fg"])
        go.configure(state="normal")

    def make_card(i, w):
        card = tk.Frame(grid, bg=c["bg"], width=cw, height=th + int(58 * S), highlightthickness=2,
                        highlightbackground=c["border"], highlightcolor=c["primary"], cursor="hand2")
        card.pack_propagate(False)
        card.grid(row=i // COLS, column=i % COLS, padx=(0, int(12 * S)), pady=(0, int(12 * S)), sticky="n")
        thumb = tk.Frame(card, bg=c["thumb"], width=cw - 4, height=th)
        thumb.pack_propagate(False)
        thumb.pack()
        jpg = thumbs.get(w["hwnd"])
        if jpg and not w.get("minimized"):
            img = Image.open(io.BytesIO(jpg)).convert("RGB")
            iw, ih = img.size
            img = img.resize((cw - 4, max(1, int(ih * (cw - 4) / iw))))
            img = img.crop((0, 0, cw - 4, min(img.size[1], th)))            # top of the window
            ph = ImageTk.PhotoImage(img)
            refs.append(ph)
            tk.Label(thumb, image=ph, bg=c["thumb"], bd=0).pack(anchor="n")
        else:
            note = "Minimized\nrestored when capture starts" if w.get("minimized") else "No preview"
            tk.Label(thumb, text=note, bg=c["thumb"], fg=c["muted"], font=f_small).pack(expand=True)
        meta = tk.Frame(card, bg=c["bg"])
        meta.pack(fill="x", padx=8, pady=(6, 8))
        title = w["title"] if len(w["title"]) <= 26 else w["title"][:25] + "…"
        tk.Label(meta, text=title, bg=c["bg"], fg=c["fg"],
                 font=f_semi, anchor="w").pack(fill="x")
        tk.Label(meta, text=w["process"], bg=c["bg"], fg=c["muted"], font=f_small, anchor="w").pack(fill="x")
        cards.append((card, w))
        for widget in [card, thumb, meta, *thumb.winfo_children(), *meta.winfo_children()]:
            widget.bind("<Button-1>", lambda e, cd=card, ww=w: select(cd, ww))
            widget.bind("<Double-Button-1>", lambda e, cd=card, ww=w: (select(cd, ww), start()))
        return card

    if not windows:
        tk.Label(grid, text="No capturable windows found.", bg=c["bg"], fg=c["muted"],
                 font=f_body).pack(pady=40)
    for i, w in enumerate(windows):
        make_card(i, w)
    idx = preselect(windows, last_title, last_process)
    if idx is not None:
        select(cards[idx][0], windows[idx])

    root.bind("<Return>", lambda e: start())
    root.bind("<Escape>", lambda e: finish({"skip": True}))
    root.protocol("WM_DELETE_WINDOW", lambda: finish({"skip": True}))

    # ---- size, centre, bring to front
    root.update_idletasks()
    rows = max(1, -(-len(windows) // COLS))
    width = grid.winfo_reqwidth() + bar.winfo_reqwidth() + int(60 * S)
    want = head.winfo_reqheight() + foot.winfo_reqheight() + rows * (th + int(70 * S)) + int(30 * S)
    height = min(max(int(440 * S), want), int(root.winfo_screenheight() * 0.8))
    x, y = (root.winfo_screenwidth() - width) // 2, (root.winfo_screenheight() - height) // 4
    root.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    if auto_close:
        root.after(int(auto_close * 1000), lambda: finish({"skip": True, "selftest": len(cards)}))
    root.mainloop()
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vcs.picker")
    ap.add_argument("--last-title", default="")
    ap.add_argument("--last-process", default="")
    ap.add_argument("--theme", choices=["light", "dark"], default="light")
    ap.add_argument("--selftest", type=float, help="open, build the cards, close after N seconds")
    a = ap.parse_args(argv)

    from .windows import list_windows, set_dpi_aware, thumbnail_jpeg
    set_dpi_aware()
    wins = list_windows()
    thumbs = {w["hwnd"]: (None if w["minimized"] else thumbnail_jpeg(w["hwnd"], 480)) for w in wins}
    res = run(wins, thumbs, last_title=a.last_title, last_process=a.last_process, theme=a.theme,
              auto_close=a.selftest)
    sys.stdout.write(json.dumps(res) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
