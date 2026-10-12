from pathlib import Path
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
