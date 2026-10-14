"""
Pipeline de desempeño de negocio de Nexova — lógica ETL.

Calcula el "Reporte Semanal de Desempeño por Oficina y Programa" a partir de
`public.telemetry_events` (solo lectura) y lo publica en
`reporting.weekly_office_program_performance`. Diseño completo en
`data/pipelines/PIPELINE_DESIGN.md`.

Este paquete no depende de Prefect ni de `services/`: son funciones normales
que `data/pipelines/pipeline.py` envuelve en tasks y que la API
(`services/api/reporting/`) reutiliza para sus consultas.
"""
