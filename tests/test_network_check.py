"""Tests for the Step 0.1 LAN check (Stage 2).

All tests run against injected address lists, so nothing here touches
the real network stack or attempts any traffic -- the check itself is
designed to read local addresses only (locked decision 5).

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.network_check import (
    NetworkCheckResult,
    check_lan,
    evaluate_addresses,
)


class TestEvaluateAddresses(unittest.TestCase):
    def test_private_lan_address_is_ok(self):
        for ip in ("192.168.1.20", "10.0.0.5", "172.16.4.9", "172.31.255.1"):
            result = evaluate_addresses([ip])
            self.assertTrue(result.ok, ip)
            self.assertEqual(result.local_ip, ip)

    def test_loopback_only_is_not_ok(self):
        result = evaluate_addresses(["127.0.0.1"])
        self.assertFalse(result.ok)
        self.assertIsNone(result.local_ip)

    def test_public_address_only_is_not_a_lan(self):
        result = evaluate_addresses(["203.0.113.10"])
        self.assertFalse(result.ok)

    def test_no_addresses_is_not_ok(self):
        self.assertFalse(evaluate_addresses([]).ok)

    def test_garbage_and_ipv6_are_ignored(self):
        result = evaluate_addresses(["not-an-ip", "::1", "fe80::1"])
        self.assertFalse(result.ok)
        result = evaluate_addresses(["::1", "192.168.0.2"])
        self.assertTrue(result.ok)
        self.assertEqual(result.local_ip, "192.168.0.2")

    def test_private_wins_over_public(self):
        result = evaluate_addresses(["203.0.113.10", "192.168.1.5"])
        self.assertTrue(result.ok)
        self.assertEqual(result.local_ip, "192.168.1.5")


class TestCheckLan(unittest.TestCase):
    def test_injected_addresses(self):
        result = check_lan(["192.168.10.10"])
        self.assertIsInstance(result, NetworkCheckResult)
        self.assertTrue(result.ok)

    def test_real_local_check_returns_a_result(self):
        # Whatever this machine's addresses are, the result must be
        # structured and immediate.
        result = check_lan()
        self.assertIsInstance(result, NetworkCheckResult)
        self.assertTrue(result.message)


if __name__ == "__main__":
    unittest.main()
