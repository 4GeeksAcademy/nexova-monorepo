"""
Router: /auth

- POST /auth/login             -> valida credenciales, devuelve un JWT.
- GET  /auth/me                -> usuario autenticado (email, role) + su Profile.
- POST /auth/forgot-password   -> envia (si el usuario existe) un email con link de reset.
- POST /auth/reset-password    -> consume el token del email y fija una nueva contraseña.
- POST /auth/change-password   -> (ruta protegida) cambia la contraseña de la sesion activa.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from email_service import send_password_reset_email
from models import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MeRead,
    MessageResponse,
    ResetPasswordRequest,
    Token,
    User,
)
from password_resets import consume_reset_token, create_reset_token
from routes.profiles import get_profile_by_user_id
from routes.users import get_user_by_email, set_user_password
from security import create_access_token, get_current_user, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

GENERIC_FORGOT_PASSWORD_MESSAGE = "Si esa dirección está registrada, recibirás un enlace en breve."


@router.post("/login", response_model=Token)
def login(payload: LoginRequest) -> Token:
    user = get_user_by_email(payload.email)
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario inactivo.",
        )

    access_token = create_access_token(user_id=user.id)
    return Token(access_token=access_token)


@router.get("/me", response_model=MeRead)
def read_me(current_user: User = Depends(get_current_user)) -> MeRead:
    profile = get_profile_by_user_id(current_user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Perfil no encontrado.")

    return MeRead(email=current_user.email, role=current_user.role, profile=profile)


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordRequest) -> MessageResponse:
    # Respuesta siempre 200 con mensaje generico: no debe revelar si el email existe.
    user = get_user_by_email(payload.email)
    if user is not None and user.is_active:
        token = create_reset_token(user.id)
        try:
            send_password_reset_email(user.email, token)
        except Exception:
            # Un fallo del proveedor de email no debe filtrar si el usuario existe
            # ni tumbar la petición: se registra y se responde igual que siempre.
            logger.exception("Fallo al enviar el email de reset a %s", user.id)

    return MessageResponse(detail=GENERIC_FORGOT_PASSWORD_MESSAGE)


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest) -> MessageResponse:
    user_id = consume_reset_token(payload.token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El enlace es invalido, expiro o ya fue utilizado.",
        )

    set_user_password(user_id, payload.new_password)
    return MessageResponse(detail="Contraseña actualizada correctamente.")


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
) -> MessageResponse:
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La contraseña actual es incorrecta.",
        )

    set_user_password(current_user.id, payload.new_password)
    return MessageResponse(detail="Contraseña actualizada correctamente.")
