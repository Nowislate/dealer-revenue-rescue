"""The built-in text renderer (Stage 4 placeholder).

Stage 4's print pipeline needs *something* to print, and the dealer's
real forms arrive in Stage 5 (add-a-doc / mapped PDFs). Until then each
pack document renders as plain text: the document title, the dealer
identity from the approved preset, the buyer / vehicle / trade facts,
and -- when the worksheet has been computed -- the engine's
line-by-line breakdown and the financing section, exactly as
calculated. Nothing here does math; it formats model data.

Markings, per the spec:

* an uncertified (draft) print carries a DRAFT watermark banner;
* a Stage 5 DRILL (practice) deal carries a SAMPLE banner on every
  document, certified or not -- a drill can be walked and certified
  for practice, but its paper must never be mistaken for a real deal;
* a reprint of a voided original carries a "REPRINT -- original
  voided" banner so a replacement can never be mistaken for a second
  original;
* when calibration offsets are applied, the payload records them
  (the Stage 5 PDF renderer will apply the same offsets to the form
  placement itself).

Plain text lines ending in CRLF. The Windows provider lays them out
on letter pages (page_drawing.pages_from_text) and draws them through
the printer's driver.
"""

from __future__ import annotations

from typing import Optional

CRLF = "\r\n"


def _money(value) -> str:
    return f"${value:,.2f}"


def render_document(
    *,
    document,
    deal,
    preset,
    draft: bool = False,
    reprint: bool = False,
    sample: bool = False,
    offsets_mm: Optional[dict] = None,
) -> str:
    """Render one pack document for one deal as plain text."""
    lines: list[str] = []
    offsets = offsets_mm or {}
    x = float(offsets.get("x_mm", 0.0))
    y = float(offsets.get("y_mm", 0.0))
    if x or y:
        lines.append(f"[Alignment offset applied: x {x:+.1f} mm, y {y:+.1f} mm]")
    if sample:
        lines.append("*** SAMPLE -- DRILL ONLY -- NOT A REAL DEAL ***")
        lines.append("*** SAMPLE ***  Practice paperwork. Do not sign or file.")
    if draft:
        lines.append("*** DRAFT -- NOT CERTIFIED -- DRAFT ***")
        lines.append("*** DRAFT ***  This deal has not been certified.")
    if reprint:
        lines.append("*** REPRINT -- original voided ***")
        lines.append("This copy replaces a voided original. Do not use both.")

    dealer = getattr(preset, "dealer", None)
    dealer_name = getattr(dealer, "legal_name", None) or "Dealer (setup profile)"
    lines.append("")
    lines.append(dealer_name)
    lines.append(document.title)
    lines.append("=" * 60)

    inputs = getattr(deal, "inputs", deal)
    buyer = getattr(inputs, "buyer", None)
    vehicle = getattr(inputs, "vehicle", None)
    trade = getattr(inputs, "trade", None)
    if buyer is not None:
        lines.append(f"Buyer: {buyer.full_name}")
        if getattr(buyer, "co_buyer_name", None):
            lines.append(f"Co-buyer: {buyer.co_buyer_name}")
        if getattr(buyer, "residence_county", ""):
            lines.append(f"Residence county: {buyer.residence_county}")
    if vehicle is not None:
        year = vehicle.year if vehicle.year is not None else ""
        lines.append(
            f"Vehicle: {year} {vehicle.make} {vehicle.model}".rstrip()
        )
        if vehicle.vin:
            lines.append(f"VIN: {vehicle.vin}")
        lines.append(f"Condition: {'New' if vehicle.is_new else 'Used'}")
    if trade is not None:
        lines.append(
            f"Trade-in: {trade.description}  allowance {_money(trade.allowance)}"
            f"  payoff {_money(trade.payoff)}"
        )
    deal_id = getattr(deal, "deal_id", None)
    if deal_id:
        lines.append(f"Deal ID: {deal_id}")

    result = getattr(deal, "worksheet_result", None)
    if result is not None and result.lines:
        lines.append("")
        lines.append("Worksheet (as calculated and certified):")
        for label, amount in result.lines:
            lines.append(f"  {label:<48} {_money(amount):>12}")
    financing = getattr(deal, "financing", None)
    if financing is not None:
        lines.append("")
        lines.append(f"Amount financed: {_money(financing.amount_financed)}")

    certification = getattr(deal, "certification", None)
    if certification is not None:
        lines.append("")
        lines.append(
            f"Certified by {certification.certifier_name} "
            f"({certification.certifier_role}) at {certification.certified_at}."
        )
    if sample:
        lines.append("")
        lines.append("*** SAMPLE -- DRILL ONLY -- NOT A REAL DEAL ***")
    if draft:
        lines.append("")
        lines.append("*** DRAFT -- NOT CERTIFIED -- DRAFT ***")
    lines.append("")
    return CRLF.join(lines) + CRLF


def render_test_page(
    *,
    printer_name: str,
    doc_id: str,
    title: str = "",
    offsets_mm: Optional[dict] = None,
) -> str:
    """A calibration test page: shows the offsets that WILL be applied
    so the user can measure the printed origin against the form."""
    offsets = offsets_mm or {}
    x = float(offsets.get("x_mm", 0.0))
    y = float(offsets.get("y_mm", 0.0))
    lines = [
        "Dealer Revenue Rescue - calibration test page",
        f"Printer: {printer_name}",
        f"Document: {title or doc_id}",
        f"Alignment offset applied: x {x:+.1f} mm, y {y:+.1f} mm",
        "",
        "+----------------------------------------------------------+",
        "|  Print origin marker. Measure this box against the form  |",
        "|  and nudge the offsets until the marker sits correctly.  |",
        "+----------------------------------------------------------+",
        "",
    ]
    return CRLF.join(lines) + CRLF
