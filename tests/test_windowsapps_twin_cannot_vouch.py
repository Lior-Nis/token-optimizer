"""#143 as the user hit it: a LIVE pythonw.exe twin beside a DEAD python.exe.

In WindowsApps each App Execution Alias name is claimed on its own, so the
Python Install Manager can own pythonw.exe while python.exe is still the Store
redirector stub. The pre-#143 probe wrote its proof-of-life marker with the
twin, so the live twin vouched for the dead candidate, which was then cached and
exec'd on every hook.

The existing launcher tests never build that pair: they pair a live candidate
with a live twin, a dead candidate with a dead twin, or have no twin at all, and
a probe that asks the twin passes every one of them. The only test that noticed
a planted twin probe was a source-string check. This drives the real
``find_interpreter`` from hooks/python-launcher.sh against the mixed pair.

POSIX bash with ``#!/bin/sh`` fakes: macOS/Linux simulation of the WindowsApps
layout (the probe keys on the path, not on the OS). Not run on the Windows legs.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "hooks" / "python-launcher.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="drives python-launcher.sh with #!/bin/sh fakes under POSIX bash",
)


def _launcher_defs() -> str:
    src = LAUNCHER.read_text(encoding="utf-8")
    cut = src.index('\nif py3=$(find_interpreter "python3")')
    src = src[:cut]
    return src.replace('\n_setup_interpreter_cache\n_exec_cached_interpreter "$@" || :\n', "\n")


def _fake(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + 'echo "$(basename "$0") $*" >> "$PROBE_LOG"\n' + body,
                    encoding="utf-8")
    path.chmod(0o755)
    return path


def _dead_store_stub(store: Path, name: str) -> Path:
    # The Store redirector: ignores -c (writes nothing), exits 0, and answers
    # --version with a localized notice that carries no version number.
    return _fake(store / name,
                 'if [ "$1" = "--version" ]; then '
                 'echo "Python wurde nicht gefunden. Aus dem Microsoft Store installieren."; fi\n'
                 "exit 0\n")


def _live_python(store: Path, name: str) -> Path:
    return _fake(store / name,
                 'if [ "$1" = "-c" ]; then printf ok > "$3"; exit 0; fi\n'
                 'if [ "$1" = "--version" ]; then echo "Python 3.13.1"; exit 0; fi\n'
                 "exit 0\n")


def _find(store: Path, log: Path, name: str):
    driver = _launcher_defs() + (
        "\n_is_safe_prefix() { return 0; }\n"
        'timeout() { shift 2; "$@"; }\n'
        f'PATH="{store}:$PATH"\n'
        "set +e\n"
        f'found=$(find_interpreter "{name}")\n'
        'printf "RC=%s FOUND=%s\\n" "$?" "$found"\n'
    )
    log.write_text("", encoding="utf-8")
    proc = subprocess.run(["bash", "-c", driver], env={**os.environ, "PROBE_LOG": str(log)},
                          capture_output=True, text=True, timeout=60)
    m = re.search(r"RC=(\d+) FOUND=(.*)", proc.stdout)
    assert m, (proc.stdout, proc.stderr)
    calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
    return (m.group(2).strip() if m.group(1) == "0" else None), calls


@pytest.fixture
def store(tmp_path: Path) -> Path:
    d = tmp_path / "Microsoft" / "WindowsApps"
    d.mkdir(parents=True)
    return d


@pytest.mark.parametrize("candidate", ["python.exe", "python3.exe"])
def test_live_pythonw_twin_does_not_vouch_for_a_dead_candidate(store, tmp_path, candidate):
    dead = _dead_store_stub(store, candidate)
    _live_python(store, "pythonw.exe")
    found, calls = _find(store, tmp_path / "probe.log", candidate[:-4])
    assert any(c.startswith(candidate) for c in calls), (
        f"the probe never asked the candidate itself: {calls}")
    assert found != str(dead), (
        f"dead {candidate} accepted because its live pythonw twin answered: {calls}")


def test_control_live_candidate_with_live_twin_is_accepted(store, tmp_path):
    """Positive control for the harness: the same layout with a live candidate
    must be accepted, so a rejection above is the probe's verdict, not a broken
    driver."""
    live = _live_python(store, "python.exe")
    _live_python(store, "pythonw.exe")
    found, _calls = _find(store, tmp_path / "probe.log", "python")
    assert found == str(live)
