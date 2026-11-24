"""Deal walk-through state machine (Stage 3).

The per-deal flow from the build specification, as a UI-independent
state machine (the Tkinter driver is ``deal_ui.py``):

    buyer -> vehicle -> trade -> worksheet -> review -> certified

A certified deal ends in state ``ready_to_print``; printing itself is
Stage 4. Until certification the deal is a DRAFT: it can be saved and
resumed, but ``is_printable_clean`` is False and the serialised record
carries ``"watermark": "DRAFT"``.

Rules enforced here (not in the UI, so they cannot be clicked past):

* Setup must be complete (Stage 2 ``is_setup_complete``) before a deal
  can start, and the deal runs only on an APPROVED preset
  (``presets.require_approved``).
* Creating/editing requires an authenticated user with the ``edit_deal``
  permission; certification additionally requires ``certify_deal``
  (see users.PERMISSIONS -- sales can edit but can never certify).
* The worksheet tax numbers come from the Stage 1 engine, untouched.
  The FINANCING section is computed here, separately, on top of the
  engine result (see ``compute_financing``).
* If the engine flags the sub-$5,000-base surtax warning (spec Section
  16), certification is blocked until the certifying user personally
  acknowledges the warning; the acknowledgment is recorded.
* Certification stamps the deal with the certifier's name/role/
  timestamp and the preset identity used (version + approver +
  approval time -- addition #8). ANY later edit to buyer, vehicle,
  trade, or money inputs invalidates the certification, returns the
  deal to the worksheet step, clears the certifier fields, and writes
  a ``certification_invalidated`` audit event.
* Autosave: the session is written to the deals directory after every
  change, one JSON file per deal; ``DealSession.load`` restores a
  session exactly, certification state included.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
from .models import (
    ZERO, Buyer, D, DealInputs, DealResult, TradeIn, Vehicle,
    buyer_from_dict, buyer_to_dict, vehicle_from_dict, vehicle_to_dict,
)
from .presets import Preset, require_approved
from .state_rules import rules_from_dict
from .presets import _lease_from_dict as _preset_lease_from_dict
from .presets import _profile_from_dict as _preset_profile_from_dict
from .setup_wizard import DEFAULT_SETUP_PATH, is_setup_complete
from .tax_engine import calculate_retail_deal, cents, installment_terms
from .users import ACTION_CERTIFY_DEAL, ACTION_EDIT_DEAL, User, has_permission

DEFAULT_DEALS_DIR = Path("data") / "deals"

# Money fields a deal entry may set, beyond buyer/vehicle/trade.
MONEY_FIELDS = (
    "selling_price",
    "accessories",
    "dealer_fee",
    "other_taxable_fees",
    "dealer_discount",
    "government_fees",
    "optional_products",
    "electronic_tag_fee",
    "manufacturer_rebate",
    "cash_down_payment",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class DealFlowError(Exception):
    """Base class for deal-flow failures."""


class SetupRequiredError(DealFlowError):
    """A deal cannot start until the Stage 2 setup wizard is complete."""


class DealPermissionError(DealFlowError):
    """The acting user lacks the permission the action needs."""


class CertificationError(DealFlowError):
    """The deal is not in a certifiable state. Message lists why."""


class DealStep(Enum):
    BUYER = "buyer"
    VEHICLE = "vehicle"
    TRADE = "trade"
    WORKSHEET = "worksheet"
    REVIEW = "review"
    CERTIFIED = "certified"


STEP_ORDER: tuple[DealStep, ...] = (
    DealStep.BUYER,
    DealStep.VEHICLE,
    DealStep.TRADE,
    DealStep.WORKSHEET,
    DealStep.REVIEW,
)


@dataclass
class FinancingBreakdown:
    """The financing section of the worksheet, computed by the flow.

    This is deliberately NOT part of the Stage 1 tax engine. The
    engine's amount-due list (its step 12) does not net the trade lien
    payoff; real amount-financed math does. Stage 3 resolves the
    Stage 1 checkpoint flag in the standard way -- the payoff nets
    into amount financed HERE, in the financing section, and the
    engine's tax semantics are untouched:

        net trade equity = trade allowance - trade payoff
        amount financed  = (selling price + accessories + dealer
                           fee + other taxable fees - dealer
                           discount + total tax + electronic tag
                           fee + other non-tax fees)
                           - manufacturer rebate
                           - cash down payment
                           - net trade equity

    Negative equity (payoff above allowance) makes the last term
    negative, which correctly RAISES the amount financed.
    ``other_non_tax_fees`` is the deal's government fees plus optional
    products -- charges that sit outside the tax lines. The electronic
    tag fee is different: it is a taxed dealer charge (Decision 14),
    so its face value enters the subtotal once, here, and its tax
    arrives inside the engine's total tax.
    """

    selling_price: Decimal
    total_tax: Decimal
    electronic_tag_fee: Decimal
    other_non_tax_fees: Decimal
    subtotal_before_credits: Decimal
    manufacturer_rebate: Decimal
    cash_down_payment: Decimal
    net_trade_equity: Decimal
    amount_financed: Decimal
    lines: tuple[tuple[str, Decimal], ...] = ()
    # Truth in Lending figures on THIS amount financed (payoff netted),
    # present only when the deal has a term and a rate.
    monthly_payment: Optional[Decimal] = None
    total_of_payments: Optional[Decimal] = None
    finance_charge: Optional[Decimal] = None
    # Accessories + dealer fee + other taxable fees - dealer discount.
    price_additions: Decimal = ZERO


def compute_financing(inputs: DealInputs, result: DealResult) -> FinancingBreakdown:
    """Amount financed for a deal, from the inputs and the engine result."""
    trade_allowance = inputs.trade.allowance if inputs.trade else ZERO
    trade_payoff = inputs.trade.payoff if inputs.trade else ZERO
    net_equity = cents(trade_allowance - trade_payoff)
    other_non_tax = cents(inputs.government_fees + inputs.optional_products)
    # What the buyer pays on top of the vehicle price before tax: the
    # same lines the engine puts in the gross price (accessories, the
    # dealer fee, other taxable fees), less the dealer's discount. They
    # are taxed AND owed; leaving them out financed too little.
    price_additions = cents(
        inputs.accessories
        + inputs.dealer_fee
        + inputs.other_taxable_fees
        - inputs.dealer_discount
    )
    subtotal = cents(
        inputs.selling_price
        + price_additions
        + result.total_tax
        + inputs.electronic_tag_fee
        + other_non_tax
    )
    amount_financed = cents(
        subtotal
        - inputs.manufacturer_rebate
        - inputs.cash_down_payment
        - net_equity
    )
    lines = (
        ("Selling price", cents(inputs.selling_price)),
        ("Accessories + dealer fees - discount", price_additions),
        ("Total sales tax", result.total_tax),
        ("Electronic tag fee", cents(inputs.electronic_tag_fee)),
        ("Other non-tax fees (government + optional)", other_non_tax),
        ("Subtotal before credits", subtotal),
        ("Manufacturer rebate", cents(-inputs.manufacturer_rebate)),
        ("Cash down payment", cents(-inputs.cash_down_payment)),
        ("Net trade equity (allowance - payoff)", cents(-net_equity)),
        ("Amount financed", amount_financed),
    )
    payment = total = charge = None
    if inputs.term_months and inputs.annual_rate is not None and amount_financed > ZERO:
        payment, total, charge = installment_terms(
            amount_financed, inputs.annual_rate, inputs.term_months,
            inputs.days_to_first_payment,
        )
        lines += (
            ("Monthly payment", payment),
            ("Total of payments", total),
            ("Finance charge", charge),
        )
    return FinancingBreakdown(
        selling_price=cents(inputs.selling_price),
        total_tax=result.total_tax,
        electronic_tag_fee=cents(inputs.electronic_tag_fee),
        other_non_tax_fees=other_non_tax,
        subtotal_before_credits=subtotal,
        manufacturer_rebate=cents(inputs.manufacturer_rebate),
        cash_down_payment=cents(inputs.cash_down_payment),
        net_trade_equity=net_equity,
        amount_financed=amount_financed,
        lines=lines,
        monthly_payment=payment,
        total_of_payments=total,
        finance_charge=charge,
        price_additions=price_additions,
    )


@dataclass
class CertificationRecord:
    """Who certified this deal, when, and on which approved preset."""

    certifier_username: str
    certifier_name: str
    certifier_role: str
    certified_at: str
    preset_version: str
    preset_approved_by: Optional[str]
    preset_approved_at: Optional[str]
    total_tax: Decimal
    amount_financed: Decimal


def preset_from_dict(data: dict) -> Preset:
    """Build a Preset from a plain dict (the preset JSON format)."""
    return Preset(
        dealer=_preset_profile_from_dict(data.get("dealer", {})),
        lease=_preset_lease_from_dict(data.get("lease")),
        state_tax_rate=D(data.get("state_tax_rate", "0.06")),
        county_surtax_rates={
            county: D(rate)
            for county, rate in data.get("county_surtax_rates", {}).items()
        },
        rules=rules_from_dict(data.get("state_rules")),
        version=str(data.get("version", "1")),
        approved_by=data.get("approved_by"),
        approved_at=data.get("approved_at"),
    )


def preset_from_setup_file(setup_path: Union[str, Path]) -> Preset:
    """Load the approved preset embedded in a Stage 2 setup file."""
    raw = json.loads(Path(setup_path).read_text(encoding="utf-8"))
    preset_data = raw.get("preset")
    if not isinstance(preset_data, dict):
        raise SetupRequiredError(
            "The setup file has no approved preset embedded in it; "
            "run the setup wizard (Stage 2) first."
        )
    return preset_from_dict(preset_data)


class DealSession:
    """One deal, walked from buyer entry to certification.

    Construct directly when you already hold an approved ``Preset``
    (tests, development), or with :meth:`start` to load the preset
    from a completed Stage 2 setup file. Either way the constructor
    enforces: setup complete, preset approved, acting user may edit
    deals.
    """

    def __init__(
        self,
        *,
        user: User,
        preset: Union[Preset, dict],
        setup_path: Union[str, Path] = DEFAULT_SETUP_PATH,
        deals_dir: Union[str, Path] = DEFAULT_DEALS_DIR,
        audit_log: Optional[AuditLog] = None,
        deal_id: Optional[str] = None,
        is_drill: bool = False,
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
            is_drill=is_drill,
        )
        # Preset store charges are the deal's starting values; the user
        # can edit them per deal during entry (and certifies the edit).
        if preset.dealer.dealer_fee is not None:
            self.inputs.dealer_fee = preset.dealer.dealer_fee
        if preset.dealer.electronic_tag_fee is not None:
            self.inputs.electronic_tag_fee = preset.dealer.electronic_tag_fee
        self._audit(EVENT_DEAL_CREATED, {"deal_id": self.deal_id})
        self.save()

    def _init_state(
        self,
        *,
        user: User,
        preset: Preset,
        deals_dir: Union[str, Path],
        audit_log: Optional[AuditLog],
        deal_id: str,
        is_drill: bool = False,
    ) -> None:
        self.deal_id = deal_id
        # Stage 5 drill mode: a practice deal. Drills carry the SAMPLE
        # watermark forever (see the watermark property) and are
        # excluded from normal deal listings.
        self.is_drill = is_drill
        self.preset = preset
        self.deals_dir = Path(deals_dir)
        self.audit_log = audit_log
        self.current_user = user
        self.created_by = user.username
        self.created_at = _now_iso()
        self.current_step: DealStep = DealStep.BUYER
        self.inputs = DealInputs()
        self.worksheet_result: Optional[DealResult] = None
        self.financing: Optional[FinancingBreakdown] = None
        self._worksheet_fingerprint: Optional[str] = None
        self.warnings_acknowledged_by: Optional[str] = None
        self.warnings_acknowledged_at: Optional[str] = None
        self.certification: Optional[CertificationRecord] = None

    @classmethod
    def start(
        cls,
        user: User,
        *,
        setup_path: Union[str, Path] = DEFAULT_SETUP_PATH,
        deals_dir: Union[str, Path] = DEFAULT_DEALS_DIR,
        audit_log: Optional[AuditLog] = None,
    ) -> "DealSession":
        """Start a deal on the preset approved in the Stage 2 setup file."""
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
    ) -> "DealSession":
        """Restore an autosaved session exactly, certification included.

        Loading does not re-check setup (a saved deal must be resumable
        even if it is being inspected later), but the acting user still
        needs the edit-deal permission to hold the session.
        """
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

    # -- state views ---------------------------------------------------------

    @property
    def is_certified(self) -> bool:
        return self.certification is not None

    @property
    def state(self) -> str:
        """'ready_to_print' once certified; otherwise the current step."""
        if self.is_certified:
            return "ready_to_print"
        return self.current_step.value

    @property
    def is_printable_clean(self) -> bool:
        """True only for a certified deal (spec Section 8)."""
        return self.is_certified

    @property
    def watermark(self) -> Optional[str]:
        """"SAMPLE" for a drill deal -- always, certified or not;
        otherwise "DRAFT" until certified, then None."""
        if self.is_drill:
            return "SAMPLE"
        return None if self.is_certified else "DRAFT"

    @property
    def warnings(self) -> tuple:
        """Warnings on the current worksheet (empty until computed)."""
        if self.worksheet_result is None:
            return ()
        return self.worksheet_result.warnings

    @property
    def worksheet_is_current(self) -> bool:
        return (
            self.worksheet_result is not None
            and self._worksheet_fingerprint == self._fingerprint()
        )

    @property
    def cash_down_payment(self) -> Decimal:
        return self.inputs.cash_down_payment

    @cash_down_payment.setter
    def cash_down_payment(self, amount) -> None:
        self.set_money(cash_down_payment=amount)

    # -- input entry (every setter audits, invalidates, autosaves) -----------

    def set_buyer(self, buyer: Buyer) -> None:
        self.inputs.buyer = buyer
        self._after_edit("buyer")

    def set_vehicle(self, vehicle: Vehicle) -> None:
        self.inputs.vehicle = vehicle
        self._after_edit("vehicle")

    def set_packaging_details(self, *, transaction_type: str, lender_name: str,
                              lender_address: str = '', **lender_parts: str) -> None:
        if transaction_type not in ('cash', 'finance'):
            raise ValueError('Choose cash or finance for this retail deal.')
        old = getattr(self, 'packaging_details', {})
        details = packaging_details_with(
            old, {'transaction_type': transaction_type, 'lender_name': lender_name.strip()},
            lender_address=lender_address, **lender_parts)
        if details != old:
            self.packaging_details = details
            self._after_edit('packaging')

    def set_products(self, products) -> None:
        """Record the F&I products this deal sold (see clean_products).
        Each sold product's own form prints in the pack."""
        cleaned = clean_products(products)
        details = dict(getattr(self, 'packaging_details', {}))
        if cleaned:
            details['products'] = cleaned
        else:
            details.pop('products', None)
        if details != getattr(self, 'packaging_details', {}):
            self.packaging_details = details
            self._after_edit('packaging')

    def set_trade(self, trade: Optional[TradeIn]) -> None:
        self.inputs.trade = trade
        self._after_edit("trade")

    def set_money(self, **amounts) -> None:
        """Set money inputs, e.g. set_money(selling_price="35000").

        Allowed fields: see MONEY_FIELDS. Values are coerced to
        Decimal; floats are never used for money.
        """
        unknown = set(amounts) - set(MONEY_FIELDS)
        if unknown:
            raise ValueError(
                f"Unknown money field(s): {', '.join(sorted(unknown))}. "
                f"Allowed: {', '.join(MONEY_FIELDS)}."
            )
        for name, value in amounts.items():
            setattr(self.inputs, name, D(value))
        if amounts:
            self._after_edit("money")

    def set_payment_terms(
        self,
        *,
        term_months: Optional[int],
        annual_rate: Optional[Decimal],
        days_to_first_payment: Optional[int] = None,
    ) -> None:
        """Set the finance terms: months, APR as a fraction (0.0834 =
        8.34%), and days to the first payment (None = 30). Clearing
        both term and rate removes the payment figures (a cash deal)."""
        if (term_months is None) != (annual_rate is None):
            raise ValueError(
                "Enter both the term and the APR for a financed deal, or "
                "neither for a cash deal."
            )
        if term_months is not None:
            term_months = int(term_months)
            if not 1 <= term_months <= 120:
                raise ValueError("Term must be 1 to 120 months.")
            annual_rate = D(annual_rate)
            if not ZERO <= annual_rate < Decimal("1"):
                raise ValueError("APR must be between 0% and 100%.")
        if term_months is None:
            days_to_first_payment = None  # a cash deal has no first payment
        if days_to_first_payment is not None:
            days_to_first_payment = int(days_to_first_payment)
            if not 1 <= days_to_first_payment <= 90:
                raise ValueError("Days to first payment must be 1 to 90.")
        new = (term_months, annual_rate, days_to_first_payment)
        old = (self.inputs.term_months, self.inputs.annual_rate,
               self.inputs.days_to_first_payment)
        if new == old:
            return
        (self.inputs.term_months, self.inputs.annual_rate,
         self.inputs.days_to_first_payment) = new
        self._after_edit("payment_terms")

    def _after_edit(self, kind: str) -> None:
        """Common post-edit bookkeeping: audit, invalidation, autosave."""
        self._audit(EVENT_DEAL_EDITED, {"deal_id": self.deal_id, "section": kind})
        # An edit can change the warnings, so any earlier acknowledgment
        # no longer stands.
        self.warnings_acknowledged_by = None
        self.warnings_acknowledged_at = None
        if self.certification is not None:
            certifier = self.certification.certifier_username
            self.certification = None
            self.current_step = DealStep.WORKSHEET
            self._audit(
                EVENT_CERTIFICATION_INVALIDATED,
                {
                    "deal_id": self.deal_id,
                    "section": kind,
                    "previously_certified_by": certifier,
                    "invalidated_by": self.current_user.username,
                },
            )
        self.save()

    # -- step validation and navigation ----------------------------------------

    def validate_step(self, step: DealStep) -> list[str]:
        """Plain-language problems blocking ``step``; empty = can pass."""
        if step is DealStep.BUYER:
            errors = []
            buyer = self.inputs.buyer
            if not buyer.full_name.strip():
                errors.append("Buyer name is blank.")
            needs_place = self.preset is None or self.preset.needs_buyer_place
            if needs_place and not buyer.residence_county.strip():
                errors.append(
                    "Buyer residence county is blank: the county surtax "
                    "rate comes from that county's approved rate."
                )
            return errors

        if step is DealStep.VEHICLE:
            errors = []
            vehicle = self.inputs.vehicle
            if vehicle.year is None:
                errors.append("Vehicle year is blank.")
            if not vehicle.make.strip():
                errors.append("Vehicle make is blank.")
            if not vehicle.model.strip():
                errors.append("Vehicle model is blank.")
            if not vehicle.vin.strip():
                errors.append("Vehicle VIN is blank.")
            return errors

        if step is DealStep.TRADE:
            trade = self.inputs.trade
            if trade is not None:
                if trade.allowance < ZERO:
                    errors.append("Trade-in allowance cannot be negative.")
                if trade.payoff < ZERO:
                    errors.append("Trade lien payoff cannot be negative.")
            return []

        if step is DealStep.WORKSHEET:
            if not self.worksheet_is_current:
                return ["Run the worksheet calculation first."]
            return []

        if step is DealStep.REVIEW:
            problems = self._certification_blockers(check_permission=False)
            return problems

        return []

    def advance(self) -> list[str]:
        """Try to move to the next step; returns the blocking errors
        (empty = advanced). Moving off the worksheet computes it first;
        the review step ends only via :meth:`certify`."""
        if self.current_step is DealStep.WORKSHEET:
            try:
                self.compute_worksheet()
            except DealFlowError as exc:
                return [str(exc)]
        if self.current_step is DealStep.REVIEW:
            return [
                "This deal is ready to certify. Certification is a "
                "separate, named act -- use Certify to finish."
            ]
        if self.current_step is DealStep.CERTIFIED:
            return []
        errors = self.validate_step(self.current_step)
        if errors:
            return errors
        idx = STEP_ORDER.index(self.current_step)
        if idx < len(STEP_ORDER) - 1:
            self.current_step = STEP_ORDER[idx + 1]
        self.save()
        return []

    def back(self) -> None:
        if self.current_step in STEP_ORDER:
            idx = STEP_ORDER.index(self.current_step)
            if idx > 0:
                self.current_step = STEP_ORDER[idx - 1]
                self.save()

    # -- worksheet --------------------------------------------------------------

    def _fingerprint(self) -> str:
        """A stable string for the current inputs, used to detect a
        worksheet computed from older inputs."""
        return json.dumps(
            _inputs_to_dict(self.inputs), sort_keys=True, default=str
        )

    def compute_worksheet(self) -> DealResult:
        """Run the Stage 1 engine on the current inputs and compute the
        financing section on top of its result."""
        county = self.inputs.buyer.residence_county.strip()
        try:
            # Decision 22: the rule sheet says whose place picks the rate.
            self.inputs.county_surtax_rate = self.preset.local_rate_for(county)
        except KeyError as exc:
            raise DealFlowError(str(exc.args[0])) from None
        self.worksheet_result = calculate_retail_deal(
            self.inputs, preset=self.preset, certify=False
        )
        self.financing = compute_financing(self.inputs, self.worksheet_result)
        # The engine's payment estimate runs on its step-12 amount due,
        # which does not net a trade payoff. The printed figures must be
        # the financing section's, so they replace the estimate here.
        self.worksheet_result = dataclasses.replace(
            self.worksheet_result,
            amount_financed=(self.financing.amount_financed
                             if self.financing.monthly_payment is not None else None),
            monthly_payment=self.financing.monthly_payment,
            total_of_payments=self.financing.total_of_payments,
            finance_charge=self.financing.finance_charge,
        )
        self._worksheet_fingerprint = self._fingerprint()
        self.save()
        return self.worksheet_result

    # -- warnings ------------------------------------------------------------------

    def acknowledge_warnings(self, user: Optional[User] = None) -> None:
        """Record that ``user`` (default: the current user) has seen and
        acknowledged the worksheet warnings. Certification checks the
        acknowledgment is by the certifying user personally."""
        actor = user or self.current_user
        self.warnings_acknowledged_by = actor.username
        self.warnings_acknowledged_at = _now_iso()
        self.save()

    # -- certification ---------------------------------------------------------------

    def _products_mismatch(self) -> Optional[str]:
        """The message when the products sold do not add up to the
        worksheet's Optional products line; None when they match or
        none were sold."""
        details = getattr(self, 'packaging_details', {})
        if details.get('products') and products_total(details) != self.inputs.optional_products:
            return (
                f"The products sold add up to ${products_total(details):,.2f}, but "
                f"Optional products on the worksheet is ${self.inputs.optional_products:,.2f}. "
                "Make them match.")
        return None

    def _certification_blockers(self, *, check_permission: bool = True) -> list[str]:
        problems: list[str] = []
        for step in (DealStep.BUYER, DealStep.VEHICLE, DealStep.TRADE):
            for error in self.validate_step(step):
                problems.append(f"Step {step.value}: {error}")
        mismatch = self._products_mismatch()
        if mismatch:
            problems.append(mismatch)
        if not self.worksheet_is_current:
            problems.append("The worksheet has not been calculated from the current inputs.")
        elif self.warnings and self.warnings_acknowledged_by is None:
            problems.append(
                "The worksheet carries a warning that must be "
                "acknowledged by the certifying user before certifying."
            )
        if check_permission and not has_permission(
            self.current_user, ACTION_CERTIFY_DEAL
        ):
            problems.append(
                f"{self.current_user.display_name} ({self.current_user.role}) "
                "does not have the certify-deal permission."
            )
        return problems

    def certify(self, user: Optional[User] = None) -> CertificationRecord:
        """Certify this deal's calculations (spec Section 8).

        ``user`` defaults to the session's current user; passing one
        switches the acting user (e.g. a manager certifying an F&I
        user's deal). Only a user with the certify-deal permission can
        certify. On success the deal is stamped with the certifier and
        the preset identity, moves to state ``ready_to_print``, and a
        ``deal_certified`` audit event is written.
        """
        actor = user or self.current_user
        self.current_user = actor
        if not has_permission(actor, ACTION_CERTIFY_DEAL):
            raise DealPermissionError(
                f"{actor.display_name} ({actor.role}) cannot certify "
                "deals. Certification requires the certify-deal "
                "permission (admin, manager, or F&I)."
            )
        for step in (DealStep.BUYER, DealStep.VEHICLE, DealStep.TRADE):
            errors = self.validate_step(step)
            if errors:
                raise CertificationError(
                    f"Step {step.value} is incomplete: " + "; ".join(errors)
                )
        mismatch = self._products_mismatch()
        if mismatch:
            raise CertificationError(mismatch)
        # Certification always runs on a worksheet computed from the
        # inputs as they stand now.
        self.compute_worksheet()
        if self.warnings:
            if self.warnings_acknowledged_by != actor.username:
                raise CertificationError(
                    "The worksheet carries a warning ("
                    + self.warnings[0]
                    + ") The certifying user must acknowledge it "
                    "personally before certifying."
                )
        assert self.worksheet_result is not None and self.financing is not None
        self.certification = CertificationRecord(
            certifier_username=actor.username,
            certifier_name=actor.display_name,
            certifier_role=actor.role,
            certified_at=_now_iso(),
            preset_version=self.preset.version,
            preset_approved_by=self.preset.approved_by,
            preset_approved_at=self.preset.approved_at,
            total_tax=self.worksheet_result.total_tax,
            amount_financed=self.financing.amount_financed,
        )
        self.current_step = DealStep.CERTIFIED
        self._audit(
            EVENT_DEAL_CERTIFIED,
            {
                "deal_id": self.deal_id,
                "certifier_role": actor.role,
                "preset_version": self.preset.version,
                "total_tax": str(self.worksheet_result.total_tax),
                "amount_financed": str(self.financing.amount_financed),
                "warnings_acknowledged": bool(self.warnings),
            },
        )
        self.save()
        return self.certification

    # -- audit + persistence ----------------------------------------------------------

    def _audit(self, event_type: str, details: dict) -> None:
        if self.audit_log is not None:
            self.audit_log.record(
                event_type, self.current_user.username, details
            )

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
            "format_version": "stage3-1",
            "packaging_details": getattr(self, 'packaging_details', {}),
            "is_drill": self.is_drill,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "current_step": self.current_step.value,
            "state": self.state,
            "watermark": self.watermark,
            "inputs": _inputs_to_dict(self.inputs),
            "worksheet_fingerprint": self._worksheet_fingerprint,
            "worksheet_result": (
                None
                if self.worksheet_result is None
                else _result_to_dict(self.worksheet_result)
            ),
            "financing": (
                None
                if self.financing is None
                else _financing_to_dict(self.financing)
            ),
            "warnings_acknowledged_by": self.warnings_acknowledged_by,
            "warnings_acknowledged_at": self.warnings_acknowledged_at,
            "certification": (
                None
                if self.certification is None
                else _certification_to_dict(self.certification)
            ),
        }

    def _restore(self, data: dict) -> None:
        self.packaging_details = dict(data.get('packaging_details', {}))
        self.is_drill = bool(data.get("is_drill", False))
        self.created_by = data.get("created_by", self.created_by)
        self.created_at = data.get("created_at", self.created_at)
        self.inputs = _inputs_from_dict(data.get("inputs", {}))
        step_value = data.get("current_step", DealStep.BUYER.value)
        self.current_step = DealStep(step_value)
        self._worksheet_fingerprint = data.get("worksheet_fingerprint")
        if data.get("worksheet_result") is not None:
            self.worksheet_result = _result_from_dict(data["worksheet_result"])
        if data.get("financing") is not None:
            self.financing = _financing_from_dict(data["financing"])
        self.warnings_acknowledged_by = data.get("warnings_acknowledged_by")
        self.warnings_acknowledged_at = data.get("warnings_acknowledged_at")
        if data.get("certification") is not None:
            self.certification = _certification_from_dict(data["certification"])
            self.current_step = DealStep.CERTIFIED


