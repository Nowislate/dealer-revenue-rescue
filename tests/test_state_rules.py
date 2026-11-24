"""Tests for the store's tax and tag rule sheet (Decision 22).

Two kinds of proof:

1. Florida stays the same to the cent. The engines read a rule sheet
   now; Florida is the built-in sheet. Thousands of generated deals run
   through the engines as they stood before Decision 22 (frozen copies
   in tests/reference/) and through the sheet-driven engines, and every
   result must be identical, worksheet lines included.
2. A typed sheet does what it says. Each sample sheet is a MADE-UP
   state ("ZZ"), not any real state's law, with deals figured by hand
   in the comments, independently of the engine.

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import random
import sys
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dealerpaperwork import state_rules as sr
from dealerpaperwork.lease_engine import calculate_lease
from dealerpaperwork.models import DealInputs, LeaseInputs, TradeIn
from dealerpaperwork.presets import Preset, load_preset, validate_preset
from dealerpaperwork.tax_engine import calculate_retail_deal
from reference.florida_lease_engine_v1 import calculate_lease as florida_lease_v1
from reference.florida_tax_engine_v1 import calculate_retail_deal as florida_retail_v1

D = Decimal


def money(rng, low, high):
    return D(rng.randint(low * 100, high * 100)) / 100


def random_retail(rng):
    trade = None
    if rng.random() < 0.6:
        trade = TradeIn(allowance=money(rng, 0, 30000), payoff=money(rng, 0, 30000))
    return DealInputs(
        trade=trade,
        selling_price=money(rng, 1000, 90000),
        accessories=money(rng, 0, 3000) if rng.random() < 0.5 else D("0"),
        dealer_fee=money(rng, 0, 1500),
        other_taxable_fees=money(rng, 0, 300) if rng.random() < 0.2 else D("0"),
        dealer_discount=money(rng, 0, 5000) if rng.random() < 0.5 else D("0"),
        county_surtax_rate=rng.choice([D("0"), D("0.005"), D("0.01"), D("0.015"), D("0.02")]),
        government_fees=money(rng, 0, 600),
        electronic_tag_fee=money(rng, 0, 250),
        optional_products=money(rng, 0, 4000) if rng.random() < 0.5 else D("0"),
        manufacturer_rebate=money(rng, 0, 4000) if rng.random() < 0.3 else D("0"),
        cash_down_payment=money(rng, 0, 10000) if rng.random() < 0.6 else D("0"),
        term_months=rng.choice([None, 36, 48, 60, 72, 84]),
        annual_rate=rng.choice([None, D("0"), D("0.0299"), D("0.0749"), D("0.1299")]),
        days_to_first_payment=rng.choice([None, 30, 41, 45]),
    )


def random_lease(rng):
    msrp = money(rng, 20000, 90000)
    return LeaseInputs(
        msrp=msrp,
        dealer_discount=money(rng, 0, 4000),
        aftermarkets=money(rng, 0, 2000) if rng.random() < 0.3 else D("0"),
        predelivery_fee=money(rng, 0, 1500),
        acquisition_fee=money(rng, 0, 1095),
        license_fee=money(rng, 0, 500),
        cap_fees=money(rng, 0, 400),
        cash_down=money(rng, 0, 5000) if rng.random() < 0.5 else D("0"),
        total_rebates=money(rng, 0, 3000) if rng.random() < 0.3 else D("0"),
        trade_allowance=money(rng, 0, 10000) if rng.random() < 0.3 else D("0"),
        residual_base_msrp=msrp,
        residual_percent=D(rng.randint(40, 70)) / 100,
        term_months=rng.choice([24, 36, 39, 48]),
        money_factor=D(rng.randint(0, 400)) / 100000,
        county_surtax_rate=rng.choice([D("0"), D("0.005"), D("0.01"), D("0.015")]),
        upfront_charges=money(rng, 0, 2000),
    )


class FloridaUnchanged(unittest.TestCase):
    def test_retail_matches_pre_decision_22_engine(self):
        rng = random.Random(22)
        presets = [None, Preset(), Preset(state_tax_rate=D("0.06"))]
        for n in range(3000):
            inputs = random_retail(rng)
            preset = presets[n % len(presets)]
            with self.subTest(n=n):
                self.assertEqual(
                    calculate_retail_deal(inputs, preset=preset),
                    florida_retail_v1(inputs, preset=preset),
                )

    def test_lease_matches_pre_decision_22_engine(self):
        rng = random.Random(13)
        for n in range(2000):
            inputs = random_lease(rng)
            with self.subTest(n=n):
                self.assertEqual(
                    calculate_lease(inputs, preset=Preset()),
                    florida_lease_v1(inputs, preset=Preset()),
                )

    def test_default_preset_is_florida(self):
        self.assertIs(Preset().rules, sr.FLORIDA)
        self.assertIs(sr.rules_from_dict(None), sr.FLORIDA)

    def test_saved_florida_sheet_cannot_be_edited_in_the_file(self):
        data = sr.rules_to_dict(sr.FLORIDA)
        data["trade_credit"] = "none"
        data["local_cap"] = "1"
        self.assertIs(sr.rules_from_dict(data), sr.FLORIDA)


# Made-up sample state A: 5% state rate; the dealership's place picks
# the local rate (Sample Town, 2%); no local limit; trade credit up to
# $3,000; rebate off before tax; accessories, tag fee and products
# taxed; dealer fee and government fees not taxed.
SHEET_A = sr.StateRules(
    state="ZZ",
    local_by=sr.LOCAL_BY_DEALER,
    dealer_place="Sample Town",
    trade_credit=sr.TRADE_CAPPED,
    trade_credit_cap=D("3000"),
    rebate_before_tax=True,
    taxed=frozenset({sr.CHARGE_ACCESSORIES, sr.CHARGE_TAG_FEE, sr.CHARGE_PRODUCTS}),
)

# Made-up sample state B: 4% state; buyer's place picks the local rate,
# which applies to the first $1,600 only; no trade credit; most tax per
# deal $500; dealer fee and accessories taxed.
SHEET_B = sr.StateRules(
    state="ZZ",
    local_by=sr.LOCAL_BY_BUYER,
    local_cap=D("1600"),
    max_tax=D("500"),
    trade_credit=sr.TRADE_NONE,
    taxed=frozenset({sr.CHARGE_DEALER_FEE, sr.CHARGE_ACCESSORIES}),
)


def preset_for(rules, state_rate, places):
    return Preset(rules=rules, state_tax_rate=D(state_rate),
                  county_surtax_rates={k: D(v) for k, v in places.items()})


class TypedSheets(unittest.TestCase):
    def test_sheet_a_by_hand(self):
        preset = preset_for(SHEET_A, "0.05", {"Sample Town": "0.02"})
        inputs = DealInputs(
            trade=TradeIn(allowance=D("5000"), payoff=D("2000")),
            selling_price=D("20000"), accessories=D("500"), dealer_fee=D("800"),
            electronic_tag_fee=D("100"), dealer_discount=D("1000"),
            government_fees=D("300"), optional_products=D("1500"),
            manufacturer_rebate=D("1000"), cash_down_payment=D("2000"),
            county_surtax_rate=preset.local_rate_for("Anywhere Else"),
        )
        r = calculate_retail_deal(inputs, preset=preset)
        # gross 20000+500+800+100 = 21400; less discount 20400; less the
        # untaxed dealer fee 19600; plus taxed products 21100; less trade
        # credit (capped) 3000 = 18100; less rebate 1000 = 17100.
        self.assertEqual(r.taxable_base, D("17100.00"))
        self.assertEqual(r.state_tax, D("855.00"))       # 5%
        self.assertEqual(r.county_surtax, D("342.00"))   # 2%, Sample Town
        self.assertEqual(r.total_tax, D("1197.00"))
        # 21400 - 1000 - 5000 + 1197 + 300 + 1500 - 1000 - 2000
        self.assertEqual(r.amount_due, D("15397.00"))
        self.assertEqual(r.warnings, ())
        labels = [label for label, _ in r.lines]
        self.assertIn("Trade-in credit against tax", labels)
        self.assertIn("Local tax", labels)
        self.assertNotIn("County surtax", labels)

    def test_sheet_b_by_hand(self):
        preset = preset_for(SHEET_B, "0.04", {"Sample County": "0.025"})
        inputs = DealInputs(
            trade=TradeIn(allowance=D("4000")),
            selling_price=D("30000"), dealer_fee=D("500"),
            county_surtax_rate=preset.local_rate_for("Sample County"),
        )
        r = calculate_retail_deal(inputs, preset=preset)
        # No trade credit: base 30500. State 4% = 1220.00; local 2.5% of
        # the first 1600 = 40.00; 1260.00 held to the $500 limit.
        self.assertEqual(r.taxable_base, D("30500.00"))
        self.assertEqual(r.state_tax, D("1220.00"))
        self.assertEqual(r.surtax_base, D("1600"))
        self.assertEqual(r.county_surtax, D("40.00"))
        self.assertEqual(r.total_tax, D("500.00"))
        self.assertIn(("Tax limit (most $500.00)", D("-760.00")), r.lines)
        # 30500 - 4000 + 500
        self.assertEqual(r.amount_due, D("27000.00"))

    def test_no_tax_state(self):
        rules = sr.StateRules(state="ZZ", tax_kind=sr.TAX_NONE, local_by=sr.LOCAL_NONE)
        preset = preset_for(rules, "0.06", {})
        inputs = DealInputs(selling_price=D("10000"), dealer_fee=D("500"),
                            county_surtax_rate=preset.local_rate_for(""))
        r = calculate_retail_deal(inputs, preset=preset)
        self.assertEqual(r.total_tax, D("0.00"))
        self.assertEqual(r.amount_due, D("10500.00"))

    def lease(self, **extra):
        return LeaseInputs(
            msrp=D("30000"), residual_base_msrp=D("30000"),
            residual_percent=D("0.60"), term_months=36,
            money_factor=D("0.002"), county_surtax_rate=D("0.01"), **extra,
        )

    def test_lease_tax_up_front_on_total_of_payments(self):
        rules = sr.StateRules(state="ZZ", lease_tax=sr.LEASE_UPFRONT_TOTAL)
        r = calculate_lease(self.lease(), preset=preset_for(rules, "0.05", {}))
        # depreciation 12000/36 = 333.333..; rent 48000 x 0.002 = 96
        self.assertEqual(r.base_monthly_payment, D("429.33"))
        self.assertEqual(r.monthly_tax, D("0.00"))
        # 429.33 x 36 = 15455.88 x 6% = 927.3528
        self.assertEqual(r.upfront_tax, D("927.35"))
        self.assertEqual(r.due_on_delivery, D("1356.68"))
        self.assertIn(("Tax due at signing", D("927.35")), r.lines)

    def test_lease_down_payment_taxed_up_front(self):
        rules = sr.StateRules(state="ZZ", lease_tax=sr.LEASE_UPFRONT_TOTAL,
                              lease_reduction_taxed=True)
        r = calculate_lease(self.lease(cash_down=D("1000")),
                            preset=preset_for(rules, "0.05", {}))
        # net cap 29000; depreciation 11000/36 = 305.555..; rent 47000 x
        # 0.002 = 94 -> 399.56. 399.56 x 36 = 14384.16 x 6% = 863.0496
        # -> 863.05, plus 6% of the 1000 down = 60.00.
        self.assertEqual(r.base_monthly_payment, D("399.56"))
        self.assertEqual(r.upfront_tax, D("923.05"))
        self.assertEqual(r.due_on_delivery, D("1322.61"))

    def test_lease_tax_up_front_on_selling_price(self):
        rules = sr.StateRules(state="ZZ", lease_tax=sr.LEASE_UPFRONT_PRICE)
        r = calculate_lease(self.lease(), preset=preset_for(rules, "0.05", {}))
        self.assertEqual(r.upfront_tax, D("1800.00"))  # 30000 x 6%
        self.assertEqual(r.total_tax, D("1800.00"))

    def test_lease_no_tax(self):
        rules = sr.StateRules(state="ZZ", lease_tax=sr.LEASE_NONE)
        r = calculate_lease(self.lease(), preset=preset_for(rules, "0.05", {}))
        self.assertEqual(r.monthly_tax, D("0.00"))
        self.assertEqual(r.monthly_payment, r.base_monthly_payment)


class LocalPlace(unittest.TestCase):
    def test_dealer_place_ignores_the_buyer(self):
        preset = preset_for(SHEET_A, "0.05", {"Sample Town": "0.02"})
        self.assertFalse(preset.needs_buyer_place)
        self.assertEqual(preset.local_rate_for(""), D("0.02"))

    def test_buyer_place_must_be_in_the_table(self):
        preset = preset_for(SHEET_B, "0.04", {"Sample County": "0.025"})
        self.assertTrue(preset.needs_buyer_place)
        with self.assertRaises(KeyError):
            preset.local_rate_for("Other County")
        with self.assertRaises(KeyError):
            preset.local_rate_for(" ")


class Validation(unittest.TestCase):
    def test_none_of_these_fit_stops_setup(self):
        for rules in (sr.StateRules(state="ZZ", tax_kind=sr.UNSUPPORTED),
                      sr.StateRules(state="ZZ", lease_tax=sr.UNSUPPORTED)):
            self.assertEqual(sr.validate_rules(rules), [sr.UNSUPPORTED_MESSAGE])

    def test_problems_are_named(self):
        bad = sr.StateRules(state="ZZ", local_by=sr.LOCAL_BY_DEALER,
                            dealer_place="Nowhere", trade_credit=sr.TRADE_CAPPED,
                            local_cap=D("0"), taxed=frozenset({"cupholders"}))
        errors = " ".join(sr.validate_rules(bad, {"Sample Town": D("0.01")}))
        for words in ("not in the local rate table", "trade-in credit limit",
                      "local tax limit", "unknown charge"):
            self.assertIn(words, errors)

    def test_florida_and_samples_pass(self):
        for rules in (sr.FLORIDA, SHEET_B):
            self.assertEqual(sr.validate_rules(rules), [])
        self.assertEqual(sr.validate_rules(SHEET_A, {"Sample Town": 1}), [])

    def test_no_tax_state_needs_no_rate_table(self):
        rules = sr.StateRules(state="ZZ", tax_kind=sr.TAX_NONE, local_by=sr.LOCAL_NONE)
        errors = validate_preset(preset_for(rules, "0", {}))
        self.assertFalse([e for e in errors if "surtax" in e or "State tax" in e])

    def test_sheet_round_trips_through_the_preset_file(self):
        import json
        import tempfile
        from dealerpaperwork.setup_wizard import preset_to_dict
        preset = preset_for(SHEET_A, "0.05", {"Sample Town": "0.02"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preset.json"
            path.write_text(json.dumps(preset_to_dict(preset)), encoding="utf-8")
            self.assertEqual(load_preset(path).rules, SHEET_A)


if __name__ == "__main__":
    unittest.main()
