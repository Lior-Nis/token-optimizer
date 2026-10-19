#!/usr/bin/env python3
"""Prove the regression guards still catch the regressions they exist for.

Each entry re-introduces one bug that has shipped before (in a throwaway copy
of the repo), runs that entry's guard tests against the copy, and requires them
to FAIL. A guard that passes on the broken code is decoration, so the check
fails. Two more ways a guard can silently stop guarding also fail the check:

  * stale mutation: the `old` text no longer appears in the file (code moved or
    was reworded). Nothing gets planted, so nothing is proven. Update the entry.
  * guard already red: the guard tests fail on the UNMUTATED code. Then every
    mutation would look "caught". The tests are run once on clean code first.

A mutation only counts as caught when pytest reports test failures (exit 1).
A collection error, a bad node id ("no tests ran") or a crash is not proof.

Mutations come from MUTATIONS below and from every scripts/mutations/*.py
(files starting with "_" are skipped), each exporting:

    MUTATIONS = [(name, [test node ids], file_relpath, [(old, new), ...])]

file_relpath is relative to the repo root (e.g.
"skills/token-optimizer/scripts/measure.py"). Each (old, new) replaces the first
occurrence of `old`, in order, so a later edit sees the earlier ones.

Run from anywhere:  python3 scripts/check_regression_guards.py
  --list            print every mutation and exit
  --only TEXT       run only mutations whose name or file contains TEXT (repeatable)
  --jobs N          run N mutations in parallel, each in its own copy (default 1)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MUTATIONS_DIR = REPO / "scripts" / "mutations"
MEASURE = "skills/token-optimizer/scripts/measure.py"

# (name, tests that must catch it, file, [(old, new), ...])
MUTATIONS = [
    ("baseline slides onto post-install months",
     ["tests/test_golden_user.py", "tests/test_pinned_baseline.py", "tests/test_weekly_full_savings.py"],
     MEASURE,
     [("            pinned = _pinned_workload_anchor()\n", "            pinned = None\n"),
      ('str(frozen.get("month") or "") <= month', 'str(frozen.get("month") or "") == month')]),
    ("re-reads priced at the family card, not the exact model",
     ["tests/test_golden_user.py"],
     MEASURE,
     [("key = _claude_price_key(model, models) if model else None",
       "key = _normalize_model_name(model) if model else None")]),
    ("savings report leaves out avoided re-reads",
     ["tests/test_golden_user.py", "tests/test_pinned_baseline.py"],
     MEASURE,
     [('    summary["reread_avoided"] = _reread_savings_for_window(days)\n', "")]),
    ("one session stored under two paths counted twice",
     ["tests/test_golden_user.py", "tests/test_cost_accuracy_200.py"],
     MEASURE,
     [("            if any(_session_rank(r[1], r[2], r[3]) >= mine for r in other):", "            if False:"),
      ("def _dedupe_session_rows(conn):", "def _dedupe_session_rows(conn):\n    return 0\n\n\ndef _unused_dedupe(conn):"),
      ('                conn.executemany("DELETE FROM session_log WHERE id = ?", [(r[0],) for r in other])',
       "                pass")]),
    ("a flat whole-workload week hides the measured savings",
     ["tests/test_weekly_full_savings.py"],
     MEASURE,
     [('return value if float((value or {}).get("saved_usd") or 0.0) > 0 else None', "return value"),
      ('if wdays == 7 and weekly_full and float(weekly_full.get("saved_usd") or 0.0) > ctx + rt:',
       "if wdays == 7 and weekly_full:")]),
    ("a growing session keeps its stale per-day usage",
     ["tests/test_golden_user.py"],
     MEASURE,
     [("               daily_usage_json=excluded.daily_usage_json",
       "               daily_usage_json=session_log.daily_usage_json")]),
    ("re-collecting a grown session adds its old tokens again",
     ["tests/test_golden_user.py"],
     MEASURE,
     [("               input_tokens=excluded.input_tokens,",
       "               input_tokens=session_log.input_tokens + excluded.input_tokens,")]),
    ("stored re-read rows keep a pricing bug after it is fixed",
     ["tests/test_golden_user.py"],
     MEASURE,
     [("        _reprice_counted_rows_once(conn)\n", "")]),
    ("a thinner history silently shrinks the frozen baseline",
     ["tests/test_baseline_safety.py"],
     MEASURE,
     [("def _baseline_shrink_is_material(incumbent, candidate):",
       "def _baseline_shrink_is_material(incumbent, candidate):\n    return False\n\n\ndef _unused_shrink(incumbent, candidate):")]),
    ("a generation model priced at its family card",
     ["tests/test_bundled_prices.py", "tests/test_cost_accuracy_200.py"],
     MEASURE,
     [("    m = _CLAUDE_GENERATION_RE.search(model_id.lower())\n    if m:",
       "    m = None\n    if m:")]),
    ("cwd/project mismatch warning printed to stdout (model context, broken hook JSON)",
     ["tests/test_cloud_parity.py::test_cwd_mismatch_warning_goes_to_stderr"],
     MEASURE,
     [('Using most recent: {result.name}",\n          file=sys.stderr)',
       'Using most recent: {result.name}")')]),
    ("Claude Code cloud runs the desktop-only daemon / settings / welcome work",
     ["tests/test_cloud_parity.py"],
     MEASURE,
     [("            return False\n        return is_claude_code_cloud()\n",
       "            return False\n        return False\n")]),
]


class MutationError(Exception):
    """A malformed or stale mutation entry: fails the check, never skipped."""


def _validate(entry, origin: str):
    if not (isinstance(entry, (tuple, list)) and len(entry) == 4):
        raise MutationError(f"{origin}: entry must be (name, [tests], file_relpath, [(old, new), ...]), got {entry!r:.120}")
    name, tests, relpath, edits = entry
    if not isinstance(name, str) or not name.strip():
        raise MutationError(f"{origin}: name must be a non-empty string")
    where = f"{origin}: {name!r}"
    if (not isinstance(tests, (list, tuple)) or not tests
            or not all(isinstance(t, str) and t.strip() for t in tests)):
        raise MutationError(f"{where}: tests must be a non-empty list of pytest node ids")
    for t in tests:
        if not (REPO / t.split("::", 1)[0]).is_file():
            raise MutationError(f"{where}: test file {t.split('::', 1)[0]} does not exist")
    if not isinstance(relpath, str) or not relpath or os.path.isabs(relpath) or ".." in Path(relpath).parts:
        raise MutationError(f"{where}: file must be a repo-relative path, got {relpath!r}")
    if not (REPO / relpath).is_file():
        raise MutationError(f"{where}: {relpath} does not exist")
    if not isinstance(edits, (list, tuple)) or not edits:
        raise MutationError(f"{where}: needs at least one (old, new) edit")
    for edit in edits:
        if not (isinstance(edit, (list, tuple)) and len(edit) == 2
                and all(isinstance(s, str) for s in edit)):
            raise MutationError(f"{where}: each edit must be an (old, new) pair of strings")
        if not edit[0] or edit[0] == edit[1]:
            raise MutationError(f"{where}: an edit has empty `old` or old == new (plants nothing)")
    return name, list(tests), relpath, [tuple(e) for e in edits]


def _load_extra() -> list:
    """(origin, entry) for every MUTATIONS in scripts/mutations/*.py. A file that
    fails to import or lacks MUTATIONS is an error, not an empty list."""
    out = []
    if not MUTATIONS_DIR.is_dir():
        return out
    for path in sorted(MUTATIONS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        origin = f"scripts/mutations/{path.name}"
        spec = importlib.util.spec_from_file_location(f"_regression_mutations_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - surface any import failure by name
            raise MutationError(f"{origin}: failed to import: {exc!r}") from exc
        entries = getattr(module, "MUTATIONS", None)
        if not isinstance(entries, (list, tuple)):
            raise MutationError(f"{origin}: must export a MUTATIONS list")
        out.extend((origin, e) for e in entries)
    return out


def collect() -> list:
    """Every validated mutation, core first. Raises MutationError on any bad entry."""
    raw = [("check_regression_guards.py", e) for e in MUTATIONS] + _load_extra()
    seen = {}
    result = []
    for origin, entry in raw:
        m = _validate(entry, origin)
        if m[0] in seen:
            raise MutationError(f"{origin}: duplicate mutation name {m[0]!r} (also in {seen[m[0]]})")
        seen[m[0]] = origin
        result.append(m + (origin,))
    return result


def plant(text: str, edits) -> str:
    """Apply edits in order. Raises MutationError naming the first `old` that no
    longer matches: a stale mutation is a silent guard loss."""
    for i, (old, new) in enumerate(edits, 1):
        if old not in text:
            snippet = old.strip().splitlines()[0][:80] if old.strip() else repr(old)
            raise MutationError(f"edit {i} is stale: text not found: {snippet!r}")
        text = text.replace(old, new, 1)
    return text


def _copy_repo(src: Path, dst: Path) -> None:
    """Copy the tree for mutation, skipping (with a warning) anything unreadable.

    One chmod-000 file must not abort the whole check: the guards never read it.
    """
    def _copy(s, d):
        try:
            return shutil.copy2(s, d)
        except OSError as exc:
            print(f"warning: skipping unreadable file {s}: {exc}", file=sys.stderr)
            return d

    try:
        shutil.copytree(src, dst, copy_function=_copy, ignore=shutil.ignore_patterns(
            ".git", "node_modules", "docs-site", "research", "__pycache__", "*.pyc",
            ".pytest_cache", ".regression-guards-tmp"))
    except shutil.Error as exc:
        for entry in exc.args[0]:
            print(f"warning: skipping unreadable path {entry[0]}: {entry[2]}", file=sys.stderr)


def _pytest(copy_root: Path, tests: list, exitfirst: bool = True):
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests]
    if exitfirst:
        args.insert(4, "-x")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(args, cwd=copy_root, capture_output=True, text=True, timeout=900, env=env)
    return proc.returncode, proc.stdout[-3000:] + proc.stderr[-2000:]


def _run_one(mut, copy_root: Path):
    """Plant one mutation in copy_root, run its tests, restore. ('ok'|'fail', detail)."""
    name, tests, relpath, edits, _origin = mut
    target = copy_root / relpath
    original = target.read_bytes()
    try:
        mutated = plant(original.decode("utf-8"), edits)
        target.write_bytes(mutated.encode("utf-8"))
        code, out = _pytest(copy_root, tests)
    finally:
        target.write_bytes(original)
    if code == 1:
        return "ok", ""
    if code == 0:
        return "fail", "guard tests PASSED on the broken code; they no longer protect it"
    return "fail", (f"pytest exit {code} (collection error, bad node id or crash), which proves "
                    f"nothing; the mutation must make a test FAIL.\n{out}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("REGRESSION_GUARDS_JOBS", "1")))
    opts = ap.parse_args(argv)

    try:
        muts = collect()
    except MutationError as exc:
        print(f"Regression guards: bad mutation entry: {exc}", file=sys.stderr)
        return 1
    if opts.only:
        muts = [m for m in muts if any(s in m[0] or s in m[2] for s in opts.only)]
        if not muts:
            print("No mutation matches --only.", file=sys.stderr)
            return 1
    if opts.list:
        for name, tests, relpath, _edits, origin in muts:
            print(f"{origin:38} {relpath}\n    {name}\n    -> {' '.join(tests)}")
        print(f"\n{len(muts)} mutations")
        return 0

    failures = []
    # Stale check up front, against the real tree: fast and names every stale entry.
    runnable = []
    for mut in muts:
        try:
            plant((REPO / mut[2]).read_text(encoding="utf-8"), mut[3])
            runnable.append(mut)
        except MutationError as exc:
            failures.append(f"{mut[0]} ({mut[4]} -> {mut[2]}): {exc}; update this entry")
            print(f"STALE {mut[0]}")

    if not runnable:
        print("\nRegression guards are not guarding:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    started = time.monotonic()
    jobs = max(1, min(opts.jobs, len(runnable)))
    tmp_parent = os.environ.get("REGRESSION_GUARDS_TMP") or None
    if tmp_parent:
        Path(tmp_parent).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_parent, prefix="regression-guards-") as tmp:
        copies = []
        for i in range(jobs):
            root = Path(tmp) / f"repo{i}"
            _copy_repo(REPO, root)
            copies.append(root)

        # The guard tests must pass on clean code, or every mutation looks caught.
        all_tests = sorted({t for m in runnable for t in m[1]})
        if all_tests:
            code, out = _pytest(copies[0], all_tests, exitfirst=False)
            if code != 0:
                print(f"Regression guards: the guard tests fail on UNMUTATED code (pytest exit {code}),\n"
                      f"so no mutation result would mean anything. Fix them first.\n{out}", file=sys.stderr)
                return 1

        free = list(copies)
        import threading
        lock = threading.Lock()

        def work(mut):
            with lock:
                root = free.pop()
            try:
                return mut, _run_one(mut, root)
            finally:
                with lock:
                    free.append(root)

        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            for mut, (status, detail) in pool.map(work, runnable):
                if status == "ok":
                    print(f"ok    {mut[0]}")
                else:
                    print(f"FAIL  {mut[0]}")
                    failures.append(f"{mut[0]} ({mut[4]} -> {mut[2]}): {detail}")

    elapsed = time.monotonic() - started
    if failures:
        print("\nRegression guards are not guarding:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"\nAll {len(muts)} known regressions are caught ({elapsed:.0f}s, {jobs} job(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
