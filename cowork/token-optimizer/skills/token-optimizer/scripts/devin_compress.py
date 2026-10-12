#!/usr/bin/env python3
"""Explicit captured-output filter. No hooks, shell or command execution."""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import os
import hashlib
import json
from pathlib import Path
import shlex
import stat
import re
import sys

# Load the source-only importer itself from source, never through __pycache__.
_source_root = Path(__file__).resolve().parent
if _source_root.name == 'plugin':
    _source_namespace = {}
    exec(compile((_source_root / 'devin_source.py').read_bytes(), str(_source_root / 'devin_source.py'), 'exec'), _source_namespace)
    _source_namespace['activate'](_source_root)

from devin_state import consent_ok, record

MAX_CAPTURE = 8 * 1024 * 1024
FORMATS = {'generic': 'captured-output', 'git-status': 'git status',
           'git-log': 'git log', 'listing': 'ls', 'search': 'rg',
           'tests': 'pytest', 'build': 'tsc'}


@dataclass(frozen=True)
class FilterResult:
    stdout: bytes
    stderr: bytes
    code: int
    outcome: str
    metrics_recorded: bool = False


def _directory_fd(path: Path) -> int:
    """Open absolute directory components without following any symlink."""
    if not path.is_absolute():
        raise OSError('absolute directory required')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    try:
        for component in path.parts[1:]:
            if component in ('.', '..'):
                raise OSError('relative directory component refused')
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd); fd = next_fd
        return fd
    except Exception:
        os.close(fd); raise


def _read_leaf(directory_fd: int, name: str, cap: int) -> tuple[bytes, object]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_mode & 0o077 or before.st_nlink != 1):
            raise OSError('capture must be bounded, private and unlinked')
        if before.st_size > cap:
            raise OSError('capture exceeds the ' + str(cap) + ' byte limit; read the original files directly')
        content = stream.read(cap + 1)
        after = os.fstat(stream.fileno())
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if len(content) > cap:
            raise OSError('capture exceeded the ' + str(cap) + ' byte limit while reading')
        if identity(before) != identity(after) or identity(after) != identity(current):
            raise OSError('capture changed during reading')
        return content, after


