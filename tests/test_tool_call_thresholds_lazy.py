#!/usr/bin/env python3
"""Tool-call fatigue thresholds follow the detected context window (#246).

THE BUG
-------
``_TOOL_CALL_WARN, _TOOL_CALL_CRITICAL = _scaled_tool_call_thresholds()`` ran at
module import in measure.py. ``_scaled_tool_call_thresholds`` calls
``detect_context_window``, which reads ``_resolve_feature_env`` and
``_TRUTHY_ENV_VALUES``, both defined ~11k lines further down the file. At that
point the call raised ``NameError``; the ``except Exception: ctx_size = 200_000``
fallback swallowed it, and every process kept the 200K thresholds (25/40) for
its whole life. A 1M session was told "CRITICAL: 40+ tool calls" once fill
crossed 50%, instead of the scaled 324.

THE CLASS
---------
Any module-level call that reaches a name defined later in measure.py and sits
under a broad ``except`` fails this same silent way. ``test_import_raises_no_name_error``
traces the import of measure.py and fails on ANY NameError raised inside the
module, swallowed or not, so the class cannot come back quietly.

Each test imports measure in a FRESH subprocess with a sandboxed HOME: the
thresholds are a property of import order, which an already-imported module
in the pytest process cannot show.

Run: python3 -m pytest tests/test_tool_call_thresholds_lazy.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"

# Everything that steers detect_context_window(), dropped so the subprocess sees
# only what the test sets.
_DROPPED_ENV_PREFIXES = ("TOKEN_OPTIMIZER_", "CLAUDE_", "ANTHROPIC_", "CODEX_", "OPENAI_", "XDG_")


def _sandbox_env(home: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(_DROPPED_ENV_PREFIXES)}
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def _run(code: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=str(SCRIPTS),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


# --------------------------------------------------------------------------- #
# The class: no NameError may be raised anywhere inside measure.py at import.
# --------------------------------------------------------------------------- #

_TRACE_IMPORT = """
    import json, os, sys
    target = os.path.join(os.getcwd(), "measure.py")
    seen = []

    def local(frame, event, arg):
        if event == "exception" and isinstance(arg[1], NameError):
            seen.append({"name": frame.f_code.co_name, "line": frame.f_lineno, "error": str(arg[1])})
        return local

    def tracer(frame, event, arg):
        if event == "call" and frame.f_code.co_filename == target:
            return local
        return None

    sys.settrace(tracer)
    try:
        import measure
    finally:
        sys.settrace(None)
    # One NameError propagates through every frame it unwinds; report each site once.
    uniq = []
    for s in seen:
        if s not in uniq:
            uniq.append(s)
    print(json.dumps(uniq))
"""


def test_import_raises_no_name_error(tmp_path):
    """Import measure.py under a tracer: a NameError anywhere inside the module
    means a module-level statement reached a name defined further down, and a
    broad ``except`` is the only reason the import still succeeded."""
    proc = _run(_TRACE_IMPORT, _sandbox_env(tmp_path))
    assert proc.returncode == 0, proc.stderr[-2000:]
    raised = json.loads(proc.stdout.strip().splitlines()[-1])
    assert raised == [], (
        "NameError raised during import of measure.py (swallowed by a broad "
        f"except, so the import still succeeded):\n{json.dumps(raised, indent=2)}"
    )


# --------------------------------------------------------------------------- #
# The instance: thresholds track the configured window in a fresh process.
# --------------------------------------------------------------------------- #

_SCORE_AT_100_CALLS = """
    import json, sys
    import measure as m
    # 60% fill of the configured window, 100 cumulative tool calls, nothing else.
    window, source = m.detect_context_window()
    qd = {
        "messages": [(0, "user", 4000, True), (1, "assistant", 8000, True)],
        "tool_results": [(2, "t1", 3000, False)],
        "tool_result_meta": [],
        "system_reminders": [],
        "reads": [],
        "writes": [],
        "compactions": 0,
        "tool_calls": 100,
        "agent_dispatches": [],
        "decisions": [],
        "total_entries": 3,
        "context_tokens": int(window * 0.6),
        "model": "claude-sonnet-4-6",
        "model_context_window": None,
        "topic": "fresh session",
    }
    res = m.compute_quality_score(qd, session_id=None)
    # Pre-fix measure.py exposes the import-time list; post-fix the resolver.
    resolve = getattr(m, "_tool_call_warn_thresholds", None)
    thresholds = resolve() if resolve else m._TOOL_CALL_WARN_THRESHOLDS
    print(json.dumps({
        "window": window,
        "source": source,
        "thresholds": [(t, level) for t, level, _ in thresholds],
        "warning": res.get("tool_call_warning"),
    }))
"""


@pytest.mark.parametrize(
    "window, expected_crit, expected_warn, expected_level",
    [
        # 1M: x^1.3 scaling -> 202/324, so 100 calls at 60% fill is quiet.
        (1_000_000, 324, 202, None),
        # 200K control: the base 25/40 still fires, so laziness lost nothing.
        (200_000, 40, 25, "CRITICAL"),
    ],
)
def test_thresholds_follow_configured_window(tmp_path, window, expected_crit, expected_warn, expected_level):
    env = _sandbox_env(tmp_path, TOKEN_OPTIMIZER_CONTEXT_SIZE=str(window))
    proc = _run(_SCORE_AT_100_CALLS, env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["window"] == window, out
    assert out["thresholds"] == [[expected_crit, "CRITICAL"], [expected_warn, "WARNING"]], out
    level = out["warning"]["level"] if out["warning"] else None
    assert level == expected_level, out


def test_thresholds_follow_settings_model(tmp_path):
    """The reporter's setup: ``"model": "opus"`` in settings.json, nothing in the
    environment. The window comes from the settings file the lazy path can
    now read, and the thresholds scale with it."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    proc = _run(_SCORE_AT_100_CALLS, _sandbox_env(tmp_path))
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["window"] == 1_000_000, out
    assert "opus" in out["source"], out
    assert out["thresholds"] == [[324, "CRITICAL"], [202, "WARNING"]], out
    assert out["warning"] is None, out
