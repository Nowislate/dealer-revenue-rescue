"""Install Dealer Revenue Rescue on one Windows PC from a release folder.

Run by ``INSTALL.cmd`` at the top of an unzipped release (built by
``scripts/build_windows_release.py``). Standard library only, and no
network call of any kind (decision 5): every step reads or writes local
files, and the only programs started are this Python itself (the test
run) and, for the desktop shortcut, the PowerShell that ships with
Windows.

What it does, in order:

1. Checks Python is 3.10 or newer and that Tcl/Tk works (the windows
   need it).
2. Checks every file in the release against ``release-manifest.json``
   (SHA-256), so a damaged USB copy is refused before anything changes.
   Any file in the release folder that the manifest does not list is
   refused too, so nothing extra can ride along into the test run or the
   installed program. Step 5 also copies only the listed program files.
   The manifest ships inside the zip, so this proves the copy is intact,
   not where it came from: the SHA-256 of the zip, published with the
   release, is the only origin check, and the IT team checks it before
   unzipping (README, SETUP-ONE-PC.md Part 3).
3. Runs the full test suite from the release folder (skip with
   ``--skip-tests``, an IT option for a reinstall of a release already
   tested on this PC; ``install.json`` records that it was skipped).
4. Backs up the store's existing data, if any, into ``store\\backups``.
5. Copies the program into ``<target>\\app``. The previous program, if
   any, moves to ``<target>\\app-previous``. The store's data in
   ``<target>\\store`` is never touched by an install.
6. Writes the launcher, ``Start Dealer Revenue Rescue.cmd`` and a desktop
   shortcut.

Layout after install (default target ``C:\\DealerRevenueRescue``)::

    C:\\DealerRevenueRescue\\
        app\\                     the program (replaced on every install)
            dealerpaperwork\\
            launch.pyw
            install.json
        app-previous\\            the program before the last install
        store\\                   the store's data: setup, users, deals
            backups\\
            logs\\
        Start Dealer Revenue Rescue.cmd
        Back up now.cmd           one verified backup zip into store\\backups
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

DEFAULT_TARGET = Path(r"C:\DealerRevenueRescue")
APP_DIR = "app"
PREVIOUS_DIR = "app-previous"
STORE_DIR = "store"
MANIFEST_NAME = "release-manifest.json"
START_CMD = "Start Dealer Revenue Rescue.cmd"
BACKUP_CMD = "Back up now.cmd"
SHORTCUT_NAME = "Dealer Revenue Rescue"
MIN_PYTHON = (3, 10)


class InstallError(Exception):
    """A plain-language reason the install stopped. Nothing was changed
    unless the message says otherwise."""


LAUNCHER = r'''"""Dealer Revenue Rescue launcher, written by the installer.

