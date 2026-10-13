from pathlib import Path
import re
import sys
import os
import pytest
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Native Windows unsupported by production design (installer refuses os.name != posix); tests gated accordingly.')

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/token-optimizer/scripts'
sys.path.insert(0, str(SCRIPTS))

def test_filter_never_executes_original(tmp_path, monkeypatch):
    import devin_compress as d
    import devin_state as state
    monkeypatch.setenv('HOME',str(tmp_path))
    state.enable_consent()
    capture=state.prepare_capture()
    out=capture/'stdout'; err=capture/'stderr'
    out.write_bytes(b'repeated log line\n'*1000); err.write_bytes(b'')
    out.chmod(0o600); err.chmod(0o600)
    state.enable_consent()
    d.seal_capture(capture,0)
    result=d.filter_capture(out,err,0,'generic','session-a')
    assert result.code == 0
    assert result.stderr == b''
    assert len(result.stdout) < out.stat().st_size
    assert out.parent.name.encode() in result.stdout
    assert out.read_bytes() == b'repeated log line\n'*1000

import pytest

def test_arbitrary_private_file_refused(tmp_path, monkeypatch):
    import devin_compress as d
    import devin_state as state
    monkeypatch.setenv('HOME', str(tmp_path)); state.enable_consent()
    out=tmp_path/'secret'; err=tmp_path/'stderr'
    out.write_text('private');err.write_text('');out.chmod(0o600);err.chmod(0o600)
    with pytest.raises(OSError):
        d.filter_capture(out,err,0,'generic','session-a')

@pytest.mark.parametrize('code,errors',[(1,b''),(0,b'warning\n'),(137,b'killed\n')])
def test_raw_failure_and_stderr(tmp_path,monkeypatch,code,errors):
    import devin_compress as d
    import devin_state as s
    monkeypatch.setenv('HOME',str(tmp_path));s.enable_consent();root=s.prepare_capture()
    out,err=root/'stdout',root/'stderr';raw=b'x\n'*1000
    out.write_bytes(raw);err.write_bytes(errors);d.seal_capture(root,code)
    value=d.filter_capture(out,err,code,'generic','session')
    assert (value.stdout,value.stderr,value.code)==(raw,errors,code)

@pytest.mark.parametrize('raw',[b'a\x00b\n'*1000,b'\xff'*1000,b'github_pat_'+b'A'*40+b'\n'*1000])
def test_binary_and_credentials_passthrough(tmp_path,monkeypatch,raw):
    import devin_compress as d
    import devin_state as s
    monkeypatch.setenv('HOME',str(tmp_path));s.enable_consent();root=s.prepare_capture()
    (root/'stdout').write_bytes(raw);d.seal_capture(root,0)
    value=d.filter_capture(root/'stdout',root/'stderr',0,'generic','session')
    assert value.stdout==raw

@pytest.mark.parametrize('change',['append','truncate','replace'])
def test_changed_completed_capture_refused(tmp_path,monkeypatch,change):
    import devin_compress as d
    import devin_state as s
    monkeypatch.setenv('HOME',str(tmp_path));s.enable_consent();root=s.prepare_capture()
    (root/'stdout').write_text('text\n'*1000);d.seal_capture(root,0)
    if change=='append':
        with (root/'stdout').open('a') as f:f.write('later')
    elif change=='truncate': (root/'stdout').write_text('short')
    else:
        (root/'stdout').unlink();(root/'stdout').write_text('new');(root/'stdout').chmod(0o600)
    with pytest.raises(OSError):d.filter_capture(root/'stdout',root/'stderr',0,'generic','session')

@pytest.mark.parametrize('attack',['symlink','fifo','hardlink','permissions','oversize','unsealed'])
def test_capture_attacks_refused(tmp_path,monkeypatch,attack):
    import os
    import devin_compress as d
    import devin_state as s
    monkeypatch.setenv('HOME',str(tmp_path));s.enable_consent();root=s.prepare_capture();out=root/'stdout'
    if attack!='unsealed':d.seal_capture(root,0)
    if attack=='symlink':out.unlink();out.symlink_to(tmp_path/'secret')
    elif attack=='fifo':out.unlink();os.mkfifo(out)
    elif attack=='hardlink':os.link(out,root/'other')
    elif attack=='permissions':out.chmod(0o644)
    elif attack=='oversize':
        with out.open('wb') as f:f.truncate(d.MAX_CAPTURE+1)
    with pytest.raises((OSError,ValueError)):d.filter_capture(out,root/'stderr',0,'generic','session')

@pytest.mark.parametrize('code',[-1,256,True,'0'])
def test_bad_exit_codes_refused(tmp_path,monkeypatch,code):
    import devin_compress as d
    import devin_state as s
    monkeypatch.setenv('HOME',str(tmp_path));s.enable_consent();root=s.prepare_capture()
    with pytest.raises(ValueError):d.filter_capture(root/'stdout',root/'stderr',code,'generic','s')

