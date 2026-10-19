#!/usr/bin/env python3
"""Token Optimizer for Claude Code cloud sessions (claude.ai/code).

Cloud VMs load no plugins, so the plugin install never reaches them. What a
cloud VM does run is the environment's *setup script* (as root, before Claude
Code starts, for every repository and multi-repo session that uses the
environment) and the hooks in the VM's user-level ``~/.claude/settings.json``.

This module puts the REAL Token Optimizer there, not a reimplementation:

``measure.py cloud-setup-script``
    Prints a short, paste-ready setup script. It downloads the published npm
    tarball ``@alexgreensh/token-optimizer@<version>`` from registry.npmjs.org,
    checks its sha512 against the integrity the registry published, and only
    then runs the ``install-cloud.sh`` inside it. Nothing unverified executes.

``install-cloud.sh`` -> ``cloud_kit.py install``
    Copies the package to ``~/.cache/token-optimizer-cloud/<version>``, writes
    ``~/.cache/token-optimizer-cloud/token-optimizer-cloud-run.sh`` (sets CLAUDE_PLUGIN_ROOT and execs
    the real hooks/python-launcher.sh -> hooks/run.py), and merges into
    ``~/.claude/settings.json`` a hook map GENERATED from the package's own
    hooks/hooks.json: same events, matchers, timeouts, async flags and runner
    arguments, only the launcher prefix differs. Commands carry absolute paths
    and never the literal ``${CLAUDE_PLUGIN_ROOT}`` (repo/user hooks containing
    it never run in cloud sessions).

``measure.py cloud-doctor``
    Checks the install from inside a VM (or any machine).

Rules this module keeps:
    * stdlib only, no imports from sibling modules: install runs under
      ``python3 -I`` straight out of the extracted tarball.
    * nothing is written into any repository working tree.
    * settings are merged, never overwritten; only entries whose command runs
      our run.sh are ever replaced or removed; JSONC / duplicate keys refused.
    * install never raises out of ``main`` and the shell wrapper exits 0.
"""

from __future__ import annotations

import base64
import compileall
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

PACKAGE = "@alexgreensh/token-optimizer"
REGISTRY = "https://registry.npmjs.org"
CACHE_DIRNAME = "token-optimizer-cloud"
RUNNER_NAME = "token-optimizer-cloud-run.sh"
# Every hook command we write contains this (whatever the cache dir is); it is
# how we, and measure.py's self-heal, recognize our entries and only ours.
MARKER = "/" + RUNNER_NAME

SCRIPTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS_DIR.parents[2]

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_ARG_RE = re.compile(r"^[A-Za-z0-9_./=:-]+$")
# The canonical hooks.json command ends with:
#   exec "$b" "$L" "${CLAUDE_PLUGIN_ROOT}/hooks/run.py" <runner args>; done; exit 0
_CANON_RE = re.compile(r'"\$\{CLAUDE_PLUGIN_ROOT\}/hooks/run\.py" ([^;"\'`$]+?); done; exit 0$')


class KitError(Exception):
    """A refusal with a message meant for the user."""


# --------------------------------------------------------------------- paths


def cache_dir() -> Path:
    override = os.environ.get("TOKEN_OPTIMIZER_CLOUD_HOME")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~")) / ".cache" / CACHE_DIRNAME


def claude_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~")) / ".claude"


