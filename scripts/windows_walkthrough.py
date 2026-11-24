"""Drive the real app window by window and screenshot every screen.

Runs on the GitHub Windows runner (and under Xvfb on Linux for
development). It walks a fresh install the way a store would: first
admin, the notice, a second admin, setup Steps 0.1 to 0.8 (with the
math check), the home screen, the demo title form, and a practice
drill up to the print window.

Printing: the printer LIST is the machine's real Windows printers, but
nothing is sent to a printer here. A cloud machine has no paper. The
driver path itself is proved by tests/test_printers_windows.py; physical
printing is the separate paper test (docs/PRINT-TEST.md).

Every value typed is SAMPLE data (decision 17).

    python scripts/windows_walkthrough.py --out shots
    python scripts/windows_walkthrough.py --out shots-typed --typed-rules

``--typed-rules`` walks a store in another state (Decision 22): it
types a made-up sample rule sheet ("ZZ", not any real state's law)
and checks two retail deals, one with a trade.

``--forms DIR`` adds a store's own forms the way a store does: each
form's JPEG pages and value placements (``DIR/forms.json`` and
``DIR/pages/<doc_id>/page-N.jpg``). It saves each form's SAMPLE PDF,
approves the forms, then prints the drill's whole pack to the
recording printer and keeps every job's PDF in ``OUT/pack``.

``--app-src DIR`` runs the program from an installed copy (the folder
holding ``dealerpaperwork``, e.g. ``C:\\DealerRevenueRescue\\app``) instead
of this checkout.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import traceback
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))
if "--app-src" in sys.argv:
    # Before any dealerpaperwork import: the installed copy wins.
    sys.path.insert(0, str(Path(sys.argv[sys.argv.index("--app-src") + 1]).resolve()))

import tkinter as tk  # noqa: E402
from tkinter import messagebox  # noqa: E402

from dealerpaperwork import app_ui, deal_ui, docs_ui, math_check as mc  # noqa: E402
from dealerpaperwork import print_ui, setup_ui  # noqa: E402
from dealerpaperwork.printers import FakePrinterProvider, get_printer_provider  # noqa: E402
from dealerpaperwork.startup import StartupFlow  # noqa: E402

PAUSE_MS = 700
MAX_WAITS = 40  # a step may wait up to 40 x PAUSE_MS for its window
captured: dict[str, object] = {}
log: list[str] = []


class NotReady(Exception):
    """The window this step drives has not opened yet; try again."""


def window(key: str):
    if key not in captured:
        raise NotReady(key)
    return captured[key]


def _capture(cls, key):
    class Captured(cls):
        def __init__(self, *args, **kwargs):
            captured[key] = self
            super().__init__(*args, **kwargs)
    return Captured


def screenshot(path: Path) -> None:
    if os.name == "nt":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
            "$m=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($m);"
            "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
            f"$m.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png)"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
    else:
        subprocess.run(["import", "-window", "root", str(path)], check=True)


def scroll_to_text(host, text: str) -> None:
    """Scroll the canvas under host so the label showing text is at the top."""
    def walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from walk(child)
    canvas = next(c for c in walk(host) if isinstance(c, tk.Canvas))
    label = next(c for c in walk(canvas) if isinstance(c, tk.Label) and c.cget("text") == text)
    canvas.update_idletasks()
    inner = canvas.nametowidget(canvas.itemcget(canvas.find_all()[0], "window"))
    y = label.winfo_rooty() - inner.winfo_rooty()
    canvas.yview_moveto(max(0, y - 8) / max(1, inner.winfo_height()))


class Walk:
    def __init__(self, root: tk.Tk, app, out: Path):
        self.root, self.app, self.out = root, app, out
        self.n = 0
        self.steps = []
        self.failed = False
        self.waits = 0
        self.in_shot = False

    def shot(self, name: str) -> None:
        # update() runs any timer that is due, including the next step's
        # tick. A step must never start inside another step's screenshot
        # (it did on a slow Windows runner: setup_approve ran inside the
        # math check), so tick() waits while this flag is set.
        self.in_shot = True
        try:
            self.root.update_idletasks()
            self.root.update()
        finally:
            self.in_shot = False
        self.n += 1
        path = self.out / f"{self.n:02d}-{name}.png"
        screenshot(path)
        log.append(f"shot {path.name}")

    def then(self, fn):
        self.steps.append(fn)
        return self

    def run(self):
        def tick():
            if self.in_shot:
                self.root.after(PAUSE_MS, tick)
                return
            if not self.steps:
                if not self.failed:
                    log.append("WALKTHROUGH COMPLETE")
                self.root.after(PAUSE_MS, self.root.destroy)
                return
            fn = self.steps.pop(0)
            # Schedule the next step first: a step that opens a modal
            # window blocks here, and the next step runs inside it.
            self.root.after(PAUSE_MS, tick)
            try:
                fn()
                self.waits = 0
            except NotReady as exc:
                self.waits += 1
                if self.waits > MAX_WAITS:
                    log.append(f"FAILED: window {exc} never opened")
                    self.failed = True
                    self.steps.clear()
                    self.shot("failure")
                else:
                    self.steps.insert(0, fn)
            except Exception:
                log.append("FAILED in " + fn.__name__ + "\n" + traceback.format_exc())
                self.failed = True
                self.steps.clear()
                self.shot("failure")
        self.root.after(PAUSE_MS, tick)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--typed-rules", action="store_true")
    # Decision 30: a typed sheet that also uses questions 13, 15 and 16
    # (an extra tax on $1,600-$3,200, a vehicle class, a $75 least tax).
    parser.add_argument("--shaped-rules", action="store_true")
    parser.add_argument("--forms", default=None)
    parser.add_argument("--app-src", default=None)
    args = parser.parse_args()
    import dealerpaperwork
    log.append(f"program: {Path(dealerpaperwork.__file__).resolve().parent} "
               f"version {dealerpaperwork.__version__}")
    forms = Path(args.forms).resolve() if args.forms else None
    shaped = args.shaped_rules
    typed = args.typed_rules or shaped
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix="rr-walk-"))

    # Real printer names, no real printing.
    try:
        names = get_printer_provider().list_printers()
    except Exception as exc:  # noqa: BLE001 - report and carry on
        log.append(f"printer list failed: {exc}")
        names = []
    log.append(f"printers on this machine: {names}")
    provider = FakePrinterProvider(names or ["Sample Printer"])

    # Capture each window the app opens; answer dialogs without a person.
    setup_ui.SetupWizardWindow = _capture(setup_ui.SetupWizardWindow, "setup")
    deal_ui.DealWindow = _capture(deal_ui.DealWindow, "deal")
    print_ui.PrintWindow = _capture(print_ui.PrintWindow, "print")
    docs_ui.DocumentsWindow = _capture(docs_ui.DocumentsWindow, "docs")
    messagebox.askyesno = lambda *a, **k: (log.append(f"askyesno: {a[:2]}"), True)[1]
    messagebox.showinfo = lambda *a, **k: log.append(f"showinfo: {a[:2]}")
    messagebox.showerror = lambda *a, **k: log.append(f"SHOWERROR: {a[:2]}")

    root = tk.Tk()
    flow = StartupFlow(data)
    app = app_ui.Application(root, flow, printer_provider=provider)
    w = Walk(root, app, out)

    def first_admin():
        w.shot("first-admin")
        flow.bootstrap(username="manager", display_name="Pat Manager (SAMPLE)",
                       secret="1357", confirmation="1357")
        app.show_stage()

    def notice():
        w.shot("notice")
        flow.accept()
        flow.users.create_user(actor=flow.user, username="admin2",
                               display_name="Second Admin (SAMPLE)", role="admin",
                               secret="2468")
        app.show_stage()  # opens setup (modal) on the next idle

    def setup_network():
        s = window("setup")
        s.wizard.choose_standalone("Pat Manager (SAMPLE)")
        s._show_step()
        w.shot("setup-0.1-network")
        s._on_next()

    def setup_profile():
        s = window("setup")
        for key, value in {
            "legal_name": "Sample Motors LLC (SAMPLE)", "street_address": "100 Sample Street",
            "city": "Sample City", "state": "FL", "zip_code": "00000",
            "licence_number": "SAMPLE-000", "title_filing_method": "in-house EFS",
            "dealer_fee": "1199", "electronic_tag_fee": "199",
        }.items():
            s.vars[key].set(value)
        s.disclosure_var.set(True)
        w.shot("setup-0.2-dealership")
        s._on_next()

    def setup_printer():
        s = window("setup")
        s.printer_list.selection_set(0)
        w.shot("setup-0.3-printer")
        s._on_next()

    def setup_trays():
        s = window("setup")
        s.vars["standard_letter_printer"].set(provider.list_printers()[0])
        s.vars["standard_letter_tray"].set("Tray 1")
        s.vars["standard_letter_paper"].set("Letter")
        w.shot("setup-0.4-trays")
        s._on_next()

    def setup_preflight():
        s = window("setup")
        s.stock_var.set(True)
        s._run_test_print()
        s.vars["attester_name"].set("Pat Manager (SAMPLE)")
        s.vars["attester_role"].set("General Manager")
        w.shot("setup-0.5-printer-check")
        s._on_next()

    def setup_rules():
        s = window("setup")
        if typed:
            from test_math_check import typed_sheet
            sheet = typed_sheet()
            s.vars["rules_mode"].set("typed")
            s.vars["rules_state"].set(sheet.state)
            s.vars["rules_tax_kind"].set(sheet.tax_kind)
            s.vars["rules_local_by"].set(sheet.local_by)
            s.vars["rules_trade"].set(sheet.trade_credit)
            s.vars["rules_lease_tax"].set(sheet.lease_tax)
            for charge, var in s.taxed_vars.items():
                var.set(charge in sheet.taxed)
            if shaped:
                s.shape_texts["bands"].insert("1.0", "2.75, 1600, 3200")
                s.shape_texts["classes"].insert("1.0", "Truck over 10,000 lb GVW, 3")
                s.vars["rules_min_tax"].set("75")
        else:
            s.vars["rules_mode"].set("florida")
        w.shot("setup-0.6-state-rules")
        s._on_next()

    def setup_tax():
        s = window("setup")
        s.county_text.insert("1.0", "Sample County, 1.5")
        if typed:
            s.vars["state_tax_pct"].set("5")
        s.leases_var.set("no")
        w.shot("setup-0.7-local-rates")
        s._on_next()

    def scroll_to_end(s):
        s.root.update_idletasks()
        for child in s.body.winfo_children():
            if isinstance(child, tk.Canvas):
                child.yview_moveto(1.0)

    def setup_math():
        s = window("setup")
        from test_math_check import retail_entry
        if typed:
            # The hand-figured deals of tests/test_math_check.py; the
            # payment is typed as the app shows it.
            deals = (
                ("retail", retail_entry(trade_allowance=Decimal("0"), trade_payoff=Decimal("0"),
                                        dms_total_tax=Decimal("1152.65"),
                                        dms_amount_financed=Decimal("19427.50"))),
                ("retail_trade", retail_entry(dms_total_tax=Decimal("860.15"),
                                              dms_amount_financed=Decimal("16735.00"))),
            )
            for kind, entry in deals:
                app = {l.label: l.app for l in s.wizard.set_retail_check(entry, kind).lines}
                entry.dms_monthly_payment = app["Monthly payment"]
                if shaped:
                    # No DMS here to read: the sheet's extra tax changes
                    # the hand figures, so the walk types the app's own.
                    entry.dms_total_tax = app["Total sales tax"]
                    entry.dms_amount_financed = app["Amount financed"]
                    app = {l.label: l.app for l in s.wizard.set_retail_check(entry, kind).lines}
                    entry.dms_monthly_payment = app["Monthly payment"]
        else:
            deals = (("retail", retail_entry()),)
        for kind, entry in deals:
            for key, value in mc.entry_to_text(
                    entry, mc.RETAIL_FIELDS + mc.RETAIL_DMS_FIELDS).items():
                s.vars[f"mc_{kind}_{key}"].set(value)
        w.shot("setup-0.8-math-check-typed")
        for kind, _entry in deals:
            s._run_math(kind)
        scroll_to_end(s)
        w.shot("setup-0.8-math-check-result")
        s._on_next()

    def setup_approve():
        s = window("setup")
        s.vars["approved_by"].set("Pat Manager (SAMPLE)")
        w.shot("setup-0.9-approve")
        s._on_next()

    def setup_done():
        s = window("setup")
        w.shot("setup-done")
        s.root.destroy()  # back to home

    def home():
        w.shot("home")
        app.open_action("documents")  # modal; the next step runs inside it

    def documents():
        d = window("docs")
        d._add_demo_form()
        w.shot("documents-demo-form")
        if not forms:
            d.root.destroy()

    def store_forms():
        # Its own step, with no screenshot: adding the forms takes a few
        # seconds, and a screenshot's update() would run the next step
        # early, nested inside this one.
        add_store_forms(window("docs"))

    def store_forms_shot():
        d = window("docs")
        d._refresh_documents()
        w.shot("documents-store-forms-approved")
        d.root.destroy()

    def add_store_forms(d):
        import json
        from dealerpaperwork.doc_library import (
            CONDITION_LEASE, CONDITION_RETAIL_NEW, CONDITION_RETAIL_USED, FieldPlacement)
        spec = json.loads((forms / "forms.json").read_text(encoding="utf-8"))
        samples = out / "forms"
        for form in spec["forms"]:
            pages = sorted((forms / "pages" / form["doc_id"]).glob("page-*.jpg"),
                           key=lambda p: int(p.stem.split("-")[1]))
            when = ((CONDITION_LEASE,) if form["prints_in"] == "lease"
                    else (CONDITION_RETAIL_NEW, CONDITION_RETAIL_USED))
            d.library.add_document(
                source_paths=pages, doc_id=form["doc_id"], title=form["title"],
                when_it_prints=when, created_by=flow.user.username,
                fields=[FieldPlacement(*p) for p in form["placements"]])
            sample = d.actions.write_sample(form["doc_id"], samples / f"{form['doc_id']}-sample.pdf")
            d.library.approve_document(form["doc_id"], flow.user)
            log.append(f"form {form['doc_id']}: {len(pages)} pages, "
                       f"{len(form['placements'])} values placed, SAMPLE {sample.name}, approved")

    def drill():
        app.open_action("drill")

    def drill_step(label):
        def step():
            deal = window("deal")
            if label == "buyer":
                # A drill leaves cash or finance to the person running it.
                deal.vars["transaction_type"].set("finance")
                deal.vars["lender_name"].set("Sample Bank (SAMPLE)")
                deal.vars["lender_street"].set("PO Box 0000")
                deal.vars["lender_city"].set("Sample City")
                deal.vars["lender_state"].set("FL")
                deal.vars["lender_zip"].set("00000")
            if label == "trade-money" and forms:
                # Sell two of the store's products: their own contracts
                # print, with their own figures; the rest stay out.
                sold = {"fi-express-3": ("1995", "60 months / 100,000 miles", "", "EX-SAMPLE-1"),
                        "fi-tire-wheel": ("799", "60 months", "", "TW-SAMPLE-1")}
                for doc_id, figures in sold.items():
                    for key, value in zip(("price", "term", "coverage", "contract_number"), figures):
                        deal.vars[f"product_{doc_id}_{key}"].set(value)
                deal.vars["money_optional_products"].set("2794")
            w.shot(f"drill-{label}")
            if label == "trade-money" and forms:
                # The products section sits below the money: scroll the
                # step to its heading and take a second shot of it.
                scroll_to_text(deal.body_host, "Products sold (F&I)")
                w.shot("drill-products-sold")
            before = deal.session.current_step
            if label == "review":
                deal._on_certify()
            else:
                deal._on_next()
            if label != "review" and deal.session.current_step == before:
                # Never screenshot the same screen under the next name.
                raise RuntimeError(
                    f"drill stayed on {before}: {deal.error_label.cget('text')}")
        step.__name__ = f"drill_{label}"
        return step

    def drill_certified():
        deal = window("deal")
        w.shot("drill-certified")
        deal._on_next()  # closes the deal window; the print window opens

    def print_window():
        p = window("print")
        w.shot("drill-print-window")
        if not forms:
            p.root.destroy()

    def print_pack():
        # No screenshot here, for the same reason as store_forms.
        window("print")._print_all()

    def print_pack_saved():
        p = window("print")
        w.shot("drill-print-window-printed")
        pack = out / "pack"
        pack.mkdir(exist_ok=True)
        for number, job in enumerate(provider.jobs, 1):
            body = job.payload
            suffix = ".pdf" if isinstance(body, bytes) else ".txt"
            name = f"{number:02d}-{job.doc_id or 'job'}{suffix}"
            (pack / name).write_bytes(body if isinstance(body, bytes) else str(body).encode("utf-8"))
            log.append(f"printed {name}: {job.title!r} on {job.printer_name!r} tray {job.tray!r}")
        if not provider.jobs:
            raise RuntimeError("print all sent no jobs")
        p.root.destroy()

    def home_after():
        w.shot("home-after-drill")

    (w.then(first_admin).then(notice).then(setup_network).then(setup_profile)
      .then(setup_printer).then(setup_trays).then(setup_preflight).then(setup_rules).then(setup_tax)
      .then(setup_math).then(setup_approve).then(setup_done).then(home)
      .then(documents))
    if forms:
        w.then(store_forms).then(store_forms_shot)
    (w.then(drill)
      .then(drill_step("buyer")).then(drill_step("vehicle")).then(drill_step("trade-money"))
      .then(drill_step("worksheet")).then(drill_step("review"))
      .then(drill_certified).then(print_window))
    if forms:
        w.then(print_pack).then(print_pack_saved)
    w.then(home_after)
    w.run()
    root.mainloop()
    (out / "walkthrough-log.txt").write_text("\n".join(log), encoding="utf-8")
    print("\n".join(log))
    return 0 if (not w.failed and "WALKTHROUGH COMPLETE" in log) else 1


if __name__ == "__main__":
    raise SystemExit(main())