@pytest.mark.parametrize('session_number',range(48))
def test_real_cli_sessions(tmp_path,monkeypatch,session_number):
    import subprocess
    import devin_compress as d
    import devin_state as s
    monkeypatch.setenv('HOME',str(tmp_path));s.enable_consent();root=s.prepare_capture()
    raw=('session-%d repeated output line\n'%session_number).encode()*1500
    (root/'stdout').write_bytes(raw);d.seal_capture(root,0)
    run=subprocess.run([sys.executable,str(SCRIPTS/'devin_compress.py'),
        '--stdout',str(root/'stdout'),'--stderr',str(root/'stderr'),'--exit-code','0',
        '--format','generic','--session','synthetic-'+str(session_number)],capture_output=True)
    assert run.returncode==0 and not run.stderr
    assert len(run.stdout)<len(raw) and root.name.encode() in run.stdout
    assert (root/'stdout').read_bytes()==raw


def _filtered(tmp_path, monkeypatch, fmt, raw, code=0, err=b''):
    import devin_compress as d
    import devin_state as s
    monkeypatch.setenv('HOME', str(tmp_path))
    s.enable_consent()
    root = s.prepare_capture()
    (root / 'stdout').write_bytes(raw)
    (root / 'stderr').write_bytes(err)
    d.seal_capture(root, code)
    return d.filter_capture(root / 'stdout', root / 'stderr', code, fmt, 'session')


def _git_log_sample():
    parts = []
    for i in range(60):
        parts += ['commit %040x' % i,
                  'Author: A U Thor <author@example.com>',
                  'Date:   Tue Oct 13 10:00:00 2026 +0300', '',
                  '    fix(module-%d): change behavior %d' % (i, i), '']
        parts += [' src/pkg/mod_%d/file_%d.py | %d +++---' % (i, f, 10 + f) for f in range(4)]
        parts += [' 4 files changed, 40 insertions(+), 12 deletions(-)', '']
    return '\n'.join(parts).encode()


def _git_status_sample():
    lines = ['On branch main', 'Changes not staged for commit:',
             '  (use "git add <file>..." to update what will be committed)', '']
    lines += ['\tmodified:   src/deep/path/component_%04d.py' % i for i in range(900)]
    lines += ['', 'Untracked files:', '']
    lines += ['\tnew_file_%04d.txt' % i for i in range(300)]
    return '\n'.join(lines).encode()


def _listing_sample():
    return '\n'.join('-rw-r--r--  1 user  staff    %5d Oct 13 10:00 file_%04d.txt'
                     % (1000 + i, i) for i in range(1500)).encode()


def _search_sample():
    return '\n'.join('src/pkg/module_%03d/file_%04d.py:42:        match_target call here'
                     % (i % 97, i) for i in range(1200)).encode()


def _tests_sample():
    lines = ['tests/test_mod_%03d.py::test_case_%04d PASSED' % (i % 31, i) for i in range(1200)]
    lines += ['', '==================== 1200 passed in 42.10s ====================']
    return '\n'.join(lines).encode()


def _build_sample():
    return '\n'.join('src/mod_%03d/file_%04d.ts(%d,%d): error TS2339: Property x does not exist on type Y.'
                     % (i % 41, i, i % 50, i % 80) for i in range(400)).encode()


_FORMAT_SAMPLES = {
    'generic': b'worker-7 processed item number 42 successfully\n' * 1000,
    'git-status': _git_status_sample(),
    'git-log': _git_log_sample(),
    'listing': _listing_sample(),
    'search': _search_sample(),
    'tests': _tests_sample(),
    'build': _build_sample(),
}


def _multibyte_sample(fmt):
    """CJK mixed with Latin in each domain's shape. A CJK character is ~3
    bytes and ~1 token, so a comparison in characters misjudges cost."""
    if fmt == 'search':
        # A search handler emits its few kept match lines verbatim: with
        # CJK-dense matches it produces FEWER characters than generic yet
        # MORE delivered bytes. The byte comparison must catch that.
        cjk = lambda seed: ''.join(
            chr(0x4E00 + (seed * 37 + i * 11) % 0x5000) for i in range(40))
        parts = []
        for i in range(60):
            parts += [' src/f%d.py | %d ++- %s' % (i, i % 7, 'x' * 45),
                      '    fix: thing %d %s' % (i, 'y' * 45), 'z' * 65]
        for f in range(5):
            parts += ['src/m%d.py:%d: %s' % (f, f * 20 + n, cjk(f * 20 + n))
                      for n in range(20)]
        for i in range(500):
            parts += [' src/g%d.py | %d ++- %s' % (i, i % 7, 'x' * 45),
                      'z' * 65]
        return '\n'.join(parts).encode('utf-8')
    cjk = '壱弐参四五六七八九十字符列です丂丄丅丆丏丒丟丠両丣並丩丮丯'
    parts = []
    for i in range(1200):
        if fmt == 'git-status':
            parts.append('\tmodified:   src/pkg/コンポーネント_%04d.py' % i)
        elif fmt == 'git-log':
            parts += ['commit %040x' % i, 'Author: A U Thor <author@example.com>',
                      '    fix(モジュール): %s item %d' % (cjk, i), '']
        elif fmt == 'listing':
            parts.append('-rw-r--r--  1 user  staff  %5d Oct 13 10:00 ファイル_%04d.txt' % (1000 + i, i))
        elif fmt == 'tests':
            parts.append('tests/test_モジュール_%03d.py::test_ケース_%04d PASSED' % (i % 31, i))
        elif fmt == 'build':
            parts.append('src/mod_%03d/ファイル_%04d.ts(%d,%d): error TS2339: プロパティ x が型 Y に存在しません'
                         % (i % 41, i, i % 50, i % 80))
        else:
            parts.append('worker-7 %s processed item number %d successfully' % (cjk, i))
    return '\n'.join(parts).encode('utf-8')


