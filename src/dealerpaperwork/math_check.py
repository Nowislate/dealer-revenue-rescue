"""Setup's math check: prove the app against the store's own DMS.

Decision 21 (Lane, 2026-11-19). Before setup can finish, a manager
types in one recent real deal from the store's own DMS -- the deal's
inputs and the DMS's own answers -- and the app shows, line by line,
whether it gets the same figures to the cent:

    retail:  total sales tax, amount financed, monthly payment
    lease:   base monthly payment, monthly tax, monthly payment

Setup cannot finish until every line matches, or a named manager
records why not. That record is audit-logged and kept in the local
setup file; it never leaves the machine. A store that leases does the
same for one lease.

The check runs the exact code a deal runs: the retail tax engine plus
the deal flow's financing section (payoff netted, Regulation Z odd
days), and the lease engine. It uses the store's own entered fees and
county rates, so a wrong county rate or a wrong fee shows up here,
before the first real deal. Florida only, like the engines.

This module is UI-independent and standard library only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

from .models import ZERO, Buyer, DealInputs, LeaseInputs, TradeIn

KIND_RETAIL = "retail"
KIND_LEASE = "lease"
# Decision 22: a store with typed rules proves a second retail deal,
# one WITH a trade (the plain retail check deal then has none).
KIND_RETAIL_TRADE = "retail_trade"

# A reason shorter than this is not a reason (e.g. "ok", "n/a").
MIN_OVERRIDE_REASON = 15


def parse_money(text, *, label: str, required: bool = False) -> Decimal:
    """Parse a typed amount ("1,199.00", "$85") to Decimal; blank = 0."""
    raw = ("" if text is None else str(text)).strip().replace("$", "").replace(",", "")
    if not raw:
        if required:
            raise ValueError(f"{label} is blank.")
        return ZERO
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"{label}: {text!r} is not an amount.") from None
    if not value.is_finite():
        raise ValueError(f"{label}: {text!r} is not an amount.")
    return value


def parse_percent(text, *, label: str) -> Decimal:
    """Parse a typed percent ("7.49", "7.49%") to a fraction (0.0749)."""
    raw = ("" if text is None else str(text)).strip().replace("%", "")
    if not raw:
        raise ValueError(f"{label} is blank.")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"{label}: {text!r} is not a percent.") from None
    if not value.is_finite() or value < 0:
        raise ValueError(f"{label}: {text!r} is not a percent.")
    return value / Decimal(100)


def parse_whole(text, *, label: str, default: Optional[int] = None) -> int:
    raw = ("" if text is None else str(text)).strip()
    if not raw:
        if default is not None:
            return default
        raise ValueError(f"{label} is blank.")
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f"{label}: {text!r} is not a whole number above zero.")
    return int(raw)


@dataclass
class RetailCheckEntry:
    """One recent retail finance deal as the store's DMS shows it.

    The dealer fee and electronic tag fee are NOT entered here: the
    check uses the fees the store entered in Step 0.2, so a wrong fee
    shows up as a mismatch.
    """

    county: str = ""
    selling_price: Decimal = ZERO
    accessories: Decimal = ZERO
    other_taxable_fees: Decimal = ZERO
    dealer_discount: Decimal = ZERO
    trade_allowance: Decimal = ZERO
    trade_payoff: Decimal = ZERO
    government_fees: Decimal = ZERO
    optional_products: Decimal = ZERO
    manufacturer_rebate: Decimal = ZERO
    cash_down_payment: Decimal = ZERO
    term_months: int = 0
    annual_rate: Decimal = ZERO  # fraction: 0.0749 = 7.49% APR
    days_to_first_payment: int = 30
    # The DMS's own answers.
    dms_total_tax: Decimal = ZERO
    dms_amount_financed: Decimal = ZERO
    dms_monthly_payment: Decimal = ZERO


@dataclass
class LeaseCheckEntry:
    """One recent lease as the store's DMS lease worksheet shows it."""

    county: str = ""
    msrp: Decimal = ZERO
    dealer_discount: Decimal = ZERO
    aftermarkets: Decimal = ZERO
    acquisition_fee: Decimal = ZERO
    cap_fees: Decimal = ZERO
    license_fee: Decimal = ZERO
    other_dealer_fees: Decimal = ZERO
    cash_down: Decimal = ZERO
    total_rebates: Decimal = ZERO
    trade_allowance: Decimal = ZERO
    trade_payoff: Decimal = ZERO
    residual_base_msrp: Decimal = ZERO
    residual_percent: Decimal = ZERO  # fraction: 0.61 = 61%
    money_factor: Decimal = ZERO
    term_months: int = 0
    # The DMS's own answers.
    dms_base_payment: Decimal = ZERO
    dms_monthly_tax: Decimal = ZERO
    dms_monthly_payment: Decimal = ZERO


