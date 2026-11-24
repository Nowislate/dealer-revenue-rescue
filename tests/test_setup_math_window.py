"""The real Tk setup window drives Steps 0.6 to 0.8 (decisions 21, 22).

Needs Tkinter and a display: it runs on the Windows CI runner and is
skipped on a headless box. The rules themselves are tested headless in
test_math_check.py; this proves the window wires them up.
"""

import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dealerpaperwork import math_check as mc
from dealerpaperwork import setup_ui
from dealerpaperwork.printers import FakePrinterProvider
from dealerpaperwork.setup_wizard import SetupStep, SetupWizard
from test_math_check import lease_entry, retail_entry
from test_setup_wizard import sample_profile

D = Decimal


@unittest.skipUnless(setup_ui.TK_AVAILABLE, "Tkinter needed")
class SetupMathWindowTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # no display
            self.skipTest(f"no display: {exc}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wizard = SetupWizard(
            printer_provider=FakePrinterProvider(["P"]),
            setup_path=Path(self.tmp.name) / "setup.json",
        )
        self.wizard.set_profile(sample_profile())
        from dealerpaperwork.state_rules import FLORIDA
        self.wizard.set_state_rules(FLORIDA)
        self.wizard.current_step = SetupStep.TAX_RATES
        self.window = setup_ui.SetupWizardWindow(self.root, self.wizard)

    def fill(self, kind, entry, specs):
        for key, value in mc.entry_to_text(entry, specs).items():
            self.window.vars[f"mc_{kind}_{key}"].set(value)

    def test_rates_then_math_check_reach_approval(self):
        w = self.window
        w._on_next()
        self.assertEqual(self.wizard.current_step, SetupStep.TAX_RATES)
        self.assertIn("DR-15DSS", w.error_label.cget("text"))
        w.county_text.insert("1.0", "Sample County, 1.5\nLease County, 1")
        w.leases_var.set("yes")
        w.vars["lessor_name"].set("Sample Lessor (SAMPLE)")
        w.vars["title_holder"].set("Sample Lessor (SAMPLE)")
        w._on_next()
        self.assertEqual(self.wizard.current_step, SetupStep.MATH_CHECK)
        self.assertEqual(self.wizard.county_surtax_rates["Sample County"], D("0.015"))

        self.fill("retail", retail_entry(dms_monthly_payment=D("290.09")),
                  mc.RETAIL_FIELDS + mc.RETAIL_DMS_FIELDS)
        self.fill("lease", lease_entry(), mc.LEASE_FIELDS + mc.LEASE_DMS_FIELDS)
        w._run_math("retail")
        self.assertIn("DIFFERENT", w.result_labels["retail"].cget("text"))
        w._on_next()
        self.assertEqual(self.wizard.current_step, SetupStep.MATH_CHECK)
        self.assertIn("Monthly payment", w.error_label.cget("text"))

        w.vars["mc_retail_override_name"].set("Pat Manager")
        w.vars["mc_retail_override_reason"].set(
            "The DMS printout used a 30-day first payment; the deal had 41.")
        w._record_override("retail")
        self.assertIn("Recorded by Pat Manager", w.result_labels["retail"].cget("text"))
        w._on_next()
        self.assertEqual(self.wizard.current_step, SetupStep.APPROVE)


    def test_typed_rules_then_two_deal_check(self):
        """Decision 22: a store in another state types its rules; the
        math check then asks for two retail deals and offers no override."""
        from dealerpaperwork import state_rules as sr
        from test_math_check import typed_sheet
        self.wizard.rules = None
        self.wizard.current_step = SetupStep.STATE_RULES
        w = self.window
        w._show_step()
        w._on_next()
        self.assertIn("Pick Florida's built-in rules", w.error_label.cget("text"))
        w.vars["rules_mode"].set("typed")
        w.vars["rules_state"].set("zz")
        w.vars["rules_tax_kind"].set(sr.TAX_ON_PRICE)
        w.vars["rules_local_by"].set(sr.LOCAL_BY_BUYER)
        w.vars["rules_trade"].set(sr.TRADE_FULL)
        for charge, var in w.taxed_vars.items():
            var.set(charge in typed_sheet().taxed)
        w._on_next()
        self.assertEqual(self.wizard.current_step, SetupStep.TAX_RATES)
        self.assertEqual(self.wizard.rules, typed_sheet())
        self.assertIn("local tax rates", w.title_label.cget("text"))
        w.county_text.insert("1.0", "Sample County, 1.5")
        w.vars["state_tax_pct"].set("5")
        w.leases_var.set("no")
        w._on_next()
        self.assertEqual(self.wizard.current_step, SetupStep.MATH_CHECK)
        self.assertIn("retail_trade", w.result_labels)
        self.assertNotIn("mc_retail_override_name", w.vars)
        # The hand-figured deals of test_math_check (tax and amount
        # financed by hand; the payment typed as the app shows it).
        plain = retail_entry(trade_allowance=D("0"), trade_payoff=D("0"),
                             dms_total_tax=D("1152.65"),
                             dms_amount_financed=D("19427.50"))
        trade = retail_entry(dms_total_tax=D("860.15"),
                             dms_amount_financed=D("16735.00"))
        for kind, entry in (("retail", plain), ("retail_trade", trade)):
            app = {l.label: l.app for l in self.wizard.set_retail_check(entry, kind).lines}
            entry.dms_monthly_payment = app["Monthly payment"]
            self.fill(kind, entry, mc.RETAIL_FIELDS + mc.RETAIL_DMS_FIELDS)
            w._run_math(kind)
            self.assertIn("MATCH", w.result_labels[kind].cget("text"))
        w._on_next()
        self.assertEqual(self.wizard.current_step, SetupStep.APPROVE)


if __name__ == "__main__":
    unittest.main()
