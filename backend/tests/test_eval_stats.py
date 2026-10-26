import pytest

from eval import stats


def test_wilson_matches_known_values():
    low, high = stats.wilson(5, 10)
    assert (round(low, 4), round(high, 4)) == (0.2366, 0.7634)
    low, high = stats.wilson(50, 100)
    assert (round(low, 4), round(high, 4)) == (0.4038, 0.5962)


def test_wilson_handles_the_extremes_without_leaving_zero_to_one():
    assert stats.wilson(0, 10) == (0.0, pytest.approx(0.2775, abs=1e-3))
    low, high = stats.wilson(10, 10)
    assert low == pytest.approx(0.7225, abs=1e-3) and high == pytest.approx(1.0)


def test_wilson_with_nothing_measured_is_none():
    assert stats.wilson(0, 0) is None
    assert stats.rate(0, 0)["value"] is None and stats.rate(0, 0)["ci_low"] is None


def test_the_interval_narrows_as_the_sample_grows_and_always_contains_the_estimate():
    small = stats.wilson(8, 10)
    large = stats.wilson(80, 100)
    assert (large[1] - large[0]) < (small[1] - small[0])
    for k, n in [(0, 5), (1, 5), (3, 7), (50, 50), (13, 60)]:
        low, high = stats.wilson(k, n)
        assert low <= k / n <= high


def test_rate_packages_counts_value_and_interval():
    r = stats.rate(3, 4)
    assert (r["k"], r["n"], r["value"], r["ci_method"]) == (3, 4, 0.75, "wilson")
    assert r["ci_low"] < 0.75 < r["ci_high"]


def test_percentiles_interpolate_like_numpy():
    assert stats.percentile([1, 2, 3, 4], 50) == 2.5
    assert stats.percentile([10], 95) == 10
    assert stats.percentile(list(range(101)), 95) == 95
    assert stats.percentile([], 50) is None
    assert stats.median([5, 1, 3]) == 3 and stats.mean([1, 2, 3, 4]) == 2.5 and stats.mean([]) is None


def test_bootstrap_is_deterministic_and_brackets_the_statistic():
    data = [3.0, 5.0, 4.0, 8.0, 6.0, 7.0, 5.0, 4.0, 9.0, 6.0]
    a = stats.bootstrap_ci(data, stats.mean)
    b = stats.bootstrap_ci(data, stats.mean)
    assert a == b, "a fixed seed gives the same interval on every run"
    assert a[0] < stats.mean(data) < a[1]
    assert stats.bootstrap_ci([], stats.mean) is None


def test_bootstrap_of_identical_values_collapses_to_a_point():
    assert stats.bootstrap_ci([2.0] * 8, stats.mean) == (2.0, 2.0)


def test_f1_from_counts():
    assert stats.f1_counts(3, 1, 1) == 0.75
    assert stats.f1_counts(0, 0, 0) is None
    assert stats.f1_counts(0, 2, 2) == 0.0


def test_f1_reports_counts_and_an_interval_over_items():
    per_item = [(1, 0, 0), (1, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0), (0, 0, 0)]
    r = stats.f1(per_item)
    assert (r["tp"], r["fp"], r["fn"], r["n"]) == (3, 1, 1, 7) and r["value"] == 0.75
    assert 0 < r["ci_low"] <= 0.75 <= r["ci_high"] <= 1
    assert stats.f1([(0, 0, 0)] * 3)["value"] is None


def test_summary_stat_names_the_statistic():
    s = stats.summary_stat([100, 200, 300, 400], stats.median, "median")
    assert (s["statistic"], s["value"], s["n"], s["ci_method"]) == ("median", 250, 4, "bootstrap")
