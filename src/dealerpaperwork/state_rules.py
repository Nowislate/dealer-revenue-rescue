"""The store's tax and tag rule sheet (Decision 22).

A rule sheet says HOW the store's state taxes a vehicle deal: which
charges are taxed, how the trade-in counts, whose address picks the
local rate, any limit on the local tax or on the whole tax, and how a
lease is taxed. The RATES stay where they were: the state rate and the
local rate table live on the preset (``Preset.state_tax_rate`` and
``Preset.county_surtax_rates``), entered and approved at setup.

Florida is the one built-in sheet (``FLORIDA``). Its answers are the
rules the engines were proven on to the cent (Decisions 13 and 14,
spec Section 9). Any other store fills in its own sheet at setup; no
other state ships pre-filled, because that would claim a state's law
no one has proven against a real worksheet. The math check (Decision
21) proves a typed sheet against the store's own DMS deals.

A rule a sheet cannot say is not guessed: setup stops with a plain
message (``UNSUPPORTED``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .models import D

# Question 1: what tax the state charges on a vehicle sale.
TAX_ON_PRICE = "price"
TAX_NONE = "none"
TAX_KINDS = (TAX_ON_PRICE, TAX_NONE)

# Question 3: whose address picks the local rate.
LOCAL_BY_BUYER = "buyer"
LOCAL_BY_DEALER = "dealer"
LOCAL_NONE = "none"
LOCAL_BY = (LOCAL_BY_BUYER, LOCAL_BY_DEALER, LOCAL_NONE)

# Question 7: trade-in credit.
TRADE_FULL = "full"
TRADE_NONE = "none"
TRADE_CAPPED = "capped"
TRADE_CREDITS = (TRADE_FULL, TRADE_NONE, TRADE_CAPPED)

# Question 9: the charges a store marks taxed or not.
CHARGE_DEALER_FEE = "dealer_fee"
CHARGE_TAG_FEE = "tag_fee"
CHARGE_ACCESSORIES = "accessories"
CHARGE_PRODUCTS = "products"
CHARGE_GOVERNMENT_FEES = "government_fees"
CHARGES = (
    CHARGE_DEALER_FEE,
    CHARGE_TAG_FEE,
    CHARGE_ACCESSORIES,
    CHARGE_PRODUCTS,
    CHARGE_GOVERNMENT_FEES,
)

# Question 11: how a lease is taxed.
LEASE_MONTHLY = "monthly"
LEASE_UPFRONT_TOTAL = "upfront_total"
LEASE_UPFRONT_PRICE = "upfront_price"
LEASE_NONE = "none"
LEASE_TAXES = (LEASE_MONTHLY, LEASE_UPFRONT_TOTAL, LEASE_UPFRONT_PRICE, LEASE_NONE)

# The last choice on questions 1 and 11: setup stops, nothing guesses.
UNSUPPORTED = "unsupported"
UNSUPPORTED_MESSAGE = (
    "This app cannot yet run your state's rules. Setup cannot finish "
    "with a rule the app would have to guess."
)


@dataclass(frozen=True)
class StateRules:
    """One store's answers to the rule sheet. Rates live on the preset."""

    state: str
    built_in: bool = False
    tax_kind: str = TAX_ON_PRICE
    local_by: str = LOCAL_BY_BUYER
    # The place whose rate applies when the dealership's address picks it.
    dealer_place: Optional[str] = None
    # The local rate applies only to the first ``local_cap`` dollars.
    local_cap: Optional[Decimal] = None
    # The most tax one deal can owe (state + local), or None.
    max_tax: Optional[Decimal] = None
    trade_credit: str = TRADE_FULL
    trade_credit_cap: Optional[Decimal] = None
    rebate_before_tax: bool = False
    taxed: frozenset = field(default_factory=frozenset)
    lease_tax: str = LEASE_MONTHLY
    lease_reduction_taxed: bool = False

    def is_taxed(self, charge: str) -> bool:
        if charge not in CHARGES:
            raise ValueError(f"Unknown charge {charge!r}.")
        return charge in self.taxed

    @property
    def local_label(self) -> str:
        return "County surtax" if self.built_in and self.state == "FL" else "Local tax"


FLORIDA = StateRules(
    state="FL",
    built_in=True,
    tax_kind=TAX_ON_PRICE,
    local_by=LOCAL_BY_BUYER,
    local_cap=Decimal("5000"),
    max_tax=None,
    trade_credit=TRADE_FULL,
    rebate_before_tax=False,
    # Decision 14: the electronic tag fee is taxed. Products and
    # government fees enter after tax, untaxed (spec Section 9).
    taxed=frozenset({CHARGE_DEALER_FEE, CHARGE_TAG_FEE, CHARGE_ACCESSORIES}),
    # Decision 13: tax on each base monthly payment only.
    lease_tax=LEASE_MONTHLY,
    lease_reduction_taxed=False,
)

BUILT_IN = {"FL": FLORIDA}


