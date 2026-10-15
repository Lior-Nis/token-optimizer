#!/usr/bin/env python3
"""Readiness check and synthetic local filter probe; records no metrics and never starts a Devin session."""
# CLI startup imports only builtins until isolated re-execution.
import sys
if __name__ == '__main__' and not sys.flags.isolated:
    import posix
    try:
        posix.execv(sys.executable, [sys.executable, '-I', *sys.argv])
    except OSError as _isolation_error:
        print('Token Optimizer: isolated startup refused: ' + str(_isolation_error), file=sys.stderr)
        raise SystemExit(2)


import argparse
import hashlib
import stat
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

# Bootstrap uses only stdlib already imported by the entrypoint.
_source_root = Path(__file__).resolve().parent
# Source development is the canonical checkout layout, not a directory-name bypass.
_source_checkout = _source_root.parent.parent.parent
_source_development = (
    _source_root == _source_checkout / 'skills/token-optimizer/scripts'
    and (_source_root / 'devin_install.py').is_file()
    and (_source_checkout / 'devin/SKILL.md').is_file()
    and (_source_checkout / 'README.md').is_file()
    and not any((directory / leaf).exists() or (directory / leaf).is_symlink()
                for directory in (_source_root, *_source_root.parents)
                for leaf in ('install.json', 'consent.json'))
)
if _source_development and __name__ == '__main__':
    # -I removed ambient paths; restore only the checked development scripts root.
    sys.path.insert(0, str(_source_root))
if not _source_development:
    try:
        if __name__ != '__main__' and not any(
                getattr(finder, '_devin_source_root', None) == _source_root for finder in sys.meta_path):
            raise ImportError('installed entrypoint requires command-line use or prior activation')
        def _bootstrap_read(path, limit):
            for component in [*reversed(path.parents), path]:
                if component.is_symlink():
                    raise ImportError('symlink bootstrap path refused')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > limit):
                    raise ImportError('private bounded bootstrap file required')
                with os.fdopen(fd, 'rb', closefd=False) as stream:
                    content = stream.read(limit + 1)
                if len(content) > limit:
                    raise ImportError('bootstrap file exceeds bound')
                return content
            finally:
                os.close(fd)
        _source_path = _source_root / 'devin_source.py'
        _source_record = json.loads(_bootstrap_read(_source_root.parent / 'install.json', 65536))
        if (not isinstance(_source_record, dict) or _source_record.get('owner') != 'devin-output-filter-v1'
                or not isinstance(_source_record.get('files'), dict)):
            raise ImportError('invalid bootstrap manifest')
        _source_bytes = _bootstrap_read(_source_path, 8 * 1024 * 1024)
        if hashlib.sha256(_source_bytes).hexdigest() != _source_record['files'].get(str(_source_path)):
            raise ImportError('bootstrap source differs from install manifest')
        _source_namespace = {}
        exec(compile(_source_bytes, str(_source_path), 'exec'), _source_namespace)
        _source_namespace['activate'](_source_root)
    except (OSError, ValueError, TypeError, ImportError, SyntaxError, RecursionError) as _bootstrap_error:
        _bootstrap_message = 'Token Optimizer: bootstrap manifest gate refused: ' + str(_bootstrap_error) + '. Originals remain unchanged.'
        if __name__ == '__main__':
            print(_bootstrap_message, file=sys.stderr)
            raise SystemExit(2)
        raise ImportError(_bootstrap_message) from None


try:
    import devin_state as state
except (ImportError, SyntaxError) as _initial_error:
    _initial_message = 'Token Optimizer: bootstrap manifest gate refused: ' + str(_initial_error) + '. Originals remain unchanged.'
    if __name__ == '__main__':
        print(_initial_message, file=sys.stderr)
        raise SystemExit(2)
    raise ImportError(_initial_message) from None


