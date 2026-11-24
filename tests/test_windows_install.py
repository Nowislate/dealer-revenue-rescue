"""The Windows install package: release builder + installer + launcher.

Runs on every OS. Off Windows the installer runs with
--allow-non-windows; the real INSTALL.cmd, desktop shortcut and backup
file run on the Windows CI job (.github/workflows/review-prototype.yml).
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))

from dealerpaperwork import install  # noqa: E402

BUILDER = REPO / 'scripts/build_windows_release.py'
# Inside an unzipped release (INSTALL.cmd's own test run) the builder is
# not shipped, so these tests run only from the repository.
IN_REPO = BUILDER.is_file()
if IN_REPO:
    _spec = importlib.util.spec_from_file_location('build_windows_release', BUILDER)
    release = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(release)


def tk_works():
    try:
        install._tk_patchlevel()
        return True
    except Exception:
        return False


@unittest.skipUnless(IN_REPO, 'release builder is repository tooling')
class ReleaseBuilderTests(unittest.TestCase):
    def test_zip_is_reproducible_and_carries_no_dealer_data(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = release.build_zip(Path(a)).read_bytes()
            second = release.build_zip(Path(b)).read_bytes()
        self.assertEqual(first, second, 'same commit must build the same bytes')
        names = zipfile.ZipFile(io.BytesIO(first)).namelist()
        top = 'DealerRevenueRescue-' + release.release_version() + '/'
        self.assertTrue(all(n.startswith(top) for n in names))
        inner = {n[len(top):] for n in names}
        for required in ('INSTALL.cmd', 'READ-ME-FIRST.txt', 'LICENSE', 'NOTICE', 'release-manifest.json',
                         'src/dealerpaperwork/install.py', 'src/dealerpaperwork/app_ui.py',
                         'config/sample_preset.json'):
            self.assertIn(required, inner)
        for name in inner:
            self.assertNotIn('__pycache__', name)
            self.assertFalse(name.startswith(('data/', 'config/setup.json', 'config/print_config.json',
                                              'config/local_preset.json')), name)

    def test_cmd_files_use_windows_line_endings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = release.write_release_folder(Path(folder))
            data = (root / 'INSTALL.cmd').read_bytes()
        self.assertIn(b'\r\n', data)
        self.assertNotIn(b'\n', data.replace(b'\r\n', b''))


@unittest.skipUnless(IN_REPO, 'release builder is repository tooling')
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.source = release.write_release_folder(base / 'release')
        self.target = base / 'DealerRevenueRescue'

    def run_install(self, *extra):
        lines = []
        code = install.main(['--source', str(self.source), '--target', str(self.target),
                             '--skip-tests', '--no-shortcut', '--allow-non-windows', *extra],
                            out=lines.append)
        return code, '\n'.join(lines)

    @unittest.skipUnless(tk_works(), 'Tcl/Tk not available on this Python')
    def test_fresh_install_lays_out_app_store_and_start_files(self):
        code, output = self.run_install()
        self.assertEqual(code, 0, output)
        app = self.target / 'app'
        self.assertTrue((app / 'dealerpaperwork' / 'app_ui.py').is_file())
        self.assertFalse(list(app.rglob('__pycache__')))
        self.assertTrue((self.target / 'store').is_dir())
        record = json.loads((app / 'install.json').read_text(encoding='utf-8'))
        self.assertEqual(record['version'], release.release_version())
        start = (self.target / install.START_CMD).read_text(encoding='utf-8')
        self.assertIn(str(app / 'launch.pyw'), start)
        backup_cmd = (self.target / install.BACKUP_CMD).read_text(encoding='utf-8')
        self.assertIn('dealerpaperwork.backup create', backup_cmd)
        self.assertIn('dealerpaperwork.backup verify', backup_cmd)
        self.assertIn('DONE.', output)

    @unittest.skipUnless(tk_works(), 'Tcl/Tk not available on this Python')
    def test_reinstall_keeps_store_data_backs_it_up_and_keeps_previous_program(self):
        self.assertEqual(self.run_install()[0], 0)
        users = self.target / 'store' / 'data' / 'users.json'
        users.parent.mkdir(parents=True)
        users.write_text('{"accounts": "synthetic"}', encoding='utf-8')
        (self.target / 'app' / 'marker.txt').write_text('old program', encoding='utf-8')
        code, output = self.run_install()
        self.assertEqual(code, 0, output)
        self.assertEqual(users.read_text(encoding='utf-8'), '{"accounts": "synthetic"}')
        self.assertTrue((self.target / 'app-previous' / 'marker.txt').is_file())
        self.assertFalse((self.target / 'app' / 'marker.txt').exists())
        backups = list((self.target / 'store' / 'backups').glob('backup-*-before-install-*.zip'))
        self.assertEqual(len(backups), 1)
        with zipfile.ZipFile(backups[0]) as archive:
            self.assertEqual(archive.read('data/users.json'), b'{"accounts": "synthetic"}')

    @unittest.skipUnless(tk_works(), 'Tcl/Tk not available on this Python')
    def test_damaged_release_is_refused_before_anything_changes(self):
        path = self.source / 'src' / 'dealerpaperwork' / 'tax_engine.py'
        path.write_text(path.read_text(encoding='utf-8') + '\n# changed\n', encoding='utf-8')
        code, output = self.run_install()
        self.assertEqual(code, 1)
        self.assertIn('Damaged or changed: src/dealerpaperwork/tax_engine.py', output)
        self.assertFalse(self.target.exists())

    def test_skipped_tests_are_recorded_and_origin_check_is_named(self):
        from unittest import mock
        with mock.patch.object(install, 'check_python', return_value=[]):
            code, output = self.run_install()
        self.assertEqual(code, 0, output)
        record = json.loads((self.target / 'app' / 'install.json').read_text(encoding='utf-8'))
        self.assertIs(record['tests_run_before_install'], False)
        self.assertIn("check the zip's SHA-256 against the one on the release page", output)

    def test_install_record_says_tests_ran_by_default(self):
        target = Path(self.tmp.name) / 'direct'
        manifest = install.read_manifest(self.source)
        app = install.install_app(self.source, target, manifest, Path(sys.executable))
        record = json.loads((Path(app) / 'install.json').read_text(encoding='utf-8'))
        self.assertIs(record['tests_run_before_install'], True)

    def test_missing_manifest_is_refused(self):
        (self.source / install.MANIFEST_NAME).unlink()
        with self.assertRaises(install.InstallError):
            install.verify_release(self.source)

    def test_python_check_names_the_fix(self):
        self.assertEqual(install.check_python((3, 12), lambda: '8.6'), [])
        old = install.check_python((3, 9), lambda: '8.6')
        self.assertTrue(any('3.10 or newer' in p for p in old))

        def broken():
            raise RuntimeError('no Tcl')
        missing = install.check_python((3, 12), broken)
        self.assertTrue(any('tcl/tk and IDLE' in p for p in missing))

    def test_refuses_non_windows_without_the_test_flag(self):
        if os.name == 'nt':
            self.skipTest('this is the Windows host')
        lines = []
        code = install.main(['--source', str(self.source), '--target', str(self.target)], out=lines.append)
        self.assertEqual(code, 1)
        self.assertIn('Windows only', '\n'.join(lines))
        self.assertFalse(self.target.exists())

    @unittest.skipUnless(tk_works(), 'Tcl/Tk not available on this Python')
    def test_installed_launcher_check_runs_from_the_install_alone(self):
        self.assertEqual(self.run_install()[0], 0)
        env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        result = subprocess.run([sys.executable, str(self.target / 'app' / 'launch.pyw'), '--check'],
                                cwd=self.tmp.name, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Dealer Revenue Rescue ' + release.release_version() + ' ready', result.stdout)
        self.assertIn(str((self.target / 'store').resolve()), result.stdout)

    def test_launcher_logs_a_startup_failure_to_the_store(self):
        app = Path(self.tmp.name) / 'broken' / 'app'
        (app / 'dealerpaperwork').mkdir(parents=True)
        (app / 'dealerpaperwork' / '__init__.py').write_text('', encoding='utf-8')
        (app / 'dealerpaperwork' / 'app_ui.py').write_text('raise RuntimeError("synthetic start failure")\n', encoding='utf-8')
        (app / 'launch.pyw').write_text(install.LAUNCHER, encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'DISPLAY')}
        env['DP_NO_DIALOG'] = '1'
        result = subprocess.run([sys.executable, str(app / 'launch.pyw')], env=env,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 1)
        log = app.parent / 'store' / 'logs' / 'launch-error.txt'
        self.assertIn('synthetic start failure', log.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
