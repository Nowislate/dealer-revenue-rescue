"""The five tax shapes the first twelve rule questions could not say
(decision 30, Lane 2026-11-24): extra tax on a dollar band, a state
rate set by price, a rate set by vehicle class, a least tax per deal,
and tax on a state-set value.

Each sheet is shaped like a real state's rule, with made-up rates where
the real one was not verified; every expected figure is worked by hand
in the comments, independently of the engine.
"""

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork import state_rules as sr
from dealerpaperwork.models import D, DealInputs, TradeIn, Vehicle, vehicle_from_dict, vehicle_to_dict
from dealerpaperwork.presets import Preset
from dealerpaperwork.tax_engine import TaxInputError, calculate_retail_deal

BASE = sr.StateRules(state="ZZ", local_by=sr.LOCAL_BY_BUYER, trade_credit=sr.TRADE_FULL,
                     taxed=frozenset({sr.CHARGE_DEALER_FEE}))


def preset_for(rules, rate="0.07", local="0"):
    return Preset(rules=rules, state_tax_rate=D(rate),
                  county_surtax_rates={"Home": D(local)})


def deal(price, *, fee="0", trade="0", discount="0", vehicle=None, local="0"):
    return DealInputs(selling_price=D(price), dealer_fee=D(fee), dealer_discount=D(discount),
                      trade=TradeIn(allowance=D(trade)) if D(trade) else None,
                      vehicle=vehicle or Vehicle(), county_surtax_rate=D(local))


class Bands(unittest.TestCase):
    # Tennessee's shape: state 7% on all, local 2.25% on the first
    # $1,600, plus 2.75% on $1,600 to $3,200.
    RULES = replace(BASE, local_cap=D("1600"),
                    bands=(sr.TaxBand(D("0.0275"), D("1600"), D("3200")),))

    def test_band_on_a_normal_deal(self):
        r = calculate_retail_deal(deal("30000", local="0.0225"), preset=preset_for(self.RULES))
        # 7% of 30000 = 2100.00; band 2.75% of 1600 = 44.00; local 2.25%
        # of 1600 = 36.00; total 2180.00.
        self.assertEqual(r.state_tax, D("2100.00"))
        self.assertEqual(r.county_surtax, D("36.00"))
        self.assertEqual(r.total_tax, D("2180.00"))
        self.assertIn(("Extra state tax (2.75% on $1,600.00 to $3,200.00)", D("44.00")), r.lines)

    def test_band_on_a_small_deal(self):
        r = calculate_retail_deal(deal("2000", local="0.0225"), preset=preset_for(self.RULES))
        # 7% of 2000 = 140.00; band 2.75% of 400 = 11.00; local 36.00.
        self.assertEqual(r.total_tax, D("187.00"))

    def test_band_below_its_bottom_is_zero(self):
        r = calculate_retail_deal(deal("1000"), preset=preset_for(self.RULES))
        self.assertEqual(r.total_tax, D("70.00"))


class Brackets(unittest.TestCase):
    # Arkansas's shape, used cars only: 0% under $4,000, 3.5% from
    # $4,000, 6.5% from $10,000 (the middle rate is made up here).
    RULES = replace(BASE, brackets_used_only=True, brackets=(
        sr.RateBracket(D("0"), D("0")), sr.RateBracket(D("4000"), D("0.035")),
        sr.RateBracket(D("10000"), D("0.065"))))
    USED = Vehicle(is_new=False)

    def tax(self, price, vehicle=None, **kw):
        return calculate_retail_deal(deal(price, vehicle=vehicle or self.USED, **kw),
                                     preset=preset_for(self.RULES, rate="0.065"))

    def test_each_bracket(self):
        self.assertEqual(self.tax("3999").total_tax, D("0.00"))
        self.assertEqual(self.tax("4000").total_tax, D("140.00"))   # 3.5%
        self.assertEqual(self.tax("9999").total_tax, D("349.97"))   # 3.5% = 349.965
        self.assertEqual(self.tax("10000").total_tax, D("650.00"))  # 6.5%

    def test_bracket_reads_price_after_discount_but_taxes_the_base(self):
        # 10500 - 1000 discount = 9500 -> 3.5% bracket. Base after a
        # 2000 trade = 7500; 3.5% = 262.50.
        r = self.tax("10500", discount="1000", trade="2000")
        self.assertEqual(r.taxable_base, D("7500.00"))
        self.assertEqual(r.total_tax, D("262.50"))
        self.assertIn(("State tax (3.5%, price bracket)", D("262.50")), r.lines)

    def test_new_cars_keep_the_state_rate(self):
        self.assertEqual(self.tax("5000", vehicle=Vehicle(is_new=True)).total_tax, D("325.00"))


