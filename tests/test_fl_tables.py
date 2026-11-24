"""Decision 32: Florida's built-in county rate tables and ZIP lookup.

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import json
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dealerpaperwork import county_field, fl_tables
from dealerpaperwork import math_check as mc
from dealerpaperwork.deal_flow import CertificationError, preset_from_dict
from dealerpaperwork.models import Buyer
from dealerpaperwork.setup_wizard import SetupWizard, preset_to_dict
from dealerpaperwork.state_rules import FLORIDA

from test_deal_flow import DealFlowTestCase

D = Decimal

# Spot checks typed from the calendar 2026 DR-15DSS (R. 11/25),
# independently of the data file.
FORM_2026 = {
    "Pinellas": "1", "Hillsborough": "1.5", "Citrus": "0", "Collier": "0",
    "Hamilton": "2", "Palm Beach": "0.5", "Martin": "0.5", "Miami-Dade": "1",
    "Orange": "0.5", "Broward": "1", "St. Johns": "0.5", "DeSoto": "1.5",
}
# The DOR's 20 Nov 2026 notices: the only total-rate changes for 2027.
CHANGES_2027 = {"Broward": "1.25", "Gadsden": "2", "Gilchrist": "2", "Gulf": "1.5"}


class RateTableTests(unittest.TestCase):
    def test_2026_table_matches_the_form(self):
        edition = fl_tables.edition_for(2026)
        self.assertFalse(edition.provisional)
        self.assertEqual(len(edition.rates_percent), 67)
        for county, pct in FORM_2026.items():
            self.assertEqual(edition.rates_percent[county], D(pct), county)

    def test_2027_differs_from_2026_only_where_the_notices_say(self):
        old = fl_tables.edition_for(2026).rates_percent
        new = fl_tables.edition_for(2027)
        self.assertTrue(new.provisional)
        changed = {c: str(r) for c, r in new.rates_percent.items() if r != old[c]}
        self.assertEqual(changed, CHANGES_2027)

    def test_table_text_reads_back_through_the_setup_parser(self):
        for edition in fl_tables.editions():
            self.assertEqual(mc.parse_county_table(fl_tables.table_text(edition)),
                             edition.rates)

    def test_every_zip_county_has_a_rate_in_every_year(self):
        raw = json.loads(fl_tables.ZIP_FILE.read_text(encoding="utf-8"))
        names = {c for counties in raw["zips"].values() for c in counties}
        for edition in fl_tables.editions():
            self.assertEqual(names, set(edition.rates_percent), edition.year)


class ZipLookupTests(unittest.TestCase):
    def test_one_county_zip_fills_a_blank_county(self):
        self.assertEqual(fl_tables.counties_for_zip("33765"), ("Pinellas",))
        self.assertEqual(fl_tables.fill_county("33765-1234", ""), "Pinellas")
        self.assertEqual(fl_tables.county_hint("33765", "Pinellas"), "")

    def test_typed_county_is_never_overwritten(self):
        self.assertEqual(fl_tables.fill_county("33765", "Pasco"), "Pasco")
        self.assertIn("is in Pinellas County, not Pasco",
                      fl_tables.county_hint("33765", "Pasco"))

    def test_zip_across_a_county_line_never_picks(self):
        counties = fl_tables.counties_for_zip("32024")
        self.assertEqual(set(counties), {"Columbia", "Suwannee"})
        self.assertEqual(fl_tables.fill_county("32024", ""), "")
        self.assertIn("crosses", fl_tables.county_hint("32024", ""))
        self.assertEqual(fl_tables.county_hint("32024", "Suwannee"), "")

    def test_unknown_or_bad_zip(self):
        self.assertEqual(fl_tables.counties_for_zip("10001"), ())
        self.assertEqual(fl_tables.counties_for_zip("3376"), ())
        self.assertEqual(fl_tables.fill_county("10001", ""), "")
        self.assertIn("not in the Florida ZIP list",
                      fl_tables.county_hint("10001", ""))
        self.assertEqual(fl_tables.county_hint("", ""), "")


class RateYearTests(unittest.TestCase):
    def test_typed_table_has_no_year_check(self):
        self.assertEqual(fl_tables.rates_year_problem(None, date(2031, 5, 1)), "")

    def test_same_year_passes_other_years_stop(self):
        self.assertEqual(fl_tables.rates_year_problem(2026, date(2026, 12, 31)), "")
        self.assertIn("change on 1 January",
                      fl_tables.rates_year_problem(2026, date(2027, 1, 1)))
        self.assertIn("start on 1 January 2027",
                      fl_tables.rates_year_problem(2027, date(2026, 12, 31)))


class SetupYearTests(unittest.TestCase):
    def wizard(self):
        w = SetupWizard.__new__(SetupWizard)
        w.county_surtax_rates, w.county_rates_year = {}, None
        w.state_tax_rate = D("0.06")
        return w

    def test_unchanged_built_in_table_keeps_its_year(self):
        w = self.wizard()
        w.set_tax_data(county_surtax_rates=fl_tables.edition_for(2026).rates,
                       county_rates_year=2026)
        self.assertEqual(w.county_rates_year, 2026)

    def test_any_edit_makes_it_a_typed_table(self):
        w = self.wizard()
        rates = dict(fl_tables.edition_for(2026).rates)
        rates["Pinellas"] = D("0.015")
        w.set_tax_data(county_surtax_rates=rates, county_rates_year=2026)
        self.assertIsNone(w.county_rates_year)

    def test_preset_saves_year_only_when_set(self):
        w = self.wizard()
        w.dealer = None
        from dealerpaperwork.presets import Preset
        plain = preset_to_dict(Preset())
        self.assertNotIn("county_rates_year", plain)
        dated = preset_to_dict(Preset(county_rates_year=2026))
        self.assertEqual(dated["county_rates_year"], 2026)
        self.assertEqual(preset_from_dict(dated).county_rates_year, 2026)
        self.assertIsNone(preset_from_dict(plain).county_rates_year)


class CertifyYearTests(DealFlowTestCase):
    def set_rates_year(self, year):
        raw = json.loads(self.setup_path.read_text(encoding="utf-8"))
        raw["preset"]["county_rates_year"] = year
        self.setup_path.write_text(json.dumps(raw), encoding="utf-8")

    def test_last_years_rates_block_certification(self):
        self.set_rates_year(date.today().year - 1)
        session = self.new_session()
        self.walk_fixture_to_review(session)
        with self.assertRaises(CertificationError) as caught:
            session.certify()
        self.assertIn("county rates are for", str(caught.exception))

    def test_this_years_rates_certify(self):
        self.set_rates_year(date.today().year)
        session = self.new_session()
        self.walk_fixture_to_review(session)
        session.certify()
        self.assertEqual(session.state, "ready_to_print")

    def test_zip_filled_county_taxes_at_its_form_rate(self):
        # Pinellas from ZIP 33765, 1% from the 2026 table: the surtax is
        # 1% of the first $5,000 = $50.00 (the sample county is 1.5%).
        raw = json.loads(self.setup_path.read_text(encoding="utf-8"))
        raw["preset"]["county_surtax_rates"] = {
            c: str(r) for c, r in fl_tables.edition_for(2026).rates.items()}
        self.setup_path.write_text(json.dumps(raw), encoding="utf-8")
        session = self.new_session()
        county = fl_tables.fill_county("33765", "")
        session.set_buyer(Buyer(full_name="Sample Buyer", zip_code="33765",
                                residence_county=county))
        session.set_money(selling_price=D("35000"))
        result = session.compute_worksheet()
        self.assertEqual(session.inputs.county_surtax_rate, D("0.01"))
        self.assertEqual(result.county_surtax, D("50.00"))


class _Var:
    def __init__(self, value=""):
        self.value, self.traces = value, {}

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        for callback in list(self.traces.values()):
            callback()

    def trace_add(self, _mode, callback):
        key = str(len(self.traces))
        self.traces[key] = callback
        return key

    def trace_remove(self, _mode, key):
        self.traces.pop(key, None)


class _Widget:
    alive = True

    def __init__(self, *_a, **kw):
        self.config = dict(kw)

    def pack(self, **_kw):
        pass

    def configure(self, **kw):
        self.config.update(kw)

    def winfo_exists(self):
        return _Widget.alive


class _Tk:
    Frame = Label = _Widget
    TclError = RuntimeError


class _Ttk:
    Combobox = _Widget


class CountyFieldTests(unittest.TestCase):
    def setUp(self):
        _Widget.alive = True

    def test_florida_store_gets_the_lookup(self):
        class P:
            rules = FLORIDA
        self.assertTrue(county_field.uses_florida_lookup(P()))
        self.assertFalse(county_field.uses_florida_lookup(None))

    def test_typing_a_zip_fills_the_county(self):
        zip_var, county_var = _Var(), _Var()
        county_field.build(_Tk, _Ttk, None, label="County", label_width=20,
                           zip_var=zip_var, county_var=county_var, enabled=True)
        zip_var.set("33765")
        self.assertEqual(county_var.get(), "Pinellas")

    def test_left_step_stops_listening(self):
        zip_var, county_var = _Var(), _Var()
        county_field.build(_Tk, _Ttk, None, label="County", label_width=20,
                           zip_var=zip_var, county_var=county_var, enabled=True)
        _Widget.alive = False
        zip_var.set("33765")
        self.assertEqual(county_var.get(), "")
        self.assertEqual(zip_var.traces, {})

    def test_disabled_does_nothing(self):
        zip_var, county_var = _Var(), _Var()
        county_field.build(_Tk, _Ttk, None, label="County", label_width=20,
                           zip_var=zip_var, county_var=county_var, enabled=False)
        zip_var.set("33765")
        self.assertEqual(county_var.get(), "")


if __name__ == "__main__":
    unittest.main()
