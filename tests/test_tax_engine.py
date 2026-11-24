"""Tests for the Florida retail calculation engine (spec Section 9).

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.models import Buyer, DealInputs, TradeIn, Vehicle
from dealerpaperwork.tax_engine import calculate_retail_deal

D = Decimal


def fixture_inputs(**overrides) -> DealInputs:
    """The spec Section 9 acceptance fixture inputs (sample data only).

    Sample vehicle price $35,000, sample dealer fee $1,199 (taxable),
    sample trade allowance $10,000, sample county surtax rate 1.5%.
    Expected: taxable base $26,199, state tax $1,571.94, surtax $75.00,
    total tax $1,646.94.
    """
    inputs = DealInputs(
        buyer=Buyer(full_name="Sample Buyer", residence_county="Sample County"),
        vehicle=Vehicle(year=2026, make="Sample", model="Vehicle", is_new=True),
        selling_price=D("35000"),
        dealer_fee=D("1199"),
        trade=TradeIn(description="Sample trade", allowance=D("10000")),
        county_surtax_rate=D("0.015"),
    )
    for key, value in overrides.items():
        setattr(inputs, key, value)
    return inputs


class TestAcceptanceFixture(unittest.TestCase):
    def test_section_9_fixture_exact(self):
        result = calculate_retail_deal(fixture_inputs())
        self.assertEqual(result.taxable_base, D("26199.00"))
        self.assertEqual(result.state_tax, D("1571.94"))
        self.assertEqual(result.county_surtax, D("75.00"))
        self.assertEqual(result.total_tax, D("1646.94"))


class TestTaxRules(unittest.TestCase):
    def test_rebate_does_not_reduce_taxable_base(self):
        without = calculate_retail_deal(fixture_inputs())
        with_rebate = calculate_retail_deal(
            fixture_inputs(manufacturer_rebate=D("2000"))
        )
        # Tax is identical with and without the rebate...
        self.assertEqual(with_rebate.taxable_base, without.taxable_base)
        self.assertEqual(with_rebate.total_tax, without.total_tax)
        # ...but the rebate reduces the amount due, after tax.
        self.assertEqual(
            without.amount_due - with_rebate.amount_due, D("2000.00")
        )

    def test_trade_in_reduces_taxable_base(self):
        no_trade = calculate_retail_deal(fixture_inputs(trade=None))
        self.assertEqual(no_trade.taxable_base, D("36199.00"))
        with_trade = calculate_retail_deal(fixture_inputs())
        self.assertEqual(
            no_trade.taxable_base - with_trade.taxable_base, D("10000.00")
        )

    def test_trade_payoff_does_not_reduce_taxable_base(self):
        result = calculate_retail_deal(
            fixture_inputs(
                trade=TradeIn(allowance=D("10000"), payoff=D("12000"))
            )
        )
        self.assertEqual(result.taxable_base, D("26199.00"))
        self.assertEqual(result.total_tax, D("1646.94"))

    def test_surtax_capped_at_first_5000_when_base_above_cap(self):
        result = calculate_retail_deal(fixture_inputs())
        self.assertEqual(result.surtax_base, D("5000"))
        self.assertEqual(result.county_surtax, D("75.00"))  # 1.5% of 5,000

    def test_surtax_base_below_5000_uses_reduced_base_and_warns(self):
        # Base below $5,000: surtax applies to the (reduced) base only,
        # never to more than the base. Spec Section 16 flags this case
        # unverified, so the result must carry the warning.
        result = calculate_retail_deal(
            fixture_inputs(
                selling_price=D("4000"),
                dealer_fee=D("0"),
                trade=None,
            )
        )
        self.assertEqual(result.taxable_base, D("4000.00"))
        self.assertEqual(result.surtax_base, D("4000.00"))
        self.assertEqual(result.county_surtax, D("60.00"))  # 1.5% of 4,000
        self.assertTrue(any("below $5,000" in w for w in result.warnings))

    def test_electronic_tag_fee_is_taxed(self):
        # Decision 14 (Lane, 2026-11-02): the electronic tag fee is a
        # private tag service, not a government agency fee -- it is
        # taxed, in the base with the other dealer-required charges.
        result = calculate_retail_deal(
            fixture_inputs(electronic_tag_fee=D("199"))
        )
        # Base rises by the fee: 26,199 + 199 = 26,398. State tax
        # 6% x 26,398 = 1,583.88; surtax unchanged at the cap, 75.00.
        self.assertEqual(result.taxable_base, D("26398.00"))
        self.assertEqual(result.state_tax, D("1583.88"))
        self.assertEqual(result.county_surtax, D("75.00"))
        self.assertEqual(result.total_tax, D("1658.88"))
        # Amount due rises by the fee's face value (now inside the
        # gross price) plus the tax on it: 199.00 + 11.94 = 210.94.
        plain = calculate_retail_deal(fixture_inputs())
        self.assertEqual(result.amount_due - plain.amount_due, D("210.94"))

    def test_state_tax_is_six_percent_of_base(self):
        result = calculate_retail_deal(fixture_inputs())
        self.assertEqual(result.state_tax, result.taxable_base * D("0.06"))


class TestDmsDealAcceptance(unittest.TestCase):
    def test_dms_style_used_deal_reproduces_exactly(self):
        # Decision 14 regression fixture, synthetic (decision 17): a
        # used-vehicle deal laid out like a DMS retail worksheet. The
        # engine was first proven against a real store worksheet; this
        # made-up deal replaces it for publication. Surtax rate 0.5%
        # caps at $25.00 (0.5% of the $5,000 cap). Government fees
        # $150.00; optional products $1,200.00; the $199.00 electronic
        # registration filing fee is taxed (Decision 14).
        # Hand math: base 14,000 + 100 + 1,199 + 199 - 3,500 = 11,998;
        # tax 6% x 11,998 = 719.88 + 25.00 surtax = 744.88;
        # amount due 11,998 + 744.88 + 150 + 1,200 = 14,092.88.
        result = calculate_retail_deal(
            DealInputs(
                buyer=Buyer(
                    full_name="Sample Buyer", residence_county="Sample County B"
                ),
                vehicle=Vehicle(
                    year=2019, make="Sample", model="Used Vehicle",
                    is_new=False,
                ),
                selling_price=D("14000.00"),
                accessories=D("100.00"),
                dealer_fee=D("1199.00"),
                trade=TradeIn(
                    description="2015 sample trade",
                    allowance=D("3500.00"),
                    payoff=D("0"),
                ),
                county_surtax_rate=D("0.005"),
                government_fees=D("150.00"),
                electronic_tag_fee=D("199.00"),
                optional_products=D("1200.00"),
            )
        )
        self.assertEqual(result.taxable_base, D("11998.00"))
        self.assertEqual(result.total_tax, D("744.88"))
        self.assertEqual(result.amount_due, D("14092.88"))


class TestPaymentEstimate(unittest.TestCase):
    def test_payment_estimate_when_term_and_rate_given(self):
        result = calculate_retail_deal(
            fixture_inputs(term_months=60, annual_rate=D("0"))
        )
        self.assertEqual(result.amount_financed, result.amount_due)
        self.assertEqual(
            result.monthly_payment * 60, result.total_of_payments
        )
        # At 0% APR the only "finance charge" is the cent-rounding of the
        # monthly payment times the term, and it is reported as such.
        self.assertEqual(
            result.finance_charge,
            result.total_of_payments - result.amount_financed,
        )

    def test_no_payment_estimate_without_term(self):
        result = calculate_retail_deal(fixture_inputs())
        self.assertIsNone(result.monthly_payment)
        self.assertIsNone(result.total_of_payments)


if __name__ == "__main__":
    unittest.main()
