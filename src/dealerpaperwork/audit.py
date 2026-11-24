"""Append-only audit log (Stage 3).

The cooperative model runs on named acts: who approved the presets, who
attested the printer, who certified this deal's calculations. The audit
log is the record of those acts. It is a plain JSON-lines file on the
store's own machine -- one JSON object per line, appended, never
rewritten by this code (``read_events`` only reads).

Every record carries a UTC-based timestamp, the event type, the acting
user's username, and a small details dict. Standard library only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

DEFAULT_AUDIT_PATH = Path("data") / "audit.jsonl"

# Event types. The deal + certification events are wired into
# deal_flow.DealSession; user/login events into users.UserStore.
EVENT_DEAL_CREATED = "deal_created"
EVENT_DEAL_EDITED = "deal_edited"
EVENT_DEAL_CERTIFIED = "deal_certified"
EVENT_CERTIFICATION_INVALIDATED = "certification_invalidated"
EVENT_USER_CREATED = "user_created"
EVENT_LOGIN_SUCCEEDED = "login_succeeded"
EVENT_LOGIN_FAILED = "login_failed"
# Stage 4 print-pipeline events, wired into print_pipeline.PrintSession.
EVENT_PACK_PRINT_STARTED = "pack_print_started"
EVENT_DOCUMENT_PRINTED = "document_printed"
EVENT_DOCUMENT_PRINT_FAILED = "document_print_failed"
EVENT_DOCUMENT_VOIDED = "document_voided"
EVENT_DOCUMENT_REPRINTED = "document_reprinted"
EVENT_SECURE_STOCK_PULLED = "secure_stock_pulled"
EVENT_PACK_PRINT_COMPLETED = "pack_print_completed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class AuditEvent:
    """One parsed audit-log line."""

    timestamp: str
    event_type: str
    actor: str
    details: dict = field(default_factory=dict)


class AuditLog:
    """An append-only JSON-lines audit log at an injectable path."""

    def __init__(self, path: Union[str, Path] = DEFAULT_AUDIT_PATH) -> None:
        self.path = Path(path)

    def record(
        self,
        event_type: str,
        actor: str,
        details: Optional[dict] = None,
    ) -> AuditEvent:
        """Append one event and return it. Creates the file (and its
        parent directory) on first use."""
        event = AuditEvent(
            timestamp=_now_iso(),
            event_type=event_type,
            actor=actor or "",
            details=dict(details) if details else {},
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "timestamp": event.timestamp,
                "event_type": event.event_type,
                "actor": event.actor,
                "details": event.details,
            },
            sort_keys=True,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return event

    def read_events(self) -> list[AuditEvent]:
        """All events in file order. A missing file reads as empty."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        events: list[AuditEvent] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            events.append(
                AuditEvent(
                    timestamp=raw.get("timestamp", ""),
                    event_type=raw.get("event_type", ""),
                    actor=raw.get("actor", ""),
                    details=raw.get("details") or {},
                )
            )
        return events