def package_version(root: Path) -> str:
    for rel in ("package.json", ".claude-plugin/plugin.json"):
        try:
            v = json.loads((root / rel).read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(v, str) and _VERSION_RE.match(v):
            return v
    raise KitError("cannot read a version from %s" % root)


# ------------------------------------------------------------------ hook map


def runner_args(command: str) -> list:
    """Runner arguments of one canonical hooks.json command (after run.py)."""
    m = _CANON_RE.search(command or "")
    if not m:
        raise KitError("hooks.json command has an unexpected shape: %r" % (command or "")[:120])
    args = m.group(1).split()
    for a in args:
        if not _ARG_RE.match(a):
            raise KitError("unexpected runner argument %r in hooks.json" % a)
    return args


def hook_command(runner: Path, args: list) -> str:
    r = shlex.quote(str(runner))
    return "[ -r %s ] || exit 0; exec sh %s %s" % (r, r, " ".join(args))


def generate_hook_map(hooks_json: dict, runner: Path) -> dict:
    """event -> groups, mirroring hooks.json exactly except each command."""
    src = hooks_json.get("hooks") if isinstance(hooks_json, dict) else None
    if not isinstance(src, dict) or not src:
        raise KitError("hooks.json has no hooks")
    out = {}
    for event, groups in src.items():
        new_groups = []
        for group in groups:
            g = {}
            for key, value in group.items():
                if key != "hooks":
                    g[key] = value
                    continue
                hooks = []
                for hook in value:
                    h = dict(hook)
                    if h.get("type") != "command":
                        raise KitError("non-command hook in hooks.json (%s)" % event)
                    h["command"] = hook_command(runner, runner_args(h.get("command", "")))
                    hooks.append(h)
                g["hooks"] = hooks
            new_groups.append(g)
        out[event] = new_groups
    return out


def load_hooks_json(root: Path) -> dict:
    return json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ settings


def _no_dup_pairs(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise KitError("duplicate key %r" % k)
        seen[k] = v
    return seen


def load_settings(path: Path):
    """Return (data, raw_text). {} for a missing or blank file."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}, None
    text = raw.decode("utf-8-sig")
    if not text.strip():
        return {}, text
    try:
        data = json.loads(text, object_pairs_hook=_no_dup_pairs)
    except KitError as exc:
        raise KitError("%s has a %s; nothing was changed" % (path, exc)) from None
    except ValueError as exc:
        raise KitError("%s is not plain JSON (comments or trailing commas?); nothing was "
                       "changed: %s" % (path, exc)) from None
    if not isinstance(data, dict):
        raise KitError("%s is not a JSON object; nothing was changed" % path)
    return data, text


def _is_ours(group) -> bool:
    return isinstance(group, dict) and isinstance(group.get("hooks"), list) and any(
        isinstance(h, dict) and MARKER in str(h.get("command", "")) for h in group["hooks"])


def merge_settings(settings: dict, hook_map: dict) -> dict:
    out = dict(settings)
    hooks = out.get("hooks", {})
    if not isinstance(hooks, dict):
        raise KitError("'hooks' in settings.json is not an object; nothing was changed")
    hooks = dict(hooks)
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            raise KitError("hooks.%s in settings.json is not a list; nothing was changed" % event)
        kept = [g for g in groups if not _is_ours(g)]
        if kept or event in hook_map or not groups:
            hooks[event] = kept
        else:
            del hooks[event]
    for event, groups in hook_map.items():
        hooks[event] = hooks.get(event, []) + groups
    out["hooks"] = hooks
    return out


def strip_settings(settings: dict) -> dict:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return dict(settings)
    out = dict(settings)
    new_hooks = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            new_hooks[event] = groups
            continue
        kept = [g for g in groups if not _is_ours(g)]
        if kept or not groups:
            new_hooks[event] = kept
    if new_hooks or not hooks:
        out["hooks"] = new_hooks
    else:
        del out["hooks"]
    return out


def _indent_of(text) -> object:
    if not text:
        return 2
    for line in text.splitlines()[1:]:
        stripped = line.lstrip(" \t")
        if stripped and stripped != line:
            ws = line[: len(line) - len(stripped)]
            return "\t" if ws.startswith("\t") else len(ws)
    return 2


def dump_settings(data: dict, like_text) -> bytes:
    return (json.dumps(data, indent=_indent_of(like_text), ensure_ascii=False) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes, mode=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_settings_hooks(settings_path: Path, hook_map) -> bool:
    """Merge (hook_map given) or strip (None). Returns True when the file changed."""
    data, text = load_settings(settings_path)
    new = merge_settings(data, hook_map) if hook_map is not None else strip_settings(data)
    if new == data:
        return False
    # A file that held only our hooks is left as {} rather than deleted: we
    # cannot know whether something else created it.
    _atomic_write(settings_path, dump_settings(new, text))
    return True


# --------------------------------------------------------------------- run.sh


RUN_SH = """#!/bin/sh
# Token Optimizer for Claude Code cloud sessions. Written by install-cloud.sh
# (version {version}); hooks in ~/.claude/settings.json call it. It runs one
# real Token Optimizer hook and stays silent itself.
# Off outside Claude Code cloud VMs; TOKEN_OPTIMIZER_CLOUD_KIT=off disables it,
# =force runs it anyway (skips both checks below).
R={root}
L="$R/hooks/python-launcher.sh"
[ -r "$L" ] || exit 0
case "${{TOKEN_OPTIMIZER_CLOUD_KIT:-}}" in
  off|0|false) exit 0 ;;
  force) ;;
  *)
    [ "$CLAUDE_CODE_REMOTE" = true ] || exit 0
    # Stand down when the real plugin is loaded too (an enabled plugin install
    # that applies here, or an account-synced copy), so no hook runs twice.
    # The grep is a cheap pre-filter; cloud_kit.py decides on the parsed file
    # (scope, project path, enabledPlugins).
    P="${{CLAUDE_CONFIG_DIR:-$HOME/.claude}}/plugins"
    if [ -f "$P/installed_plugins.json" ] && grep -Eq '"token-optimizer[^"@]*@' "$P/installed_plugins.json" 2>/dev/null; then
      python3 -I "$R/skills/token-optimizer/scripts/cloud_kit.py" plugin-active >/dev/null 2>&1 && exit 0
    fi
    for f in "$P"/synced/*/token-optimizer*/hooks/hooks.json; do [ -f "$f" ] && exit 0; done
    ;;
