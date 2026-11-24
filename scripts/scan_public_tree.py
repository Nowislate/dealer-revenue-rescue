"""Scan a source tree for real dealer, deal or customer data (decision 17).

    python scripts/scan_public_tree.py [folder] [--terms FILE]

Exit 0 when the tree is clean, 1 with one line per finding otherwise. Run
on the public tree before it is published, and by CI on every change.

What it looks for:

* **Private terms.** With ``--terms``, a list of SHA-256 hashes of words
  and figures that must never appear: the pilot store, real deal numbers,
  real worksheet amounts. Only hashes are kept, so the list itself names
  nothing. The private repository keeps that list; the public tree does not
  carry it, and the other checks still run there.
* **Real-looking VINs.** 17 VIN characters whose check digit is valid.
* **Email addresses** outside the reserved example domains.
* **US phone numbers** that could be dialed: reserved 555-0100 to 555-0199
  numbers and impossible ones (area 555, or an area or exchange starting
  with 0 or 1) pass.
* **Files that should never ship:** phone photos (HEIC), archives,
  spreadsheets, mail files, local store data folders, and JPEGs that
  carry camera (EXIF) data.
* **Internal build notes** that belong only in the private repository.

Standard library only. Text and binary files are both read: a PDF's or
JPEG's metadata is text inside the bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

SALT = "revenue-rescue-scan:"

TOKEN = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")
VIN = re.compile(r"(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
PHONE = re.compile(r"(?<!\d)\(?(\d{3})\)?[-. ](\d{3})[-.](\d{4})(?!\d)")

ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
ALLOWED_EMAIL_SUFFIXES = (".test", ".invalid", ".example", ".localhost")

BANNED_SUFFIXES = (".heic", ".heif", ".zip", ".7z", ".rar", ".xlsx", ".xls",
                   ".csv", ".docx", ".doc", ".eml", ".msg", ".pst", ".db",
                   ".sqlite", ".mov", ".mp4")
BANNED_DIRS = ("data/", "store/", "dist/")
BANNED_FILES = ("config/setup.json", "config/local_preset.json",
                "config/print_config.json")
INTERNAL = ("AGENTS.md", "CLAUDE.md", "docs/CLOUD_SETUP.md",
            "docs/CLOUD_BUILD_KICKOFF.md", "docs/checkpoints/", "docs/plans/",
            "docs/specs/", "review-prototypes/", "scripts/private-terms.sha256")
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv"}

_VIN_VALUES = {**{str(d): d for d in range(10)},
               **dict(zip("ABCDEFGH", range(1, 9))),
               **dict(zip("JKLMN", range(1, 6))), "P": 7, "R": 9,
               **dict(zip("STUVWXYZ", range(2, 10)))}
_VIN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)


def term_hash(term: str) -> str:
    """The hash kept in the private list for one word or figure."""
    return hashlib.sha256((SALT + normalize(term)).encode("utf-8")).hexdigest()


def normalize(token: str) -> str:
    token = token.lower().strip()
    if re.fullmatch(r"[0-9][0-9,]*(\.[0-9]+)?", token):
        token = token.replace(",", "")
    return token


def load_term_hashes(path) -> set[str]:
    path = Path(path)
    if not path.exists():
        return set()
    hashes = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            hashes.add(line.lower())
    return hashes


def vin_check_digit_ok(vin: str) -> bool:
    total = sum(_VIN_VALUES[c] * w for c, w in zip(vin, _VIN_WEIGHTS))
    check = total % 11
    return vin[8] == ("X" if check == 10 else str(check))


def _email_allowed(domain: str) -> bool:
    domain = domain.lower().rstrip(".")
    return domain in ALLOWED_EMAIL_DOMAINS or domain.endswith(ALLOWED_EMAIL_SUFFIXES)


def _files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and not (set(path.relative_to(root).parts) & SKIP_DIRS):
            yield path.relative_to(root).as_posix(), path


def scan_text(rel: str, text: str, term_hashes: set[str]) -> list[str]:
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        where = f"{rel}:{number}"
        if term_hashes:
            for token in TOKEN.findall(line.lower()):
                digest = term_hash(token)
                if digest in term_hashes:
                    findings.append(f"{where}: private term (hash {digest[:12]})")
        for vin in VIN.findall(line):
            if any(c.isalpha() for c in vin) and any(c.isdigit() for c in vin) \
                    and vin_check_digit_ok(vin):
                findings.append(f"{where}: real-looking VIN {vin}")
        for match in EMAIL.finditer(line):
            if not _email_allowed(match.group(1)):
                findings.append(f"{where}: email address {match.group(0)}")
        for area, exchange, last in PHONE.findall(line):
            if exchange == "555" and 100 <= int(last) <= 199:
                continue
            if area[0] in "01" or exchange[0] in "01" or area == "555":
                continue  # not a dialable North American number
            findings.append(f"{where}: phone number {area}-{exchange}-{last}")
    return findings


def scan_file(rel: str, path: Path, term_hashes: set[str]) -> list[str]:
    findings = []
    low = rel.lower()
    if low.endswith(BANNED_SUFFIXES):
        findings.append(f"{rel}: file type never ships ({Path(low).suffix})")
    if low.startswith(BANNED_DIRS) or low in BANNED_FILES:
        findings.append(f"{rel}: local store data never ships")
    for rule in INTERNAL:
        if (rule.endswith("/") and rel.startswith(rule)) or rel == rule:
            findings.append(f"{rel}: internal build note; keep it private")
    data = path.read_bytes()
    if low.endswith((".jpg", ".jpeg")) and b"Exif\x00" in data[:65536]:
        findings.append(f"{rel}: JPEG carries camera (EXIF) data; strip it")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    findings.extend(scan_text(rel, text, term_hashes))
    return findings


def scan(root, *, term_hashes=frozenset()) -> list[str]:
    root = Path(root)
    findings = []
    for rel, path in _files(root):
        findings.extend(scan_file(rel, path, set(term_hashes)))
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", nargs="?", default=".")
    parser.add_argument("--terms", help="file of private term hashes")
    parser.add_argument("--hash", metavar="TERM",
                        help="print the hash line for one term and exit")
    args = parser.parse_args(argv)
    if args.hash:
        print(term_hash(args.hash))
        return 0
    terms = load_term_hashes(args.terms) if args.terms else set()
    findings = scan(Path(args.folder), term_hashes=terms)
    for finding in findings:
        print(finding)
    print(f"Scanned {args.folder}: {len(findings)} finding(s); "
          f"private term list: {len(terms)} entries.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
