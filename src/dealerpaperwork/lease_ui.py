"""Tkinter window for the lease walk-through (Stage 6 driver).

This module is ONLY a driver: every rule lives in ``lease_flow.py``
(and ``users.py`` / ``audit.py``) and is tested headless. The window
shows one step at a time -- buyer, vehicle, lease worksheet, review,
certified -- with Back / Next buttons, the lease engine's
line-by-line worksheet, warnings, and a Certify button that is
enabled only for a user whose role may certify.

The entry parsing below (``parse_lease_entries`` and friends) sits
outside the Tk guard so it is tested without a display. It is strict
on purpose: a figure the user typed that is not a number is reported
by name, never silently read as zero, because the user certifies the
result under their own name.

Residual is entered the way the DMS lease worksheet shows it, as a
percentage (62 means 62%). The flow and engine take the fraction
(0.62); the conversion happens here, in one place.

Launch (from the repo root, on Windows):

    set PYTHONPATH=src
    python -m dealerpaperwork.lease_ui
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

try:  # Tkinter ships with Windows Python; absent on some headless boxes.
    import tkinter as tk
    from tkinter import ttk

    TK_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only without Tk
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    TK_AVAILABLE = False

from . import county_field
from .deal_flow import DEFAULT_DEALS_DIR
from .lease_flow import LeaseDealSession, LeaseStep
from .models import Buyer, LeaseInputs, Vehicle, parse_odometer
from .products_ui import collect_products, draw_products
from .setup_wizard import DEFAULT_SETUP_PATH
from .users import ACTION_CERTIFY_DEAL, DEFAULT_USERS_PATH, User, has_permission

HUNDRED = Decimal("100")

STEP_TITLES = {
    LeaseStep.BUYER: "Step 1 - Lessee",
    LeaseStep.VEHICLE: "Step 2 - Vehicle",
    LeaseStep.WORKSHEET: "Step 3 - Lease worksheet",
    LeaseStep.REVIEW: "Step 4 - Review and certify",
    LeaseStep.CERTIFIED: "Ready to print",
}

# kinds: money (>= 0), signed (any sign), optional (blank = not
# entered), percent (0-100 on screen, fraction in the flow), factor
# (money factor, >= 0), int (whole number).
LEASE_WORKSHEET_BLOCKS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    ("Capitalization", (
        ("MSRP ($)", "msrp", "money"),
        ("Dealer discount ($)", "dealer_discount", "money"),
        ("Aftermarkets ($)", "aftermarkets", "money"),
        ("Predelivery / dealer fee ($)", "predelivery_fee", "money"),
        ("Acquisition fee ($)", "acquisition_fee", "money"),
        ("Service contract ($)", "esc_premium", "money"),
        ("Maintenance ($)", "maintenance", "money"),
        ("GAP ($)", "gap_premium", "money"),
        ("Credit life / disability ($)", "lah_iui", "money"),
        ("Prior lease balance ($)", "prior_lease_balance", "money"),
        ("License fee ($)", "license_fee", "money"),
        ("Other dealer fees ($)", "dealer_fees", "money"),
        ("Capitalized fees ($)", "cap_fees", "money"),
        ("Cap taxes ($)", "cap_taxes", "money"),
    )),
    ("Cap cost reduction", (
        ("Cash down ($)", "cash_down", "money"),
        ("Rebates ($)", "total_rebates", "money"),
        ("Trade allowance ($)", "trade_allowance", "money"),
        ("Trade lien payoff ($)", "trade_payoff", "money"),
        ("Amount applied upfront ($)", "amount_applied_upfront", "money"),
        ("Cash back ($)", "cash_back", "money"),
    )),
    ("Residual", (
        ("Residual base MSRP ($)", "residual_base_msrp", "money"),
        ("Option package discount ($)", "option_package_discount", "money"),
        ("Hard adds ($)", "hard_adds", "money"),
        ("Maximum residualized MSRP ($, blank = none)", "mrm", "optional"),
        ("Residual (%) *", "residual_percent", "percent"),
        ("Mileage adjustment ($, + or -)", "mileage_adjustment", "signed"),
    )),
    ("Payment", (
        ("Term (months) *", "term_months", "int"),
        ("Money factor *", "money_factor", "factor"),
        ("Advance payments", "number_of_advance_payments", "int"),
        ("Upfront charges ($)", "upfront_charges", "money"),
        ("Security deposit ($)", "security_deposit", "money"),
    )),
    # Printed on the contract's disclosures; none changes the payment.
    ("End of lease (prints on the contract)", (
        ("Miles allowed per year (blank = not entered)", "annual_mileage_allowance", "optional_int"),
        ("Excess mileage charge ($ per mile, blank = not entered)", "excess_mileage_rate", "optional"),
        ("Purchase option fee ($, blank = not entered)", "purchase_option_fee", "optional"),
    )),
)

LEASE_ENTRY_FIELDS = tuple(
    field for _block, fields in LEASE_WORKSHEET_BLOCKS for field in fields
)


def _clean(text: Optional[str]) -> str:
    return (text or "").strip().replace("$", "").replace(",", "").replace("%", "")


def _decimal(label: str, text: str, hint: str) -> Decimal:
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}: {text!r} is not a number. {hint}") from None
    if not value.is_finite():
        raise ValueError(f"{label}: {text!r} is not a number. {hint}")
    return value


class _Blank(ValueError):
    """A required worksheet figure was left blank."""


def parse_lease_entries(entries: dict[str, str], *, require: bool = True) -> tuple[dict, list[str]]:
    """Turn the worksheet's typed text into ``set_lease`` values.

    ``entries`` maps each LeaseInputs attribute to the text on screen.
    Returns (values, errors). When ``errors`` is non-empty nothing
    should be written to the session. Blank money fields mean zero; a
    blank maximum residualized MSRP means none entered. Term, residual
    and money factor are required: blank is an error, never zero. With
    ``require=False`` (leaving the step with Back) a blank required
    figure is skipped instead, so the user can step back before the
    worksheet is complete; text that is not a number still blocks.
    """
    values: dict = {}
    errors: list[str] = []
    for label, attr, kind in LEASE_ENTRY_FIELDS:
        label = label.rstrip(" *")
        text = _clean(entries.get(attr, ""))
        try:
            if kind == "int":
                if not text:
                    if attr == "term_months":
                        raise _Blank(f"{label} is required.")
                    text = "1"
                try:
                    number = int(text)
                except ValueError:
                    raise ValueError(
                        f"{label}: {text!r} is not a whole number."
                    ) from None
                if number < 0:
                    raise ValueError(f"{label} cannot be negative.")
                values[attr] = number
            elif kind == "optional_int":
                if not text:
                    values[attr] = None
                    continue
                try:
                    number = int(text)
                except ValueError:
                    raise ValueError(
                        f"{label}: {text!r} is not a whole number."
                    ) from None
                if number < 0:
                    raise ValueError(f"{label} cannot be negative.")
                values[attr] = number
            elif kind == "optional":
                if not text:
                    values[attr] = None
                    continue
                value = _decimal(label, text, "Enter a dollar amount or leave it blank.")
                if value < 0:
                    raise ValueError(f"{label} cannot be negative.")
                values[attr] = value
            elif kind == "percent":
                if not text:
                    raise _Blank(f"{label} is required.")
                value = _decimal(label, text, "Enter the residual as a percentage, e.g. 62.")
                if not (Decimal("1") <= value <= HUNDRED):
                    raise ValueError(
                        f"{label} must be between 1 and 100. Type 62 for 62%, "
                        "not 0.62."
                    )
                values[attr] = value / HUNDRED
            elif kind == "factor":
                if not text:
                    raise _Blank(f"{label} is required. Enter 0 for a zero-rate lease.")
                value = _decimal(label, text, "Enter the money factor, e.g. 0.00150.")
                if value < 0:
                    raise ValueError(f"{label} cannot be negative.")
                values[attr] = value
            elif kind == "signed":
                values[attr] = _decimal(label, text or "0", "Enter a dollar amount, e.g. -150.00.")
            else:
                value = _decimal(label, text or "0", "Enter a dollar amount, e.g. 1250.00.")
                if value < 0:
                    raise ValueError(f"{label} cannot be negative.")
                values[attr] = value
        except _Blank as exc:
            if require:
                errors.append(str(exc))
        except ValueError as exc:
            errors.append(str(exc))
    return values, errors


def lease_entry_text(lease: LeaseInputs) -> dict[str, str]:
    """The on-screen text for a saved lease (inverse of parsing)."""
    text: dict[str, str] = {}
    untouched = lease.term_months is None  # program figures never entered
    for _label, attr, kind in LEASE_ENTRY_FIELDS:
        value = getattr(lease, attr)
        blank_program = (
            (attr == "residual_percent" and value == 0)  # 0% is never a real residual
            or (attr == "money_factor" and untouched and value == 0)
        )
        if value is None or blank_program:
            text[attr] = ""
        elif kind == "percent":
            text[attr] = format((value * HUNDRED).normalize(), "f")
        else:
            text[attr] = str(value)
    return text


def parse_vehicle_year(text: str) -> Optional[int]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"Vehicle year: {text!r} is not a year, e.g. 2026.") from None


if TK_AVAILABLE:

    class LeaseWindow:
        """One lease step at a time, for a signed-in user."""

        def __init__(
            self,
            root: "tk.Misc",
            *,
            setup_path: Path,
            deals_dir: Path,
            audit_log,
            current_user: User,
            session: Optional[LeaseDealSession] = None,
            product_forms=(),
        ) -> None:
            if current_user is None:
                raise PermissionError("Use the shared sign-in and disclaimer gate.")
            self.root = root
            # (doc_id, title) of the store's approved lease product forms.
            self.product_forms = list(product_forms)
            self.setup_path = setup_path
            self.deals_dir = deals_dir
            self.audit_log = audit_log
            self.user = current_user
            self.session = session
            self.vars: dict[str, "tk.StringVar"] = {}
            self.new_vehicle_var = tk.BooleanVar(value=True)
            self.certify_btn = None

            root.title("Dealer Revenue Rescue - Lease Walk-Through")
            root.geometry("720x720")
            self.title_label = tk.Label(root, text="", font=("Segoe UI", 13, "bold"), anchor="w")
            self.title_label.pack(fill="x", padx=16, pady=(14, 4))
            self.error_label = tk.Label(root, text="", fg="#a00000", justify="left",
                                        anchor="w", wraplength=660)
            nav = tk.Frame(root)
            nav.pack(side="bottom", fill="x", padx=16, pady=12)
            self.error_label.pack(side="bottom", fill="x", padx=16)
            self.back_btn = tk.Button(nav, text="Back", command=self._on_back)
            self.back_btn.pack(side="left")
            self.next_btn = tk.Button(nav, text="Next", command=self._on_next)
            self.next_btn.pack(side="right")
            # The worksheet's wheel binding is app-wide; drop it with the window.
            root.bind("<Destroy>", lambda e: e.widget is root and root.unbind_all("<MouseWheel>"), add="+")
            self.body_host = tk.Frame(root)
            self.body_host.pack(fill="both", expand=True, padx=16, pady=6)
            self.body = self.body_host
            if self.session is not None or self._start_deal():
                self._show_step()

        # -- helpers ---------------------------------------------------------

        def var(self, name: str, initial: str = "") -> "tk.StringVar":
            if name not in self.vars:
                self.vars[name] = tk.StringVar(value=initial)
            else:
                self.vars[name].set(initial)
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
            # Windows wheel events arrive in multiples of 120.
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
            tk.Label(row, text=label, width=40, anchor="w").pack(side="left")
            tk.Entry(row, textvariable=self.var(name, initial)).pack(side="left", fill="x", expand=True)

        def _note(self, text: str, *, color: str = "#444444") -> None:
            tk.Label(self.body, text=text, justify="left", anchor="w",
                     wraplength=640, fg=color).pack(fill="x", pady=4)

        def _heading(self, text: str) -> None:
            tk.Label(self.body, text=text, font=("Segoe UI", 11, "bold"),
                     anchor="w").pack(fill="x", pady=(10, 2))

        def _show_errors(self, errors) -> None:
            self.error_label.config(text="\n".join("• " + e for e in errors))

        def _start_deal(self) -> bool:
            try:
                self.session = LeaseDealSession.start(
                    self.user, setup_path=self.setup_path,
                    deals_dir=self.deals_dir, audit_log=self.audit_log,
                )
            except Exception as exc:
                self._show_errors([str(exc)])
                self.next_btn.config(state="disabled")
                self.back_btn.config(state="disabled")
                return False
            return True

        # -- steps -------------------------------------------------------------

        def _show_step(self) -> None:
            step = self.session.current_step
            self._clear_body()
            self._show_errors([])
            self.title_label.config(
                text=f"{STEP_TITLES[step]}    [{self.user.display_name} ({self.user.role})]"
            )
            self.back_btn.config(
                state="disabled" if step in (LeaseStep.BUYER, LeaseStep.CERTIFIED) else "normal"
            )
            self.next_btn.config(
                text="Finish" if step is LeaseStep.CERTIFIED else "Next",
                state="disabled" if step is LeaseStep.REVIEW else "normal",
            )
            {
                LeaseStep.BUYER: self._step_buyer,
                LeaseStep.VEHICLE: self._step_vehicle,
                LeaseStep.WORKSHEET: self._step_worksheet,
                LeaseStep.REVIEW: self._step_review,
                LeaseStep.CERTIFIED: self._step_certified,
            }[step]()

        def _lessor_name(self) -> str:
            lease = getattr(self.session.preset, "lease", None)
            return (lease.lessor_name if lease else "") or ""

        def _package_lessor(self) -> str:
            details = getattr(self.session, "packaging_details", {}) or {}
            return details.get("lender_name") or self._lessor_name()

        def _step_buyer(self) -> None:
            details = getattr(self.session, "packaging_details", {}) or {}
            self._note(f"Lessor (from your approved preset): {self._lessor_name() or 'not set'}")
            self._field("Lessor name for the package", "lender_name",
                        details.get("lender_name") or self._lessor_name())
            self._field("Lessor street address", "lender_street",
                        details.get("lender_street") or details.get("lender_address", ""))
            self._field("Lessor city", "lender_city", details.get("lender_city", ""))
            self._field("Lessor state", "lender_state", details.get("lender_state", ""))
            self._field("Lessor ZIP code", "lender_zip", details.get("lender_zip", ""))
            b = self.session.lease.buyer
            self._field("Lessee full name *", "buyer_name", b.full_name)
            self._field("Co-lessee name", "co_buyer", b.co_buyer_name or "")
            self._field("Street address", "buyer_street", b.street_address)
            self._field("City", "buyer_city", b.city)
            self._field("State", "buyer_state", b.state)
            self._field("ZIP code", "buyer_zip", b.zip_code)
            county_field.build(
                tk, ttk, self.body, label="Residence county *", label_width=40,
                zip_var=self.vars["buyer_zip"],
                county_var=self.var("buyer_county", b.residence_county),
                enabled=county_field.uses_florida_lookup(self.session.preset),
            )
            self._field("Lessee phone", "buyer_phone", b.phone)
            self._field("Co-lessee street address", "co_street", b.co_buyer_street_address)
            self._field("Co-lessee city", "co_city", b.co_buyer_city)
            self._field("Co-lessee state", "co_state", b.co_buyer_state)
            self._field("Co-lessee ZIP code", "co_zip", b.co_buyer_zip_code)
            self._note("The county is where the lessee registers the vehicle; "
                       "its surtax rate applies to every monthly payment.")

        def _text(self, name: str) -> str:
            var = self.vars.get(name)
            return var.get().strip() if var is not None else ""

        def _collect_buyer(self) -> None:
            s = self._text
            self.session.set_packaging_details(
                lender_name=s("lender_name"), lender_street=s("lender_street"),
                lender_city=s("lender_city"), lender_state=s("lender_state"),
                lender_zip=s("lender_zip"))
            buyer = Buyer(
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
            if buyer != self.session.lease.buyer:
                self.session.set_buyer(buyer)

        def _step_vehicle(self) -> None:
            v = self.session.lease.vehicle
            self._field("Year *", "veh_year", "" if v.year is None else str(v.year))
            self._field("Make *", "veh_make", v.make)
            self._field("Model *", "veh_model", v.model)
            self._field("VIN *", "veh_vin", v.vin)
            self._field("Stock number", "veh_stock", v.stock_number)
            self._field("Body style", "veh_body", v.body_style)
            self._field("Color", "veh_color", v.color)
            self._field("Odometer (miles)", "veh_odometer",
                        "" if v.odometer is None else str(v.odometer))
            tk.Checkbutton(self.body, text="New vehicle",
                           variable=self.new_vehicle_var).pack(anchor="w", pady=6)
            self.new_vehicle_var.set(bool(v.is_new))

        def _collect_vehicle(self) -> None:
            vehicle = Vehicle(
                year=parse_vehicle_year(self.vars["veh_year"].get()),
                make=self.vars["veh_make"].get().strip(),
                model=self.vars["veh_model"].get().strip(),
                vin=self.vars["veh_vin"].get().strip(),
                is_new=self.new_vehicle_var.get(),
                stock_number=self.vars["veh_stock"].get().strip(),
                body_style=self._text("veh_body"),
                color=self._text("veh_color"),
                odometer=parse_odometer(self._text("veh_odometer")),
            )
            if vehicle != self.session.lease.vehicle:
                self.session.set_vehicle(vehicle)

        def _step_worksheet(self) -> None:
            self._scrolling_body()
            text = lease_entry_text(self.session.lease)
            for block, fields in LEASE_WORKSHEET_BLOCKS:
                self._heading(block)
                for label, attr, _kind in fields:
                    self._field(label, f"lease_{attr}", text[attr])
            self._note("Acquisition fee, capitalized fees, upfront charges and the "
                       "dealer fee start from your approved presets. Change them "
                       "here only if this deal differs; you certify the final figures.")
            draw_products(self._field, self._heading,
                          lambda text: tk.Label(self.body, text=text, font=("Segoe UI", 9, "bold"),
                                                anchor="w").pack(fill="x", pady=(6, 0)),
                          self._note, getattr(self, "product_forms", ()),
                          (getattr(self.session, "packaging_details", {}) or {}).get("products") or {})
            tk.Button(self.body, text="Calculate", command=self._on_calculate).pack(anchor="w", pady=6)
            self.worksheet_frame = tk.Frame(self.body)
            self.worksheet_frame.pack(fill="both", expand=True)
            if self.session.worksheet_is_current:
                self._render_worksheet()

        def _collect_worksheet(self, require: bool = True) -> None:
            entries = {attr: self.vars[f"lease_{attr}"].get() for _l, attr, _k in LEASE_ENTRY_FIELDS}
            values, errors = parse_lease_entries(entries, require=require)
            if errors:
                raise ValueError("\n• ".join(errors))
            changed = {k: v for k, v in values.items() if getattr(self.session.lease, k) != v}
            if changed:
                self.session.set_lease(**changed)
            if getattr(self, "product_forms", ()):
                self.session.set_products(collect_products(self._text, self.product_forms))

        def _on_calculate(self) -> None:
            try:
                self._collect_worksheet()
                self.session.compute_worksheet()
            except Exception as exc:
                self._show_errors([str(exc)])
                return
            self._show_errors([])
            self._render_worksheet()

        def _render_worksheet(self) -> None:
            for child in self.worksheet_frame.winfo_children():
                child.destroy()
            result = self.session.worksheet_result
            lines = [f"{label:<46} {amount:>12}" for label, amount in result.lines]
            text = tk.Text(self.worksheet_frame, height=max(8, len(lines) + 1), width=72,
                           font=("Consolas", 10))
            text.pack(fill="both", expand=True)
            text.insert("1.0", "\n".join(lines))
            text.config(state="disabled")
            for warning in result.warnings:
                tk.Label(self.worksheet_frame, text="WARNING: " + warning, justify="left",
                         anchor="w", wraplength=640, fg="#a00000").pack(fill="x", pady=4)

        def _step_review(self) -> None:
            session = self.session
            lease = session.lease
            result = session.worksheet_result
            summary = [
                f"Lessee: {lease.buyer.full_name}",
                f"Vehicle: {lease.vehicle.year} {lease.vehicle.make} "
                f"{lease.vehicle.model} ({lease.vehicle.vin})",
                f"Lessor: {self._package_lessor()}",
            ]
            if result is not None:
                summary += [
                    f"Term: {lease.term_months} months",
                    f"Base monthly payment: ${result.base_monthly_payment}",
                    f"Monthly tax: ${result.monthly_tax}",
                    f"Monthly payment: ${result.monthly_payment}",
                    f"Due on delivery: ${result.due_on_delivery}",
                ]
            summary += ["", "Certifying means: I entered these figures, reviewed the "
                        "lease worksheet, and certify the calculations under my name."]
            tk.Label(self.body, text="\n".join(summary), justify="left",
                     anchor="w", wraplength=640).pack(fill="x")
            if result is not None:
                for warning in result.warnings:
                    self._note("WARNING: " + warning, color="#a00000")
            can_certify = has_permission(self.user, ACTION_CERTIFY_DEAL)
            self.certify_btn = tk.Button(
                self.body, text=f"Certify as {self.user.display_name} ({self.user.role})",
                command=self._on_certify, state="normal" if can_certify else "disabled",
            )
            self.certify_btn.pack(anchor="w", pady=10)
            if not can_certify:
                self._note("Your role cannot certify deals. A manager or F&I user "
                           "must review and certify this lease.", color="#a00000")

        def _on_certify(self) -> None:
            try:
                self.session.certify(self.user)
            except Exception as exc:
                self._show_errors([str(exc)])
                return
            self._show_step()

        def _step_certified(self) -> None:
            record = self.session.certification
            lines = ["This lease is certified and ready to print.", "",
                     "Print it from the home screen: select the deal, then "
                     "Print / package selected."]
            if record is not None:
                lines += [
                    "",
                    f"Certified by {record.certifier_name} ({record.certifier_role}) "
                    f"at {record.certified_at}",
                    f"Preset version: {record.preset_version} "
                    f"(approved by {record.preset_approved_by})",
                    f"Monthly payment: ${record.monthly_payment}   "
                    f"Due on delivery: ${record.due_on_delivery}",
                ]
            tk.Label(self.body, text="\n".join(lines), justify="left",
                     anchor="w", wraplength=640).pack(fill="x", pady=8)
            tk.Button(self.body, text="Reopen to change figures",
                      command=self._on_reopen).pack(anchor="w", pady=6)
            self._note("Reopening keeps the certification until a figure changes. "
                       "Any change clears it, and the lease must be certified again.")

        def _on_reopen(self) -> None:
            self.session.current_step = LeaseStep.WORKSHEET
            self.session.save()
            self._show_step()

        # -- navigation ----------------------------------------------------------

        _COLLECTORS = {
            LeaseStep.BUYER: "_collect_buyer",
            LeaseStep.VEHICLE: "_collect_vehicle",
            LeaseStep.WORKSHEET: "_collect_worksheet",
        }

        def _collect(self, step, *, leaving_back: bool = False) -> bool:
            collector = self._COLLECTORS.get(step)
            if not collector:
                return True
            try:
                if step is LeaseStep.WORKSHEET:
                    self._collect_worksheet(require=not leaving_back)
                else:
                    getattr(self, collector)()
            except (ValueError, InvalidOperation) as exc:
                self._show_errors([str(exc)])
                return False
            except OSError as exc:
                self._show_errors([f"The deal could not be saved: {exc}. Check the data folder, then try again."])
                return False
            return True

        def _on_next(self) -> None:
            if self.session is None:
                self._show_errors(["No authorized lease session is open. Return to the start screen."])
                return
            step = self.session.current_step
            if step is LeaseStep.CERTIFIED:
                self.root.destroy()
                return
            if not self._collect(step):
                return
            errors = self.session.advance()
            if errors:
                self._show_errors(errors)
                return
            self._show_step()

        def _on_back(self) -> None:
            if self.session is None:
                return
            if not self._collect(self.session.current_step, leaving_back=True):
                return  # keep the user's typing on screen with the error
            self.session.back()
            self._show_step()


def run_lease_walkthrough(
    *, setup_path: Path = DEFAULT_SETUP_PATH,
    users_path: Path = DEFAULT_USERS_PATH,
    deals_dir: Path = DEFAULT_DEALS_DIR,
    audit_path: Optional[Path] = None,
) -> int:
    """Enter the shared sign-in and disclaimer gate before lease entry."""
    from .app_ui import run_app
    return run_app(initial_action="lease", setup_path=setup_path,
                   users_path=users_path, deals_dir=deals_dir, audit_path=audit_path)


if __name__ == "__main__":
    raise SystemExit(run_lease_walkthrough())
