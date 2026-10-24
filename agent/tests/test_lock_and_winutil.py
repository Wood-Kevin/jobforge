import re

from jobforge_agent import winutil
from jobforge_agent.instance_lock import InstanceLock
from jobforge_agent.paths import IDENTIFIER, Paths, resolve_paths


def test_second_lock_on_same_file_fails_until_released(tmp_path):
    first, second = InstanceLock(tmp_path / "agent.lock"), InstanceLock(tmp_path / "agent.lock")
    assert first.try_acquire()
    assert not second.try_acquire()
    first.release()
    assert second.try_acquire()
    second.release()


def test_user_sid_and_elevation_are_readable():
    assert re.fullmatch(r"S-1-5-21(-\d+)+", winutil.current_user_sid())
    assert isinstance(winutil.is_elevated(), bool)


def test_restricted_dir_has_exactly_the_user_ace(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    winutil.restrict_dir_to_user(d)
    entries = winutil.dir_acl_entries(d)
    assert len(entries) == 1 and entries[0].endswith("(OI)(CI)(F)"), entries


def test_restricted_dir_drops_explicit_aces(tmp_path):
    """An explicit ACE (here Administrators, read) must not survive; /inheritance:r alone kept it."""
    import subprocess
    d = tmp_path / "run"
    d.mkdir()
    subprocess.run(["icacls", str(d), "/grant", "*S-1-5-32-544:(OI)(CI)R"], check=True, capture_output=True)
    assert any("Administrators" in e and "(I)" not in e for e in winutil.dir_acl_entries(d))
    sid = winutil.restrict_dir_to_user(d)
    entries = winutil.dir_acl_entries(d)
    assert len(entries) == 1 and entries[0].endswith("(OI)(CI)(F)"), entries
    assert sid == winutil.current_user_sid()


def test_default_data_root_is_identifier_not_install_dir(monkeypatch):
    monkeypatch.delenv("JOBFORGE_DATA_DIR", raising=False)
    p = resolve_paths()
    assert p.root.name == IDENTIFIER and p.root.parent == winutil.local_app_data()
    assert p.root.name != "JobForge"  # the installer's folder (spike P1.0d defect 7)


def test_override_for_tests(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBFORGE_DATA_DIR", str(tmp_path))
    assert resolve_paths() == Paths(tmp_path)
