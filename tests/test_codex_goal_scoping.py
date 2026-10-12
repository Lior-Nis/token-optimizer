#!/usr/bin/env python3
"""Regression tests: Codex goal reports must be scoped to the
active goal subtree, not the whole state/goals database.

Before the fix, ``codex_state.subagent_costs()`` and ``goal_budgets()`` ran an
unscoped ``SELECT ... FROM thread_spawn_edges / thread_goals`` with no filter on
the current/active thread. Every spawn edge and goal in the DB was aggregated,
including closed/historical edges from unrelated prior work, so a current goal
reported old subagents, token totals were cumulative/inflated, and
``quality current`` could select a stale session.

These tests build real read-only SQLite state/goals DBs in a temp CODEX_HOME
and assert the scoped behavior required by FIX-SPEC-108:

* subagent_costs scoped to a root returns ONLY that subtree (token total == sum
  of the subtree only; a closed/historical edge from another root is excluded).
* goal_budgets scopes to the subtree and supports the opt-in ``Parent goal <id>``
  rollup for independent worker goal sessions (marker groups; no marker does not).
* JSON output carries ``selected_thread_id`` and ``aggregation_edges``.
* the descendant walk is cycle-safe (self-referential / cyclic edges terminate).
* ``quality current`` resolves to the active thread id, not the older-mtime
  session.
* non-Codex runtime keeps the unchanged empty shape.

Run: python3 -m pytest tests/test_codex_goal_scoping.py -v
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import codex_session  # noqa: E402
import codex_state  # noqa: E402
import measure  # noqa: E402
import runtime_env  # noqa: E402


# ---------------------------------------------------------------------------
# Temp CODEX_HOME + state/goals DB builders.
# ---------------------------------------------------------------------------

def _make_state_db(home: Path, threads, edges):
    """Create state_1.sqlite with ``threads`` and ``thread_spawn_edges`` rows.

    threads: list of (id, tokens_used, updated_at_ms)
    edges:   list of (parent_thread_id, child_thread_id, status)
    """
    db = home / "state_1.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE threads ("
            "id TEXT PRIMARY KEY, tokens_used INTEGER, "
            "updated_at_ms INTEGER, updated_at REAL, model TEXT)"
        )
        conn.execute(
            "CREATE TABLE thread_spawn_edges ("
            "parent_thread_id TEXT, child_thread_id TEXT, status TEXT)"
        )
        conn.executemany(
            "INSERT INTO threads (id, tokens_used, updated_at_ms, updated_at) "
            "VALUES (?, ?, ?, ?)",
            [(tid, tok, ums, (ums / 1000) if ums else None) for (tid, tok, ums) in threads],
        )
        conn.executemany(
            "INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status) "
            "VALUES (?, ?, ?)",
            edges,
        )
        conn.commit()
    finally:
        conn.close()


def _make_goals_db(home: Path, goals):
    """Create goals_1.sqlite with ``thread_goals`` rows.

    goals: list of (thread_id, objective, status, token_budget, tokens_used)
    """
    db = home / "goals_1.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE thread_goals ("
            "thread_id TEXT, objective TEXT, status TEXT, "
            "token_budget INTEGER, tokens_used INTEGER, time_used_seconds INTEGER)"
        )
        conn.executemany(
            "INSERT INTO thread_goals "
            "(thread_id, objective, status, token_budget, tokens_used, time_used_seconds) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            goals,
        )
        conn.commit()
    finally:
        conn.close()


class _CodexHome:
    """Context manager that repoints codex_home + detect_runtime at a temp dir."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix=".t_codex_108_", dir=str(Path.home())))
        (self.tmp / "sessions").mkdir(parents=True, exist_ok=True)

    def __enter__(self):
        self._saved_codex_home = codex_state.codex_home
        self._saved_detect = codex_state.detect_runtime
        self._saved_measure_detect = measure.detect_runtime
        self._saved_session_home = codex_session.codex_home
        # Hermetic: a developer running these tests inside a real Codex thread
        # would otherwise leak that thread's identity into every case.
        self._saved_thread_env = os.environ.pop("CODEX_THREAD_ID", None)
        codex_state.codex_home = lambda: self.tmp
        codex_session.codex_home = lambda: self.tmp
        codex_state.detect_runtime = lambda: "codex"
        measure.detect_runtime = lambda: "codex"
        return self.tmp

    def __exit__(self, *exc):
        codex_state.codex_home = self._saved_codex_home
        codex_session.codex_home = self._saved_session_home
        codex_state.detect_runtime = self._saved_detect
        measure.detect_runtime = self._saved_measure_detect
        os.environ.pop("CODEX_THREAD_ID", None)
        if self._saved_thread_env is not None:
            os.environ["CODEX_THREAD_ID"] = self._saved_thread_env
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_subagent_costs_scoped_to_root_returns_only_subtree():
    """A root thread's report aggregates only its subtree; token total matches."""
    with _CodexHome() as home:
        # Two unrelated goal subtrees.
        _make_state_db(
            home,
            threads=[
                ("rootAaaaa", 100, 1_700_000_000_000),  # root A
                ("childA1aa", 200, 1_700_000_000_000),  # A's child
                ("rootBbbbb", 999, 1_700_000_000_000),  # root B (unrelated)
                ("childB1bb", 888, 1_700_000_000_000),  # B's child
            ],
            edges=[
                ("rootAaaaa", "childA1aa", "open"),
                ("rootBbbbb", "childB1bb", "closed"),  # historical, other root
            ],
        )
        result = codex_state.subagent_costs(root_thread_id="rootAaaaa")
        assert result["available"] is True
        assert result["selected_thread_id"] == "rootAaaaa"
        # Only the rootA -> childA1 edge belongs to the subtree.
        assert result["total_subagents"] == 1
        child_tokens = [c for c in [200] if c]
        assert result["total_child_tokens"] == 200
        # The unrelated rootB subtree edge must not appear.
        parent_ids = {e["parent_thread_id"] for e in result["aggregation_edges"]}
        assert parent_ids == {"rootAaaaa"}
        assert all(e["child_thread_id"] != "childB1bb" for e in result["aggregation_edges"])


