"""Pure-stdlib statistics for the Web-agent robustness paper.

No scipy / numpy / pandas is required.  Everything here is implemented with
``math`` and ``statistics`` from the standard library so that the pipeline runs
on any interpreter the repo already supports:

* exact McNemar test        -> ``math.comb`` (log-space via ``math.lgamma`` for large n)
* Wilson score interval     -> ``statistics.NormalDist``
* percentile bootstrap CI   -> ``statistics`` + an explicit fixed seed
* Wilcoxon signed-rank      -> exact enumeration for small n, normal approx otherwise
* Holm / Benjamini-Hochberg -> ``math``

The module deliberately contains no I/O and no project-specific schema so that
it can be unit-tested in isolation (see ``tests/test_stats_core.py``).
"""

from __future__ import annotations

import math
import random
from statistics import NormalDist

PAPER_BOOT_SEED = 20260914
_Z = NormalDist()


# --------------------------------------------------------------------------
# McNemar
# --------------------------------------------------------------------------
def _binom_two_sided_tail(k: int, n: int) -> float:
    """P(X <= k) + P(X >= n-k) for X ~ Binomial(n, 1/2), computed exactly."""
    if n == 0:
        return 1.0
    # P(X = i) built by ratio from P(X = 0) = 2^-n so no huge intermediate factorials.
    p0 = 2.0 ** (-n)
    pmf = [p0]
    for i in range(1, n + 1):
        pmf.append(pmf[-1] * (n - i + 1) / i)
    k = min(k, n - k)
    lo = sum(pmf[: k + 1])
    hi = sum(pmf[n - k :])
    return min(1.0, lo + hi)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar (binomial) test on discordant counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    return _binom_two_sided_tail(min(b, c), n)


def mcnemar_midp(b: int, c: int) -> float:
    """Mid-p variant; less conservative than the exact test at small n."""
    n = b + c
    if n == 0:
        return 1.0
    p0 = 2.0 ** (-n)
    pmf = [p0]
    for i in range(1, n + 1):
        pmf.append(pmf[-1] * (n - i + 1) / i)
    k = min(b, c)
    lo = sum(pmf[:k]) + 0.5 * pmf[k]
    return min(1.0, 2.0 * lo)


def mcnemar_asymptotic(b: int, c: int) -> tuple[float, float]:
    """Continuity-corrected chi-square (df=1) and its p-value."""
    n = b + c
    if n == 0:
        return 0.0, 1.0
    chi2 = (abs(b - c) - 1) ** 2 / n
    chi2 = max(0.0, chi2)
    p = math.erfc(math.sqrt(chi2 / 2.0))
    return chi2, p


def mcnemar_mde(n_pairs: int, pi_d: float, alpha: float = 0.05, power: float = 0.80) -> float:
    """Minimum detectable *difference in proportions* for a paired design.

    ``pi_d`` is the discordance rate P(the two conditions disagree).  The
    standard paired-proportion formula is used:

        n = (z_{1-alpha/2} * sqrt(pi_d) + z_{power} * sqrt(pi_d - delta^2))^2 / delta^2

    solved for delta by bisection (the equation has no closed form once
    ``delta`` appears on both sides).

    The search bracket matters.  For paired binary outcomes the risk difference
    is bounded by the discordance rate, ``|delta| <= pi_d`` -- NOT by 1.  A
    bracket that ignores this pushes ``delta`` past ``pi_d``, where
    ``pi_d - delta^2`` goes negative, every probe evaluates to infinity, and the
    function silently returns NaN for every n.  ``required(delta)`` is strictly
    decreasing on ``(0, pi_d]``, so bisection is applied there.
    """
    if n_pairs <= 0 or not 0.0 < pi_d <= 1.0:
        return float("nan")
    z_a = _Z.inv_cdf(1 - alpha / 2)
    z_b = _Z.inv_cdf(power)

    def required(delta: float) -> float:
        inner = pi_d - delta * delta
        if inner < 0:
            return float("inf")
        return (z_a * math.sqrt(pi_d) + z_b * math.sqrt(inner)) ** 2 / (delta * delta)

    hi = pi_d  # largest difference the paired design can express
    if required(hi) > n_pairs:
        # Even the most extreme detectable effect would need more pairs: the
        # design cannot reach the target power at any effect size.
        return float("nan")
    lo = 1e-9
    for _ in range(200):
        mid = (lo + hi) / 2
        if required(mid) > n_pairs:
            lo = mid
        else:
            hi = mid
    return hi


