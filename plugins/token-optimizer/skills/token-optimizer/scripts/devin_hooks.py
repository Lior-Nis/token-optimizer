"""Automatic Devin compression: eligibility, permission proof and hook config.

Devin CLI runs Claude-style hooks. A PreToolUse hook may rewrite an ``exec``
command through ``hookSpecificOutput.updatedInput``, but Devin then checks
permissions against the REWRITTEN command: an approved ``git log`` would turn
into a prompt for the Python wrapper. The PermissionRequest hook clears that
prompt, under two hard conditions:

1. Binding. It approves only the exact wrapper bytes the PreToolUse hook
   produced for the same ``tool_use_id``, once. Any other command, including
   any other wrapper-shaped Python command, gets no decision.
2. Proof. A command is rewritten only when Devin's own documented rules show
   it would have run WITHOUT a prompt in any mode: a user or project
   ``allow`` rule covers it, and no ``deny`` or ``ask`` rule at any readable
   level touches it or the interpreter. Devin's built-in read-only classifier
   is undocumented and organization rules are never stored locally, so
   neither counts as proof: the owner must attest at install time that no
   team policy applies. Any unreadable rule file means no rewrite.

Token Optimizer's whitelist only decides whether output is worth
compressing; it never stands in for a permission decision.

Pure logic only: no command execution, no output. The hook entrypoint and the
runner live in devin_compress.py; the installer and doctor share the config
helpers below.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex

HOOK_EVENTS = {'PreToolUse': 'pre-tool-use', 'PermissionRequest': 'permission-request'}
HOOK_MATCHER = '^exec$'
HOOK_TIMEOUT = 10
BINDING_SECONDS = 600
BINDING_LIMIT = 256
TOOL_USE_ID_RE = re.compile(r'[A-Za-z0-9:#._-]{1,256}')
ATTESTATION = 'no-team-policy-attested'
RECORD = 'hooks.json'
BINDINGS = 'hook-bindings.json'
SESSION_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
DEFAULT_SESSION = 'devin-auto'
MAX_RULE_FILE = 1024 * 1024
MAX_WALK = 64


def _gates():
    import bash_whitelist
    return bash_whitelist


def _compression_enabled() -> bool:
    try:
        from plugin_env import is_v5_flag_enabled
        return is_v5_flag_enabled('v5_bash_compress', 'TOKEN_OPTIMIZER_BASH_COMPRESS', default=True)
    except Exception:
        return False


def session_label(raw) -> str:
    return raw if isinstance(raw, str) and SESSION_RE.fullmatch(raw) else DEFAULT_SESSION


# ── Devin permission rules ──────────────────────────────────────────────────

class RulesUnknown(Exception):
    """A rule file exists but cannot be read or parsed: decide nothing."""


def _read_rules(path: Path) -> dict | None:
    try:
        if not path.exists() and not path.is_symlink():
            return None
        if path.stat().st_size > MAX_RULE_FILE:
            raise RulesUnknown(str(path))
        record = json.loads(path.read_text(encoding='utf-8'))
    except RulesUnknown:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise RulesUnknown(str(path)) from exc
    if not isinstance(record, dict):
        raise RulesUnknown(str(path))
    permissions = record.get('permissions', {})
    if not isinstance(permissions, dict):
        raise RulesUnknown(str(path))
    rules = {}
    for kind in ('allow', 'ask', 'deny'):
        values = permissions.get(kind, [])
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise RulesUnknown(str(path))
        rules[kind] = values
    return rules


def _project_rule_files(start: Path) -> list:
    """Project rule files from start up to the repository root, never past HOME."""
    files, folder = [], start
    stop = Path.home().resolve()
    for _ in range(MAX_WALK):
        for name in ('config.json', 'config.local.json'):
            files.append(folder / '.devin' / name)
        if (folder / '.git').exists() or folder.parent == folder or folder.resolve() == stop:
            break
        folder = folder.parent
    else:
        # Ran out of steps before the root: an unvisited ancestor may hold a
        # deny rule, so the rules are unknown, not empty.
        raise RulesUnknown(str(start))
    return files


def user_rule_files(user_config: Path) -> list:
    return [user_config, user_config.with_name('config.local.json')]


def rule_files(user_config: Path, cwd: Path, project_dir: str | None = None) -> list:
    """Every file whose deny/ask rules can block: the user's and the project's."""
    files = user_rule_files(user_config)
    starts = [cwd]
    if project_dir and os.path.isabs(project_dir):
        starts.append(Path(project_dir))
    for start in starts:
        for path in _project_rule_files(start):
            if path not in files:
                files.append(path)
    return files


def load_rules(paths, allow_from=None) -> dict:
    """deny/ask from every file; allow only from allow_from (default: all).

    Allow is the proof that Devin runs a command without a prompt. A cloned
    repository's .devin/config.json must never supply that proof, so callers
    pass the user's own files as allow_from; project files can only block."""
    merged = {'allow': [], 'ask': [], 'deny': []}
    for path in paths:
        rules = _read_rules(path)
        if rules:
            for kind in merged:
                if kind == 'allow' and allow_from is not None and path not in allow_from:
                    continue
                merged[kind].extend(rules[kind])
    return merged