esac
CLAUDE_PLUGIN_ROOT="$R"
PYTHONPYCACHEPREFIX={pycache}
export CLAUDE_PLUGIN_ROOT PYTHONPYCACHEPREFIX
# The kit version doubles as Token Optimizer's "this is a cloud VM" evidence
# (lean profile), so it is exported only inside one: =force on a desktop must
# not switch the desktop to the cloud profile.
if [ "$CLAUDE_CODE_REMOTE" = true ]; then
  TOKEN_OPTIMIZER_CLOUD_KIT_VERSION={version}
  export TOKEN_OPTIMIZER_CLOUD_KIT_VERSION
fi
exec bash "$L" "$R/hooks/run.py" "$@"
"""


def render_run_sh(root: Path, pycache: Path, version: str) -> str:
    return RUN_SH.format(root=shlex.quote(str(root)), pycache=shlex.quote(str(pycache)),
                         version=version)


# -------------------------------------------------------------------- install

# What the package carries and the install copies. Everything else in the
# extracted tarball (package.json, README, LICENSE files) is copied too; this
# list is what must exist for a usable install.
_REQUIRED = ("hooks/hooks.json", "hooks/python-launcher.sh", "hooks/run.py",
             "skills/token-optimizer/scripts/measure.py")


def _validate_tree(root: Path) -> None:
    missing = [r for r in _REQUIRED if not (root / r).is_file()]
    if missing:
        raise KitError("package at %s is incomplete, missing: %s" % (root, ", ".join(missing)))


def _precompile(root: Path, pycache: Path) -> None:
    """Byte-compile into the pycache prefix so the first hooks start warm.

    Keyed by absolute source path, so it must run on the final location.
    """
    old = sys.pycache_prefix
    sys.pycache_prefix = str(pycache)
    try:
        for sub in ("hooks", "skills/token-optimizer/scripts"):
            compileall.compile_dir(str(root / sub), quiet=1, workers=1)
    finally:
        sys.pycache_prefix = old


def install(source: Path, cache=None, claude=None, precompile=True) -> dict:
    source = Path(source).resolve()
    cache = Path(cache) if cache else cache_dir()
    claude = Path(claude) if claude else claude_dir()
    _validate_tree(source)
    version = package_version(source)
    target = cache / version
    cache.mkdir(parents=True, exist_ok=True)

    if source != target.resolve() and not (target / "hooks" / "run.py").is_file():
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=str(cache)))
        try:
            shutil.copytree(str(source), str(stage / "pkg"), symlinks=False,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            _validate_tree(stage / "pkg")
            try:
                os.rename(str(stage / "pkg"), str(target))
            except OSError:
                if not (target / "hooks" / "run.py").is_file():
                    raise
        finally:
            shutil.rmtree(str(stage), ignore_errors=True)
    _validate_tree(target)

    pycache = cache / "pycache"
    runner = cache / RUNNER_NAME
    _atomic_write(runner, render_run_sh(target, pycache, version).encode("utf-8"), 0o755)

    hook_map = generate_hook_map(load_hooks_json(target), runner)
    settings_path = claude / "settings.json"
    changed = write_settings_hooks(settings_path, hook_map)

    if precompile:
        try:
            _precompile(target, pycache)
        except Exception:  # noqa: BLE001 - warm start is an optimization only
            pass

    pruned = []
    for child in cache.iterdir():
        if child.is_dir() and _VERSION_RE.match(child.name) and child.name != version \
                and not child.is_symlink():
            shutil.rmtree(str(child), ignore_errors=True)
            pruned.append(child.name)

    return {"version": version, "root": str(target), "runner": str(runner),
            "settings": str(settings_path), "settings_changed": changed,
            "events": sorted(hook_map), "pruned": pruned}


def uninstall(cache=None, claude=None) -> dict:
    cache = Path(cache) if cache else cache_dir()
    claude = Path(claude) if claude else claude_dir()
    changed = write_settings_hooks(claude / "settings.json", None)
    removed = []
    if cache.is_dir():
        for child in cache.iterdir():
            if child.is_dir() and not child.is_symlink() and (
                    _VERSION_RE.match(child.name) or child.name in ("pycache",)
                    or child.name.startswith(".stage-")):
                shutil.rmtree(str(child), ignore_errors=True)
                removed.append(child.name)
        for name in (RUNNER_NAME, "install.log"):
            p = cache / name
            if p.is_file():
                p.unlink()
                removed.append(name)
        try:
            cache.rmdir()
        except OSError:
            pass
    return {"settings_changed": changed, "removed": sorted(removed)}


# --------------------------------------------------------------- setup script


def tarball_url(version: str, registry: str = REGISTRY) -> str:
    return "%s/%s/-/token-optimizer-%s.tgz" % (registry.rstrip("/"), PACKAGE, version)


def integrity_of_file(path: Path) -> str:
    h = hashlib.sha512()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return "sha512-" + base64.b64encode(h.digest()).decode("ascii")


def registry_dist(version: str, registry: str = REGISTRY, timeout: float = 15.0) -> dict:
    url = "%s/%s/%s" % (registry.rstrip("/"), urllib.parse.quote(PACKAGE, safe="@"), version)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed https host
            meta = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise KitError("could not read %s@%s from %s (%s). Is this version published on npm?"
                       % (PACKAGE, version, registry, exc)) from None
    dist = meta.get("dist") or {}
    integrity = " ".join(i for i in str(dist.get("integrity", "")).split() if i.startswith("sha512-"))
    if not integrity:
        raise KitError("registry has no sha512 integrity for %s@%s" % (PACKAGE, version))
    return {"tarball": dist.get("tarball") or tarball_url(version, registry),
            "integrity": integrity.split()[0]}


# The verify-then-run core shared by both channels. $U = tarball URL, $I = the
# sha512 integrity string. install-cloud.sh only runs after the hash matched.
_VERIFY_RUN = (
    'curl -fsSL --retry 2 --max-time 120 -o t.tgz "$U" && '
    '[ "sha512-$(openssl dgst -sha512 -binary t.tgz | base64 | tr -d \'\\n\')" = "$I" ] && '
    'tar -xzf t.tgz && sh package/install-cloud.sh'
)


def render_setup_script(version: str, integrity: str, url: str) -> str:
    """Pinned: the paste carries the hash, so the code that runs is fixed."""
    for v, label in ((version, "version"), (integrity, "integrity")):
        if "'" in v or "\n" in v:
            raise KitError("bad %s" % label)
    if not _VERSION_RE.match(version) or not integrity.startswith("sha512-"):
        raise KitError("bad version or integrity")
    return (
        "# Token Optimizer %s for Claude Code cloud sessions: fetches the npm tarball, checks its\n"
        "# sha512, runs its install-cloud.sh. Quiet and never fails the setup (log: ~/.cache/%s/install.log).\n"
        "( U=%s I=%s T=\"$(mktemp -d)\" && cd \"$T\" && %s; cd /; rm -rf \"$T\" ) >/dev/null 2>&1 || true\n"
        % (version, CACHE_DIRNAME, shlex.quote(url), shlex.quote(integrity), _VERIFY_RUN)
    )


def render_setup_script_latest(registry: str = REGISTRY) -> str:
    """Follows the `latest` dist-tag; the hash is the registry's own integrity."""
    meta = "%s/%s/latest" % (registry.rstrip("/"), urllib.parse.quote(PACKAGE, safe="@"))
    prefix = "%s/%s/-/" % (registry.rstrip("/"), PACKAGE)
    py = ('import json,sys;d=json.load(open(sys.argv[1]))["dist"];'
          'print(d["tarball"],[i for i in d["integrity"].split() if i.startswith("sha512-")][0])')
    return (
        "# Token Optimizer (latest on npm) for Claude Code cloud sessions: checks the tarball against\n"
        "# the sha512 the registry publishes. Quiet and never fails the setup (log: ~/.cache/%s/install.log).\n"
        "( T=\"$(mktemp -d)\" && cd \"$T\" && curl -fsSL --max-time 30 -o m.json %s && "
        "set -- $(python3 -I -c %s m.json) && U=\"$1\" I=\"$2\" && [ \"${U#%s}\" != \"$U\" ] && %s; "
        "cd /; rm -rf \"$T\" ) >/dev/null 2>&1 || true\n"
        % (CACHE_DIRNAME, shlex.quote(meta), shlex.quote(py), prefix, _VERIFY_RUN)
    )


