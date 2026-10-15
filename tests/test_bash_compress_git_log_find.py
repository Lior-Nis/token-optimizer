"""git log and find summarizers (5.13.46): savings, exact preservation, passthrough."""
import hashlib
import os
import re
import shlex
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/token-optimizer/scripts'
sys.path.insert(0, str(SCRIPTS))

import bash_compress as bc  # noqa: E402
from token_estimate import estimate_tokens  # noqa: E402


def _saving(raw, out):
    return 1.0 - estimate_tokens(out) / estimate_tokens(raw)


def _sha(i):
    return hashlib.sha1(('commit-%d' % i).encode()).hexdigest()


AUTHORS = ['Ada Lovelace', 'Grace Hopper', 'Linus T']


def _commit(i, subject=None, body=True, merge=False, deco=None, author=None, date=None):
    h = _sha(i)
    a = author or AUTHORS[i % 3]
    lines = ['commit ' + h + (' (%s)' % deco if deco else '')]
    if merge:
        lines.append('Merge: %s %s' % (h[:8], _sha(i + 1000)[:8]))
    lines += ['Author: %s <%s@users.noreply.example.com>' % (a, a.split()[0].lower()),
              'Date:   ' + (date or 'Tue Oct 13 10:%02d:00 2026 +0300' % (i % 60)), '',
              '    ' + (subject if subject is not None else 'fix(module-%d): change behavior %d' % (i, i))]
    if body:
        lines += ['    ',
                  '    Why change %d was needed, wrapped over' % i,
                  '    two lines of prose.',
                  '    ',
                  '    Co-authored-by: Pair Person <pair@example.com>',
                  '    Signed-off-by: %s <x@example.com>' % a]
    lines.append('')
    return lines


def _git_log(n=60, **kw):
    out = []
    for i in range(n):
        out += _commit(i, merge=(i % 7 == 0), deco=('HEAD -> main, tag: v1.0' if i == 0 else None), **kw)
    return '\n'.join(out)


def _expected_git_line(i):
    flags = []
    if i % 7 == 0:
        flags.append('merge')
    flags += ['+2 body lines', '+2 trailers', 'co-authors: Pair Person']
    deco = ' (HEAD -> main, tag: v1.0)' if i == 0 else ''
    return '%s 2026-10-13 10:%02d +0300 %s | fix(module-%d): change behavior %d%s [%s]' % (
        _sha(i)[:10], i % 60, AUTHORS[i % 3], i, i, deco, '; '.join(flags))


# ---------------------------------------------------------------- git log

def test_git_log_exact_pairing_and_savings():
    raw = _git_log(60)
    out = bc.compress('git log -60', raw)
    lines = out.splitlines()
    assert lines[0].startswith('[git log: 60 commits')
    assert lines[1:] == [_expected_git_line(i) for i in range(60)]
    assert _saving(raw, out) >= 0.40
    assert 'credential-bearing' not in out and 'commit ' + _sha(0) not in out
    assert len({line[:10] for line in lines[1:]}) == 60   # distinct hashes


def test_git_log_header_explains_brackets_and_omissions():
    head = bc.compress('git log', _git_log(6)).splitlines()[0]
    assert 'Text in [ ]' in head and 'text in ( )' in head and 'recoverable' in head


def test_git_log_singular_forms():
    raw = _git_log(6, body=False).replace('\n\ncommit ', '\n\n    one body line\n\ncommit ', 0)
    parts = []
    for i in range(6):
        c = _commit(i, body=False)
        c[-1:-1] = ['    ', '    only body line']
        parts += c
    out = bc._compress_git_log('\n'.join(parts))
    assert out.splitlines()[1].endswith('[+1 body line]')
    assert '6 commits' in out.splitlines()[0]


def test_git_log_pipe_in_subject_non_ascii_and_wrapped_subject():
    parts = []
    parts += _commit(1, subject='fix a | b', body=False)
    parts += _commit(2, subject='修复 バグ 🐛 ünïcode', author='山田 太郎', body=False)
    wrapped = _commit(3, body=False)
    wrapped.insert(-1, '    continuation of the subject')
    parts += wrapped
    for i in (4, 5, 6):
        parts += _commit(i, body=False)
    out = bc._compress_git_log('\n'.join(parts)).splitlines()
    assert out[1].endswith('Ada Lovelace | fix a | b') is False  # author of i=1 is Grace
    assert out[1] == '%s 2026-10-13 10:01 +0300 Grace Hopper | fix a | b' % _sha(1)[:10]
    assert out[2] == '%s 2026-10-13 10:02 +0300 山田 太郎 | 修复 バグ 🐛 ünïcode' % _sha(2)[:10]
    assert out[3].endswith('| fix(module-3): change behavior 3 continuation of the subject')


