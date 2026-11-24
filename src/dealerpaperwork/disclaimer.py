"""Fixed owner-supplied draft notice and per-account, per-install acceptance.

The installation id deliberately stays outside backup. Restoring user/deal
data onto another installation does not silently accept the notice there.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from .audit import AuditLog, AuditEvent

VERSION = "1"
TEXT = '''Before you use this software, read and accept this notice.

This software is an emergency tool. It prepares and prints vehicle sale and lease paperwork when your dealership management system is unavailable. It is a calculation and printing aid — nothing more.

Your dealership is solely responsible for every document this software produces. Before any document is signed or delivered, a qualified employee of your dealership must verify every figure and disclosure on it against source documents and current law, including the annual percentage rate, finance charge, amount financed, total of payments, payment schedule, sales tax and surtax, title and registration fees, and every disclosure required by the Truth in Lending Act, Regulation Z, and Florida law.

The forms this software prints on are yours. You supply them, you hold whatever licences they require, and their content and regulatory compliance are your responsibility. This software ships with no forms.

Tax rules, rates, fees, and lender requirements change. The people who set this software up at your dealership entered the values it calculates with, and your dealership is responsible for keeping them current and correct.

This software is provided "as is", without warranty of any kind, under the Apache License, Version 2.0. Nothing in this notice or in the software is legal, tax, or accounting advice. Have your own counsel and controller review your setup before you rely on it in a live deal.

By selecting I accept, you record your name, role, and the date and time against this notice in the software's audit log.'''


class DisclaimerStore:
    def __init__(self, data_root=Path('.'), *, audit_log: AuditLog,
                 version: str = VERSION):
        self.root = Path(data_root)
        self.path = self.root / 'data/disclaimer_acceptances.json'
        self.audit_log = audit_log
        self.version = version
        identity_path = self.root / 'data/installation.json'
        identity_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.installation_id = json.loads(identity_path.read_text(encoding='utf-8'))['installation_id']
        except FileNotFoundError:
            self.installation_id = secrets.token_hex(16)
            identity_path.write_text(json.dumps({'installation_id': self.installation_id}), encoding='utf-8')
        if not isinstance(self.installation_id, str) or not self.installation_id:
            raise ValueError('The local installation identifier is invalid. Ask dealer IT to inspect it.')

    def _records(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return []
        if not isinstance(data, list):
            raise ValueError('Disclaimer acceptance data is invalid. Ask dealer IT to inspect it.')
        return data

    def _match(self, record, user):
        return (record.get('installation_id') == self.installation_id
                and record.get('account_id') == user.account_id
                and record.get('username') == user.username
                and record.get('version') == self.version)

    def has_accepted(self, user) -> bool:
        return bool(user and user.account_id and any(self._match(r, user) for r in self._records()))

    def accept(self, user) -> AuditEvent:
        if not user or not user.username or not user.account_id or not user.role:
            raise ValueError('A named local account must sign in before accepting.')
        records = self._records()
        for r in records:
            if self._match(r, user):
                return AuditEvent(r['timestamp'], 'disclaimer_accepted', user.username, r)
        details = {'username': user.username, 'display_name': user.display_name,
                   'account_id': user.account_id, 'role': user.role,
                   'version': self.version, 'installation_id': self.installation_id}
        event = self.audit_log.record('disclaimer_accepted', user.username, details)
        records.append({**details, 'timestamp': event.timestamp})
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(records, indent=2), encoding='utf-8')
        temporary.replace(self.path)
        return event
