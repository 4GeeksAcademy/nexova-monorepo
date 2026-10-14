"""
Tests unitarios del pipeline del "Reporte Semanal de Desempeño por Oficina y Programa".

Prueban las tasks de transformación de `data/pipelines/pipeline.py` (vía
`task.fn`, sin levantar Prefect) con eventos en memoria que tienen la misma
forma que devuelve la extracción de `telemetry_events`. Ningún test toca la
base de datos: `conftest.py` la bloquea.

Ejecutar desde la raíz del monorepo:

    uv run --project services/api python -m pytest tests/pipelines/test_pipeline.py
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

import pipeline
from business_performance.extract import EVENT_COLUMNS
from business_performance.silence import classify_capture_status

# Semana ISO de referencia: lunes 5 a domingo 11 de octubre de 2026 (UTC).
WEEK = "2026-10-05"
NEXT_WEEK = "2026-10-12"

_event_counter = 0


def telemetry_event(
    event_type: str,
    timestamp: str = "2026-10-06T10:00:00+00:00",
    *,
    office: Any = "valencia",
    programme_id: Any = "b2b-sales",
    value: Any = None,
    unit_cost: Any = None,
    quantity: Any = None,
    order_id: Any = None,
    event_id: Any = "auto",
) -> dict[str, Any]:
    """Un evento con la forma de una fila extraída de `telemetry_events`
    (columnas de la tabla + campos de `tags` + `tags._envelope.eventId`)."""

    global _event_counter
    _event_counter += 1
    return {
        "timestamp": timestamp,
        "event_type": event_type,
        "value": value,
        "office": office,
        "programme_id": programme_id,
        "unit_cost": unit_cost,
        "quantity": quantity,
        "order_id": order_id,
        "event_id": f"evt-{_event_counter}" if event_id == "auto" else event_id,
    }


def inbound(unit_cost: Any, quantity: Any, order_id: int, **kwargs: Any) -> dict[str, Any]:
    return telemetry_event(
        "inbound_order_created",
        unit_cost=unit_cost,
        quantity=quantity,
        value=quantity,
        order_id=order_id,
        **kwargs,
    )


def outbound(quantity: int, order_id: int, **kwargs: Any) -> dict[str, Any]:
    return telemetry_event(
        "outbound_order_created", quantity=quantity, value=quantity, order_id=order_id, **kwargs
    )


def shortage(stock_left: int = 4, **kwargs: Any) -> dict[str, Any]:
    return telemetry_event("stock_threshold_triggered", quantity=stock_left, value=stock_left, **kwargs)


def cost_variance(variance_pct: float, order_id: int, **kwargs: Any) -> dict[str, Any]:
    return telemetry_event(
        "kit_cost_variance_detected", value=variance_pct, order_id=order_id, **kwargs
    )


def events_frame(*events: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(list(events), columns=list(EVENT_COLUMNS))


def prepare(*events: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, int]]:
    return pipeline.prepare_business_events.fn(events_frame(*events))


def kpi_by_group(kpi: pd.DataFrame, column: str) -> dict[tuple[str, str, str], Any]:
    return {
        (row["office"], row["programme_id"], row["week_start"]): row[column]
        for row in kpi.to_dict(orient="records")
    }


def weekly_rows(*events: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Ejecuta el subflow de KPIs completo, tarea por tarea, sin Prefect."""

    prepared, _ = prepare(*events)
    rows = pipeline.assemble_weekly_office_program_rows.fn(
        prepared,
        pipeline.compute_total_material_cost.fn(prepared),
        pipeline.compute_kits_delivered_count.fn(prepared),
        pipeline.compute_shortage_events_count.fn(prepared),
        pipeline.compute_cost_variance_events_count.fn(prepared),
    )
    return {(row["office"], row["programme_id"], row["week_start"]): row for row in rows}


# --- KPI: Costo de material por oficina/programa ------------------------------


def test_total_material_cost_matches_context_definition() -> None:
    """CONTEXT: "suma de los costos de inbound_order_created de la semana, en la
    moneda local de la oficina". Calculado a mano: 10 × 12,50 + 4 × 15,00 = 185,00."""

    prepared, _ = prepare(
        inbound(12.50, 10, order_id=1),
        inbound(15.00, 4, order_id=2),
        outbound(10, order_id=1),  # una salida no es gasto en material
    )

    cost = kpi_by_group(pipeline.compute_total_material_cost.fn(prepared), "total_material_cost")

    assert cost == {("valencia", "b2b-sales", WEEK): pytest.approx(185.00)}


def test_total_material_cost_excludes_missing_or_malformed_unit_cost() -> None:
    """Defensivo (D4): una entrada sin `unit_cost` o con `quantity` ilegible no
    suma ni rompe la task; queda contada en la calidad de dato."""

    prepared, quality = prepare(
        inbound(12.50, 10, order_id=1),
        inbound(None, 5, order_id=2),  # costo nulo
        inbound("no-es-un-numero", 3, order_id=3),  # costo con tipo incorrecto
        inbound(9.99, "abc", order_id=4),  # cantidad con tipo incorrecto
    )

    cost = kpi_by_group(pipeline.compute_total_material_cost.fn(prepared), "total_material_cost")

    assert cost == {("valencia", "b2b-sales", WEEK): pytest.approx(125.00)}
    assert quality["rows_missing_cost"] == 3