@pytest.mark.parametrize('cut', ['after_commit', 'after_author', 'after_date'])
def test_git_log_truncated_stream_marks_last_commit(cut):
    full = _git_log(8)
    lines = full.split('\n')
    start = len(lines) - len(_commit(7)) + 0
    last = [i for i, l in enumerate(lines) if l.startswith('commit ')][-1]
    keep = {'after_commit': last + 1, 'after_author': last + 2, 'after_date': last + 3,
            'after_blank': last + 4}[cut]
    raw = '\n'.join(lines[:keep]) + '\n'
    out = bc._compress_git_log(raw).splitlines()
    assert len(out) == 1 + 8
    assert out[-1].startswith(_sha(7)[:10] + ' [truncated')
    assert '(no message)' not in '\n'.join(out)
    assert [l[:10] for l in out[1:]] == [_sha(i)[:10] for i in range(8)]


def test_git_log_empty_message_last_commit_is_not_truncated():
    parts = []
    for i in range(6):
        parts += _commit(i, body=False)
    empty = ['commit ' + _sha(99), 'Author: Ann Dev <ann@x.com>',
             'Date:   Thu Oct 15 14:47:31 2026 +0300', '    ']
    for tail in ('\n'.join(parts + empty) + '\n', '\n'.join(parts + empty[:3] + ['']) + '\n'):
        out = bc._compress_git_log(tail).splitlines()
        assert out[-1] == '%s 2026-10-15 14:47 +0300 Ann Dev | (no message)' % _sha(99)[:10]
        assert 'truncated' not in '\n'.join(out)
        assert len(out) == 1 + 7
    cut = '\n'.join(parts + empty[:3]) + '\n'          # stream ends inside the headers
    assert bc._compress_git_log(cut).splitlines()[-1].startswith(_sha(99)[:10] + ' [truncated')


def test_git_log_small_and_foreign_untouched():
    for raw in (_git_log(3), 'some other text\n' * 50, ''):
        assert bc._compress_git_log(raw) == raw


@pytest.mark.parametrize('sep', [' ', '\x0b', '\x0c', '\x1c', '\x85', ' ', '\r'])
def test_git_log_ambiguous_separator_returns_raw(sep):
    raw = _git_log(8, subject='subject' + sep + 'tail')
    assert bc._compress_git_log(raw) == raw


GIT_FLAGS = ['--oneline', '--format=%H %s', '--pretty=format:%h', '--pretty=fuller', '-z',
             '--porcelain', '--name-status', '--name-only', '--numstat', '--stat', '--shortstat',
             '--graph', '-p', '--patch', '-60p', '--show-signature', '--raw', '--summary',
             '-L1,5:README.md']


@pytest.mark.parametrize('flag', GIT_FLAGS)
def test_git_log_machine_formats_untouched_through_compress(flag):
    sig = ['gpg: Signature made Tue Oct 13 10:00:00 2026', 'gpg: BAD signature from "X"']
    raw = _git_log(60).replace('Author:', sig[1] + '\nAuthor:', 1) + '\n'
    assert bc._detect_pattern('git log ' + flag) == 'git_log_basic'
    assert bc.compress('git log ' + flag, raw) == raw
    assert 'BAD signature' in bc.compress('git log ' + flag, raw)


def test_light_cleanup_handler_is_identity_for_signatures_and_merge_lines():
    raw = 'gpg: BAD signature from "X"\nMerge: a b\nPrimary key fingerprint: AB\n' + _git_log(6)
    assert bc._compress_git_log_basic(raw) == raw
    assert bc._compress_git_log(raw) == raw


def test_git_log_default_signature_lines_not_lost():
    raw = _git_log(60).replace('Author:', 'gpg: BAD signature from "X"\nAuthor:', 1) + '\n'
    assert bc.compress('git log -60', raw) == raw


