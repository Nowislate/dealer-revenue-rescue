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

from unittest import mock

from dealerpaperwork import network_check
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




class TestLocalAddressLookup(unittest.TestCase):
    """The only OS call: a lookup of this host's own name. No socket is
    created and no connection is opened."""

    def test_looks_up_only_the_own_hostname_and_opens_no_socket(self):
        infos = [
            (2, 1, 6, "", ("192.168.1.20", 0)),
            (2, 2, 17, "", ("192.168.1.20", 0)),
            (2, 1, 6, "", ("10.0.0.5", 0)),
        ]
        with mock.patch.object(network_check.socket, "gethostname", return_value="FRONT-PC"), \
                mock.patch.object(network_check.socket, "getaddrinfo", return_value=infos) as lookup, \
                mock.patch.object(network_check.socket, "socket") as make_socket, \
                mock.patch.object(network_check.socket, "create_connection") as connect:
            addresses = network_check.local_ipv4_addresses()
        self.assertEqual(addresses, ["10.0.0.5", "192.168.1.20"])
        lookup.assert_called_once_with("FRONT-PC", None, network_check.socket.AF_INET)
        make_socket.assert_not_called()
        connect.assert_not_called()

    def test_failed_lookup_means_no_addresses(self):
        with mock.patch.object(network_check.socket, "getaddrinfo",
                               side_effect=network_check.socket.gaierror):
            self.assertEqual(network_check.local_ipv4_addresses(), [])


if __name__ == "__main__":
    unittest.main()
