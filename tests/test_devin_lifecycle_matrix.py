"""Differential lifecycle matrix for the Devin adapter.

Every cell starts from a described on-disk state, runs one lifecycle command
with the current adapter and a twin built by the embedded previous-release
installer, and asserts the invariants that must hold in every cell: a clean
exit class (no crash, no traceback), no user-written file removed, nothing
outside the Devin home touched, and a documented route back to an
installable state. Where the previous release handled a cell safely, the
current adapter must handle it too: the baseline column is the pin.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/token-optimizer/scripts'
FIXTURE = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'devin_229_baseline'
MARKER = 'devin-output-filter-v1'


def _devin(home: Path) -> Path:
    return home / '.config' / 'devin'


def _data(home: Path) -> Path:
    return _devin(home) / 'token-optimizer'


def _skill(home: Path) -> Path:
    return _devin(home) / 'skills' / 'token-optimizer-output'


def _env(home: Path, extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env['HOME'] = str(home)
    for key in ('TOKEN_OPTIMIZER_DEVIN_HOME', 'XDG_CONFIG_HOME',
                'TOKEN_OPTIMIZER_RUNTIME', 'DEVIN_CONFIG_DIR'):
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


def _baseline_installer(baseline_dir: Path) -> Path:
    return baseline_dir / 'skills/token-optimizer/scripts/devin_install.py'


def _baseline_run(baseline_dir: Path, home: Path, argv, extra_env=None):
    run = subprocess.run(
        [sys.executable, str(_baseline_installer(baseline_dir)), *argv],
        env=_env(home, extra_env), capture_output=True, text=True, timeout=60)
    return run.returncode, (run.stderr.strip().splitlines() or [''])[-1]


def _install_baseline(baseline_dir: Path, home: Path, extra_env=None):
    rc, err = _baseline_run(baseline_dir, home, ['install'], extra_env)
    assert rc == 0, err


def _install_current(home: Path, monkeypatch, extra_env=None):
    import devin_install as inst
    monkeypatch.setenv('HOME', str(home))
    for key in ('TOKEN_OPTIMIZER_DEVIN_HOME', 'XDG_CONFIG_HOME'):
        monkeypatch.delenv(key, raising=False)
    if extra_env:
        for key, value in extra_env.items():
            monkeypatch.setenv(key, value)
    inst.install()


def _run_current(fn):
    try:
        fn()
        return 0, ''
    except OSError as exc:
        return 1, str(exc)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2, str(exc)
    except Exception as exc:  # a crash class is always a bug in a cell
        return 99, repr(exc)


def _craft_half(home: Path):
    """Pending record plus a partial payload and no manifest: the state an
    interrupted publication leaves behind."""
    data = _data(home)
    record = json.loads((data / 'install.json').read_text())
    pending = data / 'pending-install.json'
    pending.write_text(json.dumps({'owner': MARKER, 'files': record['files']}))
    pending.chmod(0o600)
    (data / 'install.json').unlink()
    plugin = data / 'plugin'
    for leaf in sorted(p.name for p in plugin.iterdir())[:3]:
        (plugin / leaf).unlink()


def _build(home: Path, name: str, installer) -> dict:
    """Create the starting state; return {'keep': [...], 'env': {...}}."""
    ctx = {'keep': [], 'env': None}
    if name == 'old_dotdot':
        pivot = home / 'seg'
        pivot.mkdir()
        installer(str(home / 'seg/../dh//inner'))
        ctx['env'] = {'TOKEN_OPTIMIZER_DEVIN_HOME': str(home / 'dh' / 'inner')}
        ctx['keep'] = [pivot]
        return ctx
    installer()
    if name == 'edited_skill':
        leaf = _skill(home) / 'SKILL.md'
        leaf.write_text(leaf.read_text() + 'user edit\n')
        ctx['keep'] = [leaf]
    elif name == 'edited_payload':
        leaf = _data(home) / 'plugin' / 'devin_state.py'
        leaf.write_text(leaf.read_text() + '# user edit\n')
        ctx['keep'] = [leaf]
    elif name == 'stray_plugin':
        leaf = _data(home) / 'plugin' / '.DS_Store'
        leaf.write_bytes(b'finder metadata')
        ctx['keep'] = [leaf]
    elif name == 'stray_skill':
        leaf = _skill(home) / 'user-notes.md'
        leaf.write_text('keep me\n')
        ctx['keep'] = [leaf]
    elif name == 'skill_moved':
        parked = home / 'parked-skill'
        shutil.move(str(_skill(home)), str(parked))
        ctx['keep'] = [parked / 'SKILL.md']
    elif name == 'skill_deleted':
        shutil.rmtree(_skill(home))
    elif name == 'data_deleted':
        shutil.rmtree(_data(home))
    elif name == 'manifest_missing':
        (_data(home) / 'install.json').unlink()
    elif name == 'manifest_unreadable':
        (_data(home) / 'install.json').write_bytes(b'not json at all')
    elif name == 'half_install':
        _craft_half(home)
    elif name == 'old_half':
        _craft_half(home)
    elif name in ('clean', 'old_install'):
        pass
    else:
        raise AssertionError('unknown state ' + name)
    return ctx


STATES = [
    'clean', 'edited_skill', 'edited_payload', 'stray_plugin', 'stray_skill',
    'skill_moved', 'skill_deleted', 'data_deleted', 'manifest_missing',
    'manifest_unreadable', 'half_install', 'old_install', 'old_dotdot', 'old_half',
]

COMMANDS = ['install', 'uninstall', 'purge', 'doctor', 'install_again']


def _command(home: Path, command: str, monkeypatch, extra_env=None):
    import devin_install as inst
    import devin_state as state
    monkeypatch.setenv('HOME', str(home))
    for key, value in (extra_env or {}).items():
        monkeypatch.setenv(key, value)
    if command == 'install':
        return _run_current(inst.install)
    if command == 'install_again':
        rc, detail = _run_current(inst.install)
        if rc != 0:
            return rc, detail
        return _run_current(inst.install)
    if command == 'uninstall':
        return _run_current(inst.uninstall)
    if command == 'purge':
        return _run_current(lambda: inst.uninstall(purge=True))
    doctor = state.data_dir() / 'plugin' / 'devin_doctor.py'
    if not doctor.is_file():
        return 'n/a', ''
    run = subprocess.run([sys.executable, str(doctor)], env=_env(home, extra_env),
                         capture_output=True, text=True, timeout=60)
    assert 'Traceback' not in run.stderr, run.stderr
    return run.returncode, run.stderr.strip()[-160:]


def _root(home: Path, extra_env=None) -> Path:
    override = (extra_env or {}).get('TOKEN_OPTIMIZER_DEVIN_HOME')
    return Path(override).resolve() if override else _devin(home)


def _baseline_command(baseline_dir: Path, home: Path, command: str, extra_env=None):
    if command == 'doctor':
        doctor = _root(home, extra_env) / 'token-optimizer' / 'plugin' / 'devin_doctor.py'
        if not doctor.is_file():
            return 'n/a', ''
        run = subprocess.run([sys.executable, str(doctor)], env=_env(home, extra_env),
                             capture_output=True, text=True, timeout=60)
        assert 'Traceback' not in run.stderr, run.stderr
        return run.returncode, run.stderr.strip()[-160:]
    if command == 'install_again':
        rc, detail = _baseline_run(baseline_dir, home, ['install'], extra_env)
        if rc != 0:
            return rc, detail
        return _baseline_run(baseline_dir, home, ['install'], extra_env)
    argv = {'install': ['install'], 'uninstall': ['uninstall'],
            'purge': ['uninstall', '--purge']}[command]
    return _baseline_run(baseline_dir, home, argv, extra_env)


def _outside_snapshot(home: Path, extra_env=None) -> set:
    override = (extra_env or {}).get('TOKEN_OPTIMIZER_DEVIN_HOME')
    root = Path(override).resolve() if override else _devin(home)
    out = set()
    for path in home.rglob('*'):
        try:
            path.relative_to(root)
        except ValueError:
            out.add(str(path))
    return out


def _documented_recovery(home: Path, monkeypatch, extra_env=None):
    """The documented escape hatches must always return to an installable
    state: plain install, then purge, then move the adapter folders aside."""
    import devin_install as inst
    import devin_state as state
    monkeypatch.setenv('HOME', str(home))
    for key, value in (extra_env or {}).items():
        monkeypatch.setenv(key, value)
    try:
        inst.install()
        return 'install'
    except OSError:
        pass
    try:
        inst.uninstall(purge=True)
    except OSError:
        pass
    try:
        inst.install()
        return 'purge'
    except OSError:
        pass
    parked = home / 'parked-aside'
    parked.mkdir(exist_ok=True)
    for folder in (state.data_dir(), state.home() / 'skills' / 'token-optimizer-output'):
        if folder.exists() or folder.is_symlink():
            target = parked / folder.name
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(folder), str(target))
    inst.install()
    return 'move-aside'


# Cells where the previous release's success was the unsafe behaviour the new
# code intentionally closes: the pin does not apply.
PIN_EXEMPT = {('stray_plugin', 'doctor'), ('stray_plugin', 'install'),
              ('stray_plugin', 'install_again'), ('stray_plugin', 'purge')}


@pytest.mark.parametrize('command', COMMANDS)
@pytest.mark.parametrize('state_name', STATES)
def test_lifecycle_cell(tmp_path, monkeypatch, state_name, command):
    tmp_path.chmod(0o700)
    cur_home = tmp_path / 'cur'
    base_home = tmp_path / 'base'
    cur_home.mkdir()
    base_home.mkdir()
    baseline_dir = tmp_path / 'baseline-src'
    shutil.copytree(FIXTURE, baseline_dir)

    # Current-adapter cell home.
    if state_name.startswith('old_'):
        def cur_installer(override=None):
            env = {'TOKEN_OPTIMIZER_DEVIN_HOME': override} if override else None
            _install_baseline(baseline_dir, cur_home, env)
    else:
        def cur_installer(override=None):
            _install_current(cur_home, monkeypatch)
    cur_ctx = _build(cur_home, state_name, cur_installer)
    canary = cur_home / 'outside-canary.txt'
    canary.write_text('KEEP\n')
    before_outside = _outside_snapshot(cur_home, cur_ctx['env'])

    # Previous-release twin: same state built by its own installer.
    def base_installer(override=None):
        env = {'TOKEN_OPTIMIZER_DEVIN_HOME': override} if override else None
        _install_baseline(baseline_dir, base_home, env)
    base_ctx = _build(base_home, state_name, base_installer)

    rc_base, base_detail = _baseline_command(baseline_dir, base_home, command, base_ctx['env'])
    rc_cur, cur_detail = _command(cur_home, command, monkeypatch, cur_ctx['env'])

    # Invariants for every cell.
    assert rc_cur in (0, 1, 2, 'n/a'), (rc_cur, cur_detail)
    for keep in cur_ctx['keep']:
        assert keep.exists(), f'{command} on {state_name} removed user file {keep}'
    assert canary.read_text() == 'KEEP\n'
    after_outside = _outside_snapshot(cur_home, cur_ctx['env'])
    assert before_outside <= after_outside, (
        f'{command} on {state_name} touched files outside the Devin home: '
        + str(sorted(before_outside - after_outside)))
    # Where the previous release handled the cell safely, this one must too.
    if rc_base == 0 and (state_name, command) not in PIN_EXEMPT:
        assert rc_cur == 0, (
            f'{command} on {state_name}: baseline rc=0 ({base_detail}) but current '
            f'rc={rc_cur} ({cur_detail})')
    # The user is never left stuck: a documented sequence must reinstall.
    route = _documented_recovery(cur_home, monkeypatch, cur_ctx['env'])
    print(f'MATRIX|{state_name}|{command}|cur={rc_cur}|base={rc_base}|via={route}')
