"""
OAuth con MCP Auth (`mcpauth`), en modo resource server.

- `build_mcp_auth()`: Protected Resource Metadata (RFC 9728) con Logto como
  authorization server y los scopes que entiende este servidor.
- La validación del bearer (firma por JWKS, `iss`, `aud` = este recurso,
  caducidad) la hace el middleware de MCP Auth en `server.py`, delante de
  todo `/mcp`: sin token válido no se puede ni listar tools.
- `require_scope()`: mínimo privilegio por tool. El token debe traer el scope
  exacto de la tool; si no, `FORBIDDEN_SCOPE`.

No se usa la capa de auth integrada de FastMCP.
"""

from __future__ import annotations

import jwt
from jwt import PyJWK, PyJWKClient
from mcpauth import MCPAuth
from mcpauth.auth.token_verifier import TokenVerifier
from mcpauth.config import AuthServerConfig, AuthServerType
from mcpauth.exceptions import MCPAuthTokenVerificationException, MCPAuthTokenVerificationExceptionCode
from mcpauth.types import AuthInfo, ResourceServerConfig, ResourceServerMetadata, VerifyAccessTokenFunction
from mcpauth.utils import create_verify_jwt, fetch_server_config

from nexova_mcp.errors import ErrorCode, NexovaToolError

SCOPE_INCIDENTS_READ = "incidents:read"
SCOPE_INCIDENTS_WRITE = "incidents:write"
SCOPE_INVENTORY_READ = "inventory:read"
# No existe `inventory:write`: el inventario es de solo lectura por diseño.
SUPPORTED_SCOPES = [SCOPE_INCIDENTS_READ, SCOPE_INCIDENTS_WRITE, SCOPE_INVENTORY_READ]


def fetch_logto_config(issuer: str) -> AuthServerConfig:
    """Descubre la configuración OIDC de Logto (`{issuer}/.well-known/openid-configuration`)."""
    return fetch_server_config(issuer, AuthServerType.OIDC)


def build_mcp_auth(resource_url: str, auth_server: AuthServerConfig) -> MCPAuth:
    return MCPAuth(
        protected_resources=ResourceServerConfig(
            metadata=ResourceServerMetadata(
                resource=resource_url,
                authorization_servers=[auth_server],
                scopes_supported=SUPPORTED_SCOPES,
                bearer_methods_supported=["header"],
                resource_name="Nexova Tools MCP",
            )
        )
    )


def build_jwt_verifier(
    auth_server: AuthServerConfig, jwk_source: PyJWKClient | PyJWK | None = None
) -> VerifyAccessTokenFunction:
    """
    Verificador JWT de MCP Auth con el JWKS cacheado.

    Es lo mismo que `bearer_auth_middleware("jwt")` (issuer conocido + firma
    por JWKS + caducidad con `create_verify_jwt`), pero reutiliza un único
    `PyJWKClient`: el modo "jwt" de mcpauth 0.2.0b1 crea uno por petición y
    descargaría las claves de Logto en cada llamada. `aud` y scopes los sigue
    comprobando el middleware.
    """
    issuers = TokenVerifier([auth_server])
    if jwk_source is None:
        jwk_source = PyJWKClient(
            auth_server.metadata.jwks_uri,
            cache_jwk_set=True,
            lifespan=300,
            headers={"user-agent": "nexova-tools-mcp", "accept": "*/*"},
        )
    verify_signature = create_verify_jwt(jwk_source)

    def verify(token: str) -> AuthInfo:
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise MCPAuthTokenVerificationException(MCPAuthTokenVerificationExceptionCode.INVALID_TOKEN, cause=exc) from exc
        issuers.validate_jwt_issuer(str(unverified.get("iss", "")))  # → 401 invalid_issuer
        return verify_signature(token)

    return verify


def current_auth(mcp_auth: MCPAuth) -> AuthInfo:
    info = mcp_auth.auth_info
    if info is None:  # no debería ocurrir: el middleware corta antes
        raise NexovaToolError(ErrorCode.FORBIDDEN_SCOPE, "Petición sin identidad OAuth.")
    return info


def require_scope(mcp_auth: MCPAuth, scope: str) -> AuthInfo:
    info = current_auth(mcp_auth)
    if scope not in info.scopes:
        raise NexovaToolError(
            ErrorCode.FORBIDDEN_SCOPE,
            f"El token no tiene el scope '{scope}' que exige esta tool.",
            {"required_scope": scope, "granted_scopes": sorted(info.scopes)},
        )
    return info
