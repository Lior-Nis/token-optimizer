"""Claude Code cloud kit: setup script -> npm package -> real TO hooks in the VM.

Three layers:
  * parity: the settings hook map is GENERATED from hooks/hooks.json and must
    match it entry for entry (event, matcher, timeout, async, runner args), so
    cloud can never drift from the plugin.
  * the pieces: settings merge (preserve, idempotent, refuse JSONC, uninstall
    only ours), run.sh gates and the double-hook stand-down, the pasted setup
    script (verifies sha512 before running anything, never fails, never exits
    the surrounding script).
  * end to end: build the npm tarball, run the pasted setup script on an empty
    HOME with CLAUDE_CODE_REMOTE=true, fire SessionStart and a PostToolUse Bash
    payload through the exact settings commands, and assert real compression
    happened while the repository working tree stayed untouched.
"""

from __future__ import annotations

import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO / "packaging" / "npm"))

import cloud_kit  # noqa: E402

POSIX = os.name != "nt"
needs_posix = pytest.mark.skipif(not POSIX, reason="cloud VMs are Linux; the kit is POSIX sh")
HOOKS_JSON = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))
RUNNER = Path("/root/.cache/token-optimizer-cloud/token-optimizer-cloud-run.sh")


def _independent_args(command: str) -> list:
    """Runner args parsed a second, different way (shlex) for the parity check."""
    tokens = shlex.split(command.split("; done;")[0].split("; exec ", 1)[1])
    i = next(n for n, t in enumerate(tokens) if t.endswith("/hooks/run.py"))
    return tokens[i + 1:]


# ------------------------------------------------------------------- parity


def test_hook_map_matches_hooks_json_entry_for_entry():
    generated = cloud_kit.generate_hook_map(HOOKS_JSON, RUNNER)
    source = HOOKS_JSON["hooks"]
    assert list(generated) == list(source), "every event, same order"
    total = 0
    for event, groups in source.items():
        gen_groups = generated[event]
        assert len(gen_groups) == len(groups), event
        for g_src, g_gen in zip(groups, gen_groups):
            assert {k: v for k, v in g_src.items() if k != "hooks"} == \
                   {k: v for k, v in g_gen.items() if k != "hooks"}, event
            assert g_src.get("matcher") == g_gen.get("matcher")
            assert len(g_src["hooks"]) == len(g_gen["hooks"]), event
            for h_src, h_gen in zip(g_src["hooks"], g_gen["hooks"]):
                total += 1
                assert {k: v for k, v in h_src.items() if k != "command"} == \
                       {k: v for k, v in h_gen.items() if k != "command"}, (event, h_src)
                assert h_src.get("timeout") == h_gen.get("timeout")
                assert h_src.get("async") == h_gen.get("async")
                gen_args = shlex.split(h_gen["command"].split("; exec sh ", 1)[1])[1:]
                assert gen_args == _independent_args(h_src["command"]), (event, h_gen["command"])
                assert "CLAUDE_PLUGIN_ROOT" not in h_gen["command"]
                assert cloud_kit.MARKER in h_gen["command"]
    assert total == sum(len(g["hooks"]) for gs in source.values() for g in gs) >= 14


def test_generated_command_is_guarded_and_absolute():
    cmd = cloud_kit.hook_command(RUNNER, ["hooks/stop_runner.py"])
    assert cmd == ("[ -r %s ] || exit 0; exec sh %s hooks/stop_runner.py" % (RUNNER, RUNNER))
    spaced = cloud_kit.hook_command(Path("/home/a b") / cloud_kit.RUNNER_NAME, ["x.py"])
    assert "'/home/a b/token-optimizer-cloud-run.sh'" in spaced
    assert cloud_kit.MARKER in spaced


def test_unknown_hook_shape_is_refused_not_guessed():
    bad = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 x.py"}]}]}}
    with pytest.raises(cloud_kit.KitError):
        cloud_kit.generate_hook_map(bad, RUNNER)
    evil = json.loads(json.dumps(HOOKS_JSON))
    evil["hooks"]["Stop"][0]["hooks"][0]["command"] = evil["hooks"]["Stop"][0]["hooks"][0]["command"].replace(
        "hooks/stop_runner.py", "hooks/stop_runner.py$(id)")
    with pytest.raises(cloud_kit.KitError):
        cloud_kit.generate_hook_map(evil, RUNNER)


