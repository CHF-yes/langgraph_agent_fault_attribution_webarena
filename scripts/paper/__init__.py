"""Reproducible statistics and table generation for the ICLR 2027 paper.

Modules
-------
stats_core   pure-stdlib statistical tests and effect sizes (no scipy)
datasets     uniform loaders over the committed experiment artifacts
latex        LaTeX emission helpers (numbers never print as ``nan``)
make_tables  orchestrator: statistics -> ``paper/iclr2027/tables/*.tex``

Enter through ``scripts/paper_tables.py``.
"""

from __future__ import annotations

__all__ = ["stats_core", "datasets", "latex", "make_tables"]
