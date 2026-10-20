"""#265: on the MSIX-packaged Windows desktop app, %APPDATA% is virtualized, so
after the launcher's `cd` the process cwd is the physical
...\\AppData\\Local\\Packages\\<pkg>\\LocalCache\\Roaming\\... path, while Claude
Code names the project dir from the logical ...\\AppData\\Roaming\\... path.
find_projects_dir() then fell back to "most recently modified project", which
can be a DIFFERENT project. These tests use the reporter's real shapes.
"""
import os
import sys
import time
from pathlib import Path, PureWindowsPath

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"))
import measure  # noqa: E402

PHYSICAL = (r"C:\Users\u\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache"
            r"\Roaming\Claude\scratch-workspaces\abc")
LOGICAL = r"C:\Users\u\AppData\Roaming\Claude\scratch-workspaces\abc"
PROJECT = "C--Users-u-AppData-Roaming-Claude-scratch-workspaces-abc"


@pytest.fixture
def projects(tmp_path, monkeypatch):
    base = tmp_path / "claude"
    (base / "projects").mkdir(parents=True)
    monkeypatch.setattr(measure, "CLAUDE_DIR", base)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    measure._RESOLVED_MODEL_CACHE.clear()
    yield base / "projects"
    measure._RESOLVED_MODEL_CACHE.clear()


def _mkproject(projects, name, newer=False):
    d = projects / name
    d.mkdir()
    if newer:  # another project touched later: what the mtime fallback picks
        future = time.time() + 3600
        os.utime(d, (future, future))
    return d


def _cwd_is(monkeypatch, text):
    """Fake the process cwd for measure only; pytest itself keeps the real one."""
    class _FakeCwdPath(type(Path())):
        @classmethod
        def cwd(cls):
            return PureWindowsPath(text)

    monkeypatch.setattr(measure, "Path", _FakeCwdPath)


def test_msix_physical_path_maps_to_the_logical_appdata_path():
    assert measure._unvirtualize_msix_path(PHYSICAL) == LOGICAL
    local = r"C:\Users\u\AppData\Local\Packages\Claude_x\LocalCache\Local\Claude\w"
    assert measure._unvirtualize_msix_path(local) == r"C:\Users\u\AppData\Local\Claude\w"
    # Anything else is left alone.
    for other in (r"D:\Code\app", "/home/u/app", r"C:\Users\u\AppData\Roaming\Claude"):
        assert measure._unvirtualize_msix_path(other) == other


def test_msix_cwd_finds_its_own_project_not_the_newest_one(projects, monkeypatch):
    own = _mkproject(projects, PROJECT)
    _mkproject(projects, "C--Code-other-project", newer=True)
    _cwd_is(monkeypatch, PHYSICAL)
    assert measure.find_projects_dir() == own
    assert measure.cwd_to_project_dir_name() == PROJECT


def test_msix_subdirectory_still_finds_the_project(projects, monkeypatch):
    own = _mkproject(projects, PROJECT)
    _mkproject(projects, "C--Code-other-project", newer=True)
    _cwd_is(monkeypatch, PHYSICAL + r"\src\deep")
    assert measure.find_projects_dir() == own


def test_transcript_path_wins_over_a_misleading_cwd(projects, monkeypatch):
    own = _mkproject(projects, PROJECT)
    other = _mkproject(projects, "C--Code-other-project", newer=True)
    _cwd_is(monkeypatch, r"C:\Somewhere\Else")
    transcript = own / "1a2b3c4d-0000-4000-8000-000000000001.jsonl"
    transcript.write_text("{}\n")
    assert measure.find_projects_dir(transcript_path=str(transcript)) == own
    # A subagent transcript sits one level deeper; same project.
    sub = own / "1a2b3c4d-0000-4000-8000-000000000001" / "subagents" / "agent-a.jsonl"
    sub.parent.mkdir(parents=True)
    sub.write_text("{}\n")
    assert measure.find_projects_dir(transcript_path=str(sub)) == own
    # A transcript outside the projects tree is ignored, not trusted.
    assert measure.find_projects_dir(transcript_path=str(projects.parent / "x.jsonl")) == other


def test_payload_cwd_is_used_before_the_process_cwd(projects, monkeypatch):
    own = _mkproject(projects, PROJECT)
    _mkproject(projects, "C--Code-other-project", newer=True)
    _cwd_is(monkeypatch, r"C:\Somewhere\Else")
    assert measure.find_projects_dir(cwd=LOGICAL) == own


def test_session_model_is_read_from_the_sessions_own_transcript(projects, monkeypatch):
    """_resolve_session_model must find {session_id}.jsonl wherever it lives,
    not in whatever project the cwd lookup guessed (wrong model = wrong cost)."""
    sid = "1a2b3c4d-0000-4000-8000-000000000001"
    own = _mkproject(projects, PROJECT)
    _mkproject(projects, "C--Code-other-project", newer=True)
    (own / f"{sid}.jsonl").write_text(
        '{"message": {"model": "claude-opus-5-5", "role": "assistant"}}\n' * 3)
    _cwd_is(monkeypatch, r"C:\Somewhere\Else")
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    monkeypatch.setattr(measure, "detect_runtime", lambda: "claude")
    assert "opus" in measure._resolve_session_model(sid)
