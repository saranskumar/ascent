"""List capturable top-level windows and grab thumbnails (Win32 via ctypes, no extra deps).

Thumbnails use PrintWindow(PW_RENDERFULLCONTENT): works for covered windows and modern
(DirectComposition) apps such as Chrome/Edge, and doesn't flash a capture border.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

import cv2
import numpy as np

IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
GW_OWNER = 4
DWMWA_CLOAKED = 14
PW_RENDERFULLCONTENT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Shell/system surfaces that are never what the user wants to watch.
SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow"}


def set_dpi_aware():
    """Per-monitor DPI awareness, so window sizes and thumbnails are in real pixels."""
    if not IS_WINDOWS:
        return
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        try:
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _process(hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)


def _cloaked(hwnd) -> bool:
    v = ctypes.c_int(0)
    try:
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v))
    except OSError:
        return False
    return v.value != 0


def _rect(hwnd) -> tuple[int, int]:
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def is_window(hwnd: int) -> bool:
    return bool(IS_WINDOWS and user32.IsWindow(hwnd))


def window_info(hwnd: int) -> dict:
    w, h = _rect(hwnd)
    return {"hwnd": int(hwnd), "title": _title(hwnd), "process": _process(hwnd),
            "width": w, "height": h, "minimized": bool(user32.IsIconic(hwnd))}


def list_windows() -> list[dict]:
    """Visible, uncloaked, unowned top-level app windows with a title."""
    if not IS_WINDOWS:
        return []
    own_pid = os.getpid()
    found: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
                return True
            ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            if ex & WS_EX_TOOLWINDOW or (ex & WS_EX_NOACTIVATE and not _title(hwnd)):
                return True
            if not _title(hwnd).strip() or _class(hwnd) in SKIP_CLASSES or _cloaked(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == own_pid:
                return True
            info = window_info(hwnd)
            if info["minimized"] or (info["width"] >= 200 and info["height"] >= 120):
                found.append(info)
        except Exception:  # noqa: BLE001 - one odd window must not break the list
            pass
        return True

    user32.EnumWindows(cb, 0)
    return found


def grab(hwnd: int) -> np.ndarray | None:
    """Full-size BGR image of a window via PrintWindow, or None (minimized / failed)."""
    if not is_window(hwnd) or user32.IsIconic(hwnd):
        return None
    w, h = _rect(hwnd)
    if w <= 0 or h <= 0:
        return None
    hdc_win = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    old = gdi32.SelectObject(hdc_mem, bmp)
    try:
        if not user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT):
            return None
        bih = BITMAPINFOHEADER()
        bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bih.biWidth, bih.biHeight = w, -h          # top-down rows
        bih.biPlanes, bih.biBitCount = 1, 32
        buf = (ctypes.c_ubyte * (w * h * 4))()
        if not gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf, ctypes.byref(bih), 0):
            return None
        img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
        return None if img.max() == 0 else img     # all black = nothing rendered
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_win)


def thumbnail_jpeg(hwnd: int, max_w: int = 360) -> bytes | None:
    img = grab(hwnd)
    if img is None:
        return None
    h, w = img.shape[:2]
    if w > max_w:
        img = cv2.resize(img, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return jpg.tobytes() if ok else None


def presenting_warning(hwnd: int) -> str | None:
    """Google Meet shows a 'You are presenting' placeholder instead of your slides in the
    presenter's own tab. Cheap OCR check so the picker can warn (docs/tech/screen-capture.md)."""
    img = grab(hwnd)
    if img is None:
        return None
    from .ocr import ocr_image
    text = ocr_image(img).lower()
    if "you are presenting" in text or "you're presenting" in text:
        return ("This window shows Meet's 'You are presenting' placeholder, not your slides. "
                "Pick the window you're sharing (e.g. PowerPoint or the slides tab) instead.")
    return None


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
    import time
    if IS_WINDOWS and user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)           # SW_RESTORE
        time.sleep(0.7)