def test_git_log_credential_in_body_still_reinjected_no_commit_hash_junk():
    token = 'ghp_' + 'a1B2c3D4' * 4 + 'wxyz'
    parts = []
    for i in range(8):
        parts += _commit(i, body=(i == 3))
    raw = '\n'.join(parts).replace('Why change 3 was needed, wrapped over',
                                   'token ' + token)
    out = bc.compress('git log', raw)
    assert token in out
    assert not re.search(r'(?m)^commit [0-9a-f]{40}', out)


# ---------------------------------------------------------------- routing / pipelines

@pytest.mark.parametrize('cmd,expected', [
    ('git log -60', 'git_log'),
    ('git log -60 | head -20', 'git_log'),
    ('git log | sort | head', 'git_log'),
    ('git log | cut -c1-20', 'git_log_basic'),
    ('git log | awk "{print $1}"', 'git_log_basic'),
    ('find . -type f | head -50', 'find'),
    ('find . -type f | sort | tail -n 20', 'find'),
    ('find . -type f | cut -d/ -f2 | sort | uniq -c | sort -rn', 'find_raw'),
    ('find . -type f | xargs ls', 'find_raw'),
    ('find . | sed s/a/b/', 'find_raw'),
    ('find . | uniq -c', 'find_raw'),
])
def test_pipeline_routing(cmd, expected):
    assert bc._detect_pattern(cmd) == expected


@pytest.mark.parametrize('cmd,expected', [
    ("find . -type f |& sed 's/$/ (x)/'", 'find_raw'),
    ('find . -type f|sed s/a/b/', 'find_raw'),
    ('find . -type f |& cut -d/ -f2', 'find_raw'),
    ('find . -type f|head -5', 'find'),
    ('find . -type f |& head -5', 'find'),
    ('git log|cut -c1-5', 'git_log_basic'),
    ('git log |& awk 1', 'git_log_basic'),
    ("find . -name 'a|b'", 'find'),
])
def test_stderr_pipe_and_glued_pipe_are_pipes(cmd, expected):
    assert bc._detect_pattern(cmd) == expected


def test_sed_modified_paths_through_stderr_pipe_stay_raw():
    raw = ''.join('./dir%d/f%d.py (x)\n' % (i % 5, i) for i in range(80))
    for cmd in ("find . -type f |& sed 's/$/ (x)/'", "find . -type f|sed 's/$/ (x)/'"):
        assert bc.compress(cmd, raw) == raw


def test_uniq_c_pipeline_output_not_treated_as_path_list():
    raw = ''.join('%7d %s\n' % (100 - i, 'dir%d' % i) for i in range(80))
    cmd = 'find . -type f | cut -d/ -f2 | sort | uniq -c | sort -rn'
    assert bc.compress(cmd, raw) == raw
    assert bc._compress_find(raw) == raw
    assert bc._compress_find(raw, 'find . -type f') == raw


# ---------------------------------------------------------------- find

def _find_paths():
    paths = ['./README.md', './setup.py', './LICENSE', './my file (1).txt']
    paths += ['./src/pkg/mod_%02d.py' % i for i in range(45)]
    paths += ['./src/pkg/sub/a.py', './src/pkg/sub/b.py']
    paths += ['./node_modules/lib%d/index.js' % i for i in range(150)]
    paths += ['./.git/objects/%s/%s' % (_sha(i)[:2], _sha(i)[2:]) for i in range(120)]
    paths += ['./tests/test_%d.py' % i for i in range(6)]
    paths += ['./docs/ünï/日本.md']
    return paths


def _expand(out):
    """Independent reader: (explicit path set, collapsed-count)."""
    listed, collapsed = [], 0
    for ln in out.splitlines()[1:]:
        if ln.startswith('* '):
            m = re.match(r"^\* (?:'(?:[^']|'\"'\"')*'|\S+) \((\d+) ", ln)
            collapsed += int(m.group(1))
        elif ln.startswith('= '):
            listed += shlex.split(ln[2:])
        else:
            tok = shlex.split(ln)
            listed += [tok[0] + (n[:-1] if n.endswith('/') else n) for n in tok[1:]]
    return listed, collapsed


