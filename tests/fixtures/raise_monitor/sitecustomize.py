"""Test-only: record every exception RAISED inside Token Optimizer code.

Loaded through PYTHONPATH by tests/test_swallowed_errors.py, so it reaches every
Python process a real hook command starts (launcher -> run.py -> module_runner
-> the script, and any worker those spawn). The product is fail-open on
purpose: broad ``except`` blocks keep a hook from ever breaking a session. That
is also how #246 hid a NameError for months. This module sees the raise even
when the product swallows it, so a test can tell "failed open on a real-world
condition" apart from "a programming error that a broad except is hiding".

Inert unless TO_TEST_RAISE_LOG and TO_TEST_RAISE_ROOT are both set. Uses
sys.monitoring (3.12+), which costs nothing for code that never raises; on an
older interpreter it falls back to sys.settrace on exception events only.
"""

import json
import os
import sys

_LOG = os.environ.get("TO_TEST_RAISE_LOG")
_ROOT = os.environ.get("TO_TEST_RAISE_ROOT")
_SKIP = (StopIteration, StopAsyncIteration, GeneratorExit, KeyboardInterrupt, SystemExit)


def _chain_original_sitecustomize():
    """Run the interpreter's own sitecustomize (e.g. Homebrew's) if there is one."""
    here = os.path.dirname(os.path.abspath(__file__))
    for entry in sys.path:
        try:
            if os.path.abspath(entry or ".") == here:
                continue
            cand = os.path.join(entry, "sitecustomize.py")
            if os.path.isfile(cand):
                with open(cand, encoding="utf-8") as fh:
                    exec(compile(fh.read(), cand, "exec"), {"__name__": "sitecustomize", "__file__": cand})
                return
        except Exception:
            return


if _LOG and _ROOT:
    _root = os.path.realpath(_ROOT) + os.sep
    _seen = set()

    def _record(filename, func, line, exc):
        if isinstance(exc, _SKIP):
            return
        try:
            real = os.path.realpath(filename)
        except Exception:
            return
        if not real.startswith(_root):
            return
        key = (type(exc).__name__, real, line)
        if key in _seen:
            return
        _seen.add(key)
        rec = {
            "type": type(exc).__name__,
            "mro": [c.__name__ for c in type(exc).__mro__],
            "msg": str(exc)[:300],
            "file": real[len(_root):],
            "func": func,
            "line": line,
            "argv": sys.argv[:4],
            "pid": os.getpid(),
        }
        try:
            fd = os.open(_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, (json.dumps(rec) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        except Exception:
            pass

    _mon = getattr(sys, "monitoring", None)
    if _mon is not None:
        _tool = None
        for _cand in (3, 4):
            try:
                _mon.use_tool_id(_cand, "to-raise-monitor")
                _tool = _cand
                break
            except ValueError:
                continue
        if _tool is not None:
            def _on_raise(code, offset, exc):
                line = code.co_firstlineno
                try:
                    for start, end, ln in code.co_lines():
                        if start <= offset < end and ln is not None:
                            line = ln
                            break
                except Exception:
                    pass
                _record(code.co_filename, code.co_name, line, exc)

            _mon.register_callback(_tool, _mon.events.RAISE, _on_raise)
            _mon.set_events(_tool, _mon.events.RAISE)
    else:  # pragma: no cover - Python < 3.12
        def _local(frame, event, arg):
            if event == "exception":
                _record(frame.f_code.co_filename, frame.f_code.co_name, frame.f_lineno, arg[1])
            return _local

        def _global(frame, event, arg):
            if frame.f_code.co_filename.startswith(_ROOT):
                return _local
            return None

        sys.settrace(_global)
        try:
            import threading
            threading.settrace(_global)
        except Exception:
            pass

_chain_original_sitecustomize()
