"""detect_runtime() caches its answer for the whole process. A test that forces
a foreign runtime must not leave that answer for the tests after it: in CI one
leftover "codex" made four unrelated tests in the same slice fail. The two
tests below run in file order; the second fails if the first one's answer leaks.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"))
import runtime_env  # noqa: E402


def test_a_forces_codex_and_leaves_without_cleaning(monkeypatch):
    monkeypatch.setenv("TOKEN_OPTIMIZER_RUNTIME", "codex")
    runtime_env.detect_runtime.cache_clear()
    assert runtime_env.detect_runtime() == "codex"


def test_b_next_test_does_not_inherit_codex(monkeypatch):
    monkeypatch.setenv("TOKEN_OPTIMIZER_RUNTIME", "claude")
    assert runtime_env.detect_runtime() == "claude"
