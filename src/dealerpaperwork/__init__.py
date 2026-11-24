"""Dealer Revenue Rescue (package name: dealerpaperwork).

A Windows, local-network-only backup for printing transaction paperwork
when the DMS and CRM are down. Stage 1 is the calculation core: deal
data model, Florida retail tax engine, and preset approval gating.
Stage 2 adds the first-run setup wizard (Step 0): UI-independent state
machine in setup_wizard.py, Tkinter driver in setup_ui.py, printer and
LAN checks behind printers.py / network_check.py. Stage 3 adds local
user accounts + RBAC (users.py), the append-only audit log (audit.py),
and the per-deal walk-through with worksheet, financing section, and
certification (deal_flow.py, Tkinter driver in deal_ui.py). Stage 4
adds the print pipeline: the standard retail pack (pack.py), copy sets
and alignment calibration (print_config.py), the built-in text
renderer (text_renderer.py), and the resumable, audited print session
(print_pipeline.py, Tkinter driver in print_ui.py) that prints a
certified deal's pack -- or a DRAFT-watermarked one -- through the
printer provider (printers.py, now with a Windows RAW spooler
print_document). Stage 5 makes every document letter size (the
long-stock gate is retired), and adds the dealer document library
(doc_library.py + pdf_overlay.py values overlays + docs_ui.py),
drill mode (drill.py: SAMPLE practice deals on the acceptance
fixture, recorded as runs), and one-click local backup/restore
(backup.py: zip + SHA-256 manifest, verified restore). See
docs/STAGES.md.
"""

from .audit import AuditEvent, AuditLog
from .deal_flow import (
    CertificationRecord,
    DealSession,
    DealStep,
    FinancingBreakdown,
    compute_financing,
    preset_from_setup_file,
)
from .models import (
    Buyer,
    DealInputs,
    DealResult,
    DealerProfile,
    TradeIn,
    Vehicle,
)
from .presets import Preset, UnapprovedPresetError, load_preset, validate_preset
from .tax_engine import calculate_retail_deal
from .users import PERMISSIONS, User, UserStore, has_permission

__all__ = [
    "AuditEvent",
    "AuditLog",
    "Buyer",
    "CertificationRecord",
    "DealInputs",
    "DealResult",
    "DealSession",
    "DealStep",
    "DealerProfile",
    "FinancingBreakdown",
    "PERMISSIONS",
    "Preset",
    "TradeIn",
    "UnapprovedPresetError",
    "User",
    "UserStore",
    "Vehicle",
    "calculate_retail_deal",
    "compute_financing",
    "has_permission",
    "load_preset",
    "preset_from_setup_file",
    "validate_preset",
]

__version__ = "1.0.0"
