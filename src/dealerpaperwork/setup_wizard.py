"""Setup wizard state machine (build specification, Section 5, Step 0).

First run, in order:

    0.1 Network          confirm this machine is on the store's LAN, or
                         record by name that it runs standalone with a
                         directly connected printer (decision 15,
                         "island mode": no LAN needed)
    0.2 Dealer profile   the dealer enters its own identity, title-filing
                         method, store fees, and disclosure confirmation
    0.3 Printer          pick the printer (network or USB) from the
                         provider list
    0.4 Tray / paper     assign printer + tray for standard letter
                         documents (Stage 5: after the migration to
                         digital contracting, EVERY document -- the LAW
                         contract included -- is 8.5x11 letter; there
                         is no long stock any more, decision 12)
    0.5 Preflight        load standard letter paper, run the test-print
                         action, check the test page printed the full
                         page legibly, and a named user ATTESTS it
                         (equipment is the user's responsibility --
                         locked decisions 11 and 12)
    0.6 State rules      Florida's built-in rule sheet, or the store's
                         own answers for another state (decision 22)
    0.7 Tax rates        the store's own local (Florida: county surtax)
                         rates, the state rate, and whether the store
                         leases (then the lessor and title holder)
    0.8 Math check       a manager types in one recent real deal from
                         the store's DMS; every figure must match to
                         the cent, or a named manager records why not
                         (decision 21). Same for one lease if the
                         store leases. Typed rules: two retail deals,
                         one with a trade, and no override (decision 22).
    0.9 Presets          review and approve: creates the Stage 1 Preset
                         from the entered profile/fees, with named
                         approver + timestamp

Gating: a step cannot be advanced past until its requirements validate
(each failure is reported as a plain-language error list); the wizard
cannot finish until every step is complete. ``finish()`` persists
everything to a local JSON setup file; a completed setup is what
unlocks deal entry in later stages (``is_setup_complete``).

This module is deliberately UI-independent: every rule is testable
headless, and the Tkinter window in ``setup_ui.py`` is only a driver.
Standard library only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Union

from . import math_check as mc
from .models import D, DealerProfile
from .network_check import NetworkCheckResult, check_lan
from .presets import LeasePreset, Preset, validate_preset
from .printers import PrinterProvider, TestPrintResult, get_printer_provider
from .state_rules import FLORIDA, StateRules, rules_to_dict, validate_rules

DEFAULT_SETUP_PATH = Path("config") / "setup.json"

# Title-filing methods offered in Step 0.2 (spec Step 0.2.3: the store
# files in-house through EFS, or through a private tag agency).
TITLE_FILING_EFS = "in-house EFS"
TITLE_FILING_TAG_AGENCY = "tag agency"
TITLE_FILING_METHODS = (TITLE_FILING_EFS, TITLE_FILING_TAG_AGENCY)

# Document classes the tray mapping knows about. Deal documents in
# later stages map onto one of these paper classes.
#
# DOC_LAW_CONTRACT is a RETIRED key (Stage 5, decision 12): before the
# digital-contracting migration the LAW contract printed on extremely
# long stock from its own tray, and setup required a separate
# assignment for it. Every document is letter size now, so the key
# survives only so older setup files still parse; it is no longer
# required, and the standard pack no longer uses it.
DOC_LAW_CONTRACT = "law_contract"  # retired Stage 5 -- legacy key only
DOC_STANDARD_LETTER = "standard_letter"
REQUIRED_DOCUMENT_CLASSES = (DOC_STANDARD_LETTER,)

ATTESTATION_STATEMENT = (
    "I confirm letter paper was loaded, the test print ran, and this "
    "printer prints the full page legibly. I understand having the "
    "right printing equipment is the store's responsibility."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class SetupStep(Enum):
    NETWORK = "network"
    PROFILE = "profile"
    PRINTER = "printer"
    TRAY_MAPPING = "tray_mapping"
    PREFLIGHT = "preflight"
    STATE_RULES = "state_rules"
    TAX_RATES = "tax_rates"
    MATH_CHECK = "math_check"
    APPROVE = "approve"
    DONE = "done"


STEP_ORDER: tuple[SetupStep, ...] = (
    SetupStep.NETWORK,
    SetupStep.PROFILE,
    SetupStep.PRINTER,
    SetupStep.TRAY_MAPPING,
    SetupStep.PREFLIGHT,
    SetupStep.STATE_RULES,
    SetupStep.TAX_RATES,
    SetupStep.MATH_CHECK,
    SetupStep.APPROVE,
)

# Decision 22 (Lane's card, 2026-11-20): typed rules must match the DMS
# on every figure; no manager override.
TYPED_RULES_NO_OVERRIDE = (
    "Your store typed its own tax rules, so every figure must match your "
    "DMS (no override). Fix the deal you typed or the rule sheet."
)

CHECK_LABELS = {
    "retail": "retail deal",
    "retail_trade": "retail deal with a trade",
    "lease": "lease",
}

# A county surtax rate at or above this is a typing slip (e.g. 15
# typed for 1.5%), not a rate. A sanity bound, not a legal figure.
MAX_COUNTY_SURTAX_RATE = Decimal("0.05")
# The same typing-slip bound for a store's own local rates (Decision 22).
MAX_LOCAL_RATE_TYPED = Decimal("0.10")


class SetupIncompleteError(Exception):
    """Raised by finish() when setup cannot be completed yet. The
    message lists every outstanding problem."""


@dataclass
class TrayAssignment:
    """One document class -> one printer + tray + paper stock.

    ``alignment_offsets_mm`` is reserved for Stage 4's per-printer /
    per-document alignment calibration (addition #3): the data slot
    lives here from the start so Stage 4 needs no format change.
    """

    document_class: str
    printer_name: str
    tray: str
    paper: str  # e.g. "Long contract stock" or "Letter"
    alignment_offsets_mm: dict[str, float] = field(default_factory=dict)


@dataclass
class Attestation:
    """The Step 0.5 equipment attestation. Mandatory: no name, no setup."""

    attester_name: str
    attester_role: str
    attested_at: str  # ISO-8601 timestamp
    statement: str = ATTESTATION_STATEMENT
    paper_loaded_confirmed: bool = True
    test_print_completed: bool = True


@dataclass
class SetupRecord:
    """Everything setup produced, as persisted to the setup file."""

    completed: bool
    completed_at: Optional[str]
    network: Optional[NetworkCheckResult]
    dealer: DealerProfile
    selected_printer: Optional[str]
    tray_mapping: dict[str, TrayAssignment]
    attestation: Optional[Attestation]
    preset: Preset
    single_admin_ack: Optional[dict] = None
    math_check: Optional[dict] = None


class SetupWizard:
    """The Step 0 state machine. UI-independent and fully testable."""

    def __init__(
        self,
        *,
        printer_provider: Optional[PrinterProvider] = None,
        setup_path: Union[str, Path] = DEFAULT_SETUP_PATH,
        network_checker: Callable[[], NetworkCheckResult] = check_lan,
        user_store=None,
        actor=None,
        audit_log=None,
    ) -> None:
        self.printer_provider = printer_provider or get_printer_provider()
        self.setup_path = Path(setup_path)
        self._network_checker = network_checker
        self.user_store = user_store
        self.actor = actor
        self.audit_log = audit_log
        self.single_admin_ack = None

        self.current_step: SetupStep = STEP_ORDER[0]
        self.network_result: Optional[NetworkCheckResult] = None
        self.dealer: DealerProfile = DealerProfile()  # blank until 0.2
        self.selected_printer: Optional[str] = None
        self.tray_mapping: dict[str, TrayAssignment] = {}
        self.paper_loaded_confirmed: bool = False
        self.test_print_result: Optional[TestPrintResult] = None
        self.attestation: Optional[Attestation] = None
        # Tax parameters the dealer reviews in Step 0.7. The state rate
        # is product preset data (Florida 6%); county rates are preset
        # data the dealer loads from the current-year DR-15DSS.
        self.state_tax_rate: Decimal = Decimal("0.06")
        # Step 0.6 (Decision 22): Florida's built-in rule sheet, or the
        # store's own answers. None until the store picks.
        self.rules: Optional[StateRules] = None
        self.county_surtax_rates: dict[str, Decimal] = {}
        # Step 0.7: does the store lease? None until answered.
        self.store_leases: Optional[bool] = None
        self.lease_preset: LeasePreset = LeasePreset()
        # Step 0.8: the check deals and any recorded overrides.
        self.retail_check: Optional[mc.RetailCheckEntry] = None
        self.retail_trade_check: Optional[mc.RetailCheckEntry] = None
        self.lease_check: Optional[mc.LeaseCheckEntry] = None
        self.math_overrides: dict[str, dict] = {}
        self.approved_by: Optional[str] = None
        self.record: Optional[SetupRecord] = None

    # -- data entry (called by the UI, or by tests) ----------------------

    def run_network_check(self) -> NetworkCheckResult:
        self.network_result = self._network_checker()
        return self.network_result

    def choose_standalone(self, confirmed_by: str) -> NetworkCheckResult:
        """Decision 15 (island mode): one computer with a directly
        connected printer is a complete deployment, so Step 0.1 passes
        without a store network once a named person records the choice.
        No network call is made; nothing else about setup changes."""
        name = (confirmed_by or "").strip()
        if not name:
            raise ValueError("Type your name to confirm this computer runs by itself.")
        self.network_result = NetworkCheckResult(
            ok=True,
            local_ip=None,
            message=(
                f"Standalone (island mode), confirmed by {name}: this computer "
                "runs by itself with a directly connected printer. No store "
                "network is needed (decision 15)."
            ),
        )
        return self.network_result

    def set_profile(self, dealer: DealerProfile) -> None:
        self.dealer = dealer

    def available_printers(self) -> list[str]:
        return self.printer_provider.list_printers()

    def select_printer(self, printer_name: Optional[str]) -> None:
        self.selected_printer = printer_name or None

    def set_tray_assignment(self, assignment: TrayAssignment) -> None:
        self.tray_mapping[assignment.document_class] = assignment

    def confirm_paper_loaded(self, confirmed: bool = True) -> None:
        self.paper_loaded_confirmed = confirmed

    # Stage 2-4 name, kept as an alias: it confirmed the (then long)
    # stock was loaded. Since Stage 5 all stock is letter paper.
    def confirm_stock_loaded(self, confirmed: bool = True) -> None:
        self.confirm_paper_loaded(confirmed)

    def run_test_print(self) -> TestPrintResult:
        """Invoke the provider's test-print action on the printer mapped
        to the standard letter documents (or the selected printer if
        not mapped yet). Running the action and checking the physical
        page is part of the preflight, and the attestation -- not this
        return value -- is what gates the step.
        """
        assignment = self.tray_mapping.get(DOC_STANDARD_LETTER)
        printer = (
            assignment.printer_name if assignment else self.selected_printer
        )
        if not printer:
            self.test_print_result = TestPrintResult(
                ok=False,
                printer_name="",
                message="No printer selected yet.",
            )
            return self.test_print_result
        self.test_print_result = self.printer_provider.send_test_print(printer)
        return self.test_print_result

    @property
    def test_print_completed(self) -> bool:
        return self.test_print_result is not None

    def attest(self, attester_name: str, attester_role: str) -> None:
        """Record the Step 0.5 attestation (timestamped)."""
        self.attestation = Attestation(
            attester_name=(attester_name or "").strip(),
            attester_role=(attester_role or "").strip(),
            attested_at=_now_iso(),
            paper_loaded_confirmed=self.paper_loaded_confirmed,
            test_print_completed=self.test_print_completed,
        )

    def set_state_rules(self, rules: StateRules) -> None:
        """Step 0.6: Florida's built-in sheet or the store's own answers."""
        self.rules = rules

    @property
    def typed_rules(self) -> bool:
        """True when the store typed its own rule sheet (not Florida)."""
        return self.rules is not None and not self.rules.built_in

    def set_tax_data(
        self,
        *,
        state_tax_rate: Optional[Decimal] = None,
        county_surtax_rates: Optional[dict[str, Decimal]] = None,
    ) -> None:
        if state_tax_rate is not None:
            self.state_tax_rate = D(state_tax_rate)
        if county_surtax_rates is not None:
            self.county_surtax_rates = {
                county: D(rate) for county, rate in county_surtax_rates.items()
            }

    def set_store_leases(
        self,
        leases: bool,
        *,
        lessor_name: str = "",
        title_holder: str = "",
    ) -> None:
        """Step 0.7: whether the store leases, and if so the lessor
        and title holder every lease deal needs (presets Stage 6)."""
        self.store_leases = bool(leases)
        if self.store_leases:
            self.lease_preset.lessor_name = (lessor_name or "").strip() or None
            self.lease_preset.title_holder = (title_holder or "").strip() or None

    # -- Step 0.8: the math check (decisions 21 and 22) ----------------

    def set_retail_check(
        self, entry: "mc.RetailCheckEntry", kind: str = mc.KIND_RETAIL
    ) -> "mc.MathComparison":
        if kind == mc.KIND_RETAIL_TRADE:
            self.retail_trade_check = entry
        else:
            self.retail_check = entry
        return self.compare(kind)

    def check_entry(self, kind: str):
        return {
            mc.KIND_RETAIL: self.retail_check,
            mc.KIND_RETAIL_TRADE: self.retail_trade_check,
            mc.KIND_LEASE: self.lease_check,
        }[kind]

    def set_lease_check(self, entry: "mc.LeaseCheckEntry") -> "mc.MathComparison":
        self.lease_check = entry
        return self.compare(mc.KIND_LEASE)

    def compare(self, kind: str) -> Optional["mc.MathComparison"]:
        """Run the stored check deal against the CURRENT fees and rates.
        Re-run every time, so a later fee or rate edit re-opens the check."""
        preset = self.build_preset()
        if kind in (mc.KIND_RETAIL, mc.KIND_RETAIL_TRADE):
            entry = self.check_entry(kind)
            return None if entry is None else mc.compare_retail(entry, preset)
        if kind == mc.KIND_LEASE:
            return None if self.lease_check is None else mc.compare_lease(self.lease_check, preset)
        raise ValueError(f"Unknown math check kind {kind!r}.")

    def record_math_override(self, kind: str, manager_name: str, reason: str) -> dict:
        """A named manager records why the app's figures stand although
        they differ from the DMS. Audit-logged; kept in the local setup
        file only. Tied to these exact figures: any change re-opens it."""
        if self.typed_rules:
            raise ValueError(TYPED_RULES_NO_OVERRIDE)
        name = (manager_name or "").strip()
        why = " ".join((reason or "").split())
        if not name:
            raise ValueError("Type the manager's name to record why the figures differ.")
        if len(why) < mc.MIN_OVERRIDE_REASON:
            raise ValueError(
                "Write why the figures differ in a full sentence, so the "
                "next person can follow it."
            )
        comparison = self.compare(kind)
        if comparison is None or not comparison.ran:
            raise ValueError("Run the math check first.")
        if comparison.all_match:
            raise ValueError("Every figure matches; there is nothing to record.")
        override = {
            "manager_name": name,
            "reason": why,
            "recorded_at": _now_iso(),
            "signed_in_user": getattr(self.actor, "username", None),
            "mismatches": comparison.mismatches,
            "fingerprint": comparison.fingerprint,
        }
        self.math_overrides[kind] = override
        if self.audit_log is not None:
            self.audit_log.record(
                "setup_math_check_override",
                getattr(self.actor, "username", "") or name,
                {"kind": kind, **override},
            )
        return override

    def math_check_kinds(self) -> tuple[str, ...]:
        kinds = [mc.KIND_RETAIL]
        if self.typed_rules:
            kinds.append(mc.KIND_RETAIL_TRADE)
        if self.store_leases:
            kinds.append(mc.KIND_LEASE)
        return tuple(kinds)

    def _math_check_errors(self, kind: str) -> list[str]:
        label = CHECK_LABELS[kind]
        comparison = self.compare(kind)
        if comparison is None:
            return [
                f"Enter one recent {label} from your DMS and run the math check."
            ]
        if comparison.errors:
            return [f"Math check ({label}): {e}" for e in comparison.errors]
        if self.typed_rules and kind != mc.KIND_LEASE:
            # Decision 22: one deal without a trade, one with.
            has_trade = self.check_entry(kind).trade_allowance > 0
            if kind == mc.KIND_RETAIL and has_trade:
                return [
                    "Math check (retail deal without a trade): this deal has "
                    "a trade. Use a deal with no trade here; the trade deal "
                    "goes in the second section."
                ]
            if kind == mc.KIND_RETAIL_TRADE and not has_trade:
                return [
                    "Math check (retail deal with a trade): type a deal that "
                    "has a trade-in allowance."
                ]
        if comparison.all_match:
            return []
        if self.typed_rules:
            return [
                f"Math check ({label}): {', '.join(comparison.mismatches)} "
                "differ from your DMS. " + TYPED_RULES_NO_OVERRIDE
            ]
        override = self.math_overrides.get(kind)
        if override and override.get("fingerprint") == comparison.fingerprint:
            return []
        return [
            f"Math check ({label}): {', '.join(comparison.mismatches)} "
            "differ from your DMS. Fix the entry, your fees or your county "
            "rates, or have a manager record why the figures differ."
        ]

    def build_preset(self, approved_by: Optional[str] = None) -> Preset:
        """The Preset Step 0.8 approves, assembled from setup data."""
        return Preset(
            dealer=self.dealer,
            lease=self.lease_preset if self.store_leases else None,
            rules=self.rules or FLORIDA,
            state_tax_rate=self.state_tax_rate,
            county_surtax_rates=dict(self.county_surtax_rates),
            version="setup-1",
            approved_by=approved_by if approved_by is not None else self.approved_by,
            approved_at=_now_iso() if (approved_by or self.approved_by) else None,
        )

    def approve(self, approved_by: str) -> None:
        self.approved_by = (approved_by or "").strip() or None

    # -- validation --------------------------------------------------------

    def validate_step(self, step: SetupStep) -> list[str]:
        """Plain-language problems blocking ``step``; empty = can pass."""
        if step is SetupStep.NETWORK:
            if self.network_result is None:
                return ["Run the network check first."]
            if not self.network_result.ok:
                return [self.network_result.message]
            return []

        if step is SetupStep.PROFILE:
            d = self.dealer
            errors: list[str] = []
            if not (d.legal_name or "").strip():
                errors.append("Dealer legal name is blank.")
            if not (d.street_address or "").strip():
                errors.append("Dealer street address is blank.")
            if not (d.city or "").strip():
                errors.append("Dealer city is blank.")
            if not (d.licence_number or "").strip():
                errors.append("Dealer licence number is blank.")
            if d.title_filing_method not in TITLE_FILING_METHODS:
                errors.append(
                    "Choose how the store files titles: "
                    + " or ".join(TITLE_FILING_METHODS)
                    + "."
                )
            if d.dealer_fee is None:
                errors.append(
                    "Dealer fee is blank: enter the store's dealer fee "
                    "amount (a blank fee blocks setup)."
                )
            elif d.dealer_fee < 0:
                errors.append("Dealer fee cannot be negative.")
            if d.electronic_tag_fee is None:
                errors.append(
                    "Electronic tag fee is blank: enter the store's "
                    "electronic tag fee amount."
                )
            elif d.electronic_tag_fee < 0:
                errors.append("Electronic tag fee cannot be negative.")
            return errors

        if step is SetupStep.PRINTER:
            if not self.selected_printer:
                return ["Select a printer from the list."]
            if self.selected_printer not in self.available_printers():
                return [
                    f"Selected printer {self.selected_printer!r} is not "
                    "in the list of printers on this machine."
                ]
            return []

        if step is SetupStep.TRAY_MAPPING:
            errors = []
            # Stage 5: every document -- the contract included -- is
            # standard letter, so only the letter assignment is required.
            for doc_class, label, paper_hint in (
                (DOC_STANDARD_LETTER, "standard letter documents", "letter"),
            ):
                assignment = self.tray_mapping.get(doc_class)
                if assignment is None:
                    errors.append(
                        f"No tray assigned for the {label}: assign a "
                        "printer, tray, and paper."
                    )
                    continue
                if not assignment.printer_name.strip():
                    errors.append(f"{label}: printer is blank.")
                if not assignment.tray.strip():
                    errors.append(f"{label}: tray is blank.")
                if not assignment.paper.strip():
                    errors.append(f"{label}: paper is blank.")
                elif paper_hint not in assignment.paper.lower():
                    errors.append(
                        f"{label}: paper should be the {paper_hint} "
                        f"stock (entered: {assignment.paper!r})."
                    )
            return errors

        if step is SetupStep.PREFLIGHT:
            errors = []
            if not self.paper_loaded_confirmed:
                errors.append(
                    "Load standard letter paper in the assigned tray "
                    "and confirm it here."
                )
            if not self.test_print_completed:
                errors.append(
                    "Run the test print and check the full page "
                    "printed legibly before attesting."
                )
            if self.attestation is None:
                errors.append(
                    "The equipment attestation is required: a named "
                    "user must attest the printer prints the full "
                    "page legibly on letter paper."
                )
            else:
                if not self.attestation.attester_name:
                    errors.append("Attester name is blank.")
                if not self.attestation.attester_role:
                    errors.append("Attester role is blank.")
            return errors

        if step is SetupStep.STATE_RULES:
            if self.rules is None:
                return [
                    "Pick Florida's built-in rules, or answer the questions "
                    "for your state."
                ]
            return validate_rules(self.rules)

        if step is SetupStep.TAX_RATES:
            errors = []
            rules = self.rules or FLORIDA
            taxes = rules.tax_kind != "none"
            local = taxes and rules.local_by != "none"
            if taxes and self.state_tax_rate <= 0:
                errors.append("State tax rate must be above zero.")
            if rules.local_by == "dealer" and self.county_surtax_rates and (
                (rules.dealer_place or "").strip() not in self.county_surtax_rates
            ):
                errors.append(
                    f"Your dealership's place {rules.dealer_place!r} is not "
                    "in the local rate list. Add it, spelled the same way."
                )
            if local and not self.county_surtax_rates:
                errors.append(
                    "Enter at least your own county's surtax rate from the "
                    "current-year DR-15DSS, plus every county your buyers "
                    "usually register in."
                    if not self.typed_rules else
                    "Enter at least one local rate: your own place's, plus "
                    "every place your buyers usually register in."
                )
            for county, rate in self.county_surtax_rates.items():
                if not county.strip():
                    errors.append("A county rate has no county name.")
                limit = MAX_LOCAL_RATE_TYPED if self.typed_rules else MAX_COUNTY_SURTAX_RATE
                if rate < 0 or rate >= limit:
                    errors.append(
                        f"County rate for {county!r} looks wrong "
                        f"({rate * 100}%). Type it as a percent, e.g. 1.5."
                    )
            if self.store_leases is None:
                errors.append("Answer whether the store writes leases.")
            elif self.store_leases:
                if not (self.lease_preset.lessor_name or "").strip():
                    errors.append("Lessor name is blank (the store leases).")
                if not (self.lease_preset.title_holder or "").strip():
                    errors.append("Lease title holder is blank (the store leases).")
            return errors

        if step is SetupStep.MATH_CHECK:
            errors = []
            for kind in self.math_check_kinds():
                errors.extend(self._math_check_errors(kind))
            return errors

        if step is SetupStep.APPROVE:
            errors = []
            if not self.approved_by:
                errors.append(
                    "A named approver must approve the presets "
                    "(usually a manager)."
                )
            else:
                errors.extend(
                    "Preset: " + e
                    for e in validate_preset(self.build_preset())
                )
            return errors

        return []

    def all_errors(self) -> list[str]:
        """Every outstanding problem across all steps, step-labelled."""
        problems: list[str] = []
        for step in STEP_ORDER:
            for error in self.validate_step(step):
                problems.append(f"Step {step.value}: {error}")
        return problems

    # -- navigation ---------------------------------------------------------

    def can_advance(self) -> bool:
        return not self.validate_step(self.current_step)

    def advance(self) -> list[str]:
        """Try to move to the next step. Returns the current step's
        error list; empty means the wizard advanced."""
        errors = self.validate_step(self.current_step)
        if errors:
            return errors
        idx = STEP_ORDER.index(self.current_step)
        if idx < len(STEP_ORDER) - 1:
            self.current_step = STEP_ORDER[idx + 1]
        return []

    def back(self) -> None:
        if self.current_step in STEP_ORDER:
            idx = STEP_ORDER.index(self.current_step)
            if idx > 0:
                self.current_step = STEP_ORDER[idx - 1]

    # -- completion ---------------------------------------------------------

    @property
    def needs_single_admin_ack(self) -> bool:
        if self.user_store is None:
            return False
        return sum(self.user_store.get_user(name).role == 'admin'
                   for name in self.user_store.usernames()) == 1

    def acknowledge_single_admin(self, actor) -> None:
        if not self.needs_single_admin_ack:
            return
        current = self.user_store.get_user(getattr(actor, 'username', ''))
        if current is None or current.role != 'admin':
            raise SetupIncompleteError('The signed-in admin must acknowledge the single-admin risk.')
        self.single_admin_ack = {'username': current.username,
                                 'display_name': current.display_name,
                                 'role': current.role, 'timestamp': _now_iso(),
                                 'account_id': current.account_id}
        if self.audit_log is not None:
            self.audit_log.record('single_admin_acknowledged', current.username,
                                  self.single_admin_ack)

    def finish(self) -> SetupRecord:
        """Complete setup: validate everything, build the approved
        preset, persist the setup file, and unlock deal entry."""
        problems = self.all_errors()
        if problems:
            raise SetupIncompleteError(
                "Setup is not complete: " + "; ".join(problems)
            )
        if self.needs_single_admin_ack and (
            not self.single_admin_ack or self.actor is None
            or self.single_admin_ack['account_id'] != self.actor.account_id
        ):
            raise SetupIncompleteError('Only one admin exists. Create a second admin or acknowledge the recovery risk by name.')
        preset = self.build_preset()
        self.record = SetupRecord(
            completed=True,
            completed_at=_now_iso(),
            network=self.network_result,
            dealer=self.dealer,
            selected_printer=self.selected_printer,
            tray_mapping=dict(self.tray_mapping),
            attestation=self.attestation,
            preset=preset,
            single_admin_ack=self.single_admin_ack,
            math_check=self._math_check_record(),
        )
        self.current_step = SetupStep.DONE
        self.save()
        return self.record

    def _math_check_record(self) -> dict:
        """What the setup file keeps about the math check: the check
        deals' figures, the line-by-line result and any override. Local
        only; it holds no buyer name, VIN or stock number."""
        record = {}
        for kind in self.math_check_kinds():
            entry = self.check_entry(kind)
            comparison = self.compare(kind)
            record[kind] = {
                "entry": mc.entry_to_dict(entry),
                "comparison": comparison.to_dict(),
                "result": "matched" if comparison.all_match else "manager recorded why",
                "override": None if comparison.all_match else self.math_overrides.get(kind),
            }
        return record

    def save(self, path: Optional[Union[str, Path]] = None) -> Path:
        """Persist the current record (finish() calls this for you)."""
        if self.record is None:
            raise SetupIncompleteError("Nothing to save: setup has not finished.")
        target = Path(path) if path is not None else self.setup_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(setup_record_to_dict(self.record), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return target


# ---------------------------------------------------------------------------
# Serialisation: setup file <-> objects. The embedded "preset" object is
# written in exactly the format presets.load_preset reads, so the setup
# output can be validated (and later loaded) by the Stage 1 code as-is.
# ---------------------------------------------------------------------------


def profile_to_dict(dealer: DealerProfile) -> dict:
    data = {
        "legal_name": dealer.legal_name,
        "trading_name": dealer.trading_name,
        "street_address": dealer.street_address,
        "city": dealer.city,
        "state": dealer.state,
        "zip_code": dealer.zip_code,
        "phone": dealer.phone,
        "contact_email": dealer.contact_email,
        "licence_number": dealer.licence_number,
        "franchise_info": dealer.franchise_info,
        "title_filing_method": dealer.title_filing_method,
        "disclosure_501_976_18_confirmed": dealer.disclosure_501_976_18_confirmed,
        "dealer_fee": None if dealer.dealer_fee is None else str(dealer.dealer_fee),
        "electronic_tag_fee": (
            None
            if dealer.electronic_tag_fee is None
            else str(dealer.electronic_tag_fee)
        ),
    }
    return data


def lease_preset_to_dict(lease: Optional[LeasePreset]) -> Optional[dict]:
    """In the format presets._lease_from_dict reads back."""
    if lease is None:
        return None
    return {
        "lessor_name": lease.lessor_name,
        "title_holder": lease.title_holder,
        "acquisition_fee": None if lease.acquisition_fee is None else str(lease.acquisition_fee),
        "cap_fees": None if lease.cap_fees is None else str(lease.cap_fees),
        "upfront_charges": None if lease.upfront_charges is None else str(lease.upfront_charges),
    }


def preset_to_dict(preset: Preset) -> dict:
    return {
        "dealer": profile_to_dict(preset.dealer),
        "lease": lease_preset_to_dict(preset.lease),
        "state_tax_rate": str(preset.state_tax_rate),
        "county_surtax_rates": {
            county: str(rate)
            for county, rate in preset.county_surtax_rates.items()
        },
        "state_rules": rules_to_dict(preset.rules),
        "version": preset.version,
        "approved_by": preset.approved_by,
        "approved_at": preset.approved_at,
    }


def setup_record_to_dict(record: SetupRecord) -> dict:
    return {
        "single_admin_ack": record.single_admin_ack,
        "math_check": record.math_check,
        "state": "complete" if record.completed else "incomplete",
        "completed": record.completed,
        "completed_at": record.completed_at,
        "network": None
        if record.network is None
        else {
            "ok": record.network.ok,
            "local_ip": record.network.local_ip,
            "message": record.network.message,
            "addresses_seen": list(record.network.addresses_seen),
        },
        "dealer": profile_to_dict(record.dealer),
        "selected_printer": record.selected_printer,
        "tray_mapping": {
            doc_class: {
                "document_class": a.document_class,
                "printer_name": a.printer_name,
                "tray": a.tray,
                "paper": a.paper,
                "alignment_offsets_mm": dict(a.alignment_offsets_mm),
            }
            for doc_class, a in record.tray_mapping.items()
        },
        "attestation": None
        if record.attestation is None
        else {
            "attester_name": record.attestation.attester_name,
            "attester_role": record.attestation.attester_role,
            "attested_at": record.attestation.attested_at,
            "statement": record.attestation.statement,
            "paper_loaded_confirmed": record.attestation.paper_loaded_confirmed,
            "test_print_completed": record.attestation.test_print_completed,
        },
        "preset": preset_to_dict(record.preset),
    }


def is_setup_complete(path: Union[str, Path] = DEFAULT_SETUP_PATH) -> bool:
    """Deal entry unlocks only when a completed setup file exists."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("state") == "complete" and bool(data.get("completed"))
