"""Small question popups shown after a Meetily recording ends (same look as vcs/picker.py).

    python -m vcs.prompt generate  --title T --screens N     -> {"action": "generate" | "skip"}
    python -m vcs.prompt overwrite --title T                 -> {"action": "replace" | "keep" | "later"}

Runs as its own process (Tk wants the main thread). Closing the window or pressing Escape picks the
last, least committal answer (skip / later). Prints one JSON line.
"""
from __future__ import annotations

import argparse
import json
import sys

from .picker import DARK, LIGHT

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
        auto_close: float | None = None) -> dict:
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

    def finish(action):
        result["action"] = action
        root.destroy()

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
    tk.Label(box, text=spec["body"].format(name=name, screens=shots), bg=c["bg"], fg=c["muted"],
             font=f_body, justify="left", wraplength=wrap, anchor="w").pack(anchor="w")

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
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vcs.prompt")
    ap.add_argument("kind", choices=sorted(KINDS))
    ap.add_argument("--title", default="")
    ap.add_argument("--screens", type=int, default=0)
    ap.add_argument("--theme", choices=["light", "dark"], default="light")
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
