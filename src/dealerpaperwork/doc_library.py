"""Local document library: immutable JPEG revisions and explicit legacy records."""
from __future__ import annotations

import json
import re
import shutil
import hashlib
import tempfile
import uuid
import copy
from contextlib import ExitStack, contextmanager
from .file_transactions import file_transaction
from .jpeg_image import parse_jpeg, JpegImage, JpegImageError, MAX_PAGE_BYTES
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Union

DEFAULT_LIBRARY_DIR = Path("data") / "documents"
RECORDS_FILENAME = "library.json"

# The canonical field list: the ONLY values a dealer document can
# place. Values are resolved from the deal at render time; a document
# can never carry its own numbers (locked decision 9).
#
# Each entry: (field name, label shown to the dealer, group, which deals
# supply it). The first 20 names are the Stage 4 list and never change:
# approved documents already reference them. The rest (2026-11-18) come
# from the boxes on public forms -- the Florida title application
# (HSMV 82040), the FTC Used Car Buyers Guide, the Truth in Lending box
# of a retail installment contract (Reg Z) and the federal consumer
# lease disclosure (Reg M) -- and the store's DMS worksheet layout.
SUPPLIED_RETAIL = "retail"
SUPPLIED_LEASE = "lease"
SUPPLIED_BOTH = "both"

FIELD_CATALOG: tuple[tuple[str, str, str, str], ...] = (
    # --- Stage 4 list (names frozen) ---------------------------------
    ("buyer_name", "Buyer name", "Buyer", SUPPLIED_BOTH),
    ("buyer_address", "Buyer address (one line)", "Buyer", SUPPLIED_BOTH),
    ("buyer_county", "Buyer residence county", "Buyer", SUPPLIED_BOTH),
    ("co_buyer_name", "Co-buyer name", "Co-buyer", SUPPLIED_BOTH),
    ("vehicle_year", "Vehicle year", "Vehicle", SUPPLIED_BOTH),
    ("vehicle_make", "Vehicle make", "Vehicle", SUPPLIED_BOTH),
    ("vehicle_model", "Vehicle model", "Vehicle", SUPPLIED_BOTH),
    ("vehicle_vin", "Vehicle VIN", "Vehicle", SUPPLIED_BOTH),
    ("vehicle_mileage", "Odometer reading", "Vehicle", SUPPLIED_BOTH),
    ("trade_description", "Trade-in description", "Trade-in", SUPPLIED_RETAIL),
    ("trade_allowance", "Trade-in allowance", "Trade-in", SUPPLIED_BOTH),
    ("trade_payoff", "Trade-in lien payoff", "Trade-in", SUPPLIED_BOTH),
    ("selling_price", "Selling price (cash price of vehicle)", "Price", SUPPLIED_BOTH),
    ("dealer_fee", "Dealer fee (predelivery service)", "Price", SUPPLIED_BOTH),
    ("sales_tax_total", "Sales tax (lease: tax on one payment)", "Price", SUPPLIED_BOTH),
    ("amount_financed", "Amount financed", "Truth in Lending", SUPPLIED_RETAIL),
    ("monthly_payment", "Monthly payment (lease: total monthly)", "Truth in Lending", SUPPLIED_BOTH),
    ("deal_date", "Deal date", "Deal", SUPPLIED_BOTH),
    ("dealer_name", "Dealer name", "Dealer", SUPPLIED_BOTH),
    ("dealer_address", "Dealer address (one line)", "Dealer", SUPPLIED_BOTH),
    # --- Buyer and co-buyer, address in parts (title application) -----
    ("buyer_street", "Buyer street address", "Buyer", SUPPLIED_BOTH),
    ("buyer_city", "Buyer city", "Buyer", SUPPLIED_BOTH),
    ("buyer_state", "Buyer state", "Buyer", SUPPLIED_BOTH),
    ("buyer_zip", "Buyer ZIP code", "Buyer", SUPPLIED_BOTH),
    ("buyer_phone", "Buyer phone", "Buyer", SUPPLIED_BOTH),
    ("co_buyer_address", "Co-buyer address (one line)", "Co-buyer", SUPPLIED_BOTH),
    ("co_buyer_street", "Co-buyer street address", "Co-buyer", SUPPLIED_BOTH),
    ("co_buyer_city", "Co-buyer city", "Co-buyer", SUPPLIED_BOTH),
    ("co_buyer_state", "Co-buyer state", "Co-buyer", SUPPLIED_BOTH),
    ("co_buyer_zip", "Co-buyer ZIP code", "Co-buyer", SUPPLIED_BOTH),
    # --- Vehicle (title application, Buyers Guide, worksheet) ---------
    ("vehicle_stock_number", "Stock number", "Vehicle", SUPPLIED_BOTH),
    ("vehicle_body_style", "Body style", "Vehicle", SUPPLIED_BOTH),
    ("vehicle_color", "Color", "Vehicle", SUPPLIED_BOTH),
    ("vehicle_condition", "New or used (NEW / USED)", "Vehicle", SUPPLIED_BOTH),
    ("vehicle_year_make_model", "Year make model (one line)", "Vehicle", SUPPLIED_BOTH),
    # --- Trade-in -------------------------------------------------------
    ("trade_vin", "Trade-in VIN", "Trade-in", SUPPLIED_RETAIL),
    ("trade_net", "Net trade-in (allowance - payoff)", "Trade-in", SUPPLIED_BOTH),
    # --- Lienholder / lessor (title application, contract) -------------
    ("lienholder_name", "Lienholder or lessor name", "Lienholder", SUPPLIED_BOTH),
    ("lienholder_address", "Lienholder or lessor address", "Lienholder", SUPPLIED_BOTH),
    # --- Retail itemization (installment contract) ----------------------
    ("accessories", "Accessories / add-ons", "Price", SUPPLIED_RETAIL),
    ("dealer_discount", "Dealer discount", "Price", SUPPLIED_BOTH),
    ("electronic_tag_fee", "Electronic tag fee", "Price", SUPPLIED_RETAIL),
    ("government_fees", "Government fees (title, registration)", "Price", SUPPLIED_RETAIL),
    ("optional_products", "Optional products (service contract etc.)", "Price", SUPPLIED_RETAIL),
    ("manufacturer_rebate", "Manufacturer rebate", "Price", SUPPLIED_BOTH),
    ("cash_down_payment", "Cash down payment", "Price", SUPPLIED_BOTH),
    ("total_down_payment", "Total down payment (cash + rebate + net trade)", "Truth in Lending", SUPPLIED_RETAIL),
    # --- Truth in Lending box (Reg Z) -----------------------------------
    ("apr", "Annual percentage rate (number, no % sign)", "Truth in Lending", SUPPLIED_RETAIL),
    ("finance_charge", "Finance charge", "Truth in Lending", SUPPLIED_RETAIL),
    ("total_of_payments", "Total of payments", "Truth in Lending", SUPPLIED_RETAIL),
    ("total_sale_price", "Total sale price", "Truth in Lending", SUPPLIED_RETAIL),
    ("number_of_payments", "Number of payments", "Truth in Lending", SUPPLIED_RETAIL),
    ("first_payment_date", "First payment date", "Truth in Lending", SUPPLIED_RETAIL),
    # --- Lease disclosures (Reg M) and lease worksheet --------------------
    ("lease_msrp", "Lease: MSRP", "Lease", SUPPLIED_LEASE),
    ("lease_gross_cap_cost", "Lease: gross capitalized cost", "Lease", SUPPLIED_LEASE),
    ("lease_cap_cost_reduction", "Lease: capitalized cost reduction", "Lease", SUPPLIED_LEASE),
    ("lease_adjusted_cap_cost", "Lease: adjusted capitalized cost", "Lease", SUPPLIED_LEASE),
    ("lease_residual_value", "Lease: residual value", "Lease", SUPPLIED_LEASE),
    ("lease_residual_percent", "Lease: residual percent", "Lease", SUPPLIED_LEASE),
    ("lease_depreciation", "Lease: depreciation and amortized amounts", "Lease", SUPPLIED_LEASE),
    ("lease_rent_charge", "Lease: rent charge (whole term)", "Lease", SUPPLIED_LEASE),
    ("lease_total_base_payments", "Lease: total of base monthly payments", "Lease", SUPPLIED_LEASE),
    ("lease_term", "Lease: term (months / number of payments)", "Lease", SUPPLIED_LEASE),
    ("lease_base_monthly_payment", "Lease: base monthly payment", "Lease", SUPPLIED_LEASE),
    ("lease_monthly_tax", "Lease: monthly sales/use tax", "Lease", SUPPLIED_LEASE),
    ("lease_amount_due_at_signing", "Lease: amount due at signing", "Lease", SUPPLIED_LEASE),
    ("lease_security_deposit", "Lease: security deposit", "Lease", SUPPLIED_LEASE),
    ("lease_acquisition_fee", "Lease: acquisition fee", "Lease", SUPPLIED_LEASE),
    ("lease_money_factor", "Lease: money factor", "Lease", SUPPLIED_LEASE),
    ("lease_annual_miles", "Lease: miles allowed per year", "Lease", SUPPLIED_LEASE),
    ("lease_excess_mileage_rate", "Lease: excess mileage charge per mile", "Lease", SUPPLIED_LEASE),
    ("lease_purchase_option_fee", "Lease: purchase option fee", "Lease", SUPPLIED_LEASE),
    ("lease_purchase_option_price", "Lease: purchase option price (residual + fee)", "Lease", SUPPLIED_LEASE),
    # --- Dealer -----------------------------------------------------------
    ("dealer_license_number", "Dealer license number", "Dealer", SUPPLIED_BOTH),
    ("dealer_phone", "Dealer phone", "Dealer", SUPPLIED_BOTH),
    # --- Address parts for forms with separate city/state/ZIP boxes ----
    ("dealer_street", "Dealer street address", "Dealer", SUPPLIED_BOTH),
    ("dealer_city", "Dealer city", "Dealer", SUPPLIED_BOTH),
    ("dealer_state", "Dealer state", "Dealer", SUPPLIED_BOTH),
    ("dealer_zip", "Dealer ZIP code", "Dealer", SUPPLIED_BOTH),
    ("lienholder_street", "Lienholder or lessor street address", "Lienholder", SUPPLIED_BOTH),
    ("lienholder_city", "Lienholder or lessor city", "Lienholder", SUPPLIED_BOTH),
    ("lienholder_state", "Lienholder or lessor state", "Lienholder", SUPPLIED_BOTH),
    ("lienholder_zip", "Lienholder or lessor ZIP code", "Lienholder", SUPPLIED_BOTH),
    # --- One F&I product's own contract (service contract, GAP, ...) ---
    # A form that places any of these is a PRODUCT form: it prints only
    # in a deal that sold that product, with that product's own figures.
    ("product_price", "Product: this product's price", "Product", SUPPLIED_BOTH),
    ("product_term", "Product: term (e.g. 60 months / 100,000 miles)", "Product", SUPPLIED_BOTH),
    ("product_coverage", "Product: coverage or plan", "Product", SUPPLIED_BOTH),
    ("product_contract_number", "Product: contract number", "Product", SUPPLIED_BOTH),
)

