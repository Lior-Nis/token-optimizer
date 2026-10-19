"""Follow-up regressions for 5.13.51, planted back for CI proof.

Shape (shared by every scripts/mutations/<area>.py):
    MUTATIONS = [(name, [test node ids], file_relpath, [(old, new), ...])]

Each entry re-introduces a bug (or its exact shape) into a throwaway copy; the
listed tests must FAIL on it. The desktop Clean up fix (#207) is TypeScript:
its planted-bug proof runs in the desktop-mod-pure job's tests, not here.
"""

MEASURE = "skills/token-optimizer/scripts/measure.py"

RESTORE = "tests/test_compact_restore_choice.py"
DOCTOR = "tests/test_doctor_hook_source_label.py"

MUTATIONS = [
    # SessionStart(compact) refreshed the quality cache first, which captured a
    # progressive-20 checkpoint from the post-compact transcript; progressive
    # outranks the PreCompact `auto` capture, so the restore gave back the
    # compaction summary as the task.
    ("post-compact restore picks a checkpoint saved after the compaction",
     [RESTORE + "::test_compact_restore_brings_back_the_task_from_before_the_compaction"],
     MEASURE,
     [("                session_checkpoints = [(r, cp) for r, cp in session_checkpoints if cp[\"created\"] <= anchor]\n",
       "                pass\n")]),

    # The fence must belong to THIS compaction: a PreCompact capture hours old
    # (this one's capture failed, #253) must not hide the saves made since.
    ("post-compact restore fences on a stale PreCompact capture",
     [RESTORE + "::test_an_old_precompact_capture_does_not_hide_later_saves"],
     MEASURE,
     [("            if (now - anchor).total_seconds() <= _COMPACT_RESTORE_ANCHOR_MAX_AGE_S:\n",
       "            if True:\n")]),

    # Over-fix guard: preferring the PreCompact capture outright would drop the
    # documented precedence of an earlier, richer progressive save.
    ("post-compact restore always prefers the PreCompact capture",
     [RESTORE + "::test_a_progressive_checkpoint_saved_before_the_compaction_still_wins"],
     MEASURE,
     [("            session_checkpoints.sort(key=lambda x: (x[0], -x[1][\"created\"].timestamp()))\n            best_cp = session_checkpoints[0][1]\n",
       "            session_checkpoints.sort(key=lambda x: (x[1].get(\"trigger\", \"auto\") != \"auto\", x[0], -x[1][\"created\"].timestamp()))\n            best_cp = session_checkpoints[0][1]\n")]),

    # doctor labelled a plugin install's hooks.json SessionEnd hook "(settings.json)".
    ("doctor calls the plugin's SessionEnd hook a settings.json one",
     [DOCTOR + "::test_plugin_hooks_json_is_not_called_settings_json"],
     MEASURE,
     [("    if _is_hook_installed(settings, settings_only=True):\n",
       "    if _is_hook_installed(settings):\n")]),
]
