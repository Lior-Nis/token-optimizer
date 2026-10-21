#!/usr/bin/env python3
"""A success claim must come with the outcome it claims (false-success class).

Each escape below told the user "done" while the thing was not done, and the
suite stayed green because the tests checked the message or an inner function:

  #253  the PreCompact checkpoint hook exited 0 on Windows while its budget had
        cut the capture off: no checkpoint, and the next restore used an older one.
  #226  doctor printed "Smart Compaction 1/4" (and the Hooks tab "Session
        Tracking off") for a plugin install whose hooks all ran.
  #149  fleet scan printed "0 scanned" for a system detect() had just found.

These tests assert the outcome itself, through the entry point the user hits:
the file on disk and the context the next session actually receives, the line
doctor prints, the warning next to a zero.

Run: python3 -m pytest tests/test_false_success.py -q
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_harness import (  # noqa: E402
    REPO, SESSION_ID, consented_home, hook_command, payload, run_hook, transcript)

MEASURE = REPO / "skills" / "token-optimizer" / "scripts" / "measure.py"
FLEET = REPO / "skills" / "fleet-auditor" / "scripts" / "fleet.py"

posix_only = pytest.mark.skipif(os.name == "nt", reason="drives the hooks.json command through /bin/sh, which native Windows lacks")


def _sandbox_env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_", "TOKEN_OPTIMIZER_", "CODEX_", "OPENCODE_", "HERMES_"))
           or k == "TOKEN_OPTIMIZER_COLD_COMPILE_WAIT_S"}
    env.update({"HOME": str(home), "USERPROFILE": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "XDG_STATE_HOME": str(home / ".local" / "state"),
                "TOKEN_OPTIMIZER_NO_PROC_SCAN": "1"})
    return env


# --------------------------------------------------------------------------- #
# #253: the before-compact checkpoint exists and the next session gets it.
# --------------------------------------------------------------------------- #

@posix_only
def test_precompact_hook_writes_the_checkpoint_the_restore_injects(tmp_path):
    """PreCompact (real hooks.json command) -> a checkpoint on disk holding this
    session's task and decision -> SessionStart(source=compact) (real command)
    puts that same content into the model's context. Exit 0 alone proves nothing:
    #253's hook exited 0 with no checkpoint written."""
    home = consented_home(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    tpath = transcript(project, home)
    log = tmp_path / "raises.jsonl"

    capture = hook_command("PreCompact", "compact-capture --trigger auto")
    proc = run_hook(capture, REPO, payload("PreCompact", None, tpath, project), home, log)
    assert proc.returncode == 0, proc.stderr[-2000:]

    ckpt_dir = home / ".claude" / "token-optimizer" / "checkpoints"
    written = sorted(p for p in ckpt_dir.glob(f"{SESSION_ID}-*.md") if "progressive" not in p.name) \
        if ckpt_dir.is_dir() else []
    assert written, ("PreCompact hook exited 0 but wrote no checkpoint for the session "
                     f"(the #253 false success). Checkpoint dir: {sorted(ckpt_dir.glob('*')) if ckpt_dir.is_dir() else 'missing'}")
    body = written[-1].read_text(encoding="utf-8")
    # Hand-checked against the transcript in _hook_harness.transcript().
    assert "Please refactor the parser in app.py and add tests." in body
    assert "Decided to split the parser into tokenize() and parse()" in body
    meta = written[-1].with_suffix(".json")
    assert meta.is_file() and json.loads(meta.read_text(encoding="utf-8"))["trigger"] == "auto"

    restore = hook_command("SessionStart")
    pl = payload("SessionStart", None, tpath, project)
    pl["source"] = "compact"
    proc = run_hook(restore, REPO, pl, home, log)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Decided to split the parser into tokenize() and parse()" in ctx, ctx[:1500]
    assert "Please refactor the parser in app.py and add tests." in ctx, ctx[:1500]


# --------------------------------------------------------------------------- #
# #226: doctor counts the hooks a marketplace plugin really runs.
# --------------------------------------------------------------------------- #

def _plugin_install_home(tmp_path: Path) -> Path:
    """A marketplace plugin install: the shipped hooks.json in the plugin cache,
    no hook entries in settings.json (the layout #226 was reported on)."""
    home = consented_home(tmp_path)
    cache = home / ".claude" / "plugins" / "cache" / "alexgreensh-token-optimizer" / "token-optimizer" / "5.13.51" / "hooks"
    cache.mkdir(parents=True)
    (cache / "hooks.json").write_text((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"), encoding="utf-8")
    return home


def test_doctor_counts_every_hook_a_plugin_install_runs(tmp_path):
    home = _plugin_install_home(tmp_path)
    proc = subprocess.run([sys.executable, str(MEASURE), "doctor"], env=_sandbox_env(home),
                          cwd=str(tmp_path), capture_output=True, text=True, timeout=120)
    assert proc.returncode in (0, 1), proc.stderr[-2000:]
    out = proc.stdout
    smart = re.search(r"Smart Compaction:\s+(.*)", out)
    assert smart, out
    # PreCompact, SessionStart, Stop, SessionEnd all ship in hooks.json.
    assert smart.group(1).startswith("4/4"), f"doctor under-counts the shipped hooks: {smart.group(0)}"
    assert "missing: SessionStart" not in out and "missing:" not in smart.group(1)
    session_end = re.search(r"\[(OK|!!)\]\s+SessionEnd hook:\s+(.*)", out)
    assert session_end and session_end.group(1) == "OK", (session_end and session_end.group(0)) or out


# --------------------------------------------------------------------------- #
# #149: a zero from fleet always says why.
# --------------------------------------------------------------------------- #

# The marker each adapter's detect() looks for, created empty: the system is
# installed, and fleet has no reader for its runs (or none recorded yet).
_DETECT_MARKERS = {
    "opencode": ".local/share/opencode",
    "nanoclaw": ".nanoclaw",
    "ironclaw": ".ironclaw",
    "openclaw": ".openclaw/agents",
    "hermes": ".hermes",
}


@pytest.mark.parametrize("system", sorted(_DETECT_MARKERS))
def test_fleet_scan_never_reports_a_silent_zero_for_a_detected_system(tmp_path, system):
    home = tmp_path / "home"
    (home / _DETECT_MARKERS[system]).mkdir(parents=True)
    env = _sandbox_env(home)
    env["OPENCODE_DATA_DIR"] = ""
    proc = subprocess.run([sys.executable, str(FLEET), "scan", "--system", system, "--json"], env=env,
                          cwd=str(tmp_path), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    data = json.loads(proc.stdout)
    rows = {row["system"]: row for row in data["systems"]}
    assert system in rows, f"the {system} marker no longer makes detect() find it; update _DETECT_MARKERS:\n{proc.stdout}"
    assert rows[system]["scanned"] == 0  # an empty install: nothing to read
    assert data["errors"] and rows[system]["errors"] >= 1, (
        f"fleet scan --system {system}: the system was detected, 0 runs came back, and "
        f"nothing says why (the #149 silent zero):\n{proc.stdout}")


def test_fleet_unknown_system_is_an_error_not_a_zero(tmp_path):
    """`--system claude-code` (the real name is `claude`) used to print
    "0 new runs collected" and exit 0."""
    home = tmp_path / "home"
    (home / ".claude" / "projects" / "-p").mkdir(parents=True)
    for cmd in ("scan", "audit", "report"):
        proc = subprocess.run([sys.executable, str(FLEET), cmd, "--system", "claude-code"],
                              env=_sandbox_env(home), cwd=str(tmp_path), capture_output=True, text=True, timeout=120)
        assert proc.returncode == 2, (cmd, proc.returncode, proc.stdout, proc.stderr)
        assert "claude-code" in proc.stderr and "claude" in proc.stderr.split("Known systems:")[-1], proc.stderr
