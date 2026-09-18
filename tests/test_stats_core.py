#!/usr/bin/env python3
"""Known-input / known-output checks for ``scripts/paper/stats_core.py``.

The paper's headline claims are all statistical (a family of null results, an
undefined minimum detectable effect, a Holm correction that leaves every p at
1.000), so the statistics module is the part of the pipeline where a silent
error would be least visible and most damaging.  These tests pin the numbers
that the prose and the tables quote, using arguments small enough to check by
hand.

Run either way::

    python3 -m pytest tests/test_stats_core.py
    python3 tests/test_stats_core.py

No third-party dependency is required; ``pytest`` is used only if present.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

from paper import stats_core as st  # noqa: E402

# Values the released tables and prose quote (paper/iclr2027/tables/macros.tex).
PAPER_MDE_40_PP = 19.2
PAPER_MDE_80_PP = 13.8
PAPER_MDE_155_PP = 10.0
PAPER_N_FOR_10PP = 155
PAPER_N_FOR_10PP_OBSERVED_NATIVE = 181
PAPER_PI_D_NATIVE = 0.234
PAPER_PARAM_ERROR_P_POOLED = 0.0703
PAPER_NATIVE_PVALUES = [0.7539, 0.5078, 1.0000, 1.0000, 0.2188]
PAPER_POOLED_PVALUES = [1.0000, 1.0000, 0.7744, 1.0000, 0.0703]


def _close(a: float, b: float, tol: float = 1e-4) -> bool:
    return abs(a - b) <= tol


# --------------------------------------------------------------------------
# McNemar
# --------------------------------------------------------------------------
def test_mcnemar_exact_known_value():
    # b=7, c=1: the parameter-error cell in the pooled table (7 negative, 1
    # positive, 32 tied).  Exact two-sided binomial tail on 8 discordant pairs.
    assert _close(st.mcnemar_exact(7, 1), 0.0703125)
    # 0.2188 is the value the native-only table quotes for the same fault.
    assert _close(st.mcnemar_exact(5, 1), 0.21875)


def test_mcnemar_midp_is_less_conservative():
    # The mid-p variant must be strictly smaller at small discordance, and the
    # paper reports both precisely because they differ.
    assert _close(st.mcnemar_midp(7, 1), 0.0390625)
    assert st.mcnemar_midp(7, 1) < st.mcnemar_exact(7, 1)


def test_mcnemar_is_symmetric_and_bounded():
    for b, c in ((0, 0), (1, 0), (7, 1), (5, 5), (12, 3)):
        assert _close(st.mcnemar_exact(b, c), st.mcnemar_exact(c, b))
        assert 0.0 <= st.mcnemar_exact(b, c) <= 1.0
    # No discordant pairs at all means no evidence either way, not p = 0.
    assert st.mcnemar_exact(0, 0) == 1.0
    # Perfectly balanced discordance is the least significant possible outcome.
    assert st.mcnemar_exact(5, 5) == 1.0


# --------------------------------------------------------------------------
# Power / MDE -- the paper's most easily-broken claim
# --------------------------------------------------------------------------
def test_mde_matches_the_published_table():
    assert _close(st.mcnemar_mde(40, 0.20) * 100, PAPER_MDE_40_PP, tol=0.05)
    assert _close(st.mcnemar_mde(80, 0.20) * 100, PAPER_MDE_80_PP, tol=0.05)
    assert _close(st.mcnemar_mde(155, 0.20) * 100, PAPER_MDE_155_PP, tol=0.05)


def test_pairs_required_matches_the_published_table():
    # The macro is emitted with f"{x:.0f}", i.e. round-half-even.
    assert f"{st.mcnemar_n_required(0.10, 0.20):.0f}" == str(PAPER_N_FOR_10PP)
    assert f"{st.mcnemar_n_required(0.10, PAPER_PI_D_NATIVE):.0f}" == str(
        PAPER_N_FOR_10PP_OBSERVED_NATIVE
    )


def test_mde_is_undefined_rather_than_wrong_at_small_n():
    """The central methodological claim: at 30--31 native pairs the design cannot
    reach 80% power at ANY effect size, so the MDE is undefined.  Returning a
    number here would fabricate a detectable effect that the design cannot see.
    """
    pi_d = PAPER_PI_D_NATIVE
    assert math.isnan(st.mcnemar_mde(30, pi_d))
    assert math.isnan(st.mcnemar_mde(31, pi_d))
    # One more pair and it becomes expressible -- the boundary is real, not a
    # blanket NaN for small n.
    mde_32 = st.mcnemar_mde(32, pi_d)
    assert not math.isnan(mde_32)
    assert 0.0 < mde_32 <= pi_d


def test_mde_respects_the_discordance_bracket():
    """|delta| <= pi_d, so the MDE must never exceed the discordance rate.

    A bracket of (0, 1] instead of (0, pi_d] makes every probe return infinity
    past pi_d and the function silently returns NaN for every n.
    """
    for n in (40, 80, 155, 400):
        mde = st.mcnemar_mde(n, 0.20)
        assert not math.isnan(mde), f"unexpected NaN at n={n}"
        assert mde <= 0.20


def test_mde_decreases_with_n():
    prev = math.inf
    for n in (40, 60, 80, 120, 155, 200, 400):
        mde = st.mcnemar_mde(n, 0.25)
        assert not math.isnan(mde)
        assert mde < prev
        prev = mde


def test_mde_rejects_impossible_arguments():
    assert math.isnan(st.mcnemar_mde(0, 0.20))
    assert math.isnan(st.mcnemar_mde(-5, 0.20))
    assert math.isnan(st.mcnemar_mde(40, 0.0))
    assert math.isnan(st.mcnemar_mde(40, 1.5))
    assert math.isinf(st.mcnemar_n_required(0.0, 0.20))
    # delta^2 > pi_d is where the formula breaks down.
    assert math.isinf(st.mcnemar_n_required(0.50, 0.20))
    # Note the gap: delta = 0.30 exceeds pi_d = 0.20, which is impossible for a
    # paired design, yet the formula happily returns ~14.8 pairs because only
    # delta^2 is compared against pi_d.  A caller that does not enforce
    # |delta| <= pi_d can therefore get a confident, meaningless sample size.
    assert st.mcnemar_n_required(0.30, 0.20) < 20


# --------------------------------------------------------------------------
# Multiplicity
# --------------------------------------------------------------------------
def test_holm_leaves_every_native_p_at_one():
    """Section 5.2: on the native-evaluator stratum, no fault survives Holm."""
    adj = st.holm_adjust(PAPER_NATIVE_PVALUES, [f"f{i}" for i in range(5)])
    assert all(_close(a, 1.0) for _, _, a, _ in adj)
    assert not any(rej for *_, rej in adj)


def test_holm_is_not_the_same_on_the_pooled_stratum():
    """The pooled p-values look stronger but still fail.  Keeping the two
    strata apart is the paper's first methodological rule, so it is worth
    asserting that they do NOT give the same adjusted numbers.
    """
    adj = st.holm_adjust(PAPER_POOLED_PVALUES, [f"f{i}" for i in range(5)])
    smallest = min(a for _, _, a, _ in adj)
    assert _close(smallest, 0.3515, tol=1e-3), smallest
    assert smallest > 0.05
    assert not any(rej for *_, rej in adj)


def test_holm_is_monotone_and_never_below_raw():
    ps = [0.001, 0.02, 0.2, 0.5]
    adj = st.holm_adjust(ps, list("abcd"))
    for _, raw, a, _ in adj:
        assert a >= raw - 1e-12
        assert a <= 1.0
    # The smallest raw p gets the largest multiplier (m * p).
    by_raw = sorted(adj, key=lambda t: t[1])
    assert _close(by_raw[0][2], min(1.0, 4 * ps[0]))


def test_benjamini_hochberg_is_bounded_and_ordered():
    labels = list("abcde")
    bh = st.benjamini_hochberg(PAPER_POOLED_PVALUES, labels)
    assert len(bh) == 5
    assert [t[0] for t in bh] == labels  # order preserved
    for _, _, a, _ in bh:
        assert 0.0 <= a <= 1.0


# --------------------------------------------------------------------------
# Intervals
# --------------------------------------------------------------------------
def test_wilson_interval_known_value():
    lo, hi = st.wilson_interval(24, 31)
    assert _close(lo, 0.6019, tol=1e-3)
    assert _close(hi, 0.8860, tol=1e-3)


def test_wilson_degenerate_and_bounded():
    assert st.wilson_interval(0, 0) == (0.0, 1.0)
    for k, n in ((0, 10), (10, 10), (1, 3), (24, 31)):
        lo, hi = st.wilson_interval(k, n)
        assert 0.0 <= lo <= k / n <= hi <= 1.0
    # A zero count must not collapse the interval to a point at 0.
    lo, _ = st.wilson_interval(0, 10)
    assert lo == 0.0
    lo, hi = st.wilson_interval(1, 10)
    assert lo < 0.1 < hi


def test_bootstrap_is_reproducible_under_a_fixed_seed():
    deltas = [0.0, 1.0, -1.0, 2.0, 0.0, -2.0, 1.0, 3.0]
    a = st.bootstrap_delta(deltas, iters=2000)
    b = st.bootstrap_delta(deltas, iters=2000)
    assert a == b, "the same seed must give the same interval"
    assert st.PAPER_BOOT_SEED == 20260914
    # The paper's interval is only trustworthy if it is not an artefact of the
    # one seed, which is why the envelope over several seeds is released with
    # it.  The envelope must contain the headline interval.
    env_lo, env_hi = st.bootstrap_stability(
        deltas, seeds=(st.PAPER_BOOT_SEED, 1, 2), iters=2000
    )
    assert env_lo <= a[0] and env_hi >= a[1]
    # A different seed must not be a no-op, or the stability check is vacuous.
    assert st.bootstrap_delta(deltas, iters=2000, seed=7) != a


def test_bootstrap_interval_contains_the_mean_and_is_ordered():
    deltas = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    lo, hi, mean, se = st.bootstrap_delta(deltas, iters=5000)
    assert lo < hi
    assert lo <= mean <= hi
    assert mean == 4.5
    assert se > 0
    assert all(math.isnan(v) for v in st.bootstrap_delta([]))


# --------------------------------------------------------------------------
# Paired non-parametric tests
# --------------------------------------------------------------------------
def test_hodges_lehmann_known_values():
    assert _close(st.hodges_lehmann([1, 2, 3, 4]), 2.5)
    assert _close(st.hodges_lehmann([5]), 5.0)
    # Walsh averages of [0, 0, 10] are {0, 0, 0, 5, 5, 10}; the median of those
    # six is 2.5, i.e. the estimator is pulled off zero by the single large value.
    assert _close(st.hodges_lehmann([0, 0, 10]), 2.5)
    assert math.isnan(st.hodges_lehmann([]))


def test_wilcoxon_matches_the_exact_null_distribution():
    # All eight differences strictly positive: P(W+ at an extreme) = 2/2^8.
    w, n, p = st.wilcoxon_signed_rank([1, 2, 3, 4, 5, 6, 7, 8])
    assert _close(p, 0.0078125), p
    assert n == 8
    # Zeros are dropped under zero_method='wilcox'.
    assert st.wilcoxon_signed_rank([0.0, 0.0])[2] == 1.0
    # Six positive differences: W+ = 21, W- = 0, so p = 2/2^6.
    assert _close(st.wilcoxon_signed_rank([1, 2, 3, 4, 5, 6])[2], 0.03125)
    # Balanced signs AND balanced magnitudes is the genuine no-evidence case.
    # (Balanced signs with unbalanced magnitudes is not: [-3,-2,-1,1,2,3] puts
    # W+ at 6 against W- at 15, because the ranks follow |difference|.)
    assert st.wilcoxon_signed_rank([-1, 1, -2, 2, -3, 3])[2] == 1.0


def test_wilcoxon_p_is_a_probability():
    for diffs in ([1, -1], [0, 0, 0], [1, 2, 3], list(range(-5, 6))):
        _, _, p = st.wilcoxon_signed_rank(diffs)
        assert 0.0 <= p <= 1.0


def test_sign_test_known_value():
    pos, neg, p = st.sign_test([1, 2, 3, -1])
    assert (pos, neg) == (3, 1)
    assert _close(p, 0.625)  # 2 * (C(4,0) + C(4,1)) / 2^4


# --------------------------------------------------------------------------
# Effect sizes
# --------------------------------------------------------------------------
def test_effect_sizes():
    assert _close(st.risk_difference(0.85, 0.80), -0.05)
    assert _close(st.risk_ratio(0.85, 0.85), 1.0)
    assert _close(st.or_haldane(7, 1), 1.5 / 7.5)
    assert math.isinf(st.matched_odds_ratio(0, 3))
    assert _close(st.success_retention(0.85, 0.85), 1.0)
    assert math.isnan(st.success_retention(0.0, 0.5))
    assert _close(st.cohen_h(0.5, 0.5), 0.0)


# --------------------------------------------------------------------------
if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
    print(f"\n{'OK' if not failures else str(failures) + ' FAILED'}")
    sys.exit(1 if failures else 0)
