"""Tkinter window for the Step 0 setup wizard (Stage 2).

This module is ONLY a driver: every rule lives in ``setup_wizard.py``
and is tested headless. The window shows one step at a time with
Back / Next buttons, plain-language labels, and the current step's
errors listed before the user can move on.

Tkinter ships with Windows Python, which is the target platform. The
``tkinter`` import is guarded: on a machine without Tk (e.g. a headless
Linux dev box) importing this module still succeeds and
``run_setup_wizard()`` explains the problem instead of crashing -- the
test-suite imports only ``setup_wizard``, never this UI.

Launch (from the repo root, on Windows):

    set PYTHONPATH=src
    python -m dealerpaperwork.setup_ui

or call ``run_setup_wizard()`` from Python.
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

from . import fl_tables
from . import math_check as mc
from . import state_rules as sr
from .models import D, DealerProfile
from .network_check import check_lan
from .printers import PrinterProvider, get_printer_provider
from .setup_wizard import (
    CHECK_LABELS,
    DEFAULT_SETUP_PATH,
    DOC_STANDARD_LETTER,
    TITLE_FILING_METHODS,
    SetupStep,
    SetupWizard,
    TrayAssignment,
)

STEP_TITLES = {
    SetupStep.NETWORK: "Step 0.1 - Store network, or this computer by itself",
    SetupStep.PROFILE: "Step 0.2 - Your dealership",
    SetupStep.PRINTER: "Step 0.3 - Choose the printer",
    SetupStep.TRAY_MAPPING: "Step 0.4 - Trays and paper",
    SetupStep.PREFLIGHT: "Step 0.5 - Printer check",
    SetupStep.STATE_RULES: "Step 0.6 - Your state's tax and tag rules",
    SetupStep.TAX_RATES: "Step 0.7 - Your local tax rates",
    SetupStep.MATH_CHECK: "Step 0.8 - Check the math against your DMS",
    SetupStep.APPROVE: "Step 0.9 - Review and approve",
    SetupStep.DONE: "Setup complete",
}


def _money_or_none(text: str) -> Optional[Decimal]:
    text = (text or "").strip().replace("$", "").replace(",", "")
    if not text:
        return None
    try:
        return D(text)
    except (InvalidOperation, ValueError):
        return None


if TK_AVAILABLE:

    class SetupWizardWindow:
        """One-window wizard driving a SetupWizard state machine."""

        def __init__(
            self,
            root: "tk.Tk",
            wizard: SetupWizard,
            manage_accounts=None,
        ) -> None:
            self.root = root
            self.wizard = wizard
            self.manage_accounts = manage_accounts
            self.vars: dict[str, "tk.StringVar"] = {}
            self.disclosure_var = tk.BooleanVar(value=False)
            self.stock_var = tk.BooleanVar(value=False)
            self.network_label: Optional[tk.Label] = None
            self.printer_list: Optional[tk.Listbox] = None
            self.test_label: Optional[tk.Label] = None

            root.title("Dealer Revenue Rescue - First-Time Setup")
            root.geometry("700x700")
            self.title_label = tk.Label(
                root, text="", font=("Segoe UI", 13, "bold"), anchor="w"
            )
            self.title_label.pack(fill="x", padx=16, pady=(14, 4))
            self.body = tk.Frame(root)
            self.body.pack(fill="both", expand=True, padx=16, pady=6)
            self.error_label = tk.Label(
                root, text="", fg="#a00000", justify="left", anchor="w",
                wraplength=600,
            )
            self.error_label.pack(fill="x", padx=16)
            nav = tk.Frame(root)
            nav.pack(fill="x", padx=16, pady=12)
            self.back_btn = tk.Button(nav, text="Back", command=self._on_back)
            self.back_btn.pack(side="left")
            self.next_btn = tk.Button(nav, text="Next", command=self._on_next)
            self.next_btn.pack(side="right")
            self._show_step()

        # -- helpers ----------------------------------------------------

        def var(self, name: str, initial: str = "") -> "tk.StringVar":
            if name not in self.vars:
                self.vars[name] = tk.StringVar(value=initial)
            return self.vars[name]

        def _clear_body(self) -> None:
            for child in self.body.winfo_children():
                child.destroy()
            self.network_label = None
            self.printer_list = None
            self.test_label = None
            self.parent = self.body

        def _scrolling(self) -> "tk.Frame":
            """A scrollable area inside the body, for the long steps.
            Later _field calls land in it."""
            canvas = tk.Canvas(self.body, highlightthickness=0)
            bar = tk.Scrollbar(self.body, orient="vertical", command=canvas.yview)
            inner = tk.Frame(canvas)
            inner.bind(
                "<Configure>",
                lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
            )
            window = canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.bind(
                "<Configure>", lambda e: canvas.itemconfigure(window, width=e.width)
            )
            canvas.configure(yscrollcommand=bar.set)
            def wheel(event):
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-event.delta / 120), "units")

            canvas.bind_all("<MouseWheel>", wheel)
            bar.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            self.parent = inner
            return inner

        def _note(self, text: str, **options) -> "tk.Label":
            label = tk.Label(
                self.parent, text=text, justify="left", anchor="w",
                wraplength=600, **options,
            )
            label.pack(fill="x", pady=(4, 4))
            return label

        def _field(self, label: str, name: str, initial: str = "") -> None:
            row = tk.Frame(getattr(self, "parent", self.body))
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, width=26, anchor="w").pack(side="left")
            tk.Entry(row, textvariable=self.var(name, initial)).pack(
                side="left", fill="x", expand=True
            )

        def _show_errors(self, errors: list[str]) -> None:
            self.error_label.config(
                text="\n".join("• " + e for e in errors)
            )

        # -- step screens -------------------------------------------------

        def _show_step(self) -> None:
            step = self.wizard.current_step
            self._clear_body()
            self._show_errors([])
            self.title_label.config(text=STEP_TITLES[step])
            self.back_btn.config(
                state="normal" if step not in (SetupStep.NETWORK, SetupStep.DONE) else "disabled"
            )
            self.next_btn.config(
                text="Approve and Finish" if step is SetupStep.APPROVE else "Next"
            )
            builder = {
                SetupStep.NETWORK: self._step_network,
                SetupStep.PROFILE: self._step_profile,
                SetupStep.PRINTER: self._step_printer,
                SetupStep.TRAY_MAPPING: self._step_trays,
                SetupStep.PREFLIGHT: self._step_preflight,
                SetupStep.STATE_RULES: self._step_state_rules,
                SetupStep.TAX_RATES: self._step_tax_rates,
                SetupStep.MATH_CHECK: self._step_math_check,
                SetupStep.APPROVE: self._step_approve,
                SetupStep.DONE: self._step_done,
            }[step]
            builder()

        def _step_network(self) -> None:
            tk.Label(
                self.body,
                text=(
                    "Is this computer on the store network, or does it run by\n"
                    "itself with a printer plugged straight into it (USB)?\n\n"
                    "Store network: connect the cable or Wi-Fi, then run the check.\n"
                    "By itself: type your name and press the second button.\n"
                    "No network is needed for one computer with its own printer."
                ),
                justify="left", anchor="w",
            ).pack(fill="x", pady=6)
            tk.Button(
                self.body, text="Run network check", command=self._run_network
            ).pack(anchor="w", pady=6)
            row = tk.Frame(self.body)
            row.pack(anchor="w", pady=6)
            tk.Label(row, text="Your name:").pack(side="left")
            self.standalone_name = tk.StringVar(value="")
            tk.Entry(row, textvariable=self.standalone_name, width=28).pack(side="left", padx=6)
            tk.Button(
                row, text="This computer runs by itself",
                command=self._choose_standalone,
            ).pack(side="left")
            self.network_label = tk.Label(
                self.body, text="", justify="left", anchor="w", wraplength=580
            )
            self.network_label.pack(fill="x")
            if self.wizard.network_result is not None:
                self.network_label.config(text=self.wizard.network_result.message)

        def _choose_standalone(self) -> None:
            try:
                result = self.wizard.choose_standalone(self.standalone_name.get())
            except ValueError as exc:
                self._show_errors([str(exc)])
                return
            if self.network_label is not None:
                self.network_label.config(text=result.message)
            self._show_errors([])

        def _run_network(self) -> None:
            result = self.wizard.run_network_check()
            if self.network_label is not None:
                self.network_label.config(text=result.message)
            self._show_errors([] if result.ok else [result.message])

        def _step_profile(self) -> None:
            d = self.wizard.dealer
            tk.Label(
                self.body,
                text="Enter your dealership's own details. Nothing is "
                "pre-filled -- this becomes your store's profile.",
                justify="left", anchor="w", wraplength=580,
            ).pack(fill="x", pady=(0, 8))
            self._field("Legal name *", "legal_name", d.legal_name or "")
            self._field("Trading name", "trading_name", d.trading_name or "")
            self._field("Street address *", "street_address", d.street_address or "")
            self._field("City *", "city", d.city or "")
            self._field("State", "state", d.state or "")
            self._field("ZIP code", "zip_code", d.zip_code or "")
            self._field("Phone", "phone", d.phone or "")
            self._field("Contact email", "contact_email", d.contact_email or "")
            self._field("Licence number *", "licence_number", d.licence_number or "")
            self._field("Franchise", "franchise_info", d.franchise_info or "")
            row = tk.Frame(self.body)
            row.pack(fill="x", pady=2)
            tk.Label(row, text="Title filing *", width=26, anchor="w").pack(side="left")
            ttk.Combobox(
                row,
                textvariable=self.var(
                    "title_filing_method", d.title_filing_method or ""
                ),
                values=list(TITLE_FILING_METHODS),
                state="readonly",
            ).pack(side="left", fill="x", expand=True)
            self._field(
                "Dealer fee ($) *", "dealer_fee",
                "" if d.dealer_fee is None else str(d.dealer_fee),
            )
            self._field(
                "Electronic tag fee ($) *", "electronic_tag_fee",
                "" if d.electronic_tag_fee is None else str(d.electronic_tag_fee),
            )
            tk.Checkbutton(
                self.body,
                text="Florida stores: the 501.976(18) disclosure prints on every document "
                "that carries the dealer fee",
                variable=self.disclosure_var,
            ).pack(anchor="w", pady=6)
            self.disclosure_var.set(bool(d.disclosure_501_976_18_confirmed))

        def _collect_profile(self) -> None:
            def s(name: str) -> Optional[str]:
                value = self.vars[name].get().strip()
                return value or None

            self.wizard.set_profile(
                DealerProfile(
                    legal_name=s("legal_name"),
                    trading_name=s("trading_name"),
                    street_address=s("street_address"),
                    city=s("city"),
                    state=s("state"),
                    zip_code=s("zip_code"),
                    phone=s("phone"),
                    contact_email=s("contact_email"),
                    licence_number=s("licence_number"),
                    franchise_info=s("franchise_info"),
                    title_filing_method=s("title_filing_method"),
                    dealer_fee=_money_or_none(self.vars["dealer_fee"].get()),
                    electronic_tag_fee=_money_or_none(
                        self.vars["electronic_tag_fee"].get()
                    ),
                    disclosure_501_976_18_confirmed=self.disclosure_var.get(),
                )
            )

        def _step_printer(self) -> None:
            tk.Label(
                self.body,
                text="Choose the printer the deal pack will print on. "
                "A USB printer plugged into this computer shows here too.",
                justify="left", anchor="w",
            ).pack(fill="x", pady=(0, 8))
            self.printer_list = tk.Listbox(self.body, height=8)
            self.printer_list.pack(fill="x")
            self._refresh_printers()
            tk.Button(
                self.body, text="Refresh list", command=self._refresh_printers
            ).pack(anchor="w", pady=6)

        def _refresh_printers(self) -> None:
            if self.printer_list is None:
                return
            self.printer_list.delete(0, "end")
            for name in self.wizard.available_printers():
                self.printer_list.insert("end", name)
            if self.wizard.selected_printer:
                names = self.wizard.available_printers()
                if self.wizard.selected_printer in names:
                    self.printer_list.selection_set(
                        names.index(self.wizard.selected_printer)
                    )

        def _collect_printer(self) -> None:
            if self.printer_list is not None:
                selection = self.printer_list.curselection()
                if selection:
                    self.wizard.select_printer(
                        self.printer_list.get(selection[0])
                    )

        def _step_trays(self) -> None:
            tk.Label(
                self.body,
                text="Tell the app which tray holds the paper. Every "
                "document -- the contract included -- prints on "
                "standard letter paper.",
                justify="left", anchor="w", wraplength=580,
            ).pack(fill="x", pady=(0, 8))
            default_printer = self.wizard.selected_printer or ""
            for doc_class, label, paper in (
                (DOC_STANDARD_LETTER, "Standard documents", "Letter"),
            ):
                existing = self.wizard.tray_mapping.get(doc_class)
                tk.Label(
                    self.body, text=label, font=("Segoe UI", 10, "bold"),
                    anchor="w",
                ).pack(fill="x", pady=(8, 2))
                self._field(
                    "  Printer", f"{doc_class}_printer",
                    existing.printer_name if existing else default_printer,
                )
                self._field(
                    "  Tray", f"{doc_class}_tray",
                    existing.tray if existing else "",
                )
                self._field(
                    "  Paper", f"{doc_class}_paper",
                    existing.paper if existing else paper,
                )

        def _collect_trays(self) -> None:
            for doc_class in (DOC_STANDARD_LETTER,):
                self.wizard.set_tray_assignment(
                    TrayAssignment(
                        document_class=doc_class,
                        printer_name=self.vars[f"{doc_class}_printer"].get().strip(),
                        tray=self.vars[f"{doc_class}_tray"].get().strip(),
                        paper=self.vars[f"{doc_class}_paper"].get().strip(),
                    )
                )

        def _step_preflight(self) -> None:
            tk.Label(
                self.body,
                text=(
                    "Load standard letter paper in the tray you just "
                    "assigned, run a test print, and check the whole "
                    "page -- top to bottom -- printed fully and "
                    "legibly.\n\nHaving the right printing equipment "
                    "is the store's responsibility; this check proves "
                    "it before an outage does."
                ),
                justify="left", anchor="w", wraplength=580,
            ).pack(fill="x", pady=(0, 8))
            tk.Checkbutton(
                self.body,
                text="Letter paper is loaded in the assigned tray",
                variable=self.stock_var,
                command=lambda: self.wizard.confirm_paper_loaded(self.stock_var.get()),
            ).pack(anchor="w")
            self.stock_var.set(self.wizard.paper_loaded_confirmed)
            tk.Button(
                self.body, text="Run test print", command=self._run_test_print
            ).pack(anchor="w", pady=6)
            self.test_label = tk.Label(
                self.body, text="", justify="left", anchor="w", wraplength=580
            )
            self.test_label.pack(fill="x")
            if self.wizard.test_print_result is not None:
                self.test_label.config(text=self.wizard.test_print_result.message)
            self._field("Your name *", "attester_name", "")
            self._field("Your role *", "attester_role", "")
            tk.Label(
                self.body,
                text="By clicking Next you attest, under your name, that "
                "this printer prints the full page legibly.",
                justify="left", anchor="w", wraplength=580, fg="#444444",
            ).pack(fill="x", pady=6)

        def _run_test_print(self) -> None:
            result = self.wizard.run_test_print()
            if self.test_label is not None:
                self.test_label.config(text=result.message)

        def _collect_preflight(self) -> None:
            self.wizard.confirm_paper_loaded(self.stock_var.get())
            name = self.vars["attester_name"].get()
            role = self.vars["attester_role"].get()
            if name.strip() or role.strip():
                self.wizard.attest(name, role)

        # -- Step 0.6: the rule sheet (Decision 22) ----------------------

        def _radio_row(self, label: str, name: str, choices, initial: str) -> None:
            var = self.var(name, initial)
            tk.Label(self.parent, text=label, anchor="w", justify="left",
                     wraplength=600).pack(fill="x", pady=(8, 0))
            row = tk.Frame(self.parent)
            row.pack(fill="x")
            for value, text in choices:
                tk.Radiobutton(row, text=text, value=value, variable=var).pack(
                    side="left", padx=(0, 8))

        def _step_state_rules(self) -> None:
            w = self.wizard
            rules = w.rules
            typed = rules is not None and not rules.built_in
            self._scrolling()
            self._note(
                "Florida's rules are built in. A store in another state "
                "answers each question below from its DMS setup or its "
                "state's tax office. The math check proves your answers "
                "against your DMS before setup can finish."
            )
            self._radio_row(
                "Where is the store? *", "rules_mode",
                (("florida", "Florida (built-in rules)"),
                 ("typed", "Another state (I answer the questions)")),
                "" if rules is None else ("typed" if typed else "florida"),
            )
            t = rules if typed else sr.StateRules(state="")
            self._field("Your state (two letters)", "rules_state", t.state)
            self._radio_row(
                "1. What tax does your state charge on a vehicle sale?",
                "rules_tax_kind",
                ((sr.TAX_ON_PRICE, "Sales tax on the price"),
                 (sr.TAX_NONE, "No tax"),
                 (sr.UNSUPPORTED, "None of these fit")), t.tax_kind)
            self._note("2. The state rate is on the next screen.", fg="#444444")
            self._radio_row(
                "3. Whose address picks the local rate?", "rules_local_by",
                ((sr.LOCAL_BY_BUYER, "The buyer's home"),
                 (sr.LOCAL_BY_DEALER, "The dealership"),
                 (sr.LOCAL_NONE, "No local tax")), t.local_by)
            self._field("Dealership's place", "rules_dealer_place",
                        t.dealer_place or "")
            self._note("Only when the dealership's address picks the rate. "
                       "Spell it as on the next screen's list.", fg="#444444")
            self._note("4. The local rates are on the next screen.", fg="#444444")
            self._field("5. Local tax on first $", "rules_local_cap",
                        "" if t.local_cap is None else str(t.local_cap))
            self._note("Blank means the local rate applies to the whole "
                       "taxable amount.", fg="#444444")
            self._field("6. Most tax per deal $", "rules_max_tax",
                        "" if t.max_tax is None else str(t.max_tax))
            self._note("Blank means no limit.", fg="#444444")
            self._radio_row(
                "7. Trade-in credit against tax", "rules_trade",
                ((sr.TRADE_FULL, "Full"), (sr.TRADE_NONE, "None"),
                 (sr.TRADE_CAPPED, "Up to a dollar limit")), t.trade_credit)
            self._field("Trade credit limit $", "rules_trade_cap",
                        "" if t.trade_credit_cap is None else str(t.trade_credit_cap))
            self._radio_row(
                "8. Is the manufacturer rebate taken off before tax?",
                "rules_rebate", (("yes", "Yes"), ("no", "No")),
                "yes" if t.rebate_before_tax else "no")
            tk.Label(self.parent, text="9. Which charges are taxed?",
                     anchor="w").pack(fill="x", pady=(8, 0))
            self.taxed_vars = {}
            for charge, text in (
                (sr.CHARGE_DEALER_FEE, "Dealer fee"),
                (sr.CHARGE_TAG_FEE, "Electronic tag fee"),
                (sr.CHARGE_ACCESSORIES, "Accessories and add-ons"),
                (sr.CHARGE_PRODUCTS, "Products sold (service contract, GAP)"),
                (sr.CHARGE_GOVERNMENT_FEES, "10. Title, plate and state fees"),
            ):
                self.taxed_vars[charge] = tk.BooleanVar(value=charge in t.taxed)
                tk.Checkbutton(self.parent, text=text, anchor="w",
                               variable=self.taxed_vars[charge]).pack(fill="x")
            self._radio_row(
                "11. How is a lease taxed?", "rules_lease_tax",
                ((sr.LEASE_MONTHLY, "Each monthly payment"),
                 (sr.LEASE_UPFRONT_TOTAL, "Up front, total of payments"),
                 (sr.LEASE_UPFRONT_PRICE, "Up front, selling price"),
                 (sr.LEASE_NONE, "No tax"),
                 (sr.UNSUPPORTED, "None of these fit")), t.lease_tax)
            self._radio_row(
                "12. Is a lease down payment or rebate taxed up front?",
                "rules_lease_reduction", (("yes", "Yes"), ("no", "No")),
                "yes" if t.lease_reduction_taxed else "no")
            self._note(
                "Questions 13 to 17 are for states whose tax the questions "
                "above cannot say. Leave them blank if they do not apply. "
                "They apply to retail deals.", fg="#444444")
            self.shape_texts = {}

            def lines(name, label, example, initial):
                tk.Label(self.parent, text=label, anchor="w").pack(fill="x", pady=(8, 0))
                self._note(example, fg="#444444")
                box = tk.Text(self.parent, height=3, width=60)
                box.pack(fill="x", pady=2)
                box.insert("1.0", initial)
                self.shape_texts[name] = box

            lines("bands", "13. Extra state tax on a dollar range of the taxable amount",
                  "One per line: rate %, from $, to $ (leave the last blank for no top). "
                  "Example: 2.75, 1600, 3200", mc.bands_text(t.bands))
            lines("brackets", "14. State rate set by the selling price",
                  "One per line: from $, rate %. Start at 0. Example: 0, 0 then "
                  "4000, 3.5 then 10000, 6.5", mc.brackets_text(t.brackets))
            self._radio_row(
                "Price brackets apply to", "rules_brackets_used",
                (("no", "All vehicles"), ("yes", "Used vehicles only")),
                "yes" if t.brackets_used_only else "no")
            lines("classes", "15. Vehicle classes with their own state rate",
                  "One per line: class name, rate %. The deal screen offers these. "
                  "Example: Truck over 10,000 lb GVW, 3", mc.classes_text(t.classes))
            self._field("16. Least tax per deal $", "rules_min_tax",
                        "" if t.min_tax is None else str(t.min_tax))
            self._note("Blank means no least amount.", fg="#444444")
            self._radio_row(
                "17. What does the state tax?", "rules_value_rule",
                ((sr.VALUE_PRICE, "The price"),
                 (sr.VALUE_STATE, "The state's value for the vehicle"),
                 (sr.VALUE_GREATER, "The greater of price and state value")),
                t.value_rule)
            self._radio_row(
                "The state value applies to", "rules_value_used",
                (("yes", "Used vehicles only"), ("no", "All vehicles")),
                "yes" if t.value_used_only else "no")
            self._note("With a state value, the deal screen asks for it. Type it "
                       "from your state's own lookup.", fg="#444444")

        def _collect_state_rules(self) -> None:
            mode = self.vars["rules_mode"].get()
            if mode == "florida":
                self.wizard.set_state_rules(sr.FLORIDA)
                return
            if mode != "typed":
                self.wizard.rules = None
                return
            v = lambda name: self.vars[name].get().strip()
            def dollars(name, label):
                return mc.parse_money(v(name), label=label) if v(name) else None
            self.wizard.set_state_rules(sr.StateRules(
                state=v("rules_state").upper(),
                tax_kind=v("rules_tax_kind") or sr.TAX_ON_PRICE,
                local_by=v("rules_local_by") or sr.LOCAL_BY_BUYER,
                dealer_place=v("rules_dealer_place") or None,
                local_cap=dollars("rules_local_cap", "Local tax limit"),
                max_tax=dollars("rules_max_tax", "Most tax per deal"),
                trade_credit=v("rules_trade") or sr.TRADE_FULL,
                trade_credit_cap=dollars("rules_trade_cap", "Trade-in credit limit"),
                rebate_before_tax=v("rules_rebate") == "yes",
                taxed=frozenset(c for c, var in self.taxed_vars.items() if var.get()),
                lease_tax=v("rules_lease_tax") or sr.LEASE_MONTHLY,
                lease_reduction_taxed=v("rules_lease_reduction") == "yes",
                bands=mc.parse_bands(self.shape_texts["bands"].get("1.0", "end")),
                brackets=mc.parse_brackets(self.shape_texts["brackets"].get("1.0", "end")),
                brackets_used_only=v("rules_brackets_used") == "yes",
                classes=mc.parse_classes(self.shape_texts["classes"].get("1.0", "end")),
                min_tax=dollars("rules_min_tax", "Least tax per deal"),
                value_rule=v("rules_value_rule") or sr.VALUE_PRICE,
                value_used_only=v("rules_value_used") != "no",
            ))

        # -- Step 0.7: local tax rates, and whether the store leases ----

        def _step_tax_rates(self) -> None:
            w = self.wizard
            self._scrolling()
            if w.typed_rules:
                self._note(
                    "Enter your local tax rates. Put your own place first, "
                    "then every place your buyers usually register in. One "
                    "place per line: the name, a comma, and the rate in "
                    "percent. Example: Sample County, 1.5. Leave it empty if "
                    "your state has no local tax."
                )
            else:
                self._note(
                    "County surtax rates from the Florida form DR-15DSS. Click "
                    "the year below to fill all 67 counties from the built-in "
                    "table, or type them: one county per line, the name, a "
                    "comma, and the rate in percent. Example: Pinellas, 1"
                )
            self.county_text = tk.Text(self.parent, height=8, width=60)
            self.county_text.pack(fill="x", pady=4)
            self.county_text.insert("1.0", mc.county_table_text(w.county_surtax_rates))
            self.loaded_rates_year = w.county_rates_year
            if not w.typed_rules:
                self._built_in_rates_row()
            self._field(
                "State sales tax (%) *", "state_tax_pct",
                format((w.state_tax_rate * 100).normalize(), "f"),
            )
            self._note(
                "A buyer from a county that is not on this list cannot be "
                "certified until a manager adds that county here."
            , fg="#444444")
            self.leases_var = tk.StringVar(
                value="" if w.store_leases is None else ("yes" if w.store_leases else "no")
            )
            row = tk.Frame(self.parent)
            row.pack(fill="x", pady=(10, 2))
            tk.Label(row, text="Does the store write leases? *", anchor="w").pack(side="left")
            tk.Radiobutton(row, text="Yes", value="yes", variable=self.leases_var).pack(side="left")
            tk.Radiobutton(row, text="No", value="no", variable=self.leases_var).pack(side="left")
            lp = w.lease_preset
            self._field("Lessor name (if yes)", "lessor_name", lp.lessor_name or "")
            self._field("Lease title holder (if yes)", "title_holder", lp.title_holder or "")

        def _built_in_rates_row(self) -> None:
            """Decision 32: fill all 67 counties from a year's built-in
            DR-15DSS table. The manager still reviews and approves them."""
            row = tk.Frame(self.parent)
            row.pack(fill="x", pady=(2, 2))
            tk.Label(row, text="Or fill every county from the built-in table:",
                     anchor="w").pack(side="left")
            for edition in fl_tables.editions():
                tk.Button(row, text=str(edition.year),
                          command=lambda e=edition: self._fill_built_in(e)).pack(side="left", padx=2)
            self.rates_source_label = tk.Label(self.parent, text=self._rates_source_text(),
                                               justify="left", anchor="w", wraplength=640,
                                               fg="#444444")
            self.rates_source_label.pack(fill="x")

        def _rates_source_text(self) -> str:
            edition = (fl_tables.edition_for(self.loaded_rates_year)
                       if self.loaded_rates_year is not None else None)
            if edition is None:
                return ("These rates are typed by hand. Deals use them in any year "
                        "until a manager changes them.")
            return (f"Loaded: {edition.label}. Source: {edition.source} Deals in "
                    f"another year cannot be certified on these rates. Changing "
                    "any line makes this your own typed table.")

        def _fill_built_in(self, edition) -> None:
            self.county_text.delete("1.0", "end")
            self.county_text.insert("1.0", fl_tables.table_text(edition))
            self.loaded_rates_year = edition.year
            self.rates_source_label.configure(text=self._rates_source_text())

        def _collect_tax_rates(self) -> None:
            rates = mc.parse_county_table(self.county_text.get("1.0", "end"))
            state = mc.parse_percent(self.vars["state_tax_pct"].get(), label="State sales tax")
            self.wizard.set_tax_data(state_tax_rate=state, county_surtax_rates=rates,
                                     county_rates_year=getattr(self, "loaded_rates_year", None))
            answer = self.leases_var.get()
            if answer in ("yes", "no"):
                self.wizard.set_store_leases(
                    answer == "yes",
                    lessor_name=self.vars["lessor_name"].get(),
                    title_holder=self.vars["title_holder"].get(),
                )

        # -- Step 0.8: the math check (decisions 21 and 22) -------------

        def _step_math_check(self) -> None:
            w = self.wizard
            self.result_labels = {}
            self._scrolling()
            self._note(
                "Prove the app gets your numbers. Pull up one recent finance "
                "deal in your DMS. Type in its numbers, then the tax, amount "
                "financed and payment the DMS shows. The dealer fee and "
                f"electronic tag fee come from Step 0.2 (${w.dealer.dealer_fee} "
                f"and ${w.dealer.electronic_tag_fee}). Type no customer name, "
                "VIN or stock number."
            )
            if w.typed_rules:
                self._note(
                    "Your store typed its own rules, so check two retail "
                    "deals: one with no trade, then one with a trade. Every "
                    "figure must match your DMS."
                )
            self._math_section(mc.KIND_RETAIL)
            if w.typed_rules:
                self._math_section(mc.KIND_RETAIL_TRADE)
            if w.store_leases:
                self._note(
                    "\nNow one recent lease from your DMS lease worksheet. The "
                    "predelivery fee is your dealer fee from Step 0.2."
                )
                self._math_section(mc.KIND_LEASE)

        def _math_specs(self, kind: str):
            entry = self.wizard.check_entry(kind)
            if kind == mc.KIND_LEASE:
                return mc.LEASE_FIELDS, mc.LEASE_DMS_FIELDS, entry
            fields = mc.RETAIL_FIELDS
            rules = self.wizard.rules
            if rules is not None and rules.uses_vehicle_details:
                fields = fields + mc.RETAIL_SHAPE_FIELDS
            return fields, mc.RETAIL_DMS_FIELDS, entry

        def _math_section(self, kind: str) -> None:
            fields, dms_fields, entry = self._math_specs(kind)
            title = {
                mc.KIND_RETAIL: "Retail finance deal"
                + (" (no trade)" if self.wizard.typed_rules else ""),
                mc.KIND_RETAIL_TRADE: "Retail finance deal (with a trade)",
                mc.KIND_LEASE: "Lease",
            }[kind]
            tk.Label(self.parent, text=title, font=("Segoe UI", 11, "bold"),
                     anchor="w").pack(fill="x", pady=(10, 2))
            texts = mc.entry_to_text(entry, fields + dms_fields) if entry else {}
            for key, label, _kind in fields:
                self._field(label, f"mc_{kind}_{key}", texts.get(key, ""))
            self._note("What your DMS shows:", fg="#444444")
            for key, label, _kind in dms_fields:
                self._field(label, f"mc_{kind}_{key}", texts.get(key, ""))
            tk.Button(self.parent, text="Check the math",
                      command=lambda: self._run_math(kind)).pack(anchor="w", pady=6)
            self.result_labels[kind] = tk.Label(
                self.parent, text="", justify="left", anchor="w",
                font=("Consolas", 9), wraplength=640,
            )
            self.result_labels[kind].pack(fill="x")
            if not self.wizard.typed_rules:
                tk.Label(self.parent, anchor="w", fg="#444444", justify="left", wraplength=600,
                         text="If a figure differs and the app is right, a manager "
                         "records why here:").pack(fill="x", pady=(6, 0))
                self._field("Manager name", f"mc_{kind}_override_name", "")
                self._field("Why the figures differ", f"mc_{kind}_override_reason", "")
                tk.Button(self.parent, text="Record why",
                          command=lambda: self._record_override(kind)).pack(anchor="w", pady=4)
            self._show_math(kind)

        def _texts(self, kind: str) -> dict:
            fields, dms_fields, _entry = self._math_specs(kind)
            return {key: self.vars[f"mc_{kind}_{key}"].get()
                    for key, _l, _k in fields + dms_fields}

        def _store_math(self, kind: str) -> None:
            texts = self._texts(kind)
            if not any(v.strip() for v in texts.values()):
                return
            if kind != mc.KIND_LEASE:
                self.wizard.set_retail_check(mc.retail_entry_from_text(texts), kind)
            else:
                self.wizard.set_lease_check(mc.lease_entry_from_text(texts))

        def _run_math(self, kind: str) -> None:
            try:
                self._store_math(kind)
            except ValueError as exc:
                self._show_errors(str(exc).splitlines())
                return
            self._show_errors([])
            self._show_math(kind)

        def _show_math(self, kind: str) -> None:
            label = self.result_labels.get(kind)
            comparison = self.wizard.compare(kind)
            if label is None or comparison is None:
                return
            text = mc.comparison_text(comparison)
            override = self.wizard.math_overrides.get(kind)
            if (override and not comparison.all_match
                    and override.get("fingerprint") == comparison.fingerprint):
                text += (f"\nRecorded by {override['manager_name']}: "
                         f"{override['reason']}")
            label.config(
                text=text,
                fg="#006400" if comparison.all_match else "#a00000",
            )

        def _record_override(self, kind: str) -> None:
            try:
                self._store_math(kind)
                self.wizard.record_math_override(
                    kind,
                    self.vars[f"mc_{kind}_override_name"].get(),
                    self.vars[f"mc_{kind}_override_reason"].get(),
                )
            except ValueError as exc:
                self._show_errors(str(exc).splitlines())
                return
            self._show_errors([])
            self._show_math(kind)

        def _collect_math_check(self) -> None:
            errors = []
            for kind in self.wizard.math_check_kinds():
                try:
                    self._store_math(kind)
                except ValueError as exc:
                    errors.extend(str(exc).splitlines())
            if errors:
                raise ValueError("\n".join(errors))

        def _step_approve(self) -> None:
            tk.Label(self.body, text='Create at least two admin accounts before finishing setup.',
                     justify='left', wraplength=580).pack(fill='x', pady=6)
            if self.manage_accounts is not None:
                tk.Button(self.body, text='Create / manage accounts', command=self.manage_accounts).pack(anchor='w')
            self.single_admin_var = tk.BooleanVar(value=False)
            if self.wizard.needs_single_admin_ack:
                tk.Checkbutton(self.body,
                    text=f'{self.wizard.actor.display_name} ({self.wizard.actor.username}): I acknowledge that one admin means a forgotten PIN may require dealer IT recovery.',
                    variable=self.single_admin_var, wraplength=560, justify='left').pack(anchor='w', pady=8)
            d = self.wizard.dealer
            lines = [
                "Review your setup before approving:",
                "",
                f"Dealer: {d.legal_name or ''}",
                f"Title filing: {d.title_filing_method or ''}",
                f"Dealer fee: ${d.dealer_fee}   Electronic tag fee: "
                f"${d.electronic_tag_fee}",
                f"Printer: {self.wizard.selected_printer or ''}",
            ]
            for doc_class, assignment in self.wizard.tray_mapping.items():
                lines.append(
                    f"  {doc_class}: {assignment.printer_name}, "
                    f"{assignment.tray}, {assignment.paper}"
                )
            if self.wizard.attestation is not None:
                lines.append(
                    f"Equipment attested by "
                    f"{self.wizard.attestation.attester_name} "
                    f"({self.wizard.attestation.attester_role})"
                )
            rules = self.wizard.rules
            lines += [
                "",
                "Tax rules: " + (
                    "Florida (built in)" if rules is None or rules.built_in
                    else f"{rules.state}, typed by the store (Step 0.6)"),
                (f"County rates: {len(self.wizard.county_surtax_rates)} "
                 "counties from the DR-15DSS (Step 0.7)")
                if not self.wizard.typed_rules else
                (f"Local rates: {len(self.wizard.county_surtax_rates)} "
                 "places (Step 0.7)"),
                "Leases: " + ("yes, lessor "
                    + (self.wizard.lease_preset.lessor_name or "")
                    if self.wizard.store_leases else "no"),
                "Math check: "
                + ", ".join(
                    f"{CHECK_LABELS[kind]} "
                    + ("matched your DMS" if self.wizard.compare(kind).all_match
                       else "differs, manager recorded why")
                    for kind in self.wizard.math_check_kinds()
                ),
            ]
            tk.Label(
                self.body, text="\n".join(lines), justify="left", anchor="w"
            ).pack(fill="x")
            self._field("Approver name (manager) *", "approved_by", "")

        def _collect_approve(self) -> None:
            self.wizard.approve(self.vars["approved_by"].get())
            if self.wizard.needs_single_admin_ack and self.single_admin_var.get():
                self.wizard.acknowledge_single_admin(self.wizard.actor)

        def _step_done(self) -> None:
            tk.Label(
                self.body,
                text=(
                    "Setup is complete and saved.\n\nDeal entry is now "
                    "unlocked on this machine. Keep your setup file -- "
                    "it holds your dealer profile, presets, and the "
                    "equipment attestation."
                ),
                justify="left", anchor="w", wraplength=580,
            ).pack(fill="x", pady=8)
            self.next_btn.config(state="disabled")

        # -- navigation -----------------------------------------------------

        _COLLECTORS = {
            SetupStep.PROFILE: "_collect_profile",
            SetupStep.PRINTER: "_collect_printer",
            SetupStep.TRAY_MAPPING: "_collect_trays",
            SetupStep.PREFLIGHT: "_collect_preflight",
            SetupStep.STATE_RULES: "_collect_state_rules",
            SetupStep.TAX_RATES: "_collect_tax_rates",
            SetupStep.MATH_CHECK: "_collect_math_check",
            SetupStep.APPROVE: "_collect_approve",
        }

        def _collect_current(self) -> None:
            method = self._COLLECTORS.get(self.wizard.current_step)
            if method:
                getattr(self, method)()

        def _on_next(self) -> None:
            step = self.wizard.current_step
            try:
                self._collect_current()
            except Exception as exc:
                self._show_errors([str(exc)])
                return
            if step is SetupStep.APPROVE:
                errors = self.wizard.validate_step(SetupStep.APPROVE)
                if errors:
                    self._show_errors(errors)
                    return
                try:
                    self.wizard.finish()
                except Exception as exc:  # SetupIncompleteError etc.
                    self._show_errors([str(exc)])
                    return
                self._show_step()
                return
            errors = self.wizard.advance()
            if errors:
                self._show_errors(errors)
                return
            self._show_step()

        def _on_back(self) -> None:
            try:
                self._collect_current()
            except ValueError:
                pass  # half-typed entries need not block going back
            self.wizard.back()
            self._show_step()


def run_setup_wizard(
    setup_path: Path = DEFAULT_SETUP_PATH,
    printer_provider: Optional[PrinterProvider] = None,
) -> int:
    """Enter shared sign-in and disclaimer gate before Step 0."""
    from .app_ui import run_app
    return run_app(initial_action='setup', setup_path=setup_path,
                   printer_provider=printer_provider)


if __name__ == "__main__":
    raise SystemExit(run_setup_wizard())
