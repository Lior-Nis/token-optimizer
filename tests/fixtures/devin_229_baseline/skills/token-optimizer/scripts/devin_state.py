"""Private local output estimates for Devin. Never stores captured text."""
from __future__ import annotations
import hashlib
import hmac
import secrets
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time
import uuid

SOURCE = 'devin_captured_output_estimate'
MAX_RECORDS = 10000
RETENTION_DAYS = 30


def home() -> Path:
    base = Path.home().absolute()
    raw = os.environ.get('TOKEN_OPTIMIZER_DEVIN_HOME')
    if raw:
        candidate = Path(raw)
    else:
        xdg = os.environ.get('XDG_CONFIG_HOME')
        candidate = Path(xdg) / 'devin' if xdg else base / '.config' / 'devin'
    if not candidate.is_absolute() or not candidate.resolve().is_relative_to(base.resolve()):
        raise OSError('Devin home must be an absolute path inside HOME')
    if any(not ch.isprintable() or ch == '`' for ch in str(candidate)):
        raise OSError('unsupported control/markdown characters in home path')
    no_symlinks(candidate)
    return candidate


def data_dir() -> Path:
    return home() / 'token-optimizer'


def no_symlinks(path: Path) -> None:
    for component in [*reversed(path.absolute().parents), path.absolute()]:
        if component.is_symlink():
            raise OSError('symlink path refused')


def private_path(path: Path, *, directory=False, create=False) -> Path:
    no_symlinks(path)
    if create:
        if directory:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        elif not path.exists():
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            except FileExistsError:
                no_symlinks(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    else:
        if info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1 and not directory:
            raise OSError('adapter state must be owner-only')
        if directory != stat.S_ISDIR(info.st_mode):
            raise OSError('unexpected state type')
    return path


def enable_consent() -> None:
    private_path(data_dir(), directory=True, create=True)
    target = private_path(data_dir() / 'consent.json')
    with tempfile.NamedTemporaryFile(mode='w', dir=data_dir(), delete=False) as out:
        json.dump({'devin_consent': True, 'schema': 1}, out)
        temporary = out.name
    os.replace(temporary, target)


def consent_ok() -> bool:
    try:
        private_path(data_dir(), directory=True)
        p = private_path(data_dir() / 'consent.json')
        return json.loads(p.read_text()) == {'devin_consent': True, 'schema': 1}
    except (OSError, ValueError, TypeError):
        return False


def record(session: str, original: str, delivered: str, outcome: str) -> None:
    from token_estimate import estimate_tokens
    private_path(data_dir(), directory=True)
    target = private_path(data_dir() / 'metrics.db', create=True)
    # SQLite sidecars inherit the DB's private mode. Refuse existing redirects.
    for suffix in ('-journal', '-wal', '-shm'):
        sidecar=Path(str(target)+suffix)
        no_symlinks(sidecar)
        try:info=sidecar.lstat()
        except FileNotFoundError:continue
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid():
            raise OSError('foreign SQLite sidecar refused')
    with sqlite3.connect(target, timeout=10) as db:
        db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, session_key TEXT, created REAL, source TEXT, original_estimate INTEGER, delivered_estimate INTEGER, outcome TEXT)')
        db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)',
                   (uuid.uuid4().hex, hmac.new(session_salt(), session.encode(), hashlib.sha256).hexdigest(), time.time(),
                    SOURCE, estimate_tokens(original), estimate_tokens(delivered), outcome))
        db.execute('DELETE FROM events WHERE created < ?', (time.time() - RETENTION_DAYS * 86400,))
        db.execute('DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY created DESC LIMIT -1 OFFSET ?)', (MAX_RECORDS,))


def summary() -> dict:
    result = {'source': SOURCE, 'provider_tokens': None, 'cost': None,
              'quality_score': None, 'delivery_verified': False, 'events': 0,
              'original_estimate': 0, 'delivered_estimate': 0,
              'estimated_output_reduction': 0}
    private_path(data_dir(), directory=True)
    target = private_path(data_dir() / 'metrics.db')
    if not target.exists():
        return result
    with sqlite3.connect(target.as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT COUNT(*), COALESCE(SUM(original_estimate),0), COALESCE(SUM(delivered_estimate),0) FROM events WHERE created >= ?', (time.time() - RETENTION_DAYS * 86400,)).fetchone()
    result.update(events=row[0], original_estimate=row[1], delivered_estimate=row[2],
                  estimated_output_reduction=row[1] - row[2])
    return result


def session_salt() -> bytes:
    p = private_path(data_dir() / 'session-salt', create=True)
    import fcntl
    with p.open('r+b') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        content = handle.read()
        if not content:
            content = secrets.token_bytes(32)
            handle.write(content); handle.flush()
        if len(content) != 32:
            raise OSError('invalid session salt')
        return content


def prepare_capture() -> Path:
    if not consent_ok():
        raise OSError('local data consent required')
    root = private_path(data_dir() / 'captures', directory=True, create=True)
    result = root / ('capture-' + uuid.uuid4().hex)
    result.mkdir(mode=0o700)
    for name in ('stdout', 'stderr'):
        private_path(result / name, create=True)
    provenance = {}
    for name in ('stdout', 'stderr'):
        info = (result / name).stat()
        provenance[name] = [info.st_dev, info.st_ino]
    p = private_path(result / '.prepared', create=True)
    p.write_text(json.dumps(provenance))
    return result
