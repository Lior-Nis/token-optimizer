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



def diagnose(probe=False):
    root = state.data_dir()
    skill = state.home() / 'skills/token-optimizer-output/SKILL.md'
    from devin_verify import verify_install
    error=None
    try:
        ready=verify_install()
        check=subprocess.run([sys.executable,'-I',str(root/'plugin/devin_compress.py'),'--help'],
                             capture_output=True,timeout=15)
        if check.returncode:raise OSError('installed dependencies cannot import')
    except (OSError,ValueError,SyntaxError,TypeError,subprocess.SubprocessError) as exc:
        ready=False;error=str(exc)
    warnings = []
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
    result = {'surface': 'explicit_capture_filter', 'status': 'contract-only beta',
              'installed': ready, 'readiness_error': error, 'devin_cli_on_path': bool(shutil.which('devin')),
              'live_devin_session_tested': False, 'warnings': warnings,
              'local_probe_passed': None}
    if probe and ready:
        from devin_compress import filter_capture, seal_capture
        tmp = state.prepare_capture()
        out, err = tmp / 'stdout', tmp / 'stderr'
        raw = b'repeated output line\n' * 1000
        out.write_bytes(raw); err.write_bytes(b'')
        seal_capture(tmp, 0)
        try:
            value = filter_capture(out, err, 0, 'generic', 'doctor-local-probe', record_metrics=False)
            result['local_probe_passed'] = value.code == 0 and len(value.stdout) < len(raw) and out.read_bytes() == raw
        finally:
            for leaf in ('stdout','stderr','.prepared','complete.json'):
                (tmp/leaf).unlink(missing_ok=True)
            tmp.rmdir()
        result['probe_scope'] = 'local filter only; not Devin adherence or provider usage'
    result['limits'] = 'caller asserts foreground completion and exit code; not a sandbox against malicious same-UID code'
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--probe',action='store_true')
    args=p.parse_args(argv)
    try:
        result=diagnose(args.probe); print(json.dumps(result,indent=2))
        return 0 if result['installed'] and result['local_probe_passed'] is not False else 1
    except (OSError, ValueError, ImportError, SyntaxError, RecursionError) as exc:
        print('Token Optimizer: readiness refused: '+str(exc)+'. Originals remain unchanged.',file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
