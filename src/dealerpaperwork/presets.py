"""Preset loading, validation, and approval gating.

A preset is the dealer's own configuration: dealer profile, store fees,
and tax parameters (spec Section 6). The cooperative model is:

    preset -> dealer edits -> dealer approves -> app runs only the
    approved set.

Stage 1 keeps this deliberately small: a preset is a JSON file, approval
is two fields in that file (``approved_by`` / ``approved_at``), and the
tax engine refuses to run a *certified* calculation against a preset that
is not approved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional, Union

from .models import D, DealerProfile
from .state_rules import FLORIDA, StateRules, local_place, rules_from_dict, validate_rules


class UnapprovedPresetError(Exception):
    """Raised when a certified calculation is requested with a preset
    that has not been approved (or with no preset at all)."""


@dataclass
class LeasePreset:
    """The lease section of a preset set (Stage 6).

    Everything here is dealer-entered data, like the rest of the
    preset: the lessor's name and who holds title on a lease (the
    lessor, on a standard lease), plus the store's standard lease
    fee defaults a new lease deal starts from. Approval rides on the
    containing preset set's approval (spec Section 6) -- a lease
    preset cannot be approved separately from the set it lives in.
    All fields start empty: a fresh install has no lessor and no
    lease fee values (locked decision 1).
    """

    lessor_name: Optional[str] = None
    title_holder: Optional[str] = None
    acquisition_fee: Optional[Decimal] = None
    cap_fees: Optional[Decimal] = None
    upfront_charges: Optional[Decimal] = None


@dataclass
class Preset:
    """An editable preset set. ``approved_by``/``approved_at`` are None
    until a named approver approves it (spec Section 6)."""

    dealer: DealerProfile = field(default_factory=DealerProfile)
    # Stage 6: the lease section (lessor, title holder, lease fee
    # defaults). None until the dealer enters a lease preset.
    lease: Optional[LeasePreset] = None
    # Florida state sales-tax rate as data (currently 6%). Held in the
    # preset, not buried in code, so a rate change is a preset change the
    # dealer approves (spec Section 6, "Fees and tax").
    state_tax_rate: Decimal = Decimal("0.06")
    # County discretionary surtax rates by buyer residence county, loaded
    # from the current-year Form DR-15DSS (spec Section 9, step 9).
    county_surtax_rates: dict[str, Decimal] = field(default_factory=dict)
    # Decision 32: the calendar year of the built-in table the rates were
    # loaded from, or None for a table the store typed by hand. A deal in
    # another year cannot be certified on a dated table.
    county_rates_year: Optional[int] = None
    # Decision 22: how the store's state taxes a deal. Florida is the
    # built-in sheet and the default (every preset before Decision 22
    # was a Florida preset).
    rules: StateRules = FLORIDA
    version: str = "1"
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None  # ISO-8601 timestamp string

    @property
    def is_approved(self) -> bool:
        return bool(self.approved_by) and bool(self.approved_at)

    @property
    def needs_buyer_place(self) -> bool:
        """True when the buyer's home county/place picks the local rate."""
        return local_place(self.rules, "x") == "x"

    def local_rate_for(self, buyer_place: str) -> Decimal:
        """The local rate a deal uses under the rule sheet (Decision 22):
        the buyer's place, the dealership's place, or none."""
        if not self.needs_buyer_place:
            place = local_place(self.rules, buyer_place)
            return Decimal("0") if place is None else self.surtax_rate_for(place)
        place = (buyer_place or "").strip()
        if not place:
            raise KeyError(
                "Buyer residence county is blank: the county surtax "
                "rate comes from that county's approved rate."
            )
        return self.surtax_rate_for(place)

    def surtax_rate_for(self, county: str) -> Decimal:
        """Surtax rate for a buyer residence county.

        Raises KeyError with a plain message when the county is not in
        the approved table -- the app must never guess a county rate.
        """
        try:
            return self.county_surtax_rates[county]
        except KeyError:
            raise KeyError(
                f"No county surtax rate in the approved preset table for "
                f"county {county!r}. Load the current-year DR-15DSS table."
            ) from None


def _profile_from_dict(data: dict) -> DealerProfile:
    profile = DealerProfile(
        legal_name=data.get("legal_name"),
        trading_name=data.get("trading_name"),
        street_address=data.get("street_address"),
        city=data.get("city"),
        state=data.get("state"),
        zip_code=data.get("zip_code"),
        phone=data.get("phone"),
        contact_email=data.get("contact_email"),
        licence_number=data.get("licence_number"),
        franchise_info=data.get("franchise_info"),
        title_filing_method=data.get("title_filing_method"),
        disclosure_501_976_18_confirmed=bool(
            data.get("disclosure_501_976_18_confirmed", False)
        ),
    )
    if data.get("dealer_fee") is not None:
        profile.dealer_fee = D(data["dealer_fee"])
    if data.get("electronic_tag_fee") is not None:
        profile.electronic_tag_fee = D(data["electronic_tag_fee"])
    return profile


