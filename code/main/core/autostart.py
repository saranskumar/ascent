"""Start with Windows: a value under HKCU\\...\\Run (per user, no admin rights needed)."""
from __future__ import annotations

import sys
from pathlib import Path

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "AscentMeetingSummaries"
RUN_PY = Path(__file__).resolve().parents[1] / "run.py"


def command() -> str:
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")           # no console window at login
    return f'"{pyw if pyw.exists() else exe}" "{RUN_PY}" --minimized'


def is_enabled() -> bool:
    if not sys.platform.startswith("win"):
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            winreg.QueryValueEx(k, NAME)
            return True
    except OSError:
        return False


def set_enabled(on: bool) -> None:
    if not sys.platform.startswith("win"):
        raise OSError("start with Windows is only available on Windows")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except FileNotFoundError:
                pass