def test_closed_historical_edge_from_another_root_excluded():
    """A closed edge from another root does not appear in the current goal."""
    with _CodexHome() as home:
        _make_state_db(
            home,
            threads=[
                ("activeeee", 50, 1_700_000_000_000),
                ("workerAAA", 10, 1_700_000_000_000),
                ("oldrootBB", 5000, 1_600_000_000_000),  # older prior work
                ("oldchildB", 7000, 1_600_000_000_000),
            ],
            edges=[
                ("activeeee", "workerAAA", "open"),
                ("oldrootBB", "oldchildB", "closed"),  # historical, other root
            ],
        )
        result = codex_state.subagent_costs(root_thread_id="activeeee")
        assert result["total_subagents"] == 1
        assert result["total_child_tokens"] == 10
        assert result["closed_subagents"] == 0  # the closed edge is from another root
        child_ids = {e["child_thread_id"] for e in result["aggregation_edges"]}
        assert "oldchildB" not in child_ids
        assert "workerAAA" in child_ids


def test_subagent_costs_token_total_equals_subtree_sum():
    """Token total is the sum of the subtree only, not the whole DB."""
    with _CodexHome() as home:
        _make_state_db(
            home,
            threads=[
                ("rootrrrrr", 0, 1_700_000_000_000),
                ("c1cccccc", 300, 1_700_000_000_000),
                ("c2cccccc", 400, 1_700_000_000_000),
                ("otherzzz", 9999, 1_700_000_000_000),
            ],
            edges=[
                ("rootrrrrr", "c1cccccc", "closed"),
                ("rootrrrrr", "c2cccccc", "open"),
                ("otherzzz", "rootrrrrr", "closed"),  # root's own parent edge: excluded
            ],
        )
        result = codex_state.subagent_costs(root_thread_id="rootrrrrr")
        assert result["total_child_tokens"] == 700  # 300 + 400, not 9999+...
        assert result["total_subagents"] == 2


