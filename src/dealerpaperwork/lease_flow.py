"""Lease walk-through state machine (Stage 6).

The lease counterpart of deal_flow.DealSession, following the same
rules and reusing the same machinery (Stage 2 setup gate, Stage 3
users/RBAC, audit log, autosave):

    buyer -> vehicle -> worksheet -> review -> certified

A certified lease deal ends in state ``ready_to_print``. Until
certification the deal is a DRAFT: ``is_printable_clean`` is False
and the serialised record carries ``"watermark": "DRAFT"``.

Differences from the retail flow, all deliberate:

* The money step is ONE lease worksheet step: capitalization, cap
  cost reductions (the trade lives here, as allowance/payoff lines,
  exactly as the DMS lease worksheet carries it), residual, and
  payment. The numbers come from lease_engine.calculate_lease --
  the retail engine and its tax steps are never touched (decision
  13: lease tax is on the payment only).
* The deal runs only on an APPROVED preset whose LEASE section the
  dealer entered: lessor name and title holder are preset data
  (presets.validate_lease_preset). Nothing about any lender or
  dealer is hard-coded.
* Certification stamps the certifier and the preset identity, like
  Stage 3, plus the lease's monthly payment and due on delivery.
  Any later edit invalidates certification with an audit event.
* Lease deals autosave into the same deals directory as retail
  deals, marked ``"deal_kind": "lease"`` with their own format
  version, so backup/restore carries them unchanged.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Optional, Union

from .audit import (
    EVENT_CERTIFICATION_INVALIDATED,
    EVENT_DEAL_CERTIFIED,
    EVENT_DEAL_CREATED,
    EVENT_DEAL_EDITED,
    AuditLog,
)
from .deal_flow import (
    DEFAULT_DEALS_DIR,
    clean_products,
    packaging_details_with,
    CertificationError,
    DealFlowError,
    DealPermissionError,
    SetupRequiredError,
    preset_from_dict,
    preset_from_setup_file,
)
from .lease_engine import calculate_lease
from .models import (
    ZERO, Buyer, D, LeaseInputs, LeaseResult, Vehicle,
    buyer_from_dict, buyer_to_dict, vehicle_from_dict, vehicle_to_dict,
)
from .presets import Preset, require_approved, validate_lease_preset
from .setup_wizard import DEFAULT_SETUP_PATH, is_setup_complete
from .users import ACTION_CERTIFY_DEAL, ACTION_EDIT_DEAL, User, has_permission

# End-of-lease terms: paperwork only, None until entered (2026-11-18).
LEASE_END_TERM_FIELDS = (
    "annual_mileage_allowance",
    "excess_mileage_rate",
    "purchase_option_fee",
)


def _end_term_value(name: str, value):
    if value is None or value == "":
        return None
    if name == "annual_mileage_allowance":
        number = int(value)
    else:
        number = D(value)
    if number < 0:
        raise ValueError(f"{name.replace('_', ' ')} cannot be negative.")
    return number


# Money fields settable on the lease worksheet inputs.
LEASE_MONEY_FIELDS = (
    "msrp",
    "dealer_discount",
    "aftermarkets",
    "predelivery_fee",
    "acquisition_fee",
    "esc_premium",
    "maintenance",
    "gap_premium",
    "lah_iui",
    "prior_lease_balance",
    "license_fee",
    "dealer_fees",
    "cap_fees",
    "cap_taxes",
    "cash_down",
    "total_rebates",
    "trade_allowance",
    "trade_payoff",
    "amount_applied_upfront",
    "cash_back",
    "residual_base_msrp",
    "option_package_discount",
    "hard_adds",
    "mileage_adjustment",
    "upfront_charges",
    "security_deposit",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class LeasePresetError(DealFlowError):
    """The approved preset set has no usable lease section."""


class LeaseStep(Enum):
    BUYER = "buyer"
    VEHICLE = "vehicle"
    WORKSHEET = "worksheet"
    REVIEW = "review"
    CERTIFIED = "certified"


LEASE_STEP_ORDER: tuple[LeaseStep, ...] = (
    LeaseStep.BUYER,
    LeaseStep.VEHICLE,
    LeaseStep.WORKSHEET,
    LeaseStep.REVIEW,
)


@dataclass
class LeaseCertificationRecord:
    """Who certified this lease, when, on which approved preset, and
    the lease totals the certification covers."""

    certifier_username: str
    certifier_name: str
    certifier_role: str
    certified_at: str
    preset_version: str
    preset_approved_by: Optional[str]
    preset_approved_at: Optional[str]
    lessor_name: Optional[str]
    monthly_payment: Decimal
    monthly_tax: Decimal
    due_on_delivery: Decimal


class LeaseDealSession:
    """One lease deal, walked from buyer entry to certification.

    Construct directly when you already hold an approved ``Preset``
    with a lease section (tests, development), or with :meth:`start`
    to load the preset from a completed Stage 2 setup file. Either
    way the constructor enforces: setup complete, preset approved,
    lease section entered, acting user may edit deals.
    """

    deal_kind = "lease"

    def __init__(
        self,
        *,
        user: User,
        preset: Union[Preset, dict],
        setup_path: Union[str, Path] = DEFAULT_SETUP_PATH,
        deals_dir: Union[str, Path] = DEFAULT_DEALS_DIR,
        audit_log: Optional[AuditLog] = None,
        deal_id: Optional[str] = None,
    ) -> None:
        if not is_setup_complete(setup_path):
            raise SetupRequiredError(
                "Setup is not complete, so deal entry is locked. Run "
                "the first-time setup wizard (Stage 2) before starting "
                "a deal."
            )
        if isinstance(preset, dict):
            preset = preset_from_dict(preset)
        require_approved(preset)  # raises UnapprovedPresetError
        lease_errors = validate_lease_preset(preset)
        if lease_errors:
            raise LeasePresetError(
                "Lease deals cannot run on this preset set: "
                + "; ".join(lease_errors)
            )
        if not has_permission(user, ACTION_EDIT_DEAL):
            raise DealPermissionError(
                f"{user.display_name} ({user.role}) does not have the "
                "edit-deal permission."
            )
        self._init_state(
            user=user,
            preset=preset,
            deals_dir=deals_dir,
            audit_log=audit_log,
            deal_id=deal_id or uuid.uuid4().hex[:12],
        )
        # Preset lease defaults are the deal's starting values; the
        # user can edit them per deal during entry.
        lease_preset = preset.lease
        assert lease_preset is not None  # validated above
        if lease_preset.acquisition_fee is not None:
            self.lease.acquisition_fee = lease_preset.acquisition_fee
        if lease_preset.cap_fees is not None:
            self.lease.cap_fees = lease_preset.cap_fees
        if lease_preset.upfront_charges is not None:
            self.lease.upfront_charges = lease_preset.upfront_charges
        if preset.dealer.dealer_fee is not None:
            self.lease.predelivery_fee = preset.dealer.dealer_fee
        self._audit(EVENT_DEAL_CREATED, {"deal_id": self.deal_id, "deal_kind": "lease"})
        self.save()

    def _init_state(
        self,
        *,
        user: User,
        preset: Preset,
        deals_dir: Union[str, Path],
        audit_log: Optional[AuditLog],
        deal_id: str,
    ) -> None:
        self.deal_id = deal_id
        self.is_drill = False  # drills are a retail-flow feature (Stage 5)
        self.preset = preset
        self.deals_dir = Path(deals_dir)
        self.audit_log = audit_log
        self.current_user = user
        self.created_by = user.username
        self.created_at = _now_iso()
        self.current_step: LeaseStep = LeaseStep.BUYER
        self.lease = LeaseInputs()
        self.worksheet_result: Optional[LeaseResult] = None
        self._worksheet_fingerprint: Optional[str] = None
        self.certification: Optional[LeaseCertificationRecord] = None

    @property
    def inputs(self) -> LeaseInputs:
        """The deal's inputs (buyer/vehicle ride on the lease inputs,
        so pack/library helpers that read ``deal.inputs`` work)."""
        return self.lease

    @classmethod
    def start(
        cls,
        user: User,
        *,
        setup_path: Union[str, Path] = DEFAULT_SETUP_PATH,
        deals_dir: Union[str, Path] = DEFAULT_DEALS_DIR,
        audit_log: Optional[AuditLog] = None,
    ) -> "LeaseDealSession":
        """Start a lease deal on the preset approved in the setup file."""
        if not is_setup_complete(setup_path):
            raise SetupRequiredError(
                "Setup is not complete, so deal entry is locked. Run "
                "the first-time setup wizard (Stage 2) before starting "
                "a deal."
            )
        return cls(
            user=user,
            preset=preset_from_setup_file(setup_path),
            setup_path=setup_path,
            deals_dir=deals_dir,
            audit_log=audit_log,
        )

    @classmethod
    def load(
        cls,
        deal_id: str,
        *,
        user: User,
        preset: Union[Preset, dict],
        deals_dir: Union[str, Path] = DEFAULT_DEALS_DIR,
        audit_log: Optional[AuditLog] = None,
    ) -> "LeaseDealSession":
        """Restore an autosaved lease session exactly, certification
        included. Loading does not re-check setup, but the acting
        user still needs the edit-deal permission."""
        if isinstance(preset, dict):
            preset = preset_from_dict(preset)
        if not has_permission(user, ACTION_EDIT_DEAL):
            raise DealPermissionError(
                f"{user.display_name} ({user.role}) does not have the "
                "edit-deal permission."
            )
        path = Path(deals_dir) / f"{deal_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            raise DealFlowError(f"No saved deal {deal_id!r} at {path}.") from None
        if data.get("deal_kind") != "lease":
            raise DealFlowError(
                f"Saved deal {deal_id!r} is not a lease deal; open it "
                "with the retail deal flow."
            )
        session = cls.__new__(cls)
        session._init_state(
            user=user,
            preset=preset,
            deals_dir=deals_dir,
            audit_log=audit_log,
            deal_id=data["deal_id"],
        )
        session._restore(data)
        return session

    # -- state views --------------------------------------------------------

    @property
    def is_certified(self) -> bool:
        return self.certification is not None

    @property
    def state(self) -> str:
        if self.is_certified:
            return "ready_to_print"
        return self.current_step.value

    @property
    def is_printable_clean(self) -> bool:
        """True only for a certified deal (spec Section 8)."""
        return self.is_certified

    @property
    def watermark(self) -> Optional[str]:
        return None if self.is_certified else "DRAFT"

    @property
    def worksheet_is_current(self) -> bool:
        return (
            self.worksheet_result is not None
            and self._worksheet_fingerprint == self._fingerprint()
        )

    # -- input entry (every setter audits, invalidates, autosaves) ----------

    def set_packaging_details(self, *, transaction_type='lease', lender_name: str,
                              lender_address: str = '', **lender_parts: str) -> None:
        if transaction_type != 'lease':
            raise ValueError('Lease packaging must remain a lease.')
        old = getattr(self, 'packaging_details', {})
        details = packaging_details_with(
            old, {'transaction_type': 'lease', 'lender_name': lender_name.strip()},
            lender_address=lender_address, **lender_parts)
        if details != old:
            self.packaging_details = details
            self._after_edit('packaging')

    def set_products(self, products) -> None:
        """Record the F&I products this lease sold; each one's own form
        prints in the pack. (A lease worksheet has no products line, so
        there is no total to match.)"""
        cleaned = clean_products(products)
        details = dict(getattr(self, 'packaging_details', {}))
        if cleaned:
            details['products'] = cleaned
        else:
            details.pop('products', None)
        if details != getattr(self, 'packaging_details', {}):
            self.packaging_details = details
            self._after_edit('packaging')

    def set_buyer(self, buyer: Buyer) -> None:
        self.lease.buyer = buyer
        self._after_edit("buyer")

    def set_vehicle(self, vehicle: Vehicle) -> None:
        self.lease.vehicle = vehicle
        self._after_edit("vehicle")

    def set_lease(self, **values) -> None:
        """Set lease worksheet values, e.g. set_lease(msrp="30000").

        Money fields (see LEASE_MONEY_FIELDS) are coerced to Decimal;
        ``money_factor`` and ``residual_percent`` are Decimals too;
        ``term_months``, ``number_of_advance_payments`` are ints and
        ``mrm`` is a Decimal or None.
        """
        money = set(LEASE_MONEY_FIELDS)
        decimal_fields = money | {"money_factor", "residual_percent", "mrm"}
        int_fields = {"term_months", "number_of_advance_payments"}
        optional_fields = set(LEASE_END_TERM_FIELDS)
        allowed = decimal_fields | int_fields | optional_fields
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(
                f"Unknown lease field(s): {', '.join(sorted(unknown))}. "
                f"Allowed: {', '.join(sorted(allowed))}."
            )
        for name, value in values.items():
            if name in optional_fields:
                setattr(self.lease, name, _end_term_value(name, value))
            elif name in int_fields:
                setattr(self.lease, name, int(value))
            elif value is None:
                setattr(self.lease, name, None)
            else:
                setattr(self.lease, name, D(value))
        if values:
            self._after_edit("lease")

    def _after_edit(self, kind: str) -> None:
        self._audit(
            EVENT_DEAL_EDITED,
            {"deal_id": self.deal_id, "deal_kind": "lease", "section": kind},
        )
        if self.certification is not None:
            certifier = self.certification.certifier_username
            self.certification = None
            self.current_step = LeaseStep.WORKSHEET
            self._audit(
                EVENT_CERTIFICATION_INVALIDATED,
                {
                    "deal_id": self.deal_id,
                    "deal_kind": "lease",
                    "section": kind,
                    "previously_certified_by": certifier,
                    "invalidated_by": self.current_user.username,
                },
            )
        self.save()

    # -- step validation and navigation ---------------------------------------

    def validate_step(self, step: LeaseStep) -> list[str]:
        """Plain-language problems blocking ``step``; empty = can pass."""
        if step is LeaseStep.BUYER:
            errors = []
            buyer = self.lease.buyer
            if not buyer.full_name.strip():
                errors.append("Buyer name is blank.")
            needs_place = self.preset is None or self.preset.needs_buyer_place
            if needs_place and not buyer.residence_county.strip():
                errors.append(
                    "Buyer residence county is blank: the county surtax "
                    "rate comes from that county's approved rate."
                )
            return errors

        if step is LeaseStep.VEHICLE:
            errors = []
            vehicle = self.lease.vehicle
            if vehicle.year is None:
                errors.append("Vehicle year is blank.")
            if not vehicle.make.strip():
                errors.append("Vehicle make is blank.")
            if not vehicle.model.strip():
                errors.append("Vehicle model is blank.")
            if not vehicle.vin.strip():
                errors.append("Vehicle VIN is blank.")
            return errors

        if step is LeaseStep.WORKSHEET:
            errors = []
            lease = self.lease
            if lease.msrp <= ZERO:
                errors.append("MSRP is blank: the lease starts from the MSRP.")
            if not lease.term_months or lease.term_months <= 0:
                errors.append("Lease term (months) must be a positive number.")
            if lease.money_factor < ZERO:
                errors.append("Money factor cannot be negative.")
            if not (ZERO <= lease.residual_percent <= Decimal("1")):
                errors.append("Residual percent must be between 0 and 1.")
            if lease.number_of_advance_payments < 0:
                errors.append("Number of advance payments cannot be negative.")
            if not self.worksheet_is_current:
                errors.append("Run the lease worksheet calculation first.")
            return errors

        if step is LeaseStep.REVIEW:
            return self._certification_blockers(check_permission=False)

        return []

    def advance(self) -> list[str]:
        """Try to move to the next step; returns the blocking errors
        (empty = advanced). Moving off the worksheet computes it
        first; the review step ends only via :meth:`certify`."""
        if self.current_step is LeaseStep.WORKSHEET:
            try:
                self.compute_worksheet()
            except DealFlowError as exc:
                return [str(exc)]
        if self.current_step is LeaseStep.REVIEW:
            return [
                "This lease is ready to certify. Certification is a "
                "separate, named act -- use Certify to finish."
            ]
        if self.current_step is LeaseStep.CERTIFIED:
            return []
        errors = self.validate_step(self.current_step)
        if errors:
            return errors
        idx = LEASE_STEP_ORDER.index(self.current_step)
        if idx < len(LEASE_STEP_ORDER) - 1:
            self.current_step = LEASE_STEP_ORDER[idx + 1]
        self.save()
        return []

    def back(self) -> None:
        if self.current_step in LEASE_STEP_ORDER:
            idx = LEASE_STEP_ORDER.index(self.current_step)
            if idx > 0:
                self.current_step = LEASE_STEP_ORDER[idx - 1]
                self.save()

    # -- worksheet ----------------------------------------------------------------

    def _fingerprint(self) -> str:
        return json.dumps(
            _lease_to_dict(self.lease), sort_keys=True, default=str
        )

    def compute_worksheet(self) -> LeaseResult:
        """Run the lease engine on the current inputs."""
        county = self.lease.buyer.residence_county.strip()
        try:
            # Decision 22: the rule sheet says whose place picks the rate.
            self.lease.county_surtax_rate = self.preset.local_rate_for(county)
        except KeyError as exc:
            raise DealFlowError(str(exc.args[0])) from None
        try:
            self.worksheet_result = calculate_lease(
                self.lease, preset=self.preset, certify=False
            )
        except ValueError as exc:
            raise DealFlowError(str(exc)) from None
        self._worksheet_fingerprint = self._fingerprint()
        self.save()
        return self.worksheet_result

    # -- certification --------------------------------------------------------------

    def _certification_blockers(self, *, check_permission: bool = True) -> list[str]:
        problems: list[str] = []
        for step in (LeaseStep.BUYER, LeaseStep.VEHICLE):
            for error in self.validate_step(step):
                problems.append(f"Step {step.value}: {error}")
        for error in self.validate_step(LeaseStep.WORKSHEET):
            if "Run the lease worksheet" not in error:
                problems.append(f"Step worksheet: {error}")
        if not self.worksheet_is_current:
            problems.append(
                "The lease worksheet has not been calculated from the "
                "current inputs."
            )
        if check_permission and not has_permission(
            self.current_user, ACTION_CERTIFY_DEAL
        ):
            problems.append(
                f"{self.current_user.display_name} ({self.current_user.role}) "
                "does not have the certify-deal permission."
            )
        return problems

    def certify(self, user: Optional[User] = None) -> LeaseCertificationRecord:
        """Certify this lease's calculations (spec Section 8).

        ``user`` defaults to the session's current user. Only a user
        with the certify-deal permission can certify (sales cannot).
        On success the deal is stamped with the certifier, the preset
        identity, and the lease totals, moves to ``ready_to_print``,
        and a ``deal_certified`` audit event is written.
        """
        actor = user or self.current_user
        self.current_user = actor
        if not has_permission(actor, ACTION_CERTIFY_DEAL):
            raise DealPermissionError(
                f"{actor.display_name} ({actor.role}) cannot certify "
                "deals. Certification requires the certify-deal "
                "permission (admin, manager, or F&I)."
            )
        for step in (LeaseStep.BUYER, LeaseStep.VEHICLE):
            errors = self.validate_step(step)
            if errors:
                raise CertificationError(
                    f"Step {step.value} is incomplete: " + "; ".join(errors)
                )
        # Certification always runs on a worksheet computed from the
        # inputs as they stand now.
        self.compute_worksheet()
        result = self.worksheet_result
        assert result is not None
        lease_preset = self.preset.lease
        self.certification = LeaseCertificationRecord(
            certifier_username=actor.username,
            certifier_name=actor.display_name,
            certifier_role=actor.role,
            certified_at=_now_iso(),
            preset_version=self.preset.version,
            preset_approved_by=self.preset.approved_by,
            preset_approved_at=self.preset.approved_at,
            lessor_name=lease_preset.lessor_name if lease_preset else None,
            monthly_payment=result.monthly_payment,
            monthly_tax=result.monthly_tax,
            due_on_delivery=result.due_on_delivery,
        )
        self.current_step = LeaseStep.CERTIFIED
        self._audit(
            EVENT_DEAL_CERTIFIED,
            {
                "deal_id": self.deal_id,
                "deal_kind": "lease",
                "certifier_role": actor.role,
                "preset_version": self.preset.version,
                "monthly_payment": str(result.monthly_payment),
                "monthly_tax": str(result.monthly_tax),
                "due_on_delivery": str(result.due_on_delivery),
            },
        )
        self.save()
        return self.certification

    # -- audit + persistence -----------------------------------------------------------

    def _audit(self, event_type: str, details: dict) -> None:
        if self.audit_log is not None:
            self.audit_log.record(event_type, self.current_user.username, details)

    @property
    def file_path(self) -> Path:
        return self.deals_dir / f"{self.deal_id}.json"

    def save(self, path: Optional[Union[str, Path]] = None) -> Path:
        """Autosave: write this session as one JSON file per deal."""
        target = Path(path) if path is not None else self.file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return target

    def to_dict(self) -> dict:
        return {
            "deal_id": self.deal_id,
            "format_version": "stage6-1",
            "packaging_details": getattr(self, 'packaging_details', {}),
            "deal_kind": "lease",
            "is_drill": False,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "current_step": self.current_step.value,
            "state": self.state,
            "watermark": self.watermark,
            "inputs": _lease_to_dict(self.lease),
            "worksheet_fingerprint": self._worksheet_fingerprint,
            "worksheet_result": (
                None
                if self.worksheet_result is None
                else _lease_result_to_dict(self.worksheet_result)
            ),
            "certification": (
                None
                if self.certification is None
                else _lease_certification_to_dict(self.certification)
            ),
        }

    def _restore(self, data: dict) -> None:
        self.packaging_details = dict(data.get('packaging_details', {}))
        self.created_by = data.get("created_by", self.created_by)
        self.created_at = data.get("created_at", self.created_at)
        self.lease = _lease_from_dict(data.get("inputs", {}))
        step_value = data.get("current_step", LeaseStep.BUYER.value)
        self.current_step = LeaseStep(step_value)
        self._worksheet_fingerprint = data.get("worksheet_fingerprint")
        if data.get("worksheet_result") is not None:
            self.worksheet_result = _lease_result_from_dict(data["worksheet_result"])
        if data.get("certification") is not None:
            self.certification = _lease_certification_from_dict(data["certification"])
            self.current_step = LeaseStep.CERTIFIED


# ---------------------------------------------------------------------------
# Serialisation helpers. Money crosses as strings, so a saved lease
# reloads to the exact cent. Rates (money factor, percents) cross as
# strings too -- never floats.
# ---------------------------------------------------------------------------


def _money(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else str(value)


def _lease_to_dict(lease: LeaseInputs) -> dict:
    buyer = lease.buyer
    vehicle = lease.vehicle
    data = {
        "buyer": buyer_to_dict(buyer),
        "vehicle": vehicle_to_dict(vehicle),
        "term_months": lease.term_months,
        "money_factor": str(lease.money_factor),
        "residual_percent": str(lease.residual_percent),
        "mrm": _money(lease.mrm),
        "county_surtax_rate": str(lease.county_surtax_rate),
        "number_of_advance_payments": lease.number_of_advance_payments,
    }
    for name in LEASE_MONEY_FIELDS:
        data[name] = str(getattr(lease, name))
    # Written only when entered, so older saved leases keep their bytes.
    for name in LEASE_END_TERM_FIELDS:
        value = getattr(lease, name)
        if value is not None:
            data[name] = value if isinstance(value, int) else str(value)
    return data


def _lease_from_dict(data: dict) -> LeaseInputs:
    buyer_data = data.get("buyer", {})
    vehicle_data = data.get("vehicle", {})
    lease = LeaseInputs(
        buyer=buyer_from_dict(buyer_data),
        vehicle=vehicle_from_dict(vehicle_data),
    )
    for name in LEASE_MONEY_FIELDS:
        if data.get(name) is not None:
            setattr(lease, name, D(data[name]))
    lease.term_months = data.get("term_months")
    if data.get("money_factor") is not None:
        lease.money_factor = D(data["money_factor"])
    if data.get("residual_percent") is not None:
        lease.residual_percent = D(data["residual_percent"])
    if data.get("mrm") is not None:
        lease.mrm = D(data["mrm"])
    if data.get("county_surtax_rate") is not None:
        lease.county_surtax_rate = D(data["county_surtax_rate"])
    lease.number_of_advance_payments = int(data.get("number_of_advance_payments", 1))
    for name in LEASE_END_TERM_FIELDS:
        if data.get(name) is not None:
            setattr(lease, name, _end_term_value(name, data[name]))
    return lease


def _lease_result_to_dict(result: LeaseResult) -> dict:
    data = {
        "combined_tax_rate": str(result.combined_tax_rate),
        "lines": [[label, str(amount)] for label, amount in result.lines],
        "warnings": list(result.warnings),
    }
    for name in (
        "selling_price",
        "gross_cap_cost",
        "net_trade",
        "total_cap_reduction",
        "net_cap_cost",
        "adjusted_msrp",
        "residual_base",
        "residual_amount",
        "adjusted_residual",
        "monthly_depreciation",
        "monthly_rent_charge",
        "base_monthly_payment",
        "monthly_tax",
        "monthly_payment",
        "advanced_payment",
        "upfront_charges",
        "security_deposit",
        "due_on_delivery",
    ):
        data[name] = str(getattr(result, name))
    # Decision 22: only a sheet that taxes up front writes this key, so a
    # Florida lease's saved record and its fingerprint stay as before.
    if result.upfront_tax:
        data["upfront_tax"] = str(result.upfront_tax)
    return data


def _lease_result_from_dict(data: dict) -> LeaseResult:
    kwargs = {
        name: D(data[name])
        for name in (
            "selling_price",
            "gross_cap_cost",
            "net_trade",
            "total_cap_reduction",
            "net_cap_cost",
            "adjusted_msrp",
            "residual_base",
            "residual_amount",
            "adjusted_residual",
            "monthly_depreciation",
            "monthly_rent_charge",
            "base_monthly_payment",
            "combined_tax_rate",
            "monthly_tax",
            "monthly_payment",
            "advanced_payment",
            "upfront_charges",
            "security_deposit",
            "due_on_delivery",
        )
    }
    return LeaseResult(
        **kwargs,
        upfront_tax=D(data.get("upfront_tax", "0")),
        lines=tuple((label, D(amount)) for label, amount in data.get("lines", [])),
        warnings=tuple(data.get("warnings", [])),
    )


def _lease_certification_to_dict(record: LeaseCertificationRecord) -> dict:
    return {
        "certifier_username": record.certifier_username,
        "certifier_name": record.certifier_name,
        "certifier_role": record.certifier_role,
        "certified_at": record.certified_at,
        "preset_version": record.preset_version,
        "preset_approved_by": record.preset_approved_by,
        "preset_approved_at": record.preset_approved_at,
        "lessor_name": record.lessor_name,
        "monthly_payment": str(record.monthly_payment),
        "monthly_tax": str(record.monthly_tax),
        "due_on_delivery": str(record.due_on_delivery),
    }


def _lease_certification_from_dict(data: dict) -> LeaseCertificationRecord:
    return LeaseCertificationRecord(
        certifier_username=data["certifier_username"],
        certifier_name=data["certifier_name"],
        certifier_role=data["certifier_role"],
        certified_at=data["certified_at"],
        preset_version=data["preset_version"],
        preset_approved_by=data.get("preset_approved_by"),
        preset_approved_at=data.get("preset_approved_at"),
        lessor_name=data.get("lessor_name"),
        monthly_payment=D(data["monthly_payment"]),
        monthly_tax=D(data["monthly_tax"]),
        due_on_delivery=D(data["due_on_delivery"]),
    )
