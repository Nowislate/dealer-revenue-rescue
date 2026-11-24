"""Build the Windows release zip a store installs from (decision 6:
updates arrive as deliberate release packages the IT team installs).

    python scripts/build_windows_release.py [--out dist]

Writes ``dist/DealerRevenueRescue-<version>.zip``. Unzipped, it holds::

    DealerRevenueRescue-<version>/
        INSTALL.cmd                double-click to install
        READ-ME-FIRST.txt          the one-PC setup guide (docs/SETUP-ONE-PC.md)
        LICENSE
        NOTICE                     copyright holder (Apache 2.0, section 4d)
        release-manifest.json      SHA-256 of every other file
        src/dealerpaperwork/...    the program
        tests/...                  the suite INSTALL.cmd runs before installing
        config/sample_preset.json  SAMPLE data the tests read; never installed

Standard library only. The zip is reproducible: fixed file order and
timestamps, no compression, so the same commit builds the same bytes on
Windows and Linux. No dealer data is
ever included: only the listed source folders are read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def release_version(repo: Path = REPO) -> str:
    for line in (repo / "src/dealerpaperwork/__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("No __version__ in src/dealerpaperwork/__init__.py")


def _tree(repo: Path, folder: str) -> list[tuple[str, Path]]:
    items = []
    for path in sorted((repo / folder).rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            items.append((path.relative_to(repo).as_posix(), path))
    return items


def release_files(repo: Path = REPO) -> list[tuple[str, bytes]]:
    """(path inside the release, bytes), manifest excluded."""
    files = [(name, path.read_bytes()) for name, path in
             _tree(repo, "src/dealerpaperwork") + _tree(repo, "tests")]
    files.append(("config/sample_preset.json", (repo / "config/sample_preset.json").read_bytes()))
    files.append(("LICENSE", (repo / "LICENSE").read_bytes()))
    files.append(("NOTICE", (repo / "NOTICE").read_bytes()))
    guide = (repo / "docs/SETUP-ONE-PC.md").read_text(encoding="utf-8")
    files.append(("READ-ME-FIRST.txt", guide.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")))
    cmd = (repo / "packaging/windows/INSTALL.cmd").read_text(encoding="utf-8")
    files.append(("INSTALL.cmd", cmd.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")))
    return sorted(files)


def build_manifest(files: list[tuple[str, bytes]], version: str) -> bytes:
    manifest = {
        "product": "Dealer Revenue Rescue",
        "version": version,
        "files": [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in files],
    }
    return json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")


def write_release_folder(dest: Path, repo: Path = REPO) -> Path:
    """Write the unzipped release into dest (for tests and inspection)."""
    version = release_version(repo)
    files = release_files(repo)
    for name, data in files + [("release-manifest.json", build_manifest(files, version))]:
        path = Path(dest) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return Path(dest)


def build_zip(out_dir: Path, repo: Path = REPO) -> Path:
    version = release_version(repo)
    files = release_files(repo)
    top = "DealerRevenueRescue-" + version
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / (top + ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as archive:
        for name, data in files + [("release-manifest.json", build_manifest(files, version))]:
            info = zipfile.ZipInfo(top + "/" + name, date_time=FIXED_TIME)
            # Stored, not deflated: Python on Windows ships a different
            # zlib from Linux, so deflated bytes differ while the files match.
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            # ZipInfo records the building OS (0 on Windows, 3 elsewhere);
            # pin it, since the Unix mode bits above mean 3.
            info.create_system = 3
            archive.writestr(info, data)
    return zip_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(REPO / "dist"))
    args = parser.parse_args(argv)
    zip_path = build_zip(Path(args.out))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(zip_path)
    print("sha256 " + digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
