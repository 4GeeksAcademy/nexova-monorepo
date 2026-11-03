"""Gráficos del pronóstico (matplotlib sin pantalla, backend Agg)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from scipy import stats  # noqa: E402

_USD = FuncFormatter(lambda value, _: f"{value / 1e3:,.0f}k")


def plot_forecast(
    history: pd.DataFrame,
    test: pd.DataFrame,
    predicted: np.ndarray,
    low: np.ndarray,
    high: np.ndarray,
    naive: np.ndarray,
    band_label: str,
    path: Path,
) -> None:
    """Ingresos reales del test frente a la predicción y su banda, con el último año de train."""
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.plot(history["month"], history["revenue_usd"], color="#9aa0a6", lw=1.5, label="Real (train)")
    ax.fill_between(test["month"], low, high, color="#4c8bf5", alpha=0.18, label=f"Banda 95 % ({band_label})")
    ax.plot(test["month"], test["revenue_usd"], color="#202124", lw=2, marker="o", ms=4, label="Real (test)")
    ax.plot(test["month"], predicted, color="#1a73e8", lw=2, marker="o", ms=4, label="Predicción Random Forest")
    ax.plot(test["month"], naive, color="#e37400", lw=1.2, ls="--", label="Naive estacional (año pasado)")
    ax.axvline(test["month"].min(), color="#5f6368", lw=1, ls=":")
    ax.text(test["month"].min(), ax.get_ylim()[1], "  inicio test", va="top", fontsize=9, color="#5f6368")

    for month, value in zip(test["month"], test["revenue_usd"]):
        if month.month == 8:
            ax.annotate("agosto", (month, value), xytext=(0, -16), textcoords="offset points", ha="center", fontsize=8)

    ax.yaxis.set_major_formatter(_USD)
    ax.set_ylabel("Ingresos mensuales (USD)")
    ax.set_title("Nexova: ingresos reales vs. pronóstico a 12 meses vista (test 2024-2025)")
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_learning_curve(curve: dict, metric: str, path: Path) -> None:
    """Curva de aprendizaje: error de train y de validación (media ± std entre pliegues) vs. tamaño del train.

    `metric` es "rmse" o "mae"; `curve` es la salida de `evaluation.learning_curve_usd`.
    """
    sizes = np.asarray(curve["train_sizes"])
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for key, color, label in (
        (f"train_{metric}", "#e37400", "Entrenamiento"),
        (f"val_{metric}", "#1a73e8", "Validación (CV temporal)"),
    ):
        mean, std = np.asarray(curve[key]["mean"]), np.asarray(curve[key]["std"])
        ax.fill_between(sizes, mean - std, mean + std, color=color, alpha=0.15)
        ax.plot(sizes, mean, color=color, lw=2, marker="o", ms=5, label=label)

    ax.xaxis.set_major_locator(plt.MultipleLocator(6))
    ax.yaxis.set_major_formatter(_USD)
    ax.set_xlabel("Meses de entrenamiento (ventana expansiva)")
    ax.set_ylabel(f"{metric.upper()} (USD)")
    ax.set_title(f"Nexova: curva de aprendizaje del Random Forest ({metric.upper()}, 5 pliegues temporales)")
    ax.grid(alpha=0.25)
    ax.legend(loc="upper right", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_residuals(test_residuals_usd: np.ndarray, oof_ratio_residuals: np.ndarray, path: Path) -> None:
    """Histograma de los residuos del test y gráfico Q-Q de los residuos fuera de muestra."""
    fig, (hist_ax, qq_ax) = plt.subplots(1, 2, figsize=(11, 4.2))
    hist_ax.hist(test_residuals_usd, bins=10, color="#4c8bf5", edgecolor="white")
    hist_ax.axvline(0, color="#202124", lw=1)
    hist_ax.xaxis.set_major_formatter(_USD)
    hist_ax.set_title("Residuos del test (real − predicción, USD)")
    hist_ax.grid(alpha=0.25)

    stats.probplot(oof_ratio_residuals, dist="norm", plot=qq_ax)
    qq_ax.set_title("Q-Q de residuos fuera de muestra (CV, cociente)")
    qq_ax.set_xlabel("Cuantiles teóricos de la normal")
    qq_ax.set_ylabel("Residuos ordenados")
    qq_ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