def test_packaged_hooks_json_is_the_plugin_one(tmp_path):
    import build  # packaging/npm/build.py

    pkg = build.stage(tmp_path)
    assert (pkg / "hooks" / "hooks.json").read_bytes() == (REPO / "hooks" / "hooks.json").read_bytes()
    meta = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert meta["name"] == "@alexgreensh/token-optimizer"
    assert meta["version"] == plugin["version"]
    assert meta["publishConfig"]["access"] == "public"
    assert (pkg / "install-cloud.sh").is_file()
    assert (pkg / "skills" / "token-optimizer" / "scripts" / "cloud_kit.py").is_file()
    assert (pkg / "skills" / "token-optimizer" / "assets" / "dashboard.html").is_file()
    assert not (pkg / "skills" / "token-optimizer" / "assets" / "dashboard-demo.mp4").exists()
    assert not list(pkg.rglob("__pycache__"))


# ----------------------------------------------------------------- settings


def _map():
    return cloud_kit.generate_hook_map(HOOKS_JSON, RUNNER)


def test_merge_preserves_user_settings_and_is_idempotent(tmp_path):
    path = tmp_path / "settings.json"
    user_hook = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
    original = {"model": "opus", "env": {"X": "ü"}, "hooks": {"PreToolUse": [user_hook], "Notification": [
        {"hooks": [{"type": "command", "command": "say hi"}]}]}}
    path.write_text(json.dumps(original, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")

    assert cloud_kit.write_settings_hooks(path, _map()) is True
    text = path.read_text(encoding="utf-8")
    assert '"ü"' in text, "ensure_ascii=False keeps non-ASCII as written"
    assert '\n    "model"' in text, "existing 4-space indent kept"
    data = json.loads(text)
    assert data["model"] == "opus" and data["env"] == {"X": "ü"}
    assert data["hooks"]["PreToolUse"][0] == user_hook, "user hook kept, first"
    assert data["hooks"]["Notification"] == original["hooks"]["Notification"]
    for event in HOOKS_JSON["hooks"]:
        assert [g for g in data["hooks"][event] if cloud_kit._is_ours(g)] == _map()[event]

    before = path.read_bytes()
    assert cloud_kit.write_settings_hooks(path, _map()) is False
    assert path.read_bytes() == before, "second run writes nothing"


def test_update_replaces_only_our_entries(tmp_path):
    path = tmp_path / "settings.json"
    stale = {"hooks": [{"type": "command", "command": "exec sh /old/dir" + cloud_kit.MARKER + " old.py"}]}
    mine = {"hooks": [{"type": "command", "command": "echo keep"}]}
    path.write_text(json.dumps({"hooks": {"OldEvent": [stale], "Stop": [mine, stale]}}), encoding="utf-8")
    cloud_kit.write_settings_hooks(path, _map())
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "OldEvent" not in data["hooks"], "an event only we used is dropped on update"
    assert data["hooks"]["Stop"][0] == mine
    assert stale not in data["hooks"]["Stop"]
    assert data["hooks"]["Stop"][1:] == _map()["Stop"]


def test_user_hook_added_inside_our_group_survives_update_and_uninstall(tmp_path):
    """Review #8: someone adds their own hook to one of our groups (same
    matcher). Update and uninstall must remove only our command, never the
    user's hook that shares the group."""
    path = tmp_path / "settings.json"
    cloud_kit.write_settings_hooks(path, _map())
    data = json.loads(path.read_text(encoding="utf-8"))
    user_hook = {"type": "command", "command": "echo user-audit"}
    group = data["hooks"]["PreToolUse"][0]
    group["hooks"].append(user_hook)
    path.write_text(json.dumps(data), encoding="utf-8")

    cloud_kit.write_settings_hooks(path, _map())  # update
    after = json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    survivors = [g for g in after if user_hook in g["hooks"]]
    assert survivors == [{"matcher": group["matcher"], "hooks": [user_hook]}]
    assert [g for g in after if cloud_kit._is_ours(g)] == _map()["PreToolUse"]

    cloud_kit.write_settings_hooks(path, None)  # uninstall
    left = json.loads(path.read_text(encoding="utf-8"))
    assert left == {"hooks": {"PreToolUse": [{"matcher": group["matcher"], "hooks": [user_hook]}]}}


@pytest.mark.parametrize("text", [
    '{\n  // comment\n  "a": 1\n}\n',
    '{"a": 1,}\n',
    '{"hooks": {}, "hooks": {}}\n',
    '[1, 2]\n',
    '{"hooks": []}\n',
    '{"hooks": {"Stop": {}}}\n',
])
def test_refuses_jsonc_duplicates_and_bad_shapes_without_writing(tmp_path, text):
    path = tmp_path / "settings.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(cloud_kit.KitError):
        cloud_kit.write_settings_hooks(path, _map())
    assert path.read_text(encoding="utf-8") == text


def test_uninstall_strips_only_ours(tmp_path):
    path = tmp_path / "settings.json"
    mine = {"hooks": [{"type": "command", "command": "echo keep"}]}
    path.write_text(json.dumps({"theme": "dark", "hooks": {"Stop": [mine]}}), encoding="utf-8")
    cloud_kit.write_settings_hooks(path, _map())
    cloud_kit.write_settings_hooks(path, None)
    assert json.loads(path.read_text(encoding="utf-8")) == {"theme": "dark", "hooks": {"Stop": [mine]}}

    only = tmp_path / "only.json"
    cloud_kit.write_settings_hooks(only, _map())
    cloud_kit.write_settings_hooks(only, None)
    assert json.loads(only.read_text(encoding="utf-8")) == {}


def test_missing_settings_file_is_created(tmp_path):
    path = tmp_path / ".claude" / "settings.json"
    assert cloud_kit.write_settings_hooks(path, _map()) is True
    assert set(json.loads(path.read_text(encoding="utf-8"))["hooks"]) == set(HOOKS_JSON["hooks"])


# ------------------------------------------------------------------ install


@pytest.fixture()
def staged(tmp_path):
    import build

    return build.stage(tmp_path / "build")


def test_install_copies_writes_runner_and_prunes(tmp_path, staged):
    cache, claude = tmp_path / "cache", tmp_path / "claude"
    (cache / "1.0.0" / "hooks").mkdir(parents=True)
    (cache / "1.0.0" / "hooks" / "run.py").write_text("")
    (cache / "1.0.0" / "package.json").write_text(json.dumps({"name": cloud_kit.PACKAGE, "version": "1.0.0"}))
    (cache / "keep-me.txt").parent.mkdir(parents=True, exist_ok=True)
    (cache / "keep-me.txt").write_text("x")
    report = cloud_kit.install(staged, cache, claude, precompile=False)
    version = report["version"]
    assert (cache / version / "hooks" / "run.py").is_file()
    assert report["pruned"] == ["1.0.0"] and not (cache / "1.0.0").exists()
    run_sh = (cache / cloud_kit.RUNNER_NAME).read_text(encoding="utf-8")
    assert "R=" + str(cache / version) in run_sh and os.access(cache / cloud_kit.RUNNER_NAME, os.X_OK)
    assert "CLAUDE_PLUGIN_ROOT" in run_sh and "PYTHONPYCACHEPREFIX" in run_sh
    settings = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    assert settings["hooks"] == cloud_kit.generate_hook_map(HOOKS_JSON, cache / cloud_kit.RUNNER_NAME)

    again = cloud_kit.install(staged, cache, claude, precompile=False)
    assert again["settings_changed"] is False

    removed = cloud_kit.uninstall(cache, claude)
    assert removed["settings_changed"] is True
    assert (cache / "keep-me.txt").exists(), "files we did not write stay"
    assert not (cache / version).exists() and not (cache / cloud_kit.RUNNER_NAME).exists()
    assert json.loads((claude / "settings.json").read_text(encoding="utf-8")) == {}


def test_user_set_cloud_home_never_loses_unrelated_version_dirs(tmp_path, staged):
    """Review #14: TOKEN_OPTIMIZER_CLOUD_HOME may point at a shared dir. Prune
    and uninstall touch only version dirs that are our package, and the
    pycache/stage dirs only where our marker says we installed."""
    cache, claude = tmp_path / "shared", tmp_path / "claude"
    for name in ("2.0.0", "3.1.4"):
        (cache / name).mkdir(parents=True)
        (cache / name / "data.bin").write_text("someone else's")
    (cache / "pycache").mkdir()
    (cache / "pycache" / "x").write_text("theirs")
    cloud_kit.uninstall(cache, claude)  # nothing of ours here yet
    assert (cache / "pycache" / "x").exists() and (cache / "2.0.0").is_dir()

    report = cloud_kit.install(staged, cache, claude, precompile=False)
    assert report["pruned"] == []
    assert (cache / "2.0.0" / "data.bin").exists() and (cache / "3.1.4" / "data.bin").exists()
    cloud_kit.uninstall(cache, claude)
    assert (cache / "2.0.0" / "data.bin").exists() and (cache / "3.1.4" / "data.bin").exists()
    assert not (cache / report["version"]).exists()


def test_install_refuses_incomplete_package(tmp_path):
    (tmp_path / "pkg").mkdir()
    with pytest.raises(cloud_kit.KitError):
        cloud_kit.install(tmp_path / "pkg", tmp_path / "c", tmp_path / "h")


# ------------------------------------------------------- run.sh + guard


def _fake_root(tmp_path) -> Path:
    root = tmp_path / "root"
    (root / "hooks").mkdir(parents=True, exist_ok=True)
    (root / "hooks" / "python-launcher.sh").write_text(
        '#!/bin/sh\necho "RAN root=$CLAUDE_PLUGIN_ROOT pyc=$PYTHONPYCACHEPREFIX '
        'kit=${TOKEN_OPTIMIZER_CLOUD_KIT_VERSION:-} args=$*"\n', encoding="utf-8")
    # run.sh asks the package's own cloud_kit.py to confirm a plugin stand-down.
    scripts = root / "skills" / "token-optimizer" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCRIPTS / "cloud_kit.py", scripts / "cloud_kit.py")
    return root


def _run_sh(tmp_path, env_extra, home=None):
    root = _fake_root(tmp_path)
    script = tmp_path / "run.sh"
    script.write_text(cloud_kit.render_run_sh(root, tmp_path / "pyc", "9.9.9"), encoding="utf-8")
    home = home or tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home)}
    env.update(env_extra)
    return subprocess.run(["sh", str(script), "hooks/stop_runner.py", "--quiet"], capture_output=True,
                          text=True, env=env, input="{}", timeout=20)


