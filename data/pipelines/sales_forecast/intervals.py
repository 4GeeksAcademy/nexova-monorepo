"""
Banda de variabilidad de la predicción.

Se calibra con los residuos fuera de muestra del CV temporal (solo train), en
escala de cociente interanual (`r − r̂`), y se traslada a USD multiplicando por
`lag_12`, que se conoce a 12 meses vista. El test K² de esos residuos decide
la forma:

- p > 0,05 (compatibles con la normal): `r̂ ± 1,96 · σ`, con σ = raíz del
  error cuadrático medio (incluye un posible sesgo);
- p ≤ 0,05: cuantiles empíricos 2,5 % y 97,5 % de los residuos.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

from .metrics import K2_ALPHA, k2_residuals

COVERAGE = 0.95


@dataclass(frozen=True)
class Band:
    method: str
    low_offset: float
    high_offset: float
    k2: dict[str, float | bool]

    def apply(self, predicted_ratio: np.ndarray, base: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ratio = np.asarray(predicted_ratio, dtype=float)
        base = np.asarray(base, dtype=float)
        return (ratio + self.low_offset) * base, (ratio + self.high_offset) * base


def calibrate_band(oof_ratio_residuals: np.ndarray, coverage: float = COVERAGE) -> Band:
    residuals = np.asarray(oof_ratio_residuals, dtype=float)
    k2 = k2_residuals(residuals)
    tail = (1 - coverage) / 2

    if k2["p_value"] > K2_ALPHA:
        sigma = float(np.sqrt(np.mean(residuals**2)))
        z = float(stats.norm.ppf(1 - tail))
        return Band("normal (±z·σ)", -z * sigma, z * sigma, k2)

    low, high = np.quantile(residuals, [tail, 1 - tail])
    return Band("cuantiles empíricos", float(low), float(high), k2)
