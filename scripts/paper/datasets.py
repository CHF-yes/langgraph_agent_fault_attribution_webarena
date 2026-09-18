"""Loaders that turn the committed experiment artifacts into a uniform schema.

Design rules enforced here:

* ``official_trials.json`` carries **no** ``evaluation_source`` field.  It must be
  joined with ``official_trial_audit.csv`` on (fault_type, task_id, seed,
  condition) or native and fallback evaluator outcomes get silently pooled.
* A design factor that was never recorded is reported as ``UNRECORDED`` -- the
  loader never invents ``react``/``gpt54`` from a directory name.
* The 48-trial baseline (16 tasks x 3 seeds) and the 400-trial official subset
  (8 tasks x 5 seeds) share a column name but not a design.  Each row is tagged
  with ``dataset_layer`` so the two cannot be concatenated by accident.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

UNRECORDED = "UNRECORDED"

FAULTS_MAIN = [
    "web_dom_missing",
    "web_popup_block",
    "web_http_error",
    "web_timeout",
    "agent_param_error",
]

FAULT_LABEL = {
    "web_dom_missing": "DOM omission",
    "web_popup_block": "Popup block",
    "web_http_error": "HTTP error",
    "web_timeout": "Timeout",
    "agent_param_error": "Parameter error",
}


class SchemaGapError(RuntimeError):
    """Raised when a claim would require a design factor the data never recorded."""


@dataclass
class Trial:
    fault_type: str
    task_id: int
    seed: int
    condition: str
    official_success: bool | None
    evaluator_status: str = ""
    evaluation_source: str = ""
    steps: float | None = None
    llm_calls: float | None = None
    time_sec: float | None = None
    model_profile: str = UNRECORDED
    architecture: str = UNRECORDED
    dataset_layer: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class PairedCell:
    fault_type: str
    task_id: int
    seed: int
    control: bool
    fault: bool
    control_source: str = ""
    fault_source: str = ""
    dataset_layer: str = ""

    @property
    def delta(self) -> float:
        return float(self.fault) - float(self.control)

    @property
    def kind(self) -> str:
        if self.control and self.fault:
            return "concordant_pass"
        if not self.control and not self.fault:
            return "concordant_fail"
        if self.control and not self.fault:
            return "control_only"
        return "fault_only"


# --------------------------------------------------------------------------
def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_official(root: Path) -> list[Trial]:
    """Join official_trials.json with official_trial_audit.csv to recover evaluation_source."""
    trials_path = root / "official_trials.json"
    audit_path = root / "official_trial_audit.csv"
    rows = json.loads(trials_path.read_text(encoding="utf-8"))
    audit = {
        (r["fault_type"], int(r["task_id"]), int(r["seed"]), r["condition"]): r
        for r in _read_csv(audit_path)
    }
    out: list[Trial] = []
    for r in rows:
        key = (r["fault_type"], int(r["task_id"]), int(r["seed"]), r["condition"])
        a = audit.get(key)
        if a is None:
            raise SchemaGapError(f"audit row missing for {key}")
        out.append(
            Trial(
                fault_type=r["fault_type"],
                task_id=int(r["task_id"]),
                seed=int(r["seed"]),
                condition=r["condition"],
                official_success=bool(r["official_success"]),
                evaluator_status=r.get("evaluator_status") or "",
                evaluation_source=a.get("evaluation_source", ""),
                model_profile=UNRECORDED,
                architecture=UNRECORDED,
                dataset_layer="official_subset",
                extra={"audit_passed": a.get("audit_passed"), "source": a.get("source")},
            )
        )
    return out


def load_matrix(root: Path) -> tuple[list[Trial], list[PairedCell]]:
    """Behavioral fault matrix.  Returns per-trial rows and the 400 paired cells."""
    rows = json.loads((root / "trials.json").read_text(encoding="utf-8"))
    trials: list[Trial] = []
    for r in rows:
        trials.append(
            Trial(
                fault_type=r["fault_type"],
                task_id=int(r["task_id"]),
                seed=int(r.get("run_seed") or 0),
                condition=r["condition"],
                official_success=None,  # NOT_EVALUATED_NO_HAR
                evaluator_status="NOT_EVALUATED_NO_HAR",
                steps=r.get("steps"),
                llm_calls=r.get("llm_calls"),
                time_sec=r.get("time_sec"),
                model_profile=r.get("model_profile", UNRECORDED),
                architecture=r.get("architecture", UNRECORDED),
                dataset_layer="behavioral_matrix",
                extra={"source_file": "trials.json"},
            )
        )
    return trials, build_pairs(trials, dataset_layer="behavioral_matrix")


def build_pairs(trials: list[Trial], dataset_layer: str = "") -> list[PairedCell]:
    """Match control and fault trials on (fault_type, task_id, seed)."""
    index: dict[tuple, dict[str, Trial]] = {}
    for t in trials:
        index.setdefault((t.fault_type, t.task_id, t.seed), {})[t.condition] = t
    cells: list[PairedCell] = []
    for (fault, task, seed), pair in sorted(index.items()):
        if "control" not in pair or "fault" not in pair:
            continue
        ctl, flt = pair["control"], pair["fault"]
        cells.append(
            PairedCell(
                fault_type=fault,
                task_id=task,
                seed=seed,
                control=bool(ctl.official_success),
                fault=bool(flt.official_success),
                control_source=ctl.evaluation_source,
                fault_source=flt.evaluation_source,
                dataset_layer=dataset_layer or ctl.dataset_layer,
            )
        )
    return cells


def concordance(cells: list[PairedCell]) -> tuple[int, int, int, int]:
    """(a=both pass, b=control only, c=fault only, d=both fail)."""
    a = sum(1 for x in cells if x.kind == "concordant_pass")
    b = sum(1 for x in cells if x.kind == "control_only")
    c = sum(1 for x in cells if x.kind == "fault_only")
    d = sum(1 for x in cells if x.kind == "concordant_fail")
    return a, b, c, d


def load_baseline(root: Path) -> list[dict]:
    """The frozen 48-trial ReAct control baseline, stratified by fallback_used."""
    return json.loads((root / "gpt54_react_public16_control_v1.json").read_text(encoding="utf-8"))


def load_architecture(root: Path) -> list[dict]:
    rows = _read_csv(root / "architecture_trial_results.csv")
    for r in rows:
        for k in ("steps", "llm_calls", "time_sec"):
            r[k] = float(r[k]) if r.get(k) not in (None, "") else None
        r["official_success"] = str(r.get("official_success")).lower() == "true"
    return rows


def load_recovery(root: Path) -> list[dict]:
    return _read_csv(root / "fault_recovery_behavior.csv")