def list_deals(
    deals_dir: Union[str, Path] = DEFAULT_DEALS_DIR,
    *,
    include_drills: bool = False,
) -> list[dict]:
    """Summaries of the autosaved deals in ``deals_dir``.

    Drill (practice) deals are EXCLUDED unless ``include_drills`` --
    a drill is training, never a real deal, and must not show up in
    the store's working deal list. Summaries are plain dicts read
    from the saved files; a corrupt file is skipped, not fatal.
    """
    directory = Path(deals_dir)
    summaries: list[dict] = []
    if not directory.exists():
        return summaries
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        is_drill = bool(data.get("is_drill", False))
        if is_drill and not include_drills:
            continue
        inputs = data.get("inputs", {})
        summaries.append(
            {
                "deal_id": data.get("deal_id", path.stem),
                "created_by": data.get("created_by", ""),
                "created_at": data.get("created_at", ""),
                "state": data.get("state", ""),
                "watermark": data.get("watermark"),
                "is_drill": is_drill,
                "deal_kind": data.get("deal_kind", "retail"),
                "buyer_name": inputs.get("buyer", {}).get("full_name", ""),
            }
        )
    return summaries


# ---------------------------------------------------------------------------
# Serialisation helpers: session <-> JSON. Money crosses as strings, so a
# saved deal reloads to the exact cent.
# ---------------------------------------------------------------------------


