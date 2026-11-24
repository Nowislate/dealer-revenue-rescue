"""Tkinter window for the per-deal walk-through (Stage 3).

This module is ONLY a driver: every rule lives in ``deal_flow.py``
(and ``users.py`` / ``audit.py``) and is tested headless. The window
shows one step at a time -- buyer, vehicle, trade, worksheet, review,
certified -- with Back / Next buttons, the engine's line-by-line
worksheet, the financing section, warnings, and a Certify button that
is enabled only for a user whose role may certify.

Flow of the window itself:

1. **Login.** Local accounts from ``users.UserStore``
   (``data/users.json``). On a true first run (no accounts yet) the
   window offers the one-time "create the first admin" form; after
   that, creating accounts is an admin act in the store layer.
2. **Deal.** A new ``DealSession`` is started against the approved
   preset embedded in the Stage 2 setup file (``config/setup.json``).
   If setup is incomplete the window says so and no deal opens --
   deal entry is locked until setup completes.

Tkinter ships with Windows Python, the target platform. The import is
guarded exactly like ``setup_ui.py``: without Tk, importing this
module still succeeds and ``run_deal_walkthrough()`` explains.

Launch (from the repo root, on Windows):

    set PYTHONPATH=src
    python -m dealerpaperwork.deal_ui

Printing the certified pack is Stage 4; the final screen here says
"Ready to print" and stops.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

try:  # Tkinter ships with Windows Python; absent on some headless boxes.
    import tkinter as tk
    from tkinter import messagebox, ttk

    TK_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only without Tk
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    TK_AVAILABLE = False

from . import county_field
from .audit import AuditLog
from .deal_flow import (
    DEFAULT_DEALS_DIR,
    DealSession,
    DealStep,
    SetupRequiredError,
    preset_from_setup_file,
)
from . import state_rules as sr
from .math_check import parse_money
from .models import D, Buyer, TradeIn, Vehicle, parse_odometer
from .doc_library import deal_condition
from .products_ui import collect_products, draw_products, offered_products
from .setup_wizard import DEFAULT_SETUP_PATH, is_setup_complete
from .users import (
    ACTION_CERTIFY_DEAL,
    DEFAULT_USERS_PATH,
    ROLE_ADMIN,
    AuthenticationError,
    User,
    UserStore,
    has_permission,
)

STEP_TITLES = {
    DealStep.BUYER: "Step 1 - Buyer",
    DealStep.VEHICLE: "Step 2 - Vehicle",
    DealStep.TRADE: "Step 3 - Trade-in and money",
    DealStep.WORKSHEET: "Step 4 - Worksheet",
    DealStep.REVIEW: "Step 5 - Review and certify",
    DealStep.CERTIFIED: "Ready to print",
}

# (label, DealInputs attribute) for the money fields on the trade step.
MONEY_FIELDS_UI = (
    ("Selling price ($)", "selling_price"),
    ("Accessories / add-ons ($)", "accessories"),
    ("Dealer fee ($)", "dealer_fee"),
    ("Other taxable fees ($)", "other_taxable_fees"),
    ("Dealer discount ($)", "dealer_discount"),
    ("Government fees ($)", "government_fees"),
    ("Optional products ($)", "optional_products"),
    ("Electronic tag fee ($)", "electronic_tag_fee"),
    ("Manufacturer rebate ($)", "manufacturer_rebate"),
    ("Cash down payment ($)", "cash_down_payment"),
)


def parse_payment_terms(term: str, apr: str, days: str) -> dict:
    """The finance-terms text on screen -> ``set_payment_terms`` values.

    Strict: text that is not a number raises ValueError naming the
    field, never a silent zero. APR is typed as a percentage (8.34
    means 8.34%). Term and APR both blank means a cash deal; blank
    days means a regular 30-day first period.
    """
    term = (term or "").strip()
    apr = (apr or "").strip().replace("%", "")
    days = (days or "").strip()
    try:
        term_months = int(term) if term else None
    except ValueError:
        raise ValueError(f"Term (months): {term!r} is not a whole number.") from None
    try:
        rate = D(apr) if apr else None
    except (InvalidOperation, ValueError):
        raise ValueError(f"APR (%): {apr!r} is not a number, e.g. 8.34.") from None
    if rate is not None:
        if not rate.is_finite():
            raise ValueError(f"APR (%): {apr!r} is not a number, e.g. 8.34.")
        # Always a percentage: 0.9 is a real 0.9% promotional rate.
        rate = rate / Decimal("100")
    try:
        days_to_first = int(days) if days else None
    except ValueError:
        raise ValueError(f"Days to first payment: {days!r} is not a whole number.") from None
    return {"term_months": term_months, "annual_rate": rate,
            "days_to_first_payment": days_to_first}


def payment_terms_text(inputs) -> dict:
    """On-screen text for saved finance terms (inverse of parsing)."""
    rate = inputs.annual_rate
    return {
        "term": "" if inputs.term_months is None else str(inputs.term_months),
        "apr": "" if rate is None else format((rate * 100).normalize(), "f"),
        "days": "" if inputs.days_to_first_payment is None else str(inputs.days_to_first_payment),
    }


def _money(text: str) -> Decimal:
    text = (text or "").strip().replace("$", "").replace(",", "")
    if not text:
        return D("0")
    try:
        return D(text)
    except (InvalidOperation, ValueError):
        return D("0")


if TK_AVAILABLE:

    class DealWindow:
        """Login screen, then one deal step at a time."""

        def __init__(
            self,
            root: "tk.Tk",
            *,
            user_store: UserStore,
            audit_log: AuditLog,
            setup_path: Path,
            deals_dir: Path,
            current_user: User,
            session: Optional[DealSession] = None,
            product_forms=(),
        ) -> None:
            self.root = root
            # (doc_id, title) of the store's approved product forms.
            # A {condition: forms} map or a plain list (see offered_products).
            self.product_forms = (dict(product_forms) if isinstance(product_forms, dict)
                                  else list(product_forms))
            self.user_store = user_store
            self.audit_log = audit_log
            self.setup_path = setup_path
            self.deals_dir = deals_dir
            self.user = current_user
            self.session = session
            self.vars: dict[str, "tk.StringVar"] = {}
            self.has_trade_var = tk.BooleanVar(value=False)
            self.new_vehicle_var = tk.BooleanVar(value=True)
            self.ack_var = tk.BooleanVar(value=False)
            self.certify_btn: Optional[tk.Button] = None

            root.title("Dealer Revenue Rescue - Deal Walk-Through")
            root.geometry("680x620")
            self.title_label = tk.Label(
                root, text="", font=("Segoe UI", 13, "bold"), anchor="w"
            )
            self.title_label.pack(fill="x", padx=16, pady=(14, 4))
            # Navigation and errors are packed first, at the bottom, so a
            # long step can never push Back/Next off the window.
            nav = tk.Frame(root)
            nav.pack(side="bottom", fill="x", padx=16, pady=12)
            self.error_label = tk.Label(
                root, text="", fg="#a00000", justify="left", anchor="w",
                wraplength=620,
            )
            self.error_label.pack(side="bottom", fill="x", padx=16)
            self.body_host = tk.Frame(root)
            self.body_host.pack(fill="both", expand=True, padx=16, pady=6)
            self.body = self.body_host
            root.bind("<Destroy>", lambda e: e.widget is root and root.unbind_all("<MouseWheel>"), add="+")
            self.back_btn = tk.Button(nav, text="Back", command=self._on_back)
            self.back_btn.pack(side="left")
            self.next_btn = tk.Button(nav, text="Next", command=self._on_next)
            self.next_btn.pack(side="right")
            if self.user is None:
                raise PermissionError('Use the shared sign-in and disclaimer gate.')
            if self.session is not None or self._start_deal():
                self._show_step()

        # -- helpers ----------------------------------------------------

        def var(self, name: str, initial: str = "") -> "tk.StringVar":
            if name not in self.vars:
                self.vars[name] = tk.StringVar(value=initial)
            return self.vars[name]

        def _clear_body(self) -> None:
            self.root.unbind_all("<MouseWheel>")
            for child in self.body_host.winfo_children():
                child.destroy()
            self.body = self.body_host
            self.certify_btn = None

        def _scrolling_body(self) -> None:
            canvas = tk.Canvas(self.body_host, highlightthickness=0)
            bar = ttk.Scrollbar(self.body_host, orient="vertical", command=canvas.yview)
            inner = tk.Frame(canvas)
            inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
            window = canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
            canvas.configure(yscrollcommand=bar.set)
            bar.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)

            def wheel(event):
                try:
                    canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
                except tk.TclError:
                    pass
            self.root.bind_all("<MouseWheel>", wheel)
            self.body = inner

        def _field(self, label: str, name: str, initial: str = "") -> None:
            row = tk.Frame(self.body)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, width=28, anchor="w").pack(side="left")
            tk.Entry(row, textvariable=self.var(name, initial)).pack(
                side="left", fill="x", expand=True
            )

        def _show_errors(self, errors) -> None:
            self.error_label.config(
                text="\n".join("• " + e for e in errors)
            )

        def _start_deal(self) -> bool:
            assert self.user is not None
            if not is_setup_complete(self.setup_path):
                self._show_errors(
                    [
                        "Setup is not complete on this machine, so "
                        "deal entry is locked. Run the first-time setup "
                        "wizard first (python -m dealerpaperwork.setup_ui)."
                    ]
                )
                return False
            try:
                self.session = DealSession(
                    user=self.user,
                    preset=preset_from_setup_file(self.setup_path),
                    setup_path=self.setup_path,
                    deals_dir=self.deals_dir,
                    audit_log=self.audit_log,
                )
            except Exception as exc:
                self._show_errors([str(exc)])
                return False
            return True

        # -- deal step screens ---------------------------------------------

        def _show_step(self) -> None:
            assert self.session is not None
            step = self.session.current_step
            self._clear_body()
            self._show_errors([])
            who = (
                f"{self.user.display_name} ({self.user.role})"
                if self.user
                else ""
            )
            self.title_label.config(
                text=f"{STEP_TITLES[step]}    [{who}]"
            )
            self.back_btn.config(
                state="disabled"
                if step in (DealStep.BUYER, DealStep.CERTIFIED)
                else "normal"
            )
            self.next_btn.config(
                text="Finish" if step is DealStep.CERTIFIED else "Next",
                state="disabled" if step is DealStep.REVIEW else "normal",
            )
            builder = {
                DealStep.BUYER: self._step_buyer,
                DealStep.VEHICLE: self._step_vehicle,
                DealStep.TRADE: self._step_trade,
                DealStep.WORKSHEET: self._step_worksheet,
                DealStep.REVIEW: self._step_review,
                DealStep.CERTIFIED: self._step_certified,
            }[step]
            builder()

        def _step_buyer(self) -> None:
            details = getattr(self.session, 'packaging_details', {})
            row = ttk.Frame(self.body)
            row.pack(fill='x', pady=2)
            ttk.Label(row, text='Transaction type *', width=26).pack(side='left')
            ttk.Combobox(row, textvariable=self.var('transaction_type', details.get('transaction_type', '')),
                         values=('cash', 'finance'), state='readonly').pack(side='left', fill='x', expand=True)
            self._field('Lender name (finance)', 'lender_name', details.get('lender_name', ''))
            self._field('Lender street address', 'lender_street',
                        details.get('lender_street') or details.get('lender_address', ''))
            self._field('Lender city', 'lender_city', details.get('lender_city', ''))
            self._field('Lender state', 'lender_state', details.get('lender_state', ''))
            self._field('Lender ZIP code', 'lender_zip', details.get('lender_zip', ''))
            b = self.session.inputs.buyer
            self._field("Buyer full name *", "buyer_name", b.full_name)
            self._field("Co-buyer name", "co_buyer", b.co_buyer_name or "")
            self._field("Street address", "buyer_street", b.street_address)
            self._field("City", "buyer_city", b.city)
            self._field("State", "buyer_state", b.state)
            self._field("ZIP code", "buyer_zip", b.zip_code)
            county_field.build(
                tk, ttk, self.body, label="Residence county *", label_width=28,
                zip_var=self.vars["buyer_zip"],
                county_var=self.var("buyer_county", b.residence_county),
                enabled=county_field.uses_florida_lookup(self.session.preset),
            )
            self._field("Buyer phone", "buyer_phone", b.phone)
            self._field("Co-buyer street address", "co_street", b.co_buyer_street_address)
            self._field("Co-buyer city", "co_city", b.co_buyer_city)
            self._field("Co-buyer state", "co_state", b.co_buyer_state)
            self._field("Co-buyer ZIP code", "co_zip", b.co_buyer_zip_code)
            tk.Label(
                self.body,
                text="The county is where the buyer registers the "
                "vehicle; it sets the county surtax rate. A Florida ZIP "
                "fills it, or lists the counties when the ZIP crosses a "
                "county line.",
                justify="left", anchor="w", wraplength=600, fg="#444444",
            ).pack(fill="x", pady=6)

        def _collect_buyer(self) -> None:
            def s(name: str) -> str:
                var = self.vars.get(name)
                return var.get().strip() if var is not None else ""

            self.session.set_packaging_details(
                transaction_type=s('transaction_type'), lender_name=s('lender_name'),
                lender_street=s('lender_street'), lender_city=s('lender_city'),
                lender_state=s('lender_state'), lender_zip=s('lender_zip'))
            self.session.set_buyer(
                Buyer(
                    full_name=s("buyer_name"),
                    co_buyer_name=s("co_buyer") or None,
                    street_address=s("buyer_street"),
                    city=s("buyer_city"),
                    state=s("buyer_state") or "FL",
                    zip_code=s("buyer_zip"),
                    residence_county=s("buyer_county"),
                    phone=s("buyer_phone"),
                    co_buyer_street_address=s("co_street"),
                    co_buyer_city=s("co_city"),
                    co_buyer_state=s("co_state"),
                    co_buyer_zip_code=s("co_zip"),
                )
            )

        def _step_vehicle(self) -> None:
            v = self.session.inputs.vehicle
            self._field(
                "Year *", "veh_year", "" if v.year is None else str(v.year)
            )
            self._field("Make *", "veh_make", v.make)
            self._field("Model *", "veh_model", v.model)
            self._field("VIN *", "veh_vin", v.vin)
            self._field("Stock number", "veh_stock", v.stock_number)
            self._field("Body style", "veh_body", v.body_style)
            self._field("Color", "veh_color", v.color)
            self._field("Odometer (miles)", "veh_odometer",
                        "" if v.odometer is None else str(v.odometer))
            tk.Checkbutton(
                self.body, text="New vehicle", variable=self.new_vehicle_var
            ).pack(anchor="w", pady=6)
            self.new_vehicle_var.set(bool(v.is_new))
            # Decision 30: only when the store's rule sheet uses them.
            preset = self.session.preset
            rules = preset.rules if preset is not None else sr.FLORIDA
            if rules.classes:
                row = tk.Frame(self.body)
                row.pack(fill="x", pady=2)
                tk.Label(row, text="Vehicle class for tax", width=28, anchor="w").pack(side="left")
                names = [""] + [c.name for c in rules.classes]
                ttk.Combobox(row, textvariable=self.var("veh_tax_class", v.tax_class),
                             values=names, state="readonly", width=34).pack(side="left")
                tk.Label(self.body, anchor="w", fg="#444444",
                         text="Blank is the standard state rate.").pack(fill="x")
            if rules.value_rule != sr.VALUE_PRICE:
                self._field("State value $", "veh_state_value",
                            "" if v.state_value is None else str(v.state_value))
                tk.Label(self.body, anchor="w", fg="#444444", justify="left", wraplength=600,
                         text=("Your state taxes this vehicle on the value it sets"
                               + (" (used vehicles)" if rules.value_used_only else "")
                               + ". Type it from the state's own lookup.")).pack(fill="x")

        def _collect_vehicle(self) -> None:
            year_text = self.vars["veh_year"].get().strip()
            try:
                year = int(year_text) if year_text else None
            except ValueError:
                year = None
            self.session.set_vehicle(
                Vehicle(
                    year=year,
                    make=self.vars["veh_make"].get().strip(),
                    model=self.vars["veh_model"].get().strip(),
                    vin=self.vars["veh_vin"].get().strip(),
                    is_new=self.new_vehicle_var.get(),
                    stock_number=self.vars["veh_stock"].get().strip(),
                    body_style=self._text("veh_body"),
                    color=self._text("veh_color"),
                    odometer=parse_odometer(self._text("veh_odometer")),
                    tax_class=self._text("veh_tax_class"),
                    state_value=(parse_money(self._text("veh_state_value"), label="State value")
                                 if self._text("veh_state_value") else None),
                )
            )

        def _text(self, name: str) -> str:
            var = self.vars.get(name)
            return var.get().strip() if var is not None else ""

        def _step_trade(self) -> None:
            self._scrolling_body()
            t = self.session.inputs.trade
            tk.Checkbutton(
                self.body,
                text="This deal has a trade-in",
                variable=self.has_trade_var,
            ).pack(anchor="w")
            self.has_trade_var.set(t is not None)
            self._field(
                "Trade description", "trade_desc", t.description if t else ""
            )
            self._field("Trade VIN", "trade_vin", t.vin if t else "")
            self._field(
                "Trade allowance ($)", "trade_allowance",
                str(t.allowance) if t else "0",
            )
            self._field(
                "Trade lien payoff ($)", "trade_payoff",
                str(t.payoff) if t else "0",
            )
            tk.Label(
                self.body, text="The money", font=("Segoe UI", 11, "bold"),
                anchor="w",
            ).pack(fill="x", pady=(12, 4))
            inputs = self.session.inputs
            for label, attr in MONEY_FIELDS_UI:
                self._field(
                    label, f"money_{attr}", str(getattr(inputs, attr))
                )
            tk.Label(
                self.body, text="Finance terms (blank for a cash deal)",
                font=("Segoe UI", 11, "bold"), anchor="w",
            ).pack(fill="x", pady=(12, 4))
            terms = payment_terms_text(inputs)
            self._field("Term (months)", "pay_term", terms["term"])
            self._field("APR (%)", "pay_apr", terms["apr"])
            self._field("Days to first payment (blank = 30)", "pay_days", terms["days"])
            draw_products(
                self._field,
                lambda text: tk.Label(self.body, text=text, font=("Segoe UI", 11, "bold"),
                                      anchor="w").pack(fill="x", pady=(12, 4)),
                lambda text: tk.Label(self.body, text=text, font=("Segoe UI", 9, "bold"),
                                      anchor="w").pack(fill="x", pady=(6, 0)),
                lambda text: tk.Label(self.body, text=text, justify="left", anchor="w",
                                      wraplength=600, fg="#444444").pack(fill="x"),
                self._offered_products(),
                getattr(self.session, "packaging_details", {}).get("products") or {})
            tk.Label(
                self.body,
                text="Dealer fee and electronic tag fee start from your "
                "approved presets; change them here only if this deal "
                "differs -- you will certify the final figures.",
                justify="left", anchor="w", wraplength=600, fg="#444444",
            ).pack(fill="x", pady=6)

        def _collect_trade(self) -> None:
            # Finance terms first: they validate before anything is
            # written, so a typing error leaves the deal untouched.
            self.session.set_payment_terms(**parse_payment_terms(
                self.vars["pay_term"].get(), self.vars["pay_apr"].get(),
                self.vars["pay_days"].get(),
            ))
            if self.has_trade_var.get():
                self.session.set_trade(
                    TradeIn(
                        description=self.vars["trade_desc"].get().strip(),
                        vin=self.vars["trade_vin"].get().strip(),
                        allowance=_money(self.vars["trade_allowance"].get()),
                        payoff=_money(self.vars["trade_payoff"].get()),
                    )
                )
            else:
                self.session.set_trade(None)
            amounts = {
                attr: _money(self.vars[f"money_{attr}"].get())
                for _label, attr in MONEY_FIELDS_UI
            }
            self.session.set_money(**amounts)
            if getattr(self, "product_forms", ()):
                # Only the products this deal's condition offers: a
                # product left over from a changed condition drops out.
                self.session.set_products(collect_products(
                    lambda name: self.vars[name].get().strip(), self._offered_products()))

        def _offered_products(self) -> list:
            return offered_products(getattr(self, "product_forms", ()),
                                    deal_condition(self.session))

        def _step_worksheet(self) -> None:
            try:
                result = self.session.compute_worksheet()
            except Exception as exc:
                tk.Label(
                    self.body, text=str(exc), justify="left", anchor="w",
                    wraplength=600, fg="#a00000",
                ).pack(fill="x")
                return
            lines = [f"{label:<46} {amount:>12}" for label, amount in result.lines]
            if self.session.financing is not None:
                lines.append("")
                lines.append("FINANCING")
                lines += [
                    f"{label:<46} {amount:>12}"
                    for label, amount in self.session.financing.lines
                ]
            text = tk.Text(self.body, height=24, width=76)
            text.pack(fill="both", expand=True)
            text.insert("1.0", "\n".join(lines))
            text.config(state="disabled")
            for warning in result.warnings:
                tk.Label(
                    self.body, text="WARNING: " + warning, justify="left",
                    anchor="w", wraplength=600, fg="#a00000",
                ).pack(fill="x", pady=4)

        def _step_review(self) -> None:
            session = self.session
            result = session.worksheet_result
            financing = session.financing
            summary = [
                f"Buyer: {session.inputs.buyer.full_name}",
                f"Vehicle: {session.inputs.vehicle.year} "
                f"{session.inputs.vehicle.make} "
                f"{session.inputs.vehicle.model} "
                f"({session.inputs.vehicle.vin})",
            ]
            if result is not None:
                summary.append(f"Taxable base: ${result.taxable_base}")
                summary.append(f"Total sales tax: ${result.total_tax}")
            if financing is not None:
                summary.append(
                    f"Amount financed: ${financing.amount_financed}"
                )
            summary += [
                "",
                "Certifying means: I entered these figures, reviewed "
                "the worksheet, and certify the calculations under my "
                "name.",
            ]
            tk.Label(
                self.body, text="\n".join(summary), justify="left",
                anchor="w", wraplength=600,
            ).pack(fill="x")
            if session.warnings:
                for warning in session.warnings:
                    tk.Label(
                        self.body, text="WARNING: " + warning,
                        justify="left", anchor="w", wraplength=600,
                        fg="#a00000",
                    ).pack(fill="x", pady=4)
                tk.Checkbutton(
                    self.body,
                    text="I have read this warning and confirm this "
                    "deal should be certified with it.",
                    variable=self.ack_var,
                ).pack(anchor="w")
                self.ack_var.set(
                    session.warnings_acknowledged_by
                    == (self.user.username if self.user else None)
                )
            can_certify = bool(
                self.user and has_permission(self.user, ACTION_CERTIFY_DEAL)
            )
            self.certify_btn = tk.Button(
                self.body,
                text=(
                    f"Certify as {self.user.display_name} "
                    f"({self.user.role})"
                    if self.user
                    else "Certify"
                ),
                command=self._on_certify,
                state="normal" if can_certify else "disabled",
            )
            self.certify_btn.pack(anchor="w", pady=10)
            if not can_certify:
                tk.Label(
                    self.body,
                    text="Your role cannot certify deals. A manager or "
                    "F&I user must review and certify this deal.",
                    justify="left", anchor="w", wraplength=600,
                    fg="#a00000",
                ).pack(fill="x")

        def _on_certify(self) -> None:
            assert self.session is not None and self.user is not None
            if self.session.warnings and self.ack_var.get():
                self.session.acknowledge_warnings(self.user)
            try:
                self.session.certify(self.user)
            except Exception as exc:
                self._show_errors([str(exc)])
                return
            self._show_step()

        def _step_certified(self) -> None:
            record = self.session.certification
            if getattr(self.session, "is_drill", False):
                how = "Click Finish to print the SAMPLE practice pack."
            else:
                how = ("Click Finish, then select this deal on the home "
                       "screen and click Print / package selected.")
            lines = ["This deal is certified and ready to print.", "", how]
            if record is not None:
                lines += [
                    "",
                    f"Certified by {record.certifier_name} "
                    f"({record.certifier_role}) at {record.certified_at}",
                    f"Preset version: {record.preset_version} "
                    f"(approved by {record.preset_approved_by})",
                    f"Total tax: ${record.total_tax}   "
                    f"Amount financed: ${record.amount_financed}",
                ]
            tk.Label(
                self.body, text="\n".join(lines), justify="left",
                anchor="w", wraplength=600,
            ).pack(fill="x", pady=8)

        # -- navigation ---------------------------------------------------

        _COLLECTORS = {
            DealStep.BUYER: "_collect_buyer",
            DealStep.VEHICLE: "_collect_vehicle",
            DealStep.TRADE: "_collect_trade",
        }

        def _on_next(self) -> None:
            if self.session is None:
                self._show_errors(['No authorized deal session is open. Return to the start screen.'])
                return
            step = self.session.current_step
            collector = self._COLLECTORS.get(step)
            if collector:
                try:
                    getattr(self, collector)()
                except (ValueError, InvalidOperation) as exc:
                    self._show_errors([str(exc)])
                    return
            if step is DealStep.CERTIFIED:
                self.root.destroy()
                return
            errors = self.session.advance()
            if errors:
                self._show_errors(errors)
                return
            self._show_step()

        def _on_back(self) -> None:
            if self.session is None:
                return
            step = self.session.current_step
            collector = self._COLLECTORS.get(step)
            if collector:
                try:
                    getattr(self, collector)()
                except (ValueError, InvalidOperation) as exc:
                    self._show_errors([str(exc)])
                    return  # keep the user's typing on screen with the error
            self.session.back()
            self._show_step()


def run_deal_walkthrough(
    *, setup_path: Path = DEFAULT_SETUP_PATH,
    users_path: Path = DEFAULT_USERS_PATH,
    deals_dir: Path = DEFAULT_DEALS_DIR,
    audit_path: Optional[Path] = None,
) -> int:
    """Enter shared sign-in and disclaimer gate before deal entry."""
    from .app_ui import run_app
    return run_app(initial_action='retail', setup_path=setup_path,
                   users_path=users_path, deals_dir=deals_dir, audit_path=audit_path)


if __name__ == "__main__":
    raise SystemExit(run_deal_walkthrough())
