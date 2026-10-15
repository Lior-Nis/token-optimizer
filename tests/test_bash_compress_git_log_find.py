"""git log and find summarizers (5.13.46): savings, preservation, passthrough."""
import os
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/token-optimizer/scripts'
sys.path.insert(0, str(SCRIPTS))

import bash_compress as bc  # noqa: E402
from token_estimate import estimate_tokens  # noqa: E402


def _saving(raw, out):
    return 1.0 - estimate_tokens(out) / estimate_tokens(raw)


def _git_log(n=60, body=True, merge_every=7):
    parts = []
    for i in range(n):
        h = '%040x' % (0xabc0000000 + i * 7919)
        parts.append('commit ' + h + (' (HEAD -> main, tag: v1.%d)' % i if i == 0 else ''))
        if i % merge_every == 0:
            parts.append('Merge: %s %s' % (h[:8], '%08x' % (i + 1)))
        parts.append('Author: Dev %d <dev%d@users.noreply.example.com>' % (i % 3, i % 3))
        parts.append('Date:   Tue Oct 13 10:%02d:00 2026 +0300' % (i % 60))
        parts.append('')
        parts.append('    fix(module-%d): change behavior %d' % (i, i))
        if body:
            parts += ['    ',
                      '    This explains in some detail why the change %d was needed and' % i,
                      '    what it touches, across several wrapped lines of prose text.',
                      '    ',
                      '    Co-authored-by: Pair Person <pair@example.com>',
                      '    Signed-off-by: Dev %d <dev%d@example.com>' % (i % 3, i % 3)]
        parts.append('')
    return '\n'.join(parts) + '\n'


def test_git_log_saves_and_keeps_every_commit():
    raw = _git_log(60)
    out = bc.compress('git log -60', raw)
    assert _saving(raw, out) >= 0.40
    hashes = re.findall(r'(?m)^commit ([0-9a-f]{40})', raw)
    assert len(hashes) == 60
    for h in hashes:
        assert h[:10] in out
    for i in range(60):
        assert 'change behavior %d' % i in out
    assert out.count('Dev 0') + out.count('Dev 1') + out.count('Dev 2') >= 60
    assert 'Signed-off-by' not in out
    assert 'Pair Person' in out          # co-author attribution survives
    assert 'dev0@users' not in out       # emails condensed
    assert '2026-10-13 10:00 +0300' in out
    assert 'HEAD -> main' in out         # decorations kept
    assert 'merge' in out


@pytest.mark.parametrize('flags', [
    '--oneline', '--format=%H %s', '--pretty=format:%h', '-z', '--porcelain',
    '--name-status', '--numstat', '--stat', '--graph', '-p', '--patch', '-60p',
])
def test_git_log_machine_formats_use_light_cleanup_only(flags):
    assert bc._detect_pattern('git log %s' % flags) == 'git_log_basic'
    raw = ('%040x one-line subject text\n' % 1) * 1  # a stand-in for any non-default shape
    raw = ''.join('%07x fix: item %d\n' % (i, i) for i in range(80))
    out = bc.compress('git log --oneline', raw)
    assert out == raw                      # nothing to strip: untouched
    assert bc._detect_pattern('git log -60') == 'git_log'
    assert bc._detect_pattern('git log -n 20 --since=1.week') == 'git_log'


def test_git_log_patch_output_never_summarized():
    raw = _git_log(8, body=False).replace(
        '\n\ncommit ', '\n\ndiff --git a/x b/x\n+added\n\ncommit ')
    out = bc._compress_git_log(raw)
    assert 'diff --git a/x b/x' in out and '[git log:' not in out


def test_git_log_small_and_foreign_output_passthrough():
    raw = _git_log(3)
    assert bc._compress_git_log(raw).startswith('commit') or '[git log:' not in bc._compress_git_log(raw)
    assert '[git log:' not in bc._compress_git_log('some other text\n' * 50)