# --- KPI: Kits entregados ------------------------------------------------------


def test_kits_delivered_count_counts_events_not_units() -> None:
    """D8: "conteo de outbound_order_created de la semana", no la suma de unidades."""

    prepared, _ = prepare(
        outbound(10, order_id=1),
        outbound(25, order_id=2),
        inbound(5.0, 40, order_id=3),
    )

    kits = kpi_by_group(pipeline.compute_kits_delivered_count.fn(prepared), "kits_delivered_count")

    assert kits == {("valencia", "b2b-sales", WEEK): 2}


# --- KPI: Frecuencia de escasez -----------------------------------------------


def test_shortage_events_count_per_office_programme_week() -> None:
    """Cada `stock_threshold_triggered` cuenta en su oficina, programa y semana.
    No traen `order_id`, así que dos avisos distintos nunca se colapsan."""

    prepared, _ = prepare(
        shortage(timestamp="2026-10-06T09:00:00+00:00"),
        shortage(timestamp="2026-10-08T09:00:00+00:00"),
        shortage(programme_id="onboarding"),
        shortage(office="miami", programme_id="onboarding"),
        shortage(timestamp="2026-10-13T09:00:00+00:00"),
    )

    counts = kpi_by_group(
        pipeline.compute_shortage_events_count.fn(prepared), "shortage_events_count"
    )

    assert counts == {
        ("miami", "onboarding", WEEK): 1,
        ("valencia", "b2b-sales", WEEK): 2,
        ("valencia", "b2b-sales", NEXT_WEEK): 1,
        ("valencia", "onboarding", WEEK): 1,
    }


# --- KPI: Frecuencia de variación de costo ------------------------------------


def test_cost_variance_events_count_only_counts_increases() -> None:
    """D1: el KPI habla de subidas anómalas de costo; una bajada no cuenta."""

    prepared, _ = prepare(
        cost_variance(20.0, order_id=1),
        cost_variance(35.5, order_id=2),
        cost_variance(-18.0, order_id=3),
    )

    variance = kpi_by_group(
        pipeline.compute_cost_variance_events_count.fn(prepared), "cost_variance_events_count"
    )

    assert variance == {("valencia", "b2b-sales", WEEK): 2}


# --- Preparación de eventos ------------------------------------------------------


def test_prepare_business_events_drops_duplicates_by_event_and_order() -> None:
    """Un reenvío con el mismo `eventId`, o la misma orden con otro `eventId`,
    cuenta una sola vez. Eventos sin `order_id` no se colapsan entre sí."""

    prepared, quality = prepare(
        outbound(2, order_id=7, event_id="evt-a"),
        outbound(2, order_id=7, event_id="evt-a"),  # mismo eventId
        outbound(2, order_id=7, event_id="evt-b"),  # misma orden, otro eventId
        inbound(3.0, 2, order_id=7),  # misma orden pero otro tipo: es otro hecho
        shortage(event_id=None),
        shortage(event_id=None),
    )

    assert quality["rows_duplicates_dropped"] == 2
    assert prepared["event_type"].value_counts().to_dict() == {
        "stock_threshold_triggered": 2,
        "outbound_order_created": 1,
        "inbound_order_created": 1,
    }


def test_prepare_business_events_rejects_unknown_office_and_assigns_unassigned() -> None:
    """Defensivo: `office` es texto libre en la captura. Solo valen valencia y
    miami (sin importar mayúsculas o espacios); lo demás se descarta y se
    cuenta. Un `programme_id` nulo o vacío va a 'unassigned' (D2)."""

    prepared, quality = prepare(
        outbound(1, order_id=1, office=" Valencia "),
        outbound(1, order_id=2, office="MIAMI", programme_id=None),
        outbound(1, order_id=3, office="miami", programme_id="   "),
        outbound(1, order_id=4, office="madrid"),
        outbound(1, order_id=5, office=None),
        telemetry_event("outbound_order_created", timestamp=None, order_id=6),
    )

    assert quality["rows_read"] == 6
    assert quality["rows_invalid_office"] == 2
    assert quality["rows_unassigned_programme"] == 2
    assert sorted(zip(prepared["office"], prepared["programme_id"])) == [
        ("miami", "unassigned"),
        ("miami", "unassigned"),
        ("valencia", "b2b-sales"),
    ]


def test_week_start_is_monday_utc_at_week_boundaries() -> None:
    """La semana es ISO y en UTC (CONTEXT §4, decisión abierta A1): un evento
    del lunes a la 01:30 en Valencia (UTC+2) es domingo en UTC."""

    prepared, _ = prepare(
        outbound(1, order_id=1, timestamp="2026-10-05T00:00:00+00:00"),  # lunes 00:00 UTC
        outbound(1, order_id=2, timestamp="2026-10-11T23:59:59+00:00"),  # domingo 23:59:59 UTC
        outbound(1, order_id=3, timestamp="2026-10-12T00:00:00+00:00"),  # lunes siguiente
        outbound(1, order_id=4, timestamp="2026-10-12T01:30:00+02:00"),  # = domingo 23:30 UTC
    )

    assert list(prepared["week_start"]) == [WEEK, WEEK, WEEK, NEXT_WEEK]


