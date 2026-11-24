"""Thin Tkinter adapter for ordered JPEG intake, numeric mapping and SAMPLE export."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Optional

try:  # Tkinter ships with Windows Python; absent on some headless boxes.
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    TK_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only without Tk
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    TK_AVAILABLE = False

from .doc_library import (
    CANONICAL_FIELDS,
    CONDITION_LEASE,
    CONDITION_RETAIL_NEW,
    CONDITION_RETAIL_USED,
    DEFAULT_LIBRARY_DIR,
    DocumentLibrary,
    DocumentLibraryError,
    FieldPlacement,
    document_fingerprint,
    field_choice_label,
    field_from_choice_label,
)
from .pdf_overlay import PdfOverlayError
from .document_setup import DocumentSetupActions

CONDITION_LABELS = {
    CONDITION_RETAIL_NEW: "Retail - new vehicle",
    CONDITION_RETAIL_USED: "Retail - used vehicle",
    CONDITION_LEASE: "Lease",
}


if TK_AVAILABLE:

    class DocumentsWindow:
        """One window over a DocumentLibrary."""

        def __init__(
            self,
            root: "tk.Tk",
            library: DocumentLibrary,
            current_user=None,
        ) -> None:
            self.root = root
            self.library = library
            self.actions = DocumentSetupActions(library)
            self.current_user = current_user
            self.vars: dict[str, "tk.StringVar"] = {}
            self.condition_vars = {
                condition: tk.BooleanVar(value=condition != CONDITION_LEASE)
                for condition in CONDITION_LABELS
            }
            self.selected_id: Optional[str] = None
            self.reviewed_fingerprint: Optional[str] = None
            self.reviewed_document = None

            root.title("Dealer Revenue Rescue - Document Library")
            root.geometry("760x640")

            canvas = tk.Canvas(root, highlightthickness=0)
            scrollbar = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
            scrollbar.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            canvas.configure(yscrollcommand=scrollbar.set)
            body = tk.Frame(canvas)
            window = canvas.create_window((0, 0), window=body, anchor="nw")
            body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
            canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units"))
            tk.Label(
                body, text="Your documents", font=("Segoe UI", 13, "bold"),
                anchor="w",
            ).pack(fill="x", padx=16, pady=(14, 4))
            tk.Label(
                body,
                text="The documents your store added. Only APPROVED "
                "documents print in a deal pack.",
                anchor="w", justify="left", wraplength=720,
            ).pack(fill="x", padx=16)
            self.doc_list = tk.Listbox(body, height=6)
            self.doc_list.pack(fill="x", padx=16, pady=6)
            self.doc_list.bind("<<ListboxSelect>>", lambda _e: self._on_select())

            # -- add a document --------------------------------------
            add_box = tk.LabelFrame(body, text="Add a document")
            add_box.pack(fill="x", padx=16, pady=6)
            tk.Label(add_box, text="JPEG pages at 300 DPI (2550 x 3300); displayed order becomes page order.", anchor="w", wraplength=680).pack(fill="x", padx=8)
            self.page_list = tk.Listbox(add_box, height=4, exportselection=False)
            self.page_list.pack(fill="x", padx=8, pady=4)
            page_buttons = tk.Frame(add_box)
            page_buttons.pack(fill="x", padx=8)
            for label, action in (("Move up", lambda: self._move_page(-1)), ("Move down", lambda: self._move_page(1)), ("Remove", self._remove_page), ("Replace selected document's page images", self._replace_images)):
                tk.Button(page_buttons, text=label, command=action).pack(side="left", padx=2)
            self.source_info = tk.Label(add_box, text="", anchor="w", wraplength=680)
            self.source_info.pack(fill="x", padx=8)
            tk.Button(
                add_box, text="Add JPEG pages...", command=self._browse_source
            ).pack(anchor="e", padx=8, pady=2)
            self._field(add_box, "Document id (e.g. we-owe-form)", "doc_id")
            self._field(add_box, "Title", "title")
            cond_row = tk.Frame(add_box)
            cond_row.pack(fill="x", padx=8, pady=4)
            tk.Label(cond_row, text="Prints when:").pack(side="left")
            for condition, label in CONDITION_LABELS.items():
                tk.Checkbutton(
                    cond_row, text=label, variable=self.condition_vars[condition]
                ).pack(side="left", padx=6)
            tk.Button(
                add_box, text="Add document", command=self._add_document
            ).pack(anchor="e", padx=8, pady=6)
            demo_row = tk.Frame(add_box)
            demo_row.pack(fill="x", padx=8, pady=(0, 6))
            tk.Label(
                demo_row, anchor="w", justify="left", wraplength=480,
                text="No form ready yet? Add the demo form: the blank Florida "
                "title application (HSMV 82040), already mapped. Use it for "
                "the first test print.",
            ).pack(side="left")
            tk.Button(
                demo_row, text="Add the demo title form", command=self._add_demo_form
            ).pack(side="right")

            # -- placements ------------------------------------------
            place_box = tk.LabelFrame(
                body, text="Field placements (for the selected document)"
            )
            place_box.pack(fill="x", padx=16, pady=6)
            self.placement_list = tk.Listbox(place_box, height=5)
            self.placement_list.pack(fill="x", padx=8, pady=4)
            row = tk.Frame(place_box)
            row.pack(fill="x", padx=8, pady=2)
            tk.Label(row, text="Field").pack(side="left")
            self.field_combo = ttk.Combobox(
                row, values=[field_choice_label(name) for name in CANONICAL_FIELDS],
                state="readonly", width=44, height=24,
            )
            self.field_combo.pack(side="left", padx=4)
            for label, name, width in (
                ("Page", "pl_page", 4), ("X mm", "pl_x", 6),
                ("Y mm", "pl_y", 6), ("Font", "pl_font", 5),
            ):
                tk.Label(row, text=label).pack(side="left")
                if name == "pl_page":
                    self.page_choice = ttk.Combobox(row, textvariable=self.var(name, "1"), width=width)
                    self.page_choice.pack(side="left", padx=4)
                else:
                    tk.Entry(row, textvariable=self.var(name), width=width).pack(side="left", padx=4)
            btn_row = tk.Frame(place_box)
            btn_row.pack(fill="x", padx=8, pady=4)
            tk.Button(
                btn_row, text="Place fields on the form...",
                command=self._open_placement_window,
            ).pack(side="left", padx=(0, 6))
            tk.Button(
                btn_row, text="Add placement", command=self._add_placement
            ).pack(side="left")
            tk.Button(
                btn_row, text="Remove selected placement",
                command=self._remove_placement,
            ).pack(side="left", padx=6)
            tk.Button(
                btn_row, text="Save placements", command=self._save_placements
            ).pack(side="left")

            # -- approve + test render --------------------------------
            action_row = tk.Frame(body)
            action_row.pack(fill="x", padx=16, pady=8)
            role = getattr(current_user, "role", None)
            self.approve_btn = tk.Button(
                action_row, text="Approve selected document",
                command=self._approve,
                state="normal" if role in ("manager", "admin") else "disabled",
            )
            self.approve_btn.pack(side="left")
            tk.Button(action_row, text="Reload selected form", command=self._reload_selected).pack(side="left", padx=8)
            tk.Button(
                action_row, text="Save SAMPLE PDF...",
                command=self._test_render,
            ).pack(side="left", padx=8)
            self.status_label = tk.Label(
                body, text="", anchor="w", justify="left", wraplength=720,
                fg="#205020",
            )
            self.status_label.pack(fill="x", padx=16)
            self._refresh_documents()

        # -- helpers ---------------------------------------------------

        def var(self, name: str, initial: str = "") -> "tk.StringVar":
            if name not in self.vars:
                self.vars[name] = tk.StringVar(value=initial)
            return self.vars[name]

        def _field(self, parent, label: str, name: str) -> None:
            row = tk.Frame(parent)
            row.pack(fill="x", pady=2, padx=8)
            tk.Label(row, text=label, width=26, anchor="w").pack(side="left")
            tk.Entry(row, textvariable=self.var(name)).pack(
                side="left", fill="x", expand=True
            )

        def _say(self, text: str, error: bool = False) -> None:
            self.status_label.config(text=text, fg="#a00000" if error else "#205020")

        def _refresh_documents(self) -> None:
            self.doc_list.delete(0, "end")
            for document in self.library.list_documents():
                self.doc_list.insert(
                    "end",
                    f"{document.doc_id} — {document.title} "
                    f"[{document.status}] prints: "
                    f"{', '.join(document.when_it_prints)}",
                )

        def _on_select(self) -> None:
            selection = self.doc_list.curselection()
            if not selection:
                return
            self.selected_id = self.library.list_documents()[selection[0]].doc_id
            self._reload_selected()

        def _reload_selected(self):
            if not self.selected_id:
                self._say("Select a document first.", error=True)
                return
            try:
                document = self.library.get_document(self.selected_id, refresh=True)
            except DocumentLibraryError as exc:
                self._say(str(exc), error=True)
                return
            self._show_document(document)

        def _show_document(self, document):
            document = copy.deepcopy(document)
            if document.form_source == "page_images":
                self.page_choice.configure(state="readonly", values=[str(p.page) for p in document.pages])
                if self.var("pl_page").get() not in [str(p.page) for p in document.pages]:
                    self.var("pl_page").set("1")
                self.source_info.configure(text="Saved pages: " + "; ".join(f"{p.page}: {p.source_filename}" for p in document.pages))
            else:
                self.page_choice.configure(state="normal", values=())
                self.source_info.configure(text="Values only - no form background. Supply exported JPEG pages to upgrade.")
            self.placement_list.delete(0, "end")
            for placement in document.fields:
                self.placement_list.insert(
                    "end",
                    f"{field_choice_label(placement.field_name)} — page {placement.page}, "
                    f"x {placement.x_mm} mm, y {placement.y_mm} mm, "
                    f"font {placement.font_size}",
                )
            # Only an actual redraw advances the displayed review identity.
            self.reviewed_fingerprint = document_fingerprint(document)
            self.reviewed_document = document

        # -- actions -----------------------------------------------------

        def _refresh_pages(self, selected=None):
            self.page_list.delete(0, "end")
            for number, path in enumerate(self.actions.page_paths, 1):
                self.page_list.insert("end", f"Page {number}: {path.name}")
            if selected is not None:
                self.page_list.selection_set(selected)

        def _browse_source(self) -> None:
            chosen = filedialog.askopenfilenames(title="Choose exported JPEG pages", filetypes=[("JPEG pages", "*.jpg *.jpeg *.JPG *.JPEG")])
            if chosen:
                self.actions.set_page_paths([*self.actions.page_paths, *chosen])
                self._refresh_pages()

        def _move_page(self, delta):
            selection = self.page_list.curselection()
            if selection:
                self._refresh_pages(self.actions.move_page(selection[0], delta))

        def _remove_page(self):
            selection = self.page_list.curselection()
            if selection:
                self.actions.remove_page(selection[0])
                self._refresh_pages()

        def _replace_images(self):
            document = self._selected_document()
            if document is None:
                return
            try:
                updated = self.actions.replace_page_images(document.doc_id,
                    expected_fingerprint=self.reviewed_fingerprint)
            except DocumentLibraryError as exc:
                self._say(str(exc), error=True)
                return
            self._show_document(updated)
            self._refresh_documents()
            self._say("Page images replaced. Placement page numbers stayed unchanged; review mapping and a new SAMPLE, then obtain approval again.")

        def _add_document(self) -> None:
            conditions = tuple(
                c for c, var in self.condition_vars.items() if var.get()
            )
            try:
                document = self.actions.add_document(
                    doc_id=self.var("doc_id").get().strip(),
                    title=self.var("title").get().strip(),
                    when_it_prints=conditions,
                    created_by=getattr(self.current_user, "username", "") or "",
                )
            except DocumentLibraryError as exc:
                self._say(str(exc), error=True)
                return
            self._say(
                f"Added {document.title!r} as a draft. Place its fields, "
                "then a manager or admin approves it."
            )
            self._refresh_documents()

        def _add_demo_form(self) -> None:
            from .demo_form import add_demo_form

            try:
                document = add_demo_form(
                    self.library,
                    created_by=getattr(self.current_user, "username", "") or "",
                )
            except DocumentLibraryError as exc:
                self._say(str(exc), error=True)
                return
            self._say(
                f"Added {document.title!r} as a draft with its fields placed. "
                "Select it, save a SAMPLE PDF or print it, move any value that "
                "sits wrong, then a manager or admin approves it."
            )
            self._refresh_documents()

        def _selected_document(self):
            if not self.selected_id:
                self._say("Select a document first.", error=True)
                return None
            if not self.reviewed_fingerprint:
                self._say("Reload the selected form before changing or approving it.", error=True)
                return None
            # Derive edits from the rows actually displayed, not a cache that
            # a background read may have replaced with a shorter field list.
            return copy.deepcopy(self.reviewed_document)

        def _add_placement(self) -> None:
            document = self._selected_document()
            if document is None:
                return
            try:
                placement = FieldPlacement(
                    field_name=field_from_choice_label(self.field_combo.get()),
                    page=int(self.var("pl_page").get() or "1"),
                    x_mm=float(self.var("pl_x").get() or "0"),
                    y_mm=float(self.var("pl_y").get() or "0"),
                    font_size=float(self.var("pl_font").get() or "10"),
                )
            except ValueError:
                self._say(
                    "Page, X, Y and font size must be numbers.", error=True
                )
                return
            try:
                updated = self.library.update_placements(
                    document.doc_id, [*document.fields, placement],
                    expected_fingerprint=self.reviewed_fingerprint
                )
            except DocumentLibraryError as exc:
                self._say(str(exc), error=True)
                return
            self._show_document(updated)
            self._refresh_documents()
            self._say("Placement added and saved.")

        def _open_placement_window(self) -> None:
            """Decision 9: click the spots on the form image."""
            document = self._selected_document()
            if document is None:
                return
            if document.form_source != "page_images":
                self._say("This document has no form pages to click on. "
                          "Replace its page images, or type millimetres below.", error=True)
                return
            try:
                images = self.library.load_page_images(document)
            except DocumentLibraryError as exc:
                self._say(str(exc), error=True)
                return
            doc_id = document.doc_id

            def save(fields):
                # Same call, checks and fingerprint as the typed rows.
                updated = self.library.update_placements(
                    doc_id, fields, expected_fingerprint=self.reviewed_fingerprint)
                self._show_document(updated)
                self._refresh_documents()
                return list(updated.fields)

            from .placement_editor import PlacementWindow  # Tk-only screen

            PlacementWindow(self.root, title=document.title, page_images=images,
                            fields=document.fields, save=save)

        def _remove_placement(self) -> None:
            document = self._selected_document()
            if document is None:
                return
            selection = self.placement_list.curselection()
            if not selection:
                self._say("Select a placement to remove.", error=True)
                return
            fields = list(document.fields)
            del fields[selection[0]]
            try:
                updated = self.library.update_placements(document.doc_id, fields,
                    expected_fingerprint=self.reviewed_fingerprint)
            except DocumentLibraryError as exc:
                self._say(str(exc), error=True)
                return
            self._show_document(updated)
            self._refresh_documents()

        def _save_placements(self) -> None:
            # Placements are saved on every add/remove (update_placements
            # persists); this button simply re-affirms and re-validates.
            document = self._selected_document()
            if document is None:
                return
            try:
                document = self.library.update_placements(document.doc_id, list(document.fields),
                    expected_fingerprint=self.reviewed_fingerprint)
            except DocumentLibraryError as exc:
                self._say(str(exc), error=True)
                return
            self._show_document(document)
            self._say(
                "Placements saved. "
                + ("This document is approved." if document.is_approved
                   else "A manager or admin must approve before it prints.")
            )
            self._refresh_documents()

        def _approve(self) -> None:
            document = self._selected_document()
            if document is None:
                return
            try:
                approved = self.library.approve_document(
                    document.doc_id, self.current_user,
                    expected_fingerprint=self.reviewed_fingerprint
                )
            except DocumentLibraryError as exc:
                self._say(str(exc), error=True)
                return
            self._show_document(approved)
            self._say(
                f"{approved.title!r} approved by {approved.approved_by} — "
                "it will print in matching deal packs."
            )
            self._refresh_documents()

        def _test_render(self) -> None:
            document = self._selected_document()
            if document is None:
                return
            chosen = filedialog.asksaveasfilename(
                title="Save the composed SAMPLE PDF",
                defaultextension=".pdf",
                filetypes=[("PDF files", "*.pdf")],
                initialfile=f"{document.doc_id}-sample.pdf",
            )
            if not chosen:
                return
            try:
                path = self.actions.write_sample(document.doc_id, chosen)
            except (DocumentLibraryError, PdfOverlayError, OSError) as exc:
                self._say(f"SAMPLE could not be saved: {exc}", error=True)
                return
            self._say(f"SAMPLE written to {path}. Print once on blank letter paper at Actual size/100%; inspect every page, smallest type and mapped values.")



def run_documents(
    library_dir: Path = DEFAULT_LIBRARY_DIR,
    current_user=None,
) -> int:
    """Enter the shared gate; a supplied User cannot bypass sign-in."""
    from .app_ui import run_app
    return run_app(initial_action='documents', library_dir=library_dir)


if __name__ == "__main__":
    raise SystemExit(run_documents())
