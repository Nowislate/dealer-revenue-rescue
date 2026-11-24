"""The demo form: Florida HSMV 82040, mapped and ready to approve.

A store needs one real form to prove its printer prints aligned before an
outage. The app ships one: the blank, public Florida "Application for
Certificate of Motor Vehicle Title" (HSMV 82040, Rev. 07/23, from
flhsmv.gov), exported at 300 DPI as two page images, with the deal's values
already placed on it.

``add_demo_form`` adds it to the store's document library as a DRAFT, the
same way a store adds its own form. A manager still opens it, checks the
SAMPLE print and approves it before it joins a deal pack (decision 9). The
store may move any value with click-to-place first. Nothing here is a legal
or filing claim: before the store files a real title application, it checks
the current revision of the form with its tax collector (decision 10:
current-year form revisions are re-pulled by the dealer).

Standard library only.
"""

from __future__ import annotations

from pathlib import Path

from .doc_library import (
    CONDITION_RETAIL_NEW,
    CONDITION_RETAIL_USED,
    DealerDocument,
    DocumentLibrary,
    FieldPlacement,
)

DEMO_DIR = Path(__file__).resolve().parent / "demo_forms" / "hsmv-82040"
DEMO_DOC_ID = "demo-hsmv-82040"
DEMO_TITLE = "DEMO: Florida title application (HSMV 82040 Rev. 07/23)"
DEMO_PAGES = ("page-1.jpg", "page-2.jpg")

# (field, page, x mm, y mm, font size). (x, y) is the top left of the
# value, measured from the top left of the letter page, inside each box
# below its printed label.
DEMO_PLACEMENTS = (
    # Page 1, Section 1: owner
    ("buyer_county", 1, 144.0, 51.5, 10),
    ("buyer_name", 1, 6.0, 74.0, 10),
    ("buyer_phone", 1, 93.0, 74.5, 9),
    ("buyer_street", 1, 50.0, 82.5, 10),
    ("buyer_city", 1, 132.5, 82.5, 9),
    ("buyer_state", 1, 178.5, 82.5, 9),
    ("buyer_zip", 1, 191.5, 82.5, 9),
    ("co_buyer_name", 1, 6.0, 125.5, 10),
    ("co_buyer_street", 1, 50.0, 134.0, 10),
    ("co_buyer_city", 1, 132.5, 134.0, 9),
    ("co_buyer_state", 1, 178.5, 134.0, 9),
    ("co_buyer_zip", 1, 191.5, 134.0, 9),
    # Page 1, Section 2: vehicle
    ("vehicle_vin", 1, 6.0, 156.0, 10),
    ("vehicle_make", 1, 6.0, 164.5, 10),
    ("vehicle_model", 1, 48.5, 164.5, 10),
    ("vehicle_year", 1, 80.5, 164.5, 10),
    ("vehicle_body_style", 1, 97.5, 164.5, 9),
    ("vehicle_color", 1, 113.5, 164.5, 9),
    # Page 1, Section 4: lienholder
    ("lienholder_address", 1, 28.0, 208.0, 8),
    ("lienholder_name", 1, 6.0, 216.5, 9),
    # Page 1, Sections 5 and 6: sale price, date, odometer
    ("selling_price", 1, 26.0, 230.5, 8),
    ("deal_date", 1, 167.0, 229.5, 8),
    ("vehicle_mileage", 1, 75.5, 249.5, 9),
    # Page 2, Section 7: dealer sales tax report and trade
    ("dealer_license_number", 2, 63.0, 28.0, 10),
    ("deal_date", 2, 104.0, 28.0, 10),
    ("sales_tax_total", 2, 130.0, 28.0, 10),
    ("trade_description", 2, 6.0, 36.0, 9),
    ("trade_vin", 2, 123.5, 36.0, 10),
    # Page 2, Section 8: VIN verification
    ("vehicle_vin", 2, 6.0, 67.0, 10),
    # Page 2, Section 12: applicant names
    ("buyer_name", 2, 6.0, 184.5, 10),
    ("co_buyer_name", 2, 6.0, 192.5, 10),
)


def demo_page_paths() -> list[Path]:
    return [DEMO_DIR / name for name in DEMO_PAGES]


def demo_placements() -> list[FieldPlacement]:
    return [FieldPlacement(field_name=f, page=p, x_mm=x, y_mm=y, font_size=s)
            for f, p, x, y, s in DEMO_PLACEMENTS]


def add_demo_form(library: DocumentLibrary, *, created_by: str = "") -> DealerDocument:
    """Add the demo title application to ``library`` as a DRAFT.

    Refused in plain words (DocumentLibraryError) when it is already in the
    library: the store keeps the copy it may have adjusted.
    """
    return library.add_document(
        source_paths=demo_page_paths(),
        doc_id=DEMO_DOC_ID,
        title=DEMO_TITLE,
        when_it_prints=(CONDITION_RETAIL_NEW, CONDITION_RETAIL_USED),
        created_by=created_by,
        fields=demo_placements(),
    )