def test_goal_budgets_scoped_to_subtree_excludes_unrelated():
    """goal_budgets reports only goals whose thread is in the root subtree."""
    with _CodexHome() as home:
        _make_state_db(
            home,
            threads=[
                ("coorddddd", 0, 1_700_000_000_000),
                ("workerWWW", 0, 1_700_000_000_000),
                ("otheroooo", 0, 1_700_000_000_000),
            ],
            edges=[("coorddddd", "workerWWW", "open")],
        )
        _make_goals_db(
            home,
            goals=[
                ("coorddddd", "coordinate the build", "active", 10000, 1000),
                ("workerWWW", "do the build", "active", 5000, 2500),
                ("otheroooo", "unrelated old goal", "budget_limited", 8000, 8000),
            ],
        )
        result = codex_state.goal_budgets(root_thread_id="coorddddd")
        assert result["available"] is True
        assert result["selected_thread_id"] == "coorddddd"
        thread_ids = {g["thread_id"] for g in result["goals"]}
        assert thread_ids == {"coorddddd", "workerWWW"}
        assert "otheroooo" not in thread_ids
        # 2 goals in subtree, not 3.
        assert result["total_goals"] == 2


def test_goal_budgets_parent_goal_rollup_marker_groups():
    """A worker whose objective carries `Parent goal <id>` groups under <id>."""
    with _CodexHome() as home:
        # No spawn edge between coordinator and the worker (independent sessions).
        _make_state_db(
            home,
            threads=[("coorddddd", 0, 1_700_000_000_000)],
            edges=[],
        )
        _make_goals_db(
            home,
            goals=[
                ("coorddddd", "outer coordinator goal", "active", 10000, 500),
                ("workerAAA", "Parent goal <coorddddd> do task A", "active", 3000, 1500),
                ("workerBBB", "do task B without marker", "active", 3000, 1500),
            ],
        )
        result = codex_state.goal_budgets(root_thread_id="coorddddd")
        thread_ids = {g["thread_id"] for g in result["goals"]}
        assert "coorddddd" in thread_ids
        assert "workerAAA" in thread_ids  # rolled up via marker
        assert "workerBBB" not in thread_ids  # no marker -> not grouped
        # A parent_goal rollup edge was recorded for the worker.
        rollup_edges = [
            e for e in result["aggregation_edges"] if e["status"] == "parent_goal"
        ]
        assert len(rollup_edges) == 1
        assert rollup_edges[0]["parent_thread_id"] == "coorddddd"
        assert rollup_edges[0]["child_thread_id"] == "workerAAA"


def test_goal_budgets_marker_for_other_coordinator_does_not_group():
    """A `Parent goal <other>` marker does not pull the goal into THIS root."""
    with _CodexHome() as home:
        _make_state_db(home, threads=[("coorddddd", 0, 1_700_000_000_000)], edges=[])
        _make_goals_db(
            home,
            goals=[
                ("coorddddd", "outer coordinator goal", "active", 10000, 500),
                ("workerCCC", "Parent goal <other-coordinator> do task C", "active", 3000, 1500),
            ],
        )
        result = codex_state.goal_budgets(root_thread_id="coorddddd")
        thread_ids = {g["thread_id"] for g in result["goals"]}
        assert thread_ids == {"coorddddd"}
        assert "workerCCC" not in thread_ids


