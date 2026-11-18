"""
Dominio del gestor de incidencias de Nexova (BRIEFING).

Única fuente de verdad de los valores permitidos: la usan el modelo y la API
(`services/api/incident_manager`) y el script de seed. Los valores de `branch`,
`category`, `status` y `origin` coinciden exactamente con el BRIEFING.
"""

from __future__ import annotations

from typing import Literal, get_args

# --- Valores permitidos -----------------------------------------------------

Branch = Literal["central", "valencia_operations", "miami_office", "remote"]
Category = Literal[
    "technical_failure",
    "process_error",
    "client_complaint",
    "candidate_issue",
    "staff_issue",
    "sla_breach",
    "data_quality",
    "other",
]
Status = Literal["open", "in_progress", "resolved", "discarded"]
Origin = Literal["customer", "branch", "internal"]

BRANCHES: tuple[str, ...] = get_args(Branch)
CATEGORIES: tuple[str, ...] = get_args(Category)
STATUSES: tuple[str, ...] = get_args(Status)
ORIGINS: tuple[str, ...] = get_args(Origin)

# Nombre para mostrar de cada sede (BRIEFING · "Oficinas de Nexova").
BRANCH_LABELS: dict[str, str] = {
    "central": "Central — Sede Valencia",
    "valencia_operations": "Valencia — Operaciones",
    "miami_office": "Miami Office",
    "remote": "Remoto (empleado sin sede fija)",
}

# --- Ciclo de vida ----------------------------------------------------------

# open → in_progress | discarded; in_progress → resolved | discarded.
# `resolved` y `discarded` son estados finales.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "open": ("in_progress", "discarded"),
    "in_progress": ("resolved", "discarded"),
    "resolved": (),
    "discarded": (),
}

# --- Límites de campos ------------------------------------------------------

TITLE_MAX_LENGTH = 120
DESCRIPTION_MAX_LENGTH = 5000

# --- Seed desde CSV ---------------------------------------------------------

SEED_ORIGIN: Origin = "customer"
SEED_BRANCH: Branch = "central"

CSV_STATUS_MAP: dict[str, str] = {
    "OPEN": "open",
    "CLOSED": "resolved",
    "DISCARDED": "discarded",
}

CSV_CATEGORY_MAP: dict[str, str] = {
    "TECHNICAL": "technical_failure",
    "BILLING": "process_error",
    "ACCESS": "technical_failure",
    "HR_QUERY": "process_error",
    "COMPLAINT": "client_complaint",
}

# Totales esperados tras cargar los 96 registros válidos del CSV (BRIEFING).
EXPECTED_SEED_BY_STATUS: dict[str, int] = {"open": 27, "resolved": 56, "discarded": 13}
EXPECTED_SEED_BY_CATEGORY: dict[str, int] = {
    "technical_failure": 49,
    "process_error": 35,
    "client_complaint": 12,
}
EXPECTED_SEED_TOTAL = 96
