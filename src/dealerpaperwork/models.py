"""Deal data model for the Dealer Revenue Rescue.

Stage 1 scope: plain dataclasses only. No persistence, no UI.

Design rules carried from the build specification:

* Dealer identity is NEVER hard-coded. ``DealerProfile`` ships empty;
  every field is entered by the user during the Step 0 setup wizard and
  approved by a manager before it can be used (see presets.py).
* Money is ``decimal.Decimal`` everywhere. Never use float for money.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


def D(value: object) -> Decimal:
    """Coerce an int/str/Decimal to Decimal without float artefacts."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


ZERO = Decimal("0")


@dataclass
class DealerProfile:
    """Everything about a dealer, entered during setup (spec Section 5, Step 0.2).

    A fresh profile contains NO dealer identity and NO fee values. That is
    a specification requirement (acceptance test 15), not an oversight:
    until setup is completed and approved, the app contains no dealer.
    """

    legal_name: Optional[str] = None
    trading_name: Optional[str] = None
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    phone: Optional[str] = None
    contact_email: Optional[str] = None
    licence_number: Optional[str] = None
    franchise_info: Optional[str] = None
    # Setup choice, e.g. "in-house EFS" or "tag agency" (spec Step 0.2.3).
    title_filing_method: Optional[str] = None
    # Store fees, entered by the dealer (spec Section 10). Blank blocks
    # preset approval until the dealer enters or explicitly removes a fee.
    dealer_fee: Optional[Decimal] = None
    electronic_tag_fee: Optional[Decimal] = None
    # Confirmed during setup: the Fla. Stat. 501.976(18) disclosure prints
    # on every document that carries the dealer fee (spec Section 10).
    disclosure_501_976_18_confirmed: bool = False

    @property
    def is_blank(self) -> bool:
        """True when no identity or fee value has been entered yet."""
        identity_fields = (
            self.legal_name,
            self.trading_name,
            self.street_address,
            self.city,
            self.licence_number,
            self.title_filing_method,
        )
        return (
            all(v is None for v in identity_fields)
            and self.dealer_fee is None
            and self.electronic_tag_fee is None
        )


@dataclass
class Buyer:
    """Buyer (and optional co-buyer) plus the residence county for surtax.

    Florida county discretionary surtax uses the buyer's residence county
    (the address on title/registration), not the store's county
    (spec Section 9, step 9).
    """

    full_name: str = ""
    co_buyer_name: Optional[str] = None
    street_address: str = ""
    city: str = ""
    state: str = "FL"
    zip_code: str = ""
    residence_county: str = ""
    # Paperwork-only details (2026-11-18): real contracts and the title
    # application print them; no calculation reads them.
    phone: str = ""
    co_buyer_street_address: str = ""
    co_buyer_city: str = ""
    co_buyer_state: str = ""
    co_buyer_zip_code: str = ""


@dataclass
class Vehicle:
    year: Optional[int] = None
    make: str = ""
    model: str = ""
    vin: str = ""
    is_new: bool = True
    stock_number: str = ""
    # Paperwork-only details (2026-11-18): the title application,
    # odometer statement and FTC Buyers Guide print them.
    body_style: str = ""
    color: str = ""
    odometer: Optional[int] = None
    # Decision 30: only for a rule sheet that uses them. The vehicle
    # class picks its own state rate (question 15); blank is the
    # standard rate. The state value is what the state says the vehicle
    # is worth (question 17), typed from the state's own lookup.
    tax_class: str = ""
    state_value: Optional[Decimal] = None


@dataclass
class TradeIn:
    description: str = ""
    vin: str = ""
    # Trade-in allowance reduces the taxable base (spec step 7).
    # The lien payoff does NOT reduce the taxable base (spec step 6).
    allowance: Decimal = ZERO
    payoff: Decimal = ZERO


