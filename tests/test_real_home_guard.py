"""Self-tests for tests/_home_guard.py: the sandbox is active and the audit
guard flags real-home access. Uses nonexistent paths under the real home, so no
real data is read or written."""

import os
import sys
from pathlib import Path

import pytest

import _home_guard

pytestmark = pytest.mark.skipif(
    not (_home_guard.sandbox_enabled() and _home_guard.guard_enabled()),
    reason="real-home sandbox/guard disabled by env",
)

SYNTH = "synthetic::home-guard-selftest"


def _hits_for_synth():
    return _home_guard.take_hits(SYNTH)


def test_home_is_sandboxed():
    real = _home_guard.REAL_HOME
    sandbox = str(_home_guard.SANDBOX) + os.sep
    # On Windows the sandbox sits in AppData/Local/Temp, under the real home,
    # so assert "inside the sandbox" rather than "outside the real home".
    assert os.path.realpath(Path.home()) != real
    assert os.path.realpath(Path.home()).startswith(sandbox)
    assert os.path.realpath(os.path.expanduser("~")).startswith(sandbox)
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "HERMES_HOME",
                "TOKEN_OPTIMIZER_SNAPSHOT_DIR", "CLAUDE_PLUGIN_DATA"):
        assert var not in os.environ or not os.environ[var].startswith(real + os.sep)


def test_import_time_paths_in_measure_are_not_under_real_home():
    scripts = str(Path(__file__).resolve().parent.parent / "skills/token-optimizer/scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import measure

    sandbox = str(_home_guard.SANDBOX) + os.sep
    for name in ("SNAPSHOT_DIR", "TRENDS_DB", "CLAUDE_DIR", "RUNTIME_DIR", "SETTINGS_PATH"):
        value = os.path.realpath(str(getattr(measure, name)))
        assert value.startswith(sandbox), f"measure.{name} cached outside the sandbox: {value}"


def test_guard_flags_read_write_and_sqlite_under_real_home():
    real = Path(_home_guard.REAL_HOME)
    ghost = real / ".claude" / "__home_guard_selftest_nonexistent__"
    _home_guard.set_current(SYNTH)
    try:
        with pytest.raises(OSError):
            open(ghost / "trends.db", "rb")
        with pytest.raises(OSError):
            open(ghost / "out.json", "w")
        with pytest.raises(OSError):
            os.listdir(ghost)
        with pytest.raises(OSError):
            os.mkdir(ghost / "a" / "b")
    finally:
        _home_guard.set_current("")
    kinds = {(h[1], h[2]) for h in _hits_for_synth()}
    assert ("open", "R") in kinds
    assert ("open", "W") in kinds
    assert ("os.listdir", "LIST") in kinds
    assert ("os.mkdir", "W") in kinds


def test_guard_ignores_sandbox_repo_and_tmp(tmp_path):
    _home_guard.set_current(SYNTH)
    try:
        (tmp_path / "x.txt").write_text("ok")
        (_home_guard.SANDBOX / "home" / "y.txt").write_text("ok")
        open(Path(__file__), "rb").close()
    finally:
        _home_guard.set_current("")
    assert _hits_for_synth() == []


def test_allowlist_only_exempts_matching_kind_and_path(monkeypatch):
    real = Path(_home_guard.REAL_HOME)
    # Parent dir does not exist, so even an un-blocked "w" open fails and
    # nothing can ever be created under the real home.
    probe = real / ".claude" / "__home_guard_absent_dir__" / "probe"
    monkeypatch.setattr(_home_guard, "ALLOWLIST",
                        [("R", r"^\.claude/__home_guard_absent_dir__/probe$", "selftest")])
    _home_guard.set_current(SYNTH)
    try:
        with pytest.raises(OSError):
            open(probe, "rb")
        with pytest.raises(OSError):
            open(probe, "w")
    finally:
        _home_guard.set_current("")
    kinds = {(h[1], h[2]) for h in _hits_for_synth()}
    assert kinds == {("open", "W")}


def test_guard_flags_exists_and_stat_probes_under_real_home():
    # Path.exists() on a cached path steered the old trends.db leak and raises
    # no audit event, so the guard wraps os.stat/lstat/access.
    ghost = Path(_home_guard.REAL_HOME) / ".claude" / "__home_guard_absent_dir__" / "trends.db"
    _home_guard.set_current(SYNTH)
    try:
        assert ghost.exists() is False
        assert os.path.lexists(ghost) is False
        assert os.access(ghost, os.R_OK) is False
    finally:
        _home_guard.set_current("")
    events = {h[1] for h in _hits_for_synth()}
    # Windows (and Path.exists() from 3.14) take os.path's C fast paths, which
    # never call os.stat/os.lstat, so either the os or os.path wrapper must fire.
    assert "os.access" in events
    assert events & {"os.stat", "os.path.exists"}
    assert events & {"os.lstat", "os.path.lexists"}