Double-click this file, the desktop shortcut, or
"Start Dealer Revenue Rescue.cmd". The store's data lives in the "store"
folder next to this program folder.
"""
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent
STORE = APP.parent / "store"
sys.path.insert(0, str(APP))


def say(text):
    if sys.stdout is not None:
        print(text)


def main(argv):
    STORE.mkdir(parents=True, exist_ok=True)
    os.chdir(STORE)
    if "--check" in argv:
        import tkinter
        level = tkinter.Tcl().eval("info patchlevel")
        import dealerpaperwork
        from dealerpaperwork import app_ui
        say("Dealer Revenue Rescue %s ready. Tcl/Tk %s. Data folder: %s"
            % (dealerpaperwork.__version__, level, STORE))
        return 0 if app_ui.TK_AVAILABLE else 1
    from dealerpaperwork.app_ui import run_app
    return run_app(data_root=STORE)


try:
    code = main(sys.argv[1:])
except Exception:
    logs = STORE / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / "launch-error.txt"
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("\n--- %s ---\n" % datetime.now().isoformat(timespec="seconds"))
        handle.write(traceback.format_exc())
    say(traceback.format_exc())
    try:
        if os.environ.get("DP_NO_DIALOG"):
            raise RuntimeError("dialog switched off")
        import tkinter
        from tkinter import messagebox
        window = tkinter.Tk()
        window.withdraw()
        messagebox.showerror(
            "Dealer Revenue Rescue could not start",
            "Dealer Revenue Rescue could not start. The details are saved in:\n%s\n\n"
            "Give that file to whoever installed the app." % log)
        window.destroy()
    except Exception:
        pass
    code = 1
sys.exit(code)
'''


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def check_python(version_info=None, tk_check: Optional[Callable[[], str]] = None) -> list[str]:
    """Plain-language problems with this Python; empty means ready."""
    problems = []
    version = tuple(version_info or sys.version_info)[:2]
    if version < MIN_PYTHON:
        problems.append(
            "This Python is %d.%d. Install Python 3.10 or newer from the "
            "python.org Windows installer, then run INSTALL.cmd again." % version)
    try:
        (tk_check or _tk_patchlevel)()
    except Exception as exc:  # any Tcl/Tk failure is the same fix
        problems.append(
            "Python's Tcl/Tk part is missing or broken (%s). Run the Python "
            "installer again, choose Modify, tick \"tcl/tk and IDLE\", then "
            "run INSTALL.cmd again." % exc)
    return problems


def _tk_patchlevel() -> str:
    import tkinter
    return tkinter.Tcl().eval("info patchlevel")


def read_manifest(source: Path) -> dict:
    path = Path(source) / MANIFEST_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError(
            "This folder is not a complete release copy: %s could not be read "
            "(%s). Unzip the release again and run INSTALL.cmd from it." % (MANIFEST_NAME, exc))


def _listed_path(relative: str) -> bool:
    """A manifest path must stay inside the release folder."""
    parts = relative.replace("\\", "/").split("/")
    return bool(relative) and not relative.startswith(("/", "\\")) and ":" not in relative \
        and all(part not in ("", ".", "..") for part in parts)


def _release_contents(source: Path) -> set[str]:
    """Every file (or anything that is not a folder) under source, as
    forward-slash paths relative to it."""
    found = set()
    for path in Path(source).rglob("*"):
        if not path.is_dir():
            found.add(path.relative_to(source).as_posix())
    return found


def verify_release(source: Path) -> tuple[dict, list[str]]:
    """Check every listed file's SHA-256, and refuse any file the manifest
    does not list. Returns (manifest, problems)."""
    source = Path(source)
    manifest = read_manifest(source)
    problems = []
    listed = set()
    for entry in manifest.get("files", []):
        relative = entry.get("path", "")
        if not _listed_path(relative):
            problems.append("Unsafe path in the manifest: " + relative)
            continue
        listed.add(relative)
        path = source / relative
        if not path.is_file():
            problems.append("Missing: " + relative)
        elif _sha256(path) != entry.get("sha256"):
            problems.append("Damaged or changed: " + relative)
    if not manifest.get("files"):
        problems.append("The release manifest lists no files.")
    for relative in sorted(_release_contents(source) - listed - {MANIFEST_NAME}):
        problems.append("Not part of the release: " + relative)
    return manifest, problems


def run_tests(source: Path, timeout: int = 900) -> tuple[bool, str]:
    """Run the release's own test suite with this Python."""
    # -B: write no __pycache__ into the release folder, so a second
    # INSTALL.cmd run from the same folder still finds only listed files.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
        cwd=str(source), capture_output=True, text=True, timeout=timeout, env=env)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output


def backup_store(store: Path, version: str) -> Optional[Path]:
    """Back up existing store data before an install. None if empty."""
    from . import backup
    if not backup.default_backup_sources(store):
        return None
    zip_path = backup.create_backup(store, store / "backups", label="before-install-" + version)
    ok, problems = backup.verify_backup(zip_path)
    if not ok:
        raise InstallError("The backup made before installing did not verify: "
                           + "; ".join(problems) + ". Nothing was installed.")
    return zip_path


