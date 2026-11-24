"""Tkinter window for the Stage 4 print pipeline.

This module is ONLY a driver: every rule lives in ``print_pipeline.py``
(tray resolution, failure/resume, void/reprint) and
is tested headless. The window shows, for one certified (or draft)
deal:

* the **pack checklist** -- every pack document with its class,
  status, and copies; buttons for Print all, Resume, Print selected;
* (Stage 5 note: the Stage 4 stock-confirmation dialog before the
  contract printed is retired -- every document is letter size now,
  so there is no special stock to confirm. The callback wiring stays
  available in the pipeline for a future special-stock document, but
  nothing in the standard pack asks it.)
* a **void dialog** (reason required in practice, recorded in the
  audit log) and reprint for a voided item;
* a **calibration screen** -- pick printer + document, see the
  current alignment offsets, nudge them with arrow buttons in 0.5 mm
  steps, save, and print a test page with the current offsets through
  the provider.

Inputs are the Stage 2 setup file (printer/tray assignments + the
approved preset), the Stage 3 deal file, and the print config. The
acting user is the logged-in user from the deal flow; this window
takes the deal's session user for audit attribution.

Tkinter ships with Windows Python. The import is guarded like the
other UI modules: without Tk, importing this module still succeeds
and ``run_print_pipeline()`` explains.

Launch (from the repo root, on Windows):

    set PYTHONPATH=src
    python -m dealerpaperwork.print_ui
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

try:  # Tkinter ships with Windows Python; absent on some headless boxes.
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk

    TK_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only without Tk
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    simpledialog = None  # type: ignore[assignment]
    TK_AVAILABLE = False

from .audit import DEFAULT_AUDIT_PATH, AuditLog
from .deal_flow import DEFAULT_DEALS_DIR, DealSession
from .pack import KIND_SECURE_STOCK
from .print_config import DEFAULT_PRINT_CONFIG_PATH, PrintConfig
from .print_pipeline import PrintPipelineError, PrintSession
from .printers import get_printer_provider
from .setup_wizard import DEFAULT_SETUP_PATH
from .text_renderer import render_test_page
from .users import DEFAULT_USERS_PATH, UserStore

STATUS_LABELS = {
    "pending": "Pending",
    "printed": "Printed",
    "failed": "FAILED",
    "pulled": "Pulled from secure stock",
}


if TK_AVAILABLE:

    class PrintWindow:
        """Pack checklist + calibration, for one deal's print session."""

        def __init__(
            self,
            root: "tk.Tk",
            *,
            session: PrintSession,
            print_config: PrintConfig,
        ) -> None:
            self.root = root
            self.session = session
            self.print_config = print_config
            self.provider = session.provider
            # Generic stock-confirmation hook: the pipeline keeps the
            # mechanism, but since Stage 5 no standard-pack document
            # is gated on it (all documents are letter size).
            self.session.stock_confirmation = self._ask_stock_confirmation

            root.title("Dealer Revenue Rescue - Print pack")
            self.frame = ttk.Frame(root, padding=12)
            self.frame.grid(sticky="nsew")
            self.status_var = tk.StringVar(value="")
            self._build_checklist()
            self._build_calibration()
            self.refresh()

        # -- checklist ----------------------------------------------------

        def _build_checklist(self) -> None:
            deal = self.session.deal
            heading = "Print pack"
            if self.session.draft_only:
                heading += "  (DRAFT - deal not certified; every page is watermarked)"
            ttk.Label(self.frame, text=heading, font=("TkDefaultFont", 12, "bold")).grid(
                row=0, column=0, columnspan=4, sticky="w", pady=(0, 6)
            )
            buyer = getattr(getattr(deal, "inputs", None), "buyer", None)
            if buyer is not None:
                ttk.Label(
                    self.frame,
                    text=f"Buyer: {buyer.full_name}    Deal ID: {self.session.deal_id}",
                ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 8))

            columns = ("title", "class", "status", "copies")
            self.tree = ttk.Treeview(
                self.frame, columns=columns, show="headings", height=12
            )
            for col, label, width in (
                ("title", "Document", 340),
                ("class", "Class", 130),
                ("status", "Status", 190),
                ("copies", "Copies", 60),
            ):
                self.tree.heading(col, text=label)
                self.tree.column(col, width=width)
            self.tree.grid(row=2, column=0, columnspan=4, sticky="nsew")

            buttons = ttk.Frame(self.frame)
            buttons.grid(row=3, column=0, columnspan=4, sticky="w", pady=8)
            ttk.Button(buttons, text="Print all", command=self._print_all).grid(row=0, column=0, padx=2)
            ttk.Button(buttons, text="Resume", command=self._resume).grid(row=0, column=1, padx=2)
            ttk.Button(buttons, text="Print selected", command=self._print_selected).grid(row=0, column=2, padx=2)
            ttk.Button(buttons, text="Pull secure stock (selected)", command=self._pull_selected).grid(row=0, column=3, padx=2)
            ttk.Button(buttons, text="Void selected...", command=self._void_selected).grid(row=0, column=4, padx=2)
            ttk.Button(buttons, text="Reprint selected", command=self._reprint_selected).grid(row=0, column=5, padx=2)
            ttk.Button(buttons, text="Packaging checks...", command=self._open_packaging).grid(row=1, column=0, columnspan=2, sticky='w', pady=6)
            ttk.Label(self.frame, textvariable=self.status_var).grid(
                row=4, column=0, columnspan=4, sticky="w"
            )

        def refresh(self) -> None:
            for row_id in self.tree.get_children():
                self.tree.delete(row_id)
            for document in self.session.documents:
                item = self.session.items[document.doc_id]
                copies = ""
                if document.kind != KIND_SECURE_STOCK:
                    copies = str(
                        self.print_config.total_copies_for(
                            document.doc_id, document.doc_class
                        )
                    )
                status = STATUS_LABELS.get(item.status, item.status)
                if item.error and item.status == "failed":
                    status = f"FAILED - {item.error}"
                self.tree.insert(
                    "",
                    "end",
                    iid=document.doc_id,
                    values=(document.title, document.doc_class, status, copies),
                )
            if self.session.is_complete:
                self.status_var.set("Pack complete: printed, secure originals pulled, and packaging signed off. DMS reconciliation remains separate.")
            else:
                done = sum(1 for i in self.session.items.values() if i.is_done)
                self.status_var.set(
                    f"{done} of {len(self.session.items)} print/pull items done; "
                    f"{len(self.session.packaging_checks)} of {len(self.session.packaging_rows)} packaging checks done."
                )

        def _open_packaging(self):
            previous_grab = self.root.grab_current()
            win = tk.Toplevel(self.root)
            win.transient(self.root)
            win.grab_set()
            win.title(f'Paper packaging — version {self.session.packaging_revision}')
            win.geometry('880x700')
            ttk.Label(win, text='Inspect and sign the actual paper before checking it off. Separately supplied paper forms are allowed with a named record.',
                      wraplength=820, padding=10).pack(fill='x')
            canvas = tk.Canvas(win, highlightthickness=0)
            scrollbar = ttk.Scrollbar(win, orient='vertical', command=canvas.yview)
            canvas.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side='right', fill='y')
            canvas.pack(fill='both', expand=True)
            content = ttk.Frame(canvas, padding=10)
            holder = canvas.create_window((0, 0), window=content, anchor='nw')
            content.bind('<Configure>', lambda _e: canvas.configure(scrollregion=canvas.bbox('all')))
            canvas.bind('<Configure>', lambda e: canvas.itemconfigure(holder, width=e.width))
            for row in self.session.packaging_rows:
                checked = self.session.packaging_checks.get(row.item_id)
                variable = tk.BooleanVar(value=bool(checked))
                label = row.section + '\n' + row.label
                if checked:
                    label += '\nChecked by ' + checked['actor'] + ' at ' + checked['timestamp']
                    if checked.get('reference'):
                        label += ' · ' + checked['reference']
                button = ttk.Checkbutton(content, text=label, variable=variable,
                    command=lambda r=row, v=variable: self._check_packaging(r, v))
                button.pack(anchor='w', fill='x', pady=6)
                if checked:
                    button.configure(state='disabled')
            ttk.Label(content, text='Post-recovery: enter the DMS exactly as signed, compare every figure, resolve variances, scan/file, then mark reconciled on the start screen.',
                      wraplength=780).pack(anchor='w', pady=14)
            ttk.Button(content, text='Close', command=win.destroy).pack(anchor='e')
            self.root.wait_window(win)
            if previous_grab is not None and previous_grab.winfo_exists():
                previous_grab.grab_set()

        def _check_packaging(self, row, variable):
            if not variable.get():
                return
            reference = ''
            if row.requires_paper:
                previous_grab = self.root.grab_current()
                prompt = ('Enter the secure original form number.' if row.secure_reference else
                          'Name the separately supplied paper form / inspection evidence. For second review, name the reviewer.')
                reference = simpledialog.askstring('Record named evidence', prompt, parent=self.root)
                if previous_grab is not None and previous_grab.winfo_exists():
                    previous_grab.grab_set()
                if reference is None:
                    variable.set(False)
                    return
            try:
                self.session.check_packaging_item(row.item_id, reference=reference)
            except (PrintPipelineError, ValueError) as exc:
                variable.set(False)
                messagebox.showerror('Cannot check off', str(exc), parent=self.root)
            self.refresh()

        def _selected_doc_id(self) -> Optional[str]:
            selection = self.tree.selection()
            return selection[0] if selection else None

        def _ask_stock_confirmation(self, document) -> bool:
            # Retained for a future special-stock document class; the
            # standard pack never triggers it since Stage 5.
            assignment = self.session.assignment_for(document)
            where = (
                f"tray {assignment.tray!r} on {assignment.printer_name!r}"
                if assignment
                else "the assigned tray"
            )
            return bool(
                messagebox.askyesno(
                    "Check the paper",
                    f"Before printing '{document.title}':\n\n"
                    f"Is the right paper loaded in {where}?",
                )
            )

        def _print_all(self) -> None:
            try:
                self.session.print_all()
            except PrintPipelineError as exc:
                messagebox.showerror('Cannot print', str(exc))
            self.refresh()

        def _resume(self) -> None:
            try:
                self.session.resume()
            except PrintPipelineError as exc:
                messagebox.showerror('Cannot resume', str(exc))
            self.refresh()

        def _print_selected(self) -> None:
            doc_id = self._selected_doc_id()
            if not doc_id:
                return
            try:
                self.session.print_item(doc_id)
            except PrintPipelineError as exc:
                messagebox.showerror("Cannot print", str(exc))
            self.refresh()

        def _pull_selected(self) -> None:
            doc_id = self._selected_doc_id()
            if not doc_id:
                return
            try:
                self.session.pull_secure_item(doc_id)
            except PrintPipelineError as exc:
                messagebox.showerror("Cannot check off", str(exc))
            self.refresh()

        def _void_selected(self) -> None:
            doc_id = self._selected_doc_id()
            if not doc_id:
                return
            reason = simpledialog.askstring(
                "Void printed document",
                "Why is this copy being voided (jam, wrong stock, spoiled)?\n"
                "The reason goes in the audit log.",
                parent=self.root,
            )
            if reason is None:
                return
            try:
                self.session.void_item(doc_id, reason=reason)
            except PrintPipelineError as exc:
                messagebox.showerror("Cannot void", str(exc))
            self.refresh()

        def _reprint_selected(self) -> None:
            doc_id = self._selected_doc_id()
            if not doc_id:
                return
            try:
                self.session.reprint_item(doc_id)
            except PrintPipelineError as exc:
                messagebox.showerror("Cannot reprint", str(exc))
            self.refresh()

        # -- calibration ------------------------------------------------------

        def _build_calibration(self) -> None:
            frame = ttk.LabelFrame(self.frame, text="Alignment calibration", padding=8)
            frame.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(10, 0))
            printers = sorted(
                {a.printer_name for a in self.session.tray_mapping.values()}
            ) or self.provider.list_printers()
            doc_ids = [
                d.doc_id for d in self.session.documents if d.kind != KIND_SECURE_STOCK
            ]
            self.cal_printer = tk.StringVar(value=printers[0] if printers else "")
            self.cal_doc = tk.StringVar(value=doc_ids[0] if doc_ids else "")
            self.cal_offsets_var = tk.StringVar(value="")
            ttk.Label(frame, text="Printer:").grid(row=0, column=0, sticky="w")
            printer_box = ttk.Combobox(
                frame, textvariable=self.cal_printer, values=printers, state="readonly", width=36
            )
            printer_box.grid(row=0, column=1, sticky="w", padx=4)
            printer_box.bind("<<ComboboxSelected>>", lambda _e: self._show_offsets())
            ttk.Label(frame, text="Document:").grid(row=1, column=0, sticky="w")
            doc_box = ttk.Combobox(
                frame, textvariable=self.cal_doc, values=doc_ids, state="readonly", width=36
            )
            doc_box.grid(row=1, column=1, sticky="w", padx=4)
            doc_box.bind("<<ComboboxSelected>>", lambda _e: self._show_offsets())
            ttk.Label(frame, textvariable=self.cal_offsets_var).grid(
                row=2, column=0, columnspan=2, sticky="w", pady=4
            )
            nudges = ttk.Frame(frame)
            nudges.grid(row=3, column=0, columnspan=2, sticky="w")
            ttk.Button(nudges, text="Left", command=lambda: self._nudge(-1, 0)).grid(row=0, column=0, padx=2)
            ttk.Button(nudges, text="Right", command=lambda: self._nudge(1, 0)).grid(row=0, column=1, padx=2)
            ttk.Button(nudges, text="Up", command=lambda: self._nudge(0, 1)).grid(row=0, column=2, padx=2)
            ttk.Button(nudges, text="Down", command=lambda: self._nudge(0, -1)).grid(row=0, column=3, padx=2)
            ttk.Button(
                nudges, text="Print test page", command=self._print_test_page
            ).grid(row=0, column=4, padx=(10, 2))
            self._show_offsets()

        def _show_offsets(self) -> None:
            offsets = self.print_config.get_offsets(
                self.cal_printer.get(), self.cal_doc.get()
            )
            self.cal_offsets_var.set(
                f"Current offsets: x {offsets['x_mm']:+.1f} mm, "
                f"y {offsets['y_mm']:+.1f} mm (steps of 0.5 mm; saved on every nudge)"
            )

        def _nudge(self, dx: int, dy: int) -> None:
            self.print_config.nudge(
                self.cal_printer.get(), self.cal_doc.get(),
                dx_steps=dx, dy_steps=dy,
            )
            self._show_offsets()

        def _print_test_page(self) -> None:
            printer = self.cal_printer.get()
            doc_id = self.cal_doc.get()
            offsets = self.print_config.get_offsets(printer, doc_id)
            payload = render_test_page(
                printer_name=printer, doc_id=doc_id, offsets_mm=offsets
            )
            result = self.provider.print_document(
                printer, payload, doc_id="calibration_test_page",
                title="Calibration test page", offsets_mm=offsets,
            )
            if result.ok:
                messagebox.showinfo("Test page", "Test page sent. Measure it against the form, then nudge as needed.")
            else:
                messagebox.showerror("Test page failed", result.message)


def run_print_pipeline(
    *, deal_id: Optional[str] = None,
    setup_path: Path = DEFAULT_SETUP_PATH,
    deals_dir: Path = DEFAULT_DEALS_DIR,
    users_path: Path = DEFAULT_USERS_PATH,
    audit_path: Path = DEFAULT_AUDIT_PATH,
    print_config_path: Path = DEFAULT_PRINT_CONFIG_PATH,
) -> int:
    """Enter shared sign-in; prints are attributed to that user."""
    from .app_ui import run_app
    return run_app(initial_action='print', deal_id=deal_id, setup_path=setup_path,
                   deals_dir=deals_dir, users_path=users_path, audit_path=audit_path,
                   print_config_path=print_config_path)


if __name__ == "__main__":
    run_print_pipeline()
