"""
Router: /inventory (Hito 5, Nexova)

Productos y stock viven en Supabase (SQLModel). `current_stock` nunca se
almacena: se calcula en cada lectura a partir de `AssetEntry`/`AssetExit`.
Toda escritura requiere autenticación; el `user_uuid` de las órdenes es el
`id` del usuario ya validado por TinyDB vía `get_current_user`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, func, select

from database import get_db
from models import Asset, AssetEntry, AssetExit, User
from schemas import (
    AssetCreate,
    AssetEntryCreate,
    AssetEntryRead,
    AssetExitCreate,
    AssetExitRead,
    AssetRead,
    AssetSummary,
    OrderRead,
)
from security import get_current_user

router = APIRouter(prefix="/inventory", tags=["inventory"])


def _current_stock(db: Session, asset_id: int) -> int:
    entries = db.exec(
        select(func.coalesce(func.sum(AssetEntry.quantity), 0)).where(
            AssetEntry.asset_id == asset_id
        )
    ).one()
    exits = db.exec(
        select(func.coalesce(func.sum(AssetExit.quantity), 0)).where(
            AssetExit.asset_id == asset_id
        )
    ).one()
    return int(entries) - int(exits)


def _stock_by_asset(db: Session) -> dict[int, int]:
    """Stock de todos los assets en 2 queries (agregadas por asset_id), en vez
    de 2 queries por asset. Evita el N+1 de `list_products` contra Supabase."""
    stock: dict[int, int] = {}

    entry_totals = db.exec(
        select(AssetEntry.asset_id, func.sum(AssetEntry.quantity)).group_by(
            AssetEntry.asset_id
        )
    ).all()
    for asset_id, total in entry_totals:
        stock[asset_id] = stock.get(asset_id, 0) + int(total)

    exit_totals = db.exec(
        select(AssetExit.asset_id, func.sum(AssetExit.quantity)).group_by(
            AssetExit.asset_id
        )
    ).all()
    for asset_id, total in exit_totals:
        stock[asset_id] = stock.get(asset_id, 0) - int(total)

    return stock


def _get_asset_or_404(db: Session, asset_id: int) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found.")
    return asset


def _asset_to_read(db: Session, asset: Asset) -> AssetRead:
    return AssetRead(
        id=asset.id,
        name=asset.name,
        sku=asset.sku,
        category=asset.category,
        office=asset.office,
        current_stock=_current_stock(db, asset.id),
    )


@router.get("/products", response_model=list[AssetRead])
def list_products(db: Session = Depends(get_db)) -> list[AssetRead]:
    assets = db.exec(select(Asset)).all()
    stock = _stock_by_asset(db)
    return [
        AssetRead(
            id=asset.id,
            name=asset.name,
            sku=asset.sku,
            category=asset.category,
            office=asset.office,
            current_stock=stock.get(asset.id, 0),
        )
        for asset in assets
    ]


@router.post("/products", response_model=AssetRead, status_code=201)
def create_product(
    payload: AssetCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AssetRead:
    existing = db.exec(select(Asset).where(Asset.sku == payload.sku)).first()
    if existing is not None:
        raise HTTPException(
            status_code=400, detail=f"An asset with sku '{payload.sku}' already exists."
        )

    asset = Asset(**payload.model_dump())
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return _asset_to_read(db, asset)


@router.get("/products/{asset_id}", response_model=AssetRead)
def get_product(asset_id: int, db: Session = Depends(get_db)) -> AssetRead:
    asset = _get_asset_or_404(db, asset_id)
    return _asset_to_read(db, asset)


@router.post("/orders/inbound", response_model=AssetEntryRead, status_code=201)
def create_inbound_order(
    payload: AssetEntryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AssetEntryRead:
    _get_asset_or_404(db, payload.asset_id)

    entry = AssetEntry(
        asset_id=payload.asset_id,
        quantity=payload.quantity,
        supplier=payload.supplier,
        office=payload.office,
        created_at=datetime.now(timezone.utc),
        user_uuid=current_user.id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return AssetEntryRead.model_validate(entry)


@router.post("/orders/outbound", response_model=AssetExitRead, status_code=201)
def create_outbound_order(
    payload: AssetExitCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AssetExitRead:
    asset = _get_asset_or_404(db, payload.asset_id)

    available = _current_stock(db, payload.asset_id)
    if payload.quantity > available:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Insufficient stock for asset '{asset.name}'. "
                f"Available: {available}, requested: {payload.quantity}."
            ),
        )

    exit_ = AssetExit(
        asset_id=payload.asset_id,
        quantity=payload.quantity,
        exit_type=payload.exit_type,
        assigned_to=payload.assigned_to,
        office=payload.office,
        created_at=datetime.now(timezone.utc),
        user_uuid=current_user.id,
    )
    db.add(exit_)
    db.commit()
    db.refresh(exit_)
    return AssetExitRead(
        id=exit_.id,
        asset_id=exit_.asset_id,
        quantity=exit_.quantity,
        exit_type=exit_.exit_type,
        assigned_to=exit_.assigned_to,
        office=exit_.office,
        created_at=exit_.created_at,
        user_uuid=exit_.user_uuid,
        current_stock=available - payload.quantity,
    )


@router.get("/orders", response_model=list[OrderRead])
def list_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[OrderRead]:
    assets_by_id = {asset.id: asset for asset in db.exec(select(Asset)).all()}

    def _summary(asset_id: int) -> AssetSummary:
        asset = assets_by_id[asset_id]
        return AssetSummary(id=asset.id, name=asset.name, sku=asset.sku)

    orders: list[OrderRead] = []

    for entry in db.exec(select(AssetEntry)).all():
        orders.append(
            OrderRead(
                order_type="inbound",
                id=entry.id,
                asset=_summary(entry.asset_id),
                quantity=entry.quantity,
                office=entry.office,
                created_at=entry.created_at,
                user_uuid=entry.user_uuid,
                supplier=entry.supplier,
            )
        )

    for exit_ in db.exec(select(AssetExit)).all():
        orders.append(
            OrderRead(
                order_type="outbound",
                id=exit_.id,
                asset=_summary(exit_.asset_id),
                quantity=exit_.quantity,
                office=exit_.office,
                created_at=exit_.created_at,
                user_uuid=exit_.user_uuid,
                exit_type=exit_.exit_type,
                assigned_to=exit_.assigned_to,
            )
        )

    orders.sort(key=lambda o: o.created_at)
    return orders