def test_find_saves_states_total_and_accounts_for_every_path():
    paths = _find_paths()
    raw = '\n'.join(paths) + '\n'
    out = bc.compress('find . -type f', raw)
    assert _saving(raw, out) >= 0.50
    head = out.splitlines()[0]
    assert '%d paths total' % len(paths) in head
    assert 'not every entry' in head and 'recoverable' in head
    listed, collapsed = _expand(out)
    assert len(listed) == len(set(listed)) and set(listed) <= set(paths)
    assert len(listed) + collapsed == len(paths)
    assert './my file (1).txt' in listed and './docs/ünï/日本.md' in listed
    assert './src/pkg/sub/a.py' in listed and './tests/test_0.py' in listed
    assert not any(p.startswith('./node_modules/') for p in listed)
    node = [l for l in out.splitlines() if l.startswith('* ./node_modules/')][0]
    assert '(150 paths collapsed: *.js x150)' in node and 'lib0/index.js' in node
    assert '* ./src/pkg/ (45 entries: *.py x45) e.g. mod_00.py mod_01.py mod_02.py' in out


def test_find_small_output_passthrough():
    raw = '\n'.join('./f%d.txt' % i for i in range(20)) + '\n'
    assert bc.compress('find . -type f', raw) == raw


def test_find_root_dot_and_slash_roots_are_labelled_truthfully():
    raw = '.\n' + '\n'.join(_find_paths()) + '\n'
    out = bc._compress_find(raw, 'find .')
    assert '= .' in out.splitlines() and './ .' not in out and './ ./' not in out
    listed, collapsed = _expand(out)
    assert len(listed) + collapsed == len(raw.splitlines())

    abs_paths = ['/usr', '/etc', '/var', '/usr/bin/tool%d' % 0] + \
                ['/usr/bin/tool%d' % i for i in range(1, 50)] + ['/etc/hosts', '/var/log/syslog']
    raw = '\n'.join(abs_paths) + '\n'
    out = bc._compress_find(raw, 'find / -maxdepth 4')
    assert '/ usr/ etc/ var/' in out or '/ etc/ usr/ var/' in out or re.search(r'(?m)^/ ', out)
    assert not re.search(r'(?m)^\./ ', out)
    listed, collapsed = _expand(out)
    assert set(listed) <= set(abs_paths) and len(listed) + collapsed == len(abs_paths)
    assert '/usr' not in [l for l in listed if l.startswith('/usr/bin')]
    assert '/etc/hosts' in listed


def test_find_noise_directory_as_root_is_never_collapsed():
    paths = ['node_modules/lodash/%s/f%d.js' % (d, i) for d in ('fp', 'internal') for i in range(30)]
    out = bc.compress('find node_modules/lodash -name "*.js"', '\n'.join(paths) + '\n')
    assert 'paths collapsed' not in out
    listed, collapsed = _expand(out)
    assert collapsed == 0 and sorted(listed) == sorted(paths)

    venv = '/home/u/proj/.venv'
    paths = ['%s/lib/site-packages/pkg%d/mod%d.py' % (venv, i % 5, i) for i in range(60)]
    out = bc.compress('find %s -type f' % venv, '\n'.join(paths) + '\n')
    assert 'paths collapsed' not in out
    listed, collapsed = _expand(out)
    assert len(listed) + collapsed == 60 and 'mod0.py' in out


def test_find_search_hits_inside_noise_dirs_keep_their_names():
    paths = ['./.venv/lib/python3.11/site-packages/p%d/m%d.py' % (i % 6, i) for i in range(60)]
    paths += ['./app/main%d.py' % i for i in range(5)]
    out = bc.compress("find . -name '*.py' -path '*lib*'", '\n'.join(paths) + '\n')
    assert 'paths collapsed' not in out
    listed, _ = _expand(out)
    assert sorted(listed) == sorted(paths)


def test_find_empty_breakdown_and_noise_only_parent_keep_slash_and_header_explains_root_line():
    paths = ['.', './d/a', './d/a/.git', './d/b', './d/b/.git/x', './d/b/.git/y'] + \
            ['./f%d.txt' % i for i in range(45)]
    out = bc._compress_find('\n'.join(paths) + '\n', 'find .')
    assert '(1 path collapsed)' in out and ': )' not in out
    assert './d/ a/ b/' in out.splitlines()
    head = out.splitlines()[0]
    assert 'A line starting with = lists top-level paths' in head
    listed, collapsed = _expand(out)
    assert len(listed) + collapsed == len(paths)


