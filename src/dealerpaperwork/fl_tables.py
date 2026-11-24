"""Florida's built-in county tables (decision 32).

Two dated tables ship inside the app as plain data files, read from
disk, never fetched:

* ``tables/fl_surtax_rates.json``: each county's total discretionary
  sales surtax by calendar year, copied from the Florida DOR form
  DR-15DSS (and, before the next form is out, the DOR's own notices).
  A manager loads one year's table at setup and approves it by name,
  like every other preset (decision 2). Nothing here reaches a deal
  until it is approved.
* ``tables/fl_zip_counties.json``: every Florida county each ZIP area
  touches, from the Census Bureau's ZIP-to-county relationship file.
  A ZIP inside one county fills the county field. A ZIP that crosses a
  county line lists its counties and the clerk picks the one where the
  buyer registers the vehicle: the app never picks for them.

The rate year matters: Florida county surtaxes change on 1 January.
A preset loaded from a year's table carries that year, and a deal
dated in a later year cannot be certified until a manager loads and
approves the new year's rates (``rates_year_problem``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent / "tables"
RATES_FILE = DATA_DIR / "fl_surtax_rates.json"
ZIP_FILE = DATA_DIR / "fl_zip_counties.json"


@dataclass(frozen=True)
class RateEdition:
    """One calendar year's county surtax table."""

    year: int
    source: str
    provisional: bool
    rates_percent: dict[str, Decimal]

    @property
    def rates(self) -> dict[str, Decimal]:
        """Rates as fractions (1.5% -> 0.015), the preset's format."""
        return {county: pct / 100 for county, pct in self.rates_percent.items()}

    @property
    def label(self) -> str:
        tail = " (from DOR notices; the form is not out yet)" if self.provisional else ""
        return f"{self.year} county rates{tail}"


@lru_cache(maxsize=1)
def editions() -> tuple[RateEdition, ...]:
    raw = json.loads(RATES_FILE.read_text(encoding="utf-8"))
    out = []
    for item in raw["editions"]:
        out.append(RateEdition(
            year=int(item["year"]),
            source=item["source"],
            provisional=bool(item.get("provisional", False)),
            rates_percent={c: Decimal(r) for c, r in item["rates_percent"].items()},
        ))
    return tuple(sorted(out, key=lambda e: e.year))


def edition_for(year: int) -> Optional[RateEdition]:
    for edition in editions():
        if edition.year == year:
            return edition
    return None


def table_text(edition: RateEdition) -> str:
    """The Step 0.7 text box format: "County, percent" per line."""
    return "\n".join(
        f"{county}, {format(pct.normalize(), 'f')}"
        for county, pct in sorted(edition.rates_percent.items())
    )


@lru_cache(maxsize=1)
def _zips() -> dict[str, tuple[str, ...]]:
    raw = json.loads(ZIP_FILE.read_text(encoding="utf-8"))
    return {z: tuple(counties) for z, counties in raw["zips"].items()}


def normalize_zip(text: str) -> str:
    """The five-digit ZIP from "33765" or "33765-1234"; "" otherwise."""
    match = re.fullmatch(r"\s*(\d{5})(?:-\d{4})?\s*", text or "")
    return match.group(1) if match else ""


def counties_for_zip(text: str) -> tuple[str, ...]:
    """Every Florida county a ZIP touches, largest share first.

    Empty when the ZIP is not a Florida ZIP area in the Census file
    (out of state, a PO-box-only ZIP, or a typo): the clerk types the
    county as before.
    """
    return _zips().get(normalize_zip(text), ())


def county_hint(zip_text: str, county: str) -> str:
    """A plain sentence for the deal screen about the ZIP and county."""
    counties = counties_for_zip(zip_text)
    county = (county or "").strip()
    if not normalize_zip(zip_text):
        return ""
    if not counties:
        return "This ZIP is not in the Florida ZIP list. Type the county."
    if len(counties) == 1:
        if county and county != counties[0]:
            return (f"ZIP {normalize_zip(zip_text)} is in {counties[0]} County, "
                    f"not {county}. Check the buyer's address.")
        return ""
    names = ", ".join(counties[:-1]) + " and " + counties[-1]
    if county in counties:
        return ""
    return (f"ZIP {normalize_zip(zip_text)} crosses {names} counties. "
            "Pick the county where the buyer registers the vehicle.")


def fill_county(zip_text: str, county: str) -> str:
    """The county to show after the ZIP is typed: the one county a ZIP
    sits in when the field is blank; otherwise what the clerk typed."""
    county = (county or "").strip()
    if county:
        return county
    counties = counties_for_zip(zip_text)
    return counties[0] if len(counties) == 1 else ""


def rates_year_problem(rates_year: Optional[int], deal_day: Optional[date] = None) -> str:
    """Why a deal cannot be certified on these rates, or "".

    Only presets loaded from a built-in year carry a year; a table a
    store typed by hand carries none and is unchanged by this check.
    """
    if rates_year is None:
        return ""
    year = (deal_day or date.today()).year
    if year == rates_year:
        return ""
    if year > rates_year:
        return (f"Your approved county rates are for {rates_year}; today is in {year}. "
                "County surtax rates change on 1 January. A manager loads the "
                f"{year} rates in setup and approves them before this deal can be "
                "certified.")
    return (f"Your approved county rates are for {rates_year}; they start on "
            f"1 January {rates_year}. A manager loads the {year} rates in setup "
            "for deals before then.")