@needs_posix
def test_run_sh_is_silent_off_cloud(tmp_path):
    r = _run_sh(tmp_path, {})
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    r = _run_sh(tmp_path, {"CLAUDE_CODE_REMOTE": "false"})
    assert (r.returncode, r.stdout) == (0, "")


@needs_posix
def test_run_sh_execs_real_launcher_in_cloud(tmp_path):
    r = _run_sh(tmp_path, {"CLAUDE_CODE_REMOTE": "true"})
    assert r.returncode == 0
    assert r.stdout.strip() == "RAN root=%s pyc=%s kit=9.9.9 args=%s hooks/stop_runner.py --quiet" % (
        tmp_path / "root", tmp_path / "pyc", tmp_path / "root" / "hooks" / "run.py")


@needs_posix
@pytest.mark.parametrize("layout", ["installed", "synced", "cowork-synced"])
def test_run_sh_stands_down_when_plugin_is_loaded(tmp_path, layout):
    home = tmp_path / "home"
    plugins = home / ".claude" / "plugins"
    if layout == "installed":
        plugins.mkdir(parents=True)
        (plugins / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"token-optimizer@alexgreensh-token-optimizer": [{"scope": "user"}]}}))
    else:
        name = "token-optimizer" if layout == "synced" else "token-optimizer-cowork~g2"
        hooks = plugins / "synced" / "acct_1" / name / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "hooks.json").write_text("{}")
    r = _run_sh(tmp_path, {"CLAUDE_CODE_REMOTE": "true"}, home=home)
    assert (r.returncode, r.stdout) == (0, ""), "kit must stand down"
    assert cloud_kit.plugin_loaded(home / ".claude"), "doctor sees the same signal"
    forced = _run_sh(tmp_path, {"CLAUDE_CODE_REMOTE": "true", "TOKEN_OPTIMIZER_CLOUD_KIT": "force"}, home=home)
    assert forced.stdout.startswith("RAN ")


