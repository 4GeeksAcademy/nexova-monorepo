"""
Entrena el pronóstico mensual de ingresos de Nexova y lo evalúa en los 2 años de test.

    uv run --project services/api --group ml python scripts/train_sales_forecast.py

Pasos: carga y valida `data/raw/nexova_sales.csv` → split 8/2 años → features
causales a 12 meses vista → Random Forest con CV temporal (solo train) →
banda calibrada con residuos fuera de muestra → métricas y gráficos del test.

Salidas en `data/eval/sales_forecast/` (o `--out`): `metrics.json`,
`test_predictions.csv`, `forecast_test.png` y `residuals_qq.png`. Es
determinista (`random_state=42`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "data" / "pipelines"))

from sales_forecast import model as forecast_model  # noqa: E402
from sales_forecast.dataset import RAW_CSV_PATH, load_sales, split_train_test  # noqa: E402
from sales_forecast.features import (  # noqa: E402
    BASE_COLUMN,
    FEATURE_COLUMNS,
    TARGET,
    build_features,
    drop_incomplete,
)
from sales_forecast.intervals import calibrate_band  # noqa: E402
from sales_forecast.metrics import (  # noqa: E402
    band_coverage,
    k2_residuals,
    psi,
    psi_label,
    psi_permutation_p_value,
    regression_report,
)
from sales_forecast.plot import plot_forecast, plot_residuals  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "data" / "eval" / "sales_forecast"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=RAW_CSV_PATH, help="CSV de ventas mensuales")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="carpeta de salida")
    return parser.parse_args()


def _period(frame: pd.DataFrame) -> dict[str, str | int]:
    return {
        "start": frame["month"].min().strftime("%Y-%m-%d"),
        "end": frame["month"].max().strftime("%Y-%m-%d"),
        "rows": int(len(frame)),
    }


def _rounded(value: object) -> object:
    """Redondea los floats del reporte: con `n_jobs=-1` el orden de las sumas
    cambia en la última cifra decimal entre ejecuciones y el JSON dejaría de ser
    idéntico."""
    if isinstance(value, float):
        return round(float(f"{value:.10g}"), 8) + 0.0  # + 0.0 evita "-0.0"
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    return value


def run(data_path: Path, out_dir: Path) -> dict:
    series = load_sales(data_path)
    train_raw, test_raw = split_train_test(series)

    features = build_features(series)
    train = drop_incomplete(features[features["month"].isin(train_raw["month"])])
    test = features[features["month"].isin(test_raw["month"])].reset_index(drop=True)
    if test[[*FEATURE_COLUMNS, TARGET]].isna().any().any():
        raise RuntimeError("El test tiene features incompletas: falta historia previa.")

    tuning = forecast_model.tune_random_forest(train)
    rf = forecast_model.fit(forecast_model.build_random_forest(**tuning.best_params), train)
    xgb = forecast_model.fit(forecast_model.build_xgboost(), train)

    y_test = test["revenue_usd"].to_numpy(dtype=float)
    ratio_hat = rf.predict(test[FEATURE_COLUMNS])
    rf_pred = ratio_hat * test[BASE_COLUMN].to_numpy(dtype=float)
    xgb_pred = forecast_model.predict_revenue(xgb, test)
    naive = forecast_model.seasonal_naive(test)
    naive_growth = forecast_model.seasonal_naive_with_growth(test, train)

    band = calibrate_band(tuning.oof_ratio_residuals)
    low, high = band.apply(ratio_hat, test[BASE_COLUMN])
    spread_low, spread_high = forecast_model.tree_spread(rf, test)

    residuals_usd = y_test - rf_pred
    psi_acv = psi(train_raw["avg_contract_value_usd"], test_raw["avg_contract_value_usd"])
    psi_target = psi(train[TARGET], test[TARGET])

    metrics = {
        "split": {"train": _period(train_raw), "test": _period(test_raw), "train_rows_with_features": _period(train)},
        "model": {
            "algorithm": "RandomForestRegressor",
            "random_state": forecast_model.RANDOM_STATE,
            "target": "yoy_ratio = revenue_t / revenue_{t-12}",
            "horizon_months": 12,
            "best_params": tuning.best_params,
            "cv_mae_usd": tuning.best_cv_mae_usd,
            "cv_splits": forecast_model.CV_SPLITS,
            "features": FEATURE_COLUMNS,
        },
        "test_metrics": {
            "random_forest": regression_report(y_test, rf_pred),
            "xgboost_reference": regression_report(y_test, xgb_pred),
            "seasonal_naive": regression_report(y_test, naive),
            "seasonal_naive_with_growth": regression_report(y_test, naive_growth),
        },
        "psi": {
            "avg_contract_value_usd": {
                "value": psi_acv,
                "label": psi_label(psi_acv),
                "permutation_p_value": psi_permutation_p_value(
                    train_raw["avg_contract_value_usd"], test_raw["avg_contract_value_usd"]
                ),
            },
            "target_yoy_ratio": {
                "value": psi_target,
                "label": psi_label(psi_target),
                "permutation_p_value": psi_permutation_p_value(train[TARGET], test[TARGET]),
            },
            "note": "El CSV solo trae la fila consolidated: avg_contract_value_usd se usa como aproximación al mix de líneas.",
        },
        "k2": {
            "test_residuals_usd": k2_residuals(residuals_usd),
            "cv_out_of_fold_residuals_ratio": band.k2,
        },
        "band": {
            "method": band.method,
            "nominal_coverage_pct": 95.0,
            "test_coverage_pct": band_coverage(y_test, low, high),
            "ratio_offsets": [band.low_offset, band.high_offset],
            "mean_width_pct_of_prediction": float(np.mean((high - low) / rf_pred) * 100),
            "tree_spread_test_coverage_pct": band_coverage(y_test, spread_low, spread_high),
        },
        "feature_importance": forecast_model.feature_importance(rf, train),
    }

    metrics = _rounded(metrics)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pd.DataFrame(
        {
            "month": test["month"].dt.strftime("%Y-%m-%d"),
            "revenue_usd": y_test,
            "predicted_usd": rf_pred.round(2),
            "band_low_usd": low.round(2),
            "band_high_usd": high.round(2),
            "seasonal_naive_usd": naive.round(2),
            "xgboost_usd": xgb_pred.round(2),
            "residual_usd": residuals_usd.round(2),
        }
    ).to_csv(out_dir / "test_predictions.csv", index=False)

    history = train_raw[train_raw["month"] >= test_raw["month"].min() - pd.DateOffset(years=2)]
    plot_forecast(history, test, rf_pred, low, high, naive, band.method, out_dir / "forecast_test.png")
    plot_residuals(residuals_usd, tuning.oof_ratio_residuals, out_dir / "residuals_qq.png")
    return metrics


def _print_summary(metrics: dict) -> None:
    rows = metrics["test_metrics"]
    print(f"Mejores hiperparámetros: {metrics['model']['best_params']} (MAE CV {metrics['model']['cv_mae_usd']:,.0f} USD)")
    print(f"{'modelo':<28}{'MSE (USD²)':>18}{'RMSE %':>9}{'MAE USD':>11}{'MAPE %':>9}{'R²':>8}{'Gini':>8}")
    for name, m in rows.items():
        print(
            f"{name:<28}{m['mse_usd2']:>18,.0f}{m['rmse_pct_of_mean_revenue']:>9.2f}"
            f"{m['mae_usd']:>11,.0f}{m['mape_pct']:>9.2f}{m['r2']:>8.3f}{m['gini_normalized']:>8.3f}"
        )
    for name, item in metrics["psi"].items():
        if isinstance(item, dict):
            print(f"PSI {name}: {item['value']:.3f} ({item['label']}, p permutación = {item['permutation_p_value']:.3f})")
    for name, k2 in metrics["k2"].items():
        print(f"K² {name}: {k2['statistic']:.2f}, p = {k2['p_value']:.3f} (n = {k2['n']})")
    band = metrics["band"]
    print(f"Banda: {band['method']}, cobertura test {band['test_coverage_pct']:.1f} %, ancho medio {band['mean_width_pct_of_prediction']:.1f} %")


def main() -> None:
    args = _parse_args()
    # La consola de Windows usa cp1252 y no imprime σ ni ².
    sys.stdout.reconfigure(encoding="utf-8")
    metrics = run(args.data, args.out)
    _print_summary(metrics)
    print(f"Artefactos en {args.out}")


if __name__ == "__main__":
    main()
