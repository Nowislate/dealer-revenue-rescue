"""The deal screens' "Residence county" field with the ZIP lookup
(decision 32). Shared by the retail and lease screens.

On a Florida store (the built-in rule sheet), typing the buyer's ZIP:
* fills a blank county when the ZIP sits in one county;
* offers the counties in the drop-down when the ZIP crosses a county
  line, and says so, so the clerk picks the county where the buyer
  registers the vehicle;
* flags a typed county the ZIP does not touch.
The clerk can always type any county: the lookup only helps. The tax
still comes from the store's approved rate for the county on the deal.
"""

from __future__ import annotations

from . import fl_tables


def uses_florida_lookup(preset) -> bool:
    rules = getattr(preset, "rules", None)
    return bool(rules is not None and rules.built_in and rules.state == "FL")


def build(tk, ttk, parent, *, label: str, label_width: int,
          zip_var, county_var, enabled: bool):
    """Pack the county row and its hint line. Returns the update
    function (called on every ZIP or county change)."""
    row = tk.Frame(parent)
    row.pack(fill="x", pady=2)
    tk.Label(row, text=label, width=label_width, anchor="w").pack(side="left")
    combo = ttk.Combobox(row, textvariable=county_var)
    combo.pack(side="left", fill="x", expand=True)
    hint = tk.Label(parent, text="", justify="left", anchor="w",
                    wraplength=600, fg="#8a4b00")
    hint.pack(fill="x")

    traces = []

    def update(*_args) -> None:
        if not enabled:
            return
        try:
            alive = bool(combo.winfo_exists())
        except tk.TclError:
            alive = False
        if not alive:  # the step was left: stop listening
            for var, trace_id in traces:
                try:
                    var.trace_remove("write", trace_id)
                except tk.TclError:
                    pass
            traces.clear()
            return
        zip_text = zip_var.get()
        combo.configure(values=fl_tables.counties_for_zip(zip_text))
        if not county_var.get().strip():
            filled = fl_tables.fill_county(zip_text, "")
            if filled:
                county_var.set(filled)  # re-enters once; then not blank
        hint.configure(text=fl_tables.county_hint(zip_text, county_var.get()))

    if enabled:
        traces.append((zip_var, zip_var.trace_add("write", update)))
        traces.append((county_var, county_var.trace_add("write", update)))
        update()
    return update