@needs_posix
def test_run_sh_ignores_other_plugins_and_honours_off(tmp_path):
    home = tmp_path / "home"
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(json.dumps({"plugins": {"other@m": [], "my-token-optimizer-fork": []}}))
    assert _run_sh(tmp_path, {"CLAUDE_CODE_REMOTE": "true"}, home=home).stdout.startswith("RAN ")
    assert cloud_kit.plugin_loaded(home / ".claude") == ""
    off = _run_sh(tmp_path, {"CLAUDE_CODE_REMOTE": "true", "TOKEN_OPTIMIZER_CLOUD_KIT": "off"}, home=home)
    assert (off.returncode, off.stdout) == (0, "")


@needs_posix
def test_forced_kit_on_desktop_does_not_claim_a_cloud_vm(tmp_path):
    """Review #14: TOKEN_OPTIMIZER_CLOUD_KIT=force off-cloud must run the hooks
    without exporting the kit version, which Token Optimizer reads as cloud-VM
    evidence (lean profile: no daemon, status line, settings self-heal)."""
    forced = _run_sh(tmp_path, {"TOKEN_OPTIMIZER_CLOUD_KIT": "force"})
    assert forced.stdout.startswith("RAN ") and " kit= " in forced.stdout, forced.stdout
    in_vm = _run_sh(tmp_path, {"TOKEN_OPTIMIZER_CLOUD_KIT": "force", "CLAUDE_CODE_REMOTE": "true"})
    assert " kit=9.9.9 " in in_vm.stdout


