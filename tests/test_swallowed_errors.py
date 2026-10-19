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
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MONITOR_DIR = Path(__file__).resolve().parent / "fixtures" / "raise_monitor"
HOOKS_JSON = REPO / "hooks" / "hooks.json"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="hooks.json commands are POSIX sh; Windows runs them through Git Bash, covered by the wire tests")

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

SESSION_ID = "0f6e3c1a-5b2d-4e8f-9a1b-246246246246"


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


def _hook_entries():
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    out = []
    for event, groups in data["hooks"].items():
        for gi, group in enumerate(groups):
            for hi, hook in enumerate(group["hooks"]):
                if hook.get("type") != "command":
                    continue
                cmd = hook["command"]
                m = re.search(r'run\.py\\?"? ([^;]+?);', cmd)
                label = m.group(1).strip() if m else f"{gi}.{hi}"
                out.append(pytest.param(event, group.get("matcher"), cmd,
                                        id=f"{event}[{group.get('matcher') or '*'}]:{label}"))
    return out


def _transcript(project: Path, home: Path) -> Path:
    """A short but realistic Claude Code transcript: usage, a tool round-trip,
    a file read, enough for quality scoring and checkpoint capture to run."""
    read_target = project / "app.py"
    read_target.write_text("def main():\n    return 42\n", encoding="utf-8")
    ts = "2026-10-19T09:00:%02d.000Z"
    usage = {"input_tokens": 1200, "cache_creation_input_tokens": 4000,
             "cache_read_input_tokens": 60000, "output_tokens": 350}
    recs = [
        {"type": "user", "sessionId": SESSION_ID, "cwd": str(project), "timestamp": ts % 0,
         "uuid": "u1", "message": {"role": "user", "content": "Please refactor the parser in app.py and add tests."}},
        {"type": "assistant", "sessionId": SESSION_ID, "cwd": str(project), "timestamp": ts % 1,
         "uuid": "a1", "parentUuid": "u1",
         "message": {"id": "msg_1", "model": "claude-opus-4-6", "role": "assistant", "usage": usage,
                     "content": [{"type": "text", "text": "Reading the file first."},
                                 {"type": "tool_use", "id": "toolu_01", "name": "Read",
                                  "input": {"file_path": str(read_target)}}]}},
        {"type": "user", "sessionId": SESSION_ID, "cwd": str(project), "timestamp": ts % 2,
         "uuid": "u2", "parentUuid": "a1",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01",
                                                  "content": "1\tdef main():\n2\t    return 42\n"}]}},
        {"type": "assistant", "sessionId": SESSION_ID, "cwd": str(project), "timestamp": ts % 3,
         "uuid": "a2", "parentUuid": "u2",
         "message": {"id": "msg_2", "model": "claude-opus-4-6", "role": "assistant", "usage": usage,
                     "content": [{"type": "tool_use", "id": "toolu_02", "name": "Bash",
                                  "input": {"command": "git status"}}]}},
        {"type": "user", "sessionId": SESSION_ID, "cwd": str(project), "timestamp": ts % 4,
         "uuid": "u3", "parentUuid": "a2",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_02",
                                                  "content": "On branch main\nnothing to commit"}]}},
        {"type": "assistant", "sessionId": SESSION_ID, "cwd": str(project), "timestamp": ts % 5,
         "uuid": "a3", "parentUuid": "u3",
         "message": {"id": "msg_3", "model": "claude-opus-4-6", "role": "assistant", "usage": usage,
                     "content": [{"type": "text", "text": "Decided to split the parser into tokenize() and parse()."}]}},
    ]
    slug = "-" + str(project).strip("/").replace("/", "-").replace(".", "-")
    path = home / ".claude" / "projects" / slug / f"{SESSION_ID}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return path


def _payload(event: str, matcher: str | None, transcript: Path, project: Path) -> dict:
    base = {"session_id": SESSION_ID, "transcript_path": str(transcript), "cwd": str(project),
            "hook_event_name": event, "permission_mode": "default"}
    tool = (matcher or "").split("|")[0]
    if tool.startswith("mcp__"):
        tool = "mcp__github__get_file_contents"
    if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        tool = tool or "Bash"
        inputs = {
            "Read": {"file_path": str(project / "app.py")},
            "Bash": {"command": "git log --oneline -5"},
            "Agent": {"description": "survey", "prompt": "Survey the parser", "subagent_type": "general-purpose"},
        }
        base["tool_name"] = tool
        base["tool_input"] = inputs.get(tool, {"owner": "a", "repo": "b", "path": "README.md"})
        base["tool_use_id"] = "toolu_09"
        if event == "PostToolUse":
            base["tool_response"] = {"stdout": "abc123 first\n" * 40, "stderr": "", "interrupted": False}
        if event == "PostToolUseFailure":
            base["error"] = "Exit code 1\nfatal: not a git repository"
    elif event == "UserPromptSubmit":
        base["prompt"] = "now add tests for tokenize()"
    elif event == "SessionStart":
        base["source"] = "startup"
        base["model"] = "claude-opus-4-6"
    elif event in ("PreCompact", "PostCompact"):
        base["trigger"] = "auto"
        base["custom_instructions"] = ""
    elif event == "SessionEnd":
        base["reason"] = "other"
    elif event == "StopFailure":
        base["error"] = "rate_limit"
    elif event == "CwdChanged":
        base["old_cwd"] = str(project)
        base["new_cwd"] = str(project)
    elif event == "Stop":
        base["stop_hook_active"] = False
    return base


def _consented_home(root: Path) -> Path:
    home = root / "home"
    cfg = home / ".claude" / "token-optimizer" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"v5_welcome_shown": True, "enterprise_consent_shown": True}), encoding="utf-8")
    return home


def run_hook(command: str, plugin_root: Path, payload: dict, home: Path, log: Path,
             timeout: float = 90.0) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_", "TOKEN_OPTIMIZER_", "CODEX_", "TO_TEST_"))}
    env.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"), "XDG_STATE_HOME": str(home / ".local" / "state"),
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        "CLAUDE_PROJECT_DIR": payload["cwd"],
        "TOKEN_OPTIMIZER_NO_PROC_SCAN": "1",
        "PYTHONPATH": str(MONITOR_DIR),
        "TO_TEST_RAISE_LOG": str(log),
        "TO_TEST_RAISE_ROOT": str(plugin_root),
    })
    return subprocess.run(["/bin/sh", "-c", command], input=json.dumps(payload), cwd=payload["cwd"],
                          env=env, capture_output=True, text=True, timeout=timeout)


def read_log(log: Path, settle: float = 0.0) -> list[dict]:
    """Records from every process, waiting briefly for async/detached workers."""
    deadline = time.monotonic() + settle
    last = -1
    while True:
        size = log.stat().st_size if log.exists() else 0
        if time.monotonic() >= deadline or (size == last and settle and time.monotonic() > deadline - settle + 1.5):
            break
        last = size
        time.sleep(0.5)
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


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
