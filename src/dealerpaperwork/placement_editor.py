"""Click-to-place field mapping on the form image (Decision 9).

"Upload, name, tick when it prints, click the spots, pick the fields,
save into the pack." The dealer sees the form page, picks a field and
clicks where the value's top-left corner belongs. Clicking a placed
value selects it; drag it, or nudge it with the arrow keys, to move it;
Delete removes it. Every change is saved at once through the same
library call the typed-millimetre rows use, so validation, the review
fingerprint and the approval reset behave exactly as before.

``PlacementEditor`` holds the logic and needs no screen, so it is
tested headless. ``PlacementWindow`` is the thin Tkinter layer.
"""
from __future__ import annotations

import copy
from typing import Callable, Optional

from .doc_library import (
    CANONICAL_FIELDS,
    FieldPlacement,
    field_choice_label,
    field_from_choice_label,
    sample_field_values,
)
from .form_preview import (
    PT_PER_MM,
    PreviewError,
    decode_preview,
    display_to_mm,
    font_height_display,
    mm_to_display,
    MAX_X_MM,
    MAX_Y_MM,
)

try:  # Tkinter ships with Windows Python; absent on some headless boxes.
    import tkinter as tk
    from tkinter import ttk

    TK_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only without Tk
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    TK_AVAILABLE = False

# Average Helvetica character width as a share of the point size; only
# used to size the clickable box around a placed value.
CHAR_WIDTH_EM = 0.55
NUDGE_MM = 0.5
FINE_NUDGE_MM = 0.1
DEFAULT_FONT_SIZE = 10.0


