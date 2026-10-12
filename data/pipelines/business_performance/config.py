"""Constantes del pipeline. Nombres alineados con PIPELINE_DESIGN.md y el CONTEXT."""

from __future__ import annotations

PIPELINE_NAME = "weekly_office_program_performance"

EVENT_INBOUND = "inbound_order_created"
EVENT_OUTBOUND = "outbound_order_created"
EVENT_SHORTAGE = "stock_threshold_triggered"
EVENT_COST_VARIANCE = "kit_cost_variance_detected"

# Los únicos eventos que alimentan los 4 KPIs (CONTEXT §3).
EVENT_TYPES: tuple[str, ...] = (
    EVENT_INBOUND,
    EVENT_OUTBOUND,
    EVENT_SHORTAGE,
    EVENT_COST_VARIANCE,
)

# Eventos que traen `order_id`: además de por `event_id`, se deduplican por
# (event_type, order_id). `stock_threshold_triggered` no lo lleva.
ORDER_EVENT_TYPES: tuple[str, ...] = (EVENT_INBOUND, EVENT_OUTBOUND, EVENT_COST_VARIANCE)

# La moneda se deriva de la oficina, igual que en `routers/inventory.py`.
# Nunca se convierten ni se suman monedas entre sí.
CURRENCY_BY_OFFICE: dict[str, str] = {"valencia": "EUR", "miami": "USD"}

# D2: `programme_id` nulo forma parte de la clave `unique`, así que se agrupa aquí.
UNASSIGNED_PROGRAMME = "unassigned"

# D5: cada corrida recalcula la semana actual y la anterior (eventos tardíos).
LOOKBACK_WEEKS = 2
MAX_LOOKBACK_WEEKS = 52

# Una corrida `running` más vieja que esto se da por muerta antes de tomar el lock.
STALE_RUN_MINUTES = 15

# Decimales de `total_material_cost`. Redondear hace el agregado determinista
# frente al orden de suma de los floats (si no, `rows_revised` daría falsos positivos).
COST_DECIMALS = 4

# Frecuencia del ciclo de reporting: lunes 04:00 UTC.
SCHEDULE_CRON = "0 4 * * 1"
