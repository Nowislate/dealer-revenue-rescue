"""Proof that a print job is drawn through a real Windows printer driver.

Runs only on Windows with DP_PRINT_CHECK=1 (set by the CI Windows job),
so the installer's own test run on a store PC never prints anything.
The printer is "Microsoft Print to PDF", a driver that ships with
Windows and does not read PDF itself: it receives the drawn pages
exactly as a laser printer's driver would, and writes them to a file
we can inspect. A job that reaches that file went through the driver
path end to end.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.demo_form import add_demo_form  # noqa: E402
from dealerpaperwork.doc_library import DocumentLibrary, sample_field_values  # noqa: E402
from dealerpaperwork.pdf_overlay import render_overlay_pdf  # noqa: E402
from dealerpaperwork.text_renderer import render_test_page  # noqa: E402

PRINTER = "Microsoft Print to PDF"
RUN = sys.platform.startswith("win") and os.environ.get("DP_PRINT_CHECK") == "1"


def _wait_for(path: Path, seconds: float = 90.0) -> bytes:
    """The spooler writes the file after the job ends; wait until it
    exists and its size stops changing."""
    deadline = time.monotonic() + seconds
    last = -1
    while time.monotonic() < deadline:
        if path.exists():
            size = path.stat().st_size
            if size > 0 and size == last:
                return path.read_bytes()
            last = size
        time.sleep(1.0)
    raise AssertionError(f"{path} was never written by the printer driver.")


@unittest.skipUnless(RUN, "Windows CI only (DP_PRINT_CHECK=1)")
class DriverPrintTest(unittest.TestCase):
    def setUp(self):
        from dealerpaperwork.printers import WindowsPrinterProvider

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        names = WindowsPrinterProvider().list_printers()
        self.assertIn(PRINTER, names, f"printers on this machine: {names}")
        self.provider_class = WindowsPrinterProvider

    def print_to_file(self, name: str, payload, copies: int = 1) -> bytes:
        out = self.root / name
        provider = self.provider_class(output_file=str(out))
        result = provider.print_document(PRINTER, payload, doc_id=name,
                                         title=name, copies=copies)
        self.assertTrue(result.ok, result.message)
        self.assertIsNotNone(result.job_id)
        data = _wait_for(out)
        self.assertTrue(data.startswith(b"%PDF"), data[:20])
        return data

    def test_demo_form_with_its_picture_prints_through_the_driver(self):
        library = DocumentLibrary(self.root / "documents")
        document = add_demo_form(library)
        pdf = render_overlay_pdf(document, sample_field_values(), banner="SAMPLE",
                                 page_images=library.load_page_images(document))
        form = self.print_to_file("demo-form.pdf", pdf)
        text = self.print_to_file("test-page.pdf", render_test_page(
            printer_name=PRINTER, doc_id="test", title="Test page"))
        # The form pages carry a full-page picture; the text page does not.
        self.assertGreater(len(form), 10 * len(text))
        if os.environ.get("RUNNER_TEMP"):  # kept as a CI artifact to look at
            proof = Path(os.environ["RUNNER_TEMP"], "driver-print-proof")
            proof.mkdir(exist_ok=True)
            (proof / "demo-form.pdf").write_bytes(form)
            (proof / "test-page.pdf").write_bytes(text)

    def test_a_foreign_payload_is_refused_before_the_printer(self):
        provider = self.provider_class(output_file=str(self.root / "never.pdf"))
        result = provider.print_document(PRINTER, b"%PDF-1.7 not ours", doc_id="x")
        self.assertFalse(result.ok)
        self.assertIn("Not printed", result.message)
        self.assertFalse((self.root / "never.pdf").exists())


if __name__ == "__main__":
    unittest.main()
