"""Validación cruzada temporal del pronóstico: orden cronológico en cada pliegue y sin fuga de lags."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")

from sales_forecast.evaluation import (  # noqa: E402
    MIN_FOLDS,
    assert_chronological,
    check_fold_feature_leakage,
    temporal_cv_splits,
)
from sales_forecast.features import build_features, drop_incomplete  # noqa: E402


def _train_series(periods: int = 96) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "month": pd.date_range("2016-01-01", periods=periods, freq="MS"),
            "revenue_usd": 100_000 + np.arange(periods) * 500 + rng.normal(0, 3_000, periods),
            "active_contracts": rng.integers(40, 60, periods),
            "avg_contract_value_usd": rng.normal(2_000, 100, periods),
            "business_line": "consolidated",
        }
    )


def _train_frame(series: pd.DataFrame) -> pd.DataFrame:
    return drop_incomplete(build_features(series))


def test_every_fold_validates_strictly_after_it_trains():
    frame = _train_frame(_train_series())
    splits = temporal_cv_splits(len(frame))

    assert len(splits) >= MIN_FOLDS
    for train_idx, val_idx in splits:
        # ningún índice de validación aparece antes (ni dentro) del entrenamiento
        assert train_idx.max() < val_idx.min()
        assert set(train_idx).isdisjoint(val_idx)
        # y lo mismo medido en fechas reales, no solo en posiciones
        assert frame["month"].iloc[train_idx].max() < frame["month"].iloc[val_idx].min()


def test_later_folds_never_start_before_earlier_folds():
    splits = temporal_cv_splits(72)

    for (_, prev_val), (_, next_val) in zip(splits, splits[1:]):
        assert next_val.min() > prev_val.min()
        assert next_val.max() > prev_val.max()
    for (prev_train, _), (next_train, _) in zip(splits, splits[1:]):
        assert next_train.max() >= prev_train.max()  # ventana expansiva


def test_indices_inside_each_fold_keep_chronological_order():
    for train_idx, val_idx in temporal_cv_splits(72):
        assert np.all(np.diff(train_idx) == 1)
        assert np.all(np.diff(val_idx) == 1)


def test_validation_blocks_do_not_overlap_each_other():
    seen: set[int] = set()
    for _, val_idx in temporal_cv_splits(72):
        assert seen.isdisjoint(val_idx)
        seen.update(val_idx.tolist())


def test_shuffled_folds_are_rejected():
    rng = np.random.default_rng(42)
    shuffled = [(rng.permutation(np.arange(0, 40)), np.arange(40, 46))]
    with pytest.raises(ValueError, match="orden cronológico"):
        assert_chronological(shuffled)


def test_validation_before_training_is_rejected():
    with pytest.raises(ValueError, match="no es posterior"):
        assert_chronological([(np.arange(10, 40), np.arange(0, 6))])


def test_fewer_than_five_folds_is_rejected():
    with pytest.raises(ValueError, match="al menos"):
        temporal_cv_splits(72, n_splits=3)


def test_lag_and_rolling_features_do_not_leak_across_fold_boundaries():
    series = _train_series()
    frame = _train_frame(series)
    splits = temporal_cv_splits(len(frame))

    results = check_fold_feature_leakage(series, frame, splits)

    assert [r["fold"] for r in results] == list(range(1, len(splits) + 1))
    assert not any(r["leakage"] for r in results)


def test_leakage_check_detects_features_that_peek_into_the_future():
    series = _train_series()
    frame = _train_frame(series)
    splits = temporal_cv_splits(len(frame))
    leaky = frame.copy()
    # una feature calculada sobre la serie completa (media global) asoma al futuro de cada pliegue
    leaky["roll_mean_12_lag12"] = leaky["roll_mean_12_lag12"].mean()

    with pytest.raises(ValueError, match="fuga"):
        check_fold_feature_leakage(series, leaky, splits)
