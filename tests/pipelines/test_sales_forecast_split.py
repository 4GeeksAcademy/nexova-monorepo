"""Split temporal del pronóstico de ventas: 8 años de train, 2 de test, sin solapamiento."""

from __future__ import annotations

import pandas as pd
import pytest

from sales_forecast.dataset import RAW_CSV_PATH, load_sales, split_train_test, validate_schema


def _monthly(start: str, periods: int) -> pd.DataFrame:
    months = pd.date_range(start, periods=periods, freq="MS")
    return pd.DataFrame(
        {
            "month": months,
            "revenue_usd": [1000.0 + i for i in range(periods)],
            "active_contracts": 1,
            "avg_contract_value_usd": 1.0,
            "business_line": "consolidated",
        }
    )


def test_split_is_first_8_years_train_last_2_test():
    train, test = split_train_test(_monthly("2016-01-01", 120), train_years=8)

    assert len(train) == 96
    assert len(test) == 24
    assert train["month"].min() == pd.Timestamp("2016-01-01")
    assert train["month"].max() == pd.Timestamp("2023-12-01")
    assert test["month"].min() == pd.Timestamp("2024-01-01")
    assert test["month"].max() == pd.Timestamp("2025-12-01")


def test_no_date_overlap_and_train_strictly_before_test():
    train, test = split_train_test(_monthly("2016-01-01", 120))

    assert set(train["month"]).isdisjoint(test["month"])
    assert train["month"].max() < test["month"].min()
    assert len(train) + len(test) == 120


def test_split_does_not_depend_on_row_order():
    shuffled = _monthly("2016-01-01", 120).sample(frac=1, random_state=42)

    train, test = split_train_test(shuffled)

    assert train["month"].is_monotonic_increasing
    assert test["month"].min() == pd.Timestamp("2024-01-01")
    assert len(test) == 24


def test_cutoff_is_derived_from_the_first_month():
    train, test = split_train_test(_monthly("2010-07-01", 120))

    assert train["month"].max() == pd.Timestamp("2018-06-01")
    assert test["month"].min() == pd.Timestamp("2018-07-01")


@pytest.mark.parametrize("periods", [100, 132])
def test_rejects_series_that_are_not_exactly_10_years(periods: int):
    with pytest.raises(ValueError, match="Se esperaban 120 meses"):
        split_train_test(_monthly("2016-01-01", periods))


def test_validate_schema_rejects_missing_month():
    df = _monthly("2016-01-01", 120).drop(index=40)

    with pytest.raises(ValueError, match="Faltan meses"):
        validate_schema(df)


def test_validate_schema_rejects_non_positive_revenue():
    df = _monthly("2016-01-01", 120)
    df.loc[5, "revenue_usd"] = 0.0

    with pytest.raises(ValueError, match="positivo"):
        validate_schema(df)


def test_validate_schema_rejects_unexpected_columns():
    df = _monthly("2016-01-01", 120).rename(columns={"revenue_usd": "revenue"})

    with pytest.raises(ValueError, match="Columnas inesperadas"):
        validate_schema(df)


def test_real_csv_split_matches_the_briefing():
    train, test = split_train_test(load_sales(RAW_CSV_PATH))

    assert (len(train), len(test)) == (96, 24)
    assert set(train["month"]).isdisjoint(test["month"])
    assert train["month"].min() == pd.Timestamp("2016-01-01")
    assert test["month"].max() == pd.Timestamp("2025-12-01")
