#!/usr/bin/env python3
"""Assemble the @alexgreensh/token-optimizer npm package (Claude Code cloud).

    python3 packaging/npm/build.py            # stage into build/npm/package
    python3 packaging/npm/build.py --pack     # stage, then `npm pack` -> build/npm/*.tgz
    python3 packaging/npm/build.py --out DIR  # stage somewhere else (tests)

The package is the Claude Code plugin's runtime as-is: hooks/, skills/ and
.claude-plugin/ copied from the repo root, plus install-cloud.sh and the
license files. Version = .claude-plugin/plugin.json. Left out: the media in
skills/token-optimizer/assets (screenshots, demo gif/mp4, svg art), except
dashboard.html, which `measure.py dashboard` renders from. Bytecode and OS
junk never ship, and nothing .gitignore
excludes is copied. Separate from the private root package.json (the Pi build).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
KEEP_ASSETS = {"dashboard.html"}


def plugin_version() -> str:
    return json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]


def tracked_files(*roots: str) -> list:
    """Tracked plus new not-ignored files: anything .gitignore excludes (internal
    notes, caches) can never ship from a working checkout."""
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *roots],
                         capture_output=True, check=True).stdout.decode("utf-8")
    files = []
    for rel in filter(None, out.split("\0")):
        parts = Path(rel).parts
        if any(p == "__pycache__" or p == ".DS_Store" or p.endswith((".pyc", ".pyo")) for p in parts):
            continue
        if parts[:3] == ("skills", "token-optimizer", "assets") and parts[-1] not in KEEP_ASSETS:
            continue
        if (REPO / rel).is_file():
            files.append(rel)
    return files


def stage(out: Path) -> Path:
    pkg = out / "package"
    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)
    version = plugin_version()
    meta = json.loads((HERE / "package.template.json").read_text(encoding="utf-8"))
    meta["version"] = version
    (pkg / "package.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(HERE / "README.md", pkg / "README.md")
    shutil.copy2(HERE / "install-cloud.sh", pkg / "install-cloud.sh")
    (pkg / "install-cloud.sh").chmod(0o755)
    for name in ("LICENSE", "LICENSE-SMALL-BUSINESS.md", "PRIVACY.md"):
        shutil.copy2(REPO / name, pkg / name)
    for rel in tracked_files("hooks", "skills", ".claude-plugin"):
        dest = pkg / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, dest)
    return pkg


def pack(pkg: Path, dest: Path, dry_run: bool = False) -> str:
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm not found on PATH")
    cmd = [npm, "pack", "--json", "--pack-destination", str(dest)]
    if dry_run:
        cmd.append("--dry-run")
    res = subprocess.run(cmd, cwd=str(pkg), capture_output=True, text=True, check=True)
    info = json.loads(res.stdout)[0]
    return json.dumps({"filename": info["filename"], "size": info["size"],
                       "unpackedSize": info["unpackedSize"], "entryCount": info["entryCount"],
                       "integrity": info["integrity"]})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=REPO / "build" / "npm")
    ap.add_argument("--pack", action="store_true", help="run npm pack after staging")
    ap.add_argument("--dry-run", action="store_true", help="npm pack --dry-run (lists files)")
    args = ap.parse_args(argv)
    pkg = stage(args.out)
    print("staged %s (version %s)" % (pkg, plugin_version()))
    if args.pack or args.dry_run:
        print(pack(pkg, args.out, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
