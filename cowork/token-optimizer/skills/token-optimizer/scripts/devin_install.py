#!/usr/bin/env python3
"""Install the explicit Devin output skill, never edit host config or hooks."""
from __future__ import annotations
import argparse
import ast
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import shutil
import sys
import tempfile

import devin_state as state
from py_trust import py_path_is_trusted

SCRIPTS = Path(__file__).resolve().parent
from devin_verify import PAYLOAD
SKILL_NAME = 'token-optimizer-output'
MARKER = 'devin-output-filter-v1'


def _render_payload(name: str) -> bytes:
    """Commit installed bytes, with a direct-execution gate on internal modules."""
    source = (SCRIPTS / name).read_bytes()
    if name in ('devin_compress.py', 'devin_doctor.py'):
        return source
    tree = ast.parse(source)
    prefix_end = 0
    prefix_count = 0
    for index, node in enumerate(tree.body):
        docstring = (index == 0 and isinstance(node, ast.Expr)
                     and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))
        future = isinstance(node, ast.ImportFrom) and node.module == '__future__'
        if not (docstring or future):
            break
        prefix_end = node.end_lineno
        prefix_count += 1
    if any(node.lineno <= prefix_end for node in tree.body[prefix_count:]):
        raise OSError('payload prefix shares a line with executable code')
    lines = source.splitlines(keepends=True)
    guard = (b"\n# Installed Devin payload: internal modules have no CLI contract.\n"
             b"if __name__ == '__main__':\n"
             b"    import sys\n"
             b"    print('Token Optimizer: internal module requires an activated entrypoint. Originals remain unchanged.', file=sys.stderr)\n"
             b"    raise SystemExit(2)\n\n")
    result = b''.join(lines[:prefix_end]) + guard + b''.join(lines[prefix_end:])
    compile(result, name, 'exec')
    return result


def skill_dir() -> Path:
    return state.home() / 'skills' / SKILL_NAME


def manifest_path() -> Path:
    return state.data_dir() / 'install.json'


