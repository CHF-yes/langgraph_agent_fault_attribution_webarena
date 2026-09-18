#!/usr/bin/env python3
"""Regenerate every table, figure input and prose macro in the paper.

    python3 scripts/paper_tables.py

Everything the paper asserts numerically is produced by this one command, from
the committed artifacts under ``experiments/``.  Nothing is hand-copied: the
prose references macros emitted into ``paper/iclr2027/tables/macros.tex``.

Read-only with respect to the experiment data.  Writes only to

    experiments/analysis/paper_v1/stats/   audit trail (one row per quantity)
    paper/iclr2027/tables/                 LaTeX tables and macros
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paper.make_tables import main  # noqa: E402

if __name__ == "__main__":
    main()
