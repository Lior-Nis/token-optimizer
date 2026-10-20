"""Context fill shown to the model (5.13.52). With a reduced auto-compact window,
messages led with the share of the compact window ("Context 76%") while the
user's meter showed the share of the model window (38%); sessions then claimed
to be nearly full. Each planted bug brings that back; the listed tests must go red.

Shape: (name, [test node ids], file_relpath, [(old, new), ...]).
"""

MEASURE = "skills/token-optimizer/scripts/measure.py"
T = "tests/test_context_window_disclosure.py"

MUTATIONS = [
    ("fill phrase leads with the auto-compact share again",
     [T + "::test_fill_phrase_leads_with_the_model_window_share",
      T + "::test_fill_phrase_matches_the_users_meter_for_a_500k_compact_window"],
     MEASURE,
     [('        return (f"{model_fill:.0f}% of {_format_window_size(model_window)} window "',
       '        return (f"{fill_pct:.0f}% of {_format_window_size(model_window)} window "')]),
    ("verbosity nudge hand-builds the fill number from fill_pct",
     [T + "::test_every_context_nudge_uses_the_shared_phrase"],
     MEASURE,
     [('f"[Token Optimizer] Context {_format_context_fill(fill_pct, cached)}, resource health',
       'f"[Token Optimizer] Context {fill_pct:.0f}%{window_note}, resource health')]),
    ("quality detail leads with the compact-window share",
     [T + "::test_quality_score_labels_lead_with_the_model_share"],
     MEASURE,
     [('            f"{round(model_fill * 100)}% of model window "\n'
       '            f"({round(fill_pct * 100)}% of the way to auto-compact), "',
       '            f"{round(fill_pct * 100)}% of compact window "\n'
       '            f"({round(model_fill * 100)}% of model window), "')]),
    ("checkpoint header shows the compact-window share",
     [T + "::test_display_fill_for_checkpoints_is_the_model_share"],
     MEASURE,
     [('    if result.get("compact_window_reduced") and isinstance(result.get("model_fill_pct"), (int, float)):\n'
       '        return result["model_fill_pct"]',
       '    if False:\n'
       '        return result["model_fill_pct"]')]),
]
