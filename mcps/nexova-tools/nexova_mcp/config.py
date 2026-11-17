"""Configuración por variables de entorno (sin valores secretos por defecto)."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Falta la variable de entorno obligatoria {name}.")
    return value


@dataclass(frozen=True)
class Settings:
    # Identificador del recurso protegido (RFC 8707/9728). Es la URL pública del
    # endpoint MCP, p. ej. https://<codespace>-8100.app.github.dev/mcp, y debe
    # coincidir con el "API identifier" del recurso en Logto (= `aud` del token).
    resource_url: str
    # Issuer OIDC de Logto, p. ej. https://<tenant>.logto.app/oidc
    oauth_issuer: str
    # API de Nexova (services/api) y cuenta de servicio con la que el MCP la llama.
    api_url: str
    api_email: str
    api_password: str
    upstream_timeout_seconds: float = 5.0
    host: str = "0.0.0.0"
    port: int = 8100

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            resource_url=_required("MCP_RESOURCE_URL").rstrip("/"),
            oauth_issuer=_required("MCP_OAUTH_ISSUER").rstrip("/"),
            api_url=os.environ.get("NEXOVA_API_URL", "http://localhost:8000").rstrip("/"),
            api_email=_required("NEXOVA_API_EMAIL"),
            api_password=_required("NEXOVA_API_PASSWORD"),
            upstream_timeout_seconds=float(os.environ.get("MCP_UPSTREAM_TIMEOUT_SECONDS", "5")),
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MCP_PORT", "8100")),
        )
