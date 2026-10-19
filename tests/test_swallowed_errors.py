#!/usr/bin/env python3
"""Fail-open must never hide a programming error (#246 class).

THE BUG
-------
#246: a module-level call in measure.py reached a name defined further down,
raised ``NameError``, and a broad ``except Exception`` swallowed it. Every hook
still exited 0 and printed what it always printed, so the suite stayed green
while 1M-window users got 200K fatigue thresholds for months.

The product is fail-open on purpose (a hook must never break a session), so
"the hook exited 0" proves nothing. What separates a legitimate fail-open (a
missing file, a locked DB, a foreign config) from a hidden bug is the TYPE of
what was raised: ``NameError`` / ``UnboundLocalError`` is never a world
condition, it is always code that cannot run. Neither is calling a function
with the wrong arguments, or reaching for a module attribute that does not
exist (a rename that missed a call site).

THE CHECK
---------
``tests/fixtures/raise_monitor/sitecustomize.py`` rides PYTHONPATH into every
Python process a real hook command starts (the exact ``hooks/hooks.json``
command string -> bash -> python-launcher.sh -> run.py -> module_runner -> the
script, plus any worker those spawn). It records every exception RAISED inside
the repo, swallowed or not. Each test drives one hooks.json entry on a
consented install with a realistic transcript, then fails on any
programming-error raise.

``test_monitor_catches_planted_swallowed_name_error`` is the self-test: it
plants a NameError inside a ``try/except Exception: pass`` in a copy of the
hook tree, shows the hook still exits 0 and prints nothing (exactly #246), and
shows the monitor reports it.

Run: python3 -m pytest tests/test_swallowed_errors.py -q
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_harness import (  # noqa: E402
    REPO, consented_home as _consented_home, hook_entries as _hook_entries,
    payload as _payload, read_log, run_hook, transcript as _transcript)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="drives the hooks.json command through /bin/sh, which native Windows lacks")

# Exceptions that are always a bug in our own code, never a world condition.
_PROGRAMMING_ERRORS = ("NameError",)  # UnboundLocalError is a NameError subclass
# TypeError/AttributeError are also used legitimately (duck typing, getattr
# fallbacks), so only their bug-shaped messages count.
_BUG_MESSAGES = (
    ("TypeError", re.compile(
        r"missing \d+ required (positional|keyword-only) argument"
        r"|takes \d+ positional arguments? but \d+ (were|was) given"
        r"|got an unexpected keyword argument"
        r"|got multiple values for argument")),
    # Dunder probes (__path__, __wrapped__...) are the import system and
    # introspection asking, answered by a module __getattr__: not a bug.
    ("AttributeError", re.compile(r"^module '[^']+' has no attribute '(?!__)")),
)


# A first-party module that cannot be imported from a first-party file is a
# wiring bug (wrong sys.path), never a missing optional dependency.
_FIRST_PARTY = frozenset(
    {p.stem for p in (REPO / "skills" / "token-optimizer" / "scripts").glob("*.py")}
    | {p.stem for p in (REPO / "hooks").glob("*.py")}
    | {p.name for p in (REPO / "skills" / "token-optimizer" / "scripts").iterdir()
       if p.is_dir() and (p / "__init__.py").exists()})
_NO_MODULE = re.compile(r"^No module named '([^'.]+)")


def is_programming_error(rec: dict) -> bool:
    if any(name in rec.get("mro", []) for name in _PROGRAMMING_ERRORS):
        return True
    if "ModuleNotFoundError" in rec.get("mro", []):
        m = _NO_MODULE.match(rec.get("msg", ""))
        if m and m.group(1) in _FIRST_PARTY:
            return True
    for name, pattern in _BUG_MESSAGES:
        if name in rec.get("mro", []) and pattern.search(rec.get("msg", "")):
            return True
    return False


def _fmt(recs):
    return "\n".join(f"  {r['type']} at {r['file']}:{r['line']} in {r['func']}(): {r['msg']}  [argv {r['argv']}]"
                     for r in recs)


@pytest.mark.parametrize("event, matcher, command", _hook_entries())
def test_hook_raises_no_programming_error(tmp_path, event, matcher, command):
    """Every hooks.json entry, through its real command string, on a consented
    install: no NameError / signature / missing-module-attribute raise in any
    process it starts, swallowed or not."""
    home = _consented_home(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    transcript = _transcript(project, home)
    log = tmp_path / "raises.jsonl"
    proc = run_hook(command, REPO, _payload(event, matcher, transcript, project), home, log)
    assert proc.returncode == 0, proc.stderr[-2000:]
    recs = read_log(log, settle=4.0)
    if os.environ.get("TO_TEST_RAISE_DUMP"):
        with open(os.environ["TO_TEST_RAISE_DUMP"], "a", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps({"event": event, **r}) + "\n")
    bugs = [r for r in recs if is_programming_error(r)]
    assert not bugs, ("programming error raised (and swallowed) while running the "
                      f"{event} hook:\n{_fmt(bugs)}")


# --------------------------------------------------------------------------- #
# Self-test: the monitor sees a NameError the product swallows.
# --------------------------------------------------------------------------- #

_PLANT_TARGET = "hooks/userpromptsubmit_runner.py"
_PLANT_ANCHOR = "def main("


def _copy_tree(dst: Path) -> Path:
    shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(
        ".git", "node_modules", "docs-site", "research", "__pycache__", "*.pyc", "tests",
        "desktop", "vscode-extension", "opencode", "pi", "openclaw", "design", "docs", "plugins"))
    return dst


def test_monitor_catches_planted_swallowed_name_error(tmp_path):
    """Plant #246's shape in a copy: a NameError inside try/except Exception: pass
    at the top of the UserPromptSubmit runner. The hook still exits 0 with the
    same output (that is the bug: fail-open hides it), and the monitor reports it."""
    root = _copy_tree(tmp_path / "plugin")
    target = root / _PLANT_TARGET
    src = target.read_text(encoding="utf-8")
    assert _PLANT_ANCHOR in src, "anchor moved; update the planted bug"
    planted = src.replace(
        _PLANT_ANCHOR,
        "def _planted_threshold():\n"
        "    try:\n"
        "        return _resolve_feature_env_defined_later()  # noqa: F821\n"
        "    except Exception:\n"
        "        return 25\n\n\n" + _PLANT_ANCHOR, 1)
    planted = planted.replace("if __name__ == \"__main__\":", "_planted_threshold()\n\nif __name__ == \"__main__\":", 1)
    assert planted.count("_planted_threshold()") == 2, "could not wire the planted call"
    target.write_text(planted, encoding="utf-8")

    entry = next(p for p in _hook_entries() if p.values[0] == "UserPromptSubmit")
    command = entry.values[2]
    home = _consented_home(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    transcript = _transcript(project, home)
    log = tmp_path / "raises.jsonl"
    proc = run_hook(command, root, _payload("UserPromptSubmit", None, transcript, project), home, log)
    assert proc.returncode == 0, "the planted bug must be swallowed, like #246"
    bugs = [r for r in read_log(log, settle=2.0) if is_programming_error(r)]
    assert any(r["type"] == "NameError" and "_resolve_feature_env_defined_later" in r["msg"]
               and r["file"].replace("\\", "/") == _PLANT_TARGET for r in bugs), _fmt(bugs) or "monitor saw nothing"


# --------------------------------------------------------------------------- #
# The user-facing CLI: the same check for the commands people run by hand.
# --------------------------------------------------------------------------- #

# Read-only or sandbox-only commands a user runs from the skill or a terminal.
# Installers, schedulers and destructive commands are left out on purpose.
_CLI_COMMANDS = [
    ["collect"], ["report"], ["quick"], ["doctor"], ["cloud-doctor"], ["health"],
    ["version"], ["drift"], ["trends"], ["savings"], ["compression-stats"],
    ["cache-report"], ["list-checkpoints"], ["checkpoint-stats"], ["read-cache-stats"],
    ["recommendations"], ["quality", "current"], ["status-bar"], ["runway-json"],
    ["pricing-tier"], ["daemon-status"], ["check-hook"], ["coach"], ["compact-advice"],
    ["cohorts"], ["git-context"], ["security-report"], ["dashboard"],
]


def _no_op_openers(root: Path) -> Path:
    shims = root / "openers"
    shims.mkdir()
    for name in ("open", "xdg-open"):
        p = shims / name
        p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        p.chmod(0o755)
    return shims


@pytest.mark.parametrize("argv", _CLI_COMMANDS, ids=lambda a: " ".join(a))
def test_cli_command_raises_no_programming_error(tmp_path, argv):
    home = _consented_home(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    _transcript(project, home)
    log = tmp_path / "raises.jsonl"
    shims = _no_op_openers(tmp_path)
    script = REPO / "skills" / "token-optimizer" / "scripts" / "measure.py"
    command = f'PATH="{shims}:$PATH" exec "{sys.executable}" "{script}" ' + " ".join(argv)
    proc = run_hook(command, REPO, {"cwd": str(project)}, home, log, timeout=180)
    recs = read_log(log, settle=2.0)
    if os.environ.get("TO_TEST_RAISE_DUMP"):
        with open(os.environ["TO_TEST_RAISE_DUMP"], "a", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps({"event": "cli " + " ".join(argv), **r}) + "\n")
    bugs = [r for r in recs if is_programming_error(r)]
    assert not bugs, f"measure.py {' '.join(argv)} raised (and swallowed):\n{_fmt(bugs)}"
    assert "Traceback (most recent call last)" not in proc.stderr, proc.stderr[-3000:]
