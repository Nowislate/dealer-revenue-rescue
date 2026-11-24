"""Tests for Stage 5 one-click backup/restore (backup.py).

Create -> verify OK; tamper with a copy -> verify fails and restore
refuses; restore round-trip puts the original bytes back and keeps
the pre-restore copy of the live data. Plus the CLI surface.

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.backup import (
    BackupError,
    create_backup,
    main,
    restore_backup,
    verify_backup,
)

FILES = {
    "config/setup.json": '{"state": "complete"}',
    "config/print_config.json": '{"copies": {}}',
    "data/users.json": '{"users": []}',
    "data/audit.jsonl": '{"event": "x"}\n',
    "data/drill_runs.json": "[]",
    "data/deals/deal-1.json": '{"deal_id": "deal-1"}',
    "data/print_sessions/s1.json": '{"deal_id": "deal-1"}',
    "data/documents/library.json": '{"documents": []}',
    "data/documents/we-owe.pdf": "%PDF-1.4 form",
}


class BackupTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.data_root = root / "app"
        for relative, content in FILES.items():
            target = self.data_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        self.dest = root / "backups"


class TestCreateVerify(BackupTestCase):
    def test_create_then_verify_ok(self):
        zip_path = create_backup(self.data_root, self.dest, label="weekly")
        self.assertTrue(zip_path.exists())
        self.assertTrue(zip_path.name.startswith("backup-"))
        self.assertIn("weekly", zip_path.name)
        with zipfile.ZipFile(zip_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            names = set(archive.namelist())
        self.assertEqual(manifest["file_count"], len(FILES))
        self.assertEqual(
            {entry["path"] for entry in manifest["files"]}, set(FILES)
        )
        self.assertIn("manifest.json", names)
        ok, problems = verify_backup(zip_path)
        self.assertTrue(ok, problems)
        self.assertEqual(problems, [])

    def test_tampered_copy_fails_verification(self):
        zip_path = create_backup(self.data_root, self.dest)
        tampered = Path(self.tmp.name) / "tampered.zip"
        with zipfile.ZipFile(zip_path) as source, zipfile.ZipFile(
            tampered, "w", zipfile.ZIP_DEFLATED
        ) as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "data/deals/deal-1.json":
                    data = b'{"deal_id": "forged"}'
                target.writestr(name, data)
        ok, problems = verify_backup(tampered)
        self.assertFalse(ok)
        self.assertTrue(any("deal-1.json" in p for p in problems))


class TestRestore(BackupTestCase):
    def test_restore_round_trip(self):
        zip_path = create_backup(self.data_root, self.dest)
        # Damage the live data after the backup.
        setup = self.data_root / "config" / "setup.json"
        setup.write_text('{"state": "broken"}', encoding="utf-8")
        (self.data_root / "data" / "deals" / "deal-1.json").unlink()
        summary = restore_backup(zip_path, self.data_root)
        # Original contents are back.
        self.assertEqual(
            setup.read_text(encoding="utf-8"), FILES["config/setup.json"]
        )
        self.assertEqual(
            (self.data_root / "data" / "deals" / "deal-1.json").read_text(
                encoding="utf-8"
            ),
            FILES["data/deals/deal-1.json"],
        )
        self.assertEqual(summary["files_restored"], len(FILES))
        # The pre-restore copy exists and holds the damaged state.
        pre = Path(summary["pre_restore_copy"])
        self.assertTrue(pre.is_dir())
        self.assertEqual(
            (pre / "config" / "setup.json").read_text(encoding="utf-8"),
            '{"state": "broken"}',
        )

    def test_restore_refuses_a_tampered_backup(self):
        zip_path = create_backup(self.data_root, self.dest)
        tampered = Path(self.tmp.name) / "tampered.zip"
        with zipfile.ZipFile(zip_path) as source, zipfile.ZipFile(
            tampered, "w", zipfile.ZIP_DEFLATED
        ) as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "data/users.json":
                    data = b'{"users": [{"username": "intruder"}]}'
                target.writestr(name, data)
        with self.assertRaises(BackupError):
            restore_backup(tampered, self.data_root)
        # Live data untouched by the refused restore.
        self.assertEqual(
            (self.data_root / "data" / "users.json").read_text(encoding="utf-8"),
            FILES["data/users.json"],
        )


class TestCli(BackupTestCase):
    def test_cli_create_verify_restore(self):
        self.assertEqual(
            main(["create", "--data-root", str(self.data_root),
                  "--dest", str(self.dest), "--label", "cli"]),
            0,
        )
        zip_path = next(self.dest.glob("backup-*.zip"))
        self.assertEqual(main(["verify", str(zip_path)]), 0)
        self.assertEqual(
            main(["restore", str(zip_path), "--data-root", str(self.data_root)]),
            0,
        )

    def test_cli_verify_tampered_returns_1(self):
        zip_path = create_backup(self.data_root, self.dest)
        tampered = Path(self.tmp.name) / "tampered.zip"
        with zipfile.ZipFile(zip_path) as source, zipfile.ZipFile(
            tampered, "w", zipfile.ZIP_DEFLATED
        ) as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "manifest.json":
                    data = b"{}"
                target.writestr(name, data)
        self.assertEqual(main(["verify", str(tampered)]), 1)


if __name__ == "__main__":
    unittest.main()

class TestImageLibraryBackup(unittest.TestCase):
    def test_image_library_backup_restore_preserves_rendering(self):
        from dealerpaperwork.doc_library import DocumentLibrary, DealerDocument, FieldPlacement
        from dealerpaperwork.pdf_overlay import render_overlay_pdf
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'source'
            folder = app / 'data/documents'
            folder.mkdir(parents=True)
            legacy = DealerDocument('legacy', 'Legacy', 'old.pdf', 'old.pdf', fields=[FieldPlacement('buyer_name', 1, 20, 55)], status='approved', approved_by='Manager', approved_at='2026-11-02')
            raw = legacy.to_dict()
            for key in ('pages', 'form_source', 'image_revision'):
                raw.pop(key)
            (folder / 'library.json').write_text(json.dumps({'format_version': 'stage5-1', 'documents': [raw]}))
            (folder / 'old.pdf').write_bytes(b'%PDF legacy retained, never rendered')
            library = DocumentLibrary(folder)
            fixtures = Path(__file__).parent / 'fixtures/form_images'
            old = library.add_document(source_paths=[fixtures / 'page-1.jpg'], doc_id='form', title='Form')
            current = library.replace_page_images('form', [fixtures / 'page-3.jpg', fixtures / 'progressive.jpg'])
            library.approve_document('form', SimpleNamespace(role='manager', display_name='Manager'))
            def rendered(lib, ident):
                doc = lib.get_document(ident)
                return render_overlay_pdf(doc, {'buyer_name': 'Sample Buyer'}, banner='SAMPLE', page_images=lib.load_page_images(doc))
            before = {ident: rendered(library, ident) for ident in ('legacy', 'form')}
            files = {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
            archive = create_backup(app, root / 'backup')
            self.assertEqual(verify_backup(archive), (True, []))
            restored_root = root / 'restored'
            restored_root.mkdir()
            (restored_root / 'marker.txt').write_text('previous data')
            summary = restore_backup(archive, restored_root)
            self.assertEqual((Path(summary['pre_restore_copy']) / 'marker.txt').read_text(), 'previous data')
            restored = DocumentLibrary(restored_root / 'data/documents')
            for relative, data in files.items():
                self.assertEqual((restored.library_dir / relative).read_bytes(), data)
            self.assertTrue(restored.page_image_path(old, 1).is_file())
            self.assertEqual(restored.get_document('form').image_revision, current.image_revision)
            for ident in before:
                self.assertEqual(rendered(restored, ident), before[ident])
