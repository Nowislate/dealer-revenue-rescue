"""Build the public source tree for the open-source release (decision 17).

    python scripts/public_tree.py --out <empty folder>

The public repository is published from a fresh, one-commit history, never
from this repository's history (decision 17: the early history holds the
original real worksheets). This script writes that tree: every file git
tracks here, minus the internal build notes listed in ``EXCLUDE``. It then
runs ``scan_public_tree.py`` on the result and refuses (exit 1) if the scan
finds anything.

Standard library only. Reads ``git ls-files``; never reads untracked files,
so a store's local data (always git-ignored) can never be copied.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Internal build notes and tooling that stay in this private repository.
# A path ending in "/" excludes the whole folder.
EXCLUDE = (
    "AGENTS.md",                 # instructions for coding agents
    "CLAUDE.md",                 # instructions for coding agents
    "docs/CLOUD_BUILD_KICKOFF.md",
    "docs/CLOUD_SETUP.md",
    "docs/STAGES.md",            # build history; links the checkpoints
    "docs/checkpoints/",         # build notes; some quote real worksheet figures
    "docs/plans/",
    "docs/specs/",
    "private-forms/",            # a store's own licensed forms, for the Windows test only
    "review-prototypes/",        # browser design prototypes, not the product
    "scripts/check-cloud-baseline.sh",
    "scripts/private-terms.sha256",  # the private scan list itself
)


def is_excluded(path: str) -> bool:
    for rule in EXCLUDE:
        if rule.endswith("/") and path.startswith(rule):
            return True
        if path == rule:
            return True
    return False


def tracked_files(repo: Path = REPO) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        check=True, capture_output=True,
    ).stdout.decode("utf-8")
    return sorted(p for p in out.split("\0") if p)


def public_files(repo: Path = REPO) -> list[str]:
    return [p for p in tracked_files(repo) if not is_excluded(p)]


def export(dest: Path, repo: Path = REPO) -> list[str]:
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} is not empty. Give an empty or new folder.")
    files = public_files(repo)
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / rel, target)
    return files


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", help="empty folder to write")
    parser.add_argument("--list", action="store_true",
                        help="print the public file list and write nothing")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(public_files()))
        return 0
    if not args.out:
        parser.error("--out is required unless --list is given")
    files = export(Path(args.out))
    sys.path.insert(0, str(REPO / "scripts"))
    import scan_public_tree

    terms = scan_public_tree.load_term_hashes(REPO / "scripts/private-terms.sha256")
    findings = scan_public_tree.scan(Path(args.out), term_hashes=terms)
    for finding in findings:
        print(finding)
    print(f"{len(files)} files written to {args.out}; "
          f"{len(findings)} scan finding(s); private term list: "
          f"{len(terms)} entries.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