# --- Filas del reporte -----------------------------------------------------------


def test_currencies_are_never_mixed() -> None:
    """CONTEXT §7: EUR (Valencia) y USD (Miami) se reportan por separado, nunca sumados."""

    rows = weekly_rows(
        inbound(12.50, 10, order_id=1, office="valencia", programme_id="onboarding"),
        inbound(8.00, 20, order_id=2, office="miami", programme_id="onboarding"),
    )

    assert rows[("valencia", "onboarding", WEEK)]["currency"] == "EUR"
    assert rows[("valencia", "onboarding", WEEK)]["total_material_cost"] == pytest.approx(125.0)
    assert rows[("miami", "onboarding", WEEK)]["currency"] == "USD"
    assert rows[("miami", "onboarding", WEEK)]["total_material_cost"] == pytest.approx(160.0)


def test_weekly_rows_combine_the_four_kpis() -> None:
    """La fila final reúne los 4 KPIs del CONTEXT para la combinación, con ceros
    en los que no tuvieron actividad."""

    rows = weekly_rows(
        inbound(12.50, 10, order_id=1),
        inbound(15.00, 4, order_id=2),
        cost_variance(20.0, order_id=2),
        outbound(10, order_id=1),
        shortage(),
        outbound(3, order_id=9, office="miami", programme_id="onboarding"),
    )

    assert rows == {
        ("valencia", "b2b-sales", WEEK): {
            "office": "valencia",
            "programme_id": "b2b-sales",
            "week_start": WEEK,
            "total_material_cost": pytest.approx(185.0),
            "kits_delivered_count": 1,
            "shortage_events_count": 1,
            "cost_variance_events_count": 1,
            "currency": "EUR",
        },
        ("miami", "onboarding", WEEK): {
            "office": "miami",
            "programme_id": "onboarding",
            "week_start": WEEK,
            "total_material_cost": 0.0,
            "kits_delivered_count": 1,
            "shortage_events_count": 0,
            "cost_variance_events_count": 0,
            "currency": "USD",
        },
    }


def test_transform_handles_empty_input() -> None:
    """Una ventana sin eventos no genera filas ni rompe ninguna task."""

    assert weekly_rows() == {}
    _, quality = prepare()
    assert quality == {
        "rows_read": 0,
        "rows_missing_cost": 0,
        "rows_unassigned_programme": 0,
        "rows_invalid_office": 0,
        "rows_duplicates_dropped": 0,
    }


# --- Caché y alerta de silencio ----------------------------------------------------


def test_transform_cache_key_changes_with_late_event() -> None:
    """La caché de 1 h se reutiliza con los mismos eventos (en cualquier orden),
    pero un evento tardío cambia la clave y fuerza el recálculo."""

    first, second = outbound(1, order_id=1), outbound(1, order_id=2)
    key = pipeline.prepare_events_cache_key(None, {"events": events_frame(first, second)})
    reordered = pipeline.prepare_events_cache_key(None, {"events": events_frame(second, first)})
    with_late = pipeline.prepare_events_cache_key(
        None, {"events": events_frame(first, second, outbound(1, order_id=3))}
    )

    assert key == reordered
    assert key != with_late


@pytest.mark.parametrize(
    ("business_events", "session_events", "expected"),
    [
        (12, 0, "active"),
        (0, 30, "no_business_activity"),
        (0, 0, "possible_capture_outage"),
    ],
)
def test_capture_silence_classification(
    business_events: int, session_events: int, expected: str
) -> None:
    """Pregunta 4 del diseño: un cero de negocio con uso del sistema es real;
    sin ningún tipo de evento, probablemente la captura se cayó."""

    assert classify_capture_status(business_events, session_events) == expected


def test_capture_silence_classification_rejects_negative_counts() -> None:
    with pytest.raises(ValueError):
        classify_capture_status(-1, 0)


# --- Subflow ejecutado de forma independiente -------------------------------------


def test_compute_kpis_subflow_runs_on_its_own() -> None:
    """El subflow de KPIs se ejecuta como flow de Prefect por sí solo, sin el
    flow principal ni base de datos (servidor de Prefect temporal y aislado)."""

    from prefect.testing.utilities import prefect_test_harness

    with prefect_test_harness():
        state = pipeline.compute_weekly_office_program_kpis_flow(
            events_frame(inbound(12.50, 10, order_id=1), outbound(10, order_id=1), shortage()),
            return_state=True,
        )

    assert state.is_completed()
    result = state.result()
    assert result["quality"]["rows_read"] == 3
    assert result["rows"] == [
        {
            "office": "valencia",
            "programme_id": "b2b-sales",
            "week_start": WEEK,
            "total_material_cost": 125.0,
            "kits_delivered_count": 1,
            "shortage_events_count": 1,
            "cost_variance_events_count": 0,
            "currency": "EUR",
        }
    ]
