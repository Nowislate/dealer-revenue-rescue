"""Generated packaging guidance. No contract text, forms or calculations."""
from __future__ import annotations
import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone

CHECKLIST_ID = 'paper_packaging_checklist'


def deal_record_fingerprint(data):
    payload = {key: data.get(key) for key in ('inputs', 'certification')}
    payload.update(packaging_details=data.get('packaging_details', {}),
                   is_drill=data.get('is_drill', False), deal_kind=data.get('deal_kind', 'retail'))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ChecklistRow:
    item_id: str
    section: str
    label: str
    source_doc_id: str = ''
    requires_paper: bool = False
    secure_reference: bool = False


def inputs_for(deal):
    return getattr(deal, 'inputs', getattr(deal, 'lease', deal))


def generate_checklist(deal, documents) -> list[ChecklistRow]:
    inputs = inputs_for(deal)
    vehicle = getattr(inputs, 'vehicle', None)
    is_new = bool(getattr(vehicle, 'is_new', True))
    lease = getattr(deal, 'deal_kind', '') == 'lease'
    details = getattr(deal, 'packaging_details', {})
    cash = details.get('transaction_type') == 'cash'
    trade = bool(getattr(inputs, 'trade', None) or getattr(inputs, 'trade_allowance', 0)
                 or getattr(inputs, 'trade_payoff', 0))
    docs = {d.doc_id: d for d in documents if d.doc_id != CHECKLIST_ID}
    rows = []
    used_docs = set()

    def form(item_id, title, doc_id='', section='1. Lender package', secure=False):
        source = docs.get(doc_id)
        # Built-in text summaries are not dealer-supplied legal forms.
        external = not source or source.kind != 'library_doc'
        if source:
            used_docs.add(doc_id)
        rows.append(ChecklistRow(item_id, section, title, doc_id if source else '', external, secure))

    if not cash or lease:
        form('lease_contract' if lease else 'contract',
             'Lease contract' if lease else 'Retail installment contract',
             'lease_contract' if lease else 'retail_installment_contract')
    form('buyers_order', "Buyer's order — buyer and dealer signed", 'buyers_order')
    form('title_application', 'Title application (HSMV 82040) — signed', 'title_application')
    form('odometer', 'Odometer disclosure — buyer and seller signed', 'odometer_statement')
    if not cash or lease:
        form('insurance', 'Agreement to provide insurance / binder — signed', 'insurance')
    if trade:
        form('payoff', 'Payoff authorization — signed', 'payoff_authorization')
    rebate = getattr(inputs, 'manufacturer_rebate', 0) or getattr(inputs, 'total_rebates', 0)
    if is_new and rebate:
        form('rebate', 'Rebate / incentive assignment — signed', 'rebate_assignment')
    jacket_ids = {'buyers_guide', 'privacy_notice', 'lemon_law', 'secure_poa', 'secure_reassignment'}
    for document in documents:
        if document.kind == 'library_doc' and document.doc_id not in used_docs | jacket_ids:
            rows.append(ChecklistRow('form:' + document.doc_id, '1. Lender package',
                document.title + ' — signed as required', document.doc_id))
    if lease:
        rows.append(ChecklistRow('lease_payment', '1. Lender package',
            'First payment collected as on worksheet; disposition/acquisition figures match contract exactly'))
    jacket = '2. Deal jacket only — never sent'
    if not is_new:
        form('buyers_guide', 'FTC Buyers Guide — signed copy in jacket', 'buyers_guide', jacket)
    form('privacy', 'Privacy notice delivered; delivery noted and copy in jacket', 'privacy_notice', jacket)
    if is_new:
        form('lemon_law', 'Lemon Law booklet delivered and acknowledgment signed', 'lemon_law', jacket)
    for doc_id in ('secure_poa', 'secure_reassignment'):
        if doc_id in docs:
            form(doc_id, docs[doc_id].title + ' — record secure original form number', doc_id, jacket, True)
    rows.append(ChecklistRow('buyer_copies', jacket, "Buyer's complete copy set handed over at delivery"))
    rows.extend([
        ChecklistRow('second_review', '3. Paper discipline', 'Second pair of eyes checked the signed package; record reviewer name', requires_paper=True),
        ChecklistRow('fi_signoff', 'Sign-off', 'F&I manager — print, sign, date'),
        ChecklistRow('assembler_signoff', 'Sign-off', 'Person who assembled the package — print, sign, date'),
    ])
    return rows


