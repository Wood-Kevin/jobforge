"""Graceful stop on Windows sign-out and shutdown (ARCHITECTURE 3.2).

A console process that loads user32 does not receive CTRL_LOGOFF_EVENT / CTRL_SHUTDOWN_EVENT,
so the agent owns a hidden top-level window instead. On WM_ENDSESSION it asks the runtime to
stop and waits (up to 4 s, inside the time Windows allows) for the run to be recorded. If
Windows kills the process anyway, startup recovery repairs the state on the next run.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

CLASS_NAME = "JobForgeAgentSessionWindow"
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
ENDSESSION_LOGOFF = 0x80000000
STOP_WAIT_S = 4.0

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


_user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.DefWindowProcW.restype = LRESULT
_user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
_user32.RegisterClassW.restype = wintypes.ATOM
_user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                    wintypes.HINSTANCE, wintypes.LPVOID]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.DestroyWindow.argtypes = [wintypes.HWND]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class SessionEndWatcher:
    """Runs a hidden window on its own thread.

    on_end(reason) is called on the window thread; it must hand off to the event loop
    thread-safely. stopped is set by the caller once the run has been recorded.
    """

    def __init__(self, on_end: Callable[[str], None], stopped: threading.Event):
        self._on_end = on_end
        self._stopped = stopped
        self._hwnd: int | None = None
        self._ready = threading.Event()
        self._proc = WNDPROC(self._wndproc)  # keep a reference: ctypes callbacks must not be collected
        self._thread = threading.Thread(target=self._run, name="session-end", daemon=True)
        self.error: str | None = None

    def start(self, timeout: float = 5.0) -> bool:
        self._thread.start()
        self._ready.wait(timeout)
        return self._hwnd is not None

    def close(self) -> None:
        if self._hwnd:
            _user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_QUERYENDSESSION:
            return 1  # never block sign-out
        if msg == WM_ENDSESSION:
            if wparam:
                reason = "session_end_logoff" if (lparam & ENDSESSION_LOGOFF) else "session_end_shutdown"
                self._on_end(reason)
                self._stopped.wait(STOP_WAIT_S)
            return 0
        if msg == WM_CLOSE:
            _user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self) -> None:
        try:
            hinst = _kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinst, lpszClassName=CLASS_NAME)
            if not _user32.RegisterClassW(ctypes.byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())
            hwnd = _user32.CreateWindowExW(0, CLASS_NAME, "JobForge agent", 0, 0, 0, 0, 0, None, None, hinst, None)
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self._hwnd = hwnd
        except OSError as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
