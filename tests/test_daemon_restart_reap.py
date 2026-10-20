"""Version-skew restart must reap the orphaned port-holder and verify it landed.

Origin: dashboard daemon silent version-skew drift. After a plugin upgrade the
service-manager restart (launchctl kickstart -k / systemctl restart / schtasks)
only restarts the job's OWN child; an orphaned dashboard-server.py -- from a
prior label, a manual launch, or ANOTHER runtime on the same port -- survives
and keeps serving the old hardcoded measure.py path. _restart_dashboard_daemon
now (a) short-circuits when the port already serves the current version
(idempotency / race guard), (b) reaps the port holder before restarting, and
(c) verifies the restart landed via the served /api/health version.

These tests mock all subprocess / network / sleep surfaces: no real processes,
no real network, no real sleeps.

Run: python3 -m pytest tests/test_daemon_restart_reap.py -v
"""

import sys
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import measure  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _allow_scheduler_code_under_sandbox_home(monkeypatch):
    """The suite runs with a sandbox HOME, which measure treats as a redirected
    home and refuses every per-account scheduler write for (_HOME_REDIRECTED).
    This file drives that installer/restart code with the service-manager
    subprocess layer stubbed, so it opts out of the refusal explicitly."""
    monkeypatch.setattr(measure, "_HOME_REDIRECTED", False)

CUR = measure.TOKEN_OPTIMIZER_VERSION


class _Completed:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _make_recording_run(call_log, ps_contains_script=True):
    """Fake subprocess.run that records argv and answers id/lsof/ps sensibly."""

    def fake_run(cmd, *a, **k):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else [cmd]
        call_log.append(argv)
        head = argv[0] if argv else ""
        if head == "id":
            return _Completed(stdout="501\n")
        if head == "lsof":
            return _Completed(stdout="4242\n")
        if head == "ps":
            cmdline = "/usr/bin/python dashboard-server.py" if ps_contains_script else "/usr/bin/python some-other-app.py"
            return _Completed(stdout=cmdline)
        return _Completed(stdout="")

    return fake_run


def _seq_served_version(values):
    """Fake _daemon_served_version returning each value in turn (then None).

    _restart_dashboard_daemon calls it twice: first the idempotency guard, then
    the post-restart verify. Supplying a 2-element sequence controls both.
    """
    vals = list(values)

    def fake(*a, **k):
        return vals.pop(0) if vals else None

    return fake


def _fake_urlopen_factory(body):
    """Return a fake urlopen whose context manager yields a .read() of `body`."""

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return body

    def _fake(url, timeout=None):
        return _Resp()

    return _fake


# --------------------------------------------------------------------------
# _restart_dashboard_daemon -- idempotency guard
# --------------------------------------------------------------------------

def test_already_current_short_circuits_without_reap(monkeypatch):
    """If the port already serves the current version, do NOT reap/restart."""
    calls = []
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: calls.append(["REAP"]))
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run(calls))
    monkeypatch.setattr(measure, "_daemon_served_version", _seq_served_version([CUR]))

    result = measure._restart_dashboard_daemon("Darwin")

    assert result == "restarted"
    assert calls == [], "already-current must not reap or restart (race guard)"


# --------------------------------------------------------------------------
# _restart_dashboard_daemon -- reap + restart + verify
# --------------------------------------------------------------------------

def test_reaper_runs_before_launchctl_on_darwin(monkeypatch):
    calls = []
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: calls.append(["REAP"]))
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run(calls))
    # guard sees stale -> proceed; verify sees current -> restarted.
    monkeypatch.setattr(measure, "_daemon_served_version", _seq_served_version([None, CUR]))
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    result = measure._restart_dashboard_daemon("Darwin")

    assert result == "restarted"
    reap_idx = calls.index(["REAP"])
    launch_idx = next(i for i, c in enumerate(calls) if c and c[0] == "launchctl")
    assert reap_idx < launch_idx, f"reaper must run before launchctl: {calls}"


def test_service_manager_argv_per_platform(monkeypatch):
    cases = {"Darwin": "launchctl", "Linux": "systemctl", "Windows": "schtasks"}
    for system, head in cases.items():
        calls = []
        monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: calls.append(["REAP"]))
        monkeypatch.setattr(measure.subprocess, "run", _make_recording_run(calls))
        monkeypatch.setattr(measure, "_daemon_served_version", _seq_served_version([None, None]))
        monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

        result = measure._restart_dashboard_daemon(system)

        assert result == "restarted"
        heads = [c[0] for c in calls if c]
        assert head in heads, f"{system}: expected {head} in {heads}"
        if system == "Windows":
            end_idx = next(i for i, c in enumerate(calls) if c[:2] == ["schtasks", "/End"])
            run_idx = next(i for i, c in enumerate(calls) if c[:2] == ["schtasks", "/Run"])
            assert end_idx < run_idx