class PlacementEditor:
    """Headless placement editing on one document.

    ``save`` receives the complete new field list and returns the saved
    list (or raises). The editor only adopts a change after ``save``
    succeeds, so a refused change leaves the screen and the record as
    they were.
    """

    def __init__(self, fields, *, page_count: int,
                 save: Callable[[list], list],
                 sample_values: Optional[dict] = None) -> None:
        if page_count < 1:
            raise ValueError("The document has no form pages to place fields on.")
        self.fields: list[FieldPlacement] = [copy.copy(f) for f in fields]
        self.page_count = page_count
        self.page = 1
        self.selected: Optional[int] = None
        # While a value is dragged: its unsaved (x_mm, y_mm), drawn
        # but not written until the drag ends.
        self.dragging: Optional[tuple[float, float]] = None
        self._save = save
        self.samples = sample_values if sample_values is not None else sample_field_values()

    # -- reading -----------------------------------------------------------

    def on_page(self) -> list[tuple[int, FieldPlacement]]:
        """This page's values as drawn (a dragged value at its drag spot)."""
        shown = []
        for i, f in enumerate(self.fields):
            if int(f.page) != self.page:
                continue
            if i == self.selected and self.dragging is not None:
                f = copy.copy(f)
                f.x_mm, f.y_mm = self.dragging
            shown.append((i, f))
        return shown

    def sample_text(self, placement: FieldPlacement) -> str:
        return self.samples.get(placement.field_name) or placement.field_name

    def box(self, placement: FieldPlacement, dw: float, dh: float) -> tuple[float, float, float, float]:
        """The value's on-screen box: (left, top, right, bottom)."""
        x, y = mm_to_display(placement.x_mm, placement.y_mm, dw, dh)
        height = font_height_display(placement.font_size, dh)
        width_mm = len(self.sample_text(placement)) * placement.font_size * CHAR_WIDTH_EM / PT_PER_MM
        width = mm_to_display(width_mm, 0, dw, dh)[0]
        return x, y, x + max(width, height), y + height

    def hit(self, x: float, y: float, dw: float, dh: float) -> Optional[int]:
        """Index of the value under (x, y) on this page; the most
        recently placed wins where two overlap."""
        for index, placement in reversed(self.on_page()):
            left, top, right, bottom = self.box(placement, dw, dh)
            if left - 2 <= x <= right + 2 and top - 2 <= y <= bottom + 2:
                return index
        return None

    def describe(self, index: Optional[int] = None) -> str:
        index = self.selected if index is None else index
        if index is None:
            return "Nothing selected."
        f = self.fields[index]
        return (f"{field_choice_label(f.field_name)} — page {f.page}, "
                f"x {f.x_mm} mm, y {f.y_mm} mm, font {f.font_size}")

    # -- changing ------------------------------------------------------------

    def drag_to(self, x: float, y: float, dw: float, dh: float) -> None:
        """Show the selected value at (x, y) without saving."""
        if self.selected is not None:
            self.dragging = display_to_mm(x, y, dw, dh)

    def end_drag(self) -> None:
        """Save the dragged value where it was dropped (or nothing)."""
        spot, self.dragging = self.dragging, None
        if spot is not None and self.selected is not None:
            self._replace_selected(x_mm=spot[0], y_mm=spot[1])

    def set_page(self, page: int) -> None:
        if not 1 <= page <= self.page_count:
            raise ValueError(f"Pick a page from 1 to {self.page_count}.")
        self.page = page
        self.selected = None

    def _commit(self, fields: list) -> None:
        saved = self._save(fields)
        self.fields = [copy.copy(f) for f in (saved if saved is not None else fields)]

    def place(self, field_name: str, x: float, y: float, dw: float, dh: float,
              font_size: float = DEFAULT_FONT_SIZE) -> int:
        """Add ``field_name`` with its top-left at the click; returns its index."""
        if field_name not in CANONICAL_FIELDS:
            raise ValueError("Pick a field from the list first.")
        x_mm, y_mm = display_to_mm(x, y, dw, dh)
        new = FieldPlacement(field_name, self.page, x_mm, y_mm, float(font_size))
        self._commit([*self.fields, new])
        self.selected = len(self.fields) - 1
        return self.selected

    def move_selected_to(self, x: float, y: float, dw: float, dh: float) -> None:
        if self.selected is None:
            raise ValueError("Select a placed value first.")
        x_mm, y_mm = display_to_mm(x, y, dw, dh)
        self._replace_selected(x_mm=x_mm, y_mm=y_mm)

    def nudge(self, dx_mm: float, dy_mm: float) -> None:
        if self.selected is None:
            raise ValueError("Select a placed value first.")
        f = self.fields[self.selected]
        x_mm = round(min(max(f.x_mm + dx_mm, 0.0), MAX_X_MM), 1)
        y_mm = round(min(max(f.y_mm + dy_mm, 0.0), MAX_Y_MM), 1)
        self._replace_selected(x_mm=x_mm, y_mm=y_mm)

    def set_selected_font(self, font_size: float) -> None:
        if self.selected is None:
            raise ValueError("Select a placed value first.")
        self._replace_selected(font_size=float(font_size))

    def remove_selected(self) -> None:
        if self.selected is None:
            raise ValueError("Select a placed value first.")
        fields = [f for i, f in enumerate(self.fields) if i != self.selected]
        self._commit(fields)
        self.selected = None

    def _replace_selected(self, **changes) -> None:
        fields = [copy.copy(f) for f in self.fields]
        target = fields[self.selected]
        for name, value in changes.items():
            setattr(target, name, value)
        if fields[self.selected].to_dict() == self.fields[self.selected].to_dict():
            return  # nothing moved: write nothing
        self._commit(fields)


def drag_offset(editor: PlacementEditor, index: int, x: float, y: float,
                dw: float, dh: float) -> tuple[float, float]:
    """Where in the value's box the pointer grabbed it, so a drag keeps
    the value under the pointer instead of jumping its corner there."""
    left, top, _right, _bottom = editor.box(editor.fields[index], dw, dh)
    return x - left, y - top


