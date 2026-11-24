"""Setup's math check (decision 21): the app against the store's DMS.

Both check deals are SYNTHETIC (decision 17), with figures computed
independently of the app:

* Retail: the synthetic finance deal of tests/test_payment_terms.py
  (hand-computed tax and amount financed; payment solved from the
  Regulation Z Appendix J equation by bisection): tax 868.98, amount
  financed 16743.83, 72 months at 7.49% with a 41-day first payment
  = 290.08.
* Lease: fixture A of tests/test_lease_engine.py at a 1% county rate:
  base payment 489.09, monthly tax 34.24, payment 523.33.

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dealerpaperwork import math_check as mc
from dealerpaperwork.audit import AuditLog
from dealerpaperwork.deal_flow import preset_from_setup_file
from dealerpaperwork.presets import validate_lease_preset
from dealerpaperwork.setup_wizard import SetupStep
from test_setup_wizard import WizardTestCase

D = Decimal


def retail_entry(**overrides) -> mc.RetailCheckEntry:
    entry = mc.RetailCheckEntry(
        county="Sample County",
        selling_price=D("16250"), accessories=D("85"),
        trade_allowance=D("4500"), trade_payoff=D("2100"),
        government_fees=D("146.85"), optional_products=D("895"),
        cash_down_payment=D("500"),
        term_months=72, annual_rate=D("0.0749"), days_to_first_payment=41,
        dms_total_tax=D("868.98"), dms_amount_financed=D("16743.83"),
        dms_monthly_payment=D("290.08"),
    )
    for key, value in overrides.items():
        setattr(entry, key, value)
    return entry


def lease_entry(**overrides) -> mc.LeaseCheckEntry:
    entry = mc.LeaseCheckEntry(
        county="Lease County",
        msrp=D("34995"), dealer_discount=D("2000"),
        acquisition_fee=D("650"), cap_fees=D("60"),
        residual_base_msrp=D("34995"), residual_percent=D("0.61"),
        term_months=36, money_factor=D("0.00200"),
        dms_base_payment=D("489.09"), dms_monthly_tax=D("34.24"),
        dms_monthly_payment=D("523.33"),
    )
    for key, value in overrides.items():
        setattr(entry, key, value)
    return entry


class MathCheckCase(WizardTestCase):
    def setUp(self):
        super().setUp()
        self.audit = AuditLog(Path(self.tmp.name) / "audit.jsonl")
        self.wizard.audit_log = self.audit

    def to_math_check(self, *, leases=False):
        self.assertEqual(self.pass_network(), [])
        self.assertEqual(self.pass_profile(), [])
        self.assertEqual(self.pass_printer(), [])
        self.assertEqual(self.pass_trays(), [])
        self.assertEqual(self.pass_preflight(), [])
        self.assertEqual(self.pass_state_rules(), [])
        self.assertEqual(self.wizard.current_step, SetupStep.TAX_RATES)
        self.wizard.set_tax_data(county_surtax_rates={
            "Sample County": D("0.015"), "Lease County": D("0.01"),
        })
        self.wizard.set_store_leases(
            leases, lessor_name="Sample Lessor (SAMPLE)",
            title_holder="Sample Lessor (SAMPLE)",
        )
        self.assertEqual(self.wizard.advance(), [])
        self.assertEqual(self.wizard.current_step, SetupStep.MATH_CHECK)


class TestRetailCheck(MathCheckCase):
    def test_matching_deal_shows_every_line_matching_and_unlocks(self):
        self.to_math_check()
        comparison = self.wizard.set_retail_check(retail_entry())
        self.assertTrue(comparison.all_match, mc.comparison_text(comparison))
        self.assertEqual(
            [(l.label, l.app) for l in comparison.lines],
            [("Total sales tax", D("868.98")), ("Amount financed", D("16743.83")),
             ("Monthly payment", D("290.08"))],
        )
        self.assertEqual(self.wizard.advance(), [])
        self.assertEqual(self.wizard.current_step, SetupStep.APPROVE)

    def test_setup_cannot_advance_without_a_check_deal(self):
        self.to_math_check()
        errors = self.wizard.advance()
        self.assertTrue(any("recent retail deal" in e for e in errors))
        self.assertEqual(self.wizard.current_step, SetupStep.MATH_CHECK)

    def test_one_cent_off_blocks_and_names_the_line(self):
        self.to_math_check()
        comparison = self.wizard.set_retail_check(
            retail_entry(dms_monthly_payment=D("290.09"))
        )
        self.assertFalse(comparison.all_match)
        self.assertEqual(comparison.mismatches, ["Monthly payment"])
        errors = self.wizard.advance()
        self.assertTrue(any("Monthly payment" in e for e in errors))

    def test_wrong_store_fee_shows_up_as_a_mismatch(self):
        # A store that typed its dealer fee wrong in Step 0.2 finds out
        # here: the check uses the store's own fee, not a typed one.
        self.to_math_check()
        self.wizard.dealer.dealer_fee = D("999")
        comparison = self.wizard.set_retail_check(retail_entry())
        self.assertIn("Total sales tax", comparison.mismatches)
        self.assertIn("Amount financed", comparison.mismatches)

    def test_wrong_county_rate_shows_up_as_a_mismatch(self):
        self.to_math_check()
        self.wizard.set_tax_data(county_surtax_rates={"Sample County": D("0.01")})
        comparison = self.wizard.set_retail_check(retail_entry())
        self.assertIn("Total sales tax", comparison.mismatches)

    def test_county_missing_from_the_table_is_an_error_not_a_guess(self):
        self.to_math_check()
        comparison = self.wizard.set_retail_check(retail_entry(county="Elsewhere"))
        self.assertFalse(comparison.ran)
        self.assertIn("not in your county rate table", comparison.errors[0])
        self.assertTrue(self.wizard.advance())

    def test_override_needs_a_name_and_a_real_reason(self):
        self.to_math_check()
        self.wizard.set_retail_check(retail_entry(dms_monthly_payment=D("289.42")))
        with self.assertRaises(ValueError):
            self.wizard.record_math_override(mc.KIND_RETAIL, "", "The DMS used a 30-day first payment.")
        with self.assertRaises(ValueError):
            self.wizard.record_math_override(mc.KIND_RETAIL, "Pat Manager", "ok")
        self.assertTrue(self.wizard.advance())

    def test_override_by_name_unlocks_and_is_audited(self):
        self.to_math_check()
        self.wizard.set_retail_check(retail_entry(dms_monthly_payment=D("289.42")))
        self.wizard.record_math_override(
            mc.KIND_RETAIL, "Pat Manager",
            "The DMS printout used a 30-day first payment; the deal had 41 days.",
        )
        self.assertEqual(self.wizard.advance(), [])
        events = [json.loads(line) for line in
                  (Path(self.tmp.name) / "audit.jsonl").read_text().splitlines()]
        override = [e for e in events if e["event_type"] == "setup_math_check_override"]
        self.assertEqual(len(override), 1)
        self.assertEqual(override[0]["details"]["manager_name"], "Pat Manager")
        self.assertEqual(override[0]["details"]["mismatches"], ["Monthly payment"])

    def test_override_lapses_when_the_figures_change(self):
        self.to_math_check()
        self.wizard.set_retail_check(retail_entry(dms_monthly_payment=D("289.42")))
        self.wizard.record_math_override(
            mc.KIND_RETAIL, "Pat Manager",
            "The DMS printout used a 30-day first payment; the deal had 41 days.",
        )
        # A later fee edit changes the app's figures: the old reason no
        # longer covers them.
        self.wizard.dealer.dealer_fee = D("1099")
        self.assertTrue(self.wizard.validate_step(SetupStep.MATH_CHECK))

    def test_override_refused_when_everything_matches(self):
        self.to_math_check()
        self.wizard.set_retail_check(retail_entry())
        with self.assertRaises(ValueError):
            self.wizard.record_math_override(
                mc.KIND_RETAIL, "Pat Manager", "No reason needed but typed anyway.")

    def test_finish_keeps_the_check_in_the_local_setup_file(self):
        self.to_math_check()
        self.wizard.set_retail_check(retail_entry())
        self.assertEqual(self.wizard.advance(), [])
        self.wizard.approve("Sample Manager")
        self.wizard.finish()
        saved = json.loads(self.setup_path.read_text(encoding="utf-8"))
        retail = saved["math_check"]["retail"]
        self.assertEqual(retail["result"], "matched")
        self.assertTrue(all(line["matches"] for line in retail["comparison"]["lines"]))
        self.assertEqual(retail["entry"]["dms_monthly_payment"], "290.08")
        self.assertNotIn("lease", saved["math_check"])


class TestLeaseCheck(MathCheckCase):
    def test_store_that_leases_must_check_a_lease_too(self):
        self.to_math_check(leases=True)
        self.wizard.set_retail_check(retail_entry())
        errors = self.wizard.advance()
        self.assertTrue(any("recent lease" in e for e in errors))
        comparison = self.wizard.set_lease_check(lease_entry())
        self.assertTrue(comparison.all_match, mc.comparison_text(comparison))
        self.assertEqual(self.wizard.advance(), [])

    def test_lease_tax_mismatch_names_the_line(self):
        self.to_math_check(leases=True)
        comparison = self.wizard.set_lease_check(lease_entry(dms_monthly_tax=D("36.69")))
        self.assertIn("Monthly tax", comparison.mismatches)

    def test_lessor_entered_in_setup_reaches_the_approved_preset(self):
        # Before decision 21 no setup screen entered the lessor, so a
        # lease deal could never start on a UI-built setup.
        self.to_math_check(leases=True)
        self.wizard.set_retail_check(retail_entry())
        self.wizard.set_lease_check(lease_entry())
        self.assertEqual(self.wizard.advance(), [])
        self.wizard.approve("Sample Manager")
        self.wizard.finish()
        preset = preset_from_setup_file(self.setup_path)
        self.assertEqual(validate_lease_preset(preset), [])
        self.assertEqual(preset.lease.lessor_name, "Sample Lessor (SAMPLE)")
        saved = json.loads(self.setup_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["math_check"]["lease"]["result"], "matched")


class TestTaxRatesStep(MathCheckCase):
    def drive_to_tax(self):
        self.pass_network(); self.pass_profile(); self.pass_printer()
        self.pass_trays(); self.pass_preflight(); self.pass_state_rules()
        self.assertEqual(self.wizard.current_step, SetupStep.TAX_RATES)

    def test_empty_county_table_blocks_with_a_plain_message(self):
        self.drive_to_tax()
        self.wizard.set_store_leases(False)
        errors = self.wizard.advance()
        self.assertTrue(any("DR-15DSS" in e for e in errors))

    def test_percent_typed_as_whole_number_is_caught(self):
        self.drive_to_tax()
        self.wizard.set_store_leases(False)
        self.wizard.set_tax_data(county_surtax_rates={"Sample County": D("1.5")})
        self.assertTrue(any("looks wrong" in e for e in self.wizard.advance()))

    def test_lease_question_must_be_answered(self):
        self.drive_to_tax()
        self.wizard.set_tax_data(county_surtax_rates={"Sample County": D("0.015")})
        self.assertTrue(any("leases" in e for e in self.wizard.advance()))
        self.wizard.set_store_leases(True)
        self.assertTrue(any("Lessor" in e for e in self.wizard.advance()))


class TestParsers(unittest.TestCase):
    def test_money_percent_and_whole(self):
        self.assertEqual(mc.parse_money("$1,199.00", label="x"), D("1199.00"))
        self.assertEqual(mc.parse_money("", label="x"), D("0"))
        with self.assertRaises(ValueError):
            mc.parse_money("abc", label="x")
        with self.assertRaises(ValueError):
            mc.parse_money("", label="x", required=True)
        self.assertEqual(mc.parse_percent("7.49%", label="x"), D("0.0749"))
        self.assertEqual(mc.parse_whole("", label="x", default=30), 30)
        with self.assertRaises(ValueError):
            mc.parse_whole("0", label="x")

    def test_entry_round_trip(self):
        entry = retail_entry()
        again = mc.entry_from_dict(mc.KIND_RETAIL, mc.entry_to_dict(entry))
        self.assertEqual(again, entry)


class TestTypedText(unittest.TestCase):
    def test_retail_form_text_round_trips_and_matches(self):
        specs = mc.RETAIL_FIELDS + mc.RETAIL_DMS_FIELDS
        texts = mc.entry_to_text(retail_entry(), specs)
        self.assertEqual(texts["annual_rate"], "7.49")
        self.assertEqual(mc.retail_entry_from_text(texts), retail_entry())

    def test_lease_form_text_round_trips(self):
        specs = mc.LEASE_FIELDS + mc.LEASE_DMS_FIELDS
        texts = mc.entry_to_text(lease_entry(), specs)
        self.assertEqual(texts["residual_percent"], "61")
        self.assertEqual(mc.lease_entry_from_text(texts), lease_entry())

    def test_blank_required_fields_are_all_reported(self):
        with self.assertRaises(ValueError) as ctx:
            mc.retail_entry_from_text({})
        message = str(ctx.exception)
        for label in ("county", "Selling price", "Term", "APR", "DMS monthly payment"):
            self.assertIn(label, message)

    def test_county_table(self):
        rates = mc.parse_county_table("Pinellas, 1.5\n\nHillsborough, 1.5\nSt. Lucie, 1\n")
        self.assertEqual(rates, {"Pinellas": D("0.015"), "Hillsborough": D("0.015"),
                                 "St. Lucie": D("0.01")})
        self.assertEqual(mc.parse_county_table(mc.county_table_text(rates)), rates)
        with self.assertRaises(ValueError) as ctx:
            mc.parse_county_table("Pinellas 1.5\nPinellas, x\n, 1")
        self.assertIn("Line 1", str(ctx.exception))
        self.assertIn("Line 2", str(ctx.exception))
        self.assertIn("Line 3", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


# Decision 22: a made-up typed sheet ("ZZ", not any real state's law):
# 5% state, the buyer's place picks the local rate, no local limit,
# full trade credit, dealer fee, tag fee and accessories taxed.
def typed_sheet():
    from dealerpaperwork import state_rules as sr
    return sr.StateRules(
        state="ZZ",
        taxed=frozenset({sr.CHARGE_DEALER_FEE, sr.CHARGE_TAG_FEE,
                         sr.CHARGE_ACCESSORIES}),
    )


class TestTypedRulesCheck(MathCheckCase):
    """Lane's card, 2026-11-20: two retail deals (one with a trade, one
    without), no manager override."""

    def to_typed_math_check(self):
        self.pass_network(); self.pass_profile(); self.pass_printer()
        self.pass_trays(); self.pass_preflight()
        self.assertEqual(self.pass_state_rules(typed_sheet()), [])
        self.wizard.set_tax_data(state_tax_rate=D("0.05"),
                                 county_surtax_rates={"Sample County": D("0.015")})
        self.wizard.set_store_leases(False)
        self.assertEqual(self.wizard.advance(), [])
        self.assertEqual(self.wizard.current_step, SetupStep.MATH_CHECK)

    def matched(self, kind, entry):
        """Put the app's own payment in as the DMS payment once the tax
        and amount financed are checked by hand."""
        comparison = self.wizard.set_retail_check(entry, kind)
        app = {line.label: line.app for line in comparison.lines}
        entry.dms_monthly_payment = app["Monthly payment"]
        return self.wizard.set_retail_check(entry, kind)

    def test_two_deals_by_hand(self):
        self.to_typed_math_check()
        self.assertEqual(self.wizard.math_check_kinds(),
                         (mc.KIND_RETAIL, mc.KIND_RETAIL_TRADE))
        # Trade deal: base 16250 + 85 + 1199 + 199 - 4500 = 13233.00;
        # 5% = 661.65; 1.5% (no limit) = 198.495 -> 198.50; tax 860.15.
        # Amount financed: 17733 - 4500 + 860.15 + 146.85 + 895 - 500
        # + 2100 payoff = 16735.00.
        trade = retail_entry(dms_total_tax=D("860.15"),
                             dms_amount_financed=D("16735.00"))
        # No trade: base 17733.00; 886.65 + 265.995 -> 266.00 = 1152.65.
        # 17733 + 1152.65 + 146.85 + 895 - 500 = 19427.50.
        plain = retail_entry(trade_allowance=D("0"), trade_payoff=D("0"),
                             dms_total_tax=D("1152.65"),
                             dms_amount_financed=D("19427.50"))
        self.assertTrue(self.matched(mc.KIND_RETAIL_TRADE, trade).all_match)
        self.assertIn("retail deal", " ".join(self.wizard.validate_step(SetupStep.MATH_CHECK)))
        self.assertTrue(self.matched(mc.KIND_RETAIL, plain).all_match)
        self.assertEqual(self.wizard.validate_step(SetupStep.MATH_CHECK), [])
        self.wizard.advance()
        self.wizard.approve("Sample Manager")
        self.wizard.finish()
        saved = json.loads(self.setup_path.read_text(encoding="utf-8"))
        self.assertEqual(set(saved["math_check"]), {"retail", "retail_trade"})
        self.assertEqual(saved["preset"]["state_rules"]["state"], "ZZ")
        self.assertEqual(preset_from_setup_file(self.setup_path).rules, typed_sheet())

    def test_each_section_needs_the_right_kind_of_deal(self):
        self.to_typed_math_check()
        self.matched(mc.KIND_RETAIL, retail_entry())  # has a trade
        self.matched(mc.KIND_RETAIL_TRADE,
                     retail_entry(trade_allowance=D("0"), trade_payoff=D("0")))
        errors = " ".join(self.wizard.validate_step(SetupStep.MATH_CHECK))
        self.assertIn("this deal has a trade", errors)
        self.assertIn("type a deal that has a trade-in allowance", errors)

    def test_no_override_for_typed_rules(self):
        self.to_typed_math_check()
        self.wizard.set_retail_check(retail_entry(), mc.KIND_RETAIL_TRADE)
        with self.assertRaises(ValueError) as caught:
            self.wizard.record_math_override(
                mc.KIND_RETAIL_TRADE, "Pat Manager",
                "The DMS rounds the tax a different way here.")
        self.assertIn("no override", str(caught.exception))
        errors = " ".join(self.wizard.validate_step(SetupStep.MATH_CHECK))
        self.assertIn("no override", errors)

    def test_changing_a_rule_reopens_the_check(self):
        self.to_typed_math_check()
        before = self.wizard.set_retail_check(retail_entry(), mc.KIND_RETAIL_TRADE)
        from dataclasses import replace
        self.wizard.set_state_rules(replace(typed_sheet(), trade_credit="none"))
        after = self.wizard.compare(mc.KIND_RETAIL_TRADE)
        self.assertNotEqual(before.fingerprint, after.fingerprint)

    def test_florida_store_keeps_one_deal_and_the_override(self):
        self.to_math_check()
        self.assertEqual(self.wizard.math_check_kinds(), (mc.KIND_RETAIL,))


class TestStateRulesStep(MathCheckCase):
    def drive_to_rules(self):
        self.pass_network(); self.pass_profile(); self.pass_printer()
        self.pass_trays(); self.pass_preflight()
        self.assertEqual(self.wizard.current_step, SetupStep.STATE_RULES)

    def test_a_choice_is_required(self):
        self.drive_to_rules()
        self.assertIn("Pick Florida's built-in rules", self.wizard.advance()[0])

    def test_none_of_these_fit_stops_setup(self):
        from dataclasses import replace
        from dealerpaperwork import state_rules as sr
        self.drive_to_rules()
        self.wizard.set_state_rules(replace(typed_sheet(), tax_kind=sr.UNSUPPORTED))
        self.assertEqual(self.wizard.advance(), [sr.UNSUPPORTED_MESSAGE])
        self.assertEqual(self.wizard.current_step, SetupStep.STATE_RULES)

    def test_dealer_place_must_be_in_the_rate_list(self):
        from dataclasses import replace
        from dealerpaperwork import state_rules as sr
        self.drive_to_rules()
        self.wizard.set_state_rules(replace(
            typed_sheet(), local_by=sr.LOCAL_BY_DEALER, dealer_place="Sample Town"))
        self.assertEqual(self.wizard.advance(), [])
        self.wizard.set_tax_data(state_tax_rate=D("0.05"),
                                 county_surtax_rates={"Other Town": D("0.02")})
        self.wizard.set_store_leases(False)
        self.assertIn("'Sample Town' is not in the local rate list",
                      " ".join(self.wizard.advance()))

    def test_no_tax_state_needs_no_rates(self):
        from dataclasses import replace
        from dealerpaperwork import state_rules as sr
        self.drive_to_rules()
        self.wizard.set_state_rules(replace(
            typed_sheet(), tax_kind=sr.TAX_NONE, local_by=sr.LOCAL_NONE))
        self.wizard.advance()
        self.wizard.set_tax_data(state_tax_rate=D("0"), county_surtax_rates={})
        self.wizard.set_store_leases(False)
        self.assertEqual(self.wizard.advance(), [])
