"""Reconcile the merged rows with the authoritative on-disk trial records.

Four rows in experiments/stage_c_full/rows.json disagreed with the trial_record at
the same path (steps, llm_calls, cap_exhausted); one of them, task 22's parameter
fault arm, was recorded as 7 steps and not exhausted where the record says 20 and
exhausted.  The merged table was a stale snapshot: the cell had been re-run after
its rows.json was written.

This rebuilds the merged table without touching the evaluator:

* official_success / evaluation_status come from the committed pipeline rows, so
  the 768 official labels are preserved exactly (they were verified to agree);
* steps, llm_calls and cap_exhausted come from the trial_record that
  ``trial_dir`` points at, which is authoritative for the cell and names the
  run_id and trace of the run that actually produced it.

Writes experiments/stage_c_full/rows.json and a reconciliation report.  Read-only
with respect to trials and to the evaluator.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTS = [("deepseek_v41_flash", "react", "stage_c_slice1"),
         ("deepseek_v41_flash", "react", "stage_c_slice1b"),
         ("deepseek_v41_flash", "plan_execute", "stage_c_slice2b"),
         ("deepseek_v41_flash", "plan_execute", "stage_c_slice2c"),
         ("qwen38_flash", "react", "stage_c_slice3"),
         ("qwen38_flash", "react", "stage_c_slice3b"),
         ("qwen38_flash", "plan_execute", "stage_c_slice4"),
         ("qwen38_flash", "plan_execute", "stage_c_slice4b")]
FIELDS = ("steps", "llm_calls", "cap_exhausted")


def main():
    merged, patched, missing = [], [], []
    label_changes = []
    for model, arch, root in ROOTS:
        rows = json.load(open(ROOT / "experiments" / root / "pipeline" / "rows.json"))["rows"]
        for row in rows:
            trial_dir = Path(row["trial_dir"])
            record_path = trial_dir / "trial_record.json"
            if not record_path.exists():
                missing.append(str(trial_dir))
                merged.append(row)
                continue
            record = json.load(open(record_path))
            changed = {}
            for field in FIELDS:
                if field in record and row.get(field) != record.get(field):
                    changed[field] = {"merged": row.get(field), "record": record.get(field)}
                    row[field] = record.get(field)
            if changed:
                patched.append({"trial_dir": str(trial_dir),
                                "run_id": (record.get("run_config") or {}).get("run_id"),
                                "changes": changed})
            row["_run_id"] = (record.get("run_config") or {}).get("run_id")
            row["_trace"] = (record.get("paths") or {}).get("trace")
            row["_source_root"] = root
            merged.append(row)

    json.dump({"rows": merged,
               "reconciliation": {"patched_rows": patched,
                                  "missing_trial_records": missing,
                                  "label_changes": label_changes}},
              open(ROOT / "experiments/stage_c_full/rows.json", "w"), indent=1)
    report = {"roots": [r for _, _, r in ROOTS], "rows": len(merged),
              "patched": len(patched), "missing_records": len(missing),
              "patched_rows": patched, "label_changes": label_changes}
    json.dump(report, open(ROOT / "experiments/stage_c_audit/reconciliation.json", "w"), indent=1)
    print(json.dumps({"rows": len(merged), "patched": len(patched),
                      "missing_records": len(missing),
                      "patched_detail": patched}, indent=1))


if __name__ == "__main__":
    main()
