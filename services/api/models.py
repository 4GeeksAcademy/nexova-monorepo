from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel

VALID_CATEGORIES = [
    "job_boards",
    "ats_software",
    "assessment_tools",
    "training_platforms",
    "payroll_and_hr_software",
    "video_interview",
    "background_check",
    "office_and_facilities",
    "it_and_software_licenses",
]

VALID_STATUSES = ["active", "suspended"]

_CATEGORY_SET = set(VALID_CATEGORIES)


class Country(str, Enum):
    SPAIN = "Spain"
    USA = "USA"


class Status(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


_STATUS_ALIASES = {
    "active": Status.ACTIVE,
    "activo": Status.ACTIVE,
    "suspended": Status.SUSPENDED,
    "suspendido": Status.SUSPENDED,
}

_CURRENCY_BY_COUNTRY = {
    Country.SPAIN: "EUR",
    Country.USA: "USD",
}


class ProviderBase(BaseModel):
    name: str = Field(min_length=1)
    country: Country
    categories: list[str] = Field(min_length=1)
    monthly_rate: float = Field(gt=0)
    currency: str
    status: Status

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, categories: list[str]) -> list[str]:
        invalid = [cat for cat in categories if cat not in _CATEGORY_SET]
        if invalid:
            raise ValueError(
                "Invalid categories: "
                f"{invalid}. Valid values: {VALID_CATEGORIES}"
            )
        return categories

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str | Status) -> Status:
        if isinstance(value, Status):
            return value

        normalized = str(value).strip().lower()
        status = _STATUS_ALIASES.get(normalized)
        if status is None:
            raise ValueError(
                "Invalid status. Use active/suspended or activo/suspendido."
            )
        return status

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return str(value).strip().upper()

    @model_validator(mode="after")
    def validate_currency_for_country(self) -> "ProviderBase":
        expected_currency = _CURRENCY_BY_COUNTRY[self.country]
        if self.currency != expected_currency:
            raise ValueError(
                f"Currency for {self.country.value} must be {expected_currency}."
            )
        return self


class ProviderCreate(ProviderBase):
    pass


class ProviderResponse(ProviderBase):
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(from_attributes=True)


# --- Auth / Users / Profiles -------------------------------------------------


class Role(str, Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"


class ProfileFields(BaseModel):
    name: str | None = None
    phone: str | None = None
    address: str | None = None


class UserCreate(BaseModel):
    """Payload de registro: credenciales + datos de perfil opcionales."""

    email: EmailStr
    password: str = Field(min_length=8)
    name: str | None = None
    phone: str | None = None
    address: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    role: Role | None = None


class UserRead(BaseModel):
    id: str
    email: EmailStr
    is_active: bool
    role: Role
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProfileUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    address: str | None = None


class ProfileRead(BaseModel):
    id: str
    user_id: str
    name: str | None = None
    phone: str | None = None
    address: str | None = None

    model_config = ConfigDict(from_attributes=True)


class MeRead(BaseModel):
    email: EmailStr
    role: Role
    profile: ProfileRead


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class User(BaseModel):
    """Representación interna completa (incluye `hashed_password`)."""

    id: str
    email: EmailStr
    hashed_password: str
    is_active: bool = True
    role: Role = Role.USER
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    password_changed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class UserRegistered(BaseModel):
    """Respuesta de `POST /users` (registro). Sin `email`: es un flujo de
    auth no autenticado y no debe reenviar el email en el body."""

    id: str
    is_active: bool
    role: Role
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Respuestas genéricas compartidas ----------------------------------------


class MessageResponse(BaseModel):
    detail: str


class DeleteResponse(BaseModel):
    deleted: bool


class HealthResponse(BaseModel):
    status: str


# --- Inventario (Hito 5, Nexova) — modelos ORM (SQLModel) --------------------
#
# Viven en Supabase, separados de TinyDB. `current_stock` nunca es una
# columna: siempre se calcula en la capa de rutas a partir de las órdenes.
# Sin tabla de usuarios aquí — `user_uuid` solo referencia el UUID de TinyDB.


class Asset(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    name: str
    sku: str = SQLField(unique=True, index=True)
    category: str
    office: str


class AssetEntry(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    asset_id: int = SQLField(foreign_key="asset.id")
    quantity: int
    supplier: str
    office: str
    created_at: datetime = SQLField(default_factory=lambda: datetime.now(timezone.utc))
    user_uuid: str


class AssetExit(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    asset_id: int = SQLField(foreign_key="asset.id")
    quantity: int
    exit_type: str
    assigned_to: str | None = None
    office: str
    created_at: datetime = SQLField(default_factory=lambda: datetime.now(timezone.utc))
    user_uuid: str
