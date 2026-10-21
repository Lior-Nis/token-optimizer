"""Which statusLine commands Token Optimizer treats as its own.

Ownership decides whether a SessionStart heal may rewrite the status line and
whether cleanup may delete it, so a foreign status line must never match just
because "token-optimizer" appears somewhere in its path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import measure  # noqa: E402


@pytest.mark.parametrize("cmd", [
    "node '/Users/a/.claude/plugins/cache/alexgreensh-token-optimizer/token-optimizer/5.13.55/skills/token-optimizer/scripts/statusline.js'",
    "node '/Users/a/.claude/plugins/marketplaces/alexgreensh-token-optimizer/skills/token-optimizer/scripts/statusline.js'",
    "node /home/a/dev/token-optimizer/skills/token-optimizer/scripts/statusline.js",
    'node "C:\\Users\\a\\.claude\\plugins\\cache\\x\\token-optimizer\\5.13.55\\skills\\token-optimizer\\scripts\\statusline.js"',
    "node '/p/token-optimizer/statusline.js'",
])
def test_our_statusline_commands_are_ours(cmd):
    assert measure._statusline_cmd_is_ours(cmd)


@pytest.mark.parametrize("cmd", [
    # Another tool's script under a folder that merely contains the name.
    "node '/Users/token-optimizer/gone/other-tool/statusline.js'",
    "node '/work/token-optimizer-notes/statusline.js'",
    "node /srv/token-optimizer/projects/mine/bin/statusline.js",
    "npx -y ccstatusline@latest",
    "",
    None,
])
def test_foreign_statusline_commands_are_not_ours(cmd):
    assert not measure._statusline_cmd_is_ours(cmd)


def test_cleanup_keeps_a_foreign_statusline_under_a_token_optimizer_folder():
    settings = {"statusLine": {"type": "command",
                               "command": "node '/Users/token-optimizer/tools/statusline.js'"}}
    removed = measure._remove_our_settings_entries(settings)
    assert "statusLine" in settings and not any("statusLine" in r for r in removed)
