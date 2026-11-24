"""Build src/dealerpaperwork/tables/fl_zip_counties.json from the Census file.

Source: U.S. Census Bureau, 2020 ZIP Code Tabulation Area (ZCTA) to
county relationship file (public domain):
https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt

Usage (a developer step, run when the Census publishes a new file):
    python scripts/build_fl_zip_counties.py <path to tab20_zcta520_county20_natl.txt>

Every Florida county a ZIP area touches is kept, largest land share first.
A ZIP that crosses a county line keeps every county, so the clerk picks;
the app never chooses one for them. Standard library only.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

FLORIDA_FIPS = "12"
OUT = Path(__file__).resolve().parents[1] / "src/dealerpaperwork/tables/fl_zip_counties.json"
SOURCE = ("U.S. Census Bureau 2020 ZCTA-to-county relationship file "
          "(tab20_zcta520_county20_natl.txt)")


def county_name(namelsad: str) -> str:
    return namelsad[:-len(" County")] if namelsad.endswith(" County") else namelsad


def build(source: Path) -> dict:
    parts: dict[str, list[tuple[str, int]]] = defaultdict(list)
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="|"):
            zcta = row["GEOID_ZCTA5_20"]
            if not zcta or not row["GEOID_COUNTY_20"].startswith(FLORIDA_FIPS):
                continue
            parts[zcta].append((county_name(row["NAMELSAD_COUNTY_20"]),
                                int(row["AREALAND_PART"] or 0)))
    zips = {
        zcta: [name for name, _land in sorted(rows, key=lambda r: (-r[1], r[0]))]
        for zcta, rows in sorted(parts.items())
    }
    return {"source": SOURCE, "state": "FL", "zips": zips}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    data = build(Path(argv[1]))
    OUT.write_text(json.dumps(data, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    crossing = sum(1 for v in data["zips"].values() if len(v) > 1)
    print(f"Wrote {OUT}: {len(data['zips'])} ZIP areas, {crossing} cross a county line.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
