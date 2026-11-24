"""Print pipeline: a certified deal's pack, printed and proven (Stage 4).

A ``PrintSession`` prints ONE deal's pack (pack.py) through a
``PrinterProvider``, driven by three pieces of Stage 1-3 state:

* the ``DealSession`` -- certified deals print clean; an uncertified
  deal may still be printed, but every payload carries the DRAFT
  watermark and the session is flagged ``draft_only`` (spec Section 8);
* the Stage 2 setup record -- printer + tray/paper assignments per
  document class. A document whose class has no assignment prints
  nothing and the failure is recorded, never improvised around.
  Since Stage 5 every document is letter size, so in practice the
  whole pack resolves through the one standard-letter assignment;
* the print config (print_config.py) -- copy sets per recipient and
  per-printer/per-document calibration offsets.

Checklist semantics: each pack item is pending -> printed (timestamp +
full job details) or pending -> failed (error recorded); secure-stock
items are pending -> pulled (a named user checks them off; they are
NEVER sent to a printer). ``print_all`` walks the pack in order and
stops at the first failure, leaving the rest pending; ``resume``
prints only what is still pending or failed -- an already-printed item
is never sent twice by resume. State is saved to one JSON file per
deal after EVERY item change, so a jam, a crash, or a closed window
mid-pack loses nothing, and a reloaded session resumes exactly.

Void and reprint: a printed item can be voided by a named user with a
reason (audit ``document_voided``); it returns to pending flagged as a
reprint, and its next print carries the "REPRINT -- original voided"
marking (audit ``document_reprinted``).

Stock confirmation (RETIRED as a gate in Stage 5): Stage 4 required
the user to confirm the extremely long contract stock was loaded
before the contract printed. Digital contracting made every document
letter size (decision 12), so there is no special stock left to
confirm and NO item in the standard pack is gated on it. The
``stock_confirmation`` callback parameter remains on the session as a
generic mechanism a future special-stock document could use, but the
pipeline does not call it for the standard pack.

Stage 5 additions: a session may carry the dealer document library
(doc_library.py); matching APPROVED library documents join the pack
(pack.build_pack) and render as PDF overlay bytes (pdf_overlay.py)
through an injectable payload builder. A DRILL deal (drill.py)
prints with the SAMPLE banner on every document, certified or not.

All logic is UI-independent and headless-testable; the Tkinter driver
is ``print_ui.py``. Standard library only.
"""

from __future__ import annotations

import json
import hashlib
from functools import wraps
from .file_transactions import file_transaction
from .packaging_checklist import CHECKLIST_ID, generate_checklist, pack_fingerprint, render_checklist, deal_record_fingerprint
from .users import has_permission, ACTION_CERTIFY_DEAL
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from .audit import (
    EVENT_DOCUMENT_PRINT_FAILED,
    EVENT_DOCUMENT_PRINTED,
    EVENT_DOCUMENT_REPRINTED,
    EVENT_DOCUMENT_VOIDED,
    EVENT_PACK_PRINT_COMPLETED,
    EVENT_PACK_PRINT_STARTED,
    EVENT_SECURE_STOCK_PULLED,
    AuditLog,
)
from .doc_library import resolve_field_values, product_field_values, DocumentLibraryError, _deal_condition
from .jpeg_image import JpegImageError
from .pack import (
    DOC_CLASS_LAW_CONTRACT,
    KIND_LIBRARY_DOC,
    KIND_PRINT,
    KIND_SECURE_STOCK,
    PackDocument,
    build_lease_pack,
    build_pack,
)
from .pdf_overlay import render_overlay_pdf, PdfOverlayError
from .print_config import PrintConfig
from .printers import PrinterProvider, get_printer_provider
from .setup_wizard import (
    DOC_LAW_CONTRACT,
    DOC_STANDARD_LETTER,
    SetupRecord,
    TrayAssignment,
)
from .text_renderer import render_document

DEFAULT_PRINT_SESSIONS_DIR = Path("data") / "print_sessions"

STATUS_PENDING = "pending"
STATUS_PRINTED = "printed"
STATUS_FAILED = "failed"
STATUS_PULLED = "pulled"

