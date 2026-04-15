"""Statistical tests for study condition comparisons.

Implements hypothesis tests for comparing conditions:
- H1/H2: Two-proportion z-test on supported-claim rate
- H3: Paired t-test on log(verification time)
- H4: Mann-Whitney U on Likert scores
- H5: Welch t-test on coverage residual
- H6: Spearman rank correlation
- Multiple comparison correction: Holm-Bonferroni
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats


def two_proportion_z(
    successes1: int,
    n1: int,
    successes2: int,
    n2: int,
) -> dict:
    """Two-proportion z-test (H1/H2: supported-claim rate)."""
    p1 = successes1 / n1
    p2 = successes2 / n2
    p_pool = (successes1 + successes2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    z = (p1 - p2) / se if se > 0 else 0.0
    p_value = 2 * (1 - stats.norm.cdf(abs(z)))
    # 95% CI for the difference
    se_diff = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    ci_diff = (p1 - p2 - 1.96 * se_diff, p1 - p2 + 1.96 * se_diff)
    return {"z": z, "p_value": p_value, "ci_diff": ci_diff}


def mann_whitney_u(
    scores1: list[float],
    scores2: list[float],
) -> dict:
    """Mann-Whitney U test (H4: Likert scores)."""
    result = stats.mannwhitneyu(
        scores1, scores2, alternative="two-sided"
    )
    # Rank-biserial as effect size: r = 1 - 2U/(n1*n2)
    effect_r = 1 - 2 * result.statistic / (
        len(scores1) * len(scores2)
    )
    return {
        "U": result.statistic,
        "p_value": result.pvalue,
        "effect_size_r": effect_r,
    }


def welch_t(
    sample1: list[float],
    sample2: list[float],
) -> dict:
    """Welch's t-test (H5: coverage residual)."""
    result = stats.ttest_ind(sample1, sample2, equal_var=False)
    mean_diff = np.mean(sample1) - np.mean(sample2)
    se = mean_diff / result.statistic if result.statistic != 0 else 0.0
    ci_diff = (mean_diff - 1.96 * se, mean_diff + 1.96 * se)
    return {
        "t": float(result.statistic),
        "p_value": float(result.pvalue),
        "ci_diff": (float(ci_diff[0]), float(ci_diff[1])),
    }


def spearman_rho(
    x: list[float],
    y: list[float],
) -> dict:
    """Spearman rank correlation (H6)."""
    result = stats.spearmanr(x, y)
    return {
        "rho": float(result.correlation),
        "p_value": float(result.pvalue),
    }


def paired_t(
    sample1: list[float],
    sample2: list[float],
) -> dict:
    """Paired t-test (H3: log verification time)."""
    result = stats.ttest_rel(sample1, sample2)
    diffs = np.array(sample1) - np.array(sample2)
    mean_diff = float(np.mean(diffs))
    se = float(np.std(diffs, ddof=1) / np.sqrt(len(diffs)))
    ci_diff = (mean_diff - 1.96 * se, mean_diff + 1.96 * se)
    return {
        "t": float(result.statistic),
        "p_value": float(result.pvalue),
        "ci_diff": ci_diff,
    }


def holm_bonferroni(
    p_values: list[float],
    alpha: float = 0.05,
) -> list[dict]:
    """Holm-Bonferroni multiple comparison correction.

    Returns one dict per input p-value (in original order) with keys:
      p, adjusted_p, reject.
    """
    n = len(p_values)
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])

    adjusted = [0.0] * n
    cummax = 0.0
    for rank, (orig_idx, p) in enumerate(indexed):
        corrected = p * (n - rank)
        cummax = max(cummax, corrected)
        adjusted[orig_idx] = min(cummax, 1.0)

    return [
        {"p": p_values[i], "adjusted_p": adjusted[i], "reject": adjusted[i] < alpha}
        for i in range(n)
    ]