_FORMAT_CASES = ([(fmt, 'ascii', raw) for fmt, raw in _FORMAT_SAMPLES.items()]
                 + [(fmt, 'multibyte', _multibyte_sample(fmt)) for fmt in sorted(_FORMAT_SAMPLES)])


@pytest.mark.parametrize('fmt,label,raw', _FORMAT_CASES,
                         ids=['%s-%s' % (f, l) for f, l, _ in _FORMAT_CASES])
def test_named_format_never_worse_than_generic(tmp_path, monkeypatch, fmt, label, raw):
    """A named format must never do worse than generic on the same bytes.
    The bound is what is delivered: encoded bytes, not characters."""
    generic = _filtered(tmp_path, monkeypatch, 'generic', raw)
    named = _filtered(tmp_path, monkeypatch, fmt, raw)
    assert len(named.stdout) <= len(generic.stdout), (fmt, len(named.stdout), len(generic.stdout))


_WRONG_DOMAIN = {
    'git-status': 'search', 'git-log': 'search', 'listing': 'search',
    'tests': 'search', 'build': 'search', 'search': 'tests',
}


@pytest.mark.parametrize('fmt', sorted(_WRONG_DOMAIN))
def test_wrong_hint_delivers_generic_result(tmp_path, monkeypatch, fmt):
    """A named hint that finds no structure for its format must not emit a
    near-empty body: it delivers what the generic handler produced."""
    raw = _FORMAT_SAMPLES[_WRONG_DOMAIN[fmt]]
    named = _filtered(tmp_path, monkeypatch, fmt, raw)
    generic = _filtered(tmp_path, monkeypatch, 'generic', raw)
    strip_id = lambda body: re.sub(rb'capture-[0-9a-f]{32}', b'capture-X', body)
    assert strip_id(named.stdout) == strip_id(generic.stdout), (fmt, len(named.stdout), len(generic.stdout))


@pytest.mark.parametrize('fmt', sorted(_FORMAT_SAMPLES))
@pytest.mark.parametrize('raw,code', [
    (b'github_pat_' + b'A' * 40 + b'\n' + b'x\n' * 1000, 0),   # credential
    (b'\xff' * 1000, 0),                                        # binary
    (b'hello\n', 0),                                            # tiny
    (b'x\n' * 1000, 3),                                         # failure
])
def test_named_format_passthrough_battery(tmp_path, monkeypatch, fmt, raw, code):
    """Credential, binary, tiny and failing captures pass through raw under
    every format hint."""
    value = _filtered(tmp_path, monkeypatch, fmt, raw, code=code)
    assert value.stdout == raw and value.code == code


_SGR = b'\x1b[1;31m'
_SGR_END = b'\x1b[m'
_COLOUR_SPLIT_SECRETS = [
    b'GITHUB_TOKEN=ghp_' + _SGR + b'abcdefghijklmnopqrstuvwxyz0123456789' + _SGR_END,
    b'AWS_SECRET_ACCESS_KEY=AKIA' + _SGR + b'IOSFODNN7EXAMPLE' + _SGR_END,
    b'key=sk-ant-' + _SGR + b'notarealkeynotarealkeynotareal' + _SGR_END,
]


@pytest.mark.parametrize('fmt', sorted(_FORMAT_SAMPLES))
@pytest.mark.parametrize('secret', _COLOUR_SPLIT_SECRETS)
def test_colour_split_credential_passes_raw(tmp_path, monkeypatch, fmt, secret):
    """grep --color=always puts SGR escapes inside a matched token. The
    credential gate must see the escape-stripped text too, or the joined
    token is compressed and stored instead of passed through raw."""
    raw = b'x\n' * 500 + secret + b'\n' + b'y\n' * 500
    value = _filtered(tmp_path, monkeypatch, fmt, raw)
    assert value.stdout == raw and value.code == 0
    assert value.outcome == 'credential_passthrough'


def test_capture_leaf_as_directory_names_leaf(tmp_path, monkeypatch):
    """A capture leaf that is a directory must refuse by name; a bare
    '[Errno 21] Is a directory: <fd>' leaks a file descriptor number."""
    import devin_compress as d
    import devin_state as s
    monkeypatch.setenv('HOME', str(tmp_path))
    s.enable_consent()
    root = s.prepare_capture()
    (root / 'stdout').unlink()
    (root / 'stdout').mkdir()
    with pytest.raises(OSError) as info:
        d.read_pair(root, sealed=False)
    assert 'stdout' in str(info.value) and 'Is a directory' not in str(info.value)
