"""Small Windows helpers: known folders, token elevation and user SID, per-user ACLs.

Everything here talks to Win32 directly through ctypes. Environment strings such as
%LOCALAPPDATA% are not trusted for locations (ARCHITECTURE 4.1).
"""
from __future__ import annotations

import ctypes
import subprocess
import uuid
from ctypes import wintypes
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000

_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_ole32 = ctypes.WinDLL("ole32", use_last_error=True)

_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_TOKEN_ELEVATION = 20


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


_shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]


def local_app_data() -> Path:
    """FOLDERID_LocalAppData for the user this process runs as."""
    u = uuid.UUID("F1B32785-6FBA-4FCF-9D55-7B8E7F157091")
    guid = _GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    out = ctypes.c_wchar_p()
    hr = _shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
    if hr != 0:
        raise OSError(f"SHGetKnownFolderPath failed: {hr & 0xFFFFFFFF:#010x}")
    try:
        return Path(out.value)
    finally:
        _ole32.CoTaskMemFree(out)


def _process_token() -> wintypes.HANDLE:
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(_kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    return token


def is_elevated() -> bool:
    """True when this process holds an elevated (administrator) token."""
    token = _process_token()
    try:
        elevation = wintypes.DWORD()
        size = wintypes.DWORD()
        if not _advapi32.GetTokenInformation(token, _TOKEN_ELEVATION, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(elevation.value)
    finally:
        _kernel32.CloseHandle(token)


def current_user_sid() -> str:
    """String SID of the user this process runs as, e.g. S-1-5-21-..."""
    token = _process_token()
    try:
        size = wintypes.DWORD()
        _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not _advapi32.GetTokenInformation(token, _TOKEN_USER, buf, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first field is the PSID.
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        if not _advapi32.ConvertSidToStringSidW(psid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            _kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        _kernel32.CloseHandle(token)


def restrict_dir_to_user(path: Path) -> str:
    """Leave the current user's SID as the directory's only ACE, with full control.

    Returns the SID. Raises if icacls fails, because the IPC token file must never sit in a
    directory other accounts can read (ARCHITECTURE 5).

    `/reset` first: `/inheritance:r` drops only inherited ACEs, so an explicit one (a folder created
    elsewhere, or with Python's mkdir(mode=0o700) ACL) used to survive. Found by public CI on a hosted
    runner, 2026-10-24; test_restricted_dir_drops_explicit_aces pins it.
    """
    sid = current_user_sid()
    for args in (["/reset"], ["/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"]):
        r = subprocess.run(["icacls", str(path), *args], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            raise OSError(f"icacls {args[0]} failed ({r.returncode}): {r.stdout.strip()} {r.stderr.strip()}")
    return sid


def dir_acl_entries(path: Path) -> list[str]:
    """ACE lines icacls prints for a directory (the path prefix stripped)."""
    r = subprocess.run(["icacls", str(path)], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise OSError(f"icacls failed ({r.returncode}): {r.stdout.strip()}")
    entries = []
    for line in r.stdout.splitlines():
        line = line.replace(str(path), "", 1).strip()
        if line and not line.startswith("Successfully processed"):
            entries.append(line)
    return entries