@dataclass
class DealInputs:
    """All user-entered numbers for one retail deal.

    Field order follows the spec's 12-step retail order (Section 9) so the
    worksheet and the code read the same way.
    """

    buyer: Buyer = field(default_factory=Buyer)
    vehicle: Vehicle = field(default_factory=Vehicle)
    trade: Optional[TradeIn] = None

    # Step 1: vehicle selling price (includes destination/freight).
    selling_price: Decimal = ZERO
    # Step 2: dealer-installed accessories / add-ons sold with the vehicle.
    accessories: Decimal = ZERO
    # Step 3: taxable dealer-required fees. The dealer fee comes from the
    # approved dealer profile; it is copied onto the deal at entry time.
    dealer_fee: Decimal = ZERO
    other_taxable_fees: Decimal = ZERO
    # Step 5: the dealer's own discount given at the time of sale.
    dealer_discount: Decimal = ZERO
    # Buyer's residence-county surtax rate, e.g. Decimal("0.015") for 1.5%.
    # Loaded from the approved DR-15DSS preset table, never hard-coded
    # (spec step 9). The rate itself is an input so the engine stays a
    # pure function of its inputs plus the approved preset.
    county_surtax_rate: Decimal = ZERO

    # Step 11: state-mandated government fees, untaxed when separately
    # itemised (title, registration, plate/transfer, lien, $2 lemon-law
    # fee on new vehicles, tire/battery fees). The user confirms the
    # actual amounts on each deal (spec Section 10); Stage 1 carries the
    # confirmed total.
    government_fees: Decimal = ZERO
    # Dealer charge for a private tag service, TAXED as part of the
    # taxable base at step 3 (Decision 14, Lane 2026-11-02): it is not
    # a government agency fee. (The spec's original "not used in the
    # tax lines" treatment is superseded.)
    electronic_tag_fee: Decimal = ZERO
    # Optional products the buyer elected (service contract, etc.).
    optional_products: Decimal = ZERO
    # Step 12 only: the manufacturer rebate applies AFTER tax, as a
    # payment. It never reduces the taxable base (spec step 6).
    manufacturer_rebate: Decimal = ZERO
    cash_down_payment: Decimal = ZERO

    # Payment estimate inputs (optional; estimate only in Stage 1).
    term_months: Optional[int] = None
    annual_rate: Optional[Decimal] = None  # e.g. Decimal("0.0899") = 8.99% APR
    # Days from the deal date to the first payment. None means one
    # regular month (30 days). A longer first period carries its extra
    # days of interest in the payment, as the DMS worksheet does.
    days_to_first_payment: Optional[int] = None