def _lease_from_dict(data) -> Optional[LeasePreset]:
    """Build the Stage 6 lease section from a plain dict (or None)."""
    if not isinstance(data, dict):
        return None
    lease = LeasePreset(
        lessor_name=data.get("lessor_name"),
        title_holder=data.get("title_holder"),
    )
    for name in ("acquisition_fee", "cap_fees", "upfront_charges"):
        if data.get(name) is not None:
            setattr(lease, name, D(data[name]))
    return lease


def rates_year_from(raw: dict) -> Optional[int]:
    """Decision 32's rate year from a saved preset; absent means None, so
    a preset saved before decision 32 reads exactly as it did."""
    value = raw.get("county_rates_year")
    return None if value in (None, "") else int(value)


def load_preset(path: Union[str, Path]) -> Preset:
    """Load a preset JSON file. Does not approve it; approval is recorded
    in the file itself by the dealer's approver."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    dealer = _profile_from_dict(raw.get("dealer", {}))
    rates = {
        county: D(rate)
        for county, rate in raw.get("county_surtax_rates", {}).items()
    }
    return Preset(
        dealer=dealer,
        lease=_lease_from_dict(raw.get("lease")),
        state_tax_rate=D(raw.get("state_tax_rate", "0.06")),
        county_surtax_rates=rates,
        county_rates_year=rates_year_from(raw),
        rules=rules_from_dict(raw.get("state_rules")),
        version=str(raw.get("version", "1")),
        approved_by=raw.get("approved_by"),
        approved_at=raw.get("approved_at"),
    )


def validate_lease_preset(preset: Preset) -> list[str]:
    """Problems with a preset set's Stage 6 lease section; empty = a
    lease deal may run on it. Kept separate from validate_preset: a
    store that never leases should not have its retail preset blocked
    by a blank lease section."""
    lease = preset.lease
    if lease is None:
        return [
            "The preset set has no lease section: the dealer must "
            "enter the lessor and title holder before a lease deal "
            "can run (Stage 6)."
        ]
    errors: list[str] = []
    if not (lease.lessor_name or "").strip():
        errors.append("Lease preset: lessor name is blank.")
    if not (lease.title_holder or "").strip():
        errors.append("Lease preset: title holder is blank.")
    return errors


def validate_preset(preset: Preset) -> list[str]:
    """Return a list of human-readable problems with a preset.

    An empty list means the preset is complete enough to approve. These
    checks mirror the spec: a blank fee blocks approval (Step 0.2.4), the
    dealer profile must exist before deals can run (acceptance test 15),
    and county rates are data the dealer must load (step 9).
    """
    errors: list[str] = []
    dealer = preset.dealer
    if dealer.is_blank:
        errors.append(
            "Dealer profile is blank: dealer identity must be entered "
            "during setup (Step 0.2) before a preset can be approved."
        )
    else:
        if not dealer.legal_name:
            errors.append("Dealer profile: legal name is missing.")
        if dealer.dealer_fee is None:
            errors.append(
                "Dealer fee is blank: enter an amount or remove the fee "
                "in setup -- a blank fee blocks approval (Step 0.2.4)."
            )
        if preset.rules is FLORIDA and not dealer.disclosure_501_976_18_confirmed:
            errors.append(
                "The 501.976(18) disclosure printing on documents that "
                "carry the dealer fee has not been confirmed in setup."
            )
    errors.extend(validate_rules(preset.rules, preset.county_surtax_rates))
    taxes = preset.rules.tax_kind != "none"
    if taxes and preset.state_tax_rate <= 0:
        errors.append("State tax rate must be greater than zero.")
    if taxes and preset.rules.local_by != "none" and not preset.county_surtax_rates:
        errors.append(
            "County surtax table is empty: load the current-year "
            "DR-15DSS rates as preset data."
        )
    for county, rate in preset.county_surtax_rates.items():
        if rate < 0:
            errors.append(f"County surtax rate for {county!r} is negative.")
    return errors


def require_approved(preset: Optional[Preset]) -> Preset:
    """Gate for certified calculations (spec Sections 6 and 8).

    Raises UnapprovedPresetError if there is no preset, if it has not
    been approved by a named approver, or if it fails validation.
    """
    if preset is None:
        raise UnapprovedPresetError(
            "A certified calculation requires an approved preset set; "
            "no preset was supplied."
        )
    if not preset.is_approved:
        raise UnapprovedPresetError(
            "Preset set is not approved. Deals run only on the last "
            "approved preset set (spec Section 6)."
        )
    errors = validate_preset(preset)
    if errors:
        raise UnapprovedPresetError(
            "Preset set is approved but incomplete: " + "; ".join(errors)
        )
    return preset
