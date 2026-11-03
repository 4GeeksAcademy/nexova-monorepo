"""Features sin fuga de información y métricas del pronóstico con casos conocidos."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")
pytest.importorskip("scipy")

from sales_forecast.features import FEATURE_COLUMNS, TARGET, build_features, drop_incomplete
from sales_forecast.metrics import gini_normalized, k2_residuals, psi, regression_report


def _series(periods: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    months = pd.date_range("2016-01-01", periods=periods, freq="MS")
    return pd.DataFrame(
        {
            "month": months,
            "revenue_usd": 1000.0 + rng.uniform(0, 100, periods).cumsum(),
            "active_contracts": rng.integers(150, 250, periods),
            "avg_contract_value_usd": rng.uniform(3500, 4200, periods),
            "business_line": "consolidated",
        }
    )


@pytest.mark.parametrize("changed_index", [30, 60, 100])
def test_features_only_use_information_at_least_12_months_old(changed_index: int):
    base = _series()
    changed = base.copy()
    changed.loc[changed_index, ["revenue_usd", "active_contracts", "avg_contract_value_usd"]] *= 3

    before = build_features(base)[FEATURE_COLUMNS]
    after = build_features(changed)[FEATURE_COLUMNS]

    unaffected = slice(0, changed_index + 12)
    pd.testing.assert_frame_equal(before.iloc[unaffected], after.iloc[unaffected])
    assert not before.iloc[changed_index + 12].equals(after.iloc[changed_index + 12])


def test_train_features_ignore_the_test_years():
    base = _series()
    changed = base.copy()
    changed.loc[96:, "revenue_usd"] = 1.0

    before = build_features(base).iloc[:96][[*FEATURE_COLUMNS, TARGET]]
    after = build_features(changed).iloc[:96][[*FEATURE_COLUMNS, TARGET]]

    pd.testing.assert_frame_equal(before, after)


def test_incomplete_rows_are_dropped_not_imputed():
    complete = drop_incomplete(build_features(_series()))

    assert complete["month"].min() == pd.Timestamp("2018-01-01")
    assert len(complete) == 120 - 24
    assert not complete[[*FEATURE_COLUMNS, TARGET]].isna().any().any()


def test_target_is_year_over_year_ratio():
    features = build_features(_series())
    row = features.iloc[50]

    assert row[TARGET] == pytest.approx(row["revenue_usd"] / row["lag_12"])


def test_psi_is_zero_for_identical_distributions_and_large_for_a_shift():
    rng = np.random.default_rng(42)
    train = rng.normal(0, 1, 500)

    assert psi(train, train) == pytest.approx(0.0)
    assert psi(train, train + 5) > 0.25


def test_gini_normalized_bounds():
    actual = np.array([5.0, 1.0, 3.0, 8.0, 2.0])

    assert gini_normalized(actual, actual) == pytest.approx(1.0)
    assert gini_normalized(actual, -actual) == pytest.approx(-1.0)
    assert gini_normalized(actual, actual * 10 + 3) == pytest.approx(1.0)


def test_regression_report_known_values():
    report = regression_report([100.0, 200.0], [110.0, 190.0])

    assert report["mse_usd2"] == pytest.approx(100.0)
    assert report["mae_usd"] == pytest.approx(10.0)
    assert report["rmse_pct_of_mean_revenue"] == pytest.approx(10 / 150 * 100)
    assert report["mape_pct"] == pytest.approx((0.10 + 0.05) / 2 * 100)


def test_k2_accepts_normal_residuals_and_rejects_heavy_skew():
    rng = np.random.default_rng(42)

    assert k2_residuals(rng.normal(0, 1, 200))["normal_at_5pct"] is True
    assert k2_residuals(rng.exponential(1, 200))["normal_at_5pct"] is False
