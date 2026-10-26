"""verify.sh "models": is the ChatGPT access token anywhere it must not be (SPEC D18)? Decrypts auth\\chatgpt.bin
(DPAPI, current user), then counts files under the database, backups and logs folders that contain the token's
first 24 characters or its refresh token's. Prints counts and file names only, never a token. Raw evidence, no verdict.
Usage: python verify_tokens.py <data root>"""
import ctypes
import ctypes.wintypes as wt
import json
import sys
from pathlib import Path


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def unprotect(data: bytes) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


root = Path(sys.argv[1])
token_file = root / "auth" / "chatgpt.bin"
if not token_file.exists():
    print(f"{token_file}: not present (not signed in to ChatGPT)")
    sys.exit(0)
t = json.loads(unprotect(token_file.read_bytes()))
needles = {"access token prefix": t["access_token"][:24].encode(), "refresh token prefix": (t.get("refresh_token") or "")[:24].encode()}
print(f"{token_file}: {token_file.stat().st_size} bytes, plaintext token prefixes in it: "
      f"{sum(n in token_file.read_bytes() for n in needles.values() if n)}")
for folder in ("data", "backups", "logs", "run"):
    files = [p for p in (root / folder).rglob("*") if p.is_file()] if (root / folder).exists() else []
    hits = []
    for p in files:
        try:
            body = p.read_bytes()
        except OSError as exc:
            print(f"  {p}: unreadable ({type(exc).__name__})")
            continue
        hits += [f"{p.name} ({label})" for label, n in needles.items() if n and n in body]
    print(f"{folder}\\: {len(files)} files searched, matches: {hits or 'none'}")