PRODUCT_FIELDS: tuple[str, ...] = (
    "product_price", "product_term", "product_coverage", "product_contract_number",
)

CANONICAL_FIELDS: tuple[str, ...] = tuple(entry[0] for entry in FIELD_CATALOG)
FIELD_LABELS: dict[str, str] = {entry[0]: entry[1] for entry in FIELD_CATALOG}


def field_choice_label(name: str) -> str:
    """How the mapping screen lists a field: group, label, name."""
    for field_name, label, group, _supplied in FIELD_CATALOG:
        if field_name == name:
            return f"{group}: {label} [{field_name}]"
    return name


def field_from_choice_label(text: str) -> str:
    """Inverse of field_choice_label; a bare field name passes through."""
    text = (text or "").strip()
    if text.endswith("]") and "[" in text:
        return text[text.rindex("[") + 1:-1]
    return text


CONDITION_RETAIL_NEW = "retail_new"
CONDITION_RETAIL_USED = "retail_used"
CONDITION_LEASE = "lease"  # served by the Stage 6 lease path
PRINT_CONDITIONS = (CONDITION_RETAIL_NEW, CONDITION_RETAIL_USED, CONDITION_LEASE)

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"

# Only these roles may approve a document for the pack (spec: presets
# and their contents are approved by management, not by the floor).
APPROVER_ROLES = ("manager", "admin")

