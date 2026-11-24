import importlib
import importlib.util
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class FileTransactionTests(unittest.TestCase):
    def test_reentrant_transactions_serialize_threads_sharing_a_file(self):
        self.assertIsNotNone(importlib.util.find_spec('dealerpaperwork.file_transactions'))
        transaction = importlib.import_module('dealerpaperwork.file_transactions').file_transaction
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'counter.json'
            path.write_text('0')
            def increment():
                for _ in range(12):
                    with transaction(path):
                        with transaction(path):
                            value = int(path.read_text())
                            time.sleep(0.001)
                            path.write_text(str(value + 1))
            with ThreadPoolExecutor(max_workers=3) as pool:
                jobs = [pool.submit(increment) for _ in range(3)]
                for job in jobs:
                    job.result(timeout=10)
            self.assertEqual(path.read_text(), '36')
