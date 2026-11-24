"""The standard retail document pack (Stage 4; letter-only since Stage 5).

A *pack* is the ordered set of documents one deal prints. The pack
definition here is data, not logic: an ordered list of entries, each
with a document id, a title, a document class, and a kind.

Document classes are the paper classes the Stage 2 tray mapping
resolves. Since Stage 5 (locked decision 12) there is only one in
the standard pack: ``standard_letter`` -- after the migration to
digital contracting, EVERY document, the LAW retail installment
contract included, is 8.5x11 letter. ``law_contract_long`` survives
below as a retired constant only: older print configurations may
still carry copy-set defaults under that class name, and nothing in
the standard pack uses it any more. The generic tray/calibration
machinery is unchanged -- multi-tray printers still exist; they just
all hold letter paper now.

Kinds:

* ``print`` -- rendered and sent to a printer by the pipeline;
* ``secure_stock`` -- NEVER printed. Secure forms (Florida secure
  power of attorney, secure reassignments) exist only as controlled
  secure-paper originals; printing a substitute is exactly the failure
  the spec designs against. These entries appear on the pack checklist
  as manual "PULL FROM SECURE STOCK" steps that a named user checks
  off by hand, and the pack is not complete until they are pulled.
* ``library_doc`` -- a dealer-added document from the Stage 5 document
  library (doc_library.py), rendered by the PDF overlay renderer
  (pdf_overlay.py) instead of the text renderer. The resolved
  ``PackDocument`` carries the library record so the pipeline can
  render it.

Conditional entries: the FTC Buyers Guide (and its covering note)
applies to USED vehicles only; ``build_pack`` resolves conditionals
against the deal's vehicle. Library documents resolve through the
library's own conditions (retail new / retail used).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from .packaging_checklist import CHECKLIST_ID

# Retired in Stage 5 (decision 12): the pre-digital-contracting long
# contract stock class. Kept so stored print configurations that name
# it still load; the standard pack never emits it.
DOC_CLASS_LAW_CONTRACT = "law_contract_long"
DOC_CLASS_STANDARD_LETTER = "standard_letter"

KIND_PRINT = "print"
KIND_SECURE_STOCK = "secure_stock"
KIND_LIBRARY_DOC = "library_doc"

# Conditions a pack entry can carry.
CONDITION_ALWAYS = "always"
CONDITION_USED_ONLY = "used_only"


@dataclass(frozen=True)
class PackEntry:
    """One entry in the pack definition."""

    doc_id: str
    title: str
    doc_class: str
    kind: str = KIND_PRINT
    condition: str = CONDITION_ALWAYS

    @property
    def is_printable(self) -> bool:
        return self.kind == KIND_PRINT

    @property
    def is_secure_stock(self) -> bool:
        return self.kind == KIND_SECURE_STOCK


@dataclass(frozen=True)
class PackDocument:
    """One resolved pack entry for a specific deal (conditionals
    already applied). This is what the print pipeline prints or checks
    off."""

    doc_id: str
    title: str
    doc_class: str
    kind: str = KIND_PRINT
    # For kind == KIND_LIBRARY_DOC: the doc_library.DealerDocument
    # record this entry renders from (untyped to avoid an import cycle).
    library_document: object = None

    @property
    def is_printable(self) -> bool:
        return self.kind in (KIND_PRINT, KIND_LIBRARY_DOC)

    @property
    def is_secure_stock(self) -> bool:
        return self.kind == KIND_SECURE_STOCK


# The standard retail pack, in print order. The contract goes first.
# Since Stage 5 it is a letter document like everything else.
STANDARD_RETAIL_PACK: tuple[PackEntry, ...] = (
    PackEntry(
        doc_id="retail_installment_contract",
        title="Retail Installment Contract (LAW)",
        doc_class=DOC_CLASS_STANDARD_LETTER,
    ),
    PackEntry(
        doc_id="buyers_order",
        title="Buyer's Order",
        doc_class=DOC_CLASS_STANDARD_LETTER,
    ),
    PackEntry(
        doc_id="deal_worksheet",
        title="Deal Worksheet",
        doc_class=DOC_CLASS_STANDARD_LETTER,
    ),
    PackEntry(
        doc_id="title_application",
        title="Title Application (HSMV 82040)",
        doc_class=DOC_CLASS_STANDARD_LETTER,
    ),
    PackEntry(
        doc_id="odometer_statement",
        title="Odometer Disclosure Statement",
        doc_class=DOC_CLASS_STANDARD_LETTER,
    ),
    PackEntry(
        doc_id="buyers_guide",
        title="FTC Buyers Guide",
        doc_class=DOC_CLASS_STANDARD_LETTER,
        condition=CONDITION_USED_ONLY,
    ),
    PackEntry(
        doc_id="buyers_guide_note",
        title="FTC Buyers Guide - Covering Note",
        doc_class=DOC_CLASS_STANDARD_LETTER,
        condition=CONDITION_USED_ONLY,
    ),
    PackEntry(
        doc_id="secure_poa",
        title="Secure Power of Attorney (HSMV 82995) - PULL FROM SECURE STOCK",
        doc_class=DOC_CLASS_STANDARD_LETTER,
        kind=KIND_SECURE_STOCK,
    ),
    PackEntry(
        doc_id="secure_reassignment",
        title="Secure Reassignment of Title - PULL FROM SECURE STOCK",
        doc_class=DOC_CLASS_STANDARD_LETTER,
        kind=KIND_SECURE_STOCK,
    ),
)


# The standard lease pack (Stage 6), in print order. The built-in
# part is the lease worksheet itself; the dealer's own lease form
# joins through the document library as a `lease`-condition document
# (the dealer-supplied lease form rides the Stage 5 add-a-doc flow).
LEASE_PACK: tuple[PackEntry, ...] = (
    PackEntry(
        doc_id="lease_worksheet",
        title="Lease Worksheet",
        doc_class=DOC_CLASS_STANDARD_LETTER,
    ),
)

# All app-owned pack IDs share the print-state dictionary namespace.
# Reserve the complete set, even when a conditional entry is absent from
# this deal, so changing deal type cannot turn a dealer form into a summary.
RESERVED_DOCUMENT_IDS = frozenset(
    entry.doc_id for entry in (*STANDARD_RETAIL_PACK, *LEASE_PACK)
) | {CHECKLIST_ID}


def build_lease_pack(deal, library=None) -> list[PackDocument]:
    """Resolve the lease pack for one lease deal (Stage 6).

    The lease worksheet print item, plus -- when ``library`` (a
    doc_library.DocumentLibrary) is given -- its APPROVED documents
    whose condition is `lease`. Retail-condition library documents
    never join a lease pack: the library resolves a lease deal to
    the `lease` condition only (doc_library._deal_condition keys off
    the session's ``deal_kind``).
    """
    resolved: list[PackDocument] = [
        PackDocument(
            doc_id=entry.doc_id,
            title=entry.title,
            doc_class=entry.doc_class,
            kind=entry.kind,
        )
        for entry in LEASE_PACK
    ]
    if library is not None:
        for document in library.documents_for_pack(deal):
            resolved.append(
                PackDocument(
                    doc_id=document.doc_id,
                    title=document.title,
                    doc_class=DOC_CLASS_STANDARD_LETTER,
                    kind=KIND_LIBRARY_DOC,
                    library_document=document,
                )
            )
    return _append_checklist(resolved)


def _vehicle_is_new(deal) -> bool:
    """Read the vehicle's new/used flag off a DealSession or inputs."""
    inputs = getattr(deal, "inputs", deal)
    vehicle = getattr(inputs, "vehicle", None)
    return True if vehicle is None else bool(vehicle.is_new)


def build_pack(
    deal,
    entries: Optional[tuple[PackEntry, ...]] = None,
    library=None,
) -> list[PackDocument]:
    """Resolve the standard retail pack for one deal.

    ``deal`` is a DealSession (or anything carrying ``inputs.vehicle``).
    Conditional entries are included only when their condition holds
    for this deal: the Buyers Guide entries only for a USED vehicle.

    ``library`` (optional, Stage 5) is a doc_library.DocumentLibrary:
    its APPROVED documents whose print conditions match this deal are
    appended after the standard entries, as ``library_doc`` pack
    documents carrying the library record. Unapproved library
    documents are never included (the library enforces that).
    """
    is_new = _vehicle_is_new(deal)
    resolved: list[PackDocument] = []
    for entry in entries or STANDARD_RETAIL_PACK:
        if (entry.doc_id == 'retail_installment_contract'
                and getattr(deal, 'packaging_details', {}).get('transaction_type') == 'cash'):
            continue
        if entry.condition == CONDITION_USED_ONLY and is_new:
            continue
        resolved.append(
            PackDocument(
                doc_id=entry.doc_id,
                title=entry.title,
                doc_class=entry.doc_class,
                kind=entry.kind,
            )
        )
    if library is not None:
        for document in library.documents_for_pack(deal):
            resolved.append(
                PackDocument(
                    doc_id=document.doc_id,
                    title=document.title,
                    doc_class=DOC_CLASS_STANDARD_LETTER,
                    kind=KIND_LIBRARY_DOC,
                    library_document=document,
                )
            )
    return _append_checklist(resolved)


def _append_checklist(resolved):
    documents = [*resolved, PackDocument(
        CHECKLIST_ID, 'Paper Deal Packaging Checklist', DOC_CLASS_STANDARD_LETTER,
    )]
    seen = set()
    for document in documents:
        if document.kind == KIND_LIBRARY_DOC and document.doc_id in RESERVED_DOCUMENT_IDS:
            raise ValueError(
                f"Dealer document {document.doc_id!r} uses an ID reserved for "
                "an app-generated or standard pack item. Printing is blocked; "
                "preserve the existing library files, return the conflicting "
                "form to draft, and re-add it with a different document ID, "
                "then obtain manager approval."
            )
        if document.doc_id in seen:
            raise ValueError(
                f"The resolved pack contains duplicate document ID "
                f"{document.doc_id!r}. Printing is blocked; each document "
                "must have a unique ID."
            )
        seen.add(document.doc_id)
    return documents