def _money(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else str(value)


def _inputs_to_dict(inputs: DealInputs) -> dict:
    buyer = inputs.buyer
    vehicle = inputs.vehicle
    trade = inputs.trade
    return {
        "buyer": buyer_to_dict(buyer),
        "vehicle": vehicle_to_dict(vehicle),
        "trade": None
        if trade is None
        else {
            "description": trade.description,
            "vin": trade.vin,
            "allowance": str(trade.allowance),
            "payoff": str(trade.payoff),
        },
        "selling_price": str(inputs.selling_price),
        "accessories": str(inputs.accessories),
        "dealer_fee": str(inputs.dealer_fee),
        "other_taxable_fees": str(inputs.other_taxable_fees),
        "dealer_discount": str(inputs.dealer_discount),
        "county_surtax_rate": str(inputs.county_surtax_rate),
        "government_fees": str(inputs.government_fees),
        "electronic_tag_fee": str(inputs.electronic_tag_fee),
        "optional_products": str(inputs.optional_products),
        "manufacturer_rebate": str(inputs.manufacturer_rebate),
        "cash_down_payment": str(inputs.cash_down_payment),
        "term_months": inputs.term_months,
        "annual_rate": _money(inputs.annual_rate),
        # Written only when entered, so deals saved before this field
        # keep byte-identical inputs (and package fingerprints).
        **({"days_to_first_payment": inputs.days_to_first_payment}
           if inputs.days_to_first_payment is not None else {}),
    }


def _inputs_from_dict(data: dict) -> DealInputs:
    buyer_data = data.get("buyer", {})
    vehicle_data = data.get("vehicle", {})
    trade_data = data.get("trade")
    inputs = DealInputs(
        buyer=buyer_from_dict(buyer_data),
        vehicle=vehicle_from_dict(vehicle_data),
        trade=None
        if trade_data is None
        else TradeIn(
            description=trade_data.get("description", ""),
            vin=trade_data.get("vin", ""),
            allowance=D(trade_data.get("allowance", "0")),
            payoff=D(trade_data.get("payoff", "0")),
        ),
    )
    for name in (
        "selling_price",
        "accessories",
        "dealer_fee",
        "other_taxable_fees",
        "dealer_discount",
        "county_surtax_rate",
        "government_fees",
        "electronic_tag_fee",
        "optional_products",
        "manufacturer_rebate",
        "cash_down_payment",
    ):
        if data.get(name) is not None:
            setattr(inputs, name, D(data[name]))
    inputs.term_months = data.get("term_months")
    if data.get("annual_rate") is not None:
        inputs.annual_rate = D(data["annual_rate"])
    inputs.days_to_first_payment = data.get("days_to_first_payment")
    return inputs


def _result_to_dict(result: DealResult) -> dict:
    return {
        "gross_sales_price": str(result.gross_sales_price),
        "taxable_base": str(result.taxable_base),
        "state_tax": str(result.state_tax),
        "surtax_base": str(result.surtax_base),
        "county_surtax": str(result.county_surtax),
        "total_tax": str(result.total_tax),
        "government_fees": str(result.government_fees),
        "electronic_tag_fee": str(result.electronic_tag_fee),
        "amount_due": str(result.amount_due),
        "amount_financed": _money(result.amount_financed),
        "monthly_payment": _money(result.monthly_payment),
        "total_of_payments": _money(result.total_of_payments),
        "finance_charge": _money(result.finance_charge),
        "lines": [[label, str(amount)] for label, amount in result.lines],
        "warnings": list(result.warnings),
    }


def _result_from_dict(data: dict) -> DealResult:
    def opt(name):
        return None if data.get(name) is None else D(data[name])

    return DealResult(
        gross_sales_price=D(data["gross_sales_price"]),
        taxable_base=D(data["taxable_base"]),
        state_tax=D(data["state_tax"]),
        surtax_base=D(data["surtax_base"]),
        county_surtax=D(data["county_surtax"]),
        total_tax=D(data["total_tax"]),
        government_fees=D(data["government_fees"]),
        electronic_tag_fee=D(data["electronic_tag_fee"]),
        amount_due=D(data["amount_due"]),
        amount_financed=opt("amount_financed"),
        monthly_payment=opt("monthly_payment"),
        total_of_payments=opt("total_of_payments"),
        finance_charge=opt("finance_charge"),
        lines=tuple((label, D(amount)) for label, amount in data.get("lines", [])),
        warnings=tuple(data.get("warnings", [])),
    )


def _financing_to_dict(financing: FinancingBreakdown) -> dict:
    return {
        "selling_price": str(financing.selling_price),
        "total_tax": str(financing.total_tax),
        "electronic_tag_fee": str(financing.electronic_tag_fee),
        "other_non_tax_fees": str(financing.other_non_tax_fees),
        "subtotal_before_credits": str(financing.subtotal_before_credits),
        "manufacturer_rebate": str(financing.manufacturer_rebate),
        "cash_down_payment": str(financing.cash_down_payment),
        "net_trade_equity": str(financing.net_trade_equity),
        "amount_financed": str(financing.amount_financed),
        "lines": [[label, str(amount)] for label, amount in financing.lines],
        # Written only when non-zero, so financing saved before this
        # line existed keeps byte-identical records (and fingerprints).
        **({"price_additions": str(financing.price_additions)}
           if financing.price_additions != ZERO else {}),
        **{name: str(getattr(financing, name))
           for name in ("monthly_payment", "total_of_payments", "finance_charge")
           if getattr(financing, name) is not None},
    }


def _financing_from_dict(data: dict) -> FinancingBreakdown:
    return FinancingBreakdown(
        selling_price=D(data["selling_price"]),
        total_tax=D(data["total_tax"]),
        electronic_tag_fee=D(data["electronic_tag_fee"]),
        other_non_tax_fees=D(data["other_non_tax_fees"]),
        subtotal_before_credits=D(data["subtotal_before_credits"]),
        manufacturer_rebate=D(data["manufacturer_rebate"]),
        cash_down_payment=D(data["cash_down_payment"]),
        net_trade_equity=D(data["net_trade_equity"]),
        amount_financed=D(data["amount_financed"]),
        lines=tuple((label, D(amount)) for label, amount in data.get("lines", [])),
        price_additions=D(data.get("price_additions", "0")),
        **{name: D(data[name])
           for name in ("monthly_payment", "total_of_payments", "finance_charge")
           if data.get(name) is not None},
    )


def _certification_to_dict(record: CertificationRecord) -> dict:
    return {
        "certifier_username": record.certifier_username,
        "certifier_name": record.certifier_name,
        "certifier_role": record.certifier_role,
        "certified_at": record.certified_at,
        "preset_version": record.preset_version,
        "preset_approved_by": record.preset_approved_by,
        "preset_approved_at": record.preset_approved_at,
        "total_tax": str(record.total_tax),
        "amount_financed": str(record.amount_financed),
    }


def _certification_from_dict(data: dict) -> CertificationRecord:
    return CertificationRecord(
        certifier_username=data["certifier_username"],
        certifier_name=data["certifier_name"],
        certifier_role=data["certifier_role"],
        certified_at=data["certified_at"],
        preset_version=data["preset_version"],
        preset_approved_by=data.get("preset_approved_by"),
        preset_approved_at=data.get("preset_approved_at"),
        total_tax=D(data["total_tax"]),
        amount_financed=D(data["amount_financed"]),
    )


def packaging_details_with(old: dict, base: dict, *, lender_address: str = "",
                           lender_street: str = "", lender_city: str = "",
                           lender_state: str = "", lender_zip: str = "") -> dict:
    """New packaging details: ``base`` plus the lender's address (one
    line or in parts) and the products already recorded. Each key is
    written only when entered, so older packages keep their bytes."""
    details = dict(base)
    for key, value in (("lender_address", lender_address), ("lender_street", lender_street),
                       ("lender_city", lender_city), ("lender_state", lender_state),
                       ("lender_zip", lender_zip)):
        if value.strip():
            details[key] = value.strip()
    if (old or {}).get("products"):
        details["products"] = old["products"]
    return details


def clean_products(products) -> dict:
    """Validate the products a deal sold: {doc_id: {"price", "term",
    "coverage", "contract_number"}}. A product is sold when it has a
    price; its price is money more than $0."""
    cleaned: dict = {}
    for doc_id, line in (products or {}).items():
        doc_id = str(doc_id).strip()
        if not doc_id:
            raise ValueError("A product has no form.")
        text = str((line or {}).get("price") or "").replace(",", "").replace("$", "").strip()
        if not text:
            continue
        try:
            price = Decimal(text)
        except InvalidOperation:
            raise ValueError(f"Product {doc_id}: the price {text!r} is not a dollar amount.") from None
        if not price.is_finite() or price <= 0:
            raise ValueError(f"Product {doc_id}: the price must be more than $0.")
        entry = {"price": str(price.quantize(Decimal("0.01")))}
        for key in ("term", "coverage", "contract_number"):
            value = str((line or {}).get(key) or "").strip()
            if value:
                entry[key] = value
        cleaned[doc_id] = entry
    return cleaned


def products_total(details: dict) -> Decimal:
    return sum((Decimal(line["price"]) for line in (details.get("products") or {}).values()),
               Decimal("0"))