@dataclass
class LeaseInputs:
    """All user-entered numbers for one lease deal (Stage 6).

    Field order follows the DMS lease worksheet the stores actually
    desk on: Capitalization, Cap Cost Reduction, Residual, Payment.
    The buyer and vehicle reuse the Stage 1 dataclasses. Program
    values (residual %, money factor, acquisition fee, fees) are
    entered per deal from the dealer-approved lease preset -- the
    app hard-codes none of them (locked decisions 1 and 2).
    """

    buyer: Buyer = field(default_factory=Buyer)
    vehicle: Vehicle = field(default_factory=Vehicle)

    # --- Capitalization (worksheet block 1) -------------------------
    msrp: Decimal = ZERO
    dealer_discount: Decimal = ZERO
    # Selling price is derived (MSRP - discount) by the engine.
    aftermarkets: Decimal = ZERO
    # The store's predelivery service charge (the dealer fee, on a
    # lease worksheet line of its own).
    predelivery_fee: Decimal = ZERO
    acquisition_fee: Decimal = ZERO
    esc_premium: Decimal = ZERO
    maintenance: Decimal = ZERO
    gap_premium: Decimal = ZERO
    lah_iui: Decimal = ZERO
    prior_lease_balance: Decimal = ZERO
    license_fee: Decimal = ZERO
    dealer_fees: Decimal = ZERO
    # Capitalized fees. The Florida rental surcharge (Fla. Stat.
    # 212.0606, $2/day for the first 30 days = $60) rides here when
    # the dealer's preset carries it -- an entered amount, never
    # hard-coded (locked decision 13).
    cap_fees: Decimal = ZERO
    # The worksheet's own cap-taxes line. Under decision 13 nothing
    # is taxed item-by-item in a lease, so this is normally zero; it
    # is carried so the worksheet structure matches the store's.
    cap_taxes: Decimal = ZERO

    # --- Cap cost reduction (worksheet block 2) ---------------------
    cash_down: Decimal = ZERO
    total_rebates: Decimal = ZERO
    trade_allowance: Decimal = ZERO
    trade_payoff: Decimal = ZERO
    # Cash collected at signing that the buyer applies to the lease
    # as a further cap reduction (beyond the first payment and the
    # upfront charges).
    amount_applied_upfront: Decimal = ZERO
    # Money handed back to the buyer at signing; it reduces the
    # total cap reduction.
    cash_back: Decimal = ZERO

    # --- Residual (worksheet block 3) -------------------------------
    # Residual base MSRP (the MSRP the residual % applies to; usually
    # the vehicle's MSRP).
    residual_base_msrp: Decimal = ZERO
    option_package_discount: Decimal = ZERO
    hard_adds: Decimal = ZERO
    # Maximum residualized MSRP from the lease program. When entered,
    # the residual base may not exceed it.
    mrm: Optional[Decimal] = None
    # The FINAL residual percentage as a fraction (e.g. 0.62). The
    # mileage-program selection behind it is a preset/entry concern;
    # the engine takes the final figure, as the worksheet does.
    residual_percent: Decimal = ZERO
    # Signed amount adjusting the residual for prepaid mileage (the
    # worksheet's "Mileage Adjustment" line; the cents-per-mile rate
    # on the worksheet is an entry aid the user multiplies out).
    mileage_adjustment: Decimal = ZERO

    # --- Payment (worksheet block 4) --------------------------------
    term_months: Optional[int] = None
    money_factor: Decimal = ZERO
    # Buyer's residence-county surtax rate (same approved DR-15DSS
    # table as retail; the 6% state rate comes from the preset).
    county_surtax_rate: Decimal = ZERO
    number_of_advance_payments: int = 1
    # Entered/preset bundle collected at signing on top of the
    # advance payment(s) (title/license/registration-type charges).
    upfront_charges: Decimal = ZERO
    security_deposit: Decimal = ZERO

    # --- End-of-lease terms (paperwork only, 2026-11-18) -------------
    # Printed on the lease contract's disclosures; none changes the
    # payment. Blank (None) means not entered, and the form spot
    # stays blank.
    annual_mileage_allowance: Optional[int] = None
    excess_mileage_rate: Optional[Decimal] = None  # dollars per mile
    purchase_option_fee: Optional[Decimal] = None


@dataclass
class LeaseResult:
    """Line-by-line result of the lease calculation.

    ``lines`` is the lease worksheet: an ordered tuple of
    (label, amount) pairs in the DMS worksheet's block order, so the
    printed worksheet reads exactly as calculated here.
    """

    selling_price: Decimal
    gross_cap_cost: Decimal
    net_trade: Decimal
    total_cap_reduction: Decimal
    net_cap_cost: Decimal
    adjusted_msrp: Decimal
    residual_base: Decimal  # adjusted MSRP after the MRM cap
    residual_amount: Decimal
    adjusted_residual: Decimal
    monthly_depreciation: Decimal
    monthly_rent_charge: Decimal
    base_monthly_payment: Decimal
    combined_tax_rate: Decimal
    monthly_tax: Decimal
    monthly_payment: Decimal
    advanced_payment: Decimal
    upfront_charges: Decimal
    security_deposit: Decimal
    due_on_delivery: Decimal
    # Duck-typing for the shared render/resolve helpers: a lease has
    # no single amount financed.
    amount_financed: Optional[Decimal] = None
    lines: tuple[tuple[str, Decimal], ...] = ()
    warnings: tuple[str, ...] = ()
    # Decision 22: tax a store's rule sheet collects at signing (a
    # state that taxes a lease up front). Always 0 under Florida rules.
    upfront_tax: Decimal = ZERO

    @property
    def total_tax(self) -> Decimal:
        """The tax a shared helper reads as "the deal's tax": on a
        Florida lease that is the tax on ONE monthly payment (decision
        13); a sheet that taxes up front adds the signing tax."""
        return self.monthly_tax + self.upfront_tax