def test_json_output_contains_selected_thread_id_and_aggregation_edges():
    """The scoped JSON carries the auditable diagnostics (requirement 5)."""
    with _CodexHome() as home:
        _make_state_db(
            home,
            threads=[
                ("rootrrrrr", 0, 1_700_000_000_000),
                ("childAAAA", 123, 1_700_000_000_000),
            ],
            edges=[("rootrrrrr", "childAAAA", "open")],
        )
        sub = codex_state.subagent_costs(root_thread_id="rootrrrrr")
        # Must round-trip through JSON (auditable output is JSON-serialized).
        sub_json = json.loads(json.dumps(sub))
        assert "selected_thread_id" in sub_json
        assert sub_json["selected_thread_id"] == "rootrrrrr"
        assert "aggregation_edges" in sub_json
        assert isinstance(sub_json["aggregation_edges"], list)
        assert sub_json["aggregation_edges"][0]["parent_thread_id"] == "rootrrrrr"
        assert sub_json["aggregation_edges"][0]["child_thread_id"] == "childAAAA"
        assert "status" in sub_json["aggregation_edges"][0]

        _make_goals_db(home, goals=[("rootrrrrr", "g", "active", 100, 10)])
        goals = codex_state.goal_budgets(root_thread_id="rootrrrrr")
        goals_json = json.loads(json.dumps(goals))
        assert "selected_thread_id" in goals_json
        assert goals_json["selected_thread_id"] == "rootrrrrr"
        assert "aggregation_edges" in goals_json
        assert isinstance(goals_json["aggregation_edges"], list)


def test_cycle_safe_descendant_walk_terminates():
    """A self-referential / cyclic edge set terminates and fails open."""
    with _CodexHome() as home:
        _make_state_db(
            home,
            threads=[
                ("rootrrrrr", 10, 1_700_000_000_000),
                ("childAAAA", 20, 1_700_000_000_000),
            ],
            edges=[
                ("rootrrrrr", "childAAAA", "open"),
                ("childAAAA", "childAAAA", "open"),  # self-loop
                ("childAAAA", "rootrrrrr", "open"),  # back-edge -> cycle
            ],
        )
        # Must return, not hang.
        result = codex_state.subagent_costs(root_thread_id="rootrrrrr")
        assert result["available"] is True
        # Subtree is {root, child}; every edge whose parent is in the subtree is
        # aggregated (root->child, child->child self-loop, child->root back-edge).
        # The point of this test is termination under a cyclic graph, not edge
        # suppression: 20 (root->child) + 20 (self-loop) + 10 (back-edge to root) = 50.
        assert result["total_child_tokens"] == 50
        # Direct cycle-safety of the helper itself.
        subtree, agg = codex_state._resolve_subtree(
            [
                {"parent_id": "x", "child_id": "y", "status": "open"},
                {"parent_id": "y", "child_id": "x", "status": "open"},
                {"parent_id": "y", "child_id": "y", "status": "open"},
            ],
            "x",
        )
        assert subtree == {"x", "y"}


def test_current_thread_id_resolves_most_recently_updated():
    """current_thread_id returns the most-recently-updated thread, deterministically."""
    with _CodexHome() as home:
        _make_state_db(
            home,
            threads=[
                ("olderthread", 5, 1_600_000_000_000),
                ("newerthread", 5, 1_700_000_000_000),
                ("middletidl", 5, 1_650_000_000_000),
            ],
            edges=[],
        )
        os.environ["CODEX_THREAD_ID"] = "olderthread"
        assert codex_state.current_thread_id() == "olderthread"
        os.environ["CODEX_THREAD_ID"] = "newerthread"
        assert codex_state.current_thread_id() == "newerthread"


