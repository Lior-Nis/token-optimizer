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

    # ---- Escaped Windows bugs, re-planted (2026-10-19 audit) ----------------
    ("#144 dashboard writer calls os.fchmod unguarded (absent on Windows)",
     ["tests/test_windows_fchmod_guard.py::test_dashboard_generation_survives_missing_fchmod"],
     MEASURE,
     [('            try:\n                if hasattr(os, "fchmod"):  # POSIX only (os.fchmod is absent on Windows)\n'
       '                    os.fchmod(tmp_fd, 0o600)\n                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:\n'
       '                    f.write(injected)',
       '            try:\n                os.fchmod(tmp_fd, 0o600)\n'
       '                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:\n                    f.write(injected)')]),
    ("#104/#107 run.py starts the hook child without DETACHED_PROCESS",
     ["tests/test_windows_spawn_no_window.py::test_run_py_spawn_nt_uses_detached_process"],
     "hooks/run.py",
     [('_flags = getattr(subprocess, "DETACHED_PROCESS", 0)', "_flags = 0")]),
    ("#104/#107 spawn_detached drops DETACHED_PROCESS",
     ["tests/test_windows_spawn_no_window.py::test_spawn_detached_nt_passes_creationflags"],
     "skills/token-optimizer/scripts/spawn_utils.py",
     [('for _name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP",',
       'for _name in ("CREATE_NEW_PROCESS_GROUP",')]),
    ("#105 UTF-8 re-exec child gets no explicit std handles (stdout lost)",
     ["tests/test_windows_spawn_no_window.py::test_utf8_io_reexec_nt_passes_std_handles"],
     "skills/token-optimizer/scripts/utf8_io.py",
     [("                    _popen_kwargs[_name] = _s\n", "                    pass\n")]),
    ("#118 Windows SessionEnd hook uses a cmd NUL redirect under Git Bash",
     ["tests/test_windows_hook_launcher.py::test_claude_windows_hook_command_constant_is_bash_safe"],
     MEASURE,
     [('        f" >/dev/null 2>&1"\n    )\nelse:', '        f" >NUL 2>&1"\n    )\nelse:')]),
    ("#118 Windows SessionEnd hook keeps backslash paths",
     ["tests/test_windows_hook_launcher.py::test_claude_windows_hook_command_constant_is_bash_safe"],
     MEASURE,
     [('    _py = shlex.quote(str(sys.executable).replace("\\\\", "/"))\n'
       '    _mp = shlex.quote(str(MEASURE_PY_PATH).replace("\\\\", "/"))',
       "    _py = shlex.quote(str(sys.executable))\n    _mp = shlex.quote(str(MEASURE_PY_PATH))")]),
    ("#143 WindowsApps probe lets a live pythonw twin vouch for a dead candidate",
     ["tests/test_windowsapps_twin_cannot_vouch.py"],
     "hooks/python-launcher.sh",
     [('        timeout --kill-after=1s 2s "$binpath" -c \\\n',
       '        timeout --kill-after=1s 2s "${binpath%/*}/pythonw.exe" -c \\\n')]),
    ("#188 Windows launcher runs the newest version dir without opening its run.py",
     ["tests/test_windows_hook_launcher.py::test_windows_version_resolver_skips_incomplete_newest_upgrade"],
     "hooks/windows-launcher.py",
     [('            try:\n                with open(run_py, "rb") as fh:\n                    if not fh.read(1):\n'
       "                        continue  # zero-byte/truncated: would run nothing rc 0\n"
       "            except OSError:\n                continue  # missing, incomplete upgrade, or ACL-denied\n"
       "            root = candidate",
       "            root = candidate")]),
    ("#253 PreCompact checkpoint back under the 2.5 s Stop budget",
     ["tests/test_hook_entry_budgets.py::test_every_in_scope_hooks_json_entry_has_the_expected_budget"],
     "skills/token-optimizer/scripts/hook_runtime.py",
     [('("measure", _trigger("compact-capture", "auto"), BUDGET_PRECOMPACT_CAPTURE,',
       '("measure", _trigger("compact-capture", "auto"), BUDGET_STOP,')]),

    # A real double start of the session-end flush worker per Stop (the shape
    # Windows CI first suggested in 5.13.51; there it was the breakaway retry,
    # one process). The recorder counts started processes, so a genuine second
    # start must still trip the budget.
    ("Stop starts the session-end flush worker twice",
     [BUDGET + "::test_real_dispatch_extra_processes_within_budget"],
     MEASURE,
     [("            close_fds=True,\n        )\n        if _proc is None:\n"
       '            _log_spawn_failure("session-end flush spawn failed")',
       "            close_fds=True,\n        )\n"
       "        spawn_detached(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
       "                       stderr=subprocess.DEVNULL, cwd=str(Path.cwd()), env=env)\n"
       "        if _proc is None:\n"
       '            _log_spawn_failure("session-end flush spawn failed")')]),
    # The recorder itself: logging an attempt before CreateProcess answers
    # counted spawn_detached's refused breakaway attempt as a second process.
    ("budget recorder counts refused CreateProcess attempts as processes",
     [BUDGET + "::test_recorder_counts_started_processes_not_refused_attempts"],
     BUDGET,
     [("                try:\n                    super().__init__(args, *a, **kw)\n",
       '                _w("popen", argv)\n'
       "                try:\n                    super().__init__(args, *a, **kw)\n")]),
    # Linux CI 3.13+: Popen starts children through os.posix_spawn, which the
    # recorder also hooks; without the re-entrancy pass-through one process is
    # recorded twice and the faked primitive returns pid 0.
    ("budget recorder double-counts Popen's own posix_spawn",
     [BUDGET + "::test_recorder_counts_started_processes_not_refused_attempts[posix_spawn]"],
     BUDGET,
     [("                if getattr(_tls, \"inside\", 0):\n"
       "                    return real(*a, **k)  # Popen's own start: already recorded\n", "")]),
]
