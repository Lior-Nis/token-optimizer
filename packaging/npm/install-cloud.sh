#!/bin/sh
# Token Optimizer: install for Claude Code cloud sessions.
#
# Run by the cloud environment's setup script after the tarball's sha512 was
# checked (see `measure.py cloud-setup-script`). Copies this package to
# ~/.cache/token-optimizer-cloud/<version>, writes the runner, and merges the
# hook map generated from hooks/hooks.json into ~/.claude/settings.json.
#
# Always exits 0 and prints nothing: a failing setup script blocks the cloud
# session. Details go to ~/.cache/token-optimizer-cloud/install.log.
S=$(cd "$(dirname "$0")" 2>/dev/null && pwd) || exit 0
C="${TOKEN_OPTIMIZER_CLOUD_HOME:-${HOME:-/root}/.cache/token-optimizer-cloud}"
mkdir -p "$C" 2>/dev/null || exit 0
{
  date -u '+%Y-%m-%dT%H:%M:%SZ install-cloud.sh start'
  if command -v python3 >/dev/null 2>&1; then
    python3 -I -B "$S/skills/token-optimizer/scripts/cloud_kit.py" install --source "$S" --cache "$C" "$@" </dev/null
    echo "exit $?"
  else
    echo "python3 not found; Token Optimizer needs Python 3.9+"
  fi
} >>"$C/install.log" 2>&1
exit 0
