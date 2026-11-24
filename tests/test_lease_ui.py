"""Tests for the lease entry screen (lease_ui).

The parsing tests run without Tk: they drive the real
LeaseDealSession with the text a user would type, and the synthetic
lease worksheet B must come out at exactly the worksheet's numbers.
The callback tests need the Tkinter module (no display or window):
they build the window object without drawing it and substitute the
entry variables, as tests/test_setup_ui_callback.py does.

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dealerpaperwork import lease_ui
from dealerpaperwork.deal_flow import list_deals
from dealerpaperwork.lease_flow import LeaseStep
from dealerpaperwork.lease_ui import (
    LEASE_ENTRY_FIELDS,
    lease_entry_text,
    parse_lease_entries,
    parse_vehicle_year,
)
from dealerpaperwork.models import Buyer, Vehicle
from test_lease_flow import LeaseFlowTestCase

D = Decimal

# What a user types for synthetic worksheet B (the preset seeds the
# fees; the user enters the rest). Residual is typed as a percentage.
FIXTURE_B_TYPED = {
    "msrp": "$30,000",
    "dealer_discount": "1,500",
    "residual_base_msrp": "30000",
    "residual_percent": "62",
    "term_months": "36",
    "money_factor": "0.00150",
}


def screen_text(session, **overrides):
    text = lease_entry_text(session.lease)
    text.update(overrides)
    return text


class LeaseUiTestCase(LeaseFlowTestCase):
    def walk_to_worksheet(self, session):
        session.set_buyer(Buyer(full_name="Sample Buyer", residence_county="Sample County"))
        self.assertEqual(session.advance(), [])
        session.set_vehicle(Vehicle(year=2026, make="Sample", model="Vehicle",
                                    vin="1SAMPLEVIN0000000", is_new=True))
        self.assertEqual(session.advance(), [])
        self.assertEqual(session.current_step, LeaseStep.WORKSHEET)
        return session


class TestParsing(LeaseUiTestCase):
    def test_typed_fixture_b_reproduces_the_worksheet(self):
        session = self.walk_to_worksheet(self.new_session())
        values, errors = parse_lease_entries(screen_text(session, **FIXTURE_B_TYPED))
        self.assertEqual(errors, [])
        self.assertEqual(values["residual_percent"], D("0.62"))
        session.set_lease(**values)
        self.assertEqual(session.advance(), [])
        result = session.worksheet_result
        self.assertEqual(result.gross_cap_cost, D("30409.00"))
        self.assertEqual(result.residual_amount, D("18600.00"))
        self.assertEqual(result.base_monthly_payment, D("401.54"))
        self.assertEqual(session.current_step, LeaseStep.REVIEW)

    def test_preset_seeded_fees_survive_the_round_trip(self):
        session = self.new_session()
        values, errors = parse_lease_entries(lease_entry_text(session.lease), require=False)
        self.assertEqual(errors, [])
        self.assertEqual(values["acquisition_fee"], D("650"))
        self.assertEqual(values["cap_fees"], D("60"))
        self.assertEqual(values["upfront_charges"], D("400.00"))
        self.assertEqual(values["predelivery_fee"], D("1199.00"))
        self.assertIsNone(values["mrm"])
        self.assertEqual(values["number_of_advance_payments"], 1)

    def test_saved_residual_shows_as_a_percentage(self):
        session = self.new_session()
        session.set_lease(residual_percent="0.6250")
        self.assertEqual(lease_entry_text(session.lease)["residual_percent"], "62.5")

    def test_text_that_is_not_a_number_is_named_never_zero(self):
        values, errors = parse_lease_entries({"msrp": "30k", "term_months": "36",
                                              "residual_percent": "62", "money_factor": "0.0015"})
        self.assertNotIn("msrp", values)
        self.assertEqual(len(errors), 1)
        self.assertIn("MSRP", errors[0])
        self.assertIn("30k", errors[0])

    def test_every_bad_field_is_reported_at_once(self):
        _values, errors = parse_lease_entries({
            "msrp": "abc", "term_months": "three", "residual_percent": "150",
            "money_factor": "-0.001", "cash_down": "-5",
        })
        joined = "\n".join(errors)
        for label in ("MSRP", "Term", "Residual", "Money factor", "Cash down"):
            self.assertIn(label, joined)
        self.assertEqual(len(errors), 5)

    def test_mileage_adjustment_may_be_negative(self):
        values, errors = parse_lease_entries({"mileage_adjustment": "-150.00"}, require=False)
        self.assertEqual(errors, [])
        self.assertEqual(values["mileage_adjustment"], D("-150.00"))

    def test_blank_program_figures_are_errors_never_zero(self):
        values, errors = parse_lease_entries({"msrp": "30000"})
        joined = "\n".join(errors)
        for label in ("Term", "Residual", "Money factor"):
            self.assertIn(label + " (", joined) if label != "Money factor" else self.assertIn(label, joined)
        self.assertEqual(len(errors), 3)
        for attr in ("term_months", "residual_percent", "money_factor"):
            self.assertNotIn(attr, values)

    def test_residual_typed_as_a_fraction_is_rejected(self):
        _values, errors = parse_lease_entries({"residual_percent": "0.62"}, require=False)
        self.assertEqual(len(errors), 1)
        self.assertIn("not 0.62", errors[0])

    def test_clearing_a_saved_term_is_an_error_not_the_old_value(self):
        session = self.walk_fixture_b_to_review(self.new_session())
        session.current_step = LeaseStep.WORKSHEET
        _values, errors = parse_lease_entries(screen_text(session, term_months=""))
        self.assertEqual(len(errors), 1)
        self.assertIn("Term", errors[0])

    def test_fresh_lease_shows_program_figures_blank(self):
        text = lease_entry_text(self.new_session().lease)
        self.assertEqual((text["term_months"], text["residual_percent"], text["money_factor"]), ("", "", ""))

    def test_nan_and_infinity_are_rejected(self):
        _values, errors = parse_lease_entries({"msrp": "NaN", "cash_down": "Infinity"}, require=False)
        self.assertEqual(len(errors), 2)

    def test_every_lease_input_is_on_the_screen(self):
        from dealerpaperwork.lease_flow import LEASE_END_TERM_FIELDS, LEASE_MONEY_FIELDS
        on_screen = {attr for _label, attr, _kind in LEASE_ENTRY_FIELDS}
        expected = set(LEASE_MONEY_FIELDS) | set(LEASE_END_TERM_FIELDS) | {
            "mrm", "residual_percent", "term_months", "money_factor",
            "number_of_advance_payments",
        }
        self.assertEqual(on_screen, expected)

    def test_end_of_lease_terms_parse_blank_as_not_entered(self):
        values, errors = parse_lease_entries({}, require=False)
        self.assertEqual(errors, [])
        for name in ("annual_mileage_allowance", "excess_mileage_rate", "purchase_option_fee"):
            self.assertIsNone(values[name])
        values, errors = parse_lease_entries({
            "annual_mileage_allowance": "12,000", "excess_mileage_rate": "$0.20",
            "purchase_option_fee": "350"}, require=False)
        self.assertEqual(errors, [])
        self.assertEqual(values["annual_mileage_allowance"], 12000)
        self.assertEqual(values["excess_mileage_rate"], Decimal("0.20"))
        _values, errors = parse_lease_entries({"annual_mileage_allowance": "lots"}, require=False)
        self.assertEqual(len(errors), 1)

    def test_vehicle_year_must_be_a_year(self):
        self.assertEqual(parse_vehicle_year(" 2026 "), 2026)
        self.assertIsNone(parse_vehicle_year(""))
        with self.assertRaises(ValueError):
            parse_vehicle_year("twenty")

    def test_deal_list_names_the_deal_kind(self):
        self.new_session()
        kinds = {deal["deal_kind"] for deal in list_deals(self.deals_dir)}
        self.assertEqual(kinds, {"lease"})


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@unittest.skipUnless(lease_ui.TK_AVAILABLE, "Tkinter import needed; no display/window is used")
class TestWindowCallbacks(LeaseUiTestCase):
    def window_for(self, session, user=None):
        window = object.__new__(lease_ui.LeaseWindow)
        window.session = session
        window.user = user or self.fi
        window.root = SimpleNamespace(destroy=lambda: None)
        window.vars = {}
        window.errors = []
        window._show_errors = lambda errors: window.errors.extend(errors)
        window._show_step = lambda: None
        return window

    def fill_worksheet(self, window, **typed):
        for attr, text in screen_text(window.session, **typed).items():
            window.vars[f"lease_{attr}"] = FakeVar(text)

    def test_next_on_worksheet_computes_and_moves_to_review(self):
        session = self.walk_to_worksheet(self.new_session())
        window = self.window_for(session)
        self.fill_worksheet(window, **FIXTURE_B_TYPED)
        window._on_next()
        self.assertEqual(window.errors, [])
        self.assertEqual(session.current_step, LeaseStep.REVIEW)
        self.assertEqual(session.worksheet_result.base_monthly_payment, D("401.54"))

    def test_bad_typing_blocks_next_and_writes_nothing(self):
        session = self.walk_to_worksheet(self.new_session())
        before = json.loads(session.file_path.read_text(encoding="utf-8"))["inputs"]
        window = self.window_for(session)
        self.fill_worksheet(window, **dict(FIXTURE_B_TYPED, msrp="thirty grand"))
        window._on_next()
        self.assertTrue(any("MSRP" in e for e in window.errors))
        self.assertEqual(session.current_step, LeaseStep.WORKSHEET)
        after = json.loads(session.file_path.read_text(encoding="utf-8"))["inputs"]
        self.assertEqual(before, after)

    def test_back_from_an_unfinished_worksheet_is_allowed(self):
        session = self.walk_to_worksheet(self.new_session())
        window = self.window_for(session)
        self.fill_worksheet(window, msrp="30000")
        window._on_back()
        self.assertEqual(window.errors, [])
        self.assertEqual(session.current_step, LeaseStep.VEHICLE)
        self.assertEqual(session.lease.msrp, D("30000"))

    def test_reopen_certified_lease_then_change_voids_it(self):
        session = self.walk_fixture_b_to_review(self.new_session())
        session.certify(self.fi)
        window = self.window_for(session)
        window._on_reopen()
        self.assertEqual(session.current_step, LeaseStep.WORKSHEET)
        self.assertTrue(session.is_certified)
        self.fill_worksheet(window, money_factor="0.00140")
        window._on_next()
        self.assertFalse(session.is_certified)
        self.assertEqual(session.current_step, LeaseStep.REVIEW)

    def test_review_shows_the_lessor_typed_for_the_package(self):
        session = self.walk_fixture_b_to_review(self.new_session())
        session.set_packaging_details(lender_name="Other Lessor (SAMPLE)")
        window = self.window_for(session)
        self.assertEqual(window._package_lessor(), "Other Lessor (SAMPLE)")

    def test_back_with_bad_typing_stays_put(self):
        session = self.walk_to_worksheet(self.new_session())
        window = self.window_for(session)
        self.fill_worksheet(window, money_factor="oops")
        window._on_back()
        self.assertEqual(session.current_step, LeaseStep.WORKSHEET)
        self.assertTrue(any("Money factor" in e for e in window.errors))

    def test_unchanged_worksheet_does_not_void_certification(self):
        session = self.walk_fixture_b_to_review(self.new_session())
        session.certify(self.fi)
        window = self.window_for(session)
        window._on_reopen()
        self.fill_worksheet(window)
        window._collect_worksheet()
        self.assertTrue(session.is_certified)

    def test_changed_worksheet_voids_certification(self):
        session = self.walk_fixture_b_to_review(self.new_session())
        session.certify(self.fi)
        session.current_step = LeaseStep.WORKSHEET
        window = self.window_for(session)
        self.fill_worksheet(window, cash_down="500")
        window._collect_worksheet()
        self.assertFalse(session.is_certified)

    def test_unchanged_vehicle_step_writes_no_edit(self):
        session = self.walk_to_worksheet(self.new_session())
        session.back()
        window = self.window_for(session)
        v = session.lease.vehicle
        window.vars = {"veh_year": FakeVar(str(v.year)), "veh_make": FakeVar(v.make),
                       "veh_model": FakeVar(v.model), "veh_vin": FakeVar(v.vin),
                       "veh_stock": FakeVar(v.stock_number)}
        window.new_vehicle_var = FakeVar(v.is_new)
        edits_before = self.audit_edit_count()
        window._collect_vehicle()
        self.assertEqual(self.audit_edit_count(), edits_before)

    def audit_edit_count(self):
        path = Path(self.tmp.name) / "audit.jsonl"
        return path.read_text(encoding="utf-8").count('"deal_edited"')

    def test_buyer_step_records_lessor_for_packaging(self):
        session = self.new_session()
        window = self.window_for(session)
        typed = {"lender_name": "Sample Lessor (SAMPLE)", "buyer_name": "Sample Buyer",
                 "co_buyer": "", "buyer_street": "", "buyer_city": "", "buyer_state": "",
                 "buyer_zip": "", "buyer_county": "Sample County"}
        window.vars = {k: FakeVar(v) for k, v in typed.items()}
        window._on_next()
        self.assertEqual(window.errors, [])
        self.assertEqual(session.current_step, LeaseStep.VEHICLE)
        self.assertEqual(session.packaging_details["lender_name"], "Sample Lessor (SAMPLE)")
        self.assertEqual(session.lease.buyer.state, "FL")

    def test_sales_user_reaches_review_but_cannot_certify(self):
        session = self.walk_fixture_b_to_review(self.new_session(self.sales))
        window = self.window_for(session, user=self.sales)
        window._on_certify()
        self.assertFalse(session.is_certified)
        self.assertTrue(any("certify" in e.lower() for e in window.errors))


@unittest.skipUnless(lease_ui.TK_AVAILABLE, "Tkinter import needed; no display/window is used")
class TestHomeRouting(LeaseUiTestCase):
    def test_open_deal_routes_a_saved_lease_to_the_lease_screen(self):
        from dealerpaperwork import app_ui
        lease = self.new_session()
        app = object.__new__(app_ui.Application)
        app.deals_dir = self.deals_dir
        app.flow = SimpleNamespace(require_access=lambda: self.fi)
        with patch.object(app_ui.Application, "open_lease") as open_lease, \
                patch.object(app_ui.Application, "open_retail") as open_retail:
            app.open_deal(lease.deal_id)
        open_lease.assert_called_once_with(lease.deal_id)
        open_retail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