# Letter page geometry for placement validation (Stage 5: all letter).
LETTER_WIDTH_MM = 216.0
LETTER_HEIGHT_MM = 279.0

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class DocumentLibraryError(Exception):
    """A library action cannot be carried out. Message says why, in
    plain language, listing every validation problem found."""


@dataclass
class FieldPlacement:
    """One field placed on one page: (x_mm, y_mm) is the TOP-LEFT of
    the value, authored the way a person looks at the page; the PDF
    renderer flips to PDF's bottom-left origin (pdf_overlay.py)."""

    field_name: str
    page: int = 1
    x_mm: float = 0.0
    y_mm: float = 0.0
    font_size: float = 10.0

    def to_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "page": self.page,
            "x_mm": self.x_mm,
            "y_mm": self.y_mm,
            "font_size": self.font_size,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FieldPlacement":
        return cls(
            field_name=str(data.get("field_name", "")),
            page=int(data.get("page", 1)),
            x_mm=float(data.get("x_mm", 0.0)),
            y_mm=float(data.get("y_mm", 0.0)),
            font_size=float(data.get("font_size", 10.0)),
        )


def validate_placements(fields, *, page_count: Optional[int] = None) -> list[str]:
    """Plain-language problems with a placement list; empty = valid."""
    errors: list[str] = []
    for i, placement in enumerate(fields, start=1):
        where = f"Placement {i} ({placement.field_name or 'unnamed'})"
        if placement.field_name not in CANONICAL_FIELDS:
            errors.append(
                f"{where}: {placement.field_name!r} is not a field the "
                "app can fill. Pick one of: " + ", ".join(CANONICAL_FIELDS) + "."
            )
        if type(placement.page) is not int or placement.page < 1:
            errors.append(f"{where}: page must be 1 or higher.")
        if page_count is not None and placement.page > page_count:
            errors.append(f"{where}: page exceeds the imported page count ({page_count}).")
        if not (0.0 <= float(placement.x_mm) <= LETTER_WIDTH_MM):
            errors.append(
                f"{where}: x is off the page -- a letter page is "
                f"0 to {LETTER_WIDTH_MM:.0f} mm wide."
            )
        if not (0.0 <= float(placement.y_mm) <= LETTER_HEIGHT_MM):
            errors.append(
                f"{where}: y is off the page -- a letter page is "
                f"0 to {LETTER_HEIGHT_MM:.0f} mm tall."
            )
        if not (4.0 <= float(placement.font_size) <= 72.0):
            errors.append(f"{where}: font size must be between 4 and 72.")
    return errors


@dataclass
class DocumentPageImage:
    page: int
    source_filename: str
    stored_name: str
    width_px: int
    height_px: int
    components: int
    sha256: str

    def to_dict(self) -> dict:
        return dict(vars(self))


