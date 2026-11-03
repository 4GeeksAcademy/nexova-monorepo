"""
Evaluación técnica del modelo de pronóstico de ventas de Nexova (diagnóstico de ajuste).

    uv run --project services/api --group ml python scripts/evaluate_sales_forecast.py

Pasos: carga `data/raw/nexova_sales.csv` → split 8/2 años (el test no se toca) →
features causales → validación cruzada temporal de 5 pliegues sobre el train
(sin barajar, con comprobación de orden y de fuga de lag/rolling) → MAE y RMSE
de train y validación (media ± desviación estándar) → curva de aprendizaje.

Salidas en `data/eval/` (o `--out`): `cv_metrics.json` y `learning_curve.png`.
El informe `evaluation_report.md` se redacta a partir de estas cifras. Es
determinista (`random_state=42`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "data" / "pipelines"))

from sales_forecast import model as forecast_model  # noqa: E402
from sales_forecast.dataset import RAW_CSV_PATH, load_sales, split_train_test  # noqa: E402
from sales_forecast.evaluation import (  # noqa: E402
    MIN_FOLDS,
    check_fold_feature_leakage,
    cross_validate_model,
    diagnose,
    learning_curve_usd,
    temporal_cv_splits,
)
from sales_forecast.features import build_features, drop_incomplete  # noqa: E402
from sales_forecast.plot import plot_learning_curve  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "data" / "eval"
CV_VALIDATION_MONTHS = 6
CURVE_STEP = 6
CURVE_MIN_SIZE = 12


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=RAW_CSV_PATH, help="CSV de ventas mensuales")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="carpeta de salida")
    return parser.parse_args()


def _rounded(value: object) -> object:
    """Redondea los floats: con `n_jobs=-1` el orden de las sumas cambia la última cifra
    decimal entre ejecuciones y el JSON dejaría de ser idéntico."""
    if isinstance(value, float):
        return round(float(f"{value:.10g}"), 8) + 0.0  # + 0.0 evita "-0.0"
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    return value


def run(data_path: Path, out_dir: Path) -> dict:
    series = load_sales(data_path)
    train_raw, test_raw = split_train_test(series)  # el test se separa y no se vuelve a usar

    features = build_features(series)
    train = drop_incomplete(features[features["month"].isin(train_raw["month"])])

    # Mismos hiperparámetros que el modelo entrenado (elegidos con CV solo sobre train).
    best_params = forecast_model.tune_random_forest(train).best_params

    splits = temporal_cv_splits(len(train), n_splits=MIN_FOLDS, test_size=CV_VALIDATION_MONTHS)
    leakage = check_fold_feature_leakage(train_raw, train, splits)
    cv = cross_validate_model(train, best_params, splits)
    # Solo tamaños que existen en los 5 pliegues (hasta el train del primero): así cada punto
    # promedia los mismos bloques de validación y la curva no mezcla "más datos" con "otro
    # periodo de validación". El tamaño completo de cada pliegue ya está en `cv`.
    sizes = list(range(CURVE_MIN_SIZE, len(splits[0][0]) + 1, CURVE_STEP))
    curve = learning_curve_usd(train, best_params, splits, sizes)
    diagnosis = diagnose(cv, curve)

    mean_revenue = float(train["revenue_usd"].mean())
    results = {
        "setup": {
            "model": "RandomForestRegressor",
            "random_state": forecast_model.RANDOM_STATE,
            "best_params": best_params,
            "train_period": [train_raw["month"].min().strftime("%Y-%m"), train_raw["month"].max().strftime("%Y-%m")],
            "train_rows_with_features": int(len(train)),
            "cv": f"TimeSeriesSplit(n_splits={MIN_FOLDS}, test_size={CV_VALIDATION_MONTHS}), ventana expansiva, sin barajar",
            "test_untouched": [test_raw["month"].min().strftime("%Y-%m"), test_raw["month"].max().strftime("%Y-%m")],
            "mean_train_revenue_usd": mean_revenue,
            "errors_in": "USD de ingreso reconstruido (yoy_ratio * lag_12)",
        },
        "chronological_order_verified": True,
        "fold_feature_leakage_check": leakage,
        "cv": cv,
        "learning_curve": curve,
        "diagnosis": diagnosis,
    }
    results = _rounded(results)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cv_metrics.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    plot_learning_curve(curve, "rmse", out_dir / "learning_curve.png")
    return results


def _print_summary(results: dict) -> None:
    cv, diagnosis = results["cv"], results["diagnosis"]
    print(f"Hiperparámetros: {results['setup']['best_params']}")
    print(f"{'pliegue':<9}{'train':>8}{'val':>6}{'MAE train':>12}{'MAE val':>11}{'RMSE train':>13}{'RMSE val':>11}")
    for fold in cv["folds"]:
        print(
            f"{fold['fold']:<9}{fold['train_rows']:>8}{fold['val_rows']:>6}"
            f"{fold['train']['mae_usd']:>12,.0f}{fold['validation']['mae_usd']:>11,.0f}"
            f"{fold['train']['rmse_usd']:>13,.0f}{fold['validation']['rmse_usd']:>11,.0f}"
        )
    for key, stats in cv["summary"].items():
        print(f"{key:<32}{stats['mean']:>12,.0f} ± {stats['std']:,.0f}")
    print(f"Diagnóstico orientativo: {diagnosis['suggested_label']} (brecha val/train = {diagnosis['gap_ratio_val_over_train_rmse']:.2f})")


def main() -> None:
    args = _parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # la consola de Windows usa cp1252 y no imprime ±
    results = run(args.data, args.out)
    _print_summary(results)
    print(f"Artefactos en {args.out}")


if __name__ == "__main__":
    main()