class Classes(unittest.TestCase):
    # Mississippi's shape: 5%, trucks over 10,000 lb GVW at 3%.
    RULES = replace(BASE, classes=(sr.TaxClass("Truck over 10,000 lb GVW", D("0.03")),))

    def test_class_rate_and_standard_rate(self):
        preset = preset_for(self.RULES, rate="0.05")
        truck = Vehicle(tax_class="Truck over 10,000 lb GVW")
        self.assertEqual(calculate_retail_deal(deal("60000", vehicle=truck), preset=preset).total_tax,
                         D("1800.00"))
        self.assertEqual(calculate_retail_deal(deal("60000"), preset=preset).total_tax,
                         D("3000.00"))

    def test_unknown_class_stops(self):
        with self.assertRaises(TaxInputError):
            calculate_retail_deal(deal("1", vehicle=Vehicle(tax_class="Boat")),
                                  preset=preset_for(self.RULES))


class LeastTax(unittest.TestCase):
    # Virginia's shape: 4.15% of the price, no trade credit, at least $75.
    RULES = replace(BASE, trade_credit=sr.TRADE_NONE, min_tax=D("75"), taxed=frozenset())

    def test_least_tax_lifts_a_small_deal(self):
        r = calculate_retail_deal(deal("1000"), preset=preset_for(self.RULES, rate="0.0415"))
        # 4.15% of 1000 = 41.50 -> 75.00 (33.50 added).
        self.assertEqual(r.total_tax, D("75.00"))
        self.assertIn(("Least tax ($75.00)", D("33.50")), r.lines)
        self.assertEqual(r.amount_due, D("1075.00"))

    def test_least_tax_leaves_a_normal_deal(self):
        r = calculate_retail_deal(deal("40000", trade="5000"),
                                  preset=preset_for(self.RULES, rate="0.0415"))
        self.assertEqual(r.total_tax, D("1660.00"))  # no trade credit


class StateValue(unittest.TestCase):
    # Georgia's shape, used cars: 7% of the state's value, less the trade.
    RULES = replace(BASE, value_rule=sr.VALUE_STATE, value_used_only=True, taxed=frozenset())

    def test_state_value_replaces_the_price(self):
        car = Vehicle(is_new=False, state_value=D("18000"))
        r = calculate_retail_deal(deal("20000", discount="500", trade="3000", vehicle=car),
                                  preset=preset_for(self.RULES))
        # 18000 - 3000 trade = 15000; 7% = 1050.00. Amount due still
        # uses the real price: 20000 - 500 - 3000 + 1050 = 17550.
        self.assertEqual(r.taxable_base, D("15000.00"))
        self.assertEqual(r.total_tax, D("1050.00"))
        self.assertEqual(r.amount_due, D("17550.00"))

    def test_greater_of_price_and_value(self):
        rules = replace(self.RULES, value_rule=sr.VALUE_GREATER)
        low = Vehicle(is_new=False, state_value=D("15000"))
        high = Vehicle(is_new=False, state_value=D("25000"))
        self.assertEqual(calculate_retail_deal(deal("20000", vehicle=low),
                                               preset=preset_for(rules)).taxable_base, D("20000.00"))
        self.assertEqual(calculate_retail_deal(deal("20000", vehicle=high),
                                               preset=preset_for(rules)).taxable_base, D("25000.00"))

    def test_missing_value_stops_and_new_cars_need_none(self):
        with self.assertRaises(TaxInputError):
            calculate_retail_deal(deal("20000", vehicle=Vehicle(is_new=False)),
                                  preset=preset_for(self.RULES))
        r = calculate_retail_deal(deal("20000", vehicle=Vehicle(is_new=True)),
                                  preset=preset_for(self.RULES))
        self.assertEqual(r.taxable_base, D("20000.00"))


