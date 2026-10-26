"""Confidence intervals for the evaluation. Pure functions, no I/O.

Proportions (accuracy, precision, recall) use the Wilson score interval, which behaves
well for small samples and for rates near 0 or 1. Statistics that are not a simple
proportion (F1, medians, p95, means) use a percentile bootstrap: resample the items with
replacement many times and take the 2.5th and 97.5th percentiles of the statistic."""

import math
import random
from collections.abc import Callable, Sequence

Z95 = 1.959963984540054
DEFAULT_BOOTSTRAPS = 2000
SEED = 20261102  # fixed, so a rerun on the same data gives the same intervals


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """Wilson score interval for a proportion. None when there is nothing to measure (n = 0)."""
    if n <= 0:
        return None
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(successes: int, n: int) -> dict:
    """A proportion with its Wilson interval, ready for the metrics file."""
    ci = wilson(successes, n)
    return {
        "k": successes,
        "n": n,
        "value": successes / n if n else None,
        "ci_low": ci[0] if ci else None,
        "ci_high": ci[1] if ci else None,
        "ci_method": "wilson",
    }


def percentile(values: Sequence[float], p: float) -> float | None:
    """Linear-interpolation percentile (p in 0..100), like numpy's default."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def median(values: Sequence[float]) -> float | None:
    return percentile(values, 50)


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def bootstrap_ci(
    items: Sequence,
    statistic: Callable[[Sequence], float | None],
    n_boot: int = DEFAULT_BOOTSTRAPS,
    seed: int = SEED,
) -> tuple[float, float] | None:
    """Percentile bootstrap interval of `statistic` over resamples of `items`."""
    if not items:
        return None
    rng = random.Random(seed)
    size = len(items)
    stats = []
    for _ in range(n_boot):
        value = statistic([items[rng.randrange(size)] for _ in range(size)])
        if value is not None:
            stats.append(value)
    if not stats:
        return None
    return percentile(stats, 2.5), percentile(stats, 97.5)


def summary_stat(values: Sequence[float], statistic: Callable[[Sequence[float]], float | None], name: str) -> dict:
    """A median / p95 / mean with a bootstrap interval."""
    ci = bootstrap_ci(list(values), statistic)
    return {
        "n": len(values),
        "value": statistic(values) if values else None,
        "ci_low": ci[0] if ci else None,
        "ci_high": ci[1] if ci else None,
        "ci_method": "bootstrap",
        "statistic": name,
    }


def f1_counts(tp: int, fp: int, fn: int) -> float | None:
    denominator = 2 * tp + fp + fn
    return 2 * tp / denominator if denominator else None


def f1(per_item: Sequence[tuple[int, int, int]]) -> dict:
    """F1 over items given as (tp, fp, fn) per item, with a bootstrap interval over items."""
    tp, fp, fn = (sum(x[i] for x in per_item) for i in range(3))

    def stat(sample):
        return f1_counts(sum(x[0] for x in sample), sum(x[1] for x in sample), sum(x[2] for x in sample))

    ci = bootstrap_ci(list(per_item), stat)
    return {
        "n": len(per_item),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "value": f1_counts(tp, fp, fn),
        "ci_low": ci[0] if ci else None,
        "ci_high": ci[1] if ci else None,
        "ci_method": "bootstrap",
    }