def pack_fingerprint(deal, documents, *, legacy_source_fields=False) -> str:
    def plain(value):
        return asdict(value) if is_dataclass(value) else value
    def library_record(document):
        if document is None:
            return None
        # Normalize integer coordinates exactly as a library reload does.
        from .doc_library import DealerDocument
        record = DealerDocument.from_dict(document.to_dict(), library_format="stage7-1").to_dict()
        if record['form_source'] == 'legacy_values_only' and not legacy_source_fields:
            # These defaults describe the old representation, not changed
            # artwork. Keep unchanged pre-image packages byte-compatible.
            for key in ('pages', 'form_source', 'image_revision'):
                record.pop(key)
        return record
    payload = {'deal': deal.to_dict() if hasattr(deal, 'to_dict') else {
        'inputs': plain(inputs_for(deal)), 'certification': plain(getattr(deal, 'certification', None))},
        'documents': [{'id': d.doc_id, 'title': d.title, 'kind': d.kind,
            'library': library_record(d.library_document)}
            for d in documents]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode('utf-8')).hexdigest()


def render_checklist(deal, documents, *, revision=1, draft=False, sample=False, reprint=False):
    inputs = inputs_for(deal)
    buyer = getattr(inputs, 'buyer', None)
    vehicle = getattr(inputs, 'vehicle', None)
    preset = getattr(deal, 'preset', None)
    cert = getattr(deal, 'certification', None)
    details = getattr(deal, 'packaging_details', {})
    lease = getattr(deal, 'deal_kind', '') == 'lease'
    lender = details.get('lender_name') or getattr(cert, 'lessor_name', '') or 'Not entered'
    lines = []
    if sample:
        lines.append('*** SAMPLE — DRILL ONLY — NOT A REAL DEAL ***')
    if draft:
        lines.append('*** DRAFT — NOT CERTIFIED ***')
    if reprint:
        lines.append('*** REPRINT — supersedes the earlier checklist ***')
    lines += ['Paper Deal Packaging Checklist', f'Checklist version: {revision}',
        f"Dealer: {getattr(getattr(preset, 'dealer', None), 'legal_name', '') or 'Not entered'}",
        f"Deal: {getattr(deal, 'deal_id', '')}",
        f"Buyer: {getattr(buyer, 'full_name', '')}  Co-buyer: {getattr(buyer, 'co_buyer_name', '') or ''}",
        f"Vehicle: {getattr(vehicle, 'year', '')} {getattr(vehicle, 'make', '')} {getattr(vehicle, 'model', '')}",
        f"VIN: {getattr(vehicle, 'vin', '')}  Stock: {getattr(vehicle, 'stock_number', '')}",
        f"Deal type: {'lease' if lease else 'retail new' if getattr(vehicle, 'is_new', True) else 'retail used'}",
        f'Lender: {lender}',
        f"Date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}",
        f"Prepared by: {getattr(getattr(deal, 'current_user', None), 'display_name', '') or getattr(deal, 'created_by', '')}",
        f"Certified by: {getattr(cert, 'certifier_name', '') or 'NOT CERTIFIED'}", '',
        'Contract: buyer/co-buyer and dealer signed and dated; initial every required page, including terms/arbitration.',
        'A generated summary is not a legal form. Supply and inspect actual dealer forms.',
        'Inspect every copy against this list; nothing leaves the store unsigned.']
    section = ''
    for row in generate_checklist(deal, documents):
        if row.section != section:
            section = row.section
            lines += ['', section]
        suffix = ' [separately supplied paper form / evidence required]' if row.requires_paper else ''
        lines.append('[ ] ' + row.label + suffix)
        if row.secure_reference:
            lines.append('    Secure original form number: ____________________')
    lines += ['', 'Paper discipline',
        'Wet ink means wet ink: no signature stamps, no copies of signatures, no white-out.',
        'Spoiled page: reprint and re-sign; destroy or void-mark and keep per store policy.',
        'Every signed figure must match the certified worksheet. If not, STOP: void, fix, recertify, reprint.',
        'A second pair of eyes checks the package before it goes.', '',
        '4. When the systems come back',
        'Enter the deal into the DMS exactly as signed — the paper is the record; the DMS catches up.',
        'Compare DMS figures to the certified worksheet line by line; manager resolves any variance before booking.',
        'Do not adjust the paper deal to match the DMS.',
        'Scan and file the paper package per store policy; file this checklist on top of the jacket.',
        '[ ] Mark reconciled in the app after comparison and filing; unresolved deals stay on the start screen.', '',
        'F&I manager (print / sign / date): __________________________________',
        'Person who assembled the package (print / sign / date): ______________', '']
    return '\r\n'.join(lines)
