"""
Métricas del pronóstico sobre el conjunto de test.

Las 4 del BRIEFING:

- MSE: en USD² y, para leerlo sin traducir, RMSE como % del ingreso medio.
- PSI: estabilidad de una distribución entre train y test, con tramos por
  cuantiles del train. Se aplica a `avg_contract_value_usd` (aproximación al
  mix de líneas, que el CSV no desglosa) y al target del modelo.
- Gini normalizado: capacidad del modelo para ordenar los meses de más a menos
  ingreso (1 = mismo orden que la realidad).
- K² de D'Agostino-Pearson: normalidad de los residuos (asimetría + curtosis).

Más MAE, MAPE y R² para Finanzas, y la cobertura de la banda de variabilidad.
"""

from __future__ import annotations

import numpy as np
from scipy import stats
from sklearn.metrics import (
    mean_absolute_error,
    mean_absolute_percentage_error,
    mean_squared_error,
    r2_score,
)

PSI_BINS = 5
PSI_EPSILON = 1e-4
PSI_STABLE = 0.10
PSI_SIGNIFICANT = 0.25
K2_ALPHA = 0.05


def _as_array(values: object) -> np.ndarray:
    return np.asarray(values, dtype=float)


def psi(expected: object, actual: object, bins: int = PSI_BINS) -> float:
    """Population Stability Index de `actual` (test) respecto a `expected` (train).

    `PSI = Σ (q_i − p_i) · ln(q_i / p_i)`, con tramos definidos solo por los
    cuantiles de `expected` y tramos vacíos llevados a `PSI_EPSILON`.
    """
    train = _as_array(expected)
    test = _as_array(actual)
    inner_edges = np.unique(np.quantile(train, np.linspace(0, 1, bins + 1)[1:-1]))
    edges = np.concatenate(([-np.inf], inner_edges, [np.inf]))

    p = np.histogram(train, bins=edges)[0] / len(train)
    q = np.histogram(test, bins=edges)[0] / len(test)
    p = np.clip(p, PSI_EPSILON, None)
    q = np.clip(q, PSI_EPSILON, None)
    return float(np.sum((q - p) * np.log(q / p)))


def psi_permutation_p_value(
    expected: object,
    actual: object,
    n_permutations: int = 2000,
    random_state: int = 42,
) -> float:
    """Probabilidad de obtener un PSI igual o mayor si train y test vinieran de la misma distribución.

    Con muestras pequeñas (24 meses de test) el PSI tiene un suelo alto solo por
    ruido de muestreo, así que los umbrales 0,10/0,25 conviene leerlos junto a
    este p-valor: se barajan las etiquetas train/test y se recalcula el PSI.
    """
    train = _as_array(expected)
    test = _as_array(actual)
    observed = psi(train, test)
    pooled = np.concatenate([train, test])
    rng = np.random.default_rng(random_state)
    hits = 0
    for _ in range(n_permutations):
        shuffled = rng.permutation(pooled)
        if psi(shuffled[: len(train)], shuffled[len(train) :]) >= observed:
            hits += 1
    return float((hits + 1) / (n_permutations + 1))


def psi_label(value: float) -> str:
    if value < PSI_STABLE:
        return "estable"
    if value < PSI_SIGNIFICANT:
        return "cambio moderado"
    return "cambio significativo"


def _gini(actual: np.ndarray, ranking: np.ndarray) -> float:
    # Orden descendente por `ranking`; los empates se resuelven por posición
    # para que el resultado sea determinista.
    order = np.lexsort((np.arange(len(ranking)), -ranking))
    cumulative = np.cumsum(actual[order]) / actual.sum()
    n = len(actual)
    return float(cumulative.sum() / n - (n + 1) / (2 * n))


def gini_normalized(actual: object, predicted: object) -> float:
    """Gini del orden de `predicted` dividido por el Gini del orden perfecto."""
    y = _as_array(actual)
    y_hat = _as_array(predicted)
    return _gini(y, y_hat) / _gini(y, y)


def k2_residuals(residuals: object) -> dict[str, float | bool]:
    """Test K² de D'Agostino-Pearson (`scipy.stats.normaltest`) sobre los residuos."""
    values = _as_array(residuals)
    result = stats.normaltest(values)
    return {
        "statistic": float(result.statistic),
        "p_value": float(result.pvalue),
        "n": int(len(values)),
        "skewness": float(stats.skew(values)),
        "excess_kurtosis": float(stats.kurtosis(values)),
        "normal_at_5pct": bool(result.pvalue > K2_ALPHA),
    }


def regression_report(actual: object, predicted: object) -> dict[str, float]:
    """Métricas de error en USD y en % para un conjunto de predicciones."""
    y = _as_array(actual)
    y_hat = _as_array(predicted)
    mse = float(mean_squared_error(y, y_hat))
    mean_revenue = float(y.mean())
    return {
        "mse_usd2": mse,
        "rmse_usd": float(np.sqrt(mse)),
        "rmse_pct_of_mean_revenue": float(np.sqrt(mse) / mean_revenue * 100),
        "mse_over_mean_revenue_squared_pct": float(mse / mean_revenue**2 * 100),
        "mae_usd": float(mean_absolute_error(y, y_hat)),
        "mape_pct": float(mean_absolute_percentage_error(y, y_hat) * 100),
        "r2": float(r2_score(y, y_hat)),
        "gini_normalized": gini_normalized(y, y_hat),
        "mean_revenue_usd": mean_revenue,
    }


def band_coverage(actual: object, lower: object, upper: object) -> float:
    """Porcentaje de meses reales dentro de la banda."""
    y = _as_array(actual)
    inside = (y >= _as_array(lower)) & (y <= _as_array(upper))
    return float(inside.mean() * 100)
