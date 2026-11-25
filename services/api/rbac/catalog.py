"""
Catálogo base de roles y departamentos de Nexova (BRIEFING de Roles y Permisos).

Dos ejes independientes:
- Rol (capacidad): `employee` (1) < `supervisor` (2) < `admin` (3). Responde a «qué puede hacer».
- Departamento (alcance de datos): responde a «qué información le compete». No da acciones extra.

Los identificadores de rol y departamento son su `code` (clave natural estable), de modo que
`User.role` y `User.department_id` son claves foráneas independientes sin tablas intermedias.
"""

from __future__ import annotations

from typing import Final

ROLE_EMPLOYEE: Final = "employee"
ROLE_SUPERVISOR: Final = "supervisor"
ROLE_ADMIN: Final = "admin"

# Códigos heredados (anteriores al RBAC). Siguen siendo válidos al leer `users.json` sin migrar:
# `manager` equivale a `supervisor`; `user` es una cuenta externa (cliente), fuera de la jerarquía interna.
LEGACY_ROLE_ALIASES: Final[dict[str, str]] = {"manager": ROLE_SUPERVISOR}
EXTERNAL_ROLE: Final = "user"
EXTERNAL_RANK: Final = 0

ROLES_SEED: Final[list[dict[str, object]]] = [
    {"code": "employee", "name": "Employee", "rank": 1},
    {"code": "supervisor", "name": "Supervisor", "rank": 2},
    {"code": "admin", "name": "Admin", "rank": 3},
]

DEPARTMENTS_SEED: Final[list[dict[str, str]]] = [
    {"code": "talent_selection", "name": "Talent Selection Operations"},
    {"code": "corporate_training", "name": "Corporate Training"},
    {"code": "customer_support", "name": "Customer Support Outsourcing"},
    {"code": "sales", "name": "Sales and Business Development"},
    {"code": "marketing", "name": "Marketing and Communications"},
    {"code": "human_resources", "name": "Human Resources (Internal)"},
    {"code": "technology", "name": "Technology and Infrastructure"},
]

# Departamento de un admin migrado que no tenía ninguno.
ADMIN_DEFAULT_DEPARTMENT: Final = "technology"

USERS_SEED: Final[list[dict[str, str]]] = [
    {"email": "javier.talent@nexova.example", "role": "supervisor", "department": "talent_selection", "name": "Javier Almeida"},
    {"email": "megan.sales@nexova.example", "role": "supervisor", "department": "sales", "name": "Megan Clarke"},
    {"email": "elena.training@nexova.example", "role": "employee", "department": "corporate_training", "name": "Elena Vargas"},
    {"email": "patricia.hr@nexova.example", "role": "employee", "department": "human_resources", "name": "Patricia Solís"},
    {"email": "sergio.admin@nexova.example", "role": "admin", "department": "technology", "name": "Sergio Molina"},
]

INTERNAL_REPORTS_SEED: Final[list[dict[str, str]]] = [
    {
        "department": "talent_selection",
        "title": "Search #441 — screening bottleneck",
        "body": "48 CVs pending consultant review. No candidate PII in this note.",
    },
    {
        "department": "sales",
        "title": "HubSpot hygiene — stale opportunities",
        "body": "12 opportunities with no activity in 14 days. Follow-up sequence proposed.",
    },
]