@dataclass
class CheckLine:
    label: str
    app: Optional[Decimal]
    dms: Decimal

    @property
    def matches(self) -> bool:
        return self.app is not None and self.app == self.dms


@dataclass
class MathComparison:
    """The line-by-line result of one check."""

    kind: str
    lines: list[CheckLine] = field(default_factory=list)
    # Problems that stop the check from running at all (e.g. the
    # deal's county is not in the store's rate table).
    errors: list[str] = field(default_factory=list)
    fingerprint: str = ""

    @property
    def ran(self) -> bool:
        return not self.errors and bool(self.lines)

    @property
    def all_match(self) -> bool:
        return self.ran and all(line.matches for line in self.lines)

    @property
    def mismatches(self) -> list[str]:
        return [line.label for line in self.lines if not line.matches]

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "errors": list(self.errors),
            "lines": [
                {
                    "label": line.label,
                    "app": None if line.app is None else str(line.app),
                    "dms": str(line.dms),
                    "matches": line.matches,
                }
                for line in self.lines
            ],
            "all_match": self.all_match,
            "fingerprint": self.fingerprint,
        }


def entry_to_dict(entry) -> dict:
    return {
        key: (str(value) if isinstance(value, Decimal) else value)
        for key, value in asdict(entry).items()
    }


_DECIMAL_FIELDS = {
    KIND_RETAIL: RetailCheckEntry,
    KIND_RETAIL_TRADE: RetailCheckEntry,
    KIND_LEASE: LeaseCheckEntry,
}


def entry_from_dict(kind: str, data: dict):
    cls = _DECIMAL_FIELDS[kind]
    template = cls()
    values = {}
    for key, default in asdict(template).items():
        if key not in data:
            continue
        raw = data[key]
        if isinstance(default, Decimal):
            values[key] = Decimal(str(raw))
        elif isinstance(default, int):
            values[key] = int(raw)
        else:
            values[key] = raw
    return cls(**values)


def _fingerprint(kind: str, entry, lines: list[CheckLine], tax_context: dict) -> str:
    payload = {
        "kind": kind,
        "entry": entry_to_dict(entry),
        "tax": tax_context,
        "app": [None if l.app is None else str(l.app) for l in lines],
    }
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _county_rate(preset, county: str):
    county = (county or "").strip()
    if not preset.needs_buyer_place:
        # Decision 22: the dealership's place (or no local tax) picks it.
        try:
            return preset.local_rate_for(county), None
        except KeyError as exc:
            return None, str(exc.args[0])
    if not county:
        return None, "Type the buyer's residence county for the check deal."
    if county not in preset.county_surtax_rates:
        return None, (
            f"County {county!r} is not in your county rate table. Add it on "
            "the tax rates step, spelled the same way, then run the check again."
        )
    return preset.county_surtax_rates[county], None


def _tax_context(preset, county: str) -> dict:
    rate, _problem = _county_rate(preset, county)
    dealer = preset.dealer
    context = {
        "state_tax_rate": str(preset.state_tax_rate),
        "county_rate": None if rate is None else str(rate),
        "dealer_fee": None if dealer.dealer_fee is None else str(dealer.dealer_fee),
        "electronic_tag_fee": (
            None if dealer.electronic_tag_fee is None else str(dealer.electronic_tag_fee)
        ),
    }
    # A typed rule sheet is part of what the check proves: change a rule
    # and the check opens again (Decision 22). Florida's built-in sheet
    # never changes, so its context stays as it was.
    if not preset.rules.built_in:
        from .state_rules import rules_to_dict
        context["state_rules"] = rules_to_dict(preset.rules)
    return context


