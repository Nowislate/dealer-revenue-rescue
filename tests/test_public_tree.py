"""Tests for the open-source release tooling (decision 17):
scripts/scan_public_tree.py and scripts/public_tree.py.

The scan must catch real-looking data planted in a tree, and the tree
this repository publishes must scan clean. The release zip a store
installs carries tests/ but not scripts/, so these tests skip there.
"""

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"


def _load(name):
    path = SCRIPTS / (name + ".py")
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Planted values are assembled at run time so this file itself scans clean.
REAL_VIN = "1M8GDM9AX" + "KP042788"       # a valid check digit (X)
WRONG_VIN = "1M8GDM9A1" + "KP042788"
EMAIL = "rep@" + "dealer-store.com"
PHONE = "813-867-" + "5309"

scan_mod = _load("scan_public_tree")
tree_mod = _load("public_tree")


def _git_checkout():
    if not (REPO / ".git").exists() or shutil.which("git") is None:
        return False
    probe = subprocess.run(["git", "-C", str(REPO), "ls-files", "README.md"],
                           capture_output=True, text=True)
    return probe.returncode == 0 and probe.stdout.strip() == "README.md"


@unittest.skipIf(scan_mod is None, "scripts/ is not shipped in the release zip")
class TestScanCatchesPlantedData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, rel, content):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    def findings(self, **kwargs):
        return scan_mod.scan(self.root, **kwargs)

    def test_clean_sample_data_passes(self):
        self.write("notes.md", "Buyer: SAMPLE BUYER, (555) 010-0000, "
                   "555-555-0123, buyer@example.com, VIN SAMPLEVIN00000000.\n")
        self.assertEqual(self.findings(), [])

    def test_real_looking_vin_is_caught(self):
        self.write("deal.json", '{"vin": "%s"}' % REAL_VIN)
        found = self.findings()
        self.assertEqual(len(found), 1)
        self.assertIn("VIN " + REAL_VIN, found[0])
        self.assertIn("deal.json:1", found[0])

    def test_vin_with_a_wrong_check_digit_passes(self):
        self.write("deal.json", '{"vin": "%s"}' % WRONG_VIN)
        self.assertEqual(self.findings(), [])

    def test_email_and_phone_are_caught(self):
        self.write("contact.txt", "Call %s or mail %s\n" % (PHONE, EMAIL))
        found = " ".join(self.findings())
        self.assertIn("phone number " + PHONE, found)
        self.assertIn("email address " + EMAIL, found)

    def test_private_term_is_caught_by_hash_only(self):
        self.write("tests/fixture.py", "TOTAL_TAX = '7,123.45'  # from deal 900001\n")
        hashes = {scan_mod.term_hash("7123.45"), scan_mod.term_hash("900001")}
        found = self.findings(term_hashes=hashes)
        self.assertEqual(len(found), 2)
        for line in found:
            self.assertIn("private term", line)
            self.assertNotIn("7123", line)  # the finding never repeats the term
        self.assertEqual(self.findings(), [])  # no list, no term check

    def test_words_match_whole_tokens_case_insensitively(self):
        self.write("a.md", "Printed at ACMEMOTORS. Not at acmemotorsport.\n")
        found = self.findings(term_hashes={scan_mod.term_hash("acmemotors")})
        self.assertEqual(len(found), 1)

    def test_banned_files_store_data_and_internal_notes_are_caught(self):
        self.write("photos/IMG_0001.HEIC", b"\x00")
        self.write("data/deals/abc.json", "{}")
        self.write("config/setup.json", "{}")
        self.write("AGENTS.md", "internal")
        self.write("docs/checkpoints/stage-1.md", "internal")
        found = " ".join(self.findings())
        self.assertIn("IMG_0001.HEIC: file type never ships", found)
        self.assertIn("data/deals/abc.json: local store data", found)
        self.assertIn("config/setup.json: local store data", found)
        self.assertIn("AGENTS.md: internal build note", found)
        self.assertIn("docs/checkpoints/stage-1.md: internal build note", found)

    def test_jpeg_with_camera_data_is_caught(self):
        self.write("tests/fixtures/photo.jpg", b"\xff\xd8\xff\xe1\x00\x10Exif\x00\x00rest")
        self.write("tests/fixtures/clean.jpg", b"\xff\xd8\xff\xdb\x00\x43rest")
        found = self.findings()
        self.assertEqual(len(found), 1)
        self.assertIn("photo.jpg: JPEG carries camera (EXIF) data", found[0])

    def test_cli_exit_code(self):
        self.write("ok.txt", "nothing here\n")
        self.assertEqual(scan_mod.main([str(self.root)]), 0)
        self.write("bad.txt", EMAIL + "\n")
        self.assertEqual(scan_mod.main([str(self.root)]), 1)


@unittest.skipIf(tree_mod is None or scan_mod is None or not _git_checkout(),
                 "needs a git checkout with scripts/")
class TestPublicTree(unittest.TestCase):
    def test_internal_notes_are_excluded(self):
        files = tree_mod.public_files()
        self.assertIn("README.md", files)
        self.assertIn("src/dealerpaperwork/tax_engine.py", files)
        for path in files:
            self.assertFalse(tree_mod.is_excluded(path), path)
            self.assertFalse(path.startswith(("docs/checkpoints/", "review-prototypes/")), path)
            self.assertNotIn(path, ("AGENTS.md", "CLAUDE.md", "scripts/private-terms.sha256"))

    def test_exported_tree_scans_clean_with_the_private_list(self):
        terms = scan_mod.load_term_hashes(SCRIPTS / "private-terms.sha256")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "public"
            tree_mod.export(out)
            self.assertFalse((out / "scripts/private-terms.sha256").exists())
            self.assertEqual(scan_mod.scan(out, term_hashes=terms), [])


if __name__ == "__main__":
    unittest.main()
