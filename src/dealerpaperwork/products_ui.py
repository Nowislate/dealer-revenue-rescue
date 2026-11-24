"""The "Products sold" section a deal screen shows (retail and lease).

A store's product forms (service contract, GAP, tire and wheel ...) are
the approved library forms that place a product field. The deal screen
lists each one; typing a price marks it sold, and its own form then
prints in the pack with that price, term, coverage and contract number.
UI-independent helpers; the windows draw them with their own ``_field``.
"""

from __future__ import annotations

from typing import Callable, Iterable

PRODUCT_KEYS = (
    ("price", "Price ($) - blank = not sold"),
    ("term", "Term (e.g. 60 mo / 100,000 mi)"),
    ("coverage", "Coverage or plan"),
    ("contract_number", "Contract number"),
)


def product_forms_for(library_dir, conditions: Iterable[str]) -> list[tuple[str, str]]:
    """(doc_id, title) of the approved product forms for these print
    conditions; empty if the library cannot be read."""
    from .doc_library import DocumentLibrary, DocumentLibraryError

    conditions = tuple(conditions)
    try:
        library = DocumentLibrary(library_dir)
    except (DocumentLibraryError, OSError):
        return []
    seen: dict[str, str] = {}
    for condition in conditions:
        for document in library.product_documents(condition):
            seen.setdefault(document.doc_id, document.title)
    return sorted(seen.items(), key=lambda item: item[1].lower())


def product_forms_by_condition(library_dir, conditions: Iterable[str]) -> dict:
    """{condition: [(doc_id, title)]}: a deal screen offers only the
    products whose contract prints for that deal's own condition (a
    used-only contract never on a new-car deal)."""
    return {condition: product_forms_for(library_dir, (condition,)) for condition in conditions}


def offered_products(product_forms, condition: str) -> list:
    """The product forms a deal of this condition offers. ``product_forms``
    is a {condition: forms} map, or a plain list offered to every deal."""
    if isinstance(product_forms, dict):
        return list(product_forms.get(condition, ()))
    return list(product_forms or ())


def var_name(doc_id: str, key: str) -> str:
    return f"product_{doc_id}_{key}"


def draw_products(field: Callable[[str, str, str], None], heading: Callable[[str], None],
                  title: Callable[[str], None], note: Callable[[str], None],
                  product_forms, recorded: dict) -> None:
    """Draw one block per product form with ``field(label, name, initial)``."""
    if not product_forms:
        return
    heading("Products sold (F&I)")
    note("Type a price for each product this deal sold. Its own contract then "
         "prints in the pack. Leave the price blank for a product not sold.")
    for doc_id, name in product_forms:
        title(name)
        line = recorded.get(doc_id) or {}
        for key, label in PRODUCT_KEYS:
            field(label, var_name(doc_id, key), str(line.get(key) or ""))


def collect_products(get: Callable[[str], str], product_forms) -> dict:
    """The products as typed: {doc_id: {key: text}} for the flow to check."""
    return {doc_id: {key: get(var_name(doc_id, key)) for key, _label in PRODUCT_KEYS}
            for doc_id, _title in product_forms}