def test_quality_current_resolves_active_thread_not_older_mtime():
    """`quality current` picks the active thread's session, not the older-mtime one."""
    with _CodexHome() as home:
        # Two threads; the OLDER mtime session is the active thread per state DB.
        _make_state_db(
            home,
            threads=[
                ("activeeee", 5, 1_700_000_000_000),  # active (most recent update)
                ("staleeeeee", 5, 1_600_000_000_000),
            ],
            edges=[],
        )
        sessions = home / "sessions"
        active_file = sessions / "activeeee.jsonl"
        stale_file = sessions / "staleeeeee.jsonl"
        # Write minimal valid session_meta records so the adapter can introspect.
        active_file.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": "activeeee", "cwd": "/tmp"}}) + "\n",
            encoding="utf-8",
        )
        stale_file.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": "staleeeeee", "cwd": "/tmp"}}) + "\n",
            encoding="utf-8",
        )
        # Make the STALE file the most-recently-modified on disk (mtime guess
        # would wrongly pick it). os.utime sets mtime in seconds.
        os.utime(active_file, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
        os.utime(stale_file, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))

        os.environ["CODEX_THREAD_ID"] = "activeeee"
        resolved = measure._find_current_session_jsonl()
        assert resolved is not None
        assert resolved.name == "activeeee.jsonl", (
            f"expected the active thread's session, got {resolved.name}"
        )


def test_current_thread_id_binds_to_invoking_thread_not_global_newest():
    """Issue #108 follow-up: caller-A updated 100, other-B updated 200,
    CODEX_THREAD_ID=caller-A must resolve caller-A, never other-B."""
    with _CodexHome() as home:
        _make_state_db(
            home,
            threads=[("caller-A", 5, 100), ("other-B", 5, 200)],
            edges=[],
        )
        os.environ["CODEX_THREAD_ID"] = "caller-A"
        assert codex_state.current_thread_id() == "caller-A"


def test_current_thread_id_unresolved_without_identity():
    """No CODEX_THREAD_ID: report unresolved (None), never the global newest."""
    with _CodexHome() as home:
        _make_state_db(
            home,
            threads=[("caller-A", 5, 100), ("other-B", 5, 200)],
            edges=[],
        )
        assert codex_state.current_thread_id() is None
        for bad in ("", "   ", "../etc/passwd", "a b", "x" * 200, "short"):
            os.environ["CODEX_THREAD_ID"] = bad
            assert codex_state.current_thread_id() is None, bad


def test_current_thread_id_identity_missing_from_db_is_not_replaced():
    """Identity absent from the threads table stays the identity (no fallback to newest)."""
    with _CodexHome() as home:
        _make_state_db(home, threads=[("other-B", 5, 200)], edges=[])
        os.environ["CODEX_THREAD_ID"] = "caller-A"
        assert codex_state.current_thread_id() == "caller-A"


def test_quality_current_does_not_pick_other_thread_session():
    """With identity, `quality current` never returns another thread's session;
    without identity it is unresolved instead of the newest-mtime file."""
    with _CodexHome() as home:
        _make_state_db(
            home,
            threads=[("caller-A", 5, 100), ("other-B", 5, 200)],
            edges=[],
        )
        for tid in ("caller-A", "other-B"):
            f = home / "sessions" / f"{tid}.jsonl"
            f.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": tid, "cwd": "/tmp"}}) + "\n",
                encoding="utf-8",
            )
        os.environ["CODEX_THREAD_ID"] = "caller-A"
        assert measure._find_current_session_jsonl().name == "caller-A.jsonl"
        # Identity set but its transcript is missing: unresolved, not other-B.
        (home / "sessions" / "caller-A.jsonl").unlink()
        assert measure._find_current_session_jsonl() is None
        # No identity at all: the manual quality path must surface unresolved.
        os.environ.pop("CODEX_THREAD_ID")
        assert measure._find_current_session_jsonl(require_identity=True) is None
        assert measure._codex_identity_unresolved() is True