def compare_retail(entry: RetailCheckEntry, preset) -> MathComparison:
    """Run the check deal through the same code a real deal runs."""
    # Imported here: deal_flow imports setup_wizard, which imports us.
    from .deal_flow import compute_financing
    from .tax_engine import calculate_retail_deal

    result = MathComparison(kind=KIND_RETAIL)
    rate, problem = _county_rate(preset, entry.county)
    if problem:
        result.errors.append(problem)
    if not entry.term_months or entry.term_months <= 0:
        result.errors.append("Term (months) is blank.")
    if entry.selling_price <= ZERO:
        result.errors.append("Selling price is blank.")
    if result.errors:
        return result

    dealer = preset.dealer
    inputs = DealInputs(
        buyer=Buyer(full_name="Setup math check", residence_county=entry.county.strip()),
        trade=TradeIn(allowance=entry.trade_allowance, payoff=entry.trade_payoff)
        if (entry.trade_allowance or entry.trade_payoff)
        else None,
        selling_price=entry.selling_price,
        accessories=entry.accessories,
        dealer_fee=dealer.dealer_fee or ZERO,
        other_taxable_fees=entry.other_taxable_fees,
        dealer_discount=entry.dealer_discount,
        county_surtax_rate=rate,
        government_fees=entry.government_fees,
        electronic_tag_fee=dealer.electronic_tag_fee or ZERO,
        optional_products=entry.optional_products,
        manufacturer_rebate=entry.manufacturer_rebate,
        cash_down_payment=entry.cash_down_payment,
        term_months=entry.term_months,
        annual_rate=entry.annual_rate,
        days_to_first_payment=entry.days_to_first_payment,
    )
    deal = calculate_retail_deal(inputs, preset=preset)
    financing = compute_financing(inputs, deal)
    result.lines = [
        CheckLine("Total sales tax", deal.total_tax, entry.dms_total_tax),
        CheckLine("Amount financed", financing.amount_financed, entry.dms_amount_financed),
        CheckLine("Monthly payment", financing.monthly_payment, entry.dms_monthly_payment),
    ]
    result.fingerprint = _fingerprint(
        KIND_RETAIL, entry, result.lines, _tax_context(preset, entry.county)
    )
    return result


def compare_lease(entry: LeaseCheckEntry, preset) -> MathComparison:
    from .lease_engine import calculate_lease

    result = MathComparison(kind=KIND_LEASE)
    rate, problem = _county_rate(preset, entry.county)
    if problem:
        result.errors.append(problem)
    if not entry.term_months or entry.term_months <= 0:
        result.errors.append("Lease term (months) is blank.")
    if entry.msrp <= ZERO:
        result.errors.append("MSRP is blank.")
    if not (ZERO < entry.residual_percent <= Decimal("1")):
        result.errors.append("Residual percent must be above 0% and at most 100%.")
    if entry.money_factor < ZERO:
        result.errors.append("Money factor cannot be negative.")
    if result.errors:
        return result

    dealer = preset.dealer
    inputs = LeaseInputs(
        buyer=Buyer(full_name="Setup math check", residence_county=entry.county.strip()),
        msrp=entry.msrp,
        dealer_discount=entry.dealer_discount,
        aftermarkets=entry.aftermarkets,
        # The lease flow fills the predelivery line from the store's
        # dealer fee; the check does the same.
        predelivery_fee=dealer.dealer_fee or ZERO,
        acquisition_fee=entry.acquisition_fee,
        license_fee=entry.license_fee,
        dealer_fees=entry.other_dealer_fees,
        cap_fees=entry.cap_fees,
        cash_down=entry.cash_down,
        total_rebates=entry.total_rebates,
        trade_allowance=entry.trade_allowance,
        trade_payoff=entry.trade_payoff,
        residual_base_msrp=entry.residual_base_msrp or entry.msrp,
        residual_percent=entry.residual_percent,
        term_months=entry.term_months,
        money_factor=entry.money_factor,
        county_surtax_rate=rate,
    )
    lease = calculate_lease(inputs, preset=preset)
    result.lines = [
        CheckLine("Base monthly payment", lease.base_monthly_payment, entry.dms_base_payment),
        CheckLine("Monthly tax", lease.monthly_tax, entry.dms_monthly_tax),
        CheckLine("Monthly payment", lease.monthly_payment, entry.dms_monthly_payment),
    ]
    result.fingerprint = _fingerprint(
        KIND_LEASE, entry, result.lines, _tax_context(preset, entry.county)
    )
    return result


def comparison_text(comparison: MathComparison) -> str:
    """Plain lines for the setup screen: one row per figure."""
    if comparison.errors:
        return "\n".join(comparison.errors)
    rows = []
    for line in comparison.lines:
        app = "--" if line.app is None else f"{line.app:,.2f}"
        mark = "MATCH" if line.matches else "DIFFERENT"
        rows.append(f"{line.label:<22} app {app:>12}   DMS {line.dms:>12,.2f}   {mark}")
    if comparison.all_match:
        rows.append("Every figure matches your DMS to the cent.")
    else:
        rows.append(
            "Some figures differ. Check the numbers you typed, your fees "
            "and your county rates. If the app is right, a manager records why."
        )
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Typed text -> entries. The setup window collects plain strings; these
# turn them into entries (and are tested headless, without Tk).
# ---------------------------------------------------------------------------

