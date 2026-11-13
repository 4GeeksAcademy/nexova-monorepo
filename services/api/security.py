"""
Autenticación JWT stateless para la API de Nexova.

- Hashing de contraseñas con `libpass[bcrypt]` (import compatible con passlib).
- Firma/verificación de tokens con `python-jose`.
- Dependencia `get_current_user` para proteger rutas con FastAPI.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.hash import bcrypt

from models import User

load_dotenv()

SECRET_KEY = os.environ["SECRET_KEY"]
ALGORITHM = os.environ.get("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

# Esquema `HTTPBearer`: lee `Authorization: Bearer <token>` (lo que ya envían el
# backoffice y los scripts) y hace que Swagger (/docs → Authorize) muestre un
# campo para pegar el token obtenido en `POST /auth/login`. Sin cabecera
# responde 401 con `WWW-Authenticate: Bearer`, igual que el esquema anterior.
bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    description="Pega el `access_token` de `POST /auth/login` (sin el prefijo `Bearer`).",
)


def get_bearer_token(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    """El JWT en bruto de la petición (sin validar; lo valida `get_current_user`)."""
    return credentials.credentials


def hash_password(plain_password: str) -> str:
    return bcrypt.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.verify(plain_password, hashed_password)


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str:
    """Devuelve el `sub` (user id) del token o lanza JWTError si es inválido/expirado."""
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    user_id = payload.get("sub")
    if user_id is None:
        raise JWTError("Token sin `sub`.")
    return user_id


async def get_current_user(token: str = Depends(get_bearer_token)) -> User:
    # Import diferido para evitar un ciclo: users_service -> security -> users_service
    from routes.users import get_user_by_id

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se pudo validar las credenciales.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        user_id = decode_access_token(token)
    except JWTError:
        raise credentials_exception

    user = get_user_by_id(user_id)
    if user is None:
        raise credentials_exception

    return user
