from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import os
import pytest
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Native Windows unsupported by production design (installer refuses os.name != posix); tests gated accordingly.')

SCRIPTS=Path(__file__).resolve().parents[1]/'skills/token-optimizer/scripts'
sys.path.insert(0,str(SCRIPTS))

def test_concurrent_numeric_only_state(tmp_path, monkeypatch):
    import devin_state as s
    monkeypatch.setenv('HOME',str(tmp_path));s.enable_consent()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n:s.record('session-'+str(n),'private secret text','short','compressed'),range(48)))
    result=s.summary();assert result['events']==48
    assert result['provider_tokens'] is None and result['delivery_verified'] is False
    db=sqlite3.connect(s.data_dir()/'metrics.db')
    assert db.execute('SELECT COUNT(DISTINCT session_key) FROM events').fetchone()[0]==48
    assert b'private secret text' not in (s.data_dir()/'metrics.db').read_bytes()


def test_multiprocess_numeric_writes_stress(tmp_path,monkeypatch):
    import subprocess,os
    import devin_state as s
    monkeypatch.setenv('HOME',str(tmp_path));s.enable_consent()
    code='import sys;sys.path.insert(0,sys.argv[1]);import devin_state as s;[s.record(str(n),"long output","short","compressed") for n in range(50)]'
    processes=[subprocess.Popen([sys.executable,'-c',code,str(SCRIPTS)],env=dict(os.environ),stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(8)]
    for process in processes:
        out,err=process.communicate(timeout=45)
        assert process.returncode==0,err
    assert s.summary()['events']==400