def _private_bytes(path: Path) -> bytes:
    state.no_symlinks(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as handle:
        before=os.fstat(handle.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid()
                or before.st_mode&0o077 or before.st_nlink!=1):
            raise OSError('private single-link ownership file required')
        content=handle.read()
        after=os.fstat(handle.fileno())
        if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
            raise OSError('ownership file changed while reading')
        return content


def _hash(path: Path) -> str:
    try:return hashlib.sha256(_private_bytes(path)).hexdigest()
    except FileNotFoundError:
        raise OSError('owned file is missing; uninstall then install to repair while preserving original captures')


def _clear_pending(path: Path) -> None:
    path.unlink(missing_ok=True)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def _resolved_keys(files) -> list:
    """Recorded manifest keys may carry a non-canonical home spelling (for
    example '..' segments) written before the override was resolved; compare
    them by where they resolve."""
    return sorted(str(Path(name).resolve()) for name in files)


def _resolved_files(files) -> dict:
    return {str(Path(name).resolve()): digest for name, digest in files.items()}


def _ownership(*, allow_missing=False) -> dict | None:
    p = state.private_path(manifest_path())
    if not p.exists():
        return None
    if p.exists() and p.stat().st_nlink != 1:raise OSError('ownership record hardlink refused')
    record = json.loads(_private_bytes(p))
    if not isinstance(record, dict) or record.get('owner') != MARKER or not isinstance(record.get('files'), dict):
        raise OSError('invalid ownership record')
    expected = {str(state.data_dir() / 'plugin' / name) for name in PAYLOAD} | {str(skill_dir() / 'SKILL.md')}
    legacy = expected - {str(state.data_dir() / 'plugin' / name) for name in ('plugin_env.py', 'refetch_fingerprint.py')}
    if _resolved_keys(record['files']) not in (sorted(expected), sorted(legacy)):
        raise OSError('ownership paths do not match this install; the manifest was recorded by a different adapter version or home spelling -- run uninstall --purge to discard adapter state')
    for name, digest in record['files'].items():
        if allow_missing and not Path(name).exists():
            state.no_symlinks(Path(name))
            continue
        if _hash(Path(name)) != digest:
            raise OSError('installed file changed; refusing overwrite/removal')
    return record


@contextmanager
def _lock():
    import fcntl
    state.private_path(state.data_dir(), directory=True, create=True)
    lock = state.private_path(state.data_dir() / 'install.lock', create=True)
    with lock.open('r+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _atomic(path: Path, content: bytes) -> None:
    state.no_symlinks(path)
    # Unique private temp avoids a stale O_EXCL name wedging future attempts.
    # A killed pre-publication temp is retained, never claimed/deleted as owned.
    fd, temporary_name = tempfile.mkstemp(prefix='.install-',dir=state.data_dir())
    temporary=Path(temporary_name)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _recover_pending() -> None:
    """Resume only a fingerprinted fresh-install transaction, never foreign files."""
    pending=state.private_path(state.data_dir()/'pending-install.json')
    if not pending.exists():return
    if pending.stat().st_nlink!=1:raise OSError('pending ownership hardlink refused')
    value=json.loads(_private_bytes(pending))
    expected={str(state.data_dir()/'plugin'/name) for name in PAYLOAD}|{str(skill_dir()/'SKILL.md')}
    if (not isinstance(value,dict) or value.get('owner')!=MARKER
            or not isinstance(value.get('files'),dict) or _resolved_keys(value['files'])!=sorted(expected)):
        raise OSError('invalid pending install record; preserved; run uninstall --purge to discard adapter state')
    # Reconcile published new bytes first: stale pending must not undo a completed update.
    if manifest_path().exists():
        try:
            published=_ownership()
        except OSError:
            published=None
        if published and _resolved_files(published['files'])==_resolved_files(value['files']):
            _clear_pending(pending);return
    source=SCRIPTS.parents[2]/'devin/SKILL.md'
    if not source.is_file():raise OSError('pending repair requires original checkout')
    rendered=source.read_text().replace('@PYTHON@',shlex.quote(os.path.realpath(sys.executable))).replace('@FILTER@',shlex.quote(str(state.data_dir()/'plugin/devin_compress.py')))
    current={str(state.data_dir()/'plugin'/name):hashlib.sha256(_render_payload(name)).hexdigest() for name in PAYLOAD}
    current[str(skill_dir()/'SKILL.md')]=hashlib.sha256(rendered.encode()).hexdigest()
    if _resolved_files(value['files'])!=current:
        raise OSError('pending version/content ambiguous; preserved; run uninstall --purge to discard adapter state or follow the documented manual repair')
    old=value.get('previous')
    if old is not None:
        legacy=expected - {str(state.data_dir()/'plugin'/name) for name in ('plugin_env.py','refetch_fingerprint.py')}
        if not isinstance(old,dict) or _resolved_keys(old) not in (sorted(expected),sorted(legacy)):raise OSError('invalid refresh recovery record')
        if any(not isinstance(content,str) for content in old.values()) or not isinstance(value.get('previous_manifest'),str):
            raise OSError('invalid refresh recovery encoding')
        previous={name:base64.b64decode(content,validate=True) for name,content in old.items()}
        previous_manifest=base64.b64decode(value['previous_manifest'],validate=True)
        previous_record=json.loads(previous_manifest)
        if (not isinstance(previous_record,dict) or previous_record.get('owner')!=MARKER
                or not isinstance(previous_record.get('files'),dict)
                or set(previous_record['files'])!=set(previous)
                or any(not isinstance(digest,str) or len(digest)!=64
                       or any(ch not in '0123456789abcdef' for ch in digest)
                       for digest in previous_record['files'].values())):
            raise OSError('invalid previous ownership commitments; preserved')
        if _private_bytes(manifest_path())!=previous_manifest:
            raise OSError('previous ownership differs from preserved manifest; preserved')
        if any(hashlib.sha256(content).hexdigest()!=previous_record['files'][name]
               for name,content in previous.items()):
            raise OSError('previous rollback source differs from ownership commitments; preserved')
        for name,content in previous.items():
            path=Path(name);state.no_symlinks(path)
            if path.exists() and _hash(path) not in (hashlib.sha256(content).hexdigest(),value['files'][name]):
                raise OSError('edited interrupted refresh; preserved')
            temporary=path.with_name(path.name+'.install-tmp')
            if temporary.exists():
                raise OSError('pre-existing refresh temporary ownership ambiguous; preserved; use manual repair')
        added=set(value['files']) - set(previous)
        for name in added:
            path=Path(name);state.no_symlinks(path)
            if path.exists() and _hash(path)!=value['files'][name]:
                raise OSError('edited newly added refresh leaf; preserved')
        for name in added:
            Path(name).unlink(missing_ok=True)
        for name,content in previous.items():
            path=Path(name)
            _atomic(path,content)
        _atomic(manifest_path(),previous_manifest)
        _clear_pending(pending);return
    # If the fresh install completed publication, validate before clearing pending.
    if manifest_path().exists():
        _ownership();_clear_pending(pending);return
    removable=[]
    recorded=_resolved_files(value['files'])
    for folder in (skill_dir(),state.data_dir()/'plugin'):
        state.no_symlinks(folder)
        if not folder.exists():continue
        for item in folder.iterdir():
            key=str(item)
            original=key.removesuffix('.install-tmp')
            if key.endswith('.install-tmp'):
                raise OSError('pre-existing temporary ownership ambiguous; preserved; use manual repair')
            if original not in expected or not item.is_file() or item.is_symlink():
                raise OSError('foreign content in interrupted install; preserved')
            # Partial/foreign temps are never erased: exact intended bytes must match.
            info=item.stat()
            if info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_nlink!=1:
                raise OSError('unsafe interrupted install content; preserved')
            if _hash(item)!=recorded[original]:
                raise OSError('edited interrupted install content; preserved')
            removable.append(item)
    for item in removable:item.unlink()
    for folder in (skill_dir(),state.data_dir()/'plugin'):
        try:folder.rmdir()
        except FileNotFoundError:pass
    _clear_pending(pending)


def install(*, dry_run=False) -> None:
    if os.name != 'posix':
        raise OSError('native Windows unsupported; use a POSIX environment')
    root, skill = state.data_dir(), skill_dir()
    state.no_symlinks(root); state.no_symlinks(skill)
    # Stable trusted interpreter, never a bare PATH command in an installed skill.
    if not py_path_is_trusted(sys.executable):
        raise OSError('no trusted Python interpreter')
    source = SCRIPTS.parents[2] / 'devin' / 'SKILL.md'
    if not source.is_file():
        raise OSError('run install from a Token Optimizer checkout')
    for name in PAYLOAD:
        if not (SCRIPTS / name).is_file():
            raise OSError('missing payload: ' + name)
    if dry_run:
        print('Would install explicit Devin output skill at ' + str(skill))
        return
    with _lock():
        _recover_pending()
        owned = _ownership()
        if not owned and (skill.exists() or (root / 'plugin').exists()):
            raise OSError('foreign install destination already exists')
        if owned:
            # Extra files are not owned; do not erase or publish over them.
            actual = {str(p) for folder in (skill, root / 'plugin') for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
            recorded = set(_resolved_files(owned['files']))
            if actual != recorded:
                stray = sorted(actual - recorded) + sorted(recorded - actual)
                raise OSError('unowned files in install destination: ' + ', '.join(stray) +
                              '; remove them or run uninstall --purge first')
        # Fresh installs roll back only leaves/directories created by this attempt.
        # Refresh retains old bytes and manifest until a complete update succeeds.
        old_bytes={name:Path(name).read_bytes() for name in owned['files']} if owned else {}
        old_manifest=manifest_path().read_bytes() if owned else None
        new_leaves=[]
        pending=state.data_dir()/'pending-install.json'
        intended={str(root/'plugin'/name):hashlib.sha256(_render_payload(name)).hexdigest() for name in PAYLOAD}
        rendered=source.read_text().replace('@PYTHON@',shlex.quote(os.path.realpath(sys.executable))).replace('@FILTER@',shlex.quote(str(root/'plugin/devin_compress.py')))
        intended[str(skill/'SKILL.md')]=hashlib.sha256(rendered.encode()).hexdigest()
        transaction={'owner':MARKER,'files':intended}
        if owned:
            transaction['previous']={name:base64.b64encode(content).decode('ascii') for name,content in old_bytes.items()}
            transaction['previous_manifest']=base64.b64encode(old_manifest).decode('ascii')
        _atomic(pending,json.dumps(transaction,sort_keys=True).encode())
        try:
            state.private_path(root / 'plugin', directory=True, create=True)
            state.private_dir(skill)
            files = {}
            for name in PAYLOAD:
                target = root / 'plugin' / name
                new_leaves.append(str(target))
                _atomic(target, _render_payload(name))
                files[str(target)] = _hash(target)
            text = source.read_text().replace('@PYTHON@', shlex.quote(os.path.realpath(sys.executable)))
            text = text.replace('@FILTER@', shlex.quote(str(root / 'plugin' / 'devin_compress.py')))
            target = skill / 'SKILL.md'
            new_leaves.append(str(target))
            _atomic(target, text.encode())
            files[str(target)] = _hash(target)
            _atomic(manifest_path(), json.dumps({'owner': MARKER, 'files': files}, sort_keys=True).encode())
            state.enable_consent()
            _clear_pending(pending)
        except Exception:
            if owned:
                for name in set(new_leaves) - set(old_bytes):
                    Path(name).unlink(missing_ok=True)
                for name,content in old_bytes.items():
                    _atomic(Path(name),content)
                _atomic(manifest_path(),old_manifest)
            else:
                for name in new_leaves:
                    Path(name).unlink(missing_ok=True)
                manifest_path().unlink(missing_ok=True)
                for folder in (skill,root/'plugin'):
                    try:folder.rmdir()
                    except OSError:pass
            _clear_pending(pending)
            raise
    print('Installed Devin explicit output skill (contract-only beta; no live session proof).')
    print('Consent: private captures may contain confidential/personal data; the adapter makes no extra copies beyond the captures you create.')
    print('A raw passthrough removes its capture after delivery; a compressed result keeps its capture for recovery until you delete it or uninstall --purge. A failed delivery keeps the capture.')
    print('Failures, stderr, binary and credential-matching output pass through RAW to Devin/model context.')
    print('Numeric local estimates retain 30 days, at most 10000 rows.')


def uninstall(*, dry_run=False, purge=False) -> None:
    if os.name != 'posix':
        raise OSError('native Windows unsupported')
    # Purge is independent of an install manifest: plain uninstall may precede it.
    # Under purge a stale or foreign-version pending record is deleted, not
    # reconciled, so it must never block the purge path.
    if not dry_run and not purge:
        with _lock():_recover_pending()
    try:
        owned = _ownership(allow_missing=True)
    except OSError:
        if not purge:
            raise
        owned = None
    if dry_run:
        print('Would remove owned files and requested numeric state' +
              ('; stored captures will be removed.' if purge else '; captures remain unchanged.'))
        return
    if not owned and not purge:
        print('No owned Devin install found.')
        return
    with _lock():
        try:
            owned = _ownership(allow_missing=True)
        except OSError:
            if not purge:
                raise
            owned = None
        if owned:
            for name in owned['files']:
                Path(name).unlink(missing_ok=True)
            manifest_path().unlink(missing_ok=True)
        cache = state.data_dir() / 'plugin' / '__pycache__'
        if cache.exists():
            state.no_symlinks(cache)
            for item in cache.iterdir():
                if item.name.endswith('.pyc') and item.is_file() and not item.is_symlink():
                    item.unlink()
            try: cache.rmdir()
            except OSError: pass
        for folder in (skill_dir(), state.data_dir() / 'plugin'):
            try: folder.rmdir()
            except OSError: pass
        if purge:
            for name in ('metrics.db', 'metrics.db-journal', 'metrics.db-wal', 'metrics.db-shm',
                         'consent.json', 'session-salt', 'install.json', 'pending-install.json'):
                p = state.private_path(state.data_dir() / name)
                p.unlink(missing_ok=True)
            # A manifest recorded by another version or spelling can leave
            # files the current manifest cannot name; purge removes every
            # regular file inside the adapter-owned directories regardless.
            for folder in (state.data_dir() / 'plugin', skill_dir()):
                try:
                    if folder.is_symlink() or not folder.is_dir():
                        continue
                    state.no_symlinks(folder)
                except OSError:
                    continue
                for item in folder.iterdir():
                    if item.is_file() and not item.is_symlink():
                        try: item.unlink()
                        except OSError: pass
                try: folder.rmdir()
                except OSError: pass
            captures = state.data_dir() / 'captures'
            if captures.exists():
                state.no_symlinks(captures)
                for folder in captures.iterdir():
                    if not folder.is_dir() or folder.is_symlink() or not re.fullmatch(r'capture-[0-9a-f]{32}', folder.name):
                        continue
                    # Stored captures may hold credentials; purge removes them.
                    # Foreign-named content keeps its directory (and itself).
                    state.delete_capture(folder)
                try: captures.rmdir()
                except OSError: pass
            print('Purged numeric metrics, SQLite sidecars, consent, HMAC key and stored captures.')
    if not purge:
        print('Original captures unchanged.')
    print('Removed owned Devin skill/payload.')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'uninstall'))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--purge', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'install' and args.purge:
        parser.error('--purge is uninstall-only')
    try:
        if args.action == 'install':
            install(dry_run=args.dry_run)
        else:
            uninstall(dry_run=args.dry_run, purge=args.purge)
        return 0
    except RecursionError as exc:
        print('Devin install refused: ' + str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print('Devin install refused: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