def validate_document_source(document) -> None:
    """Strict source state, also used on directly constructed render inputs."""
    if document.form_source == "legacy_values_only":
        if document.pages or document.image_revision is not None:
            raise DocumentLibraryError("Legacy values-only record must have no page images or revision.")
        return
    if document.form_source != "page_images" or not document.pages:
        raise DocumentLibraryError("Image document requires a complete page manifest.")
    try:
        uuid.UUID(document.image_revision)
    except (ValueError, TypeError, AttributeError):
        raise DocumentLibraryError("Image document requires a valid revision UUID.") from None
    for number, page in enumerate(document.pages, 1):
        if (type(page.page) is not int or page.page != number
            or type(page.width_px) is not int or type(page.height_px) is not int
            or (page.width_px, page.height_px) != (2550, 3300)
            or type(page.components) is not int or page.components not in (1, 3)
            or not isinstance(page.source_filename, str) or not page.source_filename
            or not isinstance(page.stored_name, str)
            or not isinstance(page.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", page.sha256)):
            raise DocumentLibraryError(f"{document.doc_id} page {number}: invalid image manifest metadata.")
        expected = f"{document.doc_id}/{document.image_revision}/page-{number:04d}.jpg"
        if page.stored_name != expected:
            raise DocumentLibraryError(f"{document.doc_id} page {number}: unsafe or inconsistent image path.")


@dataclass
class DealerDocument:
    """One dealer-added document in the library."""

    doc_id: str
    title: str
    source_filename: str = ""  # the dealer's original file name
    stored_name: str = ""  # the library's own copy, inside the library dir
    when_it_prints: tuple[str, ...] = (CONDITION_RETAIL_NEW, CONDITION_RETAIL_USED)
    fields: list[FieldPlacement] = field(default_factory=list)
    status: str = STATUS_DRAFT
    created_by: str = ""
    created_at: str = ""
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None

    pages: list[DocumentPageImage] = field(default_factory=list)
    form_source: str = "legacy_values_only"
    image_revision: Optional[str] = None

    @property
    def is_approved(self) -> bool:
        return self.status == STATUS_APPROVED

    def to_dict(self) -> dict:
        return {
            "pages": [p.to_dict() for p in self.pages],
            "form_source": self.form_source,
            "image_revision": self.image_revision,
            "doc_id": self.doc_id,
            "title": self.title,
            "source_filename": self.source_filename,
            "stored_name": self.stored_name,
            "when_it_prints": list(self.when_it_prints),
            "fields": [f.to_dict() for f in self.fields],
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
        }

    @classmethod
    def from_dict(cls, data: dict, *, library_format: str = "stage5-1") -> "DealerDocument":
        if library_format not in ("stage5-1", "stage7-1") or not isinstance(data, dict):
            raise DocumentLibraryError("Invalid document format.")
        document = cls(
            doc_id=str(data.get("doc_id", "")),
            title=str(data.get("title", "")),
            source_filename=str(data.get("source_filename", "")),
            stored_name=str(data.get("stored_name", "")),
            when_it_prints=tuple(data.get("when_it_prints", ())),
            fields=[
                FieldPlacement.from_dict(f) for f in data.get("fields", [])
            ],
            status=str(data.get("status", STATUS_DRAFT)),
            created_by=str(data.get("created_by", "")),
            created_at=str(data.get("created_at", "")),
            approved_by=data.get("approved_by"),
            approved_at=data.get("approved_at"),
        )
        if library_format == "stage7-1":
            document.form_source = data["form_source"]
            document.pages = [DocumentPageImage(**p) for p in data.get("pages", [])]
            document.image_revision = data.get("image_revision")
        validate_document_source(document)
        if (not _SLUG_RE.fullmatch(document.doc_id) or not document.title.strip()
            or document.status not in (STATUS_DRAFT, STATUS_APPROVED)
            or not document.when_it_prints
            or any(c not in PRINT_CONDITIONS for c in document.when_it_prints)):
            raise DocumentLibraryError("Invalid document identity, status or print conditions.")
        errors = validate_placements(document.fields, page_count=len(document.pages) if document.pages else None)
        if errors:
            raise DocumentLibraryError("; ".join(errors))
        return document



def document_fingerprint(document: DealerDocument) -> str:
    """Immutable identity for all displayed content, including placements."""
    data = DealerDocument.from_dict(document.to_dict(), library_format="stage7-1").to_dict()
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode('utf-8')).hexdigest()


class DocumentLibrary:
    """Library-owned images and an atomically published JSON index."""

    def __init__(self, library_dir: Union[str, Path] = DEFAULT_LIBRARY_DIR) -> None:
        self.library_dir = Path(library_dir)
        self.records_path = self.library_dir / RECORDS_FILENAME
        self._documents: dict[str, DealerDocument] = {}
        self._load()
        self._reviewed_fingerprints = {
            key: document_fingerprint(document) for key, document in self._documents.items()
        }

    def _load(self) -> None:
        try:
            data = json.loads(self.records_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("format_version") not in ("stage5-1", "stage7-1"):
                raise DocumentLibraryError("Unknown document library format.")
            if not isinstance(data.get("documents"), list):
                raise DocumentLibraryError("Document library has no valid record list.")
            documents = {}
            for raw in data["documents"]:
                document = DealerDocument.from_dict(raw, library_format=data["format_version"])
                if document.doc_id in documents:
                    raise DocumentLibraryError("Duplicate document id in library.")
                documents[document.doc_id] = document
        except FileNotFoundError as exc:
            if self.records_path.is_symlink():
                raise DocumentLibraryError("Unreadable library index symlink.") from exc
            self._documents = {}
            return
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise DocumentLibraryError(f"Cannot read document library: {exc}") from exc
        self._documents = documents

    @contextmanager
    def _exclusive(self):
        """One writer at a time, across windows and app copies.

        Every change reads the index, checks the caller's reviewed copy,
        and publishes the new index while holding the library's file lock,
        so a writer that saves in between can never be erased.
        """
        stack = ExitStack()
        try:
            self.library_dir.mkdir(parents=True, exist_ok=True)
            stack.enter_context(file_transaction(self.records_path))
        except OSError as exc:
            raise DocumentLibraryError(
                "Another window is saving the document library. Try again in a moment.") from exc
        with stack:
            yield

    def _save(self, documents=None) -> None:
        documents = self._documents if documents is None else documents
        self.library_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"format_version": "stage7-1", "documents": [d.to_dict() for d in documents.values()]}, indent=2, sort_keys=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.library_dir, prefix=".index-", delete=False) as out:
                name = Path(out.name)
                out.write(payload)
            name.replace(self.records_path)
        finally:
            if name is not None:
                try:
                    name.unlink(missing_ok=True)
                except OSError:
                    pass  # publication is already committed; never roll it back

    def __len__(self):
        return len(self._documents)

    def list_documents(self):
        return list(self._documents.values())

    def get_document(self, doc_id: str, *, refresh: bool = False, reviewed: bool = True) -> DealerDocument:
        if refresh:
            self._load()
        try:
            document = self._documents[doc_id]
        except KeyError:
            raise DocumentLibraryError(f"Document {doc_id!r} is not in the library.") from None
        if refresh and reviewed:
            self._reviewed_fingerprints[doc_id] = document_fingerprint(document)
        return document

    def document_path(self, document: DealerDocument) -> Path:
        if document.form_source != "legacy_values_only":
            raise DocumentLibraryError("Image documents have page_image_path(), not a source PDF.")
        return self.library_dir / document.stored_name

    def documents_for_pack(self, deal):
        """Approved documents for this deal's condition. A product form
        joins only a deal that sold its product (``deal_products``)."""
        sold = deal_products(deal)
        return [d for d in self._documents.values()
                if d.is_approved and _deal_condition(deal) in d.when_it_prints
                and (not is_product_document(d) or d.doc_id in sold)]

    def product_documents(self, condition: Optional[str] = None):
        """Approved product forms (optionally for one print condition),
        the forms a deal screen offers as products the deal can sell."""
        return [d for d in self._documents.values()
                if d.is_approved and is_product_document(d)
                and (condition is None or condition in d.when_it_prints)]

    def _confined(self, relative):
        path = self.library_dir / relative
        try:
            path.resolve().relative_to(self.library_dir.resolve())
        except (ValueError, OSError, RuntimeError) as exc:
            raise DocumentLibraryError("Image path escapes the library.") from exc
        return path

    def page_image_path(self, document: DealerDocument, page: int) -> Path:
        validate_document_source(document)
        if document.form_source != "page_images" or type(page) is not int or not 1 <= page <= len(document.pages):
            raise DocumentLibraryError(f"{document.doc_id} page {page}: no image page.")
        try:
            return self._confined(document.pages[page-1].stored_name)
        except DocumentLibraryError as exc:
            raise DocumentLibraryError(f"{document.doc_id} page {page}: {exc}") from exc

    @staticmethod
    def _read_image(path):
        path = Path(path)
        if path.suffix.lower() not in (".jpg", ".jpeg") or not path.is_file():
            raise DocumentLibraryError(f"{path.name}: pick a JPEG page (.jpg or .jpeg), not a PDF or directory.")
        if path.stat().st_size > MAX_PAGE_BYTES:
            raise DocumentLibraryError(f"{path.name}: exceeds 32 MiB; re-export at 300 DPI.")
        with path.open("rb") as stream:
            data = stream.read(MAX_PAGE_BYTES + 1)
        return parse_jpeg(data)

    def load_page_images(self, document: DealerDocument) -> dict[int, JpegImage]:
        validate_document_source(document)
        images = {}
        for entry in document.pages:
            try:
                image = self._read_image(self.page_image_path(document, entry.page))
                if ((image.width_px, image.height_px, image.components) != (entry.width_px, entry.height_px, entry.components)
                    or hashlib.sha256(image.data).hexdigest() != entry.sha256):
                    raise DocumentLibraryError("Image bytes or metadata do not match the approved manifest.")
                images[entry.page] = image
            except (OSError, JpegImageError, DocumentLibraryError) as exc:
                raise DocumentLibraryError(f"{document.doc_id} page {entry.page}: {exc}") from exc
        return images

    def _metadata_errors(self, *, doc_id, title, when_it_prints, fields, page_count):
        from .pack import RESERVED_DOCUMENT_IDS
        errors = []
        if not _SLUG_RE.fullmatch(doc_id or ""):
            errors.append("Document id must be a simple lowercase slug.")
        elif doc_id in RESERVED_DOCUMENT_IDS:
            errors.append(f'Document id {doc_id!r} is reserved for a built-in or generated pack document; choose a unique dealer-form id.')
        elif doc_id in self._documents:
            errors.append(f"A document with id {doc_id!r} is already in the library -- ids must be unique.")
        if not (title or "").strip():
            errors.append("The document title is blank.")
        if not when_it_prints:
            errors.append("Tick at least one 'when it prints' condition.")
        if any(c not in PRINT_CONDITIONS for c in when_it_prints):
            errors.append("Unknown print condition.")
        errors.extend(validate_placements(fields, page_count=page_count))
        return errors

    def _snapshots(self, source_paths):
        paths = list(source_paths)
        if not paths:
            raise DocumentLibraryError("Select at least one JPEG page.")
        snapshots = []
        for number, path in enumerate(paths, 1):
            try:
                snapshots.append((Path(path).name, self._read_image(path)))
            except (OSError, JpegImageError, DocumentLibraryError) as exc:
                raise DocumentLibraryError(f"Page {number}: {exc}") from exc
        return snapshots

    def validate_new_document(self, *, source_paths, doc_id, title, when_it_prints, fields):
        paths = list(source_paths)
        errors = self._metadata_errors(doc_id=doc_id, title=title, when_it_prints=when_it_prints, fields=fields, page_count=len(paths))
        try:
            self._snapshots(paths)
        except DocumentLibraryError as exc:
            errors.append(str(exc))
        return errors

    def _publish_images(self, document, snapshots):
        revision = str(uuid.uuid4())
        document = copy.deepcopy(document)
        document.form_source = "page_images"
        document.image_revision = revision
        document.source_filename = document.stored_name = ""
        document.status = STATUS_DRAFT
        document.approved_by = document.approved_at = None
        document.pages = []
        staging = destination = None
        committed = False
        try:
            self.library_dir.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".images-", dir=self.library_dir))
            destination = self._confined(f"{document.doc_id}/{revision}")
            for number, (filename, image) in enumerate(snapshots, 1):
                name = f"page-{number:04d}.jpg"
                (staging / name).write_bytes(image.data)
                document.pages.append(DocumentPageImage(number, filename, f"{document.doc_id}/{revision}/{name}", image.width_px, image.height_px, image.components, hashlib.sha256(image.data).hexdigest()))
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging.replace(destination)
            records = dict(self._documents)
            records[document.doc_id] = document
            self._save(records)
            committed = True
            self._documents = records
            self._reviewed_fingerprints[document.doc_id] = document_fingerprint(document)
            return document
        except (OSError, ValueError) as exc:
            raise DocumentLibraryError(f"Cannot publish page images: {exc}") from exc
        finally:
            for directory in (staging, destination if not committed else None):
                if directory is not None:
                    shutil.rmtree(directory, ignore_errors=True)

    def add_document(self, *, source_paths, doc_id: str, title: str,
                     when_it_prints=(CONDITION_RETAIL_NEW, CONDITION_RETAIL_USED), created_by: str = "", fields=()) -> DealerDocument:
        paths, fields, conditions = list(source_paths), [_as_placement(f) for f in fields], tuple(when_it_prints)

        def refuse_bad_metadata():
            self._load()  # include records another writer already published
            errors = self._metadata_errors(doc_id=doc_id, title=title, when_it_prints=conditions, fields=fields, page_count=len(paths))
            if errors:
                raise DocumentLibraryError("This document cannot be added: " + "; ".join(errors))

        refuse_bad_metadata()  # before the lock, so a refused add writes nothing at all
        with self._exclusive():
            refuse_bad_metadata()  # again under the lock: another writer may have taken the id
            snapshots = self._snapshots(paths)
            document = DealerDocument(doc_id, title.strip(), when_it_prints=conditions, fields=fields, created_by=created_by, created_at=_now_iso())
            return self._publish_images(document, snapshots)

    def replace_page_images(self, doc_id: str, source_paths, *, expected_fingerprint=None) -> DealerDocument:
        with self._exclusive():
            document = self._current_for_change(doc_id, expected_fingerprint=expected_fingerprint)
            snapshots = self._snapshots(source_paths)
            errors = validate_placements(document.fields, page_count=len(snapshots))
            if errors:
                raise DocumentLibraryError("; ".join(errors))
            return self._publish_images(document, snapshots)

    def _current_for_change(self, doc_id, *, expected_fingerprint=None):
        """Reject edits/approval against a document the caller has not reviewed.

        Callers hold ``_exclusive()``, so the refresh, this check and the
        publish that follows are one step: a simultaneous writer waits.
        """
        expected = (self._reviewed_fingerprints.get(doc_id)
                    if expected_fingerprint is None else expected_fingerprint)
        # Refresh storage only. Failed actions and unrelated reads must not
        # become evidence that the caller reviewed replacement content.
        self._load()
        current = self.get_document(doc_id)
        if not expected or expected != document_fingerprint(current):
            raise DocumentLibraryError(
                f"{doc_id}: this document changed since it was opened. "
                "Reload and review its current images and placements before editing or approving.")
        return current

    def _publish_record(self, document):
        records = dict(self._documents)
        records[document.doc_id] = document
        try:
            self._save(records)
        except OSError as exc:
            raise DocumentLibraryError(f"Cannot save document: {exc}") from exc
        self._documents = records
        self._reviewed_fingerprints[document.doc_id] = document_fingerprint(document)
        return document

    def update_placements(self, doc_id: str, fields, *, expected_fingerprint=None) -> DealerDocument:
        with self._exclusive():
            document = copy.deepcopy(self._current_for_change(doc_id, expected_fingerprint=expected_fingerprint))
            fields = [_as_placement(f) for f in fields]
            errors = validate_placements(fields, page_count=len(document.pages) if document.pages else None)
            if errors:
                raise DocumentLibraryError("These placements cannot be saved: " + "; ".join(errors))
            document.fields = fields
            document.status = STATUS_DRAFT
            document.approved_by = document.approved_at = None
            return self._publish_record(document)

    def approve_document(self, doc_id: str, approver, *, expected_fingerprint=None) -> DealerDocument:
        with self._exclusive():
            document = copy.deepcopy(self._current_for_change(doc_id, expected_fingerprint=expected_fingerprint))
            if getattr(approver, "role", None) not in APPROVER_ROLES:
                raise DocumentLibraryError("Only a manager or an admin can approve a document for the pack.")
            self.load_page_images(document)
            document.status = STATUS_APPROVED
            document.approved_by = getattr(approver, "display_name", None) or getattr(approver, "username", None) or str(approver)
            document.approved_at = _now_iso()
            return self._publish_record(document)


