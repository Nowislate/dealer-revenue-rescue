"""Truth in Lending figures: amount financed, payment, total, charge.

The synthetic finance deal below is laid out like the DMS retail
worksheet that first exposed two defects (2026-11-18): the payment
ignored a first payment more than 30 days out, and the amount
financed left out the taxed dealer fee and accessories. Per Decision
17 the real worksheet is not in the repo; this deal is synthetic.

Every expected figure was computed independently of the app: tax and
amount financed by hand (shown below), and the payment by solving the
Regulation Z Appendix J equation with exact fractions and bisection
(no closed-form annuity), then rounding to cents.

    taxable base = 16250 + 85 + 1199 + 199 - 4500        = 13233.00
    state tax    = 6% x 13233                            =   793.98
    surtax       = 1.5% x 5000 (Sample County, capped)   =    75.00
    total tax                                            =   868.98
    amount financed = 16250 + 85 + 1199 + 868.98 + 199
                      + 146.85 + 895 - 500 - (4500 - 2100) = 16743.83
    72 months at 7.49% APR, first payment in 41 days:
    payment 290.08, total of payments 20885.76, finance charge 4141.93
    (a 30-day first payment would be 289.42)

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
from dealerpaperwork.deal_ui import parse_payment_terms, payment_terms_text
from dealerpaperwork.doc_library import resolve_field_values
from dealerpaperwork.models import Buyer, DealInputs, TradeIn, Vehicle
from dealerpaperwork.tax_engine import installment_terms
from test_deal_flow import DealFlowTestCase

D = Decimal


class TestInstallmentTerms(unittest.TestCase):
    def test_synthetic_41_day_first_payment(self):
        self.assertEqual(
            installment_terms(D("16743.83"), D("0.0749"), 72, 41),
            (D("290.08"), D("20885.76"), D("4141.93")),
        )

    def test_30_days_and_none_are_the_ordinary_payment(self):
        regular = (D("289.42"), D("20838.24"), D("4094.41"))
        self.assertEqual(installment_terms(D("16743.83"), D("0.0749"), 72, 30), regular)
        self.assertEqual(installment_terms(D("16743.83"), D("0.0749"), 72, None), regular)

    def test_longer_first_period_costs_more_shorter_costs_less(self):
        p = lambda days: installment_terms(D("16743.83"), D("0.0749"), 72, days)[0]
        self.assertLess(p(15), p(30))
        self.assertLess(p(30), p(45))
        self.assertLess(p(45), p(60))

    def test_zero_rate_spreads_the_principal(self):
        payment, total, _charge = installment_terms(D("7200.00"), D("0"), 72, 45)
        self.assertEqual((payment, total), (D("100.00"), D("7200.00")))

    def test_bad_terms_are_refused(self):
        with self.assertRaises(ValueError):
            installment_terms(D("1000"), D("0.05"), 0)
        with self.assertRaises(ValueError):
            installment_terms(D("1000"), D("0.05"), 12, 0)


class TestFlowFinancing(DealFlowTestCase):
    def synthetic_session(self, *, days=41):
        session = DealSession(
            user=self.fi, preset=self.preset, setup_path=self.setup_path,
            deals_dir=self.deals_dir, audit_log=self.audit,
        )
        session.set_buyer(Buyer(full_name="Sample Buyer", residence_county="Sample County"))
        session.set_vehicle(Vehicle(year=2019, make="Sample", model="Wagon",
                                    vin="1SAMPLEVIN0000001", is_new=False))
        session.set_trade(TradeIn(description="Sample trade", allowance=D("4500"), payoff=D("2100")))
        session.set_money(selling_price="16250", accessories="85",
                          government_fees="146.85", optional_products="895",
                          cash_down_payment="500")
        session.set_payment_terms(term_months=72, annual_rate=D("0.0749"),
                                  days_to_first_payment=days)
        session.compute_worksheet()
        return session

    def test_synthetic_worksheet_reproduces_to_the_cent(self):
        session = self.synthetic_session()
        result, financing = session.worksheet_result, session.financing
        self.assertEqual(result.total_tax, D("868.98"))
        self.assertEqual(financing.price_additions, D("1284.00"))  # 85 + 1199
        self.assertEqual(financing.amount_financed, D("16743.83"))
        self.assertEqual(financing.monthly_payment, D("290.08"))
        self.assertEqual(financing.total_of_payments, D("20885.76"))
        self.assertEqual(financing.finance_charge, D("4141.93"))

    def test_printed_payment_is_on_the_payoff_netted_amount(self):
        # The engine's own estimate runs on its step-12 amount due,
        # which does not net the $2,100 payoff. What prints must be
        # the financing section's figures.
        session = self.synthetic_session()
        result = session.worksheet_result
        self.assertEqual(result.amount_financed, D("16743.83"))
        self.assertEqual(result.monthly_payment, D("290.08"))
        self.assertEqual(result.finance_charge, D("4141.93"))
        self.assertEqual(resolve_field_values(session)["monthly_payment"], "290.08")

    def test_cash_deal_has_no_payment_figures(self):
        session = self.synthetic_session()
        session.set_payment_terms(term_months=None, annual_rate=None)
        session.compute_worksheet()
        self.assertIsNone(session.financing.monthly_payment)
        self.assertIsNone(session.worksheet_result.monthly_payment)
        self.assertEqual(session.financing.amount_financed, D("16743.83"))

    def test_terms_survive_save_and_reload(self):
        session = self.synthetic_session()
        loaded = DealSession.load(session.deal_id, user=self.fi, preset=self.preset,
                                  deals_dir=self.deals_dir, audit_log=self.audit)
        self.assertEqual(loaded.inputs.days_to_first_payment, 41)
        self.assertEqual(loaded.financing.monthly_payment, D("290.08"))
        self.assertEqual(loaded.financing.price_additions, D("1284.00"))
        self.assertTrue(loaded.worksheet_is_current)

    def test_changing_terms_after_certify_clears_certification(self):
        session = self.synthetic_session()
        session.acknowledge_warnings(self.fi)
        session.certify(self.fi)
        session.set_payment_terms(term_months=72, annual_rate=D("0.0749"),
                                  days_to_first_payment=41)  # unchanged
        self.assertTrue(session.is_certified)
        session.set_payment_terms(term_months=72, annual_rate=D("0.0749"),
                                  days_to_first_payment=45)
        self.assertFalse(session.is_certified)

    def test_no_payment_when_nothing_is_financed(self):
        session = self.synthetic_session()
        session.set_money(cash_down_payment="20000")
        session.compute_worksheet()
        self.assertLess(session.financing.amount_financed, D("0"))
        self.assertIsNone(session.financing.monthly_payment)
        self.assertIsNone(session.worksheet_result.monthly_payment)

    def test_cash_deal_drops_days_to_first_payment(self):
        session = self.synthetic_session()
        session.set_payment_terms(term_months=None, annual_rate=None, days_to_first_payment=45)
        self.assertIsNone(session.inputs.days_to_first_payment)

    def test_payment_terms_are_validated(self):
        session = self.synthetic_session()
        for bad in (dict(term_months=72, annual_rate=None),
                    dict(term_months=0, annual_rate=D("0.05")),
                    dict(term_months=72, annual_rate=D("1.5")),
                    dict(term_months=72, annual_rate=D("0.05"), days_to_first_payment=120)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                session.set_payment_terms(**bad)

    def test_old_saved_inputs_keep_identical_records(self):
        from dealerpaperwork.deal_flow import _inputs_to_dict
        self.assertNotIn("days_to_first_payment", _inputs_to_dict(DealInputs()))


class TestScreenParsing(unittest.TestCase):
    def test_apr_is_typed_as_a_percentage(self):
        self.assertEqual(parse_payment_terms("72", "7.49", "41"),
                         {"term_months": 72, "annual_rate": D("0.0749"),
                          "days_to_first_payment": 41})

    def test_blank_is_a_cash_deal_with_a_regular_first_period(self):
        self.assertEqual(parse_payment_terms("", "", ""),
                         {"term_months": None, "annual_rate": None,
                          "days_to_first_payment": None})

    def test_sub_one_percent_promo_rate_is_a_percentage(self):
        self.assertEqual(parse_payment_terms("60", "0.9", "")["annual_rate"], D("0.009"))

    def test_text_that_is_not_a_number_is_named(self):
        for args, label in ((("six", "7", ""), "Term"), (("72", "abc", ""), "APR"),
                            (("72", "7", "soon"), "Days")):
            with self.subTest(label=label), self.assertRaises(ValueError) as caught:
                parse_payment_terms(*args)
            self.assertIn(label, str(caught.exception))

    def test_saved_terms_round_trip_to_screen_text(self):
        inputs = DealInputs(term_months=72, annual_rate=D("0.0749"), days_to_first_payment=41)
        text = payment_terms_text(inputs)
        self.assertEqual(text, {"term": "72", "apr": "7.49", "days": "41"})
        self.assertEqual(parse_payment_terms(text["term"], text["apr"], text["days"])["annual_rate"],
                         D("0.0749"))


if __name__ == "__main__":
    unittest.main()


from dealerpaperwork import deal_ui  # noqa: E402


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@unittest.skipUnless(deal_ui.TK_AVAILABLE, "Tkinter import needed; no display/window is used")
class TestRetailWindowCallbacks(DealFlowTestCase):
    synthetic_session = TestFlowFinancing.synthetic_session

    def window_on_money_step(self, session, **typed):
        from dealerpaperwork.deal_flow import DealStep
        session.current_step = DealStep.TRADE
        window = object.__new__(deal_ui.DealWindow)
        window.session, window.user = session, self.fi
        window.errors = []
        window._show_errors = lambda errors: window.errors.extend(errors)
        window._show_step = lambda: None
        window.has_trade_var = FakeVar(True)
        t = session.inputs.trade
        window.vars = {"trade_desc": FakeVar(t.description), "trade_vin": FakeVar(t.vin),
                       "trade_allowance": FakeVar(str(t.allowance)),
                       "trade_payoff": FakeVar(str(t.payoff))}
        for _label, attr in deal_ui.MONEY_FIELDS_UI:
            window.vars[f"money_{attr}"] = FakeVar(str(getattr(session.inputs, attr)))
        text = payment_terms_text(session.inputs)
        window.vars.update(pay_term=FakeVar(text["term"]), pay_apr=FakeVar(text["apr"]),
                           pay_days=FakeVar(text["days"]))
        for name, value in typed.items():
            window.vars[name].set(value)
        return window

    def test_bad_apr_writes_nothing_and_keeps_certification(self):
        session = self.synthetic_session()
        session.acknowledge_warnings(self.fi)
        session.certify(self.fi)
        window = self.window_on_money_step(session, money_selling_price="17000", pay_apr="x")
        window._on_next()
        self.assertTrue(any("APR" in e for e in window.errors))
        self.assertEqual(session.inputs.selling_price, D("16250"))
        self.assertTrue(session.is_certified)

    def test_back_with_half_entered_terms_shows_the_error(self):
        from dealerpaperwork.deal_flow import DealStep
        session = self.synthetic_session()
        window = self.window_on_money_step(session, pay_apr="")
        window._on_back()
        self.assertTrue(any("both the term and the APR" in e for e in window.errors))
        self.assertEqual(session.current_step, DealStep.TRADE)
