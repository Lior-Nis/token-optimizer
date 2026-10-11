from pathlib import Path
import sys
SCRIPTS=Path(__file__).resolve().parents[1]/'skills/token-optimizer/scripts'
sys.path.insert(0,str(SCRIPTS))

def test_install_preserves_config_and_foreign_skills(tmp_path, monkeypatch):
    import devin_install as d
    monkeypatch.setenv('HOME',str(tmp_path))
    config=tmp_path/'.config/devin/config.json'; config.parent.mkdir(parents=True)
    config.write_text('{"permissions":{"deny":["exec"]}}')
    before=config.read_bytes()
    d.install()
    assert config.read_bytes()==before
    skill=config.parent/'skills/token-optimizer-output/SKILL.md'
    assert skill.is_file()
    assert 'allowed-tools' not in skill.read_text()
    assert 'triggers: [user]' in skill.read_text()
    d.install()
    d.uninstall()
    assert config.read_bytes()==before
    assert not skill.exists()

import pytest

@pytest.mark.parametrize('mode',['foreign','symlink','edited'])
def test_installer_refuses_collision(tmp_path,monkeypatch,mode):
    import devin_install as d
    import devin_state as s
    monkeypatch.setenv('HOME',str(tmp_path))
    skill=s.home()/'skills/token-optimizer-output'
    if mode=='edited':
        d.install();(skill/'SKILL.md').write_text('user edit')
    else:
        skill.parent.mkdir(parents=True)
        if mode=='foreign':skill.mkdir();(skill/'SKILL.md').write_text('foreign')
        else:skill.symlink_to(tmp_path/'elsewhere')
    with pytest.raises(OSError):d.install()
    if mode=='edited':
        with pytest.raises(OSError):d.uninstall()
        assert (skill/'SKILL.md').read_text()=='user edit'

def test_dry_run_no_writes(tmp_path,monkeypatch):
    import devin_install as d
    monkeypatch.setenv('HOME',str(tmp_path));d.install(dry_run=True)
    assert list(tmp_path.iterdir())==[]

@pytest.mark.parametrize('name',['../outside','relative','/tmp/outside'])
def test_invalid_home_override(tmp_path,monkeypatch,name):
    import devin_state as s
    monkeypatch.setenv('HOME',str(tmp_path));monkeypatch.setenv('TOKEN_OPTIMIZER_DEVIN_HOME',name)
    with pytest.raises(OSError):s.home()

def test_genuine_devin_cli_discovery(tmp_path,monkeypatch):
    """Opt-in integration receipt; never starts a model/session."""
    import os,subprocess
    import devin_install as d
    binary=os.environ.get('DEVIN_TEST_BINARY')
    if not binary:pytest.skip('DEVIN_TEST_BINARY required for real discovery')
    monkeypatch.setenv('HOME',str(tmp_path));d.install()
    env=dict(os.environ);env.pop('XDG_CONFIG_HOME',None)
    listed=subprocess.run([binary,'skills','list'],env=env,capture_output=True,text=True,check=True)
    shown=subprocess.run([binary,'skills','show','token-optimizer-output'],env=env,capture_output=True,text=True,check=True)
    assert '/token-optimizer-output [user]' in listed.stdout
    assert 'Triggers: user' in shown.stdout
    assert 'Triggers: user, model' not in shown.stdout
    assert str(d.skill_dir()) in shown.stdout