def is_product_document(document) -> bool:
    """A form that places any product field is one product's contract."""
    return any(getattr(f, "field_name", None) in PRODUCT_FIELDS
               for f in (getattr(document, "fields", None) or ()))


def deal_products(deal) -> dict:
    """The products a deal sold: {doc_id: {"price", "term", "coverage",
    "contract_number"}}, from its packaging details. Empty if none."""
    details = getattr(deal, "packaging_details", None) or {}
    products = details.get("products") or {}
    return products if isinstance(products, dict) else {}


def product_field_values(deal, doc_id: str) -> dict[str, str]:
    """This product's own figures for its own form; blank if not sold."""
    line = deal_products(deal).get(doc_id) or {}
    price = line.get("price")
    return {
        "product_price": "" if price in (None, "") else _money(Decimal(str(price))),
        "product_term": str(line.get("term") or ""),
        "product_coverage": str(line.get("coverage") or ""),
        "product_contract_number": str(line.get("contract_number") or ""),
    }


def _as_placement(value) -> FieldPlacement:
    if isinstance(value, FieldPlacement):
        return value
    if isinstance(value, dict):
        return FieldPlacement.from_dict(value)
    raise DocumentLibraryError(f"Not a field placement: {value!r}")


def deal_condition(deal) -> str:
    """Public name for the pack condition of a deal (see _deal_condition)."""
    return _deal_condition(deal)