def _registry(home, entries, settings=None):
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": entries}))
    if settings is not None:
        (home / ".claude" / "settings.json").write_text(json.dumps(settings))


@needs_posix
def test_run_sh_stand_down_needs_a_plugin_that_loads_here(tmp_path):
    """Review #14: a disabled Token Optimizer entry, or one scoped to another
    project, does not load in this session, so it must not silence the kit."""
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    proj.mkdir()
    key = "token-optimizer@alexgreensh-token-optimizer"
    env = {"CLAUDE_CODE_REMOTE": "true", "CLAUDE_PROJECT_DIR": str(proj)}

    _registry(home, {key: [{"scope": "user"}]}, {"enabledPlugins": {key: False}})
    assert _run_sh(tmp_path, env, home=home).stdout.startswith("RAN "), "disabled plugin"
    _registry(home, {key: [{"scope": "project", "projectPath": str(tmp_path / "other")}]}, {})
    assert _run_sh(tmp_path, env, home=home).stdout.startswith("RAN "), "other project's plugin"

    _registry(home, {key: [{"scope": "project", "projectPath": str(proj)}]}, {})
    assert _run_sh(tmp_path, env, home=home).stdout == "", "this project's plugin loads"
    # Project settings beat user settings for enabledPlugins.
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {key: False}}))
    assert _run_sh(tmp_path, env, home=home).stdout.startswith("RAN ")
    (proj / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {key: True}}))
    _registry(home, {key: [{"scope": "user"}]}, {"enabledPlugins": {key: False}})
    assert _run_sh(tmp_path, env, home=home).stdout == ""
    # A registry we cannot parse but that names Token Optimizer: stand down.
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text('{"token-optimizer@x": [')
    assert _run_sh(tmp_path, env, home=home).stdout == ""


def test_plugin_loaded_ignores_an_empty_synced_dir(tmp_path):
    """The cloud VM ships ~/.claude/plugins/synced/<org>_<account>/ empty
    (live probe 2026-10-19); only a Token Optimizer copy inside it counts."""
    claude = tmp_path / ".claude"
    (claude / "plugins" / "synced" / "org_acct").mkdir(parents=True)
    (claude / "plugins" / "synced" / "org_acct" / "some-other-plugin" / "hooks").mkdir(parents=True)
    (claude / "plugins" / "synced" / "org_acct" / "some-other-plugin" / "hooks" / "hooks.json").write_text("{}")
    assert cloud_kit.plugin_loaded(claude, project=str(tmp_path)) == ""


