"""Tests for the append-only audit log (Stage 3).

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.audit import (
    EVENT_DEAL_CERTIFIED,
    EVENT_DEAL_CREATED,
    AuditLog,
)


class TestAuditLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "audit.jsonl"
        self.log = AuditLog(self.path)

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(self.log.read_events(), [])

    def test_record_and_read_round_trip(self):
        self.log.record(EVENT_DEAL_CREATED, "fay", {"deal_id": "abc123"})
        events = self.log.read_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_type, EVENT_DEAL_CREATED)
        self.assertEqual(event.actor, "fay")
        self.assertEqual(event.details, {"deal_id": "abc123"})
        self.assertTrue(event.timestamp)  # a timestamp was stamped

    def test_appends_in_order_and_never_rewrites(self):
        self.log.record(EVENT_DEAL_CREATED, "fay", {"deal_id": "1"})
        self.log.record(EVENT_DEAL_CERTIFIED, "fay", {"deal_id": "1"})
        # A fresh reader over the same file sees both, in order.
        events = AuditLog(self.path).read_events()
        self.assertEqual(
            [e.event_type for e in events],
            [EVENT_DEAL_CREATED, EVENT_DEAL_CERTIFIED],
        )
        lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)  # one JSON object per line

    def test_record_creates_parent_directories(self):
        deep = Path(self.tmp.name) / "nested" / "dir" / "audit.jsonl"
        AuditLog(deep).record(EVENT_DEAL_CREATED, "fay", {})
        self.assertTrue(deep.exists())


if __name__ == "__main__":
    unittest.main()
