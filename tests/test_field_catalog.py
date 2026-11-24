"""The expanded field list (2026-11-18): every box a real form needs.

The new fields come from public forms -- the Florida title
application (HSMV 82040), the FTC Used Car Buyers Guide, the Truth in
Lending box of a retail installment contract (Reg Z) and the federal
consumer lease disclosure (Reg M) -- and the DMS worksheet layout.
All deals here are synthetic (Decision 17).

Expected figures were worked by hand, independently of the app:

Retail (the synthetic deal in test_payment_terms.py):
    amount financed 16,743.83; total of payments 20,885.76;
    finance charge 4,141.93 (72 months, 7.49% APR, 41 days)
    down payment = 500 cash + 0 rebate + (4,500 - 2,100) net trade
                 = 2,900.00
    total sale price = 20,885.76 + 2,900.00 = 23,785.76
      check: amount financed + finance charge + down
             = 16,743.83 + 4,141.93 + 2,900.00 = 23,785.76
    first payment = deal date 2026-10-31 + 41 days = 12/11/2026

Lease (synthetic worksheet B in test_lease_flow.py):
    selling price 30,000 - 1,500 = 28,500.00
    gross cap cost 28,500 + 1,199 + 650 + 60 = 30,409.00
    residual 30,000 x 62% = 18,600.00
    depreciation 30,409 - 18,600 = 11,809.00
    base payment 11,809/36 + (30,409 + 18,600) x 0.0015
               = 328.0278 + 73.5135 = 401.54
    total of base payments 401.54 x 36 = 14,455.44
    rent charge 14,455.44 - 11,809.00 = 2,646.44
    purchase option price 18,600 + 300 fee = 18,900.00
    due at signing 401.54 + 7.5% tax 30.12 + 400.00 upfront = 831.66
      (6% state + 1.5% Sample County surtax on the payment)

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dealerpaperwork.deal_flow import DealSession
from dealerpaperwork.doc_library import (
    CANONICAL_FIELDS,
    FIELD_CATALOG,
    FIELD_LABELS,
    SUPPLIED_BOTH,
    SUPPLIED_LEASE,
    SUPPLIED_RETAIL,
    FieldPlacement,
    field_choice_label,
    field_from_choice_label,
    resolve_field_values,
    sample_field_values,
    validate_placements,
)
from dealerpaperwork.lease_flow import LeaseDealSession
from dealerpaperwork.models import (
    Buyer,
    TradeIn,
    Vehicle,
    buyer_from_dict,
    buyer_to_dict,
    vehicle_from_dict,
    vehicle_to_dict,
)
from types import SimpleNamespace

from dealerpaperwork import deal_ui, lease_ui
from dealerpaperwork.models import parse_odometer
from test_deal_flow import DealFlowTestCase
from test_lease_flow import LeaseFlowTestCase

D = Decimal

STAGE_4_TWENTY = (
    "buyer_name", "buyer_address", "buyer_county", "co_buyer_name",
    "vehicle_year", "vehicle_make", "vehicle_model", "vehicle_vin",
    "vehicle_mileage", "trade_description", "trade_allowance",
    "trade_payoff", "selling_price", "dealer_fee", "sales_tax_total",
    "amount_financed", "monthly_payment", "deal_date", "dealer_name",
    "dealer_address",
)


class TestCatalog(unittest.TestCase):
    def test_stage_4_names_stay_first_in_order(self):
        # Approved documents already reference these names.
        self.assertEqual(CANONICAL_FIELDS[:20], STAGE_4_TWENTY)

    def test_names_unique_and_every_entry_complete(self):
        self.assertEqual(len(CANONICAL_FIELDS), len(set(CANONICAL_FIELDS)))
        for name, label, group, supplied in FIELD_CATALOG:
            self.assertRegex(name, r"^[a-z][a-z0-9_]*$")
            self.assertTrue(label and group)
            self.assertIn(supplied, (SUPPLIED_RETAIL, SUPPLIED_LEASE, SUPPLIED_BOTH))
        self.assertEqual(set(FIELD_LABELS), set(CANONICAL_FIELDS))

    def test_briefed_boxes_exist(self):
        for name in (
            "apr", "finance_charge", "total_of_payments", "total_sale_price",
            "lienholder_name", "lienholder_address", "co_buyer_address",
            "vehicle_stock_number", "lease_adjusted_cap_cost",
            "lease_residual_value", "lease_term", "vehicle_mileage",
            "lease_annual_miles", "lease_purchase_option_price",
        ):
            self.assertIn(name, CANONICAL_FIELDS)

    def test_choice_label_round_trips_every_field(self):
        for name in CANONICAL_FIELDS:
            label = field_choice_label(name)
            self.assertIn(FIELD_LABELS[name], label)
            self.assertEqual(field_from_choice_label(label), name)
        self.assertEqual(field_from_choice_label("buyer_name"), "buyer_name")

    def test_new_fields_validate_and_unknown_still_refused(self):
        self.assertEqual(validate_placements([FieldPlacement("apr", 1, 10, 10)]), [])
        errors = validate_placements([FieldPlacement("apr_box", 1, 10, 10)])
        self.assertEqual(len(errors), 1)

    def test_sample_covers_every_field(self):
        values = sample_field_values()
        self.assertEqual(set(values), set(CANONICAL_FIELDS))
        self.assertTrue(all(values[name] for name in CANONICAL_FIELDS))


class TestSerialisation(unittest.TestCase):
    def test_blank_details_add_no_keys(self):
        # Deals saved before these fields keep byte-identical inputs.
        self.assertEqual(set(buyer_to_dict(Buyer())), {
            "full_name", "co_buyer_name", "street_address", "city",
            "state", "zip_code", "residence_county",
        })
        self.assertEqual(set(vehicle_to_dict(Vehicle())), {
            "year", "make", "model", "vin", "is_new", "stock_number",
        })

    def test_details_round_trip(self):
        buyer = Buyer(full_name="A", phone="(555) 010-0000",
                      co_buyer_name="B", co_buyer_street_address="2 Elm",
                      co_buyer_city="Sample City", co_buyer_state="FL",
                      co_buyer_zip_code="00000")
        vehicle = Vehicle(2024, "Sample", "Wagon", "VIN", False, "S1",
                          body_style="4D SUV", color="Gray", odometer=41250)
        self.assertEqual(buyer_from_dict(json.loads(json.dumps(buyer_to_dict(buyer)))), buyer)
        self.assertEqual(vehicle_from_dict(json.loads(json.dumps(vehicle_to_dict(vehicle)))), vehicle)

    def test_old_dict_loads_with_blank_details(self):
        vehicle = vehicle_from_dict({"year": 2020, "make": "M", "model": "X",
                                     "vin": "V", "is_new": False, "stock_number": "S"})
        self.assertIsNone(vehicle.odometer)
        self.assertEqual((vehicle.body_style, vehicle.color), ("", ""))


class TestRetailValues(DealFlowTestCase):
    def session(self, *, days=41):
        session = DealSession(
            user=self.fi, preset=self.preset, setup_path=self.setup_path,
            deals_dir=self.deals_dir, audit_log=self.audit,
        )
        session.created_at = "2026-10-31T10:00:00-04:00"
        session.set_packaging_details(transaction_type="finance",
                                      lender_name="Sample Bank (SAMPLE)",
                                      lender_address="PO Box 1, Sample City, FL 00000")
        session.set_buyer(Buyer(
            full_name="Sample Buyer", residence_county="Sample County",
            street_address="1 Oak", city="Sample City", state="FL", zip_code="00000",
            co_buyer_name="Sample Co-Buyer", co_buyer_street_address="2 Elm",
            co_buyer_city="Other City", co_buyer_state="FL", co_buyer_zip_code="00001",
        ))
        session.set_vehicle(Vehicle(year=2019, make="Sample", model="Wagon",
                                    vin="1SAMPLEVIN0000001", is_new=False,
                                    stock_number="S000123", body_style="4D Wagon",
                                    color="Blue", odometer=83685))
        session.set_trade(TradeIn(description="Sample trade", vin="2TRADE",
                                  allowance=D("4500"), payoff=D("2100")))
        session.set_money(selling_price="16250", accessories="85",
                          government_fees="146.85", optional_products="895",
                          cash_down_payment="500")
        session.set_payment_terms(term_months=72, annual_rate=D("0.0749"),
                                  days_to_first_payment=days)
        return session

    def test_truth_in_lending_box(self):
        values = resolve_field_values(self.session())
        self.assertEqual(values["apr"], "7.49")
        self.assertEqual(values["amount_financed"], "16,743.83")
        self.assertEqual(values["finance_charge"], "4,141.93")
        self.assertEqual(values["total_of_payments"], "20,885.76")
        self.assertEqual(values["total_down_payment"], "2,900.00")
        self.assertEqual(values["total_sale_price"], "23,785.76")
        self.assertEqual(values["number_of_payments"], "72")
        self.assertEqual(values["monthly_payment"], "290.08")
        self.assertEqual(values["first_payment_date"], "12/11/2026")

    def test_no_day_count_leaves_first_payment_date_blank(self):
        values = resolve_field_values(self.session(days=None))
        self.assertEqual(values["first_payment_date"], "")
        self.assertNotEqual(values["finance_charge"], "")

    def test_cash_deal_prints_no_disclosure_box(self):
        session = self.session()
        session.set_payment_terms(term_months=None, annual_rate=None)
        values = resolve_field_values(session)
        for name in ("apr", "finance_charge", "total_of_payments",
                     "total_sale_price", "number_of_payments", "first_payment_date"):
            self.assertEqual(values[name], "", name)

    def test_negative_equity_down_payment_never_below_zero(self):
        session = self.session()
        session.set_money(cash_down_payment="0")
        session.set_trade(TradeIn(description="Upside down", allowance=D("1000"),
                                  payoff=D("5000")))
        values = resolve_field_values(session)
        self.assertEqual(values["trade_net"], "-4,000.00")
        self.assertEqual(values["total_down_payment"], "0.00")
        self.assertEqual(values["total_sale_price"], values["total_of_payments"])

    def test_parties_vehicle_and_lienholder(self):
        values = resolve_field_values(self.session())
        self.assertEqual(values["co_buyer_address"], "2 Elm, Other City, FL 00001")
        self.assertEqual(values["co_buyer_zip"], "00001")
        self.assertEqual(values["buyer_street"], "1 Oak")
        self.assertEqual(values["vehicle_mileage"], "83,685")
        self.assertEqual(values["vehicle_stock_number"], "S000123")
        self.assertEqual(values["vehicle_condition"], "USED")
        self.assertEqual(values["vehicle_year_make_model"], "2019 Sample Wagon")
        self.assertEqual(values["trade_vin"], "2TRADE")
        self.assertEqual(values["trade_net"], "2,400.00")
        self.assertEqual(values["lienholder_name"], "Sample Bank (SAMPLE)")
        self.assertEqual(values["lienholder_address"], "PO Box 1, Sample City, FL 00000")
        self.assertEqual(values["deal_date"], "10/31/2026")
        self.assertEqual(set(values), set(CANONICAL_FIELDS))
        # A retail deal never fills a lease box.
        for name, _label, _group, supplied in FIELD_CATALOG:
            if supplied == SUPPLIED_LEASE:
                self.assertEqual(values[name], "", name)

    def test_details_survive_reload_and_edit_voids_certification(self):
        session = self.session()
        session.compute_worksheet()
        loaded = DealSession.load(session.deal_id, user=self.fi, preset=self.preset,
                                  deals_dir=self.deals_dir, audit_log=self.audit)
        self.assertEqual(loaded.inputs.vehicle.odometer, 83685)
        self.assertEqual(loaded.inputs.buyer.co_buyer_city, "Other City")
        self.assertEqual(loaded.packaging_details["lender_address"],
                         "PO Box 1, Sample City, FL 00000")
        loaded.acknowledge_warnings(self.fi)
        loaded.certify(self.fi)
        loaded.set_packaging_details(transaction_type="finance",
                                     lender_name="Sample Bank (SAMPLE)",
                                     lender_address="PO Box 2, Sample City, FL 00000")
        self.assertFalse(loaded.is_certified)

    def test_blank_lender_address_adds_no_key(self):
        session = self.session()
        session.set_packaging_details(transaction_type="finance", lender_name="X")
        self.assertEqual(session.packaging_details,
                         {"transaction_type": "finance", "lender_name": "X"})


class TestLeaseValues(LeaseFlowTestCase):
    def session(self):
        session = self.walk_fixture_b_to_review(self.new_session())
        session.set_lease(annual_mileage_allowance=10000,
                          excess_mileage_rate="0.25", purchase_option_fee="300")
        return session

    def test_reg_m_disclosure_boxes(self):
        values = resolve_field_values(self.session())
        self.assertEqual(values["selling_price"], "28,500.00")
        self.assertEqual(values["lease_msrp"], "30,000.00")
        self.assertEqual(values["lease_gross_cap_cost"], "30,409.00")
        self.assertEqual(values["lease_cap_cost_reduction"], "0.00")
        self.assertEqual(values["lease_adjusted_cap_cost"], "30,409.00")
        self.assertEqual(values["lease_residual_value"], "18,600.00")
        self.assertEqual(values["lease_residual_percent"], "62.00")
        self.assertEqual(values["lease_depreciation"], "11,809.00")
        self.assertEqual(values["lease_base_monthly_payment"], "401.54")
        self.assertEqual(values["lease_total_base_payments"], "14,455.44")
        self.assertEqual(values["lease_rent_charge"], "2,646.44")
        self.assertEqual(values["lease_term"], "36")
        self.assertEqual(values["lease_money_factor"], "0.00150")
        self.assertEqual(values["lease_amount_due_at_signing"], "831.66")
        self.assertEqual(values["lease_acquisition_fee"], "650.00")
        self.assertEqual(values["dealer_fee"], "1,199.00")
        self.assertEqual(values["lease_annual_miles"], "10,000")
        self.assertEqual(values["lease_excess_mileage_rate"], "0.25")
        self.assertEqual(values["lease_purchase_option_fee"], "300.00")
        self.assertEqual(values["lease_purchase_option_price"], "18,900.00")
        # The lessor defaults to the approved preset's lessor.
        self.assertEqual(values["lienholder_name"], "Sample Lessor (SAMPLE)")
        # A lease never fills a retail-only box.
        for name, _label, _group, supplied in FIELD_CATALOG:
            if supplied == SUPPLIED_RETAIL:
                self.assertEqual(values[name], "", name)

    def test_end_terms_blank_until_entered(self):
        session = self.walk_fixture_b_to_review(self.new_session())
        values = resolve_field_values(session)
        for name in ("lease_annual_miles", "lease_excess_mileage_rate",
                     "lease_purchase_option_fee", "lease_purchase_option_price"):
            self.assertEqual(values[name], "", name)

    def test_end_terms_survive_reload(self):
        session = self.session()
        loaded = LeaseDealSession.load(session.deal_id, user=self.fi,
                                       preset=self.preset, deals_dir=self.deals_dir,
                                       audit_log=self.audit)
        self.assertEqual(loaded.lease.annual_mileage_allowance, 10000)
        self.assertEqual(loaded.lease.excess_mileage_rate, D("0.25"))
        self.assertEqual(loaded.lease.purchase_option_fee, D("300"))
        session.set_lease(purchase_option_fee=None)
        self.assertIsNone(session.lease.purchase_option_fee)

    def test_negative_end_term_refused(self):
        session = self.walk_fixture_b_to_review(self.new_session())
        with self.assertRaises(ValueError):
            session.set_lease(purchase_option_fee="-1")


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


TYPED_PARTIES = {
    "lender_name": "Sample Bank (SAMPLE)", "lender_street": "PO Box 9",
    "lender_city": "Sample City", "lender_state": "FL", "lender_zip": "00000",
    "buyer_name": "Sample Buyer", "co_buyer": "Sample Co-Buyer",
    "buyer_street": "1 Oak", "buyer_city": "Sample City", "buyer_state": "FL",
    "buyer_zip": "00000", "buyer_county": "Sample County",
    "buyer_phone": "(555) 010-0000", "co_street": "2 Elm", "co_city": "Other City",
    "co_state": "FL", "co_zip": "00001",
}
TYPED_VEHICLE = {
    "veh_year": "2024", "veh_make": "Sample", "veh_model": "Wagon",
    "veh_vin": "1SAMPLEVIN0000009", "veh_stock": "S9", "veh_body": "4D SUV",
    "veh_color": "Gray", "veh_odometer": "41,250",
}


def fake_window(cls, session, user):
    window = object.__new__(cls)
    window.session = session
    window.user = user
    window.root = SimpleNamespace(destroy=lambda: None)
    window.errors = []
    window._show_errors = lambda errors: window.errors.extend(errors)
    window._show_step = lambda: None
    window.new_vehicle_var = FakeVar(False)
    return window


class TestOdometerParse(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_odometer(" 41,250 "), 41250)
        self.assertIsNone(parse_odometer(""))
        for bad in ("forty", "-5", "12.5"):
            with self.assertRaises(ValueError):
                parse_odometer(bad)


@unittest.skipUnless(deal_ui.TK_AVAILABLE, "Tkinter import needed; no window is opened")
class TestRetailScreensCollectDetails(DealFlowTestCase):
    def test_buyer_and_vehicle_steps(self):
        session = DealSession(user=self.fi, preset=self.preset, setup_path=self.setup_path,
                              deals_dir=self.deals_dir, audit_log=self.audit)
        window = fake_window(deal_ui.DealWindow, session, self.fi)
        window.vars = {k: FakeVar(v) for k, v in
                       dict(TYPED_PARTIES, transaction_type="finance").items()}
        window._collect_buyer()
        window.vars = {k: FakeVar(v) for k, v in TYPED_VEHICLE.items()}
        window._collect_vehicle()
        values = resolve_field_values(session)
        self.assertEqual(values["lienholder_address"], "PO Box 9, Sample City, FL 00000")
        self.assertEqual((values["lienholder_street"], values["lienholder_city"],
                          values["lienholder_state"], values["lienholder_zip"]),
                         ("PO Box 9", "Sample City", "FL", "00000"))
        self.assertEqual(values["co_buyer_address"], "2 Elm, Other City, FL 00001")
        self.assertEqual(values["buyer_phone"], "(555) 010-0000")
        self.assertEqual(values["vehicle_mileage"], "41,250")
        self.assertEqual(values["vehicle_body_style"], "4D SUV")
        self.assertEqual(values["vehicle_color"], "Gray")

    def test_bad_odometer_is_shown_not_saved(self):
        session = DealSession(user=self.fi, preset=self.preset, setup_path=self.setup_path,
                              deals_dir=self.deals_dir, audit_log=self.audit)
        session.current_step = deal_ui.DealStep.VEHICLE
        window = fake_window(deal_ui.DealWindow, session, self.fi)
        window.vars = {k: FakeVar(v) for k, v in
                       dict(TYPED_VEHICLE, veh_odometer="lots").items()}
        window._on_next()
        self.assertTrue(any("Odometer" in e for e in window.errors))
        self.assertIsNone(session.inputs.vehicle.odometer)


@unittest.skipUnless(lease_ui.TK_AVAILABLE, "Tkinter import needed; no window is opened")
class TestLeaseScreensCollectDetails(LeaseFlowTestCase):
    def test_buyer_and_vehicle_steps(self):
        session = self.new_session()
        window = fake_window(lease_ui.LeaseWindow, session, self.fi)
        window.vars = {k: FakeVar(v) for k, v in TYPED_PARTIES.items()}
        window._collect_buyer()
        window.vars = {k: FakeVar(v) for k, v in TYPED_VEHICLE.items()}
        window._collect_vehicle()
        values = resolve_field_values(session)
        self.assertEqual(values["lienholder_name"], "Sample Bank (SAMPLE)")
        self.assertEqual(values["lienholder_address"], "PO Box 9, Sample City, FL 00000")
        self.assertEqual((values["lienholder_street"], values["lienholder_city"],
                          values["lienholder_state"], values["lienholder_zip"]),
                         ("PO Box 9", "Sample City", "FL", "00000"))
        self.assertEqual(values["co_buyer_city"], "Other City")
        self.assertEqual(values["vehicle_mileage"], "41,250")


if __name__ == "__main__":
    unittest.main()