def mcnemar_n_required(delta: float, pi_d: float, alpha: float = 0.05, power: float = 0.80) -> float:
    """Pairs per arm needed to detect ``delta`` at the given discordance rate."""
    if delta <= 0:
        return float("inf")
    z_a = _Z.inv_cdf(1 - alpha / 2)
    z_b = _Z.inv_cdf(power)
    inner = pi_d - delta * delta
    if inner < 0:
        return float("inf")
    return (z_a * math.sqrt(pi_d) + z_b * math.sqrt(inner)) ** 2 / (delta * delta)


# --------------------------------------------------------------------------
# Interval estimates
# --------------------------------------------------------------------------
def wilson_interval(k: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a single proportion."""
    if n == 0:
        return (0.0, 1.0)
    z = _Z.inv_cdf(1 - (1 - conf) / 2)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_delta(
    deltas: list[float], iters: int = 20000, seed: int = PAPER_BOOT_SEED, conf: float = 0.95
) -> tuple[float, float, float, float]:
    """Percentile bootstrap CI for the mean of paired differences.

    Resampling is done over **pair units** (one (task, seed) cell), which is the
    correct unit for a paired design.  Returns (lo, hi, mean, se).
    """
    if not deltas:
        return (float("nan"), float("nan"), float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(deltas)
    means: list[float] = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += deltas[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int((1 - conf) / 2 * iters)]
    hi = means[min(iters - 1, int((1 + conf) / 2 * iters))]
    mean = sum(deltas) / n
    var = sum((d - mean) ** 2 for d in deltas) / max(1, n - 1)
    return (lo, hi, mean, math.sqrt(var / n))


def bootstrap_stability(
    deltas: list[float], seeds: tuple[int, ...], iters: int = 20000
) -> tuple[float, float]:
    """CI envelope across several fixed bootstrap seeds (guards seed-shopping)."""
    los, his = [], []
    for s in seeds:
        lo, hi, _, _ = bootstrap_delta(deltas, iters=iters, seed=s)
        los.append(lo)
        his.append(hi)
    return (min(los), max(his))


# --------------------------------------------------------------------------
# Effect sizes
# --------------------------------------------------------------------------
def risk_difference(p_control: float, p_fault: float) -> float:
    return p_fault - p_control


def risk_ratio(p_control: float, p_fault: float) -> float:
    if p_control == 0:
        return float("inf")
    return p_fault / p_control


def matched_odds_ratio(b: int, c: int) -> float:
    """Discordant-pair odds ratio c/b (fault-only vs control-only)."""
    if b == 0:
        return float("inf")
    return c / b


def or_haldane(b: int, c: int) -> float:
    """Haldane-Anscombe corrected OR; finite when b or c is zero."""
    return (c + 0.5) / (b + 0.5)


def success_retention(p_control: float, p_fault: float) -> float:
    """R_success = fault / control."""
    if p_control == 0:
        return float("nan")
    return p_fault / p_control


def cohen_h(p_control: float, p_fault: float) -> float:
    return 2 * math.asin(math.sqrt(p_fault)) - 2 * math.asin(math.sqrt(p_control))


# --------------------------------------------------------------------------
# Paired non-parametric tests on continuous deltas
# --------------------------------------------------------------------------
def wilcoxon_signed_rank(diffs: list[float], zero_method: str = "wilcox") -> tuple[float, int, float]:
    """Two-sided Wilcoxon signed-rank test.

    ``zero_method='wilcox'`` drops zero differences; ``'pratt'`` keeps them in
    the ranking.  Exact enumeration is used when the effective n is small enough
    (<= 20), otherwise a tie-corrected normal approximation.
    """
    vals = [d for d in diffs if d != 0] if zero_method == "wilcox" else list(diffs)
    n = len(vals)
    if n == 0:
        return (0.0, 0, 1.0)
    order = sorted(range(n), key=lambda i: abs(vals[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(vals[order[j + 1]]) == abs(vals[order[i]]):
            j += 1
        avg = (i + j + 2) / 2.0  # 1-based average rank
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    w_plus = sum(ranks[i] for i in range(n) if vals[i] > 0)
    if zero_method == "pratt":
        for i in range(n):
            if vals[i] == 0:
                w_plus += ranks[i] / 2.0
    total = sum(ranks)
    w = min(w_plus, total - w_plus)

    if n <= 20:
        # Exact distribution of W+ over all 2^n sign assignments of the ranks.
        counts: dict[float, int] = {0.0: 1}
        for r in ranks:
            nxt: dict[float, int] = {}
            for s, cnt in counts.items():
                nxt[s] = nxt.get(s, 0) + cnt
                nxt[s + r] = nxt.get(s + r, 0) + cnt
            counts = nxt
        target = min(w_plus, total - w_plus)
        tail = sum(cnt for s, cnt in counts.items() if min(s, total - s) <= target + 1e-9)
        p = min(1.0, tail / (2 ** n))
        return (w, n, p)

    mean_w = total / 2.0
    var_w = n * (n + 1) * (2 * n + 1) / 24.0
    if var_w <= 0:
        return (w, n, 1.0)
    z = (w_plus - mean_w) / math.sqrt(var_w)
    return (w, n, math.erfc(abs(z) / math.sqrt(2)))


def sign_test(diffs: list[float]) -> tuple[int, int, float]:
    """Two-sided sign test; zeros are dropped."""
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    n = pos + neg
    if n == 0:
        return (0, 0, 1.0)
    return (pos, neg, _binom_two_sided_tail(min(pos, neg), n))


def hodges_lehmann(diffs: list[float]) -> float:
    """Hodges-Lehmann estimate: median of Walsh averages."""
    if not diffs:
        return float("nan")
    walsh = []
    n = len(diffs)
    for i in range(n):
        for j in range(i, n):
            walsh.append((diffs[i] + diffs[j]) / 2.0)
    walsh.sort()
    m = len(walsh)
    return walsh[m // 2] if m % 2 else (walsh[m // 2 - 1] + walsh[m // 2]) / 2.0


def mean_median_iqr(values: list[float]) -> tuple[float, float, float, float]:
    """(mean, median, q1, q3); NaN for an empty input."""
    if not values:
        return (float("nan"),) * 4

    def q(p: float) -> float:
        s = sorted(values)
        if len(s) == 1:
            return s[0]
        idx = p * (len(s) - 1)
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return s[lo]
        return s[lo] + (s[hi] - s[lo]) * (idx - lo)

    return (sum(values) / len(values), q(0.5), q(0.25), q(0.75))


# --------------------------------------------------------------------------
# Multiplicity
# --------------------------------------------------------------------------
def holm_adjust(pvalues: list[float], labels: list[str]) -> list[tuple[str, float, float, bool]]:
    """Holm-Bonferroni step-down. Returns (label, raw, adjusted, reject_at_0.05)."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    out: list[tuple[str, float, float, bool]] = []
    running = 0.0
    for rank, i in enumerate(order):
        adj = min(1.0, (m - rank) * pvalues[i])
        running = max(running, adj)
        out.append((labels[i], pvalues[i], running, running < 0.05))
    return out


def benjamini_hochberg(pvalues: list[float], labels: list[str]) -> list[tuple[str, float, float, bool]]:
    """Benjamini-Hochberg FDR step-up."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adj = [0.0] * m
    running = 1.0
    for rank in range(m - 1, -1, -1):
        i = order[rank]
        running = min(running, pvalues[i] * m / (rank + 1))
        adj[i] = min(1.0, running)
    return [(labels[i], pvalues[i], adj[i], adj[i] < 0.05) for i in range(m)]
