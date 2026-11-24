"""F&I product forms: a product's own contract prints only in a deal
that sold it, with that product's own price, term, coverage and
contract number; the products must add up to the worksheet's optional
products line before the deal can certify. Also the address parts for
forms with separate city/state/ZIP boxes. All values SAMPLE."""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dealerpaperwork.deal_flow import clean_products  # noqa: E402
from dealerpaperwork.doc_library import (  # noqa: E402
    CONDITION_RETAIL_NEW, CONDITION_RETAIL_USED, DocumentLibrary, FieldPlacement,
    is_product_document, product_field_values, resolve_field_values,
)
from dealerpaperwork.models import TradeIn  # noqa: E402
from dealerpaperwork.pack import KIND_LIBRARY_DOC, build_pack  # noqa: E402
from dealerpaperwork.deal_flow import CertificationError  # noqa: E402
from dealerpaperwork.products_ui import (  # noqa: E402
    collect_products, offered_products, product_forms_by_condition, product_forms_for,
)
from test_deal_flow import DealFlowTestCase, D  # noqa: E402

FIX = Path(__file__).parent / "fixtures/form_images"
MANAGER = SimpleNamespace(role="manager", display_name="Sample Manager")
RETAIL = (CONDITION_RETAIL_NEW, CONDITION_RETAIL_USED)


class TestCleanProducts(unittest.TestCase):
    def test_blank_price_means_not_sold(self):
        self.assertEqual(clean_products({"svc": {"price": "", "term": "60 mo"}}), {})

    def test_sold_product_keeps_its_figures(self):
        self.assertEqual(
            clean_products({"svc": {"price": "$1,995", "term": " 60 mo ", "coverage": "Plan",
                                    "contract_number": ""}}),
            {"svc": {"price": "1995.00", "term": "60 mo", "coverage": "Plan"}})

    def test_bad_or_zero_price_is_refused(self):
        for price in ("abc", "0", "-5", "NaN"):
            with self.subTest(price=price), self.assertRaises(ValueError):
                clean_products({"svc": {"price": price}})


class ProductCase(DealFlowTestCase):
    def library(self):
        library = DocumentLibrary(Path(self.tmp.name) / "library")
        for doc_id, fields in (
            ("svc-contract", [FieldPlacement("buyer_name", 1, 10, 10, 10),
                              FieldPlacement("product_price", 1, 10, 30, 10),
                              FieldPlacement("dealer_city", 1, 10, 50, 10)]),
            ("we-owe", [FieldPlacement("buyer_name", 1, 10, 10, 10)]),
        ):
            library.add_document(source_paths=[FIX / "page-1.jpg"], doc_id=doc_id,
                                 title=doc_id, when_it_prints=RETAIL, created_by="t",
                                 fields=fields)
            library.approve_document(doc_id, MANAGER)
        return library

    def reviewed_deal(self, products_money="0"):
        session = self.new_session()
        self.walk_to_review(
            session, trade=TradeIn(description="Sample trade", allowance=D("10000")),
            money={"selling_price": D("35000"), "optional_products": D(products_money)})
        return session


class TestProductForms(ProductCase):
    def test_a_form_with_a_product_field_is_a_product_form(self):
        library = self.library()
        self.assertTrue(is_product_document(library.get_document("svc-contract")))
        self.assertFalse(is_product_document(library.get_document("we-owe")))
        self.assertEqual([d.doc_id for d in library.product_documents(CONDITION_RETAIL_NEW)],
                         ["svc-contract"])

    def test_product_form_prints_only_when_sold(self):
        library = self.library()
        session = self.reviewed_deal()
        ids = [d.doc_id for d in build_pack(session, library=library) if d.kind == KIND_LIBRARY_DOC]
        self.assertEqual(ids, ["we-owe"])
        session.set_products({"svc-contract": {"price": "1995", "term": "60 mo / 100,000 mi"}})
        ids = [d.doc_id for d in build_pack(session, library=library) if d.kind == KIND_LIBRARY_DOC]
        self.assertEqual(sorted(ids), ["svc-contract", "we-owe"])

    def test_product_values_belong_to_their_own_form(self):
        session = self.reviewed_deal()
        session.set_products({"svc-contract": {"price": "1995", "term": "60 mo",
                                               "coverage": "Gold", "contract_number": "X-1"}})
        self.assertEqual(product_field_values(session, "svc-contract"),
                         {"product_price": "1,995.00", "product_term": "60 mo",
                          "product_coverage": "Gold", "product_contract_number": "X-1"})
        self.assertEqual(set(product_field_values(session, "we-owe").values()), {""})

    def test_printed_overlay_carries_the_product_price(self):
        from dealerpaperwork.print_pipeline import PrintSession  # noqa: F401
        library = self.library()
        session = self.reviewed_deal("1995")
        session.set_products({"svc-contract": {"price": "1995"}})
        record = library.get_document("svc-contract", refresh=True, reviewed=False)
        values = resolve_field_values(session)
        values.update(product_field_values(session, record.doc_id))
        self.assertEqual(values["product_price"], "1,995.00")
        self.assertEqual(values["dealer_city"], self.preset.dealer.city)


