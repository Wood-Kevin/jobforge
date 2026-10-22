"""Per-user locations (ARCHITECTURE 4.1).

The data root is %LOCALAPPDATA%\\<identifier>, deliberately different from the install
directory (%LOCALAPPDATA%\\JobForge): spike P1.0d showed the per-user installer uses the
product name, so a product-named data folder would sit inside the program files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from jobforge_agent import winutil

IDENTIFIER = "dev.kevin.jobforge"
DATA_DIR_ENV = "JOBFORGE_DATA_DIR"  # tests and verify.sh only


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def db(self) -> Path:
        return self.data / "jobforge.db"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def run(self) -> Path:
        return self.root / "run"

    @property
    def lock(self) -> Path:
        return self.run / "agent.lock"

    @property
    def ipc(self) -> Path:
        return self.run / "ipc.json"

    def ensure(self) -> None:
        for d in (self.data, self.logs, self.backups, self.run):
            d.mkdir(parents=True, exist_ok=True)


def resolve_paths() -> Paths:
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Paths(Path(override))
    return Paths(winutil.local_app_data() / IDENTIFIER)
