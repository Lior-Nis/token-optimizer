"""Silent-failure and false-success regressions, planted back for CI proof.

Shape (shared by every scripts/mutations/<area>.py):
    MUTATIONS = [(name, [test node ids], file_relpath, [(old, new), ...])]

Each entry re-introduces a bug that shipped (or its exact shape) into a
throwaway copy; the listed tests must FAIL on it.
"""

MEASURE = "skills/token-optimizer/scripts/measure.py"
HOOK_RUNTIME = "skills/token-optimizer/scripts/hook_runtime.py"
MODULE_RUNNER = "hooks/module_runner.py"
UPS_RUNNER = "hooks/userpromptsubmit_runner.py"
FLEET = "skills/fleet-auditor/scripts/fleet.py"

SWALLOWED = "tests/test_swallowed_errors.py"
FALSE_SUCCESS = "tests/test_false_success.py"
REAL_LAYOUT = "tests/test_module_runner_real_layout.py"

DRIFT = "tests/test_release_drift.py"

UPS_HOOK = SWALLOWED + "::test_hook_raises_no_programming_error[UserPromptSubmit[*]:hooks/userpromptsubmit_runner.py]"
POST_HOOK = (SWALLOWED + "::test_hook_raises_no_programming_error[PostToolUse[Bash|PowerShell|Read|Glob|Grep|"
             "Agent|Edit|Write|MultiEdit|NotebookEdit|mcp__.*]:hooks/posttooluse_runner.py]")

MUTATIONS = [
    # #246: a module-level call reached a name defined later in measure.py; the
    # broad except in detect_context_window's caller swallowed the NameError and
    # every process kept the 200K thresholds.
    ("#246 module-level threshold call raises a NameError the fallback swallows",
     [SWALLOWED + "::test_cli_command_raises_no_programming_error[quick]"],
     MEASURE,
     [("    return _TOOL_CALL_WARN_THRESHOLDS\n\n# Configurable via env vars\n",
       "    return _TOOL_CALL_WARN_THRESHOLDS\n\n"
       "_TOOL_CALL_WARN, _TOOL_CALL_CRITICAL = _scaled_tool_call_thresholds()\n\n"
       "# Configurable via env vars\n")]),

    # The #246 shape in a hook runner: a NameError inside try/except Exception.
    ("swallowed NameError in the UserPromptSubmit runner",
     [UPS_HOOK],
     UPS_RUNNER,
     [("def main() -> int:\n",
       "def main() -> int:\n    try:\n        _planted_helper_renamed_away()  # noqa: F821\n"
       "    except Exception:\n        pass\n")]),

    # 5.13.0-5.13.51: module_runner could not import hook_runtime for runners in
    # hooks/, so the PostToolUse runner budget and the 110s backstop never armed.
    ("module_runner cannot reach hook_runtime from hooks/ runners",
     [REAL_LAYOUT + "::test_runner_deadline_is_armed_in_the_real_layout[posttooluse_runner-4.5-canonical]",
      POST_HOOK],
     MODULE_RUNNER,
     [("        sys.path.append(_plugin_scripts)\n", "        pass\n")]),

    # #253: the before-compact capture was cut off by its budget and the hook
    # still exited 0; no checkpoint, the restore used an older one.
    ("#253 PreCompact capture cut off by its budget, hook still exits 0",
     [FALSE_SUCCESS + "::test_precompact_hook_writes_the_checkpoint_the_restore_injects"],
     HOOK_RUNTIME,
     [("BUDGET_PRECOMPACT_CAPTURE = 15.0\n", "BUDGET_PRECOMPACT_CAPTURE = 0.01\n")]),

    # #226: diagnostics only knew direct measure.py commands, not the runners.
    ("#226 doctor does not recognise the shipped hook runners",
     [FALSE_SUCCESS + "::test_doctor_counts_every_hook_a_plugin_install_runs"],
     MEASURE,
     [('    return bool(re.search(r"(?:^|[\\s\\"\'/\\\\])" + name + r"\\.py(?:$|[\\s\\"\';])", cmd))\n',
       "    return False\n")]),

    # #149: an adapter without a reader printed "0 scanned" and no warning.
    ("#149 detected fleet system reports a silent zero",
     [FALSE_SUCCESS + "::test_fleet_scan_never_reports_a_silent_zero_for_a_detected_system[opencode]",
      FALSE_SUCCESS + "::test_fleet_scan_never_reports_a_silent_zero_for_a_detected_system[nanoclaw]"],
     FLEET,
     [('        return [], [f"{self.display_name} fleet collection is not implemented yet, "\n'
       '                    f"so its runs are not counted here (0 is not a measurement)."]\n',
       "        return [], []\n")]),

    # Guard the guard: a monitor that records nothing would pass every hook.
    ("raise monitor stops listening (every swallowed-error test goes vacuous)",
     [SWALLOWED + "::test_monitor_catches_planted_swallowed_name_error"],
     "tests/fixtures/raise_monitor/sitecustomize.py",
     [("            _mon.set_events(_tool, _mon.events.RAISE)\n", "            _mon.set_events(_tool, 0)\n")]),

    ("unknown fleet --system prints a zero instead of an error",
     [FALSE_SUCCESS + "::test_fleet_unknown_system_is_an_error_not_a_zero"],
     FLEET,
     [("    if target_system not in known:\n", "    if False:\n")]),

    # 5.13.50: npm bytes from an earlier commit than the git release; the
    # release gate only checked that the version existed.
    ("npm tree match ignores changed bytes",
     [DRIFT + "::test_tree_comparison_names_every_kind_of_drift"],
     "scripts/check_npm_published.py",
     [("        elif found[rel] != expected[rel]:\n", "        elif False:\n")]),
    ("release workflow stops running the npm tree match",
     [DRIFT + "::test_sign_release_workflow_runs_the_tree_match"],
     ".github/workflows/sign-release.yml",
     [('then match="--match-tree"; fi', 'then match=""; fi')]),
    ("signer drops a root the installer still requires a checksum for",
     [DRIFT + "::test_signer_and_installer_checksum_the_same_roots"],
     "scripts/sign-release.sh",
     [("    .codex/ \\\n", "")]),

    # Self-serving sweep: these two tests wrapped the call in try/except
    # Exception: pass, so a heal that raised still passed them.
    ("settings self-heal raises on an unreadable or malformed file",
     ["tests/test_settings_self_heal.py::test_malformed_file_is_left_alone",
      "tests/test_settings_self_heal.py::test_unreadable_file_is_left_alone"],
     MEASURE,
     [("        if not ok or not isinstance(data, dict):\n            return  # unreadable or malformed: fail open\n",
       "        if not ok or not isinstance(data, dict):\n            raise ValueError(\"planted: unreadable settings\")\n"),
      ("    except Exception:\n        # Fail open, always. A heal that breaks a session is a worse bug\n",
       "    except OSError:\n        # Fail open, always. A heal that breaks a session is a worse bug\n")]),
]

MUTATIONS += [
    ("hook stdin read cut off at 0.5 s while a big payload is still streaming",
     ["tests/test_hook_robustness_regressions.py::test_stdin_payload_still_streaming_after_the_first_half_second_is_read_whole"],
     "skills/token-optimizer/scripts/hook_io.py",
     [("_STDIN_TOTAL_CAP = 5.0", "_STDIN_TOTAL_CAP = 0.5")]),
]