class SheetChecksAndSaving(unittest.TestCase):
    FULL = replace(BASE, min_tax=D("75"), value_rule=sr.VALUE_GREATER, value_used_only=False,
                   bands=(sr.TaxBand(D("0.0275"), D("1600"), D("3200")),),
                   brackets=(sr.RateBracket(D("0"), D("0.05")),),
                   brackets_used_only=True,
                   classes=(sr.TaxClass("Heavy truck", D("0.03")),))

    def test_a_full_sheet_saves_and_reads_back(self):
        self.assertEqual(sr.validate_rules(self.FULL), [])
        self.assertEqual(sr.rules_from_dict(sr.rules_to_dict(self.FULL)), self.FULL)

    def test_a_sheet_without_shapes_saves_as_before(self):
        # Same keys as a sheet saved before decision 30, so approvals and
        # math-check fingerprints of existing stores do not reopen.
        self.assertEqual(set(sr.rules_to_dict(BASE)), {
            "state", "built_in", "tax_kind", "local_by", "dealer_place", "local_cap",
            "max_tax", "trade_credit", "trade_credit_cap", "rebate_before_tax", "taxed",
            "lease_tax", "lease_reduction_taxed"})
        self.assertEqual(sr.rules_from_dict(sr.rules_to_dict(BASE)), BASE)

    def test_bad_answers_are_named(self):
        bad = [
            replace(BASE, bands=(sr.TaxBand(D("0.02"), D("3000"), D("1000")),)),
            replace(BASE, brackets=(sr.RateBracket(D("4000"), D("0.03")),)),
            replace(BASE, classes=(sr.TaxClass("", D("0.03")),)),
            replace(BASE, min_tax=D("0")),
            replace(BASE, min_tax=D("600"), max_tax=D("500")),
            replace(BASE, tax_kind=sr.TAX_NONE, min_tax=D("75")),
        ]
        for rules in bad:
            self.assertTrue(sr.validate_rules(rules), rules)

    def test_an_old_math_check_keeps_its_fingerprint_fields(self):
        from dealerpaperwork import math_check as mc
        entry = mc.RetailCheckEntry(selling_price=D("1"))
        self.assertFalse({"used_vehicle", "tax_class", "state_value"} & set(mc.entry_to_dict(entry)))
        shaped = replace(entry, used_vehicle=True, tax_class="Heavy truck", state_value=D("9"))
        self.assertEqual(mc.entry_from_dict(mc.KIND_RETAIL, mc.entry_to_dict(shaped)), shaped)

    def test_typed_lines_parse_and_print_back(self):
        from dealerpaperwork import math_check as mc
        bands = mc.parse_bands("2.75, 1600, 3200\n\n1, 5000")
        self.assertEqual(bands, (sr.TaxBand(D("0.0275"), D("1600"), D("3200")),
                                 sr.TaxBand(D("0.01"), D("5000"), None)))
        self.assertEqual(mc.parse_bands(mc.bands_text(bands)), bands)
        brackets = mc.parse_brackets("0, 0\n4000, 3.5")
        self.assertEqual(mc.parse_brackets(mc.brackets_text(brackets)), brackets)
        classes = mc.parse_classes("Truck over 10,000 lb GVW, 3")
        self.assertEqual(classes, (sr.TaxClass("Truck over 10,000 lb GVW", D("0.03")),))
        self.assertEqual(mc.parse_classes(mc.classes_text(classes)), classes)
        with self.assertRaises(ValueError):
            mc.parse_classes("Heavy truck")

    def test_vehicle_class_and_value_are_saved_with_the_deal(self):
        car = Vehicle(is_new=False, tax_class="Heavy truck", state_value=D("1234.56"))
        self.assertEqual(vehicle_from_dict(vehicle_to_dict(car)), car)
        self.assertNotIn("state_value", vehicle_to_dict(Vehicle()))


if __name__ == "__main__":
    unittest.main()