def _desktop_app_roots():
    """Heuristic locations/layout only, not an authoritative app inventory."""
    # Candidate paths and applicationName are unverified heuristics; vendor
    # documentation does not list them as a supported detection interface.
    return [Path('/Applications/Devin.app/Contents/Resources/app'),
            Path.home() / 'Applications/Devin.app/Contents/Resources/app',
            Path.home() / '.local/share/Devin/resources/app']


def local_agent_surfaces() -> dict:
    desktop = []
    bundled = []
    for root in _desktop_app_roots():
        try:
            state.no_symlinks(root)
            product = root / 'product.json'
            fd = os.open(product, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
                    continue
                # Regular-file reads stay bounded; O_NONBLOCK avoids waiting on
                # a special-file substitution before the fstat refusal above.
                raw = os.read(fd, 1024 * 1024 + 1)
            finally:
                os.close(fd)
            if len(raw) > 1024 * 1024:
                continue
            metadata = json.loads(raw)
            if not isinstance(metadata, dict) or metadata.get('applicationName') != 'devin-desktop':
                continue
            desktop.append(str(root))
            # Historical Windsurf-layout candidate, not a required dependency.
            agent = root / 'extensions/windsurf/devin/bin/devin'
            try:
                state.no_symlinks(agent)
                if stat.S_ISREG(agent.lstat().st_mode):
                    bundled.append(str(agent))
            except OSError:
                pass
        except (OSError, ValueError, TypeError, RuntimeError):
            continue
    return {
        'cli_on_path': bool(shutil.which('devin')),
        'desktop_bundle_present': bool(desktop),
        'bundled_agent_file_found': bool(bundled),
        'desktop_bundle_paths': desktop,
        'bundled_agent_paths': bundled,
        'desktop_search_scope': 'heuristic paths and product/layout match; relocated, remote and other installs may be missed',
        'desktop_presence_basis': 'filesystem metadata only; product.json is not authenticated',
        'desktop_loading_verified': False,
        'desktop_execution_verified': False,
    }


PROBE_LINES = 2000


def _devin_log_hooks():
    """The newest 'hooks discovery' line Devin wrote: proof Devin itself loaded
    global hooks, not just that our command works when we run it."""
    import re
    logs = Path.home() / '.local/share/devin/cli/logs'
    try:
        candidates = sorted((entry for entry in os.scandir(logs) if entry.name.endswith('.log')),
                            key=lambda entry: entry.stat().st_mtime, reverse=True)[:40]
    except OSError:
        return {'seen': False, 'reason': 'no Devin CLI logs at ' + str(logs)}
    for entry in candidates:
        try:
            with open(entry.path, 'rb') as handle:
                handle.seek(max(0, entry.stat().st_size - 4 * 1024 * 1024))
                tail = handle.read().decode('utf-8', 'replace')
        except OSError:
            continue
        found = re.findall(r'hooks discovery: .*?loaded=(\d+) \(global=(\d+)', tail)
        if found:
            loaded, global_count = found[-1]
            return {'seen': True, 'log': entry.path, 'global_hooks_loaded': int(global_count),
                    'log_time': entry.stat().st_mtime}
    return {'seen': False, 'reason': 'no Devin session has started since logging began'}


def _run_hook(command, payload, cwd):
    """Run a configured hook command exactly as written, through the shell Devin uses."""
    run = subprocess.run(['/bin/sh', '-c', command], input=json.dumps(payload).encode(), cwd=cwd,
                         capture_output=True, timeout=30)
    try:
        decision = json.loads(run.stdout) if run.stdout.strip() else None
    except ValueError:
        decision = 'unparseable'
    return run.returncode, decision


def automatic_status(probe=False):
    """Automatic mode: recorded entries, what the Devin config holds, and with
    probe=True a real round trip through the configured hook commands."""
    import devin_hooks as hooks
    import shlex
    import tempfile
    import time
    root = state.data_dir()
    config_path = state.home() / 'config.json'
    out = {'mode': 'manual', 'config': str(config_path)}
    record_path = root / hooks.RECORD
    try:
        state.private_path(record_path)
        record = json.loads(record_path.read_text()) if record_path.exists() else None
    except (OSError, ValueError) as exc:
        out.update(mode='broken', error='hook record unreadable: ' + str(exc))
        return out
    try:
        config = json.loads(config_path.read_text()) if config_path.exists() else {}
    except (OSError, ValueError) as exc:
        config = None
        config_error = str(exc)
    if not isinstance(record, dict) or not isinstance(record.get('entries'), dict):
        if isinstance(config, dict) and any(found == 'edited' for found in hooks.installed_state(
                config, hooks.owned_entries('python3', str(root / 'plugin/devin_compress.py'))).values()):
            out['warning'] = 'Token Optimizer hook entries are in the Devin config but not recorded; remove them by hand'
        return out
    if config is None:
        out.update(mode='broken', error='Devin config unreadable: ' + config_error)
        return out
    entries = record['entries']
    wiring = hooks.installed_state(config, entries)
    out['wiring'] = wiring
    out['attested'] = record.get('org_policy') == hooks.ATTESTATION
    out['mode'] = 'automatic' if all(v == 'wired' for v in wiring.values()) and out['attested'] else 'broken'
    if out['mode'] == 'broken':
        out['error'] = 'hook entries missing or edited; rerun install.sh --devin --auto'
    try:
        hook_python = shlex.split(entries['PreToolUse']['hooks'][0]['command'])[0]
    except (KeyError, IndexError, TypeError, ValueError):
        hook_python = ''
    if out['mode'] == 'automatic' and not os.access(hook_python, os.X_OK):
        # The hook fails open (nothing breaks) but compression silently stops,
        # typically after a Python upgrade removed the versioned interpreter.
        out.update(mode='broken', error='hook Python is gone (' + hook_python +
                   '); rerun install.sh --devin --auto to repoint it')
    out['devin_log'] = _devin_log_hooks()
    if not probe or out['mode'] != 'automatic':
        return out
    pre = entries['PreToolUse']['hooks'][0]['command']
    permission = entries['PermissionRequest']['hooks'][0]['command']
    interpreter = shlex.split(pre)[0]
    result = {'passed': False}
    out['probe'] = result
    with tempfile.TemporaryDirectory(prefix='doctor-probe-', dir=root) as work:
        for index in range(20):
            (Path(work) / ('module%02d.py' % index)).write_text(
                ''.join('probe = repeated_value(%d)\n' % (i % 7) for i in range(PROBE_LINES // 20)))
        rules = hooks.load_effective_rules(config_path, Path(work))
        candidates = ['grep -rn probe .', 'ls -la']
        command = next((c for c in candidates if hooks.eligible(c, rules, interpreter)), None)
        if command is None:
            result['reason'] = ('no Exec allow rule in your Devin config covers grep or ls, so nothing '
                                'can be shown compressing here; commands your rules allow still compress')
            return out
        result['command'] = command
        tool_use_id = 'doctor-probe-' + os.urandom(8).hex()
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'exec', 'tool_input': {'command': command},
                   'tool_use_id': tool_use_id, 'session_id': 'doctor-probe', 'cwd': work}
        code, decision = _run_hook(pre, payload, work)
        wrapper = (decision or {}).get('hookSpecificOutput', {}).get('updatedInput', {}).get('command') \
            if isinstance(decision, dict) else None
        result['pre_tool_use_rewrote'] = bool(wrapper) and code == 0
        if not wrapper:
            result['reason'] = 'PreToolUse hook returned no rewrite (exit %d)' % code
            return out
        payload.update(hook_event_name='PermissionRequest', tool_input={'command': wrapper})
        code, decision = _run_hook(permission, payload, work)
        result['permission_approved_once'] = isinstance(decision, dict) and decision.get('decision') == 'approve'
        _, again = _run_hook(permission, payload, work)
        result['second_request_still_prompts'] = again is None
        other = dict(payload, tool_use_id='doctor-probe-other')
        _, foreign = _run_hook(permission, other, work)
        result['other_tool_call_still_prompts'] = foreign is None
        started = time.monotonic()
        raw = subprocess.run(shlex.split(command), cwd=work, capture_output=True, timeout=60)
        wrapped = subprocess.run(shlex.split(wrapper), cwd=work, capture_output=True, timeout=60)
        result['raw_bytes'] = len(raw.stdout) + len(raw.stderr)
        result['delivered_bytes'] = len(wrapped.stdout) + len(wrapped.stderr)
        result['exit_code_preserved'] = raw.returncode == wrapped.returncode
        result['seconds'] = round(time.monotonic() - started, 2)
        result['passed'] = all(result[k] for k in ('pre_tool_use_rewrote', 'permission_approved_once',
                                                    'second_request_still_prompts', 'other_tool_call_still_prompts',
                                                    'exit_code_preserved'))
    return out


def diagnose(probe=False):
    root = state.data_dir()
    skill = state.home() / 'skills/token-optimizer-output/SKILL.md'
    recovery_pending = (root/'pending-install.json').exists() or (root/'pending-install.json').is_symlink()
    from devin_verify import verify_install
    error=None
    # Numeric estimates are optional: the filter works without them, so an
    # unsafe metrics leaf is its own field and warning, not a readiness failure.
    numeric_errors = []
    for leaf in state.NUMERIC_STATE_LEAVES:
        try:
            state.private_path(root / leaf)
        except OSError as exc:
            numeric_errors.append(str(exc))
    try:
        ready=verify_install()
        from devin_verify import PAYLOAD
        # Import the whole manifested payload through the digest-checked
        # loader, not just the entrypoint bootstrap: a lazily imported module
        # that cannot import must still fail readiness.
        check=subprocess.run(
            [sys.executable,'-I','-c',
             'import sys\n'
             'from pathlib import Path\n'
             'root = Path(sys.argv[1]); namespace = {}\n'
             "exec(compile((root / 'devin_source.py').read_bytes(), 'devin_source.py', 'exec'), namespace)\n"
             "namespace['activate'](root)\n"
             'for name in sys.argv[2:]:\n'
             '    __import__(name)\n',
             str(root/'plugin'), *(Path(name).stem for name in PAYLOAD)],
            capture_output=True,timeout=15)
        if check.returncode:raise OSError('installed dependencies cannot import')
    except (OSError,ValueError,SyntaxError,TypeError,subprocess.SubprocessError) as exc:
        ready=False;error=str(exc)
    warnings = []
    if recovery_pending:
        ready = False
        error = (error + '; ' if error else '') + 'Recovery is pending: ' + str(root / 'pending-install.json')
        try:
            state.private_path(root / 'pending-install.json')
        except OSError as exc:
            error += ': ' + str(exc)
            if (root / 'pending-install.json').is_dir() and not (root / 'pending-install.json').is_symlink():
                warnings.append(error + '; directory-shaped pending state cannot be recovered or purged. Move it aside after inspecting it.')
            else:
                warnings.append(error + '; ordinary recovery refuses this path. Move it aside or use uninstall --purge to discard adapter state and stored captures.')
        else:
            warnings.append('Recovery is pending; real install/uninstall validates and attempts recovery, but invalid transaction contents can still refuse; uninstall --purge then discards adapter state and also removes stored captures. Dry-run refuses pending recovery.')
    for name in ('.claude/settings.json', '.claude.json'):
        if (Path.home() / name).is_file():
            warnings.append('Claude config exists and Devin imports may load its hooks: ' + name)
    for name in ('.claude/settings.json', '.claude/settings.local.json', '.devin/hooks.v1.json'):
        if (Path.cwd() / name).is_file():
            warnings.append('Project hook/config exists; this adapter does not edit it: ' + name)
    claude=Path(os.environ.get('CLAUDE_CONFIG_DIR',str(Path.home()/'.claude')))
    if (claude/'skills/token-optimizer').exists() or (claude/'skills/token-optimizer').is_symlink():
        warnings.append('Imported Claude token-optimizer skill exists; coexistence is outside this adapter.')
    for base in (Path.cwd()/'.devin',Path.cwd()/'.claude'):
        if (base/'skills/token-optimizer-output/SKILL.md').exists():
            warnings.append('Project token-optimizer-output skill can shadow installed global skill: '+str(base))
    try:
        state.private_path(root / 'install.lock')
    except OSError as exc:
        warnings.append('Install, update and uninstall will refuse until this is fixed: ' + str(exc))
    for message in numeric_errors:
        warnings.append('Numeric estimates will not be recorded: ' + message)
    loader = state.loader_path_info()
    if loader['status'] != 'documented_default':
        warnings.append('Nondefault skill directory: CLI/Desktop discovery is unverified; check the loaded skill source.')
    surfaces = local_agent_surfaces()
    result = {'recovery_pending': recovery_pending, 'local_agent_surfaces': surfaces, 'loader_path': loader, 'surface': 'explicit_capture_filter', 'status': 'recovery-pending' if recovery_pending else 'contract-only beta',
              'installed': ready, 'readiness_error': error, 'numeric_state_ok': not numeric_errors, 'devin_cli_on_path': surfaces['cli_on_path'],
              'live_devin_session_tested': False, 'warnings': warnings,
              'local_probe_passed': None}
    if probe and ready:
        from devin_compress import filter_capture, seal_capture
        tmp = state.prepare_capture()
        out, err = tmp / 'stdout', tmp / 'stderr'
        raw = b'repeated output line\n' * 1000
        try:
            out.write_bytes(raw); err.write_bytes(b'')
            seal_capture(tmp, 0)
            value = filter_capture(out, err, 0, 'generic', 'doctor-local-probe', record_metrics=False)
            result['local_probe_passed'] = value.code == 0 and len(value.stdout) < len(raw) and out.read_bytes() == raw
        finally:
            for leaf in ('stdout','stderr','.prepared','complete.json'):
                (tmp/leaf).unlink(missing_ok=True)
            tmp.rmdir()
        result['probe_scope'] = 'local filter only; not Devin adherence or provider usage'
    if ready:
        try:
            result['automatic'] = automatic_status(probe)
        except (OSError, ValueError, ImportError, subprocess.SubprocessError) as exc:
            result['automatic'] = {'mode': 'broken', 'error': str(exc)}
        automatic = result['automatic']
        if automatic.get('mode') == 'broken':
            warnings.append('Automatic mode is broken: ' + str(automatic.get('error')))
        elif automatic.get('mode') == 'automatic':
            seen = automatic.get('devin_log', {})
            if seen.get('seen') and seen.get('global_hooks_loaded', 0) < 2:
                warnings.append('Devin\'s newest session log shows ' + str(seen.get('global_hooks_loaded')) +
                                ' global hooks loaded; restart Devin so it loads the Token Optimizer hooks')
        result['status'] = 'automatic' if automatic.get('mode') == 'automatic' else result['status']
    result['limits'] = 'caller asserts foreground completion and exit code; not a sandbox against malicious same-UID code'
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--probe',action='store_true')
    args=p.parse_args(argv)
    try:
        result=diagnose(args.probe); print(json.dumps(result,indent=2))
        automatic_probe = result.get('automatic', {}).get('probe')
        failed = result['local_probe_passed'] is False or (automatic_probe is not None and not automatic_probe.get('passed')
                                                            and 'command' in automatic_probe)
        return 0 if result['installed'] and not failed and result.get('automatic', {}).get('mode') != 'broken' else 1
    except (OSError, ValueError, ImportError, SyntaxError, RecursionError, RuntimeError) as exc:
        print('Token Optimizer: readiness refused: '+str(exc)+'. Originals remain unchanged.',file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
