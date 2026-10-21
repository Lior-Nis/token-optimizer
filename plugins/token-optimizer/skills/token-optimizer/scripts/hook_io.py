"""Shared I/O utilities for Token Optimizer hook scripts.

Single source of truth for reading JSON hook input from stdin.
Each hook script runs as a separate subprocess; this module
provides a consistent, bounded stdin reader across all of them.
"""

from __future__ import annotations

import json
import sys
import threading

# The wait for the first byte, and the longest pause allowed while a payload is
# still arriving. A host that opens stdin and then goes silent costs at most this.
_STDIN_TIMEOUT = 0.5
# Hard ceiling for the whole read. A multi-MB tool result can take longer than
# _STDIN_TIMEOUT to stream on a loaded machine; reading stops only when the host
# goes quiet or this ceiling is reached, never mid-stream at the first 0.5 s.
_STDIN_TOTAL_CAP = 5.0


def _read_stdin_windows(timeout: float = _STDIN_TIMEOUT, max_bytes: int = 1_048_576) -> str:
    import time
    chunks = []
    state = {"done": False, "last": time.monotonic()}

    def _reader() -> None:
        try:
            buf = sys.stdin.buffer
            read = getattr(buf, "read1", buf.read)
            total = 0
            while total < max_bytes:
                chunk = read(min(65536, max_bytes - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                state["last"] = time.monotonic()
        except Exception:
            pass
        state["done"] = True

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    start = time.monotonic()
    # Keep waiting while bytes keep arriving; give up after `timeout` of silence
    # or at the total ceiling. A partial payload is not valid JSON, so a cut-off
    # read degrades to {} exactly as a timeout always did.
    while not state["done"]:
        t.join(0.05)
        now = time.monotonic()
        if now - state["last"] >= timeout or now - start >= _STDIN_TOTAL_CAP:
            break
    if not state["done"]:
        return ""
    return b"".join(chunks).decode("utf-8", errors="replace")


def read_stdin_hook_input(max_bytes: int = 1_048_576) -> dict:
    """Read JSON hook input from stdin non-blocking.

    Returns parsed dict or empty dict on failure.
    Bounds read size to max_bytes (default 1MB for PostToolUse payloads
    that include tool_response). PreToolUse callers can pass a lower cap.
    Works on Unix and Windows.

    C9: if the parsed JSON is valid but NOT a dict (e.g. a list, string, or
    number), prints a loud warning to stderr and returns {}. Callers do
    hook_input.get(...) which would AttributeError on a non-dict; the empty
    dict lets the existing `if not hook_input:` guards catch it.
    """
    try:
        if sys.platform == "win32":
            data = _read_stdin_windows(max_bytes=max_bytes)
        else:
            import os
            import select
            import time
            # Two clocks: the first byte must arrive within _STDIN_TIMEOUT, and
            # after that each pause may last up to _STDIN_TIMEOUT, all inside
            # one _STDIN_TOTAL_CAP deadline set BEFORE the first select. A
            # payload still streaming is never cut off at the first 0.5 s.
            deadline = time.monotonic() + _STDIN_TOTAL_CAP
            if not select.select([sys.stdin], [], [], _STDIN_TIMEOUT)[0]:
                return {}
            try:
                fd = sys.stdin.fileno()
            except (OSError, ValueError, AttributeError):
                fd = None
            if fd is not None:
                # select() only guarantees one byte is ready; a blocking
                # read(max_bytes) would then hang until max_bytes or EOF, so a
                # host that writes a partial payload and holds the pipe open
                # defeats the timeout. Read what is actually available in a loop
                # bounded by the same deadline, assembling chunked payloads
                # without ever blocking on a silent pipe past _STDIN_TIMEOUT.
                chunks = []
                total = 0
                while total < max_bytes:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    if not select.select([sys.stdin], [], [], min(_STDIN_TIMEOUT, remaining))[0]:
                        break  # the host went quiet: take what arrived
                    chunk = os.read(fd, max_bytes - total)
                    if not chunk:
                        break  # EOF: host closed stdin, payload complete
                    chunks.append(chunk)
                    total += len(chunk)
                raw = b"".join(chunks)
            else:
                # Non-fd stdin (e.g. a test double): no pipe to hang on.
                raw = sys.stdin.buffer.read(max_bytes)
            # Decode from raw bytes as UTF-8 so a non-UTF-8 host locale can't
            # corrupt or crash on non-ASCII hook payloads (e.g. Hebrew cwd /
            # tool args). Mirrors the Windows path above.
            data = raw.decode("utf-8", errors="replace")
        if not data:
            return {}
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            # Loud fail + return. A non-dict payload (list, string, number)
            # is a protocol violation; callers would AttributeError on .get().
            print(
                f"[hook_io] stdin JSON is {type(parsed).__name__}, not a dict; "
                f"treating as empty (hook will no-op)",
                file=sys.stderr,
            )
            return {}
        from runtime_env import detect_runtime
        if detect_runtime() == 'codex':
            import codex_session
            sid = codex_session._safe_session_id(parsed.get('session_id'))
            if sid:
                resolved = codex_session.resolve_session(parsed.get('transcript_path'), sid)
                # Only overwrite on success: resolve_session returns None when
                # the transcript exists but names a different session. Clobbering
                # a valid caller-provided path with None would silently lose the
                # transcript for every hook whose session_id is stale/mismatched.
                if resolved:
                    parsed['transcript_path'] = str(resolved)
        return parsed
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {}