@dataclass
class DealResult:
    """Line-by-line result of the retail calculation.

    ``lines`` is the worksheet: an ordered tuple of (label, amount) pairs
    in the spec's 12-step order, so later stages can render or print the
    breakdown exactly as calculated here.
    """

    gross_sales_price: Decimal
    taxable_base: Decimal
    state_tax: Decimal
    surtax_base: Decimal  # min(taxable_base, $5,000)
    county_surtax: Decimal
    total_tax: Decimal
    government_fees: Decimal
    electronic_tag_fee: Decimal
    amount_due: Decimal
    # Payment estimate (None unless term/rate were provided).
    amount_financed: Optional[Decimal] = None
    monthly_payment: Optional[Decimal] = None
    total_of_payments: Optional[Decimal] = None
    finance_charge: Optional[Decimal] = None
    lines: tuple[tuple[str, Decimal], ...] = ()
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Shared JSON shape for buyer and vehicle (retail and lease sessions).
# Paperwork-only details are written only when entered, so a deal saved
# before they existed keeps byte-identical inputs and fingerprints.
# ---------------------------------------------------------------------------

_BUYER_EXTRA = (
    "phone",
    "co_buyer_street_address",
    "co_buyer_city",
    "co_buyer_state",
    "co_buyer_zip_code",
)
_VEHICLE_EXTRA = ("body_style", "color")


def buyer_to_dict(buyer: Buyer) -> dict:
    data = {
        "full_name": buyer.full_name,
        "co_buyer_name": buyer.co_buyer_name,
        "street_address": buyer.street_address,
        "city": buyer.city,
        "state": buyer.state,
        "zip_code": buyer.zip_code,
        "residence_county": buyer.residence_county,
    }
    for name in _BUYER_EXTRA:
        if getattr(buyer, name):
            data[name] = getattr(buyer, name)
    return data


def buyer_from_dict(data: dict) -> Buyer:
    return Buyer(
        full_name=data.get("full_name", ""),
        co_buyer_name=data.get("co_buyer_name"),
        street_address=data.get("street_address", ""),
        city=data.get("city", ""),
        state=data.get("state", "FL"),
        zip_code=data.get("zip_code", ""),
        residence_county=data.get("residence_county", ""),
        **{name: str(data.get(name) or "") for name in _BUYER_EXTRA},
    )


def vehicle_to_dict(vehicle: Vehicle) -> dict:
    data = {
        "year": vehicle.year,
        "make": vehicle.make,
        "model": vehicle.model,
        "vin": vehicle.vin,
        "is_new": vehicle.is_new,
        "stock_number": vehicle.stock_number,
    }
    for name in _VEHICLE_EXTRA:
        if getattr(vehicle, name):
            data[name] = getattr(vehicle, name)
    if vehicle.odometer is not None:
        data["odometer"] = vehicle.odometer
    if vehicle.tax_class:
        data["tax_class"] = vehicle.tax_class
    if vehicle.state_value is not None:
        data["state_value"] = str(vehicle.state_value)
    return data


def vehicle_from_dict(data: dict) -> Vehicle:
    odometer = data.get("odometer")
    return Vehicle(
        year=data.get("year"),
        make=data.get("make", ""),
        model=data.get("model", ""),
        vin=data.get("vin", ""),
        is_new=bool(data.get("is_new", True)),
        stock_number=data.get("stock_number", ""),
        odometer=None if odometer is None else int(odometer),
        tax_class=str(data.get("tax_class") or ""),
        state_value=None if data.get("state_value") in (None, "") else D(data["state_value"]),
        **{name: str(data.get(name) or "") for name in _VEHICLE_EXTRA},
    )


def parse_odometer(text: Optional[str]) -> Optional[int]:
    """Typed odometer text -> whole miles; blank means not entered."""
    cleaned = (text or "").strip().replace(",", "")
    if not cleaned:
        return None
    try:
        miles = int(cleaned)
    except ValueError:
        raise ValueError(f"Odometer: {text!r} is not a whole number of miles, e.g. 41250.") from None
    if miles < 0:
        raise ValueError("Odometer cannot be negative.")
    return miles
