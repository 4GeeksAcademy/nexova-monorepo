"""
Modelos del pronóstico: baselines, Random Forest (elegido) y XGBoost (referencia).

Todo se ajusta solo con filas de train. Los hiperparámetros del Random Forest
se eligen con `TimeSeriesSplit` (cada pliegue valida un año natural posterior
a los meses con los que entrena), minimizando el MAE del ingreso reconstruido,
y los residuos de esos pliegues (fuera de muestra) alimentan la banda de
variabilidad.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import TimeSeriesSplit
from xgboost import XGBRegressor

from .features import BASE_COLUMN, FEATURE_COLUMNS, TARGET, ratio_to_revenue
from .dataset import TARGET_COLUMN

RANDOM_STATE = 42
CV_SPLITS = 4
# Cada pliegue valida un año natural completo (2020, 2021, 2022 y 2023).
CV_VALIDATION_MONTHS = 12
RF_PARAM_GRID: dict[str, list[Any]] = {
    "n_estimators": [300, 500],
    "max_depth": [3, 5, None],
    "min_samples_leaf": [2, 4, 6],
}
XGB_PARAMS: dict[str, Any] = {
    "n_estimators": 300,
    "max_depth": 3,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 2,
}


@dataclass
class TuningResult:
    best_params: dict[str, Any]
    best_cv_mae_usd: float
    oof_ratio_residuals: np.ndarray
    oof_months: list[pd.Timestamp]
    grid: list[dict[str, Any]] = field(default_factory=list)


def build_random_forest(**params: Any) -> RandomForestRegressor:
    return RandomForestRegressor(random_state=RANDOM_STATE, n_jobs=-1, **params)


def build_xgboost() -> XGBRegressor:
    return XGBRegressor(random_state=RANDOM_STATE, n_jobs=1, **XGB_PARAMS)


def predict_revenue(model: Any, frame: pd.DataFrame) -> np.ndarray:
    """Predice el cociente interanual y lo convierte a USD con `lag_12`."""
    ratio = model.predict(frame[FEATURE_COLUMNS])
    return ratio_to_revenue(ratio, frame[BASE_COLUMN])


def seasonal_naive(frame: pd.DataFrame) -> np.ndarray:
    """"Repetir el año pasado": `ŷ_t = y_{t-12}`."""
    return frame[BASE_COLUMN].to_numpy(dtype=float)


def seasonal_naive_with_growth(frame: pd.DataFrame, train: pd.DataFrame) -> np.ndarray:
    """Año pasado × crecimiento interanual medio del train."""
    return ratio_to_revenue(train[TARGET].mean(), frame[BASE_COLUMN])


def tune_random_forest(train: pd.DataFrame, n_splits: int = CV_SPLITS) -> TuningResult:
    """Grid pequeño con CV temporal; devuelve los mejores parámetros y sus residuos OOF."""
    splitter = TimeSeriesSplit(n_splits=n_splits, test_size=CV_VALIDATION_MONTHS)
    X = train[FEATURE_COLUMNS]
    y_ratio = train[TARGET]
    keys = list(RF_PARAM_GRID)

    grid: list[dict[str, Any]] = []
    best: tuple[float, dict[str, Any], np.ndarray, list[pd.Timestamp]] | None = None
    for values in product(*RF_PARAM_GRID.values()):
        params = dict(zip(keys, values))
        maes: list[float] = []
        residuals: list[np.ndarray] = []
        months: list[pd.Timestamp] = []
        for fit_idx, val_idx in splitter.split(X):
            model = build_random_forest(**params).fit(X.iloc[fit_idx], y_ratio.iloc[fit_idx])
            fold = train.iloc[val_idx]
            predicted_ratio = model.predict(fold[FEATURE_COLUMNS])
            revenue_hat = ratio_to_revenue(predicted_ratio, fold[BASE_COLUMN])
            maes.append(mean_absolute_error(fold[TARGET_COLUMN], revenue_hat))
            residuals.append(fold[TARGET].to_numpy() - predicted_ratio)
            months.extend(fold["month"].tolist())

        # Redondeo a céntimos: con n_jobs=-1 dos configuraciones equivalentes
        # (p. ej. max_depth=5 y None con hojas grandes) empatan salvo ruido en
        # la última cifra; así gana siempre la primera del grid, la más simple.
        cv_mae = round(float(np.mean(maes)), 2)
        grid.append({**params, "cv_mae_usd": cv_mae})
        if best is None or cv_mae < best[0]:
            best = (cv_mae, params, np.concatenate(residuals), months)

    assert best is not None
    return TuningResult(
        best_params=best[1],
        best_cv_mae_usd=best[0],
        oof_ratio_residuals=best[2],
        oof_months=best[3],
        grid=grid,
    )


def fit(model: Any, train: pd.DataFrame) -> Any:
    return model.fit(train[FEATURE_COLUMNS], train[TARGET])


def tree_spread(model: RandomForestRegressor, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Percentiles 2,5 y 97,5 de las predicciones de cada árbol, en USD (diagnóstico)."""
    X = frame[FEATURE_COLUMNS].to_numpy()
    per_tree = np.stack([tree.predict(X) for tree in model.estimators_])
    base = frame[BASE_COLUMN].to_numpy(dtype=float)
    low, high = np.percentile(per_tree, [2.5, 97.5], axis=0)
    return low * base, high * base


def feature_importance(model: Any, train: pd.DataFrame) -> list[dict[str, float | str]]:
    """Importancia por permutación sobre el train (cuánto empeora el MAE al barajar cada feature)."""
    result = permutation_importance(
        model,
        train[FEATURE_COLUMNS],
        train[TARGET],
        n_repeats=20,
        random_state=RANDOM_STATE,
        scoring="neg_mean_absolute_error",
    )
    rows = [
        {"feature": name, "importance": float(mean), "std": float(std)}
        for name, mean, std in zip(FEATURE_COLUMNS, result.importances_mean, result.importances_std)
    ]
    return sorted(rows, key=lambda row: row["importance"], reverse=True)
