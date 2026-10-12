#!/usr/bin/env python3
"""Token Optimizer v5: PreToolUse Bash Hook.

On Claude Code this hook is always silent. It used to rewrite whitelisted
commands through bash_compress.py and return permissionDecision "allow" so the
rewrite ran without a prompt. A hook "allow" approves the call outright: it
skips the user's permission prompt and the auto mode classifier, and the
whitelist includes commands that can delete (`find -delete`, cloud CLIs,
`git branch -D`). Bash output is compressed after the command has run, by the
PostToolUse bash_compress_hook.py, which takes no part in permissions.

This module still holds the shared gates the other host bridges import.

Exit behavior:
- No output, always = no decision, the normal permission flow applies
"""

from plugin_env import is_v5_flag_enabled

# The whitelist gate lives in a dependency-free module so the compression
# wrapper's startup self-check can import it without this module's
# environment-resolution import chain.
from bash_whitelist import (
    _DANGEROUS_CHARS,
    _GIT_WRITE_SUBCMDS,
    _SAFE_ENV_VARS,
    _WHITELIST_COMPOUND,
    _WHITELIST_SINGLE,
    has_dangerous_chars as _has_dangerous_chars_impl,
    is_auto_approvable as _is_auto_approvable_impl,
    is_whitelisted as _is_whitelisted_impl,
)


def _has_dangerous_chars(command_str):
    """Check if command contains shell metacharacters."""
    return _has_dangerous_chars_impl(command_str)


def _is_whitelisted(command_str):
    """Check if command matches the compression whitelist."""
    return _is_whitelisted_impl(command_str)


def _is_auto_approvable(command_str):
    """Check if a command is safe for a rewrite that also approves the call."""
    return _is_auto_approvable_impl(command_str)


def _is_bash_compress_enabled():
    """Check if bash compression is enabled. Default ON since v5.5."""
    return is_v5_flag_enabled("v5_bash_compress", "TOKEN_OPTIMIZER_BASH_COMPRESS", default=True)


def main():
    # Never print: any PreToolUse output here would be a permission decision.
    return


if __name__ == "__main__":
    main()
