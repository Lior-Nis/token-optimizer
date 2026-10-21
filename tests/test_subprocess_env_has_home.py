"""A test that starts a process with a hand-built env must give it HOME. Without
it Python falls back to the real account home, so the child reads and writes the
developer's own ~/.claude (one test left a file there before this check)."""
import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent
_LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output"}


def _bare_envs(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", None) in _LAUNCHERS):
            continue
        for kw in node.keywords:
            if kw.arg == "env" and isinstance(kw.value, ast.Dict):
                keys = {k.value for k in kw.value.keys if isinstance(k, ast.Constant)}
                spread = any(k is None for k in kw.value.keys)  # {**os.environ, ...}
                if not spread and "HOME" not in keys:
                    yield node.lineno


def test_every_hand_built_subprocess_env_sets_home():
    offenders = [f"{p.name}:{line}" for p in sorted(TESTS.glob("test_*.py")) for line in _bare_envs(p)]
    assert not offenders, f"subprocess env without HOME (falls back to the real home): {offenders}"


def test_the_check_sees_a_bare_env(tmp_path):
    bad = tmp_path / "test_bad.py"
    bad.write_text('import subprocess\nsubprocess.run(["x"], env={"PATH": "/bin"})\n', encoding="utf-8")
    assert list(_bare_envs(bad)) == [2]