class TestProductsMustMatchTheWorksheet(ProductCase):
    def test_mismatch_blocks_certification(self):
        session = self.reviewed_deal("1000")
        session.set_products({"svc-contract": {"price": "1995"}})
        problems = session._certification_blockers(check_permission=False)
        self.assertTrue(any("add up to $1,995.00" in p and "$1,000.00" in p for p in problems),
                        problems)

    def test_certify_itself_refuses_a_mismatch(self):
        # The Certify button calls certify() directly, not the blocker
        # list: certify() must refuse on its own.
        session = self.reviewed_deal("1000")
        session.set_products({"svc-contract": {"price": "1995"}})
        with self.assertRaises(CertificationError) as raised:
            session.certify()
        self.assertIn("add up to $1,995.00", str(raised.exception))
        self.assertIsNone(session.certification)

    def test_certify_succeeds_when_products_match(self):
        session = self.reviewed_deal("1995")
        session.set_products({"svc-contract": {"price": "1995"}})
        self.assertIsNotNone(session.certify())

    def test_match_does_not_block(self):
        session = self.reviewed_deal("1995")
        session.set_products({"svc-contract": {"price": "1995.00"}})
        session.compute_worksheet()
        problems = session._certification_blockers(check_permission=False)
        self.assertFalse(any("add up to" in p for p in problems), problems)

    def test_lender_step_keeps_the_products(self):
        session = self.reviewed_deal("1995")
        session.set_products({"svc-contract": {"price": "1995"}})
        session.set_packaging_details(transaction_type="finance", lender_name="Sample Bank",
                                      lender_street="PO Box 9", lender_city="Sample City",
                                      lender_state="FL", lender_zip="00000")
        self.assertIn("svc-contract", session.packaging_details["products"])
        values = resolve_field_values(session)
        self.assertEqual(values["lienholder_address"], "PO Box 9, Sample City, FL 00000")
        self.assertEqual(values["lienholder_zip"], "00000")

    def test_products_survive_save_and_load(self):
        from dealerpaperwork.deal_flow import DealSession
        session = self.reviewed_deal("1995")
        session.set_products({"svc-contract": {"price": "1995", "term": "60 mo"}})
        loaded = DealSession.load(session.deal_id, user=self.fi, preset=self.preset,
                                  deals_dir=self.deals_dir, audit_log=self.audit)
        self.assertEqual(loaded.packaging_details["products"],
                         {"svc-contract": {"price": "1995.00", "term": "60 mo"}})


class TestDealScreenHelpers(ProductCase):
    def test_screen_lists_approved_product_forms_and_collects_them(self):
        library = self.library()
        forms = product_forms_for(library.library_dir, RETAIL)
        self.assertEqual(forms, [("svc-contract", "svc-contract")])
        typed = {"product_svc-contract_price": "1995", "product_svc-contract_term": "60 mo",
                 "product_svc-contract_coverage": "", "product_svc-contract_contract_number": ""}
        self.assertEqual(clean_products(collect_products(typed.get, forms)),
                         {"svc-contract": {"price": "1995.00", "term": "60 mo"}})

    def test_a_deal_offers_only_its_own_conditions_products(self):
        library = self.library()
        library.add_document(source_paths=[FIX / "page-1.jpg"], doc_id="used-only",
                             title="used-only", when_it_prints=(CONDITION_RETAIL_USED,),
                             created_by="t",
                             fields=[FieldPlacement("product_price", 1, 10, 30, 10)])
        library.approve_document("used-only", MANAGER)
        forms = product_forms_by_condition(library.library_dir, RETAIL)
        new_ids = [doc_id for doc_id, _ in offered_products(forms, CONDITION_RETAIL_NEW)]
        used_ids = [doc_id for doc_id, _ in offered_products(forms, CONDITION_RETAIL_USED)]
        self.assertNotIn("used-only", new_ids)
        self.assertIn("used-only", used_ids)
        self.assertIn("svc-contract", new_ids)

    def test_a_plain_list_is_offered_to_every_deal(self):
        forms = [("svc", "Service")]
        self.assertEqual(offered_products(forms, CONDITION_RETAIL_NEW), forms)

    def test_unreadable_library_offers_no_products(self):
        self.assertEqual(product_forms_for(Path(self.tmp.name) / "none", RETAIL), [])


if __name__ == "__main__":
    unittest.main()
