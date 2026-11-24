"""Tests for the Stage 6 lease calculation engine (lease_engine.py).

The fixtures are two SYNTHETIC lease worksheets laid out like a DMS
lease worksheet (decision 17). The engine was first proven to the
cent against real store worksheets; those were replaced with
made-up numbers for publication. Every expected figure below was
recomputed by hand from the worksheet formulas, independently of
the engine. Fixture numbers live in tests only (locked decision 1).

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.lease_engine import calculate_lease
from dealerpaperwork.models import LeaseInputs
from dealerpaperwork.tax_engine import cents

D = Decimal

# Combined rate on both worksheets: 6% state (the engine's
# default / preset rate) + 1% county surtax.
SURTAX = D("0.01")


def fixture_a() -> LeaseInputs:
    """Synthetic worksheet A: sample vehicle, 36 months."""
    return LeaseInputs(
        msrp=D("34995"), dealer_discount=D("2000"),
        predelivery_fee=D("1199"), acquisition_fee=D("650"),
        cap_fees=D("60"),
        residual_base_msrp=D("34995"), residual_percent=D("0.61"),
        term_months=36, money_factor=D("0.00200"),
        county_surtax_rate=SURTAX, upfront_charges=D("400.00"),
    )


def fixture_b() -> LeaseInputs:
    """Synthetic worksheet B: sample vehicle, 36 months."""
    return LeaseInputs(
        msrp=D("30000"), dealer_discount=D("1500"),
        predelivery_fee=D("1199"), acquisition_fee=D("650"),
        cap_fees=D("60"),
        residual_base_msrp=D("30000"), residual_percent=D("0.62"),
        term_months=36, money_factor=D("0.00150"),
        county_surtax_rate=SURTAX, upfront_charges=D("400.00"),
    )


class TestFixtureA(unittest.TestCase):
    def setUp(self):
        self.result = calculate_lease(fixture_a())

    def test_capitalization(self):
        r = self.result
        self.assertEqual(r.selling_price, D("32995.00"))
        self.assertEqual(r.gross_cap_cost, D("34904.00"))
        self.assertEqual(r.total_cap_reduction, D("0.00"))
        self.assertEqual(r.net_cap_cost, D("34904.00"))

    def test_residual(self):
        r = self.result
        self.assertEqual(r.adjusted_msrp, D("34995.00"))
        self.assertEqual(r.residual_amount, D("21346.95"))
        self.assertEqual(r.adjusted_residual, D("21346.95"))

    def test_payment(self):
        r = self.result
        self.assertEqual(r.monthly_depreciation, D("376.58"))
        self.assertEqual(r.monthly_rent_charge, D("112.50"))
        self.assertEqual(r.base_monthly_payment, D("489.09"))
        self.assertEqual(r.monthly_payment, D("523.33"))

    def test_tax_is_seven_percent_of_the_base_payment(self):
        r = self.result
        self.assertEqual(r.combined_tax_rate, D("0.07"))
        self.assertEqual(r.monthly_tax, D("34.24"))
        self.assertEqual(r.monthly_tax, cents(r.base_monthly_payment * D("0.07")))

    def test_due_on_delivery(self):
        r = self.result
        self.assertEqual(r.advanced_payment, D("523.33"))
        self.assertEqual(r.due_on_delivery, D("923.33"))


class TestFixtureB(unittest.TestCase):
    def setUp(self):
        self.result = calculate_lease(fixture_b())

    def test_whole_worksheet(self):
        r = self.result
        self.assertEqual(r.selling_price, D("28500.00"))
        self.assertEqual(r.gross_cap_cost, D("30409.00"))
        self.assertEqual(r.net_cap_cost, D("30409.00"))
        self.assertEqual(r.residual_amount, D("18600.00"))
        self.assertEqual(r.monthly_depreciation, D("328.03"))
        self.assertEqual(r.monthly_rent_charge, D("73.51"))
        self.assertEqual(r.base_monthly_payment, D("401.54"))
        self.assertEqual(r.monthly_tax, D("28.11"))
        self.assertEqual(r.monthly_payment, D("429.65"))
        self.assertEqual(r.advanced_payment, D("429.65"))
        self.assertEqual(r.due_on_delivery, D("829.65"))


class TestTaxRuleDecision13(unittest.TestCase):
    """Decision 13: tax is on the payment only, on the FULL payment."""

    def test_capitalized_fee_changes_tax_only_through_the_payment(self):
        plain = calculate_lease(fixture_b())
        with_fee = fixture_b()
        with_fee.cap_fees = D("660")  # +600 capitalized
        taxed = calculate_lease(with_fee)
        self.assertEqual(taxed.gross_cap_cost, plain.gross_cap_cost + D("600"))
        self.assertGreater(taxed.base_monthly_payment, plain.base_monthly_payment)
        # The tax is still exactly the combined rate x the (higher)
        # base payment -- there is no separate tax on the fee itself.
        self.assertEqual(
            taxed.monthly_tax, cents(taxed.base_monthly_payment * D("0.07"))
        )
        fee_lines = dict(taxed.lines)
        self.assertEqual(fee_lines["Cap taxes"], D("0.00"))

    def test_no_retail_surtax_cap_payment_over_5000_taxed_in_full(self):
        inputs = LeaseInputs(
            msrp=D("300000"), residual_base_msrp=D("300000"),
            residual_percent=D("0.40"), term_months=36,
            money_factor=D("0.003"), county_surtax_rate=SURTAX,
        )
        result = calculate_lease(inputs)
        self.assertGreater(result.base_monthly_payment, D("5000"))
        self.assertEqual(result.base_monthly_payment, D("6260.00"))
        # Full-payment tax at 7%: 438.20. Had the retail $5,000 cap
        # applied to the surtax piece, the tax would be lower.
        self.assertEqual(result.monthly_tax, D("438.20"))
        self.assertGreater(result.monthly_tax, D("5000") * D("0.07"))

    def test_cap_taxes_line_is_carried_not_computed(self):
        # The worksheet's own cap-taxes line is an entered value that
        # joins the cap cost; the engine never invents a tax on it.
        inputs = fixture_b()
        inputs.cap_taxes = D("25")
        result = calculate_lease(inputs)
        self.assertEqual(result.gross_cap_cost, D("30434.00"))
        self.assertEqual(
            result.monthly_tax, cents(result.base_monthly_payment * D("0.07"))
        )


class TestReductionsAndResidual(unittest.TestCase):
    def test_cap_reductions_net_trade_and_cash_back(self):
        inputs = fixture_b()
        inputs.cash_down = D("1000")
        inputs.total_rebates = D("500")
        inputs.trade_allowance = D("8000")
        inputs.trade_payoff = D("6500")
        inputs.amount_applied_upfront = D("200")
        inputs.cash_back = D("100")
        result = calculate_lease(inputs)
        self.assertEqual(result.net_trade, D("1500.00"))
        # 1000 + 500 + 1500 net trade + 200 applied - 100 cash back
        self.assertEqual(result.total_cap_reduction, D("3100.00"))
        self.assertEqual(result.net_cap_cost, D("27309.00"))

    def test_negative_equity_raises_net_cap_cost(self):
        inputs = fixture_b()
        inputs.trade_allowance = D("5000")
        inputs.trade_payoff = D("7000")
        result = calculate_lease(inputs)
        self.assertEqual(result.net_trade, D("-2000.00"))
        self.assertEqual(result.net_cap_cost, D("32409.00"))

    def test_mrm_caps_the_residual_base(self):
        inputs = fixture_b()
        inputs.mrm = D("29000")
        result = calculate_lease(inputs)
        self.assertEqual(result.adjusted_msrp, D("30000.00"))
        self.assertEqual(result.residual_base, D("29000.00"))
        self.assertEqual(result.residual_amount, D("17980.00"))

    def test_mileage_adjustment_moves_adjusted_residual(self):
        inputs = fixture_b()
        inputs.mileage_adjustment = D("-500")
        result = calculate_lease(inputs)
        self.assertEqual(result.residual_amount, D("18600.00"))
        self.assertEqual(result.adjusted_residual, D("18100.00"))

    def test_package_discount_and_hard_adds(self):
        inputs = fixture_b()
        inputs.option_package_discount = D("319")
        inputs.hard_adds = D("100")
        result = calculate_lease(inputs)
        self.assertEqual(result.adjusted_msrp, D("29781.00"))


class TestDueOnDelivery(unittest.TestCase):
    def test_multiple_advance_payments_and_security_deposit(self):
        inputs = fixture_b()
        inputs.number_of_advance_payments = 2
        inputs.security_deposit = D("450")
        result = calculate_lease(inputs)
        self.assertEqual(result.advanced_payment, D("859.30"))
        self.assertEqual(
            result.due_on_delivery, D("859.30") + D("400.00") + D("450")
        )


class TestValidation(unittest.TestCase):
    def test_term_required(self):
        inputs = fixture_b()
        inputs.term_months = None
        with self.assertRaises(ValueError):
            calculate_lease(inputs)

    def test_residual_percent_range(self):
        inputs = fixture_b()
        inputs.residual_percent = D("1.5")
        with self.assertRaises(ValueError):
            calculate_lease(inputs)


if __name__ == "__main__":
    unittest.main()