@needs_posix
def test_missing_runner_is_a_clean_noop(tmp_path):
    cmd = cloud_kit.hook_command(tmp_path / "nope" / cloud_kit.RUNNER_NAME, ["hooks/stop_runner.py"])
    r = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=10)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


# ------------------------------------------------------------ setup script

needs_tools = pytest.mark.skipif(
    not POSIX or not all(shutil.which(t) for t in ("curl", "openssl", "base64", "tar", "mktemp")),
    reason="setup script needs curl, openssl, base64, tar, mktemp (all present on cloud VMs)")


def _fake_tarball(path: Path, body: str) -> Path:
    data = body.encode("utf-8")
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo("package/install-cloud.sh")
        info.size, info.mode = len(data), 0o755
        tf.addfile(info, io.BytesIO(data))
    return path


@needs_tools
def test_setup_script_runs_install_only_after_hash_matches(tmp_path):
    marker = tmp_path / "ran"
    tgz = _fake_tarball(tmp_path / "t.tgz", "#!/bin/sh\necho ran > %s\necho noisy\n" % shlex.quote(str(marker)))
    good = cloud_kit.render_setup_script("1.2.3", cloud_kit.integrity_of_file(tgz), tgz.as_uri())
    r = subprocess.run(["sh", "-c", good + "echo after-ours"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and marker.read_text().strip() == "ran"
    assert r.stdout == "after-ours\n", "no noise, and the user's later setup lines still run"

    marker.unlink()
    bad = cloud_kit.render_setup_script("1.2.3", "sha512-" + "A" * 86 + "==", tgz.as_uri())
    r = subprocess.run(["sh", "-c", bad + "echo after-ours"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout == "after-ours\n"
    assert not marker.exists(), "a tarball that fails the hash never runs"


@needs_tools
def test_setup_script_survives_unreachable_registry(tmp_path):
    script = cloud_kit.render_setup_script("1.2.3", "sha512-x", (tmp_path / "missing.tgz").as_uri())
    r = subprocess.run(["sh", "-ec", script + "echo after-ours"], capture_output=True, text=True, timeout=60)
    assert (r.returncode, r.stdout) == (0, "after-ours\n"), "even under sh -e"


def test_setup_script_renderer_rejects_injection():
    with pytest.raises(cloud_kit.KitError):
        cloud_kit.render_setup_script("1.2.3'; rm -rf /", "sha512-x", "https://x")
    with pytest.raises(cloud_kit.KitError):
        cloud_kit.render_setup_script("1.2.3", "md5-x", "https://x")
    s = cloud_kit.render_setup_script("1.2.3", "sha512-abc", "https://registry.npmjs.org/a b")
    assert "'https://registry.npmjs.org/a b'" in s


def test_pinned_script_targets_the_scoped_registry_tarball():
    s = cloud_kit.render_setup_script("5.0.1", "sha512-abc", cloud_kit.tarball_url("5.0.1"))
    assert "https://registry.npmjs.org/@alexgreensh/token-optimizer/-/token-optimizer-5.0.1.tgz" in s
    assert len(s.strip().splitlines()) == 3


@needs_tools
def test_latest_script_follows_registry_metadata(tmp_path):
    reg = tmp_path / "reg"
    pkgdir = reg / "@alexgreensh" / "token-optimizer" / "-"
    pkgdir.mkdir(parents=True)
    marker = tmp_path / "ran"
    tgz = _fake_tarball(pkgdir / "token-optimizer-2.0.0.tgz", "#!/bin/sh\necho ok > %s\n" % shlex.quote(str(marker)))
    (reg / "@alexgreensh" / "token-optimizer" / "latest").write_text(json.dumps(
        {"dist": {"tarball": reg.as_uri() + "/@alexgreensh/token-optimizer/-/" + tgz.name,
                  "integrity": cloud_kit.integrity_of_file(tgz)}}))
    script = cloud_kit.render_setup_script_latest(reg.as_uri())
    meta = reg / "@alexgreensh" / "token-optimizer" / "latest"  # curl decodes %2F
    r = subprocess.run(["sh", "-c", script.replace(">/dev/null 2>&1", "")], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0
    assert marker.read_text().strip() == "ok", (script, r.stdout, r.stderr)

    # A tarball URL outside the package's own registry path is refused.
    marker.unlink()
    meta.write_text(json.dumps({"dist": {"tarball": "file:///elsewhere/x.tgz",
                                                    "integrity": cloud_kit.integrity_of_file(tgz)}}))
    subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=60)
    assert not marker.exists()


def test_measure_cli_prints_setup_script(tmp_path):
    tgz = _fake_tarball(tmp_path / "t.tgz", "#!/bin/sh\n")
    r = subprocess.run([sys.executable, str(SCRIPTS / "measure.py"), "cloud-setup-script",
                        "--from-tarball", str(tgz)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert cloud_kit.integrity_of_file(tgz) in r.stdout
    assert "@alexgreensh/token-optimizer/-/token-optimizer-" in r.stdout


# --------------------------------------------------------------- end to end


def _npm_tarball(tmp_path) -> Path:
    import build

    pkg = build.stage(tmp_path / "build")
    if shutil.which("npm"):
        info = json.loads(build.pack(pkg, tmp_path / "build"))
        return tmp_path / "build" / info["filename"]
    tgz = tmp_path / "build" / "pkg.tgz"
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(str(pkg), arcname="package")
    return tgz


def _shims(tmp_path) -> Path:
    """Service managers that log instead of acting: an e2e run on a developer
    Mac must never (re)load a real LaunchAgent or signal a real daemon."""
    d = tmp_path / "shims"
    d.mkdir()
    for name in ("launchctl", "systemctl", "lsof", "schtasks", "crontab", "loginctl"):
        p = d / name
        p.write_text('#!/bin/sh\necho "%s $*" >> "%s"\nexit 1\n' % (name, tmp_path / "shims.log"))
        p.chmod(0o755)
    return d


def _commands(settings: dict, event: str, tool: str | None = None) -> list:
    import re

    out = []
    for group in settings["hooks"].get(event, []):
        m = group.get("matcher")
        if tool and m and not re.fullmatch(m, tool):
            continue
        out += [h["command"] for h in group["hooks"]]
    return out


@needs_tools
@pytest.mark.skipif(sys.platform == "win32", reason="cloud VMs are Linux")
def test_end_to_end_fresh_vm(tmp_path):
    tgz = _npm_tarball(tmp_path)
    home = tmp_path / "root-home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(repo)]
    subprocess.run(git + ["init", "-q"], check=True)
    (repo / "README.md").write_text("hello\n")
    subprocess.run(git + ["add", "."], check=True)
    subprocess.run(git + ["commit", "-qm", "init"], check=True)

    path = "%s:%s" % (_shims(tmp_path), os.environ.get("PATH", "/usr/bin:/bin"))
    base_env = {"PATH": path, "HOME": str(home), "LANG": "C.UTF-8",
                "TMPDIR": str(tmp_path / "tmp")}
    (tmp_path / "tmp").mkdir()

    # 1. The setup script, as pasted (runs before Claude Code; not remote yet).
    setup = cloud_kit.render_setup_script(json.loads(
        (REPO / ".claude-plugin" / "plugin.json").read_text())["version"],
        cloud_kit.integrity_of_file(tgz), tgz.as_uri())
    r = subprocess.run(["sh", "-c", setup], capture_output=True, text=True, env=base_env, timeout=300)
    log = home / ".cache" / "token-optimizer-cloud" / "install.log"
    assert (r.returncode, r.stdout, r.stderr) == (0, "", ""), log.read_text() if log.exists() else ""
    settings_path = home / ".claude" / "settings.json"
    assert settings_path.is_file(), log.read_text()
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    runner = home / ".cache" / "token-optimizer-cloud" / cloud_kit.RUNNER_NAME
    assert settings["hooks"] == cloud_kit.generate_hook_map(HOOKS_JSON, runner)

    # 2. A session: hooks fire exactly as Claude Code runs them.
    env = dict(base_env, CLAUDE_CODE_REMOTE="true", CLAUDE_PROJECT_DIR=str(repo))
    sid = "cloud-e2e-session"
    transcript = home / ".claude" / "projects" / "-repo" / (sid + ".jsonl")
    transcript.parent.mkdir(parents=True)
    transcript.write_text("")
    common = {"session_id": sid, "transcript_path": str(transcript), "cwd": str(repo)}

    def fire(event, payload, tool=None):
        results = []
        for cmd in _commands(settings, event, tool):
            results.append(subprocess.run(["sh", "-c", cmd], input=json.dumps(dict(common, **payload)),
                                          capture_output=True, text=True, cwd=str(repo), env=env, timeout=120))
        return results

    for res in fire("SessionStart", {"hook_event_name": "SessionStart", "source": "startup"}):
        assert res.returncode == 0, res.stderr

    listing = "\n".join("./src/pkg%d/module_%d/file_%d.py" % (i % 9, i % 13, i) for i in range(600))
    post = fire("PostToolUse", {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                                "tool_input": {"command": "find . -name '*.py'"},
                                "tool_response": {"stdout": listing, "stderr": "", "interrupted": False,
                                                  "isImage": False},
                                "tool_use_id": "toolu_01CLOUDE2E"}, tool="Bash")
    assert len(post) == 1 and post[0].returncode == 0, post
    out = json.loads(post[0].stdout)
    compressed = out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    assert compressed.startswith("[find: 600 paths total"), compressed[:200]
    assert len(compressed) < len(listing) * 0.75

    # 3. Nothing landed in the repo, TO did not rewire settings behind us
    #    (no double hooks), and nothing made the kit stand itself down.
    status = subprocess.run(git + ["status", "--porcelain", "--ignored", "--untracked-files=all"],
                            capture_output=True, text=True, check=True).stdout
    assert status == "", status
    after = json.loads(settings_path.read_text(encoding="utf-8"))
    assert after["hooks"] == settings["hooks"], "TO's own heal must not add or drop hooks"
    assert cloud_kit.plugin_loaded(home / ".claude") == ""
    assert (home / ".cache" / "token-optimizer-cloud" / "pycache").is_dir()

    # 4. Off the cloud the very same settings do nothing.
    quiet = subprocess.run(["sh", "-c", _commands(settings, "PostToolUse", "Bash")[0]],
                           input="{}", capture_output=True, text=True, cwd=str(repo),
                           env=dict(base_env, CLAUDE_PROJECT_DIR=str(repo)), timeout=30)
    assert (quiet.returncode, quiet.stdout) == (0, "")


# ------------------------------------------------- self-heal + doctor


def _owns(env_extra, home):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home)}
    env.update(env_extra)
    code = ("import measure; r = measure.setup_all_hooks(dry_run=True); "
            "print(measure._cloud_kit_owns_hooks(), r.get('reason'))")
    return subprocess.run([sys.executable, "-c", code], cwd=str(SCRIPTS), capture_output=True,
                          text=True, env=env, timeout=120).stdout.strip()


def test_self_heal_stands_down_for_kit_hooks(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    assert _owns({}, home) == "False None", "no kit: normal self-heal behaviour"
    assert _owns({"TOKEN_OPTIMIZER_CLOUD_KIT_VERSION": "1.0.0"}, home) == "True cloud_kit"
    cloud_kit.write_settings_hooks(home / ".claude" / "settings.json",
                                   cloud_kit.generate_hook_map(HOOKS_JSON, home / cloud_kit.RUNNER_NAME))
    assert _owns({}, home) == "True cloud_kit", "kit hooks in settings.json are enough"


def test_doctor_reports_a_clean_install(tmp_path, staged):
    cache, claude = tmp_path / "cache", tmp_path / "claude"
    assert cloud_kit.doctor(cache, claude)["ok"] is False
    cloud_kit.install(staged, cache, claude, precompile=False)
    report = cloud_kit.doctor(cache, claude)
    assert report["ok"] is True, report
    settings = claude / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 1
    settings.write_text(json.dumps(data), encoding="utf-8")
    assert cloud_kit.doctor(cache, claude)["ok"] is False, "drift from hooks.json is caught"
