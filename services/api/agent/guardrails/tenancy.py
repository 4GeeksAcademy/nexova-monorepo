"""
Tenancy: a qué empresa cliente pertenece quien escribe (BRIEFING §3: nunca
mezclar ni filtrar datos entre cuentas).

- Personal interno de Nexova (`admin` / `manager`): ve todas las empresas.
- Usuario externo con `client_company`: solo su empresa. El filtro se aplica
  por código en la tool de tickets, no depende de que el modelo obedezca.
- Usuario externo sin empresa: no consulta tickets (fallo seguro).

`INTERNAL_TENANT` es el valor por defecto de `run_agent` para scripts y tests
internos; la API siempre construye el tenant del usuario autenticado
(`tenant_for_user`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

# Empresas cliente de outsourcing conocidas (gestor de incidentes) y los alias
# con los que se las nombra. Sirven para detectar cruces de cuenta en la
# entrada y en la salida.
CLIENT_COMPANY_ALIASES: dict[str, tuple[str, ...]] = {
    "FinServ Group": ("finserv",),
    "CloudBase Ltd": ("cloudbase", "cloud base"),
    "Retail Iberia SL": ("retail iberia",),
    "RetailMax Corp": ("retailmax", "retail max"),
    "UrbanMobility SA": ("urbanmobility", "urban mobility"),
    "DataBridge Ltd": ("databridge", "data bridge"),
    "InsureNow Group": ("insurenow", "insure now"),
    "Logify GmbH": ("logify",),
    "PharmaCore SL": ("pharmacore", "pharma core"),
    "MediaFirst SA": ("mediafirst", "media first"),
    "SkyTech SL": ("skytech", "sky tech"),
    "TechNova Inc": ("technova", "tech nova"),
}


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def same_company(a: str | None, b: str | None) -> bool:
    return bool(a and b) and _plain(a).strip() == _plain(b).strip()


@dataclass(frozen=True)
class Tenant:
    client_company: str | None = None
    is_staff: bool = False

    @property
    def can_read_tickets(self) -> bool:
        return self.is_staff or bool(self.client_company)

    def owns(self, company: str | None) -> bool:
        return self.is_staff or same_company(self.client_company, company)

    def as_state(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "Tenant":
        raw = state.get("tenant")
        if not raw:
            return INTERNAL_TENANT
        return cls(client_company=raw.get("client_company"), is_staff=bool(raw.get("is_staff")))


INTERNAL_TENANT = Tenant(client_company=None, is_staff=True)

_STAFF_ROLES = {"admin", "manager"}


def tenant_for_user(user: Any) -> Tenant:
    """Tenant del usuario autenticado (`models.User`)."""
    role = getattr(getattr(user, "role", None), "value", getattr(user, "role", None))
    return Tenant(
        client_company=getattr(user, "client_company", None) or None,
        is_staff=str(role) in _STAFF_ROLES,
    )


def mentioned_companies(text: str) -> set[str]:
    """Empresas cliente conocidas que aparecen en `text` (por nombre o alias)."""
    plain = _plain(text)
    found = set()
    for company, aliases in CLIENT_COMPANY_ALIASES.items():
        names = (_plain(company), *aliases)
        if any(re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", plain) for name in names):
            found.add(company)
    return found


def foreign_companies(text: str, tenant: Tenant) -> set[str]:
    """Empresas nombradas en `text` que no son la del tenant (vacío para personal interno)."""
    if tenant.is_staff:
        return set()
    return {c for c in mentioned_companies(text) if not same_company(c, tenant.client_company)}