# (field, label, kind). kind: text | money | money* (required) |
# percent | whole | days | factor
RETAIL_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("county", "Buyer's county *", "text"),
    ("selling_price", "Selling price *", "money*"),
    ("accessories", "Accessories and add-ons", "money"),
    ("other_taxable_fees", "Other taxable fees", "money"),
    ("dealer_discount", "Dealer discount", "money"),
    ("trade_allowance", "Trade allowance", "money"),
    ("trade_payoff", "Trade payoff", "money"),
    ("government_fees", "Title, plate, state fees", "money"),
    ("optional_products", "Products sold (service, GAP)", "money"),
    ("manufacturer_rebate", "Manufacturer rebate", "money"),
    ("cash_down_payment", "Cash down payment", "money"),
    ("term_months", "Term (months) *", "whole"),
    ("annual_rate", "APR (%) *", "percent"),
    ("days_to_first_payment", "Days to first payment", "days"),
)
RETAIL_DMS_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("dms_total_tax", "DMS total sales tax *", "money*"),
    ("dms_amount_financed", "DMS amount financed *", "money*"),
    ("dms_monthly_payment", "DMS monthly payment *", "money*"),
)
LEASE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("county", "Lessee's county *", "text"),
    ("msrp", "MSRP *", "money*"),
    ("dealer_discount", "Dealer discount", "money"),
    ("aftermarkets", "Aftermarkets", "money"),
    ("acquisition_fee", "Acquisition fee", "money"),
    ("cap_fees", "Capitalized fees", "money"),
    ("license_fee", "License fee", "money"),
    ("other_dealer_fees", "Other dealer fees", "money"),
    ("cash_down", "Cash down", "money"),
    ("total_rebates", "Rebates", "money"),
    ("trade_allowance", "Trade allowance", "money"),
    ("trade_payoff", "Trade payoff", "money"),
    ("residual_base_msrp", "Residual MSRP (blank = MSRP)", "money"),
    ("residual_percent", "Residual (%) *", "percent"),
    ("money_factor", "Money factor *", "factor"),
    ("term_months", "Term (months) *", "whole"),
)
LEASE_DMS_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("dms_base_payment", "DMS base monthly payment *", "money*"),
    ("dms_monthly_tax", "DMS monthly tax *", "money*"),
    ("dms_monthly_payment", "DMS monthly payment *", "money*"),
)


def _parse_fields(cls, specs, texts: dict):
    values = {}
    errors = []
    for key, label, kind in specs:
        raw = texts.get(key, "")
        label = label.rstrip(" *")
        try:
            if kind == "text":
                value = (raw or "").strip()
                if not value:
                    raise ValueError(f"{label} is blank.")
            elif kind in ("money", "money*"):
                value = parse_money(raw, label=label, required=kind == "money*")
            elif kind == "percent":
                value = parse_percent(raw, label=label)
            elif kind == "whole":
                value = parse_whole(raw, label=label)
            elif kind == "days":
                value = parse_whole(raw, label=label, default=30)
            elif kind == "factor":
                value = parse_money(raw, label=label, required=True)
            else:  # pragma: no cover - spec typo
                raise ValueError(f"Unknown field kind {kind!r}.")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        values[key] = value
    if errors:
        raise ValueError("\n".join(errors))
    return cls(**values)


def retail_entry_from_text(texts: dict) -> RetailCheckEntry:
    return _parse_fields(RetailCheckEntry, RETAIL_FIELDS + RETAIL_DMS_FIELDS, texts)


def lease_entry_from_text(texts: dict) -> LeaseCheckEntry:
    return _parse_fields(LeaseCheckEntry, LEASE_FIELDS + LEASE_DMS_FIELDS, texts)


def entry_to_text(entry, specs) -> dict:
    """The reverse, to refill the form: fractions shown as percents."""
    texts = {}
    for key, _label, kind in specs:
        value = getattr(entry, key)
        if kind == "percent":
            texts[key] = format((value * 100).normalize(), "f")
        else:
            texts[key] = str(value)
    return texts


def parse_county_table(text: str) -> dict[str, Decimal]:
    """One county per line, "County, rate in percent": "Pinellas, 1.5".

    Rates are typed as percents, as the DR-15DSS prints them, and
    stored as fractions. Blank lines are skipped. Every bad line is
    reported by number.
    """
    rates: dict[str, Decimal] = {}
    errors = []
    for number, line in enumerate((text or "").splitlines(), start=1):
        if not line.strip():
            continue
        if "," not in line:
            errors.append(f"Line {number}: write it as County, rate (e.g. Pinellas, 1.5).")
            continue
        county, _, rate_text = line.rpartition(",")
        county = county.strip()
        if not county:
            errors.append(f"Line {number}: the county name is blank.")
            continue
        try:
            rate = parse_percent(rate_text, label=f"Line {number} rate")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if county in rates:
            errors.append(f"Line {number}: {county} is listed twice.")
            continue
        rates[county] = rate
    if errors:
        raise ValueError("\n".join(errors))
    return rates


def county_table_text(rates: dict[str, Decimal]) -> str:
    return "\n".join(
        f"{county}, {format((rate * 100).normalize(), 'f')}"
        for county, rate in sorted(rates.items())
    )
