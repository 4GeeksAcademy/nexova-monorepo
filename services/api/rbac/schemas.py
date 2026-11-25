"""Esquemas de entrada y salida de roles, departamentos e informes internos."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


def _check_code(value: str) -> str:
    if not _CODE_RE.match(value):
        raise ValueError("El código usa minúsculas, dígitos y «_», empieza por letra (2-40 caracteres).")
    return value


class RoleRead(BaseModel):
    id: str
    code: str
    name: str
    rank: int


class RoleCreate(BaseModel):
    code: str
    name: str = Field(min_length=1, max_length=80)
    rank: int = Field(ge=1, le=99)
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    _code = field_validator("code")(_check_code)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    rank: int | None = Field(default=None, ge=1, le=99)
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @model_validator(mode="after")
    def _not_empty(self) -> "RoleUpdate":
        if self.name is None and self.rank is None:
            raise ValueError("Indica `name` o `rank`.")
        return self


class DepartmentRead(BaseModel):
    id: str
    code: str
    name: str


class DepartmentCreate(BaseModel):
    code: str
    name: str = Field(min_length=1, max_length=120)
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    _code = field_validator("code")(_check_code)


class DepartmentUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class InternalReportCreate(BaseModel):
    """`department_id` solo lo puede elegir un Admin; los demás roles crean en su departamento."""

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)
    department_id: str | None = None
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class InternalReportUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=10_000)
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @model_validator(mode="after")
    def _not_empty(self) -> "InternalReportUpdate":
        if self.title is None and self.body is None:
            raise ValueError("Indica `title` o `body`.")
        return self


class InternalReportRead(BaseModel):
    id: str
    department_id: str
    title: str
    body: str
    created_by: str
    created_at: datetime
