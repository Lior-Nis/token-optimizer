"""Bug C regression: ``cleanup_old_archives`` sweeps stale ``.reclaim-{digest}``
orphans from ``archive_root/.locks/``, and the deterministic-claim
same-generation reclaimer serialization is preserved.

``hook_runtime.LeaseLock._reclaim_path`` claims the right to unlink an expired
lease by ``os.link(self.path, claim)`` where
``claim = .{sid}.lease.reclaim-{digest}`` and
``digest = sha256("owner:{nonce}")[:32]``.  The claim name is **deterministic
per generation** so that concurrent same-generation reclaimers are serialized
(exactly one winner; the rest get ``FileExistsError`` and fail open).  If a
reclaimer is killed between ``os.link`` and the ``finally`` that unlinks the
claim, the claim persists and every future reclaimer for that generation hits
``FileExistsError`` and returns False **forever** -- a permanent liveness
deadlock.  ``HookDeadline._watch`` calls ``os._exit(0)`` which is exactly the
kind of mid-reclaim kill that orphans the claim.

The fix (Option 1 -- sweep) extends Bug B's ``_sweep_stale_lock_candidates`` to
also reap ``*.reclaim-*`` older than the max lease duration.  The deterministic
claim name in ``hook_runtime._reclaim_path`` is **NOT modified** -- it is
load-bearing serialization.  The sweep bounds the deadlock from *permanent* to
*until the next cleanup pass*.

These tests prove (mapping to the validation contract):

  * VAL-C-001 - a stale ``.reclaim-{digest}`` claim aged past the max lease
    duration (``lease_seconds + reclaim_grace`` = 10.25s for archive locks) is
    unlinked by ``cleanup_old_archives()``.
  * VAL-C-002 - after the sweep reaps a stale ``.reclaim-{digest}`` for an
    expired owner generation, a subsequent ``LeaseLock._reclaim_if_expired()``
    for that same generation returns True (reclaim succeeds) instead of False
    forever.  The permanent deadlock is bounded to "until the next cleanup
    pass."
  * VAL-C-003 - multiple concurrent reclaimers for the SAME expired owner
    generation: exactly ONE returns True (unlinks the lease); the rest return
    False.  The claim name remains deterministic
    (``sha256("owner:{nonce}")[:32]``, NO random suffix added).
  * VAL-C-004 - a young ``.reclaim-{digest}`` claim newer than the sweep
    threshold (an in-flight reclaimer) survives ``cleanup_old_archives()``.

``hook_runtime.py`` is NOT edited by this bug.  The fix lives entirely in
``archive_result.py`` (both script trees).

Run: python3 -m pytest tests/test_reclaim_sweep_serialization.py -q
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hook_runtime  # noqa: E402

# Archive lock sites use LeaseLock(..., acquire_timeout=0) with NO deadline, so
# the max lease a candidate/reclaim artifact can represent is the default
# lease_seconds (10.0) + reclaim_grace (0.25) = 10.25s.  Bug B's fix exposes
# this as ``archive_result.LOCK_SWEEP_STALE_SECONDS``; Bug C reuses the same
# threshold for the .reclaim-* sweep.
_DEFAULT_LEASE = hook_runtime.LeaseLock(
    SCRIPTS / ".probe.lease", acquire_timeout=0
)
MAX_LEASE_SECONDS = _DEFAULT_LEASE.lease_seconds + _DEFAULT_LEASE.reclaim_grace


def _load_archive_result(monkeypatch: pytest.MonkeyPatch, snapshot_dir: Path):
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", str(snapshot_dir))
    monkeypatch.setenv("TOKEN_OPTIMIZER_ARCHIVE_RETENTION_HOURS", "24")
    monkeypatch.setenv("TOKEN_OPTIMIZER_ARCHIVE_RETENTION_MAX_FILES", "1000")
    monkeypatch.setenv("TOKEN_OPTIMIZER_ARCHIVE_RETENTION_MAX_BYTES", "1000000")
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "archive_result", SCRIPTS / "archive_result.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "archive_result", module)
    spec.loader.exec_module(module)
    return module


def _lease_path(archive_root: Path, sid: str) -> Path:
    """Mirror ``archive_result._session_lock_path``."""
    return archive_root / ".locks" / f"{sid}.lease"


def _reclaim_claim_path(lease_path: Path, owner_nonce: str) -> Path:
    """Mirror ``LeaseLock._reclaim_path`` for the owner generation.

    ``digest = sha256("owner:{nonce}")[:32]``;
    ``claim = .{lease_path.name}.reclaim-{digest}``.
    """
    digest = hashlib.sha256(f"owner:{owner_nonce}".encode("utf-8")).hexdigest()[:32]
    return lease_path.with_name(f".{lease_path.name}.reclaim-{digest}")


def _write_expired_owner_lease(lease_path: Path, owner_nonce: str) -> None:
    """Write a valid, expired owner lease metadata blob at ``lease_path``.

    Mirrors the JSON shape ``LeaseLock._metadata`` produces so that
    ``_read_owner`` parses it and ``_reclaim_if_expired`` reaches the
    ``owner:{nonce}`` reclaim branch (created in the past, not released,
    ``now > expires + reclaim_grace``).
    """
    now = time.time()
    meta = {
        "pid": 999999,
        "nonce": owner_nonce,
        "released": 0,
        "reuse_wall": now - 100.0,
        "created_wall": now - 100.0,
        "expires_wall": now - 50.0,
    }
    lease_path.parent.mkdir(parents=True, exist_ok=True)
    lease_path.write_text(
        json.dumps(meta, separators=(",", ":"), sort_keys=True), encoding="utf-8"
    )
    os.chmod(lease_path, 0o600)


def _age_old(path: Path) -> None:
    """Age ``path`` past its sweep threshold.

    Candidates and reclaim claims are on deliberately different fuses: reaping a
    live candidate is harmless (the acquirer just fails open), but reaping a live
    reclaim claim frees a deterministic name and can produce two winners, so the
    claim fuse is much longer. Age against whichever applies to this file.
    """
    import archive_result
    if ".lease.reclaim-" in path.name:
        threshold = archive_result.RECLAIM_SWEEP_STALE_SECONDS
    else:
        threshold = archive_result.LOCK_SWEEP_STALE_SECONDS
    old = time.time() - (threshold + 5)
    os.utime(path, (old, old))


# ---------------------------------------------------------------------------
# VAL-C-001: stale .reclaim-{digest} is reaped by cleanup_old_archives
# ---------------------------------------------------------------------------


def test_stale_reclaim_claim_older_than_max_lease_is_reaped(monkeypatch, tmp_path):
    """VAL-C-001: an orphan ``.reclaim-{digest}`` aged past 10.25s is unlinked."""
    module = _load_archive_result(monkeypatch, tmp_path)
    root = tmp_path / "tool-archive"
    locks = root / ".locks"
    locks.mkdir(parents=True)

    lease = _lease_path(root, "sid-stale")
    owner_nonce = "deadbeefdeadbeef"
    _write_expired_owner_lease(lease, owner_nonce)
    claim = _reclaim_claim_path(lease, owner_nonce)
    # Mirror _reclaim_path: the claim is a hard link to the lease file.
    os.link(str(lease), str(claim))
    os.chmod(claim, 0o600)
    _age_old(claim)

    assert claim.exists()  # sanity: present before cleanup
    module.cleanup_old_archives()

    assert not claim.exists(), "stale .reclaim-{digest} orphan survived cleanup"


# ---------------------------------------------------------------------------
# VAL-C-002: stale reclaim no longer permanently deadlocks a lease
# ---------------------------------------------------------------------------


def test_sweep_unblocks_subsequent_reclaim_after_reaping_stale_claim(
    monkeypatch, tmp_path
):
    """VAL-C-002: after the sweep reaps a stale claim for an expired owner,
    a subsequent ``LeaseLock._reclaim_if_expired()`` for that generation
    returns True (no permanent deadlock)."""
    module = _load_archive_result(monkeypatch, tmp_path)
    root = tmp_path / "tool-archive"
    locks = root / ".locks"
    locks.mkdir(parents=True)

    lease = _lease_path(root, "sid-deadlock")
    owner_nonce = "cafecafecafecafe"
    _write_expired_owner_lease(lease, owner_nonce)
    claim = _reclaim_claim_path(lease, owner_nonce)
    os.link(str(lease), str(claim))
    os.chmod(claim, 0o600)
    _age_old(claim)

    lock = hook_runtime.LeaseLock(lease, acquire_timeout=0)
    # Before sweep: the stale deterministic claim blocks reclamation forever.
    assert lock._reclaim_if_expired() is False, (
        "pre-sweep reclaim should be blocked by the stale deterministic claim"
    )
    assert claim.exists(), "pre-sweep: stale claim should still be present"

    # Sweep reaps the stale claim (the fix).
    module.cleanup_old_archives()
    assert not claim.exists(), (
        "sweep should have reaped the stale .reclaim-{digest} claim"
    )

    # After sweep: reclamation succeeds -- the permanent deadlock is bounded.
    result = lock._reclaim_if_expired()
    assert result is True, (
        "post-sweep reclaim should succeed (no permanent deadlock); "
        "got _reclaim_if_expired()=%r" % (result,)
    )
    assert not lease.exists(), "post-sweep: the expired lease should be unlinked"


# ---------------------------------------------------------------------------
# VAL-C-003: deterministic-claim same-generation reclaimer serialization
# ---------------------------------------------------------------------------


def test_concurrent_same_generation_reclaimers_exactly_one_winner(tmp_path):
    """VAL-C-003: N threads calling ``_reclaim_if_expired`` on the SAME
    expired owner generation -> exactly ONE returns True, the rest return
    False.  The claim name is deterministic (no random suffix), so the
    exactly-one-winner property holds after the Bug C sweep change."""
    root = tmp_path / "tool-archive"
    locks = root / ".locks"
    locks.mkdir(parents=True)

    lease = _lease_path(root, "sid-concurrent")
    owner_nonce = "concurrentconcurrent"
    _write_expired_owner_lease(lease, owner_nonce)

    n = 12
    barrier = threading.Barrier(n)
    results: list[bool] = [False] * n
    errors: list[BaseException] = []

    def contender(idx: int) -> None:
        try:
            # Each thread gets its own LeaseLock pointing at the same expired
            # lease.  The claim path is derived from the lease file's owner
            # nonce (read from self.path), not self.nonce, so every thread
            # computes the SAME deterministic claim path.
            lock = hook_runtime.LeaseLock(lease, acquire_timeout=0)
            barrier.wait()
            results[idx] = bool(lock._reclaim_if_expired())
        except BaseException as exc:  # pragma: no cover - surfaced in assert
            errors.append(exc)

    threads = [threading.Thread(target=contender, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, "threads raised: %r" % (errors,)
    assert sum(1 for r in results if r) == 1, (
        "exactly one reclaimer should win; results=%r" % (results,)
    )
    assert sum(1 for r in results if not r) == n - 1, (
        "the rest should fail open; results=%r" % (results,)
    )
    # The winner unlinked the expired lease.
    assert not lease.exists(), "the expired lease should be unlinked by the winner"
    # No reclaim claim artifacts left behind -- the single deterministic claim
    # was unlinked in the winner's finally.  A random-suffix implementation
    # would leave stray claim files.
    leftover_reclaims = list(locks.glob("*.reclaim-*"))
    assert leftover_reclaims == [], (
        "deterministic claim should leave no stray reclaim files; "
        "leftovers=%r" % (leftover_reclaims,)
    )


def test_reclaim_claim_name_is_deterministic_no_random_suffix(tmp_path):
    """VAL-C-003 (source-level): ``_reclaim_path`` derives the claim name from
    ``sha256(generation)[:32]`` with NO random suffix.  Proven structurally by
    pre-creating the deterministic claim path and observing that a reclaimer
    for the same generation hits ``FileExistsError`` (returns False) -- i.e.
    the reclaimer computes exactly the path we computed independently."""
    root = tmp_path / "tool-archive"
    locks = root / ".locks"
    locks.mkdir(parents=True)

    lease = _lease_path(root, "sid-det")
    owner_nonce = "deterministicdeterm"
    _write_expired_owner_lease(lease, owner_nonce)

    # Independently compute the deterministic claim path and pre-create it as
    # a hard link to the lease (mirroring what _reclaim_path does).
    claim = _reclaim_claim_path(lease, owner_nonce)
    os.link(str(lease), str(claim))
    os.chmod(claim, 0o600)

    lock = hook_runtime.LeaseLock(lease, acquire_timeout=0)
    # The reclaimer computes the SAME path -> os.link fails FileExistsError ->
    # returns False.  This proves the claim name is deterministic (if it had a
    # random suffix, the reclaimer would create a different path and succeed).
    assert lock._reclaim_if_expired() is False, (
        "deterministic claim should block a same-generation reclaimer"
    )
    assert claim.exists(), "the pre-created deterministic claim should still exist"

    # Source inspection: _reclaim_path uses hashlib.sha256 (deterministic), not
    # secrets/uuid/random for the claim name.
    source = (SCRIPTS / "hook_runtime.py").read_text(encoding="utf-8")
    assert "digest = hashlib.sha256(generation" in source, (
        "_reclaim_path must derive the digest from hashlib.sha256(generation)"
    )
    # The claim filename pattern is .{name}.reclaim-{digest} with no random
    # component.
    assert ".reclaim-{digest}" in source, (
        "_reclaim_path must use the .reclaim-{digest} claim name pattern"
    )


# ---------------------------------------------------------------------------
# VAL-C-004: young .reclaim-* is NOT reaped (no false positive)
# ---------------------------------------------------------------------------


def test_young_reclaim_claim_newer_than_threshold_survives(monkeypatch, tmp_path):
    """VAL-C-004: an in-flight ``.reclaim-{digest}`` claim newer than the
    sweep threshold survives ``cleanup_old_archives()``."""
    module = _load_archive_result(monkeypatch, tmp_path)
    root = tmp_path / "tool-archive"
    locks = root / ".locks"
    locks.mkdir(parents=True)

    lease = _lease_path(root, "sid-live")
    owner_nonce = "liveclaimliveclaim"
    _write_expired_owner_lease(lease, owner_nonce)
    claim = _reclaim_claim_path(lease, owner_nonce)
    os.link(str(lease), str(claim))
    os.chmod(claim, 0o600)
    # Just-created mtime (well within the sweep threshold -- a live reclaimer
    # is mid-flight).
    now = time.time()
    os.utime(claim, (now, now))

    module.cleanup_old_archives()

    assert claim.exists(), (
        "young in-flight .reclaim-{digest} was reaped (false positive)"
    )


# ---------------------------------------------------------------------------
# Windows sharing-violation regression
# ---------------------------------------------------------------------------


def test_sharing_violation_on_lease_rename_still_yields_one_winner(
    tmp_path, monkeypatch
):
    """Windows refuses rename/unlink on a file held open without delete
    share: the claim winner's ``os.rename(self.path, victim)`` races every
    other contender's ``read_text`` handle and transiently raises
    PermissionError.  Simulate that errno on POSIX for the first attempts on
    the lease rename; exactly one reclaimer must still win."""
    root = tmp_path / "tool-archive"
    locks = root / ".locks"
    locks.mkdir(parents=True)

    lease = _lease_path(root, "sid-share")
    _write_expired_owner_lease(lease, "shareshareshare1")

    real_rename = os.rename
    calls = {"n": 0}

    def sharing_violation_rename(src, dst, *args, **kwargs):
        if Path(src).name == lease.name:
            calls["n"] += 1
            if calls["n"] <= 20:
                raise PermissionError(
                    13, "The process cannot access the file because it is "
                    "being used by another process", str(src), 32, str(dst)
                )
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "rename", sharing_violation_rename)

    n = 6
    barrier = threading.Barrier(n)
    results: list[bool] = [False] * n
    errors: list[BaseException] = []

    def contender(idx: int) -> None:
        try:
            lock = hook_runtime.LeaseLock(lease, acquire_timeout=0)
            barrier.wait()
            results[idx] = bool(lock._reclaim_if_expired())
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=contender, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, "threads raised: %r" % (errors,)
    assert calls["n"] > 0, "the lease rename was never reached"
    assert sum(1 for r in results if r) == 1, (
        "exactly one reclaimer should win despite transient sharing "
        "violations; results=%r" % (results,)
    )
    assert not lease.exists(), "the expired lease should be unlinked by the winner"
