"""
Carga, validación y split temporal del histórico mensual de ventas.

Solo depende de pandas para que el test del split no arrastre scikit-learn,
xgboost ni matplotlib.

Restricciones del BRIEFING que se validan (si alguna falla, se lanza
`ValueError` en vez de "arreglar" los datos: el ticket prohíbe simularlos):

- columnas exactas: month, revenue_usd, active_contracts,
  avg_contract_value_usd, business_line;
- sin nulos;
- `revenue_usd` > 0;
- sin meses faltantes ni repetidos en la fila `consolidated`.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

RAW_CSV_PATH = Path(__file__).resolve().parents[2] / "raw" / "nexova_sales.csv"

DATE_COLUMN = "month"
TARGET_COLUMN = "revenue_usd"
BUSINESS_LINE = "consolidated"
EXPECTED_COLUMNS = [
    "month",
    "revenue_usd",
    "active_contracts",
    "avg_contract_value_usd",
    "business_line",
]
NUMERIC_COLUMNS = ["revenue_usd", "active_contracts", "avg_contract_value_usd"]

TRAIN_YEARS = 8
TEST_YEARS = 2


def validate_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Valida el contrato del BRIEFING y devuelve la serie `consolidated` ordenada."""
    columns = list(df.columns)
    if sorted(columns) != sorted(EXPECTED_COLUMNS):
        missing = sorted(set(EXPECTED_COLUMNS) - set(columns))
        extra = sorted(set(columns) - set(EXPECTED_COLUMNS))
        raise ValueError(f"Columnas inesperadas: faltan {missing}, sobran {extra}.")

    nulls = df[EXPECTED_COLUMNS].isna().sum()
    if nulls.any():
        raise ValueError(f"Hay nulos en el dataset: {nulls[nulls > 0].to_dict()}.")

    out = df.loc[df["business_line"] == BUSINESS_LINE, EXPECTED_COLUMNS].copy()
    if out.empty:
        raise ValueError(f"No hay filas con business_line = '{BUSINESS_LINE}'.")

    out[DATE_COLUMN] = pd.to_datetime(out[DATE_COLUMN], format="%Y-%m-%d")
    for column in NUMERIC_COLUMNS:
        out[column] = pd.to_numeric(out[column], errors="raise")

    if (out[DATE_COLUMN].dt.day != 1).any():
        raise ValueError("`month` debe ser siempre el primer día del mes.")
    if (out[TARGET_COLUMN] <= 0).any():
        raise ValueError("`revenue_usd` debe ser positivo en todos los meses.")
    if out[DATE_COLUMN].duplicated().any():
        raise ValueError("Hay meses repetidos en la fila consolidated.")

    out = out.sort_values(DATE_COLUMN).reset_index(drop=True)
    expected = pd.date_range(out[DATE_COLUMN].min(), out[DATE_COLUMN].max(), freq="MS")
    if len(expected) != len(out):
        missing = expected.difference(out[DATE_COLUMN])
        raise ValueError(f"Faltan meses: {[m.strftime('%Y-%m') for m in missing]}.")
    return out


def load_sales(path: Path | str = RAW_CSV_PATH) -> pd.DataFrame:
    """Lee el CSV provisto, sin modificarlo, y lo valida."""
    return validate_schema(pd.read_csv(path))


def split_train_test(
    df: pd.DataFrame,
    train_years: int = TRAIN_YEARS,
    test_years: int = TEST_YEARS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Primeros `train_years` años para train y los `test_years` siguientes para test.

    El corte se calcula desde la primera fecha (`min + train_years`), no se
    escribe a mano, y no depende del orden de las filas. Exige que la serie
    cubra exactamente `train_years + test_years` años de meses completos.
    """
    data = df.sort_values(DATE_COLUMN).reset_index(drop=True)
    start = data[DATE_COLUMN].min()
    cutoff = start + pd.DateOffset(years=train_years)
    expected_months = 12 * (train_years + test_years)
    if len(data) != expected_months:
        raise ValueError(
            f"Se esperaban {expected_months} meses ({train_years}+{test_years} años) "
            f"y hay {len(data)}."
        )

    is_train = data[DATE_COLUMN] < cutoff
    train = data.loc[is_train].reset_index(drop=True)
    test = data.loc[~is_train].reset_index(drop=True)
    return train, test