def load_effective_rules(user_config: Path, cwd: Path, project_dir: str | None = None) -> dict:
    return load_rules(rule_files(user_config, cwd, project_dir), allow_from=user_rule_files(user_config))


_EXEC_RULE = re.compile(r'(?i)exec(?:\((.*)\))?')


def _rule_prefix(rule: str):
    """None = not an exec rule; [] = every command; else prefix tokens."""
    match = _EXEC_RULE.fullmatch(rule.strip())
    if not match:
        return None
    inner = (match.group(1) or '').strip()
    if not inner or any(ch in inner for ch in '*?['):
        return []
    try:
        return shlex.split(inner)
    except ValueError:
        return []


def _prefix_matches(prefix, tokens) -> bool:
    return len(tokens) >= len(prefix) and tokens[:len(prefix)] == prefix


def _touches_interpreter(prefix, interpreter: str) -> bool:
    if not prefix:
        return True
    head = prefix[0]
    base = os.path.basename(head)
    return head == interpreter or base == os.path.basename(interpreter) or base.startswith('python')


def blocked_by_rules(tokens, rules, interpreter: str) -> bool:
    """True when any deny/ask rule touches the same program or the wrapper.

    Deliberately broader than Devin's own matching: `ask Exec(grep -r)` also
    stops `grep -rn`, since flag spellings are not compared exactly."""
    for rule in rules['deny'] + rules['ask']:
        prefix = _rule_prefix(rule)
        if prefix is None:
            continue
        if not prefix or prefix[0] == tokens[0] or _touches_interpreter(prefix, interpreter):
            return True
    return False


def allowed_by_rules(tokens, rules) -> bool:
    for rule in rules['allow']:
        prefix = _rule_prefix(rule)
        if prefix and _prefix_matches(prefix, tokens):
            return True
    return False


# ── Eligibility ─────────────────────────────────────────────────────────────

def has_shell_expansion(command: str) -> bool:
    """Unquoted glob, tilde or comment characters. The shell would expand or
    drop them, the argv runner would not, so the rewrite would change what
    runs (`ls *.txt` would look for a file literally named '*.txt')."""
    quote = None
    escaped = False
    for ch in command:
        if escaped:
            escaped = False
        elif ch == '\\' and quote != "'":
            escaped = True
        elif quote:
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch in '*?[]~#!':
            return True
    return False


def machine_format(tokens) -> bool:
    """git output the caller explicitly asked to be compact or machine-readable
    (--short, --porcelain, -z and short clusters holding s or z). Claude Code's
    router has no handler for these and would fall back to the generic
    summarizer; here they are simply never rewritten, so they run untouched."""
    if len(tokens) < 2 or tokens[0] != 'git':
        return False
    for arg in tokens[1:]:
        if arg.startswith(('--porcelain', '--short', '--null')) or arg == '-z':
            return True
        if tokens[1] == 'status' and arg.startswith('-') and not arg.startswith('--') and ('s' in arg or 'z' in arg):
            return True
    return False


def eligible(command: str, rules: dict, interpreter: str) -> bool:
    """May this command be rewritten AND its bound wrapper approved?"""
    if not isinstance(command, str) or not command.strip() or not _compression_enabled():
        return False
    gates = _gates()
    if gates.has_dangerous_chars(command) or has_shell_expansion(command) or not gates.is_whitelisted(command):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if (not tokens or '' in tokens or '=' in tokens[0] or machine_format(tokens)
            or blocked_by_rules(tokens, rules, interpreter)):
        return False
    return allowed_by_rules(tokens, rules)


def runner_eligible(argv) -> bool:
    """The runner's own re-check: compressible and free of shell syntax."""
    if not argv or not all(isinstance(a, str) and a for a in argv):
        return False
    command = shlex.join(argv)
    gates = _gates()
    return not gates.has_dangerous_chars(command) and gates.is_whitelisted(command)


# ── Wrapper strings ─────────────────────────────────────────────────────────

def build_wrapper(interpreter: str, entry: str, session: str, argv) -> str:
    return shlex.join([interpreter, '-I', entry, 'run', '--session', session_label(session), '--', *argv])


def parse_wrapper(command: str, interpreter: str, entry: str):
    """Return the wrapped argv for an exact wrapper string, else None."""
    if not isinstance(command, str):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if (len(tokens) < 8 or tokens[:4] != [interpreter, '-I', entry, 'run'] or tokens[4] != '--session'
            or not SESSION_RE.fullmatch(tokens[5]) or tokens[6] != '--'):
        return None
    argv = tokens[7:]
    if build_wrapper(interpreter, entry, tokens[5], argv) != command:
        return None
    return argv


# ── Devin user config: owned hook entries ───────────────────────────────────

def hook_command(interpreter: str, entry: str, event: str) -> str:
    # Devin reads hook exit 2 as "block", and Python exits 2 when the entry
    # file is missing (a stale entry after a manual removal). The shell guard
    # turns every failure into a silent pass, so a broken hook never blocks.
    return shlex.join([interpreter, '-I', entry, 'hook', HOOK_EVENTS[event]]) + ' || true'


