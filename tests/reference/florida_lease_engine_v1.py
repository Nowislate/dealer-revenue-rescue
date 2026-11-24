"""Florida lease calculation engine (Stage 6, spec Build 2).

Deterministic code only -- no AI decides lease math, exactly like
the retail engine. The calculation follows the DMS lease worksheet
the stores desk on, block by block:

    Capitalization      -> gross cap cost
    Cap Cost Reduction  -> net (adjusted) cap cost
    Residual            -> adjusted residual
    Payment             -> base payment, monthly tax, due on delivery

The tax rule is locked decision 13 (Lane, 2026-11-02) and it is ONE
rule: Florida lease tax is charged on the base monthly payment only.

    monthly tax = base monthly payment x combined rate
    combined rate = 6% state rate (preset data) + the buyer's
                    residence-county surtax rate

Nothing else in the lease is taxed item-by-item. Capitalized fees
and products are taxed only through the payment, because they change
the adjusted cap cost and therefore the payment. Each payment is its
own taxable transaction, so the retail engine's $5,000 surtax cap
(spec Section 9 step 9) does NOT apply here and is deliberately not
inherited: this module never touches tax_engine's steps. Amounts
collected at signing are worksheet arithmetic -- advance payment(s),
plus entered upfront charges, plus any security deposit -- never a
second tax calculation.

Acceptance fixtures: the engine was proven to the cent against two
real store lease worksheets, on which Monthly Tax is exactly the
combined rate x the base payment and the "Cap Taxes" line is 0.00.
The tests now carry two synthetic worksheets in the same layout
(decision 17), recomputed by hand, in tests/test_lease_engine.py.

Rounding: depreciation and rent charge are computed unrounded and
the base payment is rounded to cents (half-up); the monthly tax is
the ROUNDED base payment x the combined rate, rounded to cents --
the order the DMS worksheets themselves prove out.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from dealerpaperwork.models import ZERO, LeaseInputs, LeaseResult
from dealerpaperwork.presets import Preset, require_approved
from reference.florida_tax_engine_v1 import DEFAULT_STATE_RATE, cents


def calculate_lease(
    inputs: LeaseInputs,
    *,
    preset: Optional[Preset] = None,
    certify: bool = False,
) -> LeaseResult:
    """Run the lease worksheet calculation.

    ``certify=True`` means the result will be certified for clean
    printing: that requires an approved, valid preset set, enforced
    by ``presets.require_approved``. Draft calculations may run
    without one.
    """
    if certify:
        require_approved(preset)
    if not inputs.term_months or inputs.term_months <= 0:
        raise ValueError("Lease term (months) must be a positive number.")
    if inputs.money_factor < ZERO:
        raise ValueError("Money factor cannot be negative.")
    if not (ZERO <= inputs.residual_percent <= Decimal("1")):
        raise ValueError("Residual percent must be between 0 and 1.")

    state_rate = preset.state_tax_rate if preset is not None else DEFAULT_STATE_RATE
    combined_rate = state_rate + inputs.county_surtax_rate
    term = inputs.term_months

    # --- Capitalization ------------------------------------------------
    selling_price = cents(inputs.msrp - inputs.dealer_discount)
    gross_cap_cost = cents(
        selling_price
        + inputs.aftermarkets
        + inputs.predelivery_fee
        + inputs.acquisition_fee
        + inputs.esc_premium
        + inputs.maintenance
        + inputs.gap_premium
        + inputs.lah_iui
        + inputs.prior_lease_balance
        + inputs.license_fee
        + inputs.dealer_fees
        + inputs.cap_fees
        + inputs.cap_taxes
    )

    # --- Cap cost reduction ---------------------------------------------
    net_trade = cents(inputs.trade_allowance - inputs.trade_payoff)
    total_cap_reduction = cents(
        inputs.cash_down
        + inputs.total_rebates
        + net_trade
        + inputs.amount_applied_upfront
        - inputs.cash_back
    )
    net_cap_cost = cents(gross_cap_cost - total_cap_reduction)

    # --- Residual -----------------------------------------------------------
    adjusted_msrp = cents(
        inputs.residual_base_msrp
        - inputs.option_package_discount
        + inputs.hard_adds
    )
    residual_base = adjusted_msrp
    if inputs.mrm is not None and residual_base > inputs.mrm:
        residual_base = cents(inputs.mrm)
    residual_amount = cents(residual_base * inputs.residual_percent)
    adjusted_residual = cents(residual_amount + inputs.mileage_adjustment)

    # --- Payment --------------------------------------------------------------
    monthly_depreciation = (net_cap_cost - adjusted_residual) / term
    monthly_rent_charge = (net_cap_cost + adjusted_residual) * inputs.money_factor
    base_monthly_payment = cents(monthly_depreciation + monthly_rent_charge)
    # Decision 13: the tax is on the payment, on the FULL payment.
    monthly_tax = cents(base_monthly_payment * combined_rate)
    monthly_payment = cents(base_monthly_payment + monthly_tax)

    # --- Due on delivery ---------------------------------------------------------
    advanced_payment = cents(monthly_payment * inputs.number_of_advance_payments)
    due_on_delivery = cents(
        advanced_payment + inputs.upfront_charges + inputs.security_deposit
    )

    lines: tuple[tuple[str, Decimal], ...] = (
        ("MSRP", cents(inputs.msrp)),
        ("Dealer discount", cents(-inputs.dealer_discount)),
        ("Selling price", selling_price),
        ("Aftermarkets", cents(inputs.aftermarkets)),
        ("Predelivery service fee", cents(inputs.predelivery_fee)),
        ("Acquisition fee", cents(inputs.acquisition_fee)),
        ("ESC premium", cents(inputs.esc_premium)),
        ("Maintenance", cents(inputs.maintenance)),
        ("GAP premium", cents(inputs.gap_premium)),
        ("LAH/IUI", cents(inputs.lah_iui)),
        ("Prior lease balance", cents(inputs.prior_lease_balance)),
        ("License fee", cents(inputs.license_fee)),
        ("Dealer fees", cents(inputs.dealer_fees)),
        ("Cap fees", cents(inputs.cap_fees)),
        ("Cap taxes", cents(inputs.cap_taxes)),
        ("Gross cap cost", gross_cap_cost),
        ("Cash down", cents(-inputs.cash_down)),
        ("Total rebates", cents(-inputs.total_rebates)),
        ("Trade allowance", cents(-inputs.trade_allowance)),
        ("Trade payoff", cents(inputs.trade_payoff)),
        ("Net trade (allowance - payoff)", cents(-net_trade)),
        ("Amount applied upfront", cents(-inputs.amount_applied_upfront)),
        ("Cash back", cents(inputs.cash_back)),
        ("Total cap reduction", cents(-total_cap_reduction)),
        ("Net (adjusted) cap cost", net_cap_cost),
        ("Residual base MSRP", cents(inputs.residual_base_msrp)),
        ("Option package discount", cents(-inputs.option_package_discount)),
        ("Hard adds", cents(inputs.hard_adds)),
        ("Adjusted MSRP", adjusted_msrp),
        ("Residual base (after MRM cap)", residual_base),
        ("Residual amount", residual_amount),
        ("Mileage adjustment", cents(inputs.mileage_adjustment)),
        ("Adjusted residual", adjusted_residual),
        ("Monthly depreciation", cents(monthly_depreciation)),
        ("Monthly rent charge", cents(monthly_rent_charge)),
        ("Base monthly payment", base_monthly_payment),
        ("Combined tax rate (state + county surtax)", combined_rate),
        ("Monthly tax (on the base payment)", monthly_tax),
        ("Monthly payment", monthly_payment),
        ("Advanced payment", advanced_payment),
        ("Upfront charges", cents(inputs.upfront_charges)),
        ("Security deposit", cents(inputs.security_deposit)),
        ("Due on delivery", due_on_delivery),
    )

    return LeaseResult(
        selling_price=selling_price,
        gross_cap_cost=gross_cap_cost,
        net_trade=net_trade,
        total_cap_reduction=total_cap_reduction,
        net_cap_cost=net_cap_cost,
        adjusted_msrp=adjusted_msrp,
        residual_base=residual_base,
        residual_amount=residual_amount,
        adjusted_residual=adjusted_residual,
        monthly_depreciation=cents(monthly_depreciation),
        monthly_rent_charge=cents(monthly_rent_charge),
        base_monthly_payment=base_monthly_payment,
        combined_tax_rate=combined_rate,
        monthly_tax=monthly_tax,
        monthly_payment=monthly_payment,
        advanced_payment=advanced_payment,
        upfront_charges=cents(inputs.upfront_charges),
        security_deposit=cents(inputs.security_deposit),
        due_on_delivery=due_on_delivery,
        lines=lines,
    )