def test_git_log_hash_count_invariant_guard():
    raw = _git_log(40)
    out = bc._compress_git_log(raw)
    assert len(re.findall(r'(?m)^[0-9a-f]{10} 2026', out)) == 40


def _find_paths():
    paths = ['./README.md', './setup.py', './LICENSE']
    paths += ['./src/pkg/mod_%02d.py' % i for i in range(45)]
    paths += ['./src/pkg/sub/a.py', './src/pkg/sub/b.py']
    paths += ['./node_modules/lib%d/index.js' % i for i in range(150)]
    paths += ['./.git/objects/%02x/%038x' % (i, i) for i in range(120)]
    paths += ['./tests/test_%d.py' % i for i in range(6)]
    return paths


def test_find_saves_and_states_total():
    paths = _find_paths()
    raw = '\n'.join(paths) + '\n'
    out = bc.compress('find . -type f', raw)
    assert _saving(raw, out) >= 0.50
    assert ('%d paths total' % len(paths)) in out
    assert './node_modules/ (' in out and './.git/ (' in out
    assert 'paths collapsed' in out
    assert './src/pkg/sub/ a.py b.py' in out        # small dirs keep every name
    assert './tests/ test_0.py' in out
    assert '*.py x45' in out


def test_find_small_output_passthrough():
    raw = '\n'.join('./f%d.txt' % i for i in range(20)) + '\n'
    assert bc.compress('find . -type f', raw) == raw


@pytest.mark.parametrize('args', ['-print0', '-printf %p\\n', '-exec echo {} ;',
                                   '-delete', '-ls', '-fprint out.txt'])
def test_find_machine_or_acting_forms_never_compressed(args):
    assert bc._detect_pattern('find . -type f ' + args) == 'find_raw'
    raw = '\n'.join(_find_paths()) + '\n'
    assert bc.compress('find . -type f ' + args, raw) == raw


def test_find_not_a_path_list_unchanged():
    raw = '\n'.join('%s  column  value %d' % ('./x%d' % i, i) for i in range(100)) + '\n'
    assert bc._compress_find(raw) == raw
    tabbed = '\n'.join('./a\tb%d' % i for i in range(100)) + '\n'
    assert bc._compress_find(tabbed) == tabbed


def test_find_counts_add_up():
    paths = _find_paths()
    out = bc._compress_find('\n'.join(paths) + '\n')
    claimed = 0
    for line in out.splitlines()[1:]:
        m = re.search(r'\((\d+) (?:entries|paths collapsed)', line)
        claimed += int(m.group(1)) if m else len(line.split(' ')[1:])
    assert claimed == len(paths)


def test_errors_still_pass_raw():
    raw = '\n'.join(_find_paths()) + '\n'
    assert bc.compress('find . -type f', raw, returncode=1) == raw
    assert bc.compress('find . -type f', raw, stderr='error: something failed') == raw


# ---- Devin automatic path -------------------------------------------------

@pytest.mark.skipif(os.name != 'posix', reason='Devin adapter is POSIX only')
@pytest.mark.parametrize('command,raw', [
    ('git log -60', _git_log(60)),
    ('find . -type f', '\n'.join(_find_paths()) + '\n'),
])
def test_devin_filter_capture_now_compresses(tmp_path, monkeypatch, command, raw):
    import devin_compress as d
    import devin_state as s
    monkeypatch.setenv('HOME', str(tmp_path))
    s.enable_consent()
    root = s.prepare_capture()
    (root / 'stdout').write_bytes(raw.encode())
    (root / 'stderr').write_bytes(b'')
    d.seal_capture(root, 0)
    res = d.filter_capture(root / 'stdout', root / 'stderr', 0, 'generic', 'session', command=command)
    assert res.outcome == 'compressed'
    assert len(res.stdout) < len(raw.encode()) * 0.6
    assert root.name.encode() in res.stdout            # recovery pointer present
    assert (root / 'stdout').read_bytes() == raw.encode()  # original kept exactly
