"""Drill mode (Stage 5): SAMPLE-watermarked practice deals.

The contingency only pays on the day the store has REHEARSED it.
Drill mode lets a store run the whole flow -- walk-through, worksheet,
certification, pack printing -- on a practice deal that can never be
mistaken for a real one:

* ``start_drill`` creates a DealSession flagged ``is_drill`` and
  prefilled with clearly SAMPLE data. Its amounts are the spec
  Section 9 acceptance fixture (price $35,000, dealer fee $1,199,
  trade allowance $10,000, a 1.5% county), so a drill also
  RE-VERIFIES the math: a correct drill worksheet totals exactly
  $1,658.88 of tax (the drill deal's preset carries the $199
  electronic tag fee, taxed under Decision 14).
* A drill deal's serialized watermark is "SAMPLE" (deal_flow), and
  the print pipeline banners every drill document SAMPLE -- certified
  or not. A certify-capable user may certify a drill for practice;
  that changes nothing about the marking and the deal never counts
  as real: ``deal_flow.list_deals`` excludes drills unless asked.
* ``finish_drill`` records the run (who, when, how long, whether the
  fixture tax verified, and the outcome) in a local JSON file, and
  ``last_drill_summary`` answers the readiness question: when did we
  last drill, who ran it, and did it pass?

Standard library only; all logic headless-testable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Union

from .deal_flow import DEFAULT_DEALS_DIR, DealSession
from .models import Buyer, TradeIn, Vehicle
from .presets import Preset

DEFAULT_DRILL_RUNS_PATH = Path("data") / "drill_runs.json"

# Decision 14: includes the tax on the preset's $199 electronic tag fee.
FIXTURE_TOTAL_TAX = Decimal("1658.88")
FIXTURE_COUNTY_RATE = Decimal("0.015")
SAMPLE_BUYER_NAME = "SAMPLE BUYER - DRILL"


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _fixture_county(preset: Preset) -> str:
    """The preset county that makes the drill the Section 9 fixture:
    one whose approved surtax rate is 1.5%. Falls back to the first
    county in the table (the drill still runs; the fixture check in
    the run record will honestly report False)."""
    for county, rate in preset.county_surtax_rates.items():
        if rate == FIXTURE_COUNTY_RATE:
            return county
    if preset.county_surtax_rates:
        return sorted(preset.county_surtax_rates)[0]
    return "Sample County"


def start_drill(
    user,
    *,
    preset: Union[Preset, dict],
    setup_path,
    deals_dir: Union[str, Path] = DEFAULT_DEALS_DIR,
    audit_log=None,
) -> DealSession:
    """Create a SAMPLE practice deal on the approved preset.

    The session enforces the usual rules (setup complete, approved
    preset, edit permission -- DealSession's constructor). The user
    then walks, computes, and may certify it exactly like a real
    deal; every artefact it produces is SAMPLE-marked.
    """
    if isinstance(preset, dict):
        from .deal_flow import preset_from_dict

        preset_obj = preset_from_dict(preset)
    else:
        preset_obj = preset
    session = DealSession(
        user=user,
        preset=preset_obj,
        setup_path=setup_path,
        deals_dir=deals_dir,
        audit_log=audit_log,
        is_drill=True,
    )
    session.set_buyer(
        Buyer(
            full_name=SAMPLE_BUYER_NAME,
            street_address="100 Sample Street",
            city="Sample City",
            state="FL",
            zip_code="00000",
            residence_county=_fixture_county(preset_obj),
        )
    )
    session.set_vehicle(
        Vehicle(
            year=2026,
            make="Sample",
            model="Drill Vehicle",
            vin="SAMPLEVIN00000000",
            is_new=True,
            stock_number="DRILL",
        )
    )
    session.set_trade(
        TradeIn(
            description="Sample trade-in (drill)",
            allowance=Decimal("10000"),
            payoff=Decimal("0"),
        )
    )
    # The fixture's money: the dealer fee and electronic tag fee were
    # already seeded from the approved preset by DealSession; the
    # fixture expects 1199 / 199, which the drill store's preset
    # carries when it is the sample/fixture configuration.
    session.set_money(selling_price=Decimal("35000"))
    session.compute_worksheet()
    return session


def fixture_tax_verified(session: DealSession) -> bool:
    """True when the drill's worksheet reproduces the fixture total
    tax exactly ($1,658.88 under Decision 14 -- the drill deal carries
    the preset's taxed $199 electronic tag fee)."""
    result = getattr(session, "worksheet_result", None)
    if result is None:
        try:
            result = session.compute_worksheet()
        except Exception:
            return False
    return result is not None and result.total_tax == FIXTURE_TOTAL_TAX


def _pack_done(print_session) -> bool:
    """True when the drill's pack went all the way through: every
    printable item printed and every secure item checked off.

    ``PrintSession.is_complete`` is False for every SAMPLE session by
    design (a SAMPLE pack is never a real, finished package), so a drill
    asks the checklist items directly.
    """
    if print_session is None:
        return False
    if getattr(print_session, "is_complete", False):
        return True
    items = getattr(print_session, "items", None)
    if not items:
        return False
    values = items.values() if isinstance(items, dict) else items
    return all(getattr(item, "is_done", False) for item in values)


def finish_drill(
    session: DealSession,
    *,
    runs_path: Union[str, Path] = DEFAULT_DRILL_RUNS_PATH,
    print_session=None,
) -> dict:
    """Record a finished drill run and return the record.

    Outcome is "completed" when a print session is supplied and its
    pack is complete (every printable printed, every secure item
    pulled); otherwise the drill simply "ended". The record appends
    to the runs file -- the store's drill history is the readiness
    evidence.
    """
    completed_at = _now()
    started_at = session.created_at
    try:
        started = datetime.fromisoformat(str(started_at))
        duration = (completed_at - started).total_seconds()
    except (TypeError, ValueError):
        duration = 0.0
    outcome = "completed" if _pack_done(print_session) else "ended"
    entry = {
        "deal_id": session.deal_id,
        "user": session.created_by,
        "started_at": started_at,
        "completed_at": completed_at.isoformat(timespec="seconds"),
        "duration_seconds": round(max(duration, 0.0), 1),
        "fixture_tax_verified": fixture_tax_verified(session),
        "outcome": outcome,
    }
    path = Path(runs_path)
    runs = drill_runs(path)
    runs.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs, indent=2, sort_keys=True), encoding="utf-8")
    return entry