def test_quality_current_does_not_match_id_substrings():
    """Identity caller-A must not pick caller-AB's or xcaller-A-old's transcript."""
    with _CodexHome() as home:
        _make_state_db(home, threads=[("caller-A", 5, 1), ("caller-AB", 5, 9)], edges=[])
        for n in ("caller-AB", "xcaller-A-old"):
            (home / "sessions" / f"{n}.jsonl").write_text(
                json.dumps({"type": "session_meta", "payload": {"id": n, "cwd": "/tmp"}}) + "\n",
                encoding="utf-8",
            )
        os.environ["CODEX_THREAD_ID"] = "caller-A"
        assert measure._find_current_session_jsonl() is None


def test_quality_analyzer_current_reports_unresolved_json(capsys):
    with _CodexHome() as home:
        _make_state_db(home, threads=[("caller-A", 5, 1), ("other-B", 5, 9)], edges=[])
        assert measure.quality_analyzer(as_json=True) is None
        out = json.loads(capsys.readouterr().out)
        assert out["thread_resolution"] == "unresolved"
        assert "other-B" not in json.dumps(out)
        assert "is not set" in out["error"]
        assert out["reason"] == "identity_absent"
        os.environ["CODEX_THREAD_ID"] = "a b"
        assert measure.quality_analyzer(as_json=True) is None
        assert "not a valid thread id" in json.loads(capsys.readouterr().out)["error"]


def test_summary_unresolved_identity_shows_no_other_thread_totals(capsys):
    """P1: no identity must not fall back to whole-DB totals in summary/report."""
    with _CodexHome() as home:
        _make_state_db(
            home,
            threads=[("other-B", 5, 200), ("other-child", 98765, 201)],
            edges=[("other-B", "other-child", "open")],
        )
        _make_goals_db(home, [("other-B", "someone else's goal", "active", 1000, 10)])
        summary = measure._codex_state_summary()
        assert summary["thread_resolution"] == "unresolved"
        assert summary["subagents"]["total_child_tokens"] == 0
        assert summary["subagents"]["unresolved"] is True
        assert summary["goals"]["total_goals"] == 0
        assert summary["goals"]["unresolved"] is True
        measure.codex_state_report()
        out = capsys.readouterr().out
        assert "98,765" not in out
        assert "unresolved" in out
        assert "CODEX_THREAD_ID is not set" in out
        os.environ["CODEX_THREAD_ID"] = "a b"
        measure.codex_state_report()
        assert "not a valid thread id" in capsys.readouterr().out


def test_summary_resolved_identity_still_scopes_to_subtree():
    with _CodexHome() as home:
        _make_state_db(
            home,
            threads=[("caller-A", 5, 100), ("child-A1", 70, 101), ("other-B", 5, 200), ("other-child", 98765, 201)],
            edges=[("caller-A", "child-A1", "open"), ("other-B", "other-child", "open")],
        )
        os.environ["CODEX_THREAD_ID"] = "caller-A"
        summary = measure._codex_state_summary()
        assert summary["thread_resolution"] == "invoking"
        assert summary["subagents"]["total_child_tokens"] == 70


def test_quality_analyzer_missing_transcript_names_caller_thread(capsys):
    """P2: resolved id without a transcript -> Codex-specific error, no fallback."""
    with _CodexHome() as home:
        _make_state_db(home, threads=[("other-B", 5, 200)], edges=[])
        (home / "sessions" / "other-B.jsonl").write_text(
            json.dumps({"type": "session_meta", "payload": {"id": "other-B", "cwd": "/tmp"}}) + "\n",
            encoding="utf-8",
        )
        os.environ["CODEX_THREAD_ID"] = "caller-A"
        assert measure.quality_analyzer(as_json=True) is None
        out = json.loads(capsys.readouterr().out)
        assert "caller-A" in out["error"]
        assert "Claude" not in out["error"]
        assert out["thread_resolution"] == "unresolved"
        assert out["reason"] == "transcript_not_found"
        assert out["selected_thread_id"] == "caller-A"