def _deal_condition(deal) -> str:
    """The pack condition for a deal: lease (Stage 6), else retail
    new vs retail used. A lease deal resolves to the lease condition
    ONLY -- retail documents must never join a lease pack."""
    if getattr(deal, "deal_kind", None) == CONDITION_LEASE:
        return CONDITION_LEASE
    inputs = getattr(deal, "inputs", deal)
    vehicle = getattr(inputs, "vehicle", None)
    is_new = True if vehicle is None else bool(vehicle.is_new)
    return CONDITION_RETAIL_NEW if is_new else CONDITION_RETAIL_USED


# ---------------------------------------------------------------------------
# Field value resolution: deal -> {canonical field: display string}.
# This is the ONLY source of values for dealer documents, which is
# what "a document can never invent its own math" means in code.
# ---------------------------------------------------------------------------


def _money(value) -> str:
    if value is None:
        return ""
    return f"{Decimal(str(value)):,.2f}"


def _join_address(*parts: str) -> str:
    return ", ".join(p.strip() for p in parts if p and p.strip())


def _rate_percent(rate) -> str:
    """0.0834 -> "8.34"; keeps extra precision (0.08345 -> "8.345")."""
    if rate is None:
        return ""
    text = format((Decimal(str(rate)) * 100).normalize(), "f")
    whole, _, frac = text.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def _plain(value) -> str:
    return "" if value is None else str(value)


def _parts_address(street: str, city: str, state: str, zip_code: str) -> str:
    return _join_address(
        street or "", city or "",
        _join_address(state or "", zip_code or "").replace(", ", " "),
    )


def _deal_date(deal) -> Optional[date]:
    created_at = getattr(deal, "created_at", None)
    if created_at:
        try:
            return datetime.fromisoformat(str(created_at)).date()
        except ValueError:
            pass
    return None