def drill_runs(runs_path: Union[str, Path] = DEFAULT_DRILL_RUNS_PATH) -> list[dict]:
    """All recorded drill runs, oldest first ([] when none)."""
    try:
        data = json.loads(Path(runs_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def last_drill_summary(
    runs_path: Union[str, Path] = DEFAULT_DRILL_RUNS_PATH,
) -> Optional[dict]:
    """The readiness one-liner: last drill, who ran it, how it went.
    None when the store has never drilled."""
    runs = drill_runs(runs_path)
    if not runs:
        return None
    last = runs[-1]
    return {
        "last_drill_at": last.get("completed_at"),
        "user": last.get("user"),
        "outcome": last.get("outcome"),
        "fixture_tax_verified": bool(last.get("fixture_tax_verified")),
        "duration_seconds": last.get("duration_seconds"),
    }


def run_drill(
    user,
    *,
    preset: Union[Preset, dict],
    setup_path,
    walk,
    print_pack,
    deals_dir: Union[str, Path] = DEFAULT_DEALS_DIR,
    audit_log=None,
    runs_path: Union[str, Path] = DEFAULT_DRILL_RUNS_PATH,
) -> dict:
    """One whole drill, the way the home screen's "Practice drill"
    button runs it, and the run record it leaves.

    ``walk(session)`` shows the SAMPLE deal in the normal walk-through
    (the user may change it and certify it for practice).
    ``print_pack(session)`` offers the SAMPLE pack for printing and
    returns the print session, or None when the user skips printing.
    The deal is reloaded from its autosave between the two, so the
    record reflects what the user actually left on screen. The run is
    recorded even when a step raises, so an interrupted drill still
    shows in the store's history.
    """
    if isinstance(preset, dict):
        from .deal_flow import preset_from_dict

        preset = preset_from_dict(preset)
    session = start_drill(
        user, preset=preset, setup_path=setup_path,
        deals_dir=deals_dir, audit_log=audit_log,
    )
    print_session = None
    try:
        walk(session)
        session = DealSession.load(
            session.deal_id, user=user, preset=preset,
            deals_dir=deals_dir, audit_log=audit_log,
        )
        print_session = print_pack(session)
    finally:
        record = finish_drill(
            session, runs_path=runs_path, print_session=print_session,
        )
    return record


def readiness_line(summary: Optional[dict]) -> str:
    """The plain-words drill line for the home screen.

    ``summary`` is ``last_drill_summary()`` (or a run record). The
    sample-tax check is mentioned only when it passed: on a store whose
    approved fees differ from the sample fees the check cannot match,
    and that is not a failure of the drill.
    """
    if not summary:
        return ("No practice drill on record. Run one now, before an "
                "outage, so the store knows the steps.")
    when = summary.get("last_drill_at") or summary.get("completed_at") or ""
    when = str(when).replace("T", " ")[:16]
    who = summary.get("user") or "unknown user"
    if summary.get("outcome") == "completed":
        result = "the SAMPLE pack printed in full"
    else:
        result = "the pack was not printed in full"
    line = f"Last practice drill: {when}, run by {who}; {result}."
    if summary.get("fixture_tax_verified"):
        line += " The sample tax check matched."
    return line
