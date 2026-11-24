"""Named post-outage DMS reconciliation, separate from pack completion."""
from __future__ import annotations
import json
from pathlib import Path
from .audit import AuditLog
from .file_transactions import file_transaction
from .packaging_checklist import deal_record_fingerprint


def deal_fingerprint(data):
    # The exact saved deal, including certification, must still match.
    return deal_record_fingerprint(data)


class ReconciliationStore:
    def __init__(self, path=Path('data/reconciliation.json'), *, audit_log: AuditLog):
        self.path = Path(path)
        self.audit_log = audit_log
        self.read_error = ''

    def _records(self):
        with file_transaction(self.path):
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                return {}
            if not isinstance(data, dict):
                raise ValueError('Reconciliation data is invalid. Ask dealer IT to inspect it.')
            return data

    def mark(self, deal, *, actor, print_session):
        # Lock the shared ledger first, then pin the pack revision through
        # validation and persistence. Print operations never lock the ledger.
        with file_transaction(self.path), file_transaction(print_session.file_path):
            username = getattr(actor, 'username', '')
            if not username or not deal.is_certified or getattr(deal, 'is_drill', False):
                raise ValueError('Reconciliation requires a named user and a certified real deal.')
            if print_session.deal_id != deal.deal_id or not print_session.is_complete:
                raise ValueError('Finish the current paper package before marking the deal reconciled.')
            record = {'deal_id': deal.deal_id,
                      'deal_fingerprint': deal_fingerprint(deal.to_dict()),
                      'pack_fingerprint': print_session.packaging_fingerprint,
                      'pack_revision': print_session.packaging_revision}
            records = self._records()
            previous = records.get(deal.deal_id, {})
            if all(previous.get(k) == v for k, v in record.items()):
                return previous
            event = self.audit_log.record('deal_reconciled', username, record)
            records[deal.deal_id] = {**record, 'actor': username, 'timestamp': event.timestamp}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(records, indent=2), encoding='utf-8')
            temporary.replace(self.path)
            return records[deal.deal_id]

    def unreconciled(self, deals_dir=Path('data/deals'), sessions_dir=None):
        deals_dir = Path(deals_dir)
        sessions_dir = Path(sessions_dir) if sessions_dir is not None else deals_dir.parent / 'print_sessions'
        self.read_error = ''
        try:
            records = self._records()
        except (OSError, ValueError) as exc:
            records = {}
            self.read_error = f'Reconciliation ledger is unreadable; dealer IT recovery is required: {exc}'
        pending = []
        for path in sorted(deals_dir.glob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(data, dict):
                    raise ValueError('Expected a saved deal object.')
                if not isinstance(data.get('inputs', {}), dict) or not isinstance(data.get('inputs', {}).get('buyer', {}), dict):
                    raise ValueError('Saved deal inputs are malformed.')
            except (OSError, ValueError) as exc:
                pending.append({'deal_id': path.stem, 'buyer': '', 'certified': False,
                    'deal_kind': 'unknown', 'record_error': f'Unreadable deal: {path.name}: {exc}'})
                continue
            if data.get('is_drill'):
                continue
            deal_id = data.get('deal_id', path.stem)
            previous = records.get(deal_id, {})
            session_path = sessions_dir / (deal_id + '.json')
            record_error = self.read_error
            try:
                pack = json.loads(session_path.read_text(encoding='utf-8')) if session_path.exists() else {}
                if not isinstance(pack, dict):
                    raise ValueError('Expected a print-session object.')
            except (OSError, ValueError) as exc:
                pack = {}
                record_error = f'Unreadable print session: {session_path.name}: {exc}'
            matches = (previous.get('deal_fingerprint') == deal_fingerprint(data)
                and previous.get('pack_fingerprint') == pack.get('packaging_fingerprint')
                and previous.get('pack_revision') == pack.get('packaging_revision'))
            if not matches or record_error:
                pending.append({'deal_id': deal_id,
                    'buyer': data.get('inputs', {}).get('buyer', {}).get('full_name', ''),
                    'certified': bool(data.get('certification')),
                    'deal_kind': data.get('deal_kind', 'retail'), 'record_error': record_error})
        return pending
