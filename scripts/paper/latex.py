"""LaTeX emission helpers.

Every number that appears in the paper is written through these helpers so that
the prose can reference macros (``\\ResParamErrDelta{}``) instead of literals.
That is what makes "numbers are never hand-copied" checkable: regenerating the
pipeline rewrites both the tables and the macros the prose points at.
"""

from __future__ import annotations

import math

_ESCAPES = {
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape(s: str) -> str:
    return "".join(_ESCAPES.get(ch, ch) for ch in str(s))


def num(x, digits: int = 3) -> str:
    """Format a number, never emitting nan/None and never faking significance."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    if isinstance(x, float) and math.isinf(x):
        return r"$\infty$"
    if 0 < abs(x) < 10 ** (-digits) and x != 0:
        return f"$<$0.{'0' * (digits - 1)}1"
    return f"{x:.{digits}f}".rstrip("0").rstrip(".") if isinstance(x, float) else str(x)


def pval(p) -> str:
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "n/a"
    if p < 0.001:
        return r"$<$0.001"
    return f"{p:.3f}"


def pp(x, digits: int = 1) -> str:
    """Signed percentage points, e.g. -15.0 -> '$-15.0$'."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    sign = "$-$" if x < 0 else "$+$"
    return f"{sign}{abs(x):.{digits}f}"


def pct(k: int, n: int, digits: int = 1) -> str:
    """Percentage with an escaped percent sign.

    The escape must be a single backslash (``\\%`` in source).  Emitting ``\\\\%``
    would end the table row and comment out the rest of the line.
    """
    if n == 0:
        return "n/a"
    return f"{100 * k / n:.{digits}f}\\%"


def ci(lo, hi, digits: int = 1) -> str:
    if lo is None or hi is None or (isinstance(lo, float) and math.isnan(lo)):
        return "n/a"
    return f"[{pp(lo, digits)}, {pp(hi, digits)}]"


def macro(name: str, value: str) -> str:
    return f"\\newcommand{{\\{name}}}{{{value}}}"


def macro_block(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(macro(n, v) for n, v in pairs) + "\n"


def booktabs(
    header: list[str],
    rows: list[list[str]],
    caption: str,
    label: str,
    col_spec: str | None = None,
    notes: str | None = None,
    small: bool = False,
) -> str:
    spec = col_spec or ("l" + "r" * (len(header) - 1))
    body = ["\\toprule", " & ".join(header) + r" \\", "\\midrule"]
    for row in rows:
        body.append(" & ".join(row) + r" \\")
    body.append("\\bottomrule")
    inner = "\n".join(body)
    # \small belongs in the table, NOT inside the tabular.  booktabs' \toprule is a
    # \noalign, and \noalign is only legal directly after the alignment's \cr; a font
    # switch as the tabular body's first item breaks that ("Misplaced \noalign").
    sizing = "\\small\n" if small else ""
    # The note must also sit AFTER \end{tabular}: a \parbox inside the alignment is
    # parsed as a malformed extra row and blows the table far past \linewidth.
    note = ("\\vspace{2pt}\n\\parbox{\\linewidth}{\\footnotesize " + notes + "}\n") if notes else ""
    # adjustbox's `max width` shrinks an over-wide table to \linewidth but never
    # enlarges a narrow one, which \resizebox would.
    return (
        "\\begin{table}[t]\n\\centering\n" + sizing +
        f"\\caption{{{caption}}}\n\\label{{{label}}}\n"
        "\\begin{adjustbox}{max width=\\linewidth}\n"
        f"\\begin{{tabular}}{{{spec}}}\n{inner}\n\\end{{tabular}}\n"
        "\\end{adjustbox}\n" + note +
        "\\end{table}\n"
    )