def resolve_field_values(deal) -> dict[str, str]:
    """Resolve every canonical field for a deal to a display string.

    ``deal`` is a DealSession or LeaseSession (preferred: its computed
    worksheet and financing supply the money fields; the worksheet is
    computed on demand when possible), or anything carrying
    ``inputs``/``preset``. Fields the deal cannot supply resolve to ""
    -- the overlay simply leaves that spot blank. Every figure here is
    the engine's or the user's own entry; nothing is estimated.
    """
    from .models import LeaseInputs

    inputs = getattr(deal, "inputs", deal)
    is_lease = isinstance(inputs, LeaseInputs)
    buyer = getattr(inputs, "buyer", None)
    vehicle = getattr(inputs, "vehicle", None)
    trade = None if is_lease else getattr(inputs, "trade", None)
    preset = getattr(deal, "preset", None)
    dealer = getattr(preset, "dealer", None)
    details = getattr(deal, "packaging_details", None) or {}

    result = getattr(deal, "worksheet_result", None)
    financing = getattr(deal, "financing", None)
    # Same rule as before the catalog grew: a lease has no financing
    # section, so its worksheet is recomputed here every time.
    if (result is None or financing is None) and hasattr(deal, "compute_worksheet"):
        try:
            deal.compute_worksheet()
            result = getattr(deal, "worksheet_result", None)
            financing = getattr(deal, "financing", None)
        except Exception:
            pass  # values below fall back to "" where they must

    the_date = _deal_date(deal) or date.today()
    deal_date = the_date.strftime("%m/%d/%Y")

    def b(name: str) -> str:
        return (getattr(buyer, name, "") or "") if buyer is not None else ""

    def v(name: str) -> str:
        return (getattr(vehicle, name, "") or "") if vehicle is not None else ""

    odometer = getattr(vehicle, "odometer", None) if vehicle is not None else None
    year = getattr(vehicle, "year", None) if vehicle is not None else None
    lessor_default = ""
    if is_lease:
        lease_preset = getattr(preset, "lease", None)
        lessor_default = getattr(lease_preset, "lessor_name", "") or ""

    values: dict[str, str] = {name: "" for name in CANONICAL_FIELDS}
    values.update({
        "buyer_name": b("full_name"),
        "buyer_address": _parts_address(b("street_address"), b("city"), b("state"), b("zip_code")),
        "buyer_street": b("street_address"),
        "buyer_city": b("city"),
        "buyer_state": b("state"),
        "buyer_zip": b("zip_code"),
        "buyer_phone": b("phone"),
        "buyer_county": b("residence_county"),
        "co_buyer_name": b("co_buyer_name"),
        "co_buyer_address": _parts_address(
            b("co_buyer_street_address"), b("co_buyer_city"),
            b("co_buyer_state"), b("co_buyer_zip_code")),
        "co_buyer_street": b("co_buyer_street_address"),
        "co_buyer_city": b("co_buyer_city"),
        "co_buyer_state": b("co_buyer_state"),
        "co_buyer_zip": b("co_buyer_zip_code"),
        "vehicle_year": "" if year is None else str(year),
        "vehicle_make": v("make"),
        "vehicle_model": v("model"),
        "vehicle_vin": v("vin"),
        "vehicle_mileage": "" if odometer is None else f"{int(odometer):,}",
        "vehicle_stock_number": v("stock_number"),
        "vehicle_body_style": v("body_style"),
        "vehicle_color": v("color"),
        "vehicle_condition": (
            "" if vehicle is None else ("NEW" if vehicle.is_new else "USED")
        ),
        "vehicle_year_make_model": " ".join(
            part for part in ("" if year is None else str(year), v("make"), v("model")) if part
        ),
        "lienholder_name": details.get("lender_name") or lessor_default,
        "lienholder_address": details.get("lender_address") or _parts_address(
            details.get("lender_street", ""), details.get("lender_city", ""),
            details.get("lender_state", ""), details.get("lender_zip", "")),
        "lienholder_street": details.get("lender_street", ""),
        "lienholder_city": details.get("lender_city", ""),
        "lienholder_state": details.get("lender_state", ""),
        "lienholder_zip": details.get("lender_zip", ""),
        "deal_date": deal_date,
        "dealer_name": (
            "" if dealer is None
            else (dealer.legal_name or dealer.trading_name or "")
        ),
        "dealer_address": _parts_address(
            getattr(dealer, "street_address", None) or "",
            getattr(dealer, "city", None) or "",
            getattr(dealer, "state", None) or "",
            getattr(dealer, "zip_code", None) or "",
        ),
        "dealer_license_number": getattr(dealer, "licence_number", None) or "",
        "dealer_phone": getattr(dealer, "phone", None) or "",
        "dealer_street": getattr(dealer, "street_address", None) or "",
        "dealer_city": getattr(dealer, "city", None) or "",
        "dealer_state": getattr(dealer, "state", None) or "",
        "dealer_zip": getattr(dealer, "zip_code", None) or "",
    })
    if is_lease:
        values.update(_lease_values(inputs, result))
    else:
        values.update(_retail_values(inputs, trade, result, financing, the_date))
    return values


def _retail_values(inputs, trade, result, financing, the_date: date) -> dict[str, str]:
    allowance = trade.allowance if trade is not None else None
    payoff = trade.payoff if trade is not None else None
    out = {
        "trade_description": getattr(trade, "description", "") or "",
        "trade_vin": getattr(trade, "vin", "") or "",
        "trade_allowance": _money(allowance),
        "trade_payoff": _money(payoff),
        "trade_net": "" if trade is None else _money(allowance - payoff),
        "selling_price": _money(getattr(inputs, "selling_price", None)),
        "accessories": _money(getattr(inputs, "accessories", None)),
        "dealer_fee": _money(getattr(inputs, "dealer_fee", None)),
        "dealer_discount": _money(getattr(inputs, "dealer_discount", None)),
        "electronic_tag_fee": _money(getattr(inputs, "electronic_tag_fee", None)),
        "government_fees": _money(getattr(inputs, "government_fees", None)),
        "optional_products": _money(getattr(inputs, "optional_products", None)),
        "manufacturer_rebate": _money(getattr(inputs, "manufacturer_rebate", None)),
        "cash_down_payment": _money(getattr(inputs, "cash_down_payment", None)),
        "sales_tax_total": "" if result is None else _money(result.total_tax),
    }
    if financing is not None:
        out["amount_financed"] = _money(financing.amount_financed)
    elif result is not None and result.amount_financed is not None:
        out["amount_financed"] = _money(result.amount_financed)

    payment = total = charge = None
    if financing is not None and getattr(financing, "monthly_payment", None) is not None:
        payment, total, charge = (
            financing.monthly_payment, financing.total_of_payments, financing.finance_charge)
    elif result is not None and result.monthly_payment is not None:
        payment, total, charge = (
            result.monthly_payment, result.total_of_payments, result.finance_charge)
    out["monthly_payment"] = _money(payment)

    # The Truth in Lending box prints only with real payment terms.
    if payment is not None and financing is not None:
        rebate = Decimal(str(getattr(inputs, "manufacturer_rebate", 0) or 0))
        cash = Decimal(str(getattr(inputs, "cash_down_payment", 0) or 0))
        # Total down = cash + rebate + net trade, as the DMS worksheet
        # adds it; negative trade equity is financed, so the down
        # payment never goes below zero.
        down = max(Decimal("0"), cash + rebate + financing.net_trade_equity)
        days = getattr(inputs, "days_to_first_payment", None)
        out.update({
            "apr": _rate_percent(getattr(inputs, "annual_rate", None)),
            "finance_charge": _money(charge),
            "total_of_payments": _money(total),
            "total_down_payment": _money(down),
            "total_sale_price": "" if total is None else _money(total + down),
            "number_of_payments": _plain(getattr(inputs, "term_months", None)),
            # Only an entered day count dates the first payment.
            "first_payment_date": (
                "" if days is None
                else (the_date + timedelta(days=int(days))).strftime("%m/%d/%Y")
            ),
        })
    return out


