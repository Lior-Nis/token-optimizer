"""CI test slicing (TO_TEST_SHARD). A broken split runs CI green while never
running some tests; each planted bug is one such split. Listed tests must go red.

Shape: (name, [test node ids], file_relpath, [(old, new), ...]).
"""

CONF = "tests/conftest.py"
T = "tests/test_ci_sharding.py"

MUTATIONS = [
    ("split drops the last file of every slice",
     [T + "::test_every_file_lands_in_exactly_one_slice"],
     CONF,
     [("    return [set(s) for s in slices]", "    return [set(s[:-1]) for s in slices]")]),
    ("end-of-session guard files run in one slice only",
     [T + "::test_every_file_lands_in_exactly_one_slice"],
     CONF,
     [('        if Path(name).name.startswith("test_zz_"):', '        if False:')]),
    ("split ignores file times (round-robin by name)",
     [T + "::test_slices_are_balanced_by_time"],
     CONF,
     [("        i = min(range(n), key=lambda j: (loads[j], j))", "        i = sorted(weights).index(name) % n")]),
]

MUTATIONS += [
    ("runtime answer cached by one test leaks into the next",
     ["tests/test_runtime_cache_isolation.py"],
     CONF,
     [("    _clear_runtime_cache()\n    yield", "    yield"),
      ("            os.environ[key] = value\n    _clear_runtime_cache()\n", "            os.environ[key] = value\n")]),
]
