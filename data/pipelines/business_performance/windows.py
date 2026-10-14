"""Semanas ISO en UTC: `week_start` es siempre el lunes 00:00 UTC (CONTEXT §4)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from .config import LOOKBACK_WEEKS, MAX_LOOKBACK_WEEKS


def week_start_of(moment: datetime) -> date:
    """Lunes de la semana ISO (UTC) a la que pertenece `moment`."""

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    day = moment.astimezone(timezone.utc).date()
    return day - timedelta(days=day.weekday())


def resolve_window(
    week_start: date | None = None,
    lookback_weeks: int = LOOKBACK_WEEKS,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Ventana `[start, end)` UTC que recalcula la corrida.

    Termina al cierre de `week_start` (por defecto, la semana actual) y abarca
    `lookback_weeks` semanas completas hacia atrás, incluida esa.
    """

    if not 1 <= lookback_weeks <= MAX_LOOKBACK_WEEKS:
        raise ValueError(f"lookback_weeks debe estar entre 1 y {MAX_LOOKBACK_WEEKS}.")
    if week_start is None:
        week_start = week_start_of(now or datetime.now(timezone.utc))
    elif week_start.weekday() != 0:
        raise ValueError(f"week_start debe ser lunes: {week_start.isoformat()}.")

    last_monday = datetime.combine(week_start, time.min, tzinfo=timezone.utc)
    return last_monday - timedelta(weeks=lookback_weeks - 1), last_monday + timedelta(weeks=1)