# --------------------------------------------------------------------- doctor


_TO_KEY_RE = re.compile(r"^token-optimizer[^@]*@")


def _same_dir(a, b) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, RuntimeError, ValueError, TypeError):
        return False


def _enabled_flag(path: Path, key: str):
    """enabledPlugins[key] from one settings file: True / False / None (unset)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    flags = data.get("enabledPlugins") if isinstance(data, dict) else None
    value = flags.get(key) if isinstance(flags, dict) else None
    return value if isinstance(value, bool) else None


def _registry_entry_active(key: str, records, claude: Path, project) -> bool:
    """True when one installed_plugins.json entry is a Token Optimizer install
    that Claude Code loads for THIS session: a user-scope install, or a
    project/local-scope one whose projectPath is this project, and not turned
    off in enabledPlugins (project settings beat user settings)."""
    applies = False
    for rec in records if isinstance(records, list) else []:
        if not isinstance(rec, dict):
            continue
        scope = rec.get("scope") or "user"
        if scope in ("user", "managed"):
            applies = True
        elif scope in ("project", "local") and project and _same_dir(rec.get("projectPath") or "", project):
            applies = True
    if not applies:
        return False
    layers = []
    if project:
        layers += [Path(project) / ".claude" / "settings.local.json", Path(project) / ".claude" / "settings.json"]
    layers.append(claude / "settings.json")
    for layer in layers:
        flag = _enabled_flag(layer, key)
        if flag is not None:
            return flag
    return True


def plugin_loaded(claude=None, project=None) -> str:
    """Mirror of run.sh's stand-down check. Returns the reason, '' when clear.

    Stands down only for a Token Optimizer plugin Claude Code actually loads
    here: an enabled install whose scope covers this project, or an
    account-synced copy (when account plugin sync reaches cloud VMs, the synced
    plugin runs and the kit must step aside). A disabled entry, or one scoped
    to another project, no longer silences the kit. An unreadable registry that
    names Token Optimizer still stands down: two copies of every hook is the
    worse failure."""
    claude = Path(claude) if claude else claude_dir()
    if project is None:
        project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    plugins = claude / "plugins"
    reg = plugins / "installed_plugins.json"
    try:
        text = reg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if text and re.search(r'"token-optimizer[^"@]*@', text):
        try:
            data = json.loads(text)
            entries = data.get("plugins") if isinstance(data, dict) else None
            if not isinstance(entries, dict):
                raise ValueError("no plugins map")
        except ValueError:
            return "plugin named in an unreadable %s" % reg
        for key, records in sorted(entries.items()):
            if isinstance(key, str) and _TO_KEY_RE.match(key) and \
                    _registry_entry_active(key, records, claude, project):
                return "plugin %s listed in %s" % (key, reg)
    for f in sorted(plugins.glob("synced/*/token-optimizer*/hooks/hooks.json")):
        if f.is_file():
            return "account-synced plugin at %s" % f.parents[1]
    return ""


def doctor(cache=None, claude=None) -> dict:
    cache = Path(cache) if cache else cache_dir()
    claude = Path(claude) if claude else claude_dir()
    checks = []

    def add(ok, name, detail):
        checks.append({"ok": bool(ok), "check": name, "detail": detail})

    remote = os.environ.get("CLAUDE_CODE_REMOTE", "")
    add(True, "Cloud session", "CLAUDE_CODE_REMOTE=%s" % (remote or "(unset)"))
    runner = cache / RUNNER_NAME
    add(runner.is_file(), "Runner", str(runner) if runner.is_file() else "missing: run the setup script")
    versions = sorted(c.name for c in cache.iterdir() if c.is_dir() and _VERSION_RE.match(c.name)) \
        if cache.is_dir() else []
    root = cache / versions[-1] if versions else None
    add(bool(versions), "Installed version", ", ".join(versions) or "none")
    expected = None
    if root is not None:
        try:
            expected = generate_hook_map(load_hooks_json(root), runner)
        except (OSError, ValueError, KitError) as exc:
            add(False, "Hook map", "cannot build from %s: %s" % (root, exc))
    try:
        data, _ = load_settings(claude / "settings.json")
        hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
        ours = {e: [g for g in gs if _is_ours(g)] for e, gs in hooks.items() if isinstance(gs, list)}
        ours = {e: gs for e, gs in ours.items() if gs}
        if expected is None:
            add(bool(ours), "Settings hooks", "%d events wired" % len(ours))
        else:
            add(ours == expected, "Settings hooks",
                "match hooks.json (%d events)" % len(expected) if ours == expected
                else "drift from hooks.json: re-run the setup script")
    except KitError as exc:
        add(False, "Settings hooks", str(exc))
    reason = plugin_loaded(claude)
    add(True, "Plugin also loaded", ("yes, the cloud hooks stand down (%s)" % reason) if reason else "no")
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


# ------------------------------------------------------------------------ CLI

_SETUP_USAGE = """usage: measure.py cloud-setup-script [--latest] [--version V] [--integrity sha512-...]
                                     [--from-tarball FILE] [--registry URL]

