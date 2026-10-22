"""Start agent processes the way a user would: at medium integrity.

The agent refuses to run elevated (SPEC D16). When the test shell itself is elevated (the
Claude Code shell on Kevin's machine is), processes are started through explorer.exe, which
always runs unelevated, using a generated .cmd file. Environment variables are set inside
that file because explorer does not inherit ours.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from jobforge_agent import winutil

PYTHON = sys.executable


def _cmd_line(args: list[str]) -> str:
    return " ".join(f'"{a}"' for a in [PYTHON, "-m", "jobforge_agent", *args])


def start_agent(data_dir: Path, *args: str) -> subprocess.Popen | None:
    """Start an agent without waiting. Returns the Popen when not elevated, else None."""
    env = {"JOBFORGE_DATA_DIR": str(data_dir)}
    if not winutil.is_elevated():
        return subprocess.Popen([PYTHON, "-m", "jobforge_agent", *args], env={**os.environ, **env},
                                creationflags=winutil.CREATE_NO_WINDOW)
    folder = Path(tempfile.mkdtemp(prefix="jf_launch_"))
    cmd = folder / "start.cmd"
    cmd.write_text("@echo off\r\n" + "".join(f'set "{k}={v}"\r\n' for k, v in env.items())
                   + f'start "" /min {_cmd_line(list(args))}\r\n', encoding="ascii")
    subprocess.run(["explorer.exe", str(cmd)])
    return None


def run_agent(data_dir: Path, *args: str, timeout: float = 60) -> int:
    """Run an agent command to completion unelevated; return its exit code."""
    env = {"JOBFORGE_DATA_DIR": str(data_dir)}
    if not winutil.is_elevated():
        return subprocess.run([PYTHON, "-m", "jobforge_agent", *args], env={**os.environ, **env},
                              creationflags=winutil.CREATE_NO_WINDOW, timeout=timeout).returncode
    folder = Path(tempfile.mkdtemp(prefix="jf_launch_"))
    cmd, marker = folder / "run.cmd", folder / "exit.txt"
    cmd.write_text("@echo off\r\n" + "".join(f'set "{k}={v}"\r\n' for k, v in env.items())
                   + f"{_cmd_line(list(args))}\r\n" + f'echo %ERRORLEVEL% > "{marker}"\r\n', encoding="ascii")
    subprocess.run(["explorer.exe", str(cmd)])
    deadline = time.time() + timeout
    while time.time() < deadline:
        if marker.exists():
            text = marker.read_text(encoding="ascii").strip()
            if text:
                return int(text)
        time.sleep(0.2)
    raise TimeoutError(f"agent command {args} did not finish within {timeout} s")
