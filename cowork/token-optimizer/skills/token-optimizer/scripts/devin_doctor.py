#!/usr/bin/env python3
"""Readiness check and synthetic local filter probe; records no metrics and never starts a Devin session."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

# Load the source-only importer itself from source, never through __pycache__.
_source_root = Path(__file__).resolve().parent
if _source_root.name == 'plugin':
    _source_namespace = {}
    exec(compile((_source_root / 'devin_source.py').read_bytes(), str(_source_root / 'devin_source.py'), 'exec'), _source_namespace)
    _source_namespace['activate'](_source_root)

import devin_state as state


def diagnose(probe=False):
    root = state.data_dir()
    skill = state.home() / 'skills/token-optimizer-output/SKILL.md'
    from devin_verify import verify_install
    error=None
    try:
        ready=verify_install()
        check=subprocess.run([sys.executable,'-I','-c',
            'import sys;sys.path.insert(0,'+repr(str(root/'plugin'))+');exec(compile(open('+repr(str(root/'plugin/devin_source.py'))+',"rb").read(),"devin_source.py","exec"));activate('+repr(str(root/'plugin'))+');import bash_compress, credential_patterns, token_estimate, devin_compress'],capture_output=True,timeout=15)
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
    except (OSError, ValueError) as exc:
        print(json.dumps({'installed':False,'error':str(exc),'live_devin_session_tested':False}))
        return 1

if __name__=='__main__':
    raise SystemExit(main())
