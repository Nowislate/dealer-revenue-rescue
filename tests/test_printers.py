"""Tests for the printer provider interface (Stage 2).

Headless: only the fake provider runs here. The Windows provider's
spooler path is Windows-only code and is validated on real hardware at
the dealer site; here we only prove the module imports cleanly and the
factory picks the right provider for this platform.

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.printers import (
    FakePrinterProvider,
    WindowsPrinterProvider,
    get_printer_provider,
)


class TestFakePrinterProvider(unittest.TestCase):
    def test_configured_names_are_listed(self):
        provider = FakePrinterProvider(["Printer A", "Printer B"])
        self.assertEqual(provider.list_printers(), ["Printer A", "Printer B"])

    def test_default_names_exist(self):
        self.assertTrue(FakePrinterProvider().list_printers())

    def test_list_is_a_copy(self):
        provider = FakePrinterProvider(["Printer A"])
        provider.list_printers().append("Injected")
        self.assertEqual(provider.list_printers(), ["Printer A"])

    def test_test_print_records_and_reports_ok(self):
        provider = FakePrinterProvider(["Printer A"])
        result = provider.send_test_print("Printer A")
        self.assertTrue(result.ok)
        self.assertEqual(provider.test_prints, ["Printer A"])

    def test_test_print_unknown_printer_fails(self):
        provider = FakePrinterProvider(["Printer A"])
        result = provider.send_test_print("Nope")
        self.assertFalse(result.ok)
        self.assertEqual(provider.test_prints, [])


class TestFactory(unittest.TestCase):
    def test_factory_matches_platform(self):
        provider = get_printer_provider()
        if sys.platform.startswith("win"):
            self.assertIsInstance(provider, WindowsPrinterProvider)
        else:
            self.assertIsInstance(provider, FakePrinterProvider)


if __name__ == "__main__":
    unittest.main()
