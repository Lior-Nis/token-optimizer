"""#265 (5.13.53): Windows desktop (MSIX) sessions resolved another project's
dir. Each planted bug brings a piece of that back; listed tests must go red.

Shape: (name, [test node ids], file_relpath, [(old, new), ...]).
"""

MEASURE = "skills/token-optimizer/scripts/measure.py"
T = "tests/test_msix_project_dir.py"

MUTATIONS = [
    ("MSIX physical path is not mapped back to AppData",
     [T + "::test_msix_physical_path_maps_to_the_logical_appdata_path",
      T + "::test_msix_cwd_finds_its_own_project_not_the_newest_one",
      T + "::test_msix_subdirectory_still_finds_the_project"],
     MEASURE,
     [("    m = _MSIX_REDIRECT_RE.match(text)\n    if not m:", "    m = None\n    if not m:")]),
    ("hook transcript_path is ignored",
     [T + "::test_transcript_path_wins_over_a_misleading_cwd"],
     MEASURE,
     [("    if from_transcript is not None:\n        return from_transcript\n",
       "    if False:\n        return from_transcript\n")]),
    ("payload cwd is ignored",
     [T + "::test_payload_cwd_is_used_before_the_process_cwd"],
     MEASURE,
     [("    if cwd:\n        bases.append(cwd)\n", "    if False:\n        bases.append(cwd)\n")]),
    ("session model read through the cwd guess instead of the session's own file",
     [T + "::test_session_model_is_read_from_the_sessions_own_transcript"],
     MEASURE,
     [('            candidate = next(iter(sorted((CLAUDE_DIR / "projects").glob(f"*/{session_id}.jsonl"))), None)',
       '            candidate = None')]),
]
