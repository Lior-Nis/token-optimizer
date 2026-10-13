"""Installed payload integrity/readiness shared by doctor and filter."""

import hashlib
import json
import os
import stat
from pathlib import Path
import devin_state as state

from devin_source import PAYLOAD

def verify_install():
    root=state.data_dir()
    plugin=state.private_path(root/'plugin',directory=True)
    for entry in plugin.iterdir():
        if entry.name in PAYLOAD:
            state.private_path(entry)
            continue
        if entry.name=='__pycache__':
            state.no_symlinks(entry)
            info=entry.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o022:
                raise OSError('unsafe incidental cache directory')
            for cached in entry.iterdir():
                state.no_symlinks(cached)
                info=cached.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid()
                        or info.st_nlink!=1 or info.st_mode&0o022 or not cached.name.endswith('.pyc')):
                    raise OSError('unmanifested artifact in incidental cache: ' + cached.name +
                                  '; remove the stray file or reinstall the Devin skill')
            continue
        raise OSError('unmanifested artifact in installed payload: ' + entry.name +
                      '; remove the stray file or reinstall the Devin skill')
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
