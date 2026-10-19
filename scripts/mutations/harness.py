"""The planted-bug harness guards itself: these re-introduce the ways it could
pass while planting nothing. Shape: (name, [test node ids], file_relpath, [(old, new), ...])."""

HARNESS = "scripts/check_regression_guards.py"
TESTS = "tests/test_regression_guard_harness.py"

MUTATIONS = [
    ("harness skips a stale mutation instead of failing",
     [TESTS + "::test_stale_mutation_fails_loudly",
      TESTS + "::test_one_stale_edit_among_several_is_still_stale"],
     HARNESS,
     [("        if old not in text:\n            snippet",
       "        if old not in text:\n            continue\n            snippet")]),
    ("harness counts a collection error or empty run as caught",
     [TESTS + "::test_only_a_real_test_failure_counts_as_caught"],
     HARNESS,
     [("    if code == 1:\n        return \"ok\", \"\"", "    if code != 0:\n        return \"ok\", \"\"")]),
    ("harness silently ignores a mutation file that fails to import",
     [TESTS + "::test_malformed_mutation_files_fail_the_check"],
     HARNESS,
     [("            raise MutationError(f\"{origin}: failed to import: {exc!r}\") from exc",
       "            continue")]),
]