def validate_rules(rules: StateRules, local_places: Optional[dict] = None) -> list[str]:
    """Plain problems with a rule sheet; empty means setup may go on."""
    errors: list[str] = []
    if rules.tax_kind == UNSUPPORTED or rules.lease_tax == UNSUPPORTED:
        return [UNSUPPORTED_MESSAGE]
    if not (rules.state or "").strip():
        errors.append("Rule sheet: the state is blank.")
    if rules.tax_kind not in TAX_KINDS:
        errors.append(f"Rule sheet: unknown tax kind {rules.tax_kind!r}.")
    if rules.local_by not in LOCAL_BY:
        errors.append(f"Rule sheet: unknown local-rate choice {rules.local_by!r}.")
    if rules.local_by == LOCAL_BY_DEALER:
        place = (rules.dealer_place or "").strip()
        if not place:
            errors.append(
                "Rule sheet: the dealership's place is blank, so the app "
                "cannot pick its local rate."
            )
        elif local_places is not None and place not in local_places:
            errors.append(
                f"Rule sheet: the dealership's place {place!r} is not in "
                "the local rate table."
            )
    if rules.local_cap is not None and rules.local_cap <= 0:
        errors.append("Rule sheet: the local tax limit must be more than $0.")
    if rules.max_tax is not None and rules.max_tax <= 0:
        errors.append("Rule sheet: the most tax per deal must be more than $0.")
    if rules.trade_credit not in TRADE_CREDITS:
        errors.append(f"Rule sheet: unknown trade credit {rules.trade_credit!r}.")
    if rules.trade_credit == TRADE_CAPPED and (
        rules.trade_credit_cap is None or rules.trade_credit_cap <= 0
    ):
        errors.append("Rule sheet: enter the trade-in credit limit in dollars.")
    for charge in rules.taxed:
        if charge not in CHARGES:
            errors.append(f"Rule sheet: unknown charge {charge!r}.")
    if rules.lease_tax not in LEASE_TAXES:
        errors.append(f"Rule sheet: unknown lease tax {rules.lease_tax!r}.")
    return errors


def trade_credit(rules: StateRules, allowance: Decimal) -> Decimal:
    """The part of the trade-in allowance that comes off the taxable base."""
    if rules.trade_credit == TRADE_NONE:
        return Decimal("0")
    if rules.trade_credit == TRADE_CAPPED and rules.trade_credit_cap is not None:
        return min(allowance, rules.trade_credit_cap)
    return allowance


def local_place(rules: StateRules, buyer_place: str) -> Optional[str]:
    """The place whose local rate applies, or None for no local tax."""
    if rules.tax_kind == TAX_NONE or rules.local_by == LOCAL_NONE:
        return None
    if rules.local_by == LOCAL_BY_DEALER:
        return (rules.dealer_place or "").strip() or None
    return (buyer_place or "").strip() or None


def _money(value) -> Optional[Decimal]:
    return None if value in (None, "") else D(value)


def rules_to_dict(rules: StateRules) -> dict:
    return {
        "state": rules.state,
        "built_in": rules.built_in,
        "tax_kind": rules.tax_kind,
        "local_by": rules.local_by,
        "dealer_place": rules.dealer_place,
        "local_cap": None if rules.local_cap is None else str(rules.local_cap),
        "max_tax": None if rules.max_tax is None else str(rules.max_tax),
        "trade_credit": rules.trade_credit,
        "trade_credit_cap": None
        if rules.trade_credit_cap is None
        else str(rules.trade_credit_cap),
        "rebate_before_tax": rules.rebate_before_tax,
        "taxed": sorted(rules.taxed),
        "lease_tax": rules.lease_tax,
        "lease_reduction_taxed": rules.lease_reduction_taxed,
    }


def rules_from_dict(data) -> StateRules:
    """Read a saved sheet. No sheet saved (a pre-Decision-22 setup) is
    Florida, the only state the app ran before. A built-in sheet is
    always the code's own copy, so an edited file cannot change it."""
    if not isinstance(data, dict):
        return FLORIDA
    if data.get("built_in") and data.get("state") in BUILT_IN:
        return BUILT_IN[data["state"]]
    return StateRules(
        state=str(data.get("state") or ""),
        built_in=False,
        tax_kind=str(data.get("tax_kind") or TAX_ON_PRICE),
        local_by=str(data.get("local_by") or LOCAL_BY_BUYER),
        dealer_place=data.get("dealer_place"),
        local_cap=_money(data.get("local_cap")),
        max_tax=_money(data.get("max_tax")),
        trade_credit=str(data.get("trade_credit") or TRADE_FULL),
        trade_credit_cap=_money(data.get("trade_credit_cap")),
        rebate_before_tax=bool(data.get("rebate_before_tax", False)),
        taxed=frozenset(data.get("taxed") or ()),
        lease_tax=str(data.get("lease_tax") or LEASE_MONTHLY),
        lease_reduction_taxed=bool(data.get("lease_reduction_taxed", False)),
    )

