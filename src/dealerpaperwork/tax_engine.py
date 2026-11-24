"""Florida retail calculation engine (build specification, Section 9).

Deterministic code only -- no AI decides tax, payment, or APR. The engine
applies the dealer-approved presets; the numbers it produces are the only
numbers that flow onto documents in later stages.

The 12-step retail order from spec Section 9 is implemented literally,
one step at a time, in ``calculate_retail_deal``. Mapping notes where the
spec wording needed pinning down:

* Step 3 adds TAXABLE dealer-required fees (the dealer fee and similar
  buyer-must-pay preparation/settlement/closing/handling/delivery
  charges). State-mandated title/registration/lien fees are NOT added
  here; they enter untaxed at step 11.
* The electronic tag fee IS taxed (Decision 14, Lane 2026-11-02): it
  is a dealer charge for a private tag service, not a government
  agency fee, so it joins the step 3 taxable dealer-required charges
  in the taxable base. (The spec's original treatment -- fee added
  after tax at step 12, per the Section 9 fixture's "not used in the
  tax lines" wording -- is superseded.) It is never presented as a
  step 11 government fee.
* Step 6 is a negative rule: manufacturer rebate, cash down payment,
  amount financed, and trade lien payoff are NOT subtracted before tax.
  The rebate is applied at step 12, after tax, as a payment.
* Step 9 surtax: applied to the first $5,000 of the taxable base at the
  buyer's residence-county rate. NOTE (spec Section 16): whether a trade
  that drops the base below $5,000 changes the surtax base is flagged
  unverified. Stage 1 follows the plain rule (surtax on the reduced
  base, capped at $5,000) and attaches a warning to the result whenever
  the base is below $5,000, so a later stage can flag the deal for human
  confirmation instead of the engine deciding silently.
* Step 12 is implemented exactly as the spec lists it. The trade lien
  payoff is carried on the inputs and shown on the worksheet as the
  net-trade line (allowance - payoff) for transparency; per the spec's
  step-12 list it does not change the step-12 total in Stage 1.

Rounding: state tax and county surtax are each rounded to cents
(ROUND_HALF_UP) at steps 8 and 9 -- the steps where the spec fixes
amounts -- and every money line is carried at cents thereafter.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from .models import ZERO, DealInputs, DealResult
from .presets import Preset, require_approved
from .state_rules import (
    CHARGE_ACCESSORIES,
    CHARGE_DEALER_FEE,
    CHARGE_GOVERNMENT_FEES,
    CHARGE_PRODUCTS,
    CHARGE_TAG_FEE,
    FLORIDA,
    TAX_NONE,
    VALUE_GREATER,
    VALUE_STATE,
    StateRules,
    trade_credit,
)


class TaxInputError(ValueError):
    """The deal lacks a figure the store's rule sheet needs (decision
    30): the vehicle's state value, or a class the sheet does not list.
    Nothing is guessed; the worksheet stops with a plain message."""

CENTS = Decimal("0.01")
SURTAX_CAP = Decimal("5000")
DEFAULT_STATE_RATE = Decimal("0.06")


def cents(amount: Decimal) -> Decimal:
    """Round a money amount to cents, half-up (standard retail rounding)."""
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def installment_terms(
    principal: Decimal,
    annual_rate: Decimal,
    term_months: int,
    days_to_first_payment: Optional[int] = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """Monthly payment, total of payments and finance charge.

    Equal monthly payments, interest at ``annual_rate / 12`` per
    month. The first period runs ``days_to_first_payment`` days (None
    = 30, one regular month). It is split into whole 30-day months
    plus a fraction ``f`` of a month carrying simple interest, the
    federal Truth in Lending actuarial method (Regulation Z, Appendix
    J): the amount financed equals each payment discounted by
    ``(1 + f*i) * (1 + i) ** (q + t - 1)``. This reproduces the DMS
    retail worksheet to the cent, including a 45-day first payment.
    The finance charge is total of payments minus the principal, so
    every cent of payment rounding lands in it.
    """
    if term_months is None or term_months <= 0:
        raise ValueError("Term must be a positive number of months.")
    days = 30 if days_to_first_payment is None else int(days_to_first_payment)
    if days <= 0:
        raise ValueError("Days to first payment must be positive.")
    principal = cents(principal)
    i = annual_rate / Decimal("12")
    if i == ZERO:
        payment = cents(principal / term_months)
    else:
        # days / 30 = q whole months + fraction f. The first payment's
        # discount is (1 + f*i) * (1 + i) ** q, later ones one more
        # month each; with the annuity counted from t = 1 that leaves
        # (1 + i) ** (q - 1). q = 1, f = 0 is the ordinary case.
        q, extra = divmod(days, 30)
        f = Decimal(extra) / Decimal(30)
        whole = q - 1
        one = Decimal("1")
        annuity = (one - (one + i) ** -term_months) / i
        payment = cents(principal * (one + f * i) * (one + i) ** whole / annuity)
    total = cents(payment * term_months)
    return payment, total, cents(total - principal)


def _pct(rate: Decimal) -> str:
    text = format((rate * 100).normalize(), "f")
    return f"{text}%"


def _taxed_word(rules: StateRules, charge: str) -> str:
    return "taxed" if rules.is_taxed(charge) else "not taxed"


def _sheet_lines(rules: StateRules, inputs: DealInputs, **v) -> tuple:
    """Worksheet lines for a store's own rule sheet (Decision 22).

    The same order as the Florida worksheet, with each line saying how
    the sheet treats it, so the printed worksheet can be checked
    against the store's DMS line by line.
    """
    lines = [
        ("Vehicle selling price", cents(v["price"])),
        (f"Accessories and add-ons ({_taxed_word(rules, CHARGE_ACCESSORIES)})",
         cents(inputs.accessories)),
        (f"Dealer-required fees ({_taxed_word(rules, CHARGE_DEALER_FEE)})",
         cents(v["taxable_fees"])),
        (f"Electronic tag fee ({_taxed_word(rules, CHARGE_TAG_FEE)})",
         v["electronic_tag_fee"]),
        ("Gross sales price", cents(v["gross"])),
        ("Dealer discount", cents(-inputs.dealer_discount)),
        ("Trade-in allowance", cents(-v["trade_allowance"])),
        ("Trade-in credit against tax", cents(-v["credit"])),
    ]
    if rules.rebate_before_tax:
        lines.append(("Manufacturer rebate (before tax)",
                      cents(-inputs.manufacturer_rebate)))
    if v["value_adjust"]:
        word = ("State value replaces the price" if rules.value_rule == VALUE_STATE
                else "State value is above the price")
        lines.append((f"{word} (state value ${v['state_value']:,.2f})",
                      v["value_adjust"]))
    rate_note = ""
    if inputs.vehicle.tax_class:
        rate_note = f", {inputs.vehicle.tax_class}"
    elif rules.brackets and v["state_rate"] != v["base_rate"]:
        rate_note = ", price bracket"
    lines += [
        ("Taxable base", v["taxable_base"]),
        (f"State tax ({_pct(v['state_rate'])}{rate_note})", v["state_tax"]),
    ]
    for band, amount in zip(rules.bands, v["band_taxes"]):
        top = "and up" if band.high is None else f"to ${band.high:,.2f}"
        lines.append((f"Extra state tax ({_pct(band.rate)} on ${band.low:,.2f} {top})",
                      amount))
    lines += [
        ("Local tax base" + (
            f" (first ${rules.local_cap:,.0f})" if rules.local_cap is not None else ""),
         v["surtax_base"]),
        ("Local tax", v["county_surtax"]),
    ]
    if rules.max_tax is not None:
        lines.append((f"Tax limit (most ${rules.max_tax:,.2f})", cents(-v["tax_limit_cut"])))
    if rules.min_tax is not None:
        lines.append((f"Least tax (${rules.min_tax:,.2f})", v["min_tax_add"]))
    lines += [
        ("Total sales tax", v["total_tax"]),
        (f"Government fees ({_taxed_word(rules, CHARGE_GOVERNMENT_FEES)})",
         v["government_fees"]),
        (f"Optional products elected ({_taxed_word(rules, CHARGE_PRODUCTS)})",
         cents(inputs.optional_products)),
        ("Trade lien payoff", cents(v["trade_payoff"])),
        ("Net trade (allowance - payoff)",
         cents(v["trade_allowance"] - v["trade_payoff"])),
    ]
    if not rules.rebate_before_tax:
        lines.append(("Manufacturer rebate (after tax)",
                      cents(-inputs.manufacturer_rebate)))
    lines += [
        ("Cash down payment", cents(-inputs.cash_down_payment)),
        ("Amount due (payoff not netted)", v["amount_due"]),
    ]
    return tuple(lines)


def calculate_retail_deal(
    inputs: DealInputs,
    *,
    preset: Optional[Preset] = None,
    certify: bool = False,
) -> DealResult:
    """Run the spec Section 9 retail calculation.

    ``certify=True`` means the result will be certified for clean
    printing (spec Section 8): that requires an approved, valid preset
    set, enforced by ``presets.require_approved``. Draft/estimate
    calculations (``certify=False``) may run without one.
    """
    if certify:
        require_approved(preset)

    state_rate = preset.state_tax_rate if preset is not None else DEFAULT_STATE_RATE
    # Decision 22: the store's rule sheet says which charges are taxed,
    # how the trade counts, and any limits. Florida is the built-in
    # sheet; with it every step below is the spec Section 9 order.
    rules = preset.rules if preset is not None else FLORIDA
    vehicle = inputs.vehicle
    base_rate = state_rate
    if rules.tax_kind == TAX_NONE:
        state_rate = ZERO
    else:
        # Questions 14 and 15: a vehicle class or a price bracket can set
        # the state rate. The bracket reads the selling price after the
        # dealer's own discount.
        try:
            state_rate = rules.state_rate_for(
                state_rate, price=inputs.selling_price - inputs.dealer_discount,
                is_new=vehicle.is_new, tax_class=vehicle.tax_class)
        except ValueError as exc:
            raise TaxInputError(str(exc)) from None
    florida = rules.built_in and rules.state == "FL"
    taxed = rules.is_taxed

    # --- The 12 steps, in spec order -------------------------------------
    # Step 1: vehicle selling price (includes destination/freight).
    price = inputs.selling_price
    # Step 2: add dealer-installed accessories and add-ons.
    with_accessories = price + inputs.accessories
    # Step 3: add dealer-required fees (dealer fee + other preparation/
    # settlement/closing/handling/delivery charges). State-mandated
    # title/registration/lien fees are NOT added here.
    taxable_fees = inputs.dealer_fee + inputs.other_taxable_fees
    # The electronic tag fee (Decision 14): a private tag service
    # charged by the dealer, not a government agency fee. It enters
    # the gross price here, as its own worksheet line below.
    electronic_tag_fee = cents(inputs.electronic_tag_fee)
    # Step 4: gross sales price.
    gross = with_accessories + taxable_fees + electronic_tag_fee
    # Step 5: subtract the dealer's own discount given at the time of sale.
    after_discount = gross - inputs.dealer_discount
    # Step 6 (Florida): do NOT subtract manufacturer rebate, cash down
    # payment, amount financed, or the trade lien payoff. A rule sheet
    # may take the rebate off before tax (question 8).
    # Step 7: subtract the trade-in credit -> taxable base.
    trade_allowance = inputs.trade.allowance if inputs.trade else ZERO
    trade_payoff = inputs.trade.payoff if inputs.trade else ZERO
    credit = trade_credit(rules, trade_allowance)
    # Charges the sheet leaves untaxed come back out of the base; taxed
    # products and government fees go in (question 9).
    untaxed_in_gross = ZERO
    if not taxed(CHARGE_ACCESSORIES):
        untaxed_in_gross += inputs.accessories
    if not taxed(CHARGE_DEALER_FEE):
        untaxed_in_gross += taxable_fees
    if not taxed(CHARGE_TAG_FEE):
        untaxed_in_gross += electronic_tag_fee
    taxed_after_gross = ZERO
    if taxed(CHARGE_PRODUCTS):
        taxed_after_gross += inputs.optional_products
    if taxed(CHARGE_GOVERNMENT_FEES):
        taxed_after_gross += inputs.government_fees
    taxable_base = after_discount - untaxed_in_gross + taxed_after_gross - credit
    # Question 17: the state's value for the vehicle in place of the
    # price (after the dealer discount), or the greater of the two.
    value_adjust = ZERO
    state_value = vehicle.state_value
    if rules.needs_state_value(vehicle.is_new):
        if state_value is None or state_value <= ZERO:
            raise TaxInputError(
                "Enter the vehicle's state value. Your state taxes this "
                "vehicle on the value the state sets; look it up in the "
                "state's own tool.")
        sold_for = price - inputs.dealer_discount
        taxed_value = state_value if rules.value_rule == VALUE_STATE else max(
            sold_for, state_value) if rules.value_rule == VALUE_GREATER else sold_for
        value_adjust = cents(taxed_value - sold_for)
        taxable_base += value_adjust
    if rules.rebate_before_tax:
        taxable_base -= inputs.manufacturer_rebate
    if taxable_base < ZERO:
        taxable_base = ZERO
    taxable_base = cents(taxable_base)
    # Step 8: state tax = state rate x the taxable base (rate from preset data).
    state_tax = cents(taxable_base * state_rate)
    # Question 13: extra state tax on slices of the taxable base.
    band_taxes = [ZERO if rules.tax_kind == TAX_NONE else cents(band.tax_on(taxable_base))
                  for band in rules.bands]
    # Step 9: local tax at the buyer's (or the dealership's) place's
    # rate; Florida applies it only to the first $5,000 of the base.
    local_rate = ZERO if rules.tax_kind == TAX_NONE else inputs.county_surtax_rate
    surtax_base = (
        min(taxable_base, rules.local_cap) if rules.local_cap is not None
        else taxable_base
    )
    county_surtax = cents(surtax_base * local_rate)
    # Step 10: total sales tax, held to the sheet's most-tax limit.
    total_tax = state_tax + sum(band_taxes, ZERO) + county_surtax
    tax_limit_cut = ZERO
    if rules.max_tax is not None and total_tax > rules.max_tax:
        tax_limit_cut = cents(total_tax - rules.max_tax)
        total_tax = cents(rules.max_tax)
    # Question 16: the least tax a taxed deal owes.
    min_tax_add = ZERO
    if (rules.min_tax is not None and rules.tax_kind != TAX_NONE
            and total_tax < rules.min_tax):
        min_tax_add = cents(rules.min_tax - total_tax)
        total_tax = cents(rules.min_tax)
    # Step 11: government fees are added after tax, when separately
    # itemised and state-mandated (amounts confirmed per deal).
    government_fees = cents(inputs.government_fees)
    # Step 12: amount due / amount financed, exactly as the spec lists:
    # gross price - dealer discount - trade allowance + total sales tax
    # + government fees + optional products - manufacturer rebate
    # (after tax, as a payment) - cash down payment.
    # The electronic tag fee is already inside the gross price (step 3,
    # Decision 14) -- it is NOT added again here.
    amount_due = cents(
        gross
        - inputs.dealer_discount
        - trade_allowance
        + total_tax
        + government_fees
        + inputs.optional_products
        - inputs.manufacturer_rebate
        - inputs.cash_down_payment
    )

    warnings: list[str] = []
    if florida and ZERO < taxable_base < SURTAX_CAP:
        warnings.append(
            "Taxable base is below $5,000: whether the county surtax "
            "applies to the reduced base or to $5,000 of price is "
            "flagged unverified in spec Section 16. Stage 1 applies the "
            "surtax to the reduced base; a human must confirm this deal."
        )

    if florida:
        lines: tuple[tuple[str, Decimal], ...] = (
            ("Vehicle selling price", cents(price)),
            ("Accessories and add-ons", cents(inputs.accessories)),
            ("Taxable dealer-required fees", cents(taxable_fees)),
            ("Electronic tag fee (taxable, Decision 14)", electronic_tag_fee),
            ("Gross sales price", cents(gross)),
            ("Dealer discount", cents(-inputs.dealer_discount)),
            ("Trade-in allowance", cents(-trade_allowance)),
            ("Taxable base", taxable_base),
            ("State tax (6%)", state_tax),
            ("County surtax base (first $5,000)", surtax_base),
            ("County surtax", county_surtax),
            ("Total sales tax", total_tax),
            ("Government fees (untaxed)", government_fees),
            ("Optional products elected", cents(inputs.optional_products)),
            ("Trade lien payoff (not subtracted before tax)", cents(trade_payoff)),
            ("Net trade (allowance - payoff)", cents(trade_allowance - trade_payoff)),
            ("Manufacturer rebate (applied after tax)", cents(-inputs.manufacturer_rebate)),
            ("Cash down payment", cents(-inputs.cash_down_payment)),
            ("Step 12 amount due (payoff not netted)", amount_due),
        )
    else:
        lines = _sheet_lines(
            rules, inputs, price=price, taxable_fees=taxable_fees,
            electronic_tag_fee=electronic_tag_fee, gross=gross,
            trade_allowance=trade_allowance, trade_payoff=trade_payoff,
            credit=credit, taxable_base=taxable_base, state_rate=state_rate,
            state_tax=state_tax, surtax_base=surtax_base,
            county_surtax=county_surtax, tax_limit_cut=tax_limit_cut,
            total_tax=total_tax, government_fees=government_fees,
            amount_due=amount_due, base_rate=base_rate, band_taxes=band_taxes,
            value_adjust=value_adjust, state_value=state_value,
            min_tax_add=min_tax_add,
        )

    # --- Payment estimate (only when term and rate were supplied) -------
    amount_financed: Optional[Decimal] = None
    monthly_payment: Optional[Decimal] = None
    total_of_payments: Optional[Decimal] = None
    finance_charge: Optional[Decimal] = None
    if inputs.term_months and inputs.annual_rate is not None:
        amount_financed = amount_due
        monthly_payment, total_of_payments, finance_charge = installment_terms(
            amount_financed, inputs.annual_rate, inputs.term_months,
            inputs.days_to_first_payment,
        )

    return DealResult(
        gross_sales_price=cents(gross),
        taxable_base=taxable_base,
        state_tax=state_tax,
        surtax_base=surtax_base,
        county_surtax=county_surtax,
        total_tax=total_tax,
        government_fees=government_fees,
        electronic_tag_fee=cents(inputs.electronic_tag_fee),
        amount_due=amount_due,
        amount_financed=amount_financed,
        monthly_payment=monthly_payment,
        total_of_payments=total_of_payments,
        finance_charge=finance_charge,
        lines=lines,
        warnings=tuple(warnings),
    )
