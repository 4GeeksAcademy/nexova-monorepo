"""
Evaluación técnica del modelo de pronóstico: validación cruzada temporal,
curva de aprendizaje y diagnóstico de ajuste. Sin I/O: solo funciones puras.

Todo se hace sobre el set de entrenamiento (2016-2023); el test 2024-2025 no
interviene. Los errores se miden en USD (el ingreso reconstruido con
`predict_revenue`), no sobre el cociente interanual que usa el modelo.

Validación cruzada: ventana expansiva (`TimeSeriesSplit`, sin barajar). Cada
pliegue entrena con los meses más antiguos y valida con un bloque posterior.
Como las features son causales (`shift >= 12`), la fila de un mes solo lee
meses anteriores; `check_fold_feature_leakage` lo comprueba recalculando las
features con la serie cortada en cada frontera de pliegue.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from .dataset import DATE_COLUMN, TARGET_COLUMN
from .features import FEATURE_COLUMNS, TARGET, build_features, drop_incomplete
from .model import build_random_forest, fit, predict_revenue, seasonal_naive_with_growth

MIN_FOLDS = 5
Split = tuple[np.ndarray, np.ndarray]

# Umbrales heurísticos del diagnóstico automático (el reporte los contrasta
# con la curva y los matiza). Un Random Forest siempre ajusta mejor sus propios
# datos que los nuevos, así que la brecha por sí sola no basta: se exige además
# que el error de validación no mejore al añadir datos.
UNDERFIT_TRAIN_VS_NAIVE = 0.8
OVERFIT_GAP_RATIO = 2.0
CONVERGENCE_TOLERANCE = 0.05


def temporal_cv_splits(n_rows: int, n_splits: int = MIN_FOLDS, test_size: int = 6) -> list[Split]:
    """Pliegues cronológicos de ventana expansiva; verifica el orden antes de devolverlos."""
    if n_splits < MIN_FOLDS:
        raise ValueError(f"Se piden al menos {MIN_FOLDS} pliegues, no {n_splits}.")
    splitter = TimeSeriesSplit(n_splits=n_splits, test_size=test_size)
    splits = [(fit_idx, val_idx) for fit_idx, val_idx in splitter.split(np.arange(n_rows))]
    assert_chronological(splits)
    return splits


def assert_chronological(splits: list[Split]) -> None:
    """Falla si algún pliegue baraja o mezcla el orden temporal.

    Condiciones: índices crecientes dentro de cada pliegue; todo índice de
    validación posterior a todo índice de entrenamiento; y pliegues sucesivos
    que avanzan en el tiempo (la validación nunca empieza antes que la del
    pliegue anterior, y el entrenamiento nunca termina antes).
    """
    if not splits:
        raise ValueError("No hay pliegues que comprobar.")
    previous: Split | None = None
    for number, (train_idx, val_idx) in enumerate(splits, start=1):
        train_idx, val_idx = np.asarray(train_idx), np.asarray(val_idx)
        if len(train_idx) == 0 or len(val_idx) == 0:
            raise ValueError(f"Pliegue {number}: entrenamiento o validación vacíos.")
        if np.any(np.diff(train_idx) <= 0) or np.any(np.diff(val_idx) <= 0):
            raise ValueError(f"Pliegue {number}: los índices no están en orden cronológico estricto.")
        if train_idx.max() >= val_idx.min():
            raise ValueError(f"Pliegue {number}: la validación no es posterior al entrenamiento.")
        if previous is not None:
            prev_train, prev_val = previous
            if val_idx.min() <= prev_val.min() or train_idx.max() < prev_train.max():
                raise ValueError(f"Pliegue {number}: no avanza respecto al pliegue anterior.")
        previous = (train_idx, val_idx)


def _errors(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    residual = np.asarray(actual, dtype=float) - np.asarray(predicted, dtype=float)
    return {"mae_usd": float(np.mean(np.abs(residual))), "rmse_usd": float(np.sqrt(np.mean(residual**2)))}


def _check_frame(frame: pd.DataFrame) -> None:
    if not frame[DATE_COLUMN].is_monotonic_increasing:
        raise ValueError("El train debe estar ordenado por mes antes de validar.")


def cross_validate_model(frame: pd.DataFrame, params: dict[str, Any], splits: list[Split]) -> dict[str, Any]:
    """MAE y RMSE (USD) de train y validación por pliegue, y su media ± desviación estándar."""
    _check_frame(frame)
    assert_chronological(splits)
    folds: list[dict[str, Any]] = []
    for number, (train_idx, val_idx) in enumerate(splits, start=1):
        fit_rows, val_rows = frame.iloc[train_idx], frame.iloc[val_idx]
        model = fit(build_random_forest(**params), fit_rows)
        train_err = _errors(fit_rows[TARGET_COLUMN], predict_revenue(model, fit_rows))
        val_err = _errors(val_rows[TARGET_COLUMN], predict_revenue(model, val_rows))
        naive_err = _errors(val_rows[TARGET_COLUMN], seasonal_naive_with_growth(val_rows, fit_rows))
        folds.append(
            {
                "fold": number,
                "train_months": [fit_rows[DATE_COLUMN].min().strftime("%Y-%m"), fit_rows[DATE_COLUMN].max().strftime("%Y-%m")],
                "val_months": [val_rows[DATE_COLUMN].min().strftime("%Y-%m"), val_rows[DATE_COLUMN].max().strftime("%Y-%m")],
                "train_rows": int(len(fit_rows)),
                "val_rows": int(len(val_rows)),
                "val_mean_revenue_usd": float(val_rows[TARGET_COLUMN].mean()),
                "train": train_err,
                "validation": val_err,
                "naive_validation": naive_err,
            }
        )

    def summarize(section: str, metric: str) -> dict[str, float]:
        values = np.array([fold[section][metric] for fold in folds])
        return {"mean": float(values.mean()), "std": float(values.std(ddof=1))}

    summary = {
        f"{section}_{metric}": summarize(section, metric)
        for section in ("train", "validation", "naive_validation")
        for metric in ("mae_usd", "rmse_usd")
    }
    return {"folds": folds, "summary": summary}


def learning_curve_usd(
    frame: pd.DataFrame, params: dict[str, Any], splits: list[Split], sizes: list[int]
) -> dict[str, Any]:
    """Curva de aprendizaje en USD con los mismos pliegues temporales.

    En cada pliegue se entrena con los primeros `n` meses de su train y se mide
    el error de esas filas (train) y del bloque de validación del pliegue. Los
    tamaños mayores que el train de un pliegue quedan en NaN para él; la media
    por tamaño usa solo los pliegues disponibles (`n_folds`).
    """
    _check_frame(frame)
    assert_chronological(splits)
    shape = (len(sizes), len(splits))
    metrics = {name: np.full(shape, np.nan) for name in ("train_mae", "train_rmse", "val_mae", "val_rmse")}
    for col, (train_idx, val_idx) in enumerate(splits):
        val_rows = frame.iloc[val_idx]
        for row, size in enumerate(sizes):
            if size > len(train_idx):
                continue
            fit_rows = frame.iloc[train_idx[:size]]
            model = fit(build_random_forest(**params), fit_rows)
            train_err = _errors(fit_rows[TARGET_COLUMN], predict_revenue(model, fit_rows))
            val_err = _errors(val_rows[TARGET_COLUMN], predict_revenue(model, val_rows))
            metrics["train_mae"][row, col] = train_err["mae_usd"]
            metrics["train_rmse"][row, col] = train_err["rmse_usd"]
            metrics["val_mae"][row, col] = val_err["mae_usd"]
            metrics["val_rmse"][row, col] = val_err["rmse_usd"]

    available = ~np.isnan(metrics["val_rmse"])
    n_folds = available.sum(axis=1)
    curve: dict[str, Any] = {"train_sizes": list(sizes), "n_folds": [int(n) for n in n_folds]}
    for name, values in metrics.items():
        curve[name] = {
            "mean": [float(v) for v in np.nanmean(values, axis=1)],
            # con un solo pliegue disponible (el tamaño máximo) no hay dispersión que medir
            "std": [float(np.nanstd(row, ddof=1)) if n > 1 else 0.0 for row, n in zip(values, n_folds)],
        }
    return curve


def check_fold_feature_leakage(series: pd.DataFrame, frame: pd.DataFrame, splits: list[Split]) -> list[dict[str, Any]]:
    """Comprueba que las features de lag/rolling no se asoman al futuro de cada pliegue.

    `series` es la serie mensual cruda del train y `frame` el train con features
    (`drop_incomplete(build_features(...))`). En cada pliegue se recalculan las
    features con la serie cortada (a) en el último mes de entrenamiento y (b) en
    el último mes de validación, y se exige que las filas de entrenamiento y de
    validación sean idénticas a las del `frame` global. Si alguna feature leyera
    meses posteriores a la frontera, los valores cambiarían.
    """
    _check_frame(frame)
    columns = [*FEATURE_COLUMNS, TARGET]
    results: list[dict[str, Any]] = []
    for number, (train_idx, val_idx) in enumerate(splits, start=1):
        for label, idx in (("entrenamiento", train_idx), ("validación", np.concatenate([train_idx, val_idx]))):
            rows = frame.iloc[idx]
            cutoff = rows[DATE_COLUMN].max()
            truncated = drop_incomplete(build_features(series[series[DATE_COLUMN] <= cutoff]))
            truncated = truncated[truncated[DATE_COLUMN].isin(rows[DATE_COLUMN])].reset_index(drop=True)
            same = len(truncated) == len(rows) and np.allclose(
                truncated[columns].to_numpy(dtype=float), rows[columns].to_numpy(dtype=float), equal_nan=False
            )
            if not same:
                raise ValueError(f"Pliegue {number}: las features de {label} cambian al cortar la serie en {cutoff:%Y-%m}: hay fuga.")
        results.append({"fold": number, "leakage": False})
    return results


def diagnose(cv: dict[str, Any], curve: dict[str, Any]) -> dict[str, Any]:
    """Etiqueta orientativa (bien ajustado / underfitting / overfitting) con las cifras que la sustentan.

    - underfitting: ni siquiera en train el modelo mejora con claridad al naive
      estacional con crecimiento (error de train ≥ 0,8 × error de validación del naive).
    - overfitting: brecha validación/train ≥ 2 **y** el error de validación sigue
      sin converger hacia el de train al crecer el set (variación del último tramo
      de la curva menor a la tolerancia, con la brecha aún abierta).
    - bien ajustado: en otro caso.
    """
    summary = cv["summary"]
    train_rmse = summary["train_rmse_usd"]["mean"]
    val_rmse = summary["validation_rmse_usd"]["mean"]
    naive_rmse = summary["naive_validation_rmse_usd"]["mean"]
    gap_ratio = val_rmse / train_rmse

    val_curve = curve["val_rmse"]["mean"]
    last, before = val_curve[-1], val_curve[-3] if len(val_curve) >= 3 else val_curve[0]
    val_slope = (last - before) / before  # < 0: el error de validación aún mejora con más datos
    gap_still_open = curve["val_rmse"]["mean"][-1] > 1.5 * curve["train_rmse"]["mean"][-1]

    if train_rmse >= UNDERFIT_TRAIN_VS_NAIVE * naive_rmse:
        label = "underfitting"
    elif gap_ratio >= OVERFIT_GAP_RATIO and gap_still_open and val_slope > -CONVERGENCE_TOLERANCE:
        label = "overfitting"
    else:
        label = "bien ajustado"
    return {
        "suggested_label": label,
        "gap_ratio_val_over_train_rmse": float(gap_ratio),
        "train_rmse_vs_naive_validation_rmse": float(train_rmse / naive_rmse),
        "validation_rmse_vs_naive_validation_rmse": float(val_rmse / naive_rmse),
        "val_rmse_change_last_two_steps_pct": float(val_slope * 100),
        "gap_still_open_at_full_size": bool(gap_still_open),
        "thresholds": {
            "underfit_train_vs_naive": UNDERFIT_TRAIN_VS_NAIVE,
            "overfit_gap_ratio": OVERFIT_GAP_RATIO,
            "convergence_tolerance": CONVERGENCE_TOLERANCE,
        },
    }