def pythonw_path(executable: Optional[str] = None) -> Path:
    """The windowless Python next to this one, so no black console box
    stays open behind the app. Falls back to this Python."""
    exe = Path(executable or sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.is_file() else exe


def install_app(source: Path, target: Path, manifest: dict, python_exe: Path,
                tests_run: bool = True) -> Path:
    """Copy the program into target/app, keeping the old one as app-previous."""
    source, target = Path(source), Path(target)
    target.mkdir(parents=True, exist_ok=True)
    staging = target / (APP_DIR + ".new")
    if staging.exists():
        shutil.rmtree(staging)
    # Copy only the program files the manifest lists, never the folder
    # wholesale: a file that is not in the release never reaches app.
    prefix = "src/"
    program = [entry["path"] for entry in manifest.get("files", [])
               if entry.get("path", "").startswith(prefix + "dealerpaperwork/")
               and _listed_path(entry["path"])]
    if not program:
        raise InstallError("The release manifest lists no program files. Nothing was installed.")
    for relative in program:
        destination = staging / relative[len(prefix):]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
    (staging / "launch.pyw").write_text(LAUNCHER, encoding="utf-8")
    record = {
        "version": manifest.get("version", ""),
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": str(python_exe),
        "release_manifest_sha256": _sha256(source / MANIFEST_NAME),
        "tests_run_before_install": bool(tests_run),
    }
    (staging / "install.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    app, previous = target / APP_DIR, target / PREVIOUS_DIR
    if app.exists():
        if previous.exists():
            shutil.rmtree(previous)
        app.rename(previous)
    staging.rename(app)
    return app


def write_start_cmd(target: Path, python_exe: Path) -> Path:
    path = Path(target) / START_CMD
    launcher = Path(target) / APP_DIR / "launch.pyw"
    path.write_text('@echo off\r\nstart "" "%s" "%s"\r\n' % (python_exe, launcher), encoding="utf-8")
    return path


def console_python(python_exe: Path) -> Path:
    """python.exe beside pythonw.exe, for jobs that print a result."""
    candidate = Path(python_exe).with_name("python.exe")
    return candidate if candidate.is_file() else Path(python_exe)


def write_backup_cmd(target: Path, python_exe: Path) -> Path:
    """Double-click to make and verify one backup zip of the store's data."""
    target = Path(target)
    store, app = target / STORE_DIR, target / APP_DIR
    python = console_python(python_exe)
    lines = [
        "@echo off",
        'set "FAILED="',
        "rem Makes one backup zip of this store's Dealer Revenue Rescue data, then checks it.",
        'set "PYTHONPATH=%s"' % app,
        '"%s" -m dealerpaperwork.backup create --data-root "%s" --dest "%s" --label manual'
        % (python, store, store / "backups"),
        'if errorlevel 1 goto failed',
        'for /f "delims=" %%%%f in (\'dir /b /o-d "%s\\backup-*.zip"\') do (set "LATEST=%%%%f" & goto verify)'
        % (store / "backups"),
        ":verify",
        '"%s" -m dealerpaperwork.backup verify "%s\\%%LATEST%%"' % (python, store / "backups"),
        'if errorlevel 1 goto failed',
        "echo.",
        "echo Copy that zip to a USB stick and keep the stick away from this PC.",
        'if not defined DP_NO_PAUSE start "" "%s"' % (store / "backups"),
        "goto done",
        ":failed",
        "echo BACKUP FAILED. Do not rely on it. Tell whoever installed the app.",
        "set FAILED=1",
        ":done",
        "if not defined DP_NO_PAUSE pause",
        "if defined FAILED exit /b 1",
    ]
    path = target / BACKUP_CMD
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return path


def create_shortcut(target: Path, python_exe: Path, desktop: Path) -> Path:
    """A desktop shortcut. Uses Windows' own PowerShell to write a .lnk;
    if that is blocked, puts a copy of the start file on the desktop."""
    target, desktop = Path(target), Path(desktop)
    desktop.mkdir(parents=True, exist_ok=True)
    link = desktop / (SHORTCUT_NAME + ".lnk")
    launcher = target / APP_DIR / "launch.pyw"

    def quoted(value) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(%s); "
        "$s.TargetPath = %s; $s.Arguments = %s; $s.WorkingDirectory = %s; "
        "$s.Description = 'Dealer Revenue Rescue'; $s.Save()"
        % (quoted(link), quoted(python_exe), quoted('"%s"' % launcher), quoted(target / STORE_DIR)))
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       check=True, capture_output=True, timeout=60)
        if link.is_file():
            return link
    except (OSError, subprocess.SubprocessError):
        pass
    fallback = desktop / START_CMD
    shutil.copyfile(target / START_CMD, fallback)
    return fallback