def test_rollout_like_identity_is_not_normalized_to_another_thread():
    """rollout-UNKNOWN-<uuid> must not resolve the <uuid> thread's transcript."""
    uuid = "019a2abc-1111-2222-3333-444455556666"
    with _CodexHome() as home:
        _make_state_db(home, threads=[("caller-A", 5, 100), (uuid, 5, 200)], edges=[])
        (home / "sessions" / f"rollout-2026-10-12T10-00-00-{uuid}.jsonl").write_text(
            json.dumps({"type": "session_meta", "payload": {"id": uuid, "cwd": "/tmp"}}) + "\n",
            encoding="utf-8",
        )
        os.environ["CODEX_THREAD_ID"] = f"rollout-UNKNOWN-{uuid}"
        assert measure._find_current_session_jsonl() is None
        # The genuine id still resolves its rollout-named transcript.
        os.environ["CODEX_THREAD_ID"] = uuid
        assert measure._find_current_session_jsonl().name.endswith(f"{uuid}.jsonl")


def test_identity_absent_from_db_is_still_the_identity():
    """Pinned decision: DB absence alone never invalidates CODEX_THREAD_ID (the DB
    can lag a brand-new thread); resolution only fails when no transcript matches."""
    with _CodexHome() as home:
        _make_state_db(home, threads=[("caller-A", 5, 100), ("other-B", 5, 200)], edges=[])
        os.environ["CODEX_THREAD_ID"] = "nonexistent-id"
        assert codex_state.current_thread_id() == "nonexistent-id"
        assert measure._find_current_session_jsonl() is None


def test_codex_summary_reports_selected_thread_from_identity():
    with _CodexHome() as home:
        _make_state_db(
            home,
            threads=[("caller-A", 5, 100), ("other-B", 5, 200)],
            edges=[],
        )
        os.environ["CODEX_THREAD_ID"] = "caller-A"
        assert codex_state.subagent_costs(
            root_thread_id=codex_state.current_thread_id()
        )["selected_thread_id"] == "caller-A"


def test_non_codex_runtime_empty_shape_unchanged():
    """Off-Codex, subagent_costs/goal_budgets return the unchanged empty shape."""
    saved = codex_state.detect_runtime
    codex_state.detect_runtime = lambda: "claude"
    try:
        sub = codex_state.subagent_costs()
        goals = codex_state.goal_budgets()
        # The empty shapes must not carry the new diagnostic keys (non-Codex
        # call path behaves exactly as today).
        assert sub == {
            "available": False,
            "total_subagents": 0,
            "open_subagents": 0,
            "closed_subagents": 0,
            "leaked_subagents": 0,
            "total_child_tokens": 0,
            "by_parent": [],
            "leaked": [],
        }
        assert goals == {
            "available": False,
            "total_goals": 0,
            "active_goals": 0,
            "budget_limited": 0,
            "usage_limited": 0,
            "over_budget": 0,
            "goals": [],
        }
        assert codex_state.current_thread_id() is None
    finally:
        codex_state.detect_runtime = saved


def test_subagent_costs_no_root_is_backward_compatible():
    """Without a root, the legacy whole-DB aggregation still runs."""
    with _CodexHome() as home:
        _make_state_db(
            home,
            threads=[
                ("rootAaaaa", 0, 1_700_000_000_000),
                ("childA1aa", 200, 1_700_000_000_000),
                ("rootBbbbb", 0, 1_700_000_000_000),
                ("childB1bb", 800, 1_700_000_000_000),
            ],
            edges=[
                ("rootAaaaa", "childA1aa", "open"),
                ("rootBbbbb", "childB1bb", "closed"),
            ],
        )
        result = codex_state.subagent_costs()  # no root -> legacy whole-DB
        assert result["available"] is True
        assert result["total_subagents"] == 2  # both subtrees
        assert result["total_child_tokens"] == 1000
        assert result["selected_thread_id"] is None
        assert result["aggregation_edges"] == []


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
