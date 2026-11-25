"""
Router: /auth

- POST /auth/register          -> registro público: crea siempre una cuenta externa (`user`, sin departamento).
- POST /auth/login             -> valida credenciales, devuelve un JWT.
- GET  /auth/me                -> usuario autenticado (email, rol y departamento) + su Profile.
- POST /auth/forgot-password   -> envia (si el usuario existe) un email con link de reset.
- POST /auth/reset-password    -> consume el token del email y fija una nueva contraseña.
- POST /auth/change-password   -> (ruta protegida) cambia la contraseña de la sesion activa.

Límite de intentos (auditoría OWASP, H1): 5 fallos de login por IP+email,
3 peticiones de reset por email/IP, 10 tokens de reset por IP y 5 contraseñas
actuales incorrectas por usuario en 15 min → 429 con `Retry-After`. Login de
un email inexistente también ejecuta bcrypt (tiempo constante, sin enumeración).
"""

from __future__ import annotations

import logging

from email_service import send_password_reset_email
from fastapi import APIRouter, Depends, HTTPException, Request, status
from models import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MeRead,
    MessageResponse,
    ResetPasswordRequest,
    Token,
    User,
    UserCreate,
    UserRegistered,
)
from password_resets import consume_reset_token, create_reset_token
from rate_limit import (
    client_ip,
    login_failures,
    password_changes,
    reset_attempts,
    reset_requests,
)
from security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    get_current_user,
    verify_password,
)

from routes.profiles import get_profile_by_user_id
from routes.telemetry import log_telemetry_event
from routes.users import get_user_by_email, register_external_user, set_user_password
from authz import role_code
from rbac import store as rbac_store

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

GENERIC_FORGOT_PASSWORD_MESSAGE = "Si esa dirección está registrada, recibirás un enlace en breve."


@router.post("/register", response_model=UserRegistered, status_code=201)
def register(payload: UserCreate) -> UserRegistered:
    """Registro público. Nunca concede acceso interno: el rol y el departamento los asigna un admin."""
    return UserRegistered.model_validate(register_external_user(payload))


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, request: Request) -> Token:
    key = f"login:{client_ip(request)}:{payload.email.strip().lower()}"
    login_failures.check(key)
    user = get_user_by_email(payload.email)
    # bcrypt también para emails inexistentes: mismo tiempo de respuesta.
    password_ok = verify_password(payload.password, user.hashed_password if user else DUMMY_PASSWORD_HASH)
    if user is None or not password_ok:
        login_failures.hit(key)
        log_telemetry_event(
            "login_failed",
            source="backend",
            session_id=None,
            user_id=None,
            request_id=None,
            properties={"failure_reason": "invalid_credentials"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos.",
        )

    if not user.is_active:
        log_telemetry_event(
            "login_failed",
            source="backend",
            session_id=None,
            user_id=None,
            request_id=None,
            properties={"failure_reason": "inactive_user"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario inactivo.",
        )

    login_failures.reset(key)
    access_token = create_access_token(user_id=user.id)
    log_telemetry_event(
        "login_succeeded",
        source="backend",
        session_id=None,
        user_id=user.id,
        request_id=None,
        properties={"role": role_code(user)},
    )
    return Token(access_token=access_token)


@router.get("/me", response_model=MeRead)
def read_me(current_user: User = Depends(get_current_user)) -> MeRead:
    profile = get_profile_by_user_id(current_user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Perfil no encontrado.")

    code = role_code(current_user)
    department = rbac_store.get_department(current_user.department_id) if current_user.department_id else None
    return MeRead(
        email=current_user.email,
        role=code,
        role_id=code,
        rank=rbac_store.rank_of(code),
        department_id=current_user.department_id,
        department_name=department["name"] if department else None,
        profile=profile,
    )


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordRequest, request: Request) -> MessageResponse:
    # Respuesta siempre 200 con mensaje generico: no debe revelar si el email existe.
    keys = (f"forgot:{payload.email.strip().lower()}", f"forgot-ip:{client_ip(request)}")
    reset_requests.check(*keys)
    for key in keys:
        reset_requests.hit(key)
    user = get_user_by_email(payload.email)
    if user is not None and user.is_active:
        token = create_reset_token(user.id)
        try:
            send_password_reset_email(user.email, token)
        except Exception:
            # Un fallo del proveedor de email no debe filtrar si el usuario existe
            # ni tumbar la petición: se registra y se responde igual que siempre.
            logger.exception("Fallo al enviar el email de reset a %s", user.id)

        # Solo en esta rama (usuario existe y activo) — nunca en la rama de
        # abajo, para no recrear a nivel de telemetría el canal de
        # enumeración de cuentas que la respuesta HTTP ya evita.
        log_telemetry_event(
            "password_reset_requested",
            source="backend",
            session_id=None,
            user_id=user.id,
            request_id=None,
            properties={},
        )

    return MessageResponse(detail=GENERIC_FORGOT_PASSWORD_MESSAGE)


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest, request: Request) -> MessageResponse:
    key = f"reset:{client_ip(request)}"
    reset_attempts.check(key)
    user_id = consume_reset_token(payload.token)
    if user_id is None:
        reset_attempts.hit(key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El enlace es invalido, expiro o ya fue utilizado.",
        )

    set_user_password(user_id, payload.new_password)
    log_telemetry_event(
        "password_reset_completed",
        source="backend",
        session_id=None,
        user_id=user_id,
        request_id=None,
        properties={},
    )
    return MessageResponse(detail="Contraseña actualizada correctamente.")


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
) -> MessageResponse:
    key = f"change:{current_user.id}"
    password_changes.check(key)
    if not verify_password(payload.current_password, current_user.hashed_password):
        password_changes.hit(key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La contraseña actual es incorrecta.",
        )

    set_user_password(current_user.id, payload.new_password)
    log_telemetry_event(
        "password_changed",
        source="backend",
        session_id=None,
        user_id=current_user.id,
        request_id=None,
        properties={},
    )
    return MessageResponse(detail="Contraseña actualizada correctamente.")
