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

# Questions 13-17 (Lane, 2026-11-24, decision 30): the state tax shapes
# the first twelve questions could not say. Each is optional; a sheet
# that leaves them blank runs exactly as before.

# Question 17: tax on a state-set value instead of the price.
VALUE_PRICE = "price"  # the usual: tax the price
VALUE_STATE = "state_value"  # tax the state's value for the vehicle
VALUE_GREATER = "greater"  # tax the greater of price and state value
VALUE_RULES = (VALUE_PRICE, VALUE_STATE, VALUE_GREATER)


@dataclass(frozen=True)
class TaxBand:
    """Question 13: an extra state tax on one slice of the taxable
    amount, from ``low`` up to ``high`` dollars (None: no top).
    Tennessee's single article tax is one band: 2.75% from $1,600 to
    $3,200."""

    rate: Decimal
    low: Decimal
    high: Optional[Decimal] = None

    def tax_on(self, base: Decimal) -> Decimal:
        top = base if self.high is None else min(base, self.high)
        return max(top - self.low, Decimal("0")) * self.rate


@dataclass(frozen=True)
class RateBracket:
    """Question 14: the state rate when the selling price is at least
    ``from_price``. Arkansas used cars: none under $4,000, a lower rate
    from $4,000 to $10,000, the full rate from $10,000."""

    from_price: Decimal
    rate: Decimal


@dataclass(frozen=True)
class TaxClass:
    """Question 15: a vehicle class with its own state rate, picked on
    the deal. Mississippi: trucks over 10,000 lb GVW at 3%."""

    name: str
    rate: Decimal


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
    # Questions 13-17 (retail deals). Empty means the shape is not used.
    bands: tuple = ()
    brackets: tuple = ()
    brackets_used_only: bool = False
    classes: tuple = ()
    min_tax: Optional[Decimal] = None
    value_rule: str = VALUE_PRICE
    value_used_only: bool = True

    def state_rate_for(self, base_rate: Decimal, *, price: Decimal,
                       is_new: bool, tax_class: str = "") -> Decimal:
        """The state rate for one deal: its vehicle class's rate, else
        the price bracket's, else the preset's state rate."""
        if tax_class:
            for item in self.classes:
                if item.name == tax_class:
                    return item.rate
            raise ValueError(
                f"The vehicle class {tax_class!r} is not on the rule sheet.")
        if self.brackets and not (self.brackets_used_only and is_new):
            rate = None
            for bracket in sorted(self.brackets, key=lambda b: b.from_price):
                if price >= bracket.from_price:
                    rate = bracket.rate
            if rate is not None:
                return rate
        return base_rate

    @property
    def uses_vehicle_details(self) -> bool:
        """Whether a deal must say new or used, its class or its state
        value for this sheet's tax (questions 14, 15 and 17)."""
        return bool(self.brackets or self.classes or self.value_rule != VALUE_PRICE)

    def needs_state_value(self, is_new: bool) -> bool:
        return self.value_rule != VALUE_PRICE and not (self.value_used_only and is_new)

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
    errors += _shape_errors(rules)
    return errors


def _rate_ok(rate) -> bool:
    return isinstance(rate, Decimal) and Decimal("0") <= rate < Decimal("1")


def _shape_errors(rules: StateRules) -> list[str]:
    """Plain problems with questions 13-17."""
    errors: list[str] = []
    for band in rules.bands:
        if not _rate_ok(band.rate) or band.low < 0 or (
                band.high is not None and band.high <= band.low):
            errors.append(
                "Rule sheet 13: each extra tax needs a rate and a dollar "
                "range whose top is above its bottom.")
            break
    if rules.brackets:
        froms = [b.from_price for b in rules.brackets]
        if any(not _rate_ok(b.rate) or b.from_price < 0 for b in rules.brackets):
            errors.append("Rule sheet 14: each price bracket needs a price "
                          "and a rate from 0 to 99.99%.")
        elif min(froms) != 0:
            errors.append("Rule sheet 14: the first price bracket must start "
                          "at $0, so every price has a rate.")
        elif len(set(froms)) != len(froms):
            errors.append("Rule sheet 14: two price brackets start at the same price.")
    names = [c.name for c in rules.classes]
    if any(not n.strip() or not _rate_ok(c.rate) for n, c in zip(names, rules.classes)):
        errors.append("Rule sheet 15: each vehicle class needs a name and a rate.")
    elif len(set(names)) != len(names):
        errors.append("Rule sheet 15: two vehicle classes have the same name.")
    if rules.min_tax is not None and rules.min_tax <= 0:
        errors.append("Rule sheet 16: the least tax per deal must be more than $0.")
    if (rules.min_tax is not None and rules.max_tax is not None
            and rules.min_tax > rules.max_tax):
        errors.append("Rule sheet 16: the least tax is more than the most tax.")
    if rules.value_rule not in VALUE_RULES:
        errors.append(f"Rule sheet 17: unknown value rule {rules.value_rule!r}.")
    if rules.tax_kind == TAX_NONE and (rules.bands or rules.brackets or rules.classes
                                        or rules.min_tax is not None
                                        or rules.value_rule != VALUE_PRICE):
        errors.append("Rule sheet: questions 13-17 need a state that taxes the sale.")
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


# Questions 13-17 at their blank answers. They are left out of the saved
# sheet, so a sheet saved before decision 30 reads, saves and
# fingerprints exactly as it did: no approval or math check reopens.
_SHAPE_BLANK = {"bands": [], "brackets": [], "brackets_used_only": False,
                "classes": [], "min_tax": None, "value_rule": VALUE_PRICE,
                "value_used_only": True}


def rules_to_dict(rules: StateRules) -> dict:
    data = _rules_dict(rules)
    return {key: value for key, value in data.items()
            if not (key in _SHAPE_BLANK and value == _SHAPE_BLANK[key])}


def _rules_dict(rules: StateRules) -> dict:
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
        "bands": [{"rate": str(b.rate), "low": str(b.low),
                   "high": None if b.high is None else str(b.high)}
                  for b in rules.bands],
        "brackets": [{"from_price": str(b.from_price), "rate": str(b.rate)}
                     for b in rules.brackets],
        "brackets_used_only": rules.brackets_used_only,
        "classes": [{"name": c.name, "rate": str(c.rate)} for c in rules.classes],
        "min_tax": None if rules.min_tax is None else str(rules.min_tax),
        "value_rule": rules.value_rule,
        "value_used_only": rules.value_used_only,
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
        bands=tuple(TaxBand(D(b["rate"]), D(b["low"]), _money(b.get("high")))
                    for b in data.get("bands") or ()),
        brackets=tuple(RateBracket(D(b["from_price"]), D(b["rate"]))
                       for b in data.get("brackets") or ()),
        brackets_used_only=bool(data.get("brackets_used_only", False)),
        classes=tuple(TaxClass(str(c["name"]), D(c["rate"]))
                      for c in data.get("classes") or ()),
        min_tax=_money(data.get("min_tax")),
        value_rule=str(data.get("value_rule") or VALUE_PRICE),
        value_used_only=bool(data.get("value_used_only", True)),
    )

