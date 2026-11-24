import importlib
import importlib.util
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class NativeLauncherTests(unittest.TestCase):
    def test_all_public_ui_launchers_enter_shared_gate(self):
        self.assertIsNotNone(importlib.util.find_spec('dealerpaperwork.app_ui'), 'shared app launcher is missing')
        for module, function, action in (
            ('setup_ui', 'run_setup_wizard', 'setup'),
            ('deal_ui', 'run_deal_walkthrough', 'retail'),
            ('lease_ui', 'run_lease_walkthrough', 'lease'),
            ('docs_ui', 'run_documents', 'documents'),
            ('print_ui', 'run_print_pipeline', 'print'),
        ):
            with self.subTest(module=module), patch('dealerpaperwork.app_ui.run_app', return_value=0) as gate:
                getattr(importlib.import_module('dealerpaperwork.' + module), function)()
                gate.assert_called_once()
                self.assertEqual(gate.call_args.kwargs['initial_action'], action)

    def test_shared_launcher_explains_missing_tk_without_mutating_data(self):
        self.assertIsNotNone(importlib.util.find_spec('dealerpaperwork.app_ui'), 'shared app launcher is missing')
        import dealerpaperwork.app_ui as app
        import tempfile
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'TK_AVAILABLE', False):
            self.assertEqual(app.run_app(data_root=Path(folder)), 1)
            self.assertEqual(list(Path(folder).iterdir()), [])
