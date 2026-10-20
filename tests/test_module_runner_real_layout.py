#!/usr/bin/env python3
"""module_runner arms the hook deadline for the runners that live in hooks/.

THE BUG
-------
module_runner.py imports ``hook_runtime`` to arm each entry's self-imposed
deadline (and the 110s Windows orphan backstop), inside ``try/except
Exception``. ``hook_runtime`` lives in skills/token-optimizer/scripts, but the
consolidated runners (posttooluse / sessionstart / stop / userpromptsubmit)
live in hooks/, so for them scripts_dir is hooks/ and the import ALWAYS failed.
The except swallowed it: since 5.13.0 the PostToolUse runner's 4.5s budget and
every runner's 110s backstop were never armed in a real install.

tests/test_hook_entry_budgets.py never saw it: its fixture copies
hook_runtime.py next to a stub script, a layout no real hook has. This test
runs the REAL module_runner against the REAL runner paths and reads back the
deadline that is actually live when the runner starts.

Run: python3 -m pytest tests/test_module_runner_real_layout.py -q
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

_PROBE = textwrap.dedent("""
    import json, runpy, sys, threading
    sys.argv = ["module_runner.py", sys.argv[1], sys.argv[2]]
    sys.path.insert(0, {hooks!r})
    import module_runner

    def fake_run_module(name, run_name=None, alter_sys=False):
        hr = sys.modules.get("hook_runtime")
        cur = getattr(getattr(hr, "_DEADLINE_LOCAL", None), "current", None) if hr else None
        alive = [t.name for t in threading.enumerate() if t.name == "token-optimizer-hook-deadline"]
        print(json.dumps({{"seconds": getattr(cur, "seconds", None), "threads": len(alive)}}))

    runpy.run_module = fake_run_module
    module_runner.main()
""")


@pytest.mark.parametrize("plugin_root", [
    REPO,
    REPO / "plugins" / "token-optimizer",
    REPO / "cowork" / "token-optimizer",
], ids=["canonical", "codex-mirror", "cowork-mirror"])
@pytest.mark.parametrize("runner, expected_seconds", [
    # hook_runtime.BUDGET_POSTTOOL_RUNNER: the PostToolUse runner's own budget.
    ("posttooluse_runner", 4.5),
    # Unbudgeted consolidated runners keep module_runner's 110s orphan backstop.
    ("sessionstart_runner", 110.0),
    ("stop_runner", 110.0),
    ("userpromptsubmit_runner", 110.0),
])
def test_runner_deadline_is_armed_in_the_real_layout(tmp_path, plugin_root, runner, expected_seconds):
    hooks = plugin_root / "hooks"
    if not (hooks / f"{runner}.py").is_file():
        pytest.skip(f"{runner} not shipped in {plugin_root.name}")
    # Keep the cold-compile wait conftest sets on Windows CI: with a fresh
    # PYTHONPYCACHEPREFIX the hook must wait for the first compile, not fail
    # open (#264), or this probe has nothing to read.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("TOKEN_OPTIMIZER_") or k == "TOKEN_OPTIMIZER_COLD_COMPILE_WAIT_S"}
    env["PYTHONPYCACHEPREFIX"] = str(tmp_path / "pyc")
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE.format(hooks=str(hooks)), str(hooks), runner],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["seconds"] == expected_seconds and out["threads"] == 1, (
        f"{plugin_root.name}/hooks/{runner}: live deadline {out}; expected one "
        f"{expected_seconds}s deadline. hook_runtime was not importable from "
        "module_runner, so the swallowed ImportError left the runner unguarded.")