def test_find_collapsed_group_always_shows_example_names_and_grammar():
    paths = ['./f%d.txt' % i for i in range(45)] + ['./__pycache__/x.pyc']
    out = bc._compress_find('\n'.join(paths) + '\n', 'find . -type f')
    assert '* ./__pycache__/ (1 path collapsed: *.pyc x1) e.g. x.pyc' in out
    assert '1 paths' not in out


@pytest.mark.parametrize('bad', [
    "find: './secret': Permission denied",
    "find: './gone': No such file or directory",
])
def test_find_error_lines_return_raw(bad):
    raw = '\n'.join(_find_paths()[:60]) + '\n' + bad + '\n'
    assert bc._compress_find(raw, 'find . -type f') == raw
    assert bc.compress('find . -type f 2>&1', raw) == raw


def test_find_newline_in_name_and_ambiguous_separators_return_raw():
    paths = ['./a%d.py' % i for i in range(60)] + ['./bad\nname.py']
    raw = '\n'.join(paths) + '\n'
    assert bc._compress_find(raw, 'find . -type f') == raw
    for sep in (' ', '\x0b', '\x85', '\r', '\t'):
        raw = '\n'.join(['./a%d.py' % i for i in range(60)] + ['./x' + sep + 'y.py']) + '\n'
        assert bc._compress_find(raw, 'find . -type f') == raw


def test_find_doubled_or_trailing_slash_and_duplicates_return_raw():
    base = ['./a%d.py' % i for i in range(60)]
    for extra in ('./dir/', './x//y.py', './a1.py'):
        raw = '\n'.join(base + [extra]) + '\n'
        assert bc._compress_find(raw, 'find . -type f') == raw


def test_find_lines_outside_the_roots_return_raw():
    raw = '\n'.join(['./a%d.py' % i for i in range(60)] + ['b.py']) + '\n'
    assert bc._compress_find(raw, 'find . -type f') == raw


def test_find_size_cap_returns_raw():
    raw = '\n'.join('./d%d/f%d.py' % (i % 50, i) for i in range(bc._FIND_MAX_PATHS + 1)) + '\n'
    assert bc._compress_find(raw, 'find . -type f') == raw


@pytest.mark.parametrize('args', ['-print0', '-printf %p\\n', '-exec echo {} ;',
                                   '-delete', '-ls', '-fprint out.txt'])
def test_find_machine_or_acting_forms_never_compressed(args):
    assert bc._detect_pattern('find . -type f ' + args) == 'find_raw'
    raw = '\n'.join(_find_paths()) + '\n'
    assert bc.compress('find . -type f ' + args, raw) == raw


def test_errors_still_pass_raw():
    raw = '\n'.join(_find_paths()) + '\n'
    assert bc.compress('find . -type f', raw, returncode=1) == raw
    assert bc.compress('find . -type f', raw, stderr='error: something failed') == raw


# ---------------------------------------------------------------- Devin automatic path

@pytest.mark.skipif(os.name != 'posix', reason='Devin adapter is POSIX only')
@pytest.mark.parametrize('command,raw', [
    ('git log -60', _git_log(60)),
    ('find . -type f', '\n'.join(_find_paths()) + '\n'),
])
def test_devin_filter_capture_now_compresses(tmp_path, monkeypatch, command, raw):
    import devin_compress as d
    import devin_state as s
    # CI sets XDG_CONFIG_HOME; any inherited Devin home override would point
    # outside the synthetic HOME and be refused.
    for key in ('XDG_CONFIG_HOME', 'TOKEN_OPTIMIZER_DEVIN_HOME', 'DEVIN_CONFIG_DIR', 'TOKEN_OPTIMIZER_RUNTIME'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('HOME', str(tmp_path))
    s.enable_consent()
    root = s.prepare_capture()
    (root / 'stdout').write_bytes(raw.encode())
    (root / 'stderr').write_bytes(b'')
    d.seal_capture(root, 0)
    res = d.filter_capture(root / 'stdout', root / 'stderr', 0, 'generic', 'session', command=command)
    assert res.outcome == 'compressed'
    assert len(res.stdout) < len(raw.encode()) * 0.6
    assert root.name.encode() in res.stdout
    assert (root / 'stdout').read_bytes() == raw.encode()
