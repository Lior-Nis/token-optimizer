"""Release gate: a plugin release must have its cloud npm package published.

scripts/check_npm_published.py is the gate (review 1, item 1). Its whole job is
to go red, so its red paths run here against a local registry, never npmjs.org.
"""

from __future__ import annotations

import http.server
import os
import re
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "token-optimizer" / "scripts"))

import check_npm_published as gate  # noqa: E402
import cloud_kit  # noqa: E402

GOOD = "sha512-" + "A" * 86 + "=="


def _missing(version):
    raise cloud_kit.KitError("404")


def test_missing_version_fails_with_publish_hint():
    ok, msg = gate.check("9.9.9", fetch=_missing)
    assert ok is False
    assert "9.9.9 is not on npm" in msg and "RELEASING.md" in msg


def test_published_version_passes():
    ok, msg = gate.check("9.9.9", fetch=lambda v: {"integrity": GOOD, "tarball": "x"})
    assert ok is True and GOOD in msg


def test_integrity_mismatch_fails():
    ok, msg = gate.check("9.9.9", fetch=lambda v: {"integrity": GOOD},
                         expect_integrity="sha512-" + "B" * 86 + "==")
    assert ok is False and "Never republish" in msg


def test_non_sha512_integrity_fails():
    ok, _ = gate.check("9.9.9", fetch=lambda v: {"integrity": "sha1-abc"})
    assert ok is False


def test_retries_cover_registry_propagation():
    calls = []

    def flaky(version):
        calls.append(version)
        if len(calls) < 3:
            raise cloud_kit.KitError("404")
        return {"integrity": GOOD}

    slept = []
    ok, _ = gate.check("9.9.9", fetch=flaky, attempts=3, wait_seconds=5, sleep=slept.append)
    assert ok is True and len(calls) == 3 and slept == [5, 5]
    calls.clear()
    ok, _ = gate.check("9.9.9", fetch=flaky, attempts=2, wait_seconds=0, sleep=lambda s: None)
    assert ok is False


class _Registry(http.server.BaseHTTPRequestHandler):
    docs: dict = {}

    def do_GET(self):  # noqa: N802
        body = self.docs.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def registry():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Registry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()


def _run(args):
    return subprocess.run([sys.executable, str(REPO / "scripts" / "check_npm_published.py"), *args],
                          capture_output=True, text=True, timeout=60)


def test_cli_red_and_green_against_a_local_registry(registry):
    _Registry.docs = {}
    r = _run(["--tag", "v7.7.7", "--registry", registry])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "::error::" in r.stderr and "7.7.7" in r.stderr

    _Registry.docs = {"/@alexgreensh%2Ftoken-optimizer/7.7.7": {"dist": {"integrity": GOOD, "tarball": "t"}}}
    r = _run(["--tag", "v7.7.7", "--registry", registry])
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("OK: ")
    r = _run(["--version", "7.7.7", "--registry", registry, "--expect-integrity", "sha512-other"])
    assert r.returncode == 1


def test_cli_defaults_to_plugin_version_and_rejects_junk(registry):
    _Registry.docs = {}
    version = gate.plugin_version()
    r = _run(["--registry", registry])
    assert r.returncode == 1 and version in r.stderr
    assert _run(["--tag", "nightly", "--registry", registry]).returncode == 2


def test_tree_without_npm_packaging_is_skipped(tmp_path):
    r = _run(["--repo", str(tmp_path), "--version", "1.0.0", "--registry", "http://127.0.0.1:9"])
    assert r.returncode == 0 and "skipped" in r.stdout


def test_gate_is_wired_into_the_release():
    """The gate only counts if the release runs it: the Sign release workflow
    (after the CHECKSUMS upload, so a missing npm package never also breaks
    the script installer) and the documented publish steps."""
    wf = (REPO / ".github" / "workflows" / "sign-release.yml").read_text(encoding="utf-8")
    assert "scripts/check_npm_published.py" in wf
    assert wf.index("bash scripts/sign-release.sh") < wf.index(
        "- name: Check the cloud npm package for this release is published")
    releasing = (REPO / "packaging" / "npm" / "RELEASING.md").read_text(encoding="utf-8")
    assert "scripts/check_npm_published.py --expect-integrity" in releasing


def _gate_step_script() -> str:
    """The run: block of the Sign release npm gate step, as bash text."""
    wf = (REPO / ".github" / "workflows" / "sign-release.yml").read_text(encoding="utf-8")
    step = wf[wf.index("- name: Check the cloud npm package for this release is published"):]
    body = step[step.index("run: |") + len("run: |"):]
    lines = []
    for line in body.splitlines()[1:]:
        if line.strip() and not line.startswith("          "):
            break
        lines.append(line[10:])
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(os.name == "nt", reason="runs the workflow step under bash")
def test_sign_release_gate_skips_tags_that_predate_the_script(tmp_path):
    """Review 2 LOW-4: a workflow_dispatch backfill of an old tag checks out a
    tree without scripts/check_npm_published.py, and the step failed the whole
    signing workflow. It must skip there, and still run the gate otherwise."""
    script = _gate_step_script()
    old = subprocess.run(["bash", "-c", script], cwd=str(tmp_path), capture_output=True, text=True,
                         env={"PATH": os.environ.get("PATH", ""), "TAG": "v5.11.64"}, timeout=30)
    assert old.returncode == 0, old.stderr
    assert "skipped" in old.stdout
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "check_npm_published.py").write_text(
        "import sys; print('GATE', sys.argv[1:]); sys.exit(1)\n", encoding="utf-8")
    new = subprocess.run(["bash", "-c", script], cwd=str(tmp_path), capture_output=True, text=True,
                         env={"PATH": os.environ.get("PATH", ""), "TAG": "v9.9.9"}, timeout=30)
    assert new.returncode == 1 and "GATE" in new.stdout and "v9.9.9" in new.stdout


def _npm_job() -> str:
    wf = (REPO / ".github" / "workflows" / "sign-release.yml").read_text(encoding="utf-8")
    return wf[wf.index("\n  npm:\n"):wf.index("\n  sign:\n")]


def test_release_publishes_npm_with_trusted_publishing_not_a_token():
    """Every release publishes the npm package from CI through npm trusted
    publishing (GitHub OIDC), so no publish token sits in the repo and nobody
    answers a 2FA prompt per release."""
    job = _npm_job()
    assert "id-token: write" in job
    assert "environment: npm-publish" in job  # npmjs.com trusts this env, tags only
    assert "npm publish " in job and "--access public" in job
    assert "NPM_TOKEN" not in job and "NODE_AUTH_TOKEN" not in job
    # Trusted publishing needs npm >= 11.5.1; Node 24 ships older.
    m = re.search(r"npm install -g npm@(\d+)\.(\d+)\.(\d+)", job)
    assert m and tuple(map(int, m.groups())) >= (11, 5, 1), job
    # The published bytes are proven against the tagged tree before success.
    assert job.index("npm publish ") < job.index("check_npm_published.py")
    assert "--match-tree" in job


def test_npm_publish_is_idempotent_for_a_retried_release():
    """A version's bytes never change once published: a re-run must skip."""
    job = _npm_job()
    assert job.index("npm view") < job.index("npm publish ")
