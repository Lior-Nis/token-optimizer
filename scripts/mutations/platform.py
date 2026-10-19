"""Platform-reality regressions (Windows above all), re-planted for CI proof.

Shape (shared by every scripts/mutations/<area>.py):
    MUTATIONS = [(name, [test node ids], file_relpath, [(old, new), ...])]

Each entry re-introduces a bug that shipped (or the hot-path shape of one) into
a throwaway copy; the listed tests must FAIL on it. Every ``old`` must match the
file exactly once, or the check reports the entry as stale.
"""

from __future__ import annotations

import json

MEASURE = "skills/token-optimizer/scripts/measure.py"
BUDGET = "tests/test_hot_path_process_budget.py"

# --- #139: UserPromptSubmit fanned out to six hooks.json entries -------------
_UPS_ENTRY = (
    '      {\n'
    '        "hooks": [\n'
    '          {\n'
    '            "type": "command",\n'
    '            "command": ' + json.dumps(
        'for b in bash /bin/bash /usr/bin/bash /usr/local/bin/bash /opt/homebrew/bin/bash; '
        'do command -v "$b" >/dev/null 2>&1 || continue; L="${CLAUDE_PLUGIN_ROOT}/hooks/python-launcher.sh"; '
        '[ -r "$L" ] || exit 0; exec "$b" "$L" "${CLAUDE_PLUGIN_ROOT}/hooks/run.py" '
        'skills/token-optimizer/scripts/measure.py %s; done; exit 0') + ',\n'
    '            "timeout": 10\n'
    '          }\n'
    '        ]\n'
    '      },\n'
)
_PRE_139_UPS = "".join(_UPS_ENTRY % sub for sub in (
    "quality-cache --warn --quiet", "prompt-continuity --quiet", "verbosity-steer --quiet",
    "ensure-health --once-per-session", "quality-cache --force --quiet --once-per-session"))

MUTATIONS = [
    # 5.13.50 (78670f91): the full scheduler guard (a `schtasks /Query` on
    # Windows) ran ahead of the cheap gates in the three daemon writers, so
    # every healthy Windows SessionStart spawned schtasks. Fixed in 143f9841.
    ("5.13.50 schtasks /Query on every healthy SessionStart (guard before cheap gates)",
     [BUDGET + "::test_healthy_session_start_starts_no_scheduler_query",
      "tests/test_cloud_parity.py::test_scheduler_writers_run_cheap_gates_before_the_job_query"],
     MEASURE,
     [("    # cheap gates and the health probe say a repair is actually due.\n"
       "    if _daemon_snapshot_sandboxed():\n",
       "    # cheap gates and the health probe say a repair is actually due.\n"
       "    if _scheduler_install_refused():\n"),
      ('    if _daemon_snapshot_sandboxed():\n        return "noop-sandbox"\n    try:\n'
       "        # Already current (a sibling session fixed it)?",
       '    if _scheduler_install_refused():\n        return "noop-sandbox"\n    try:\n'
       "        # Already current (a sibling session fixed it)?"),
      ("    # cheap gates below say a heal is actually due.\n"
       "    if _daemon_snapshot_sandboxed():\n",
       "    # cheap gates below say a heal is actually due.\n"
       "    if _scheduler_install_refused():\n")]),

    # Same class, still live in 5.13.51 and found by the budget test: the
    # macOS-only keep-warm plist heal ran the full guard first, so on Windows
    # every SessionStart's ensure-health spawned `schtasks /Query`.
    ("keep-warm plist heal queries the Windows scheduler on every SessionStart",
     [BUDGET + "::test_healthy_session_start_starts_no_scheduler_query"],
     MEASURE,
     [("once a stale plist exists and needs a rewrite.\n"
       "    if _daemon_snapshot_sandboxed():\n        return False\n",
       "once a stale plist exists and needs a rewrite.\n"
       "    if _scheduler_install_refused():\n        return False\n")]),

    # #139: six UserPromptSubmit entries, each its own process chain.
    ("#139 UserPromptSubmit fans out to six hooks.json entries",
     [BUDGET + "::test_hooks_json_python_processes_per_event_within_budget"],
     "hooks/hooks.json",
     [('    "UserPromptSubmit": [\n', '    "UserPromptSubmit": [\n' + _PRE_139_UPS)]),

    # #139 in-runner shape: the consolidated runner shells out to measure.py
    # for a subcommand instead of calling it in-process.
    ("#139 UserPromptSubmit runner spawns measure.py per subcommand",
     [BUDGET + "::test_healthy_prompt_and_tool_hooks_start_nothing",
      BUDGET + "::test_real_dispatch_extra_processes_within_budget"],
     "hooks/userpromptsubmit_runner.py",
     [("    _install_runner_deadline()\n\n    # Capture each subcommand's stdout",
       "    _install_runner_deadline()\n"
       "    __import__('subprocess').run([sys.executable, str(Path(measure.__file__)),"
       " 'quality-cache', '--quiet'], capture_output=True)\n\n"
       "    # Capture each subcommand's stdout")]),

    # #117 shape: a process-table enumeration (`tasklist /v`) on a per-prompt path.
    ("#117 tasklist /v on the per-prompt daemon pulse (Windows)",
     [BUDGET + "::test_healthy_prompt_and_tool_hooks_start_nothing"],
     MEASURE,
     [('        if _daemon_snapshot_sandboxed():\n            return "noop-sandbox"\n'
       "        # SAFETY gates run EVERY turn",
       '        if _daemon_snapshot_sandboxed():\n            return "noop-sandbox"\n'
       '        if _normalized_platform() == "Windows":\n'
       '            subprocess.run(["tasklist", "/v", "/fo", "csv", "/nh"],\n'
       "                           capture_output=True, text=True, timeout=10)\n"
       "        # SAFETY gates run EVERY turn")]),
]
