"""CI runs the suite in slices on separate machines (TO_TEST_SHARD=K/N). A
split that drops a file would turn CI green while never running it, so the
split itself is tested: every file in exactly one slice, end-of-session guards
in all of them, the same answer on every machine, and slices of similar time.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import conftest

REPO = Path(__file__).resolve().parent.parent
WEIGHTS = {"tests/test_a.py": 300.0, "tests/test_b.py": 120.0, "tests/test_c.py": 90.0,
           "tests/test_d.py": 60.0, "tests/test_e.py": 5.0, "tests/test_zz_tree.py": 1.0}


@pytest.mark.parametrize("n", [1, 2, 3, 4, 7])
def test_every_file_lands_in_exactly_one_slice(n):
    slices = conftest.shard_files(WEIGHTS, n)
    assert len(slices) == n
    for f in WEIGHTS:
        holders = sum(f in s for s in slices)
        if Path(f).name.startswith("test_zz_"):
            assert holders == n, f  # end-of-session guards run in every slice
        else:
            assert holders == 1, (f, holders)


def test_split_is_the_same_on_every_machine():
    shuffled = dict(reversed(list(WEIGHTS.items())))
    assert conftest.shard_files(WEIGHTS, 3) == conftest.shard_files(shuffled, 3)


def test_slices_are_balanced_by_time():
    weights = {f"tests/test_{i:03d}.py": float((i * 37) % 97 + 1) for i in range(200)}
    slices = conftest.shard_files(weights, 4)
    loads = [sum(weights[f] for f in s) for s in slices]
    assert max(loads) - min(loads) <= max(weights.values()), loads


def test_weights_file_is_valid_and_names_real_test_files():
    data = json.loads((REPO / "tests" / "shard_weights.json").read_text(encoding="utf-8"))
    assert data and all(isinstance(v, (int, float)) and v >= 0 for v in data.values())
    missing = [f for f in data if not (REPO / f).exists()]
    # Stale entries are harmless (unknown files fall back to an estimate), but a
    # file full of them means the weights were never regenerated.
    assert len(missing) < len(data) / 4, missing[:10]


def _collected(env_shard):
    env = {**os.environ, "TO_TEST_SHARD": env_shard} if env_shard else dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    if not env_shard:
        env.pop("TO_TEST_SHARD", None)
    out = subprocess.run([sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q", "-p", "no:cacheprovider"],
                         cwd=REPO, env=env, capture_output=True, text=True, timeout=300).stdout
    return {line.strip() for line in out.splitlines() if "::" in line}


@pytest.mark.slow
def test_real_suite_slices_cover_every_test_exactly_once():
    everything = _collected("")
    parts = [_collected(f"{k}/4") for k in range(1, 5)]
    union = set().union(*parts)
    assert union == everything
    shared = {t for t in everything if sum(t in p for p in parts) > 1}
    assert all("/test_zz_" in t for t in shared), sorted(shared)[:5]