Prints a setup script for Claude Code cloud environments (claude.ai/code ->
environment settings -> Setup script). Paste it once per environment: every
session in that environment, any repo, then runs the real Token Optimizer
hooks. Default pins this version and its registry sha512. --latest prints a
script that follows npm's latest tag instead (see the docs for the tradeoff)."""


def main_setup_script(argv) -> int:
    if "-h" in argv or "--help" in argv:
        print(_SETUP_USAGE)
        return 0
    opts = {"--version": None, "--integrity": None, "--from-tarball": None, "--registry": None,
            "--tarball-url": None}
    latest = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--latest":
            latest = True
        elif a in opts and i + 1 < len(argv):
            opts[a] = argv[i + 1]
            i += 1
        else:
            print("cloud-setup-script: unknown argument %r\n\n%s" % (a, _SETUP_USAGE), file=sys.stderr)
            return 2
        i += 1
    registry = opts["--registry"] or REGISTRY
    try:
        if latest:
            sys.stdout.write(render_setup_script_latest(registry))
            return 0
        version = opts["--version"] or package_version(PLUGIN_ROOT)
        url = opts["--tarball-url"] or tarball_url(version, registry)
        if opts["--integrity"]:
            integrity = opts["--integrity"]
        elif opts["--from-tarball"]:
            integrity = integrity_of_file(Path(opts["--from-tarball"]))
        else:
            dist = registry_dist(version, registry)
            integrity, url = dist["integrity"], opts["--tarball-url"] or dist["tarball"]
        sys.stdout.write(render_setup_script(version, integrity, url))
        return 0
    except (KitError, OSError) as exc:
        print("cloud-setup-script: %s" % exc, file=sys.stderr)
        return 1


