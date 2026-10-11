"""Installed payload integrity/readiness shared by doctor and filter."""
import hashlib
import json
from pathlib import Path
import devin_state as state

PAYLOAD = ('devin_compress.py', 'devin_state.py', 'devin_doctor.py', 'devin_verify.py', 'devin_source.py',
           'bash_compress.py', 'bash_whitelist.py', 'credential_patterns.py',
           'token_estimate.py', 'command_filters.py', 'runtime_env.py', 'py_trust.py')

def verify_install():
    root=state.data_dir()
    manifest=state.private_path(root/'install.json')
    if manifest.stat().st_nlink!=1:raise OSError('manifest hardlink refused')
    record=json.loads(manifest.read_text())
    if not isinstance(record,dict) or not isinstance(record.get('files'),dict):
        raise OSError('invalid installed payload manifest types')
    if any(not isinstance(v,str) or len(v)!=64 or any(ch not in '0123456789abcdef' for ch in v) for v in record['files'].values()):
        raise OSError('invalid manifest digest')
    expected={str(root/'plugin'/name) for name in PAYLOAD}|{str(state.home()/'skills/token-optimizer-output/SKILL.md')}
    if record.get('owner')!='devin-output-filter-v1' or set(record.get('files',{}))!=expected:
        raise OSError('invalid installed payload manifest')
    for name,digest in record['files'].items():
        path=state.private_path(Path(name))
        if path.stat().st_nlink!=1:raise OSError('payload hardlink refused')
        content=path.read_bytes()
        if hashlib.sha256(content).hexdigest()!=digest:raise OSError('installed payload missing or changed')
        if path.suffix=='.py':compile(content,str(path),'exec')
    if not state.consent_ok():raise OSError('missing local data consent')
    return True