def test_unsupported_platform_is_restart_failed(monkeypatch):
    calls = []
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: calls.append(["REAP"]))
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run(calls))
    monkeypatch.setattr(measure, "_daemon_served_version", _seq_served_version([None, None]))

    result = measure._restart_dashboard_daemon("Plan9")

    assert result == "restart-failed"
    assert not any(c and c[0] in ("launchctl", "systemctl", "schtasks") for c in calls)


def test_reaper_exception_does_not_abort_restart(monkeypatch):
    calls = []

    def boom(*a, **k):
        raise RuntimeError("reaper blew up")

    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", boom)
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run(calls))
    monkeypatch.setattr(measure, "_daemon_served_version", _seq_served_version([None, CUR]))
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    result = measure._restart_dashboard_daemon("Darwin")

    assert result == "restarted"


def test_served_version_mismatch_is_restart_stale(monkeypatch):
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: None)
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run([]))
    # guard: stale (proceed); verify: still stale -> restart-stale.
    monkeypatch.setattr(measure, "_daemon_served_version", _seq_served_version(["0.0.1-old", "0.0.1-old"]))
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    result = measure._restart_dashboard_daemon("Darwin")

    assert result == "restart-stale"


def test_served_version_none_is_safe_degrade_to_restarted(monkeypatch):
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: None)
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run([]))
    monkeypatch.setattr(measure, "_daemon_served_version", _seq_served_version([None, None]))
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    result = measure._restart_dashboard_daemon("Darwin")

    assert result == "restarted"  # unknown must NOT be a false stale


def test_subprocess_exception_is_restart_failed(monkeypatch):
    monkeypatch.setattr(measure, "_daemon_served_version", _seq_served_version([None, None]))
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: None)

    def boom(*a, **k):
        raise OSError("launchctl missing")

    monkeypatch.setattr(measure.subprocess, "run", boom)

    result = measure._restart_dashboard_daemon("Darwin")

    assert result == "restart-failed"


# --------------------------------------------------------------------------
# _daemon_served_version -- probe behavior (real loop, urlopen mocked)
# --------------------------------------------------------------------------

def test_served_version_reads_version_field(monkeypatch):
    body = b'{"ok": true, "server": "token-optimizer-daemon", "version": "9.9.9"}'
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(body))

    assert measure._daemon_served_version() == "9.9.9"


def test_served_version_none_when_no_version_field(monkeypatch):
    body = b'{"ok": true, "server": "token-optimizer-daemon"}'
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(body))

    assert measure._daemon_served_version() is None


def test_served_version_ignores_foreign_server(monkeypatch):
    """A foreign listener returning JSON with a version key must be ignored."""
    body = b'{"server": "some-other-service", "version": "1.2.3"}'
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(body))

    assert measure._daemon_served_version() is None


def test_served_version_none_when_unreachable(monkeypatch):
    def boom(url, timeout=None):
        raise ConnectionError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    # Exercise the real default budget path (env knob controls it), not an override.
    monkeypatch.setenv("TOKEN_OPTIMIZER_DAEMON_VERIFY_TIMEOUT", "0.4")
    assert measure._daemon_served_version() is None


def test_served_version_retries_then_succeeds(monkeypatch):
    """The retry loop: fail twice, then succeed within budget -> return version."""
    body = b'{"server": "token-optimizer-daemon", "version": "7.7.7"}'

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return body

    state = {"n": 0}

    def flaky_urlopen(url, timeout=None):
        state["n"] += 1
        if state["n"] < 3:
            raise ConnectionError("not up yet")
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", flaky_urlopen)
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    assert measure._daemon_served_version(total_timeout=5.0) == "7.7.7"
    assert state["n"] == 3, "should have retried past the transient failures"


# --------------------------------------------------------------------------
# _reclaim_posix_daemon_port -- foreign-process guard
# --------------------------------------------------------------------------

def test_reaper_never_kills_foreign_process(monkeypatch):
    killed = []
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run([], ps_contains_script=False))
    monkeypatch.setattr(measure.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    measure._reclaim_posix_daemon_port()

    assert killed == [], "must never SIGTERM a process whose command lacks dashboard-server.py"


def test_reaper_kills_our_own_daemon(monkeypatch):
    killed = []
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run([], ps_contains_script=True))
    monkeypatch.setattr(measure.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    measure._reclaim_posix_daemon_port()

    assert killed and killed[0][0] == 4242
    assert killed[0][1] == measure.signal.SIGTERM