if TK_AVAILABLE:

    class PlacementWindow:
        """The form page with its placed values; click to place."""

        ZOOM = 2  # preview pixel -> screen pixels (319 x 413 -> 638 x 826)

        def __init__(self, parent, *, title: str, page_images: dict,
                     fields, save: Callable[[list], list],
                     on_close: Optional[Callable[[], None]] = None) -> None:
            self.page_images = page_images
            self.editor = PlacementEditor(fields, page_count=len(page_images), save=save)
            self.on_close = on_close
            self._photos: dict[int, object] = {}
            self._drag: Optional[tuple[float, float]] = None
            self._drag_moved = False

            top = self.top = tk.Toplevel(parent)
            top.title(f"Place fields - {title}")
            top.protocol("WM_DELETE_WINDOW", self.close)

            bar = tk.Frame(top)
            bar.pack(fill="x", padx=8, pady=6)
            tk.Label(bar, text="Page").pack(side="left")
            self.page_var = tk.StringVar(value="1")
            page_box = ttk.Combobox(bar, textvariable=self.page_var, state="readonly", width=4,
                                    values=[str(p) for p in sorted(page_images)])
            page_box.pack(side="left", padx=4)
            page_box.bind("<<ComboboxSelected>>", lambda _e: self._change_page())
            tk.Label(bar, text="Field to place").pack(side="left", padx=(12, 0))
            self.field_var = tk.StringVar()
            ttk.Combobox(bar, textvariable=self.field_var, state="readonly", width=46, height=24,
                         values=[field_choice_label(n) for n in CANONICAL_FIELDS]).pack(side="left", padx=4)
            tk.Label(bar, text="Font").pack(side="left", padx=(12, 0))
            self.font_var = tk.StringVar(value=str(int(DEFAULT_FONT_SIZE)))
            font_entry = tk.Entry(bar, textvariable=self.font_var, width=4)
            font_entry.pack(side="left", padx=4)
            font_entry.bind("<Return>", lambda _e: self._apply_font())

            tk.Label(
                top, anchor="w", justify="left", wraplength=640,
                text="Pick a field, then click where the top-left corner of its value "
                     "goes. Click a placed value to select it; drag it or use the arrow "
                     "keys to move it (Shift = finer); Delete removes it. Every change "
                     "saves at once. Check a SAMPLE print before approval.",
            ).pack(fill="x", padx=8)

            frame = tk.Frame(top)
            frame.pack(fill="both", expand=True, padx=8, pady=6)
            self.canvas = tk.Canvas(frame, width=660, height=640, background="#808080",
                                    highlightthickness=1)
            vbar = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
            self.canvas.configure(yscrollcommand=vbar.set)
            vbar.pack(side="right", fill="y")
            self.canvas.pack(side="left", fill="both", expand=True)

            self.status = tk.Label(top, anchor="w", justify="left", wraplength=640, fg="#205020")
            self.status.pack(fill="x", padx=8, pady=(0, 8))

            self.canvas.bind("<ButtonPress-1>", self._press)
            self.canvas.bind("<B1-Motion>", self._motion)
            self.canvas.bind("<ButtonRelease-1>", self._release)
            for key, (dx, dy) in {"Left": (-1, 0), "Right": (1, 0), "Up": (0, -1), "Down": (0, 1)}.items():
                top.bind(f"<{key}>", lambda _e, dx=dx, dy=dy: self._nudge(dx * NUDGE_MM, dy * NUDGE_MM))
                top.bind(f"<Shift-{key}>", lambda _e, dx=dx, dy=dy: self._nudge(dx * FINE_NUDGE_MM, dy * FINE_NUDGE_MM))
            top.bind("<Delete>", lambda _e: self._remove())
            self._change_page()

        # -- drawing -----------------------------------------------------

        @property
        def size(self) -> tuple[int, int]:
            photo = self._photos.get(self.editor.page)
            if photo is None:
                return 638, 826
            return photo.width(), photo.height()

        def _say(self, text: str, error: bool = False) -> None:
            self.status.config(text=text, fg="#a00000" if error else "#205020")

        def _photo(self, page: int):
            if page not in self._photos:
                preview = decode_preview(self.page_images[page].data)
                photo = tk.PhotoImage(master=self.top, data=preview.to_png_base64())
                self._photos[page] = photo.zoom(self.ZOOM, self.ZOOM)
            return self._photos[page]

        def _change_page(self) -> None:
            try:
                self.editor.set_page(int(self.page_var.get() or "1"))
                self._photo(self.editor.page)
            except (ValueError, PreviewError) as exc:
                self._say(f"This page cannot be shown: {exc} Typed millimetres still work.", error=True)
                return
            self.redraw()
            self._say(f"Page {self.editor.page}: {len(self.editor.on_page())} placed value(s).")

        def redraw(self) -> None:
            canvas = self.canvas
            canvas.delete("all")
            photo = self._photos.get(self.editor.page)
            if photo is None:
                return
            canvas.create_image(0, 0, image=photo, anchor="nw")
            dw, dh = self.size
            for index, placement in self.editor.on_page():
                left, top, right, bottom = self.editor.box(placement, dw, dh)
                chosen = index == self.editor.selected
                colour = "#0050c8" if chosen else "#c00000"
                canvas.create_rectangle(left, top, right, bottom, outline=colour,
                                        width=2 if chosen else 1)
                size = max(5, int(round(bottom - top)))
                canvas.create_text(left, top, anchor="nw", fill=colour,
                                   text=self.editor.sample_text(placement),
                                   font=("Helvetica", -size))
                canvas.create_line(left - 4, top, left + 4, top, fill=colour)
                canvas.create_line(left, top - 4, left, top + 4, fill=colour)
            canvas.configure(scrollregion=(0, 0, dw, dh))

        # -- events --------------------------------------------------------

        def _point(self, event) -> tuple[float, float]:
            return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

        def _press(self, event) -> None:
            self.top.focus_set()
            x, y = self._point(event)
            dw, dh = self.size
            index = self.editor.hit(x, y, dw, dh)
            self._drag = None
            self._drag_moved = False
            if index is not None:
                self.editor.selected = index
                self._drag = drag_offset(self.editor, index, x, y, dw, dh)
                self.redraw()
                self._say("Selected: " + self.editor.describe())
                return
            field = field_from_choice_label(self.field_var.get())
            if not field:
                self.editor.selected = None
                self.redraw()
                self._say("Pick a field to place first, or click a placed value to select it.", error=True)
                return
            try:
                self.editor.place(field, x, y, dw, dh, self._font())
            except Exception as exc:
                self._say(str(exc), error=True)
                return
            self.redraw()
            self._say("Placed and saved: " + self.editor.describe())

        def _motion(self, event) -> None:
            if self._drag is None or self.editor.selected is None:
                return
            x, y = self._point(event)
            dw, dh = self.size
            # Drawn while dragging; one save on release.
            self.editor.drag_to(x - self._drag[0], y - self._drag[1], dw, dh)
            self._drag_moved = True
            self.redraw()

        def _release(self, event) -> None:
            moved, self._drag = self._drag_moved, None
            self._drag_moved = False
            if not moved:
                return
            try:
                self.editor.end_drag()
            except Exception as exc:
                self.editor.dragging = None
                self._say(str(exc), error=True)
            else:
                self._say("Moved and saved: " + self.editor.describe())
            self.redraw()

        def _font(self) -> float:
            try:
                return float(self.font_var.get() or DEFAULT_FONT_SIZE)
            except ValueError:
                raise ValueError("Font size must be a number, e.g. 10.") from None

        def _apply_font(self) -> None:
            if self.editor.selected is None:
                return
            try:
                self.editor.set_selected_font(self._font())
            except Exception as exc:
                self._say(str(exc), error=True)
                return
            self.redraw()
            self._say("Font saved: " + self.editor.describe())

        def _nudge(self, dx: float, dy: float) -> None:
            if self.editor.selected is None:
                return
            try:
                self.editor.nudge(dx, dy)
            except Exception as exc:
                self._say(str(exc), error=True)
                return
            self.redraw()
            self._say("Moved and saved: " + self.editor.describe())

        def _remove(self) -> None:
            if self.editor.selected is None:
                return
            try:
                self.editor.remove_selected()
            except Exception as exc:
                self._say(str(exc), error=True)
                return
            self.redraw()
            self._say("Removed and saved.")

        def close(self) -> None:
            self.top.destroy()
            if self.on_close:
                self.on_close()
