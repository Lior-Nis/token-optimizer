"""The planted-bug harness (scripts/check_regression_guards.py) must never pass
by planting nothing: a stale `old`, a malformed entry, a broken mutation file or
a pytest run that collected nothing all have to fail the check loudly."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_regression_guards",
                                               REPO / "scripts" / "check_regression_guards.py")
guards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guards)

TARGET = "skills/token-optimizer/scripts/cloud_kit.py"
TEST = "tests/test_cloud_kit.py::test_prerelease_versions_are_accepted_and_sorted"


@pytest.fixture
def mutations_dir(tmp_path, monkeypatch):
    d = tmp_path / "mutations"
    d.mkdir()
    monkeypatch.setattr(guards, "MUTATIONS_DIR", d)
    monkeypatch.setattr(guards, "MUTATIONS", [])
    return d


def test_every_registered_mutation_is_well_formed_and_still_applies():
    muts = guards.collect()
    assert len(muts) >= 12
    for name, _tests, relpath, edits, origin in muts:
        guards.plant((REPO / relpath).read_text(encoding="utf-8"), edits)  # raises if stale


def test_stale_mutation_fails_loudly(mutations_dir, capsys):
    (mutations_dir / "area.py").write_text(
        "MUTATIONS = [('gone', [%r], %r, [('text that is not in the file', 'x')])]\n" % (TEST, TARGET))
    assert guards.main([]) == 1
    captured = capsys.readouterr()
    assert "STALE gone" in captured.out and "stale" in captured.err


def test_one_stale_edit_among_several_is_still_stale():
    text = "alpha beta"
    with pytest.raises(guards.MutationError):
        guards.plant(text, [("alpha", "A"), ("alpha", "again")])  # 2nd edit no longer matches
    assert guards.plant(text, [("alpha", "A"), ("beta", "B")]) == "A B"


@pytest.mark.parametrize("body,needle", [
    ("MUTATIONS = None\n", "must export a MUTATIONS list"),
    ("raise RuntimeError('boom')\n", "failed to import"),
    ("MUTATIONS = [('n', [%r], %r, [('a', 'b')], 'extra')]\n" % (TEST, TARGET), "entry must be"),
    ("MUTATIONS = [('n', [], %r, [('a', 'b')])]\n" % TARGET, "non-empty list of pytest node ids"),
    ("MUTATIONS = [('n', ['tests/nope.py'], %r, [('a', 'b')])]\n" % TARGET, "does not exist"),
    ("MUTATIONS = [('n', [%r], 'skills/nope.py', [('a', 'b')])]\n" % TEST, "does not exist"),
    ("MUTATIONS = [('n', [%r], '../x.py', [('a', 'b')])]\n" % TEST, "repo-relative"),
    ("MUTATIONS = [('n', [%r], %r, [('same', 'same')])]\n" % (TEST, TARGET), "plants nothing"),
    ("MUTATIONS = [('n', [%r], %r, [])]\n" % (TEST, TARGET), "at least one"),
])
def test_malformed_mutation_files_fail_the_check(mutations_dir, capsys, body, needle):
    (mutations_dir / "area.py").write_text(body)
    assert guards.main(["--list"]) == 1
    assert needle in capsys.readouterr().err


def test_duplicate_names_across_files_fail(mutations_dir, capsys):
    line = "MUTATIONS = [('dup', [%r], %r, [('def _version_key', 'def _vk')])]\n" % (TEST, TARGET)
    (mutations_dir / "a.py").write_text(line)
    (mutations_dir / "b.py").write_text(line)
    assert guards.main(["--list"]) == 1
    assert "duplicate" in capsys.readouterr().err


def test_underscore_files_are_helpers_not_mutation_lists(mutations_dir):
    (mutations_dir / "_shared.py").write_text("X = 1\n")
    assert guards.collect() == []


def test_only_a_real_test_failure_counts_as_caught(monkeypatch, tmp_path):
    """pytest exit 1 = caught. 0 = guard passed on broken code. 2/4/5 (collection
    error, usage error, no tests collected) prove nothing and must fail too."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "f.py").write_text("X = 1\n")
    mut = ("m", ["t"], "pkg/f.py", [("X = 1", "X = 2")], "here")
    for code, status in ((1, "ok"), (0, "fail"), (2, "fail"), (4, "fail"), (5, "fail")):
        monkeypatch.setattr(guards, "_pytest", lambda *_a, _c=code, **_k: (_c, ""))
        assert guards._run_one(mut, root)[0] == status, code
        assert (root / "pkg" / "f.py").read_text() == "X = 1\n", "copy restored after each mutation"