def _lease_values(lease, result) -> dict[str, str]:
    out = {
        "trade_allowance": _money(lease.trade_allowance),
        "trade_payoff": _money(lease.trade_payoff),
        "trade_net": _money(lease.trade_allowance - lease.trade_payoff),
        "dealer_fee": _money(lease.predelivery_fee),
        "dealer_discount": _money(lease.dealer_discount),
        "manufacturer_rebate": _money(lease.total_rebates),
        "cash_down_payment": _money(lease.cash_down),
        "lease_msrp": _money(lease.msrp),
        "lease_acquisition_fee": _money(lease.acquisition_fee),
        "lease_security_deposit": _money(lease.security_deposit),
        "lease_money_factor": "" if not lease.term_months else _plain(lease.money_factor),
        "lease_residual_percent": (
            "" if not lease.residual_percent else _rate_percent(lease.residual_percent)
        ),
        "lease_term": _plain(lease.term_months),
        "lease_annual_miles": (
            "" if lease.annual_mileage_allowance is None
            else f"{lease.annual_mileage_allowance:,}"
        ),
        "lease_excess_mileage_rate": _money(lease.excess_mileage_rate),
        "lease_purchase_option_fee": _money(lease.purchase_option_fee),
    }
    if result is None:
        return out
    term = lease.term_months or 0
    depreciation = result.net_cap_cost - result.adjusted_residual
    total_base = result.base_monthly_payment * term
    out.update({
        "selling_price": _money(result.selling_price),
        "sales_tax_total": _money(result.monthly_tax),
        "monthly_payment": _money(result.monthly_payment),
        "lease_gross_cap_cost": _money(result.gross_cap_cost),
        "lease_cap_cost_reduction": _money(result.total_cap_reduction),
        "lease_adjusted_cap_cost": _money(result.net_cap_cost),
        "lease_residual_value": _money(result.adjusted_residual),
        # Reg M's model form: depreciation = adjusted cap cost - residual;
        # rent charge = total of base payments - depreciation, so the
        # three boxes always add up on the printed page.
        "lease_depreciation": _money(depreciation),
        "lease_total_base_payments": _money(total_base),
        "lease_rent_charge": _money(total_base - depreciation),
        "lease_base_monthly_payment": _money(result.base_monthly_payment),
        "lease_monthly_tax": _money(result.monthly_tax),
        "lease_amount_due_at_signing": _money(result.due_on_delivery),
        "lease_purchase_option_price": (
            "" if lease.purchase_option_fee is None
            else _money(result.adjusted_residual + lease.purchase_option_fee)
        ),
    })
    return out


# Clearly SAMPLE values: what the document screen's test render draws,
# so a dealer can check placements before any real deal exists. Every
# number is made up (Decision 17).
_SAMPLE_VALUES: dict[str, str] = {
    "buyer_name": "Sample Buyer",
    "buyer_address": "100 Sample Street, Sample City, FL 00000",
    "buyer_street": "100 Sample Street",
    "buyer_city": "Sample City",
    "buyer_state": "FL",
    "buyer_zip": "00000",
    "buyer_phone": "(555) 010-0000",
    "buyer_county": "Sample County",
    "co_buyer_name": "Sample Co-Buyer",
    "co_buyer_address": "200 Sample Avenue, Sample City, FL 00000",
    "co_buyer_street": "200 Sample Avenue",
    "co_buyer_city": "Sample City",
    "co_buyer_state": "FL",
    "co_buyer_zip": "00000",
    "vehicle_year": "2026",
    "vehicle_make": "Sample",
    "vehicle_model": "Vehicle",
    "vehicle_vin": "1SAMPLEVIN0000000",
    "vehicle_mileage": "15",
    "vehicle_stock_number": "S000000",
    "vehicle_body_style": "4D SUV",
    "vehicle_color": "Sample Gray",
    "vehicle_condition": "NEW",
    "vehicle_year_make_model": "2026 Sample Vehicle",
    "trade_description": "Sample trade-in",
    "trade_vin": "2SAMPLETRADE00000",
    "trade_allowance": "10,000.00",
    "trade_payoff": "0.00",
    "trade_net": "10,000.00",
    "lienholder_name": "Sample Bank (SAMPLE)",
    "lienholder_address": "PO Box 0000, Sample City, FL 00000",
    "selling_price": "35,000.00",
    "accessories": "0.00",
    "dealer_fee": "1,199.00",
    "dealer_discount": "0.00",
    "electronic_tag_fee": "0.00",
    "government_fees": "0.00",
    "optional_products": "0.00",
    "manufacturer_rebate": "0.00",
    "cash_down_payment": "0.00",
    "sales_tax_total": "1,658.88",
    "amount_financed": "28,056.88",
    "monthly_payment": "0.00",
    "apr": "0.00",
    "finance_charge": "0.00",
    "total_of_payments": "0.00",
    "total_down_payment": "10,000.00",
    "total_sale_price": "0.00",
    "number_of_payments": "60",
    "first_payment_date": "01/01/2027",
    "lease_msrp": "0.00",
    "lease_gross_cap_cost": "0.00",
    "lease_cap_cost_reduction": "0.00",
    "lease_adjusted_cap_cost": "0.00",
    "lease_residual_value": "0.00",
    "lease_residual_percent": "0.00",
    "lease_depreciation": "0.00",
    "lease_rent_charge": "0.00",
    "lease_total_base_payments": "0.00",
    "lease_term": "36",
    "lease_base_monthly_payment": "0.00",
    "lease_monthly_tax": "0.00",
    "lease_amount_due_at_signing": "0.00",
    "lease_security_deposit": "0.00",
    "lease_acquisition_fee": "0.00",
    "lease_money_factor": "0.00000",
    "lease_annual_miles": "10,000",
    "lease_excess_mileage_rate": "0.00",
    "lease_purchase_option_fee": "0.00",
    "lease_purchase_option_price": "0.00",
    "dealer_name": "Sample Motors LLC (SAMPLE)",
    "dealer_address": "100 Sample Street, Sample City, FL 00000",
    "dealer_license_number": "SAMPLE-0000",
    "dealer_phone": "(555) 010-0001",
    "dealer_street": "100 Sample Street",
    "dealer_city": "Sample City",
    "dealer_state": "FL",
    "dealer_zip": "00000",
    "lienholder_street": "PO Box 0000",
    "lienholder_city": "Sample City",
    "lienholder_state": "FL",
    "lienholder_zip": "00000",
    "product_price": "1,995.00",
    "product_term": "60 months / 100,000 miles",
    "product_coverage": "Sample Plan",
    "product_contract_number": "SAMPLE-0001",
}


def sample_field_values() -> dict[str, str]:
    """Clearly SAMPLE values for every canonical field."""
    values = dict(_SAMPLE_VALUES)
    values["deal_date"] = date.today().strftime("%m/%d/%Y")
    return values
