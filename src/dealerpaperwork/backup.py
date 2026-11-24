"""One-click local backup + verified restore (Stage 5).

The app's whole value sits in local files on one store machine:
the setup file (dealer profile + approved preset), the print config,
the user accounts, the audit log, the deals, the print-session
checklists, and the dealer document library. A backup copies ALL of
it into a single zip the store can put on a USB stick -- still local,
still no internet (locked decision 5).

Safety properties, by construction:

* Every backup carries a ``manifest.json`` inside the zip: when it
  was made, the app version, and for every file its archive path,
  size, and SHA-256.
* ``verify_backup`` re-reads the manifest and checks every listed
  file is present in the zip with a matching hash. A tampered or
  truncated copy fails loudly, not silently.
* ``restore_backup`` verifies FIRST and refuses a bad backup; it
  then moves the current data aside to ``<data_root>.pre-restore-
  <timestamp>`` before extracting, so a restore can itself be
  undone by hand.

Which paths go into a backup is assembled in ONE place
(``default_backup_sources``) from the app's data root -- the folder
holding ``config/`` and ``data/`` -- so the list cannot drift between
create and restore. Callers (and tests) may pass their own sources.

Command line (from the repo root):

    python -m dealerpaperwork.backup create [--data-root .] [--dest backups] [--label before-update]
    python -m dealerpaperwork.backup verify <zip>
    python -m dealerpaperwork.backup restore <zip> [--data-root .]

Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from . import __version__

MANIFEST_NAME = "manifest.json"

# The app's local data, relative to the data root. Files may be absent
# (a store that has not reached that feature yet); directories are
# walked recursively. This list IS the backup definition.
_BACKUP_FILES = (
    "config/setup.json",
    "config/print_config.json",
    "data/users.json",
    "data/disclaimer_acceptances.json",
    "data/bootstrap_pending.json",
    "data/reconciliation.json",
    "data/audit.jsonl",
    "data/drill_runs.json",
)
_BACKUP_DIRS = (
    "data/deals",
    "data/print_sessions",
    "data/documents",
)


class BackupError(Exception):
    """A backup or restore cannot be carried out. Message says why."""


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def default_backup_sources(
    data_root: Union[str, Path],
) -> list[tuple[str, Path]]:
    """(archive path, filesystem path) for every existing data file
    under ``data_root``. Missing files/dirs are skipped -- a backup
    of what exists."""
    root = Path(data_root)
    sources: list[tuple[str, Path]] = []
    for relative in _BACKUP_FILES:
        candidate = root / relative
        if candidate.is_file():
            sources.append((relative, candidate))
    for relative in _BACKUP_DIRS:
        directory = root / relative
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    sources.append((path.relative_to(root).as_posix(), path))
    return sources


def create_backup(
    data_root: Union[str, Path],
    dest_dir: Union[str, Path],
    label: str = "",
    *,
    sources: Optional[list[tuple[str, Path]]] = None,
) -> Path:
    """Zip the app's local data (plus manifest) into ``dest_dir``.

    Returns the zip's path. The name is
    ``backup-YYYYmmdd-HHMMSS[-label].zip``.
    """
    root = Path(data_root)
    items = (
        list(sources)
        if sources is not None
        else default_backup_sources(root)
    )
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    safe_label = "".join(
        c if (c.isalnum() or c in "-_") else "-" for c in (label or "")
    ).strip("-")
    name = f"backup-{stamp}" + (f"-{safe_label}" if safe_label else "") + ".zip"
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    zip_path = dest / name

    manifest_files: list[dict] = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for arcname, path in items:
            data = path.read_bytes()
            manifest_files.append(
                {
                    "path": arcname,
                    "size": len(data),
                    "sha256": _sha256(data),
                }
            )
            archive.writestr(arcname, data)
        manifest = {
            "created_at": _now().isoformat(timespec="seconds"),
            "app_version": __version__,
            "label": label or "",
            "file_count": len(manifest_files),
            "files": manifest_files,
        }
        archive.writestr(
            MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True)
        )
    return zip_path


def _read_manifest(archive: zipfile.ZipFile) -> dict:
    try:
        raw = archive.read(MANIFEST_NAME)
    except KeyError:
        raise BackupError(
            f"The backup has no {MANIFEST_NAME} -- it was not made by "
            "this app, or the copy is damaged."
        ) from None
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BackupError(
            f"The backup's {MANIFEST_NAME} is not readable -- the "
            "copy is damaged."
        ) from None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise BackupError(f"The backup's {MANIFEST_NAME} is malformed.")
    return manifest


def verify_backup(zip_path: Union[str, Path]) -> tuple[bool, list[str]]:
    """Check a backup against its manifest.

    Returns (ok, problems): ok only when every file the manifest
    lists is present in the zip with the recorded size and SHA-256.
    """
    problems: list[str] = []
    try:
        archive = zipfile.ZipFile(Path(zip_path))
    except (OSError, zipfile.BadZipFile) as exc:
        return False, [f"Not a readable zip file: {exc}"]
    with archive:
        try:
            manifest = _read_manifest(archive)
        except BackupError as exc:
            return False, [str(exc)]
        names = set(archive.namelist())
        for entry in manifest["files"]:
            arcname = entry.get("path", "")
            if arcname not in names:
                problems.append(f"Missing from the backup: {arcname}")
                continue
            data = archive.read(arcname)
            if len(data) != entry.get("size"):
                problems.append(
                    f"Size changed for {arcname}: manifest says "
                    f"{entry.get('size')}, the copy holds {len(data)}."
                )
            if _sha256(data) != entry.get("sha256"):
                problems.append(
                    f"Content changed for {arcname}: its SHA-256 does "
                    "not match the manifest (tampered or corrupted)."
                )
    return (not problems), problems


def restore_backup(
    zip_path: Union[str, Path],
    data_root: Union[str, Path],
) -> dict:
    """Restore a verified backup into ``data_root``.

    Verifies first and REFUSES a backup that fails verification. The
    current contents of ``data_root`` are moved aside to a sibling
    ``<name>.pre-restore-<timestamp>`` folder first, never deleted.
    Returns a summary dict.
    """
    ok, problems = verify_backup(zip_path)
    if not ok:
        raise BackupError(
            "Refusing to restore: the backup failed verification: "
            + "; ".join(problems)
        )
    root = Path(data_root)
    # The destination's installation identity never comes from a backup.
    identity_path = root / "data/installation.json"
    local_identity = identity_path.read_bytes() if identity_path.exists() else None
    pre_restore: Optional[Path] = None
    if root.exists() and any(root.iterdir()):
        stamp = _now().strftime("%Y%m%d-%H%M%S")
        pre_restore = root.with_name(f"{root.name}.pre-restore-{stamp}")
        shutil.move(str(root), str(pre_restore))
    root.mkdir(parents=True, exist_ok=True)
    restored = 0
    with zipfile.ZipFile(Path(zip_path)) as archive:
        for name in archive.namelist():
            if name in (MANIFEST_NAME, "data/installation.json") or name.endswith("/"):
                continue
            target = (root / name).resolve()
            # Zip-slip guard: nothing extracts outside the data root.
            if root.resolve() not in target.parents and target != root.resolve():
                raise BackupError(
                    f"Refusing to extract {name!r}: it points outside "
                    "the data folder."
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
            restored += 1
    if local_identity is not None:
        identity_path.parent.mkdir(parents=True, exist_ok=True)
        identity_path.write_bytes(local_identity)
    return {
        "restored_to": str(root),
        "pre_restore_copy": None if pre_restore is None else str(pre_restore),
        "files_restored": restored,
    }


# ---------------------------------------------------------------------------
# Command line: python -m dealerpaperwork.backup create|verify|restore
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dealerpaperwork.backup",
        description="One-click local backup / verified restore for the "
        "Dealer Revenue Rescue app data.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create a backup zip.")
    create.add_argument("--data-root", default=".", help="App folder holding config/ and data/ (default: .)")
    create.add_argument("--dest", default="backups", help="Folder to write the zip into (default: backups)")
    create.add_argument("--label", default="", help="Optional label in the zip name.")

    verify = sub.add_parser("verify", help="Verify a backup against its manifest.")
    verify.add_argument("zip_path")

    restore = sub.add_parser("restore", help="Restore a verified backup.")
    restore.add_argument("zip_path")
    restore.add_argument("--data-root", default=".", help="App folder to restore into (default: .)")

    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            zip_path = create_backup(args.data_root, args.dest, args.label)
            print(f"Backup created: {zip_path}")
            return 0
        if args.command == "verify":
            ok, problems = verify_backup(args.zip_path)
            if ok:
                print("Backup verified: every file matches its manifest.")
                return 0
            print("Backup FAILED verification:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        if args.command == "restore":
            summary = restore_backup(args.zip_path, args.data_root)
            print(
                "Restored {files_restored} files into {restored_to}. "
                "Previous data kept at: {pre_restore_copy}".format(**summary)
            )
            return 0
    except BackupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
