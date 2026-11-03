"""
Features causales para pronosticar el ingreso a 12 meses vista.

Regla anti-fuga: para el mes `t` solo se usa información de `t-12` o anterior
(`shift(k)` con `k >= 12`; nunca `center=True`, `bfill` ni estadísticas de
toda la serie). Consecuencias:

- Las features de los meses de train (hasta 2023-12) solo leen meses de train
  (hasta 2022-12): el test no influye en nada de lo que el modelo ve al ajustar.
- Las features de los meses de test leen valores reales de 2023-2024, que a
  12 meses vista ya son pasado conocido. El modelo no se reentrena con ellos.

Target: cociente interanual `r_t = y_t / y_{t-12}`. Es estacionario (el test
está por encima del rango de ingresos del train y los árboles no extrapolan);
el ingreso se reconstruye como `r̂_t · y_{t-12}`.

No se escala nada: los árboles son invariantes a transformaciones monótonas
de las features, y la normalización que importa es la del target en cociente.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .dataset import DATE_COLUMN, TARGET_COLUMN

HORIZON = 12
TARGET = "yoy_ratio"
BASE_COLUMN = "lag_12"

FEATURE_COLUMNS = [
    "month_of_year",
    "quarter",
    "is_august",
    "is_jan_feb",
    "lag_12",
    "lag_13",
    "lag_24",
    "yoy_ratio_lag12",
    "roll_mean_12_lag12",
    "roll_std_12_lag12",
    "seasonal_index_lag12",
    "contracts_lag12",
    "acv_lag12",
]


def build_features(series: pd.DataFrame) -> pd.DataFrame:
    """Añade features y target a la serie mensual completa (train + test).

    Devuelve todas las filas, incluidas las primeras con NaN en los lags: quien
    entrena decide descartarlas (`drop_incomplete`), nunca se imputan.
    """
    df = series.sort_values(DATE_COLUMN).reset_index(drop=True)
    y = df[TARGET_COLUMN]
    month = df[DATE_COLUMN].dt.month

    out = pd.DataFrame({DATE_COLUMN: df[DATE_COLUMN], TARGET_COLUMN: y})
    out["month_of_year"] = month
    out["quarter"] = df[DATE_COLUMN].dt.quarter
    out["is_august"] = (month == 8).astype(int)
    out["is_jan_feb"] = month.isin([1, 2]).astype(int)

    out["lag_12"] = y.shift(HORIZON)
    out["lag_13"] = y.shift(HORIZON + 1)
    out["lag_24"] = y.shift(2 * HORIZON)
    out["yoy_ratio_lag12"] = out["lag_12"] / out["lag_24"]

    past = y.shift(HORIZON)
    out["roll_mean_12_lag12"] = past.rolling(12).mean()
    out["roll_std_12_lag12"] = past.rolling(12).std()
    out["seasonal_index_lag12"] = out["lag_12"] / out["roll_mean_12_lag12"]

    out["contracts_lag12"] = df["active_contracts"].shift(HORIZON)
    out["acv_lag12"] = df["avg_contract_value_usd"].shift(HORIZON)

    out[TARGET] = y / out["lag_12"]
    return out


def drop_incomplete(features: pd.DataFrame) -> pd.DataFrame:
    """Quita las filas sin historia suficiente (los primeros 24 meses)."""
    return features.dropna(subset=[*FEATURE_COLUMNS, TARGET]).reset_index(drop=True)


def ratio_to_revenue(ratio: np.ndarray | pd.Series, base: np.ndarray | pd.Series) -> np.ndarray:
    """Reconstruye el ingreso a partir del cociente interanual y de `lag_12`."""
    return np.asarray(ratio, dtype=float) * np.asarray(base, dtype=float)
