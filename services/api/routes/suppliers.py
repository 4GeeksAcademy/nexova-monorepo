from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from tinydb import TinyDB

from models import Country, DeleteResponse, ProviderCreate, Status, User, VALID_CATEGORIES
from security import get_current_user

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "suppliers.json"
TABLE_NAME = "suppliers"

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


class SupplierCreate(ProviderCreate):
    contract_renewal_date: str | None = None
    contact_email: EmailStr | None = None
    notes: str | None = None


class SupplierRead(BaseModel):
    """Esquema de salida, declarado con sus propios campos — independiente
    de `SupplierCreate` para no acoplar el contrato de lectura al de escritura."""

    id: int
    name: str
    country: Country
    categories: list[str]
    monthly_rate: float
    currency: str
    status: Status
    contract_renewal_date: str | None = None
    contact_email: EmailStr | None = None
    notes: str | None = None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RateUpdate(BaseModel):
    monthly_rate: float = Field(gt=0)


class StatusUpdate(BaseModel):
    status: Status


def _get_table() -> tuple[TinyDB, object]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = TinyDB(DB_PATH)
    return db, db.table(TABLE_NAME)


def _doc_to_response(doc: object) -> SupplierRead:
    payload = dict(doc)
    payload["id"] = doc.doc_id
    return SupplierRead.model_validate(payload)


@router.post("", response_model=SupplierRead, status_code=201)
def create_supplier(
    supplier: SupplierCreate, current_user: User = Depends(get_current_user)
) -> SupplierRead:
    db, table = _get_table()
    try:
        payload = supplier.model_dump(mode="json")
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        doc_id = table.insert(payload)
        created = table.get(doc_id=doc_id)
        if created is None:
            raise HTTPException(status_code=500, detail="No se pudo crear el proveedor.")
        return _doc_to_response(created)
    finally:
        db.close()


@router.get("", response_model=list[SupplierRead])
def list_suppliers(
    country: Country | None = Query(default=None),
    category: str | None = Query(default=None),
) -> list[SupplierRead]:
    if category is not None and category not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid category '{category}'. Valid values: {VALID_CATEGORIES}",
        )

    db, table = _get_table()
    try:
        docs = table.all()

        if country is not None:
            docs = [doc for doc in docs if doc.get("country") == country.value]

        if category is not None:
            docs = [doc for doc in docs if category in doc.get("categories", [])]

        return [_doc_to_response(doc) for doc in docs]
    finally:
        db.close()


@router.get("/{supplier_id}", response_model=SupplierRead)
def get_supplier(supplier_id: int) -> SupplierRead:
    db, table = _get_table()
    try:
        doc = table.get(doc_id=supplier_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Supplier not found.")
        return _doc_to_response(doc)
    finally:
        db.close()


@router.patch("/{supplier_id}/rate", response_model=SupplierRead)
def update_supplier_rate(
    supplier_id: int,
    payload: RateUpdate,
    current_user: User = Depends(get_current_user),
) -> SupplierRead:
    db, table = _get_table()
    try:
        existing = table.get(doc_id=supplier_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Supplier not found.")

        table.update(
            {
                "monthly_rate": payload.monthly_rate,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            doc_ids=[supplier_id],
        )

        updated = table.get(doc_id=supplier_id)
        if updated is None:
            raise HTTPException(status_code=404, detail="Supplier not found.")
        return _doc_to_response(updated)
    finally:
        db.close()


@router.patch("/{supplier_id}/status", response_model=SupplierRead)
def update_supplier_status(
    supplier_id: int,
    payload: StatusUpdate,
    current_user: User = Depends(get_current_user),
) -> SupplierRead:
    db, table = _get_table()
    try:
        existing = table.get(doc_id=supplier_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Supplier not found.")

        table.update({"status": payload.status.value}, doc_ids=[supplier_id])

        updated = table.get(doc_id=supplier_id)
        if updated is None:
            raise HTTPException(status_code=404, detail="Supplier not found.")
        return _doc_to_response(updated)
    finally:
        db.close()


@router.delete("/{supplier_id}", response_model=DeleteResponse)
def delete_supplier(
    supplier_id: int, current_user: User = Depends(get_current_user)
) -> DeleteResponse:
    db, table = _get_table()
    try:
        existing = table.get(doc_id=supplier_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Supplier not found.")

        table.remove(doc_ids=[supplier_id])
        return DeleteResponse(deleted=True)
    finally:
        db.close()