def owned_entries(interpreter: str, entry: str) -> dict:
    return {event: {'matcher': HOOK_MATCHER,
                    'hooks': [{'type': 'command', 'command': hook_command(interpreter, entry, event),
                               'timeout': HOOK_TIMEOUT}]}
            for event in HOOK_EVENTS}


def _entry_key(value) -> str:
    return json.dumps(value, sort_keys=True)


def merge_hooks(config: dict, entries: dict, previous: dict | None = None) -> dict:
    """Add owned entries; replace only previously recorded, byte-equal entries."""
    if not isinstance(config, dict):
        raise ValueError('Devin config must be a JSON object')
    hooks = config.get('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('Devin config "hooks" must be an object')
    stale = {_entry_key(v) for v in (previous or {}).values()}
    result = dict(config)
    hooks = dict(hooks)
    for event, entry in entries.items():
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise ValueError('Devin config hooks.' + event + ' must be a list')
        kept = [g for g in groups if _entry_key(g) not in stale]
        # Already present: leave every group where it is (reinstall is a no-op).
        hooks[event] = kept if any(_entry_key(g) == _entry_key(entry) for g in kept) else kept + [entry]
    result['hooks'] = hooks
    return result


def remove_hooks(config: dict, recorded: dict) -> tuple[dict, list]:
    """Remove only byte-equal recorded entries. Returns (config, preserved events)."""
    if not isinstance(config, dict):
        raise ValueError('Devin config must be a JSON object')
    hooks = config.get('hooks')
    if not isinstance(hooks, dict):
        return config, []
    wanted = {_entry_key(v) for v in recorded.values()}
    result, hooks, preserved = dict(config), dict(hooks), []
    for event in recorded:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept = [g for g in groups if _entry_key(g) not in wanted]
        if any(_looks_owned(g) for g in kept):
            preserved.append(event)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if hooks:
        result['hooks'] = hooks
    else:
        result.pop('hooks', None)
    return result, preserved


def _looks_owned(group) -> bool:
    text = json.dumps(group)
    return 'devin_compress.py' in text and ' hook ' in text


def installed_state(config: dict, entries: dict) -> dict:
    """Per-event: 'wired', 'missing' or 'edited' (a TO-looking entry that differs)."""
    hooks = config.get('hooks') if isinstance(config, dict) else None
    hooks = hooks if isinstance(hooks, dict) else {}
    state = {}
    for event, entry in entries.items():
        groups = hooks.get(event) if isinstance(hooks.get(event), list) else []
        if any(_entry_key(g) == _entry_key(entry) for g in groups):
            state[event] = 'wired'
        elif any(_looks_owned(g) for g in groups):
            state[event] = 'edited'
        else:
            state[event] = 'missing'
    return state


# ── One-time binding between PreToolUse and PermissionRequest ───────────────

def _digest(command: str) -> str:
    import hashlib
    return hashlib.sha256(command.encode('utf-8', 'surrogatepass')).hexdigest()


def _with_bindings(path: Path, change):
    """Run change(entries, now) under an exclusive lock and persist the result."""
    import fcntl
    import tempfile
    import time
    fd = os.open(path.with_name(path.name + '.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            entries = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError):
            entries = {}
        if not isinstance(entries, dict):
            entries = {}
        now = time.time()
        entries = {k: v for k, v in entries.items()
                   if isinstance(k, str) and isinstance(v, list) and len(v) == 2
                   and isinstance(v[0], str) and isinstance(v[1], (int, float))
                   and 0 <= now - v[1] <= BINDING_SECONDS}
        result = change(entries, now)
        if len(entries) > BINDING_LIMIT:
            for key in sorted(entries, key=lambda k: entries[k][1])[:len(entries) - BINDING_LIMIT]:
                del entries[key]
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as out:
            json.dump(entries, out)
            temporary = out.name
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        return result
    finally:
        os.close(fd)


def bind(path: Path, tool_use_id, wrapper: str) -> bool:
    if not isinstance(tool_use_id, str) or not TOOL_USE_ID_RE.fullmatch(tool_use_id):
        return False

    def change(entries, now):
        entries[tool_use_id] = [_digest(wrapper), now]
        return True
    return _with_bindings(path, change)


def consume(path: Path, tool_use_id, command: str) -> bool:
    """True once, only for the exact wrapper bound to this tool_use_id."""
    if not isinstance(tool_use_id, str) or not TOOL_USE_ID_RE.fullmatch(tool_use_id) or not path.exists():
        return False

    def change(entries, now):
        entry = entries.pop(tool_use_id, None)
        return entry is not None and entry[0] == _digest(command)
    return _with_bindings(path, change)


def attested(record_path: Path) -> bool:
    """The install record exists and carries the owner's no-team-policy attestation."""
    try:
        record = json.loads(record_path.read_text()) if record_path.exists() else None
    except (OSError, ValueError, RecursionError):
        return False
    return isinstance(record, dict) and record.get('org_policy') == ATTESTATION