def main_doctor(argv) -> int:
    report = doctor()
    if "--json" in argv:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("Token Optimizer cloud doctor")
        for c in report["checks"]:
            print("  [%s] %s: %s" % ("OK" if c["ok"] else "!!", c["check"], c["detail"]))
    return 0 if report["ok"] else 1


def main(argv) -> int:
    """Entry for install-cloud.sh: ``cloud_kit.py install --source DIR [--cache DIR]``."""
    cmd = argv[0] if argv else ""
    args = argv[1:]
    opts = {}
    i = 0
    while i < len(args):
        if args[i] in ("--source", "--cache", "--claude-dir") and i + 1 < len(args):
            opts[args[i]] = args[i + 1]
            i += 2
        elif args[i] == "--no-precompile":
            opts["--no-precompile"] = True
            i += 1
        else:
            print("cloud_kit: unknown argument %r" % args[i], file=sys.stderr)
            return 2
    try:
        if cmd == "install":
            report = install(Path(opts.get("--source") or PLUGIN_ROOT), opts.get("--cache"),
                             opts.get("--claude-dir"), precompile="--no-precompile" not in opts)
        elif cmd == "uninstall":
            report = uninstall(opts.get("--cache"), opts.get("--claude-dir"))
        elif cmd == "doctor":
            return main_doctor(args)
        elif cmd == "plugin-active":
            # run.sh's stand-down: exit 0 = a loaded plugin owns the hooks.
            return 0 if plugin_loaded() else 1
        else:
            print("usage: cloud_kit.py install|uninstall|doctor|plugin-active [--source DIR] [--cache DIR] "
                  "[--claude-dir DIR]", file=sys.stderr)
            return 2
    except (KitError, OSError) as exc:
        print("cloud_kit %s: %s" % (cmd, exc), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