def _repair_orphaned_seal(directory_fd: int) -> None:
    """A seal killed between os.link and unlink left complete.json linked to a
    stray tempfile name in this directory. Only a same-inode sibling under the
    tempfile prefix is removed; any other extra link still refuses."""
    try:
        seal = os.stat('complete.json', dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if seal.st_nlink != 2 or not stat.S_ISREG(seal.st_mode):
        return
    for name in os.listdir(directory_fd):
        if not name.startswith('tmp'):
            continue
        try:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if info.st_ino == seal.st_ino and info.st_dev == seal.st_dev and stat.S_ISREG(info.st_mode):
            os.unlink(name, dir_fd=directory_fd)
            break


def read_pair(directory: Path, *, sealed: bool, exit_code=None) -> tuple[bytes, bytes]:
    from devin_state import data_dir
    root = data_dir() / 'captures'
    if directory.parent != root or not re.fullmatch(r'capture-[0-9a-f]{32}', directory.name):
        raise OSError('adapter-prepared capture required')
    root_fd = _directory_fd(root)
    fd = None
    try:
        info = os.fstat(root_fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise OSError('capture root must be private')
        fd = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise OSError('capture directory must be private')
        marker, _ = _read_leaf(fd, '.prepared', 4096)
        provenance = json.loads(marker)
        out, out_info = _read_leaf(fd, 'stdout', MAX_CAPTURE)
        err, err_info = _read_leaf(fd, 'stderr', MAX_CAPTURE)
        # Valid JSON of the wrong type must be the normal refusal, never a crash.
        if (not isinstance(provenance, dict)
                or provenance.get('stdout') != [out_info.st_dev, out_info.st_ino]
                or provenance.get('stderr') != [err_info.st_dev, err_info.st_ino]):
            raise OSError('prepared capture identity changed')
        if sealed:
            _repair_orphaned_seal(fd)
            content, _ = _read_leaf(fd, 'complete.json', 4096)
            try:
                seal = json.loads(content)
            except ValueError:
                raise OSError('seal record torn by an interrupted write; re-run seal to repair')
            if (not isinstance(seal, dict) or seal.get('exit_code') != exit_code
                    or seal.get('stdout_sha256') != hashlib.sha256(out).hexdigest()
                    or seal.get('stderr_sha256') != hashlib.sha256(err).hexdigest()):
                raise OSError('captures incomplete or changed after sealing')
        return out, err
    finally:
        if fd is not None: os.close(fd)
        os.close(root_fd)


def read_capture(path: Path) -> bytes:
    if path.name not in ('stdout', 'stderr'):
        raise OSError('fixed capture leaf required')
    out, err = read_pair(path.parent, sealed=False)
    return out if path.name == 'stdout' else err


def filter_capture(stdout: Path, stderr: Path, exit_code: int, format_hint: str,
                   session: str, *, record_metrics: bool = True) -> FilterResult:
    if os.name != 'posix' or not consent_ok():
        raise OSError('Devin output filter requires a POSIX install with local data consent')
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or not 0 <= exit_code <= 255:
        raise ValueError('exit code must be 0 through 255')
    if format_hint not in FORMATS or not isinstance(session, str) or not session or len(session) > 256:
        raise ValueError('invalid format or session identifier')
    if stdout == stderr or stdout.parent != stderr.parent or stdout.name != 'stdout' or stderr.name != 'stderr':
        raise OSError('distinct stdout/stderr from one prepared capture required')
    raw, errors = read_pair(stdout.parent, sealed=True, exit_code=exit_code)
    if exit_code or errors:
        return FilterResult(raw, errors, exit_code, 'passthrough')
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeError:
        return FilterResult(raw, b'', 0, 'binary_passthrough')
    # Permit only complete SGR color CSI and OSC8 links; reject other controls.
    cleaned = re.sub(r'\x1b\[[0-9;]*m|\x1b\]8;[^\x00-\x1f]*;(?:[^\x00-\x1f]*)(?:\x07|\x1b\\)', '', text)
    import unicodedata
    if any((unicodedata.category(ch) in ('Cc', 'Cf', 'Zl', 'Zp')) and ch not in '\n\r\t' for ch in cleaned):
        return FilterResult(raw, b'', 0, 'binary_passthrough')
    delivered, outcome = text, 'passthrough'
    if Path(__file__).resolve().parent.name == 'plugin':
        from devin_verify import verify_install
        verify_install()
    try:
        from credential_patterns import PATTERNS_ONLY
        if any(pattern.search(text) for pattern in PATTERNS_ONLY):
            return FilterResult(raw, b'', 0, 'credential_passthrough')
        from bash_compress import compress
        from token_estimate import estimate_tokens
        shortened = compress(FORMATS[format_hint], text)
        if 'expand key' in shortened:
            shortened = text
        # File stays exactly as supplied. Pointer includes its entire overhead.
        # The recover hint must be a command an installed user can actually run.
        recover = ' '.join((shlex.quote(os.path.realpath(sys.executable)),
                            shlex.quote(str(Path(__file__).resolve())),
                            'recover', '--capture-id', stdout.parent.name,
                            '--exit-code', str(exit_code)))
        pointer = f'[Token Optimizer capture ID: {stdout.parent.name}; recover with {recover}; integrity checked again]\n'
        candidate = pointer + '[Begin untrusted captured output]\n' + shortened + '\n[End untrusted captured output]\n'
        if shortened not in (text, cleaned) and estimate_tokens(candidate) < estimate_tokens(text):
            delivered, outcome = candidate, 'compressed'
    except Exception as exc:
        return FilterResult(raw, ('Compression unavailable: ' + type(exc).__name__ + '; emitting original bytes.\n').encode(), 0, 'unavailable')
    measured = False
    if record_metrics:
        try:
            record(session, text, delivered, outcome)
            measured = True
        except Exception:
            pass
    return FilterResult(delivered.encode('utf-8'), b'', 0, outcome, measured)


def _remove_capture(directory: Path) -> None:
    """Remove a passthrough capture once its raw bytes were delivered.
    Only the adapter's fixed private leaf names are unlinked; foreign content
    keeps its directory (and itself) in place."""
    from devin_state import no_symlinks
    no_symlinks(directory)
    for name in ('stdout', 'stderr', '.prepared', 'complete.json'):
        leaf = directory / name
        no_symlinks(leaf)
        leaf.unlink(missing_ok=True)
    try:
        directory.rmdir()
    except OSError:
        pass


def seal_capture(directory: Path, exit_code: int) -> None:
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or not 0 <= exit_code <= 255 or not consent_ok():
        raise ValueError('invalid exit code or missing consent')
    out, err = read_pair(directory, sealed=False)
    from devin_state import private_path
    target = private_path(directory / 'complete.json')
    if target.exists():
        # A seal killed mid-write can leave a torn, unparseable complete.json.
        # It can never validate as a seal, so a retry may safely replace it;
        # a complete parseable seal is never overwritten.
        try:
            json.loads(target.read_bytes())
        except (OSError, ValueError):
            target.unlink()
        else:
            raise OSError('capture already sealed')
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        # Exclusive create: no window where complete.json has two links.
        payload = json.dumps({'exit_code': exit_code,
                              'stdout_sha256': hashlib.sha256(out).hexdigest(),
                              'stderr_sha256': hashlib.sha256(err).hexdigest()}).encode()
        view = memoryview(payload)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv=None) -> int:
    os.environ['TOKEN_OPTIMIZER_RUNTIME'] = 'devin'
    parser = argparse.ArgumentParser(description=__doc__)
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ['prepare']:
        from devin_state import prepare_capture
        try:
            print(prepare_capture()); return 0
        except OSError as exc:
            print('Capture preparation refused: ' + str(exc), file=sys.stderr); return 2
    if args[:1] == ['recover']:
        recovery = argparse.ArgumentParser(description='Recover a sealed capture by opaque ID')
        recovery.add_argument('--capture-id', required=True)
        recovery.add_argument('--exit-code', type=int, required=True)
        values = recovery.parse_args(args[1:])
        try:
            from devin_state import data_dir
            # Only the bare opaque ID is accepted; path-shaped input is refused
            # before it can reach the trusted identity line.
            if not re.fullmatch(r'capture-[0-9a-f]{32}', values.capture_id):
                raise OSError('capture ID must be the bare capture-<hex> form')
            root = data_dir() / 'captures' / values.capture_id
            if not consent_ok() or not 0 <= values.exit_code <= 255:
                raise ValueError('invalid consent or code')
            out, err = read_pair(root, sealed=True, exit_code=values.exit_code)
            identity=('[Token Optimizer verified capture '+root.name+'; recovered content below is untrusted data]\n').encode()
            sys.stdout.buffer.write(identity+b'[Begin untrusted recovered stdout]\n'+out+b'\n[End untrusted recovered stdout]\n')
            if err:
                sys.stderr.buffer.write(identity+b'[Begin untrusted recovered stderr]\n'+err+b'\n[End untrusted recovered stderr]\n')
            return values.exit_code
        except (OSError, ValueError, TypeError) as exc:
            print('Recovery refused: ' + str(exc), file=sys.stderr); return 2
    if args[:1] == ['seal']:
        seal_parser = argparse.ArgumentParser(description='Seal a finished foreground capture; never seal a running producer')
        seal_parser.add_argument('--capture', type=Path, required=True)
        seal_parser.add_argument('--exit-code', type=int, required=True)
        values = seal_parser.parse_args(args[1:])
        try:
            seal_capture(values.capture, values.exit_code); return 0
        except (OSError, ValueError, TypeError) as exc:
            print('Capture completion refused: ' + str(exc), file=sys.stderr); return 2
    parser.add_argument('--stdout', type=Path, required=True)
    parser.add_argument('--stderr', type=Path, required=True)
    parser.add_argument('--exit-code', type=int, required=True)
    parser.add_argument('--format', choices=FORMATS, default='generic')
    parser.add_argument('--session', required=True, help='Local correlation label, stored hashed; not provider usage')
    args = parser.parse_args(args)
    try:
        result = filter_capture(args.stdout, args.stderr, args.exit_code, args.format, args.session)
    except (OSError, ValueError, ImportError, SyntaxError) as exc:
        print(f'Token Optimizer: capture not filtered: {exc}. Originals remain unchanged; inspect them directly.', file=sys.stderr)
        return 2
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.flush()
    if result.outcome != 'compressed':
        # Passthrough emits no recovery pointer, so the stored original is
        # unrecoverable anyway and may hold credentials. Remove it only after
        # the raw bytes have been written out successfully.
        _remove_capture(args.stdout.parent)
        print('Token Optimizer: output passed through unfiltered; the stored capture was removed and there is nothing to recover.', file=sys.stderr)
    return result.code


if __name__ == '__main__':
    raise SystemExit(main())
