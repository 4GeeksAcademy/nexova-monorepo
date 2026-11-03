"""
Pronóstico mensual de ingresos de Nexova (BRIEFING: modelo de series de tiempo).

Paquete puro, sin Prefect ni base de datos, con el mismo patrón que
`business_performance/`:

    dataset    carga y valida `data/raw/nexova_sales.csv` y hace el split 8/2 años
    features   features causales a 12 meses vista (solo lags >= 12)
    model      baselines, Random Forest con CV temporal y XGBoost de referencia
    intervals  banda de variabilidad a partir de residuos fuera de muestra
    metrics    MSE, PSI, Gini normalizado, K² y métricas para Finanzas
    plot       gráficos de la predicción y de los residuos

`dataset` solo depende de pandas; el resto necesita el grupo `ml` de
`services/api` (`uv run --project services/api --group ml ...`).
"""
