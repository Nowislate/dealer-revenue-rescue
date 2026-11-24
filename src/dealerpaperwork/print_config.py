"""Print configuration: copy sets + alignment calibration (Stage 4).

Two dealer-editable preset families, persisted together in one local
JSON file (path injectable; default ``config/print_config.json``,
git-ignored like the other dealer data):

Copy sets
    How many copies of each document go to each recipient. Defaults
    are by document class (pack.py): standard letter documents print
    buyer + dealer. Individual documents can override their class
    default -- the retail installment contract prints one copy each
    for buyer, dealer, and lender (its override preserves the 3-copy
    set it had under the retired long-stock class, Stage 5), and the
    deal worksheet is dealer-only by default.
    Copies print grouped by recipient set, in the order buyer,
    dealer, lender (see ``recipient_order``); the pipeline submits
    one job per document with the total copy count, and the grouping
    order is recorded with the job.

Calibration
    Per (printer, document) alignment offsets in millimetres, for
    nudging printed fields onto pre-printed forms. ``nudge`` moves an
    offset in fixed 0.5 mm steps, the way the calibration screen's
    arrow buttons do. Stage 4's text renderer applies the offsets to
    the payload's origin note; Stage 5's PDF renderer will apply them
    to the form placement itself. The Stage 2 setup file's
    per-assignment ``alignment_offsets_mm`` slot is untouched -- this
    store is the working one.

Standard library only. Offsets are floats (millimetres, never money);
copy counts are ints.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from .pack import DOC_CLASS_LAW_CONTRACT, DOC_CLASS_STANDARD_LETTER

DEFAULT_PRINT_CONFIG_PATH = Path("config") / "print_config.json"

RECIPIENTS = ("buyer", "dealer", "lender")

NUDGE_STEP_MM = 0.5


@dataclass
class CopySet:
    """Copies per recipient for one document."""

    buyer: int = 0
    dealer: int = 0
    lender: int = 0

    @property
    def total(self) -> int:
        return self.buyer + self.dealer + self.lender

    def recipient_order(self) -> tuple[str, ...]:
        """Recipients with at least one copy, in print grouping order."""
        return tuple(r for r in RECIPIENTS if getattr(self, r) > 0)

    def to_dict(self) -> dict:
        return {"buyer": self.buyer, "dealer": self.dealer, "lender": self.lender}

    @classmethod
    def from_dict(cls, data: dict) -> "CopySet":
        return cls(
            buyer=int(data.get("buyer", 0)),
            dealer=int(data.get("dealer", 0)),
            lender=int(data.get("lender", 0)),
        )


def default_copy_sets() -> tuple[dict[str, CopySet], dict[str, CopySet]]:
    """(by-class defaults, per-document overrides) as shipped."""
    by_class = {
        DOC_CLASS_LAW_CONTRACT: CopySet(buyer=1, dealer=1, lender=1),
        DOC_CLASS_STANDARD_LETTER: CopySet(buyer=1, dealer=1, lender=0),
    }
    overrides = {
        # The contract goes to all three recipients (Stage 5: it is a
        # letter document now, so this override keeps its copy set).
        "retail_installment_contract": CopySet(buyer=1, dealer=1, lender=1),
        # The worksheet is the store's working paper: dealer copy only.
        "deal_worksheet": CopySet(buyer=0, dealer=1, lender=0),
    }
    return by_class, overrides


@dataclass
class PrintConfig:
    """Copy sets + calibration offsets, persisted as one JSON file."""

    path: Path = field(default_factory=lambda: DEFAULT_PRINT_CONFIG_PATH)
    copy_sets_by_class: dict[str, CopySet] = field(default_factory=dict)
    copy_set_overrides: dict[str, CopySet] = field(default_factory=dict)
    # key: f"{printer_name}|{doc_id}" -> {"x_mm": float, "y_mm": float}
    calibration: dict[str, dict[str, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not self.copy_sets_by_class and not self.copy_set_overrides:
            by_class, overrides = default_copy_sets()
            self.copy_sets_by_class = by_class
            self.copy_set_overrides = overrides

    # -- copy sets -----------------------------------------------------------

    def copy_set_for(self, doc_id: str, doc_class: str) -> CopySet:
        """The effective copy set: per-document override wins, then
        the document class default, then a single dealer copy."""
        if doc_id in self.copy_set_overrides:
            return self.copy_set_overrides[doc_id]
        if doc_class in self.copy_sets_by_class:
            return self.copy_sets_by_class[doc_class]
        return CopySet(buyer=0, dealer=1, lender=0)

    def total_copies_for(self, doc_id: str, doc_class: str) -> int:
        return self.copy_set_for(doc_id, doc_class).total

    def set_copy_set(
        self, copy_set: CopySet, *, doc_id: Optional[str] = None,
        doc_class: Optional[str] = None,
    ) -> None:
        """Set an override (doc_id) or a class default (doc_class), and save."""
        if (doc_id is None) == (doc_class is None):
            raise ValueError("Give exactly one of doc_id / doc_class.")
        if doc_id is not None:
            self.copy_set_overrides[doc_id] = copy_set
        else:
            self.copy_sets_by_class[doc_class] = copy_set
        self.save()

    # -- calibration -----------------------------------------------------------

    @staticmethod
    def _calibration_key(printer_name: str, doc_id: str) -> str:
        return f"{printer_name}|{doc_id}"

    def get_offsets(self, printer_name: str, doc_id: str) -> dict[str, float]:
        """Current offsets for (printer, document); zeros when unset."""
        stored = self.calibration.get(self._calibration_key(printer_name, doc_id), {})
        return {"x_mm": float(stored.get("x_mm", 0.0)), "y_mm": float(stored.get("y_mm", 0.0))}

    def set_offsets(self, printer_name: str, doc_id: str, x_mm: float, y_mm: float) -> dict[str, float]:
        offsets = {"x_mm": round(float(x_mm), 2), "y_mm": round(float(y_mm), 2)}
        self.calibration[self._calibration_key(printer_name, doc_id)] = offsets
        self.save()
        return offsets

    def nudge(
        self, printer_name: str, doc_id: str, *, dx_steps: int = 0, dy_steps: int = 0,
        step_mm: float = NUDGE_STEP_MM,
    ) -> dict[str, float]:
        """Move the offsets by whole nudge steps (default 0.5 mm each)."""
        current = self.get_offsets(printer_name, doc_id)
        return self.set_offsets(
            printer_name,
            doc_id,
            current["x_mm"] + dx_steps * step_mm,
            current["y_mm"] + dy_steps * step_mm,
        )

    # -- persistence -----------------------------------------------------------

    def save(self, path: Optional[Union[str, Path]] = None) -> Path:
        target = Path(path) if path is not None else self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return target

    def to_dict(self) -> dict:
        return {
            "format_version": "stage4-1",
            "copy_sets_by_class": {
                key: cs.to_dict() for key, cs in self.copy_sets_by_class.items()
            },
            "copy_set_overrides": {
                key: cs.to_dict() for key, cs in self.copy_set_overrides.items()
            },
            "calibration": {
                key: {"x_mm": float(v.get("x_mm", 0.0)), "y_mm": float(v.get("y_mm", 0.0))}
                for key, v in self.calibration.items()
            },
        }

    @classmethod
    def load(cls, path: Union[str, Path] = DEFAULT_PRINT_CONFIG_PATH) -> "PrintConfig":
        """Load a saved config; a missing file yields the shipped defaults."""
        target = Path(path)
        if not target.exists():
            return cls(path=target)
        data = json.loads(target.read_text(encoding="utf-8"))
        by_class, overrides = default_copy_sets()
        config = cls(path=target)
        config.copy_sets_by_class = {
            key: CopySet.from_dict(value)
            for key, value in data.get("copy_sets_by_class", {}).items()
        } or by_class
        config.copy_set_overrides = {
            key: CopySet.from_dict(value)
            for key, value in data.get("copy_set_overrides", {}).items()
        } or overrides
        config.calibration = {
            key: {"x_mm": float(v.get("x_mm", 0.0)), "y_mm": float(v.get("y_mm", 0.0))}
            for key, v in data.get("calibration", {}).items()
        }
        return config