# Setup tray-mapping keys (Stage 2) -> pack document classes (Stage 4).
_SETUP_CLASS_FOR_PACK_CLASS = {
    DOC_CLASS_LAW_CONTRACT: DOC_LAW_CONTRACT,
    "standard_letter": DOC_STANDARD_LETTER,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class PrintPipelineError(Exception):
    """A print action cannot be carried out. Message says why."""


def _current_session_operation(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with file_transaction(self.file_path):
            self._assert_state_current()
            return method(self, *args, **kwargs)
    return call


def _actor_name(actor) -> str:
    if actor is None:
        return ""
    return getattr(actor, "username", None) or str(actor)


@dataclass
class PrintItemState:
    """Checklist state for one pack item within one print session."""

    doc_id: str
    title: str
    doc_class: str
    kind: str = KIND_PRINT
    status: str = STATUS_PENDING
    printed_at: Optional[str] = None
    job: Optional[dict] = None  # details of the successful job
    error: Optional[str] = None
    print_count: int = 0
    # Void bookkeeping: the most recent void, kept after a reprint so
    # the record of the spoiled original is never lost.
    voided_by: Optional[str] = None
    voided_at: Optional[str] = None
    void_reason: Optional[str] = None
    reprint_pending: bool = False  # next print is a reprint of a void
    # Secure-stock check-off.
    pulled_by: Optional[str] = None
    pulled_at: Optional[str] = None

    @property
    def is_done(self) -> bool:
        if self.kind == KIND_SECURE_STOCK:
            return self.status == STATUS_PULLED
        return self.status == STATUS_PRINTED


@dataclass
class PrintSession:
    """Prints one deal's pack; persists its checklist as it goes."""

    deal: object  # DealSession (kept untyped to avoid a cycle)
    tray_mapping: dict  # pack doc_class -> TrayAssignment
    print_config: PrintConfig
    session_dir: Path = field(default_factory=lambda: DEFAULT_PRINT_SESSIONS_DIR)
    provider: Optional[PrinterProvider] = None
    audit_log: Optional[AuditLog] = None
    actor: object = None
    stock_confirmation: Optional[Callable[[PackDocument], bool]] = None
    require_stock_confirmation: bool = True
    # Stage 5: the dealer document library, and an optional override
    # for how library documents become payloads (tests/tools may
    # inject their own; the default is the PDF overlay renderer).
    library: object = None
    document_payload_builder: Optional[Callable] = None

    def __post_init__(self) -> None:
        self.provider = self.provider or get_printer_provider()
        self.session_dir = Path(self.session_dir)
        self.deal_id: str = getattr(self.deal, "deal_id", "unknown-deal")
        self.draft_only: bool = not bool(getattr(self.deal, "is_certified", False))
        # A drill deal prints SAMPLE on everything, even once certified
        # for practice (Stage 5, drill.py).
        self.sample_only: bool = bool(getattr(self.deal, "is_drill", False))
        # Stage 6: a lease deal prints the lease pack (lease
        # worksheet + lease-condition library documents); every
        # other deal prints the retail pack.
        if getattr(self.deal, "deal_kind", None) == "lease":
            self.documents: list[PackDocument] = build_lease_pack(
                self.deal, library=self.library
            )
        else:
            self.documents = build_pack(self.deal, library=self.library)
        self.items: dict[str, PrintItemState] = {
            doc.doc_id: PrintItemState(
                doc_id=doc.doc_id, title=doc.title,
                doc_class=doc.doc_class, kind=doc.kind,
            )
            for doc in self.documents
        }
        self._stock_confirmed_this_run = False
        self._completion_audited = False
        self.packaging_rows = generate_checklist(self.deal, self.documents)
        self.packaging_checks = {}
        self.packaging_revision = 1
        self.packaging_history = []
        self.packaging_fingerprint = pack_fingerprint(self.deal, self.documents)
        self.packaging_artwork_hashes = self._artwork_hashes()
        self._state_version = 0
        self._expected_state_digest = None
        with file_transaction(self.file_path):
            if self.file_path.exists():
                raw = self.file_path.read_bytes()
                try:
                    data = json.loads(raw)
                    if not isinstance(data, dict) or data.get('deal_id') != self.deal_id:
                        raise ValueError('The saved print session has the wrong identity.')
                    version = data.get('state_version', 0)
                    if type(version) is not int or version < 0:
                        raise ValueError('The saved print session has an invalid state version.')
                except (ValueError, TypeError) as exc:
                    raise PrintPipelineError('The saved print session is invalid. Ask dealer IT to recover it.') from exc
                self._state_version = version
                self._expected_state_digest = hashlib.sha256(raw).hexdigest()
                self._restore(data)
                # Upgrade legacy records or invalidate a changed pack atomically.
                # An ordinary load does not write or briefly publish defaults.
                if self.to_dict() != data:
                    self.save()
            else:
                self.save()

    # -- construction helpers -------------------------------------------------

    @classmethod
    def from_setup(
        cls,
        *,
        deal,
        setup,
        print_config: PrintConfig,
        session_dir: Union[str, Path] = DEFAULT_PRINT_SESSIONS_DIR,
        provider: Optional[PrinterProvider] = None,
        audit_log: Optional[AuditLog] = None,
        actor=None,
        stock_confirmation: Optional[Callable[[PackDocument], bool]] = None,
        require_stock_confirmation: bool = True,
        library=None,
        document_payload_builder: Optional[Callable] = None,
    ) -> "PrintSession":
        """Build a session from a Stage 2 setup: a SetupRecord, the
        parsed setup-file dict, or the path to the setup file."""
        return cls(
            deal=deal,
            tray_mapping=resolve_tray_mapping(setup),
            print_config=print_config,
            session_dir=Path(session_dir),
            provider=provider,
            audit_log=audit_log,
            actor=actor,
            stock_confirmation=stock_confirmation,
            require_stock_confirmation=require_stock_confirmation,
            library=library,
            document_payload_builder=document_payload_builder,
        )

    @classmethod
    def load(
        cls,
        deal_id: str,
        *,
        deal,
        setup,
        print_config: PrintConfig,
        session_dir: Union[str, Path] = DEFAULT_PRINT_SESSIONS_DIR,
        provider: Optional[PrinterProvider] = None,
        audit_log: Optional[AuditLog] = None,
        actor=None,
        stock_confirmation: Optional[Callable[[PackDocument], bool]] = None,
        require_stock_confirmation: bool = True,
        library=None,
        document_payload_builder: Optional[Callable] = None,
    ) -> "PrintSession":
        """Reload a saved print session for a deal and restore its
        checklist exactly (items already printed stay printed)."""
        path = Path(session_dir) / f"{deal_id}.json"
        if getattr(deal, 'deal_id', None) != deal_id:
            raise PrintPipelineError('The saved print session must match the deal being opened.')
        with file_transaction(path):
            if not path.is_file():
                raise PrintPipelineError(f"No saved print session for deal {deal_id!r} at {path}.")
            return cls.from_setup(
                deal=deal, setup=setup, print_config=print_config,
                session_dir=session_dir, provider=provider,
                audit_log=audit_log, actor=actor,
                stock_confirmation=stock_confirmation,
                require_stock_confirmation=require_stock_confirmation,
                library=library,
                document_payload_builder=document_payload_builder,
            )

    # -- views ------------------------------------------------------------------

    @property
    def file_path(self) -> Path:
        return self.session_dir / f"{self.deal_id}.json"

    @property
    def is_complete(self) -> bool:
        """Every printable item printed and every secure item pulled."""
        return (self._pack_is_current() and not self.draft_only and not self.sample_only
                and all(item.is_done for item in self.items.values())
                and all(row.item_id in self.packaging_checks for row in self.packaging_rows))

    def _pack_is_current(self):
        with file_transaction(self.file_path):
            try:
                self._assert_state_current()
            except PrintPipelineError:
                return False
            return self._deal_and_forms_are_current()

    def _assert_state_current(self):
        try:
            current_digest = hashlib.sha256(self.file_path.read_bytes()).hexdigest()
        except FileNotFoundError:
            current_digest = None
        except OSError as exc:
            raise PrintPipelineError('The saved print session cannot be read. Ask dealer IT to recover it.') from exc
        if current_digest != self._expected_state_digest:
            raise PrintPipelineError('This print session changed in another window or is missing/corrupt. Close it and reopen the current package.')

    def _deal_and_forms_are_current(self):
        try:
            if self._artwork_hashes() != self.packaging_artwork_hashes:
                return False
            if hasattr(self.deal, 'file_path'):
                saved = json.loads(self.deal.file_path.read_text(encoding='utf-8'))
                if deal_record_fingerprint(saved) != deal_record_fingerprint(self.deal.to_dict()):
                    return False
            documents = self.documents
            if self.library is not None and hasattr(self.library, 'library_dir'):
                from .doc_library import DocumentLibrary
                current_library = DocumentLibrary(self.library.library_dir)
                builder = build_lease_pack if getattr(self.deal, 'deal_kind', '') == 'lease' else build_pack
                documents = builder(self.deal, library=current_library)
            return self.packaging_fingerprint == pack_fingerprint(self.deal, documents)
        except (OSError, ValueError, AttributeError, DocumentLibraryError):
            return False

    def _document_artwork_hash(self, document):
        record = document.library_document
        if record is None:
            raise DocumentLibraryError(f'{document.doc_id}: saved required form is unavailable or unapproved. Restore/reapprove it and reopen the pack.')
        if record.form_source == 'page_images':
            images = self.library.load_page_images(record)
            # Validate all bytes against the exact manifest; retain page order.
            return {str(page): hashlib.sha256(image.data).hexdigest()
                    for page, image in images.items()}
        return hashlib.sha256(self.library.document_path(record).read_bytes()).hexdigest()

    def _artwork_hashes(self):
        if self.library is None or not hasattr(self.library, 'document_path'):
            return {}
        return {d.doc_id: self._document_artwork_hash(d)
                for d in self.documents if d.kind == KIND_LIBRARY_DOC}

    def _require_current_pack(self, actor):
        # Image damage is a recorded document failure before any spooler call.
        for document in self.documents:
            if document.kind != KIND_LIBRARY_DOC:
                continue
            try:
                self._document_artwork_hash(document)
            except (DocumentLibraryError, JpegImageError, OSError) as exc:
                item = self.item(document.doc_id)
                # Damage cannot undo a successful physical submission and
                # silently make it retryable without void/reprint authority.
                if not item.is_done:
                    item.status, item.error = STATUS_FAILED, str(exc)
                    self._audit(EVENT_DOCUMENT_PRINT_FAILED, actor,
                                {'deal_id': self.deal_id, 'doc_id': document.doc_id, 'error': item.error})
                    self.save()
                raise PrintPipelineError(str(exc)) from exc
        if not self._pack_is_current():
            raise PrintPipelineError('The deal or resolved pack changed. Reopen a current print session after recertification.')

    @_current_session_operation
    def check_packaging_item(self, item_id, actor=None, *, reference=''):
        actor = actor if actor is not None else self.actor
        if not _actor_name(actor):
            raise PrintPipelineError('A named user must check off packaging items.')
        if not self._pack_is_current() or self.draft_only or self.sample_only:
            raise PrintPipelineError('Use a current, certified real deal for packaging completion.')
        row = next((r for r in self.packaging_rows if r.item_id == item_id), None)
        if row is None:
            raise PrintPipelineError('Unknown packaging item.')
        if not all(item.is_done for item in self.items.values()):
            raise PrintPipelineError('Print the pack and pull secure originals before signing the packaging checklist.')
        if row.requires_paper and not reference.strip():
            raise PrintPipelineError('Record the separately supplied paper form or inspection evidence by name.')
        if item_id == 'fi_signoff' and not has_permission(actor, ACTION_CERTIFY_DEAL):
            raise PrintPipelineError('F&I sign-off requires an admin, manager or F&I user.')
        if item_id in self.packaging_checks:
            return self.packaging_checks[item_id]
        checked = {'actor': _actor_name(actor), 'timestamp': _now_iso(),
                   'reference': reference.strip(), 'revision': self.packaging_revision,
                   'separately_supplied': row.requires_paper}
        self.packaging_checks[item_id] = checked
        self._audit('packaging_item_checked', actor,
                    {'deal_id': self.deal_id, 'item_id': item_id, **checked})
        self.save()
        self._maybe_audit_completion(actor)
        return checked

    def item(self, doc_id: str) -> PrintItemState:
        try:
            return self.items[doc_id]
        except KeyError:
            raise PrintPipelineError(
                f"Document {doc_id!r} is not in this deal's pack."
            ) from None

    def document(self, doc_id: str) -> PackDocument:
        for doc in self.documents:
            if doc.doc_id == doc_id:
                return doc
        raise PrintPipelineError(f"Document {doc_id!r} is not in this deal's pack.")

    # -- audit --------------------------------------------------------------------

    def _audit(self, event_type: str, actor, details: dict) -> None:
        if self.audit_log is not None:
            self.audit_log.record(event_type, _actor_name(actor), details)

    # -- tray / stock resolution -----------------------------------------------------

    def assignment_for(self, document: PackDocument) -> Optional[TrayAssignment]:
        return self.tray_mapping.get(document.doc_class)

    def _confirm_stock(self, document: PackDocument) -> bool:
        """Generic stock-confirmation mechanism, retained from Stage 4.

        Since Stage 5 retired the long stock (decision 12), NO
        standard-pack document calls this -- the standard pack is
        never gated on a stock confirmation. It remains available if
        a future special-stock document class is ever introduced."""
        if not self.require_stock_confirmation:
            return True
        if self._stock_confirmed_this_run:
            return True
        if self.stock_confirmation is None:
            return False
        confirmed = bool(self.stock_confirmation(document))
        if confirmed:
            self._stock_confirmed_this_run = True
        return confirmed

    # -- printing -----------------------------------------------------------------------

    @_current_session_operation
    def print_item(self, doc_id: str, actor=None) -> PrintItemState:
        """Print one pack item now (or record why it did not print)."""
        actor = actor if actor is not None else self.actor
        self._require_current_pack(actor)
        document = self.document(doc_id)
        item = self.item(doc_id)
        if document.kind == KIND_SECURE_STOCK:
            raise PrintPipelineError(
                f"{document.title}: secure forms are never printed. "
                "Pull the controlled original from secure stock and "
                "check it off (pull_secure_item)."
            )
        if item.status == STATUS_PRINTED:
            return item
        if doc_id == CHECKLIST_ID and any(
            not self.items[d.doc_id].is_done
            for d in self.documents if d.doc_id != CHECKLIST_ID and d.kind != KIND_SECURE_STOCK
        ):
            raise PrintPipelineError('The packaging checklist prints last, after every other printed document.')

        assignment = self.assignment_for(document)
        if assignment is None:
            message = (
                f"No tray assignment in setup for document class "
                f"{document.doc_class!r}: nothing was printed. Run the "
                "setup wizard's tray-mapping step for this class."
            )
            item.status = STATUS_FAILED
            item.error = message
            self._audit(
                EVENT_DOCUMENT_PRINT_FAILED, actor,
                {"deal_id": self.deal_id, "doc_id": doc_id, "error": message},
            )
            self.save()
            raise PrintPipelineError(message)

        copy_set = self.print_config.copy_set_for(doc_id, document.doc_class)
        if doc_id == CHECKLIST_ID:
            from .print_config import CopySet
            copy_set = CopySet(dealer=1)
        copies = copy_set.total
        offsets = self.print_config.get_offsets(assignment.printer_name, doc_id)
        is_reprint = item.reprint_pending
        self.draft_only = not bool(getattr(self.deal, "is_certified", False))
        self.sample_only = bool(getattr(self.deal, "is_drill", False))
        self._library_provenance = {}
        try:
            if doc_id == CHECKLIST_ID:
                payload = render_checklist(self.deal, self.documents,
                    revision=self.packaging_revision, draft=self.draft_only,
                    reprint=is_reprint, sample=self.sample_only)
            elif document.kind == KIND_LIBRARY_DOC:
                payload = self._build_library_payload(
                    document,
                    draft=self.draft_only,
                    reprint=is_reprint,
                    offsets_mm=offsets,
                )
            else:
                payload = render_document(
                    document=document,
                    deal=self.deal,
                    preset=getattr(self.deal, "preset", None),
                    draft=self.draft_only,
                    reprint=is_reprint,
                    sample=self.sample_only,
                    offsets_mm=offsets,
                )
        except (DocumentLibraryError, JpegImageError, PdfOverlayError, OSError, PrintPipelineError) as exc:
            item.status, item.error = STATUS_FAILED, str(exc)
            self._audit(EVENT_DOCUMENT_PRINT_FAILED, actor,
                        {'deal_id': self.deal_id, 'doc_id': doc_id, 'error': item.error})
            self.save()
            raise PrintPipelineError(item.error) from exc
        result = self.provider.print_document(
            assignment.printer_name,
            payload,
            doc_id=doc_id,
            title=document.title,
            tray=assignment.tray,
            paper=assignment.paper,
            copies=copies,
            offsets_mm=offsets,
        )
        if result.ok:
            item.status = STATUS_PRINTED
            item.printed_at = _now_iso()
            item.error = None
            item.print_count += 1
            item.job = {
                "printer_name": assignment.printer_name,
                "tray": assignment.tray,
                "paper": assignment.paper,
                "copies": copies,
                "copy_set": copy_set.to_dict(),
                "recipients": list(copy_set.recipient_order()),
                "offsets_mm": offsets,
                "spooler_job_id": result.job_id,
                "reprint": is_reprint,
                **self._library_provenance,
            }
            item.reprint_pending = False
            self._audit(
                EVENT_DOCUMENT_REPRINTED if is_reprint else EVENT_DOCUMENT_PRINTED,
                actor,
                {
                    "deal_id": self.deal_id, "doc_id": doc_id,
                    "printer_name": assignment.printer_name,
                    "tray": assignment.tray, "paper": assignment.paper,
                    "copies": copies,
                    **self._library_provenance,
                },
            )
        else:
            item.status = STATUS_FAILED
            item.error = result.message or "The printer reported a failure."
            self._audit(
                EVENT_DOCUMENT_PRINT_FAILED, actor,
                {"deal_id": self.deal_id, "doc_id": doc_id, "error": item.error},
            )
        self.save()
        self._maybe_audit_completion(actor)
        return item

    def _build_library_payload(
        self, document: PackDocument, *, draft: bool, reprint: bool,
        offsets_mm: Optional[dict],
    ):
        """Payload for a dealer library document: PDF overlay bytes.

        An injected ``document_payload_builder`` wins; the default
        resolves the deal's field values and renders the overlay PDF
        (pdf_overlay.py), bannered SAMPLE for drills, DRAFT for
        uncertified deals, and marked on a reprint.
        """
        if self.document_payload_builder is not None:
            return self.document_payload_builder(
                document, self.deal,
                draft=draft, reprint=reprint, offsets_mm=offsets_mm,
            )
        if self.library is None:
            raise PrintPipelineError(f'Library document {document.doc_id!r} has no available library.')
        record = self.library.get_document(document.doc_id, refresh=True, reviewed=False)
        if not record.is_approved or _deal_condition(self.deal) not in record.when_it_prints:
            raise PrintPipelineError(f'{record.title}: review current print conditions and obtain manager/admin approval, then reopen the pack.')
        images = self.library.load_page_images(record)
        self._library_provenance = {
            'library_revision': record.image_revision,
            'library_page_sha256': {str(page.page): page.sha256 for page in record.pages},
        }
        if self.sample_only:
            banner = "SAMPLE"
        elif draft:
            banner = "DRAFT"
        elif reprint:
            banner = "REPRINT - original voided"
        else:
            banner = None
        values = resolve_field_values(self.deal)
        values.update(product_field_values(self.deal, record.doc_id))
        return render_overlay_pdf(
            record, values, banner=banner, offsets_mm=offsets_mm, page_images=images
        )

    @_current_session_operation
    def _run_remaining(self, actor, *, mode: str) -> dict:
        """Print every still-unfinished printable item, in pack order,
        stopping at the first failure. Shared by print_all / resume."""
        self._require_current_pack(actor)
        self._audit(
            EVENT_PACK_PRINT_STARTED, actor,
            {"deal_id": self.deal_id, "mode": mode, "draft_only": self.draft_only},
        )
        printed: list[str] = []
        failed: Optional[str] = None
        for document in self.documents:
            item = self.items[document.doc_id]
            if item.is_done or document.kind == KIND_SECURE_STOCK:
                continue
            try:
                outcome = self.print_item(document.doc_id, actor=actor)
            except PrintPipelineError:
                failed = document.doc_id
                break
            if outcome.status == STATUS_PRINTED:
                printed.append(document.doc_id)
            else:  # failed (incl. stock not confirmed): stop the run here
                failed = document.doc_id
                break
        remaining = [
            doc.doc_id
            for doc in self.documents
            if not self.items[doc.doc_id].is_done
        ]
        self._maybe_audit_completion(actor)
        return {"printed": printed, "failed": failed, "remaining": remaining}

    def print_all(self, actor=None) -> dict:
        """Print the whole pack (what is not already printed), in order."""
        return self._run_remaining(actor if actor is not None else self.actor, mode="print_all")

    def resume(self, actor=None) -> dict:
        """Continue after a failure: only pending/failed items print;
        already-printed items are never re-sent."""
        return self._run_remaining(actor if actor is not None else self.actor, mode="resume")

    # -- secure stock ---------------------------------------------------------------------

    @_current_session_operation
    def pull_secure_item(self, doc_id: str, actor=None) -> PrintItemState:
        """Check off a secure-stock item: a named user pulled the
        controlled original by hand. Never sends anything to a printer."""
        actor = actor if actor is not None else self.actor
        document = self.document(doc_id)
        item = self.item(doc_id)
        if document.kind != KIND_SECURE_STOCK:
            raise PrintPipelineError(
                f"{document.title} is a printed document, not a secure-stock item."
            )
        if not _actor_name(actor):
            raise PrintPipelineError(
                "Secure stock must be checked off by a named user."
            )
        item.status = STATUS_PULLED
        item.pulled_by = _actor_name(actor)
        item.pulled_at = _now_iso()
        self._audit(
            EVENT_SECURE_STOCK_PULLED, actor,
            {"deal_id": self.deal_id, "doc_id": doc_id},
        )
        self.save()
        self._maybe_audit_completion(actor)
        return item

    # -- void and reprint ---------------------------------------------------------------------

    @_current_session_operation
    def void_item(self, doc_id: str, actor=None, reason: str = "") -> PrintItemState:
        """Void a printed copy (jam damage, wrong stock, spoiled form).

        Records who voided it, when, and why (audit ``document_voided``)
        and returns the item to pending flagged as a reprint, so its
        replacement carries the REPRINT marking and can never be
        mistaken for a second original.
        """
        actor = actor if actor is not None else self.actor
        item = self.item(doc_id)
        if item.kind == KIND_SECURE_STOCK:
            raise PrintPipelineError(
                "Secure-stock items are checked off, not printed; "
                "there is no printed copy to void."
            )
        if item.status != STATUS_PRINTED:
            raise PrintPipelineError(
                f"Only a printed item can be voided; {doc_id!r} is {item.status}."
            )
        item.voided_by = _actor_name(actor)
        item.voided_at = _now_iso()
        item.void_reason = (reason or "").strip()
        item.status = STATUS_PENDING
        item.reprint_pending = True
        item.job = None
        item.printed_at = None
        self._completion_audited = False
        self.packaging_history.append({'revision': self.packaging_revision,
            'checks': dict(self.packaging_checks), 'superseded_at': _now_iso(),
            'voided_doc_id': doc_id, 'fingerprint': self.packaging_fingerprint})
        self.packaging_revision += 1
        self.packaging_checks = {}
        if doc_id != CHECKLIST_ID:
            checklist = self.items[CHECKLIST_ID]
            checklist.reprint_pending = checklist.status == STATUS_PRINTED or checklist.reprint_pending
            checklist.status = STATUS_PENDING
            checklist.printed_at = None
            checklist.job = None
        self._audit(
            EVENT_DOCUMENT_VOIDED, actor,
            {
                "deal_id": self.deal_id, "doc_id": doc_id,
                "reason": item.void_reason,
            },
        )
        self.save()
        return item

    @_current_session_operation
    def reprint_item(self, doc_id: str, actor=None) -> PrintItemState:
        """Reprint a voided item (its payload carries the REPRINT mark)."""
        item = self.item(doc_id)
        if not item.reprint_pending:
            raise PrintPipelineError(
                f"{doc_id!r} has not been voided; there is nothing to reprint."
            )
        return self.print_item(doc_id, actor=actor)

    # -- completion ------------------------------------------------------------------------------

    def _maybe_audit_completion(self, actor) -> None:
        if self.is_complete and not self._completion_audited:
            self._completion_audited = True
            self._audit(
                EVENT_PACK_PRINT_COMPLETED, actor,
                {"deal_id": self.deal_id, "draft_only": self.draft_only},
            )
            self.save()

    # -- persistence ----------------------------------------------------------------------------------

    @_current_session_operation
    def save(self, path: Optional[Union[str, Path]] = None) -> Path:
        target = Path(path) if path is not None else self.file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        canonical = target.resolve() == self.file_path.resolve()
        data = self.to_dict()
        if canonical:
            data['state_version'] = self._state_version + 1
        payload = json.dumps(data, indent=2, sort_keys=True).encode('utf-8')
        if canonical:
            self._write_state_file(target, payload)
        else:
            # Export is a new immutable snapshot, never a back door into a
            # different live session or an older exported approval branch.
            if target.exists():
                raise PrintPipelineError('The export destination already exists. Choose a new versioned filename.')
            with file_transaction(target):
                if target.exists():
                    raise PrintPipelineError('The export destination already exists. Choose a new versioned filename.')
                self._write_state_file(target, payload)
        if canonical:
            self._state_version = data['state_version']
            self._expected_state_digest = hashlib.sha256(payload).hexdigest()
        return target

    @staticmethod
    def _write_state_file(target, payload):
        temporary = target.with_name(target.name + '.tmp')
        temporary.write_bytes(payload)
        temporary.replace(target)

    def to_dict(self) -> dict:
        return {
            "deal_id": self.deal_id,
            "format_version": "stage4-1",
            "document_order": [doc.doc_id for doc in self.documents],
            "state_version": self._state_version,
            "draft_only": self.draft_only,
            "completion_audited": self._completion_audited,
            "packaging_fingerprint": self.packaging_fingerprint,
            "packaging_artwork_hashes": self.packaging_artwork_hashes,
            "packaging_revision": self.packaging_revision,
            "packaging_checks": self.packaging_checks,
            "packaging_history": self.packaging_history,
            "items": {
                doc_id: {
                    "doc_id": item.doc_id,
                    "title": item.title,
                    "doc_class": item.doc_class,
                    "kind": item.kind,
                    "status": item.status,
                    "printed_at": item.printed_at,
                    "job": item.job,
                    "error": item.error,
                    "print_count": item.print_count,
                    "voided_by": item.voided_by,
                    "voided_at": item.voided_at,
                    "void_reason": item.void_reason,
                    "reprint_pending": item.reprint_pending,
                    "pulled_by": item.pulled_by,
                    "pulled_at": item.pulled_at,
                }
                for doc_id, item in self.items.items()
            },
        }

    def _restore(self, data: dict) -> None:
        # Retain saved obligations even if a form was removed or unapproved.
        # The pack cannot complete or print until current approval is restored
        # and the operator reopens it. The generated checklist remains last.
        from .pack import RESERVED_DOCUMENT_IDS
        for doc_id, raw in data.get('items', {}).items():
            if doc_id not in self.items and raw.get('kind') == KIND_LIBRARY_DOC:
                if doc_id in RESERVED_DOCUMENT_IDS:
                    raise PrintPipelineError(f'{doc_id}: saved dealer form uses a reserved document id.')
                if self.library is not None:
                    try:
                        current = self.library.get_document(doc_id, refresh=True, reviewed=False)
                    except DocumentLibraryError:
                        current = None
                    if (current is not None and current.is_approved
                            and _deal_condition(self.deal) not in current.when_it_prints):
                        # Approved applicability changes belong to the new
                        # revision; missing/unapproved forms still block it.
                        continue
                document = PackDocument(doc_id, raw.get('title', doc_id),
                                        raw.get('doc_class', 'standard_letter'), KIND_LIBRARY_DOC)
                self.documents.append(document)
                self.items[doc_id] = PrintItemState(doc_id, document.title, document.doc_class, KIND_LIBRARY_DOC)
        # Current resolution owns ordering when applicability changes (e.g.
        # new→used adds Buyers Guide entries). Saved order remains history,
        # not authority to reorder a newly resolved package.
        self.documents.sort(key=lambda doc: doc.doc_id == CHECKLIST_ID)
        self.packaging_rows = generate_checklist(self.deal, self.documents)
        self.packaging_fingerprint = pack_fingerprint(self.deal, self.documents)
        previous_fingerprint = data.get('packaging_fingerprint')
        fingerprint_matches = previous_fingerprint == self.packaging_fingerprint
        if previous_fingerprint and not fingerprint_matches:
            # Initial PR4 encoded explicit defaults for unchanged legacy
            # forms. Accept only that exact equivalent current projection.
            fingerprint_matches = previous_fingerprint == pack_fingerprint(
                self.deal, self.documents, legacy_source_fields=True)
        if previous_fingerprint and (
            not fingerprint_matches
            or data.get('packaging_artwork_hashes', {}) != self.packaging_artwork_hashes
        ):
            self.packaging_revision = int(data.get('packaging_revision', 1)) + 1
            self.packaging_history = [*data.get('packaging_history', []), {
                'revision': data.get('packaging_revision', 1),
                'checks': data.get('packaging_checks', {}), 'items': data.get('items', {}),
                'superseded_at': _now_iso(), 'reason': 'deal or resolved pack changed'}]
            return
        self.packaging_revision = int(data.get('packaging_revision', 1))
        self.packaging_checks = dict(data.get('packaging_checks', {}))
        self.packaging_history = list(data.get('packaging_history', []))
        self.draft_only = bool(data.get("draft_only", self.draft_only))
        self._completion_audited = bool(data.get("completion_audited", False))
        for doc_id, raw in data.get("items", {}).items():
            if doc_id not in self.items:
                continue
            item = self.items[doc_id]
            item.status = raw.get("status", STATUS_PENDING)
            item.printed_at = raw.get("printed_at")
            item.job = raw.get("job")
            item.error = raw.get("error")
            item.print_count = int(raw.get("print_count", 0))
            item.voided_by = raw.get("voided_by")
            item.voided_at = raw.get("voided_at")
            item.void_reason = raw.get("void_reason")
            item.reprint_pending = bool(raw.get("reprint_pending", False))
            item.pulled_by = raw.get("pulled_by")
            item.pulled_at = raw.get("pulled_at")


def resolve_tray_mapping(setup) -> dict:
    """Normalise the Stage 2 setup's tray mapping to pack doc classes.

    Accepts a ``SetupRecord``, the parsed setup-file dict, or a path
    to the setup file. Returns {pack_doc_class: TrayAssignment}.
    """
    raw_mapping: dict = {}
    if isinstance(setup, (str, Path)):
        data = json.loads(Path(setup).read_text(encoding="utf-8"))
        raw_mapping = data.get("tray_mapping", {})
    elif isinstance(setup, SetupRecord):
        raw_mapping = {
            key: {
                "document_class": a.document_class,
                "printer_name": a.printer_name,
                "tray": a.tray,
                "paper": a.paper,
                "alignment_offsets_mm": dict(a.alignment_offsets_mm),
            }
            for key, a in setup.tray_mapping.items()
        }
    elif isinstance(setup, dict):
        raw_mapping = setup.get("tray_mapping", setup)
    resolved: dict[str, TrayAssignment] = {}
    for pack_class, setup_class in _SETUP_CLASS_FOR_PACK_CLASS.items():
        raw = raw_mapping.get(setup_class) or raw_mapping.get(pack_class)
        if raw is None:
            continue
        if isinstance(raw, TrayAssignment):
            assignment = raw
        else:
            assignment = TrayAssignment(
                document_class=raw.get("document_class", setup_class),
                printer_name=raw.get("printer_name", ""),
                tray=raw.get("tray", ""),
                paper=raw.get("paper", ""),
                alignment_offsets_mm=dict(raw.get("alignment_offsets_mm", {})),
            )
        resolved[pack_class] = assignment
    return resolved