def default_desktop() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


def main(argv: Optional[list[str]] = None, *, out=print) -> int:
    parser = argparse.ArgumentParser(prog="INSTALL.cmd", description="Install Dealer Revenue Rescue on this PC.")
    parser.add_argument("--source", default=".", help="the unzipped release folder")
    parser.add_argument("--target", default=str(DEFAULT_TARGET), help="install folder (default C:\\DealerRevenueRescue)")
    parser.add_argument("--skip-tests", action="store_true", help="do not run the test suite first")
    parser.add_argument("--no-shortcut", action="store_true", help="do not put a shortcut on the desktop")
    parser.add_argument("--desktop", default=None, help="folder for the shortcut (default: this user's desktop)")
    parser.add_argument("--allow-non-windows", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    source, target = Path(args.source).resolve(), Path(args.target)

    try:
        if os.name != "nt" and not args.allow_non_windows:
            raise InstallError("Dealer Revenue Rescue installs on Windows only (decision 4).")
        out("Step 1 of 6: checking Python and Tcl/Tk...")
        problems = check_python()
        if problems:
            raise InstallError(" ".join(problems))
        out("  OK: Python %d.%d.%d" % sys.version_info[:3])

        out("Step 2 of 6: checking every file in the release...")
        manifest, problems = verify_release(source)
        if problems:
            raise InstallError("The release copy is not intact. Unzip it again from the "
                               "original file. Problems: " + "; ".join(problems[:10]))
        version = manifest.get("version", "unknown")
        out("  OK: %d files match (version %s)" % (len(manifest["files"]), version))
        out("  This shows the copy is undamaged. It does not show where the zip came")
        out("  from: check the zip's SHA-256 against the one on the release page.")

        if args.skip_tests:
            out("Step 3 of 6: test suite skipped (--skip-tests). install.json records this.")
        else:
            out("Step 3 of 6: running the test suite. Windows may flash open and close; "
                "this takes about a minute...")
            ok, output = run_tests(source)
            if not ok:
                out(output[-4000:])
                raise InstallError("The test suite failed on this PC, so nothing was "
                                   "installed. Send the text above to whoever gave you the release.")
            summary = [line for line in output.splitlines() if line.startswith(("Ran ", "OK"))]
            out("  OK: " + (" ".join(summary) or "tests passed"))

        store = target / STORE_DIR
        out("Step 4 of 6: backing up this store's existing data...")
        saved = backup_store(store, version) if store.exists() else None
        out("  OK: " + ("saved " + str(saved) if saved else "no existing data, nothing to back up"))

        out("Step 5 of 6: copying the program to %s..." % (target / APP_DIR))
        python_exe = pythonw_path()
        install_app(source, target, manifest, python_exe, tests_run=not args.skip_tests)
        store.mkdir(parents=True, exist_ok=True)
        out("  OK: program installed; store data folder is " + str(store))

        out("Step 6 of 6: making the start file, backup file and desktop shortcut...")
        start = write_start_cmd(target, python_exe)
        write_backup_cmd(target, python_exe)
        if args.no_shortcut:
            out("  OK: " + str(start) + " (no desktop shortcut, --no-shortcut)")
        else:
            link = create_shortcut(target, python_exe, Path(args.desktop) if args.desktop else default_desktop())
            out("  OK: desktop shortcut " + str(link))
    except InstallError as exc:
        out("")
        out("INSTALL STOPPED: " + str(exc))
        return 1

    out("")
    out("DONE. Dealer Revenue Rescue %s is installed." % version)
    out("Open it from the \"Dealer Revenue Rescue\" icon on the desktop, or from")
    out(str(target / START_CMD) + ".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
