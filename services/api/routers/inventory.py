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

import cache
from database import get_db
from models import Asset, AssetEntry, AssetExit, User
from routes.telemetry import log_telemetry_event
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

# TTL cortos: stock y órdenes son datos operativos que cambian con cada
# movimiento, pero cachear 30s evita recalcular agregaciones en ráfagas de
# lecturas (ver CACHING_REPORT.md).
_PRODUCTS_CACHE_KEY = "inventory:products"
_PRODUCTS_TTL_SECONDS = 30
_ORDERS_CACHE_KEY = "inventory:orders"
_ORDERS_TTL_SECONDS = 30

# --- Telemetría (docs/telemetry/telemetry-plan.md, event-schemas.json) ------
#
# `Asset.category` usa hoy hardware/peripherals/office_supplies/training_materials
# (schemas.py) — distinto del vocabulario de negocio exigido por el CONTEXT
# (training_kit/certification/onboarding_equipment). Se mapea solo en el punto
# de emisión, nunca se cambia el dato de negocio original. `certification`
# queda hoy inalcanzable — pendiente de validación de negocio.
_PRODUCT_CATEGORY_TELEMETRY_MAP = {
    "hardware": "onboarding_equipment",
    "peripherals": "onboarding_equipment",
    "office_supplies": "onboarding_equipment",
    "training_materials": "training_kit",
}
_DEFAULT_TELEMETRY_PRODUCT_CATEGORY = "onboarding_equipment"

_OFFICE_TELEMETRY_CODES = {"valencia": "valencia", "miami": "miami"}
_CURRENCY_BY_TELEMETRY_OFFICE = {"valencia": "EUR", "miami": "USD"}

KIT_COST_VARIANCE_THRESHOLD_PCT = 10.0
_KIT_COST_BASELINE_SAMPLE_SIZE = 5


def _telemetry_office(office: str) -> str:
    return office.strip().lower()


def _telemetry_currency(office: str) -> str:
    return _CURRENCY_BY_TELEMETRY_OFFICE.get(_telemetry_office(office), "EUR")


def _telemetry_product_category(category: str) -> str:
    return _PRODUCT_CATEGORY_TELEMETRY_MAP.get(category, _DEFAULT_TELEMETRY_PRODUCT_CATEGORY)


def _kit_cost_baseline(db: Session, asset_id: int, supplier: str) -> float | None:
    """Media móvil de `unit_cost` de las últimas órdenes de entrada del mismo
    producto+proveedor. `None` si no hay ninguna orden previa con coste."""

    rows = db.exec(
        select(AssetEntry.unit_cost)
        .where(
            AssetEntry.asset_id == asset_id,
            AssetEntry.supplier == supplier,
            AssetEntry.unit_cost.is_not(None),
        )
        .order_by(AssetEntry.created_at.desc())
        .limit(_KIT_COST_BASELINE_SAMPLE_SIZE)
    ).all()
    if not rows:
        return None
    return sum(rows) / len(rows)


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
        min_stock_threshold=asset.min_stock_threshold,
        programme_id=asset.programme_id,
    )


@router.get("/products", response_model=list[AssetRead])
def list_products(db: Session = Depends(get_db)) -> list[AssetRead]:
    def _compute() -> list[AssetRead]:
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
                min_stock_threshold=asset.min_stock_threshold,
                programme_id=asset.programme_id,
            )
            for asset in assets
        ]

    return cache.get_or_set(_PRODUCTS_CACHE_KEY, _PRODUCTS_TTL_SECONDS, _compute)


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
    cache.invalidate(_PRODUCTS_CACHE_KEY)
    return _asset_to_read(db, asset)


@router.get("/products/{asset_id}", response_model=AssetRead)
def get_product(asset_id: int, db: Session = Depends(get_db)) -> AssetRead:
    asset = _get_asset_or_404(db, asset_id)
    return _asset_to_read(db, asset)


@router.patch("/products/{asset_id}", status_code=403)
def reject_direct_stock_edit(
    asset_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """El stock nunca se modifica directamente (CONTEXT §6): toda escritura
    pasa por /orders/inbound o /orders/outbound. Esta ruta existe solo para
    rechazarlo explícitamente y emitir `direct_stock_edit_rejected` — no hay
    ningún otro camino de código que pudiera disparar ese evento hoy."""

    asset = _get_asset_or_404(db, asset_id)
    log_telemetry_event(
        "direct_stock_edit_rejected",
        source="backend",
        session_id=None,
        user_id=current_user.id,
        request_id=None,
        properties={
            "office": _telemetry_office(asset.office),
            "product_id": asset.id,
            "attempted_action": "direct_field_update",
            "rejection_reason": "no_direct_write_endpoint",
        },
    )
    raise HTTPException(
        status_code=403,
        detail="Direct stock edits are not allowed; use POST /inventory/orders/inbound or /inventory/orders/outbound.",
    )


@router.post("/orders/inbound", response_model=AssetEntryRead, status_code=201)
def create_inbound_order(
    payload: AssetEntryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AssetEntryRead:
    asset = db.get(Asset, payload.asset_id)
    if asset is None:
        log_telemetry_event(
            "inbound_order_rejected",
            source="backend",
            session_id=None,
            user_id=current_user.id,
            request_id=None,
            properties={"office": None, "product_id": None, "rejection_reason": "asset_not_found"},
        )
        raise HTTPException(status_code=404, detail="Asset not found.")

    # Baseline ANTES de insertar la orden nueva, para no comparar el pedido
    # consigo mismo.
    baseline_unit_cost = _kit_cost_baseline(db, payload.asset_id, payload.supplier)

    entry = AssetEntry(
        asset_id=payload.asset_id,
        quantity=payload.quantity,
        supplier=payload.supplier,
        office=payload.office,
        created_at=datetime.now(timezone.utc),
        user_uuid=current_user.id,
        unit_cost=payload.unit_cost,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    cache.invalidate(_PRODUCTS_CACHE_KEY)
    cache.invalidate(_ORDERS_CACHE_KEY)

    telemetry_office = _telemetry_office(payload.office)
    telemetry_currency = _telemetry_currency(payload.office)
    telemetry_category = _telemetry_product_category(asset.category)

    log_telemetry_event(
        "inbound_order_created",
        source="backend",
        session_id=None,
        user_id=current_user.id,
        request_id=None,
        properties={
            "order_id": entry.id,
            "office": telemetry_office,
            "product_id": asset.id,
            "product_category": telemetry_category,
            "programme_id": asset.programme_id,
            "quantity": entry.quantity,
            "currency": telemetry_currency,
            "unit_cost": entry.unit_cost,
            "supplier_id": None,
            "supplier_name": entry.supplier,
        },
    )

    if entry.unit_cost is not None and baseline_unit_cost is not None and baseline_unit_cost > 0:
        variance_pct = ((entry.unit_cost - baseline_unit_cost) / baseline_unit_cost) * 100
        if abs(variance_pct) > KIT_COST_VARIANCE_THRESHOLD_PCT:
            log_telemetry_event(
                "kit_cost_variance_detected",
                source="backend",
                session_id=None,
                user_id=current_user.id,
                request_id=None,
                properties={
                    "order_id": entry.id,
                    "office": telemetry_office,
                    "product_id": asset.id,
                    "programme_id": asset.programme_id,
                    "supplier_id": None,
                    "supplier_name": entry.supplier,
                    "quantity": entry.quantity,
                    "currency": telemetry_currency,
                    "unit_cost": entry.unit_cost,
                    "baseline_unit_cost": baseline_unit_cost,
                    "variance_pct": variance_pct,
                    "threshold_pct": KIT_COST_VARIANCE_THRESHOLD_PCT,
                },
            )

    return AssetEntryRead.model_validate(entry)


@router.post("/orders/outbound", response_model=AssetExitRead, status_code=201)
def create_outbound_order(
    payload: AssetExitCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AssetExitRead:
    asset = db.get(Asset, payload.asset_id)
    if asset is None:
        log_telemetry_event(
            "outbound_order_rejected",
            source="backend",
            session_id=None,
            user_id=current_user.id,
            request_id=None,
            properties={
                "office": None,
                "product_id": None,
                "requested_quantity": payload.quantity,
                "rejection_reason": "asset_not_found",
            },
        )
        raise HTTPException(status_code=404, detail="Asset not found.")

    telemetry_office = _telemetry_office(payload.office)
    telemetry_category = _telemetry_product_category(asset.category)

    available = _current_stock(db, payload.asset_id)
    if payload.quantity > available:
        log_telemetry_event(
            "outbound_order_rejected",
            source="backend",
            session_id=None,
            user_id=current_user.id,
            request_id=None,
            properties={
                "office": telemetry_office,
                "product_id": asset.id,
                "product_category": telemetry_category,
                "programme_id": asset.programme_id,
                "requested_quantity": payload.quantity,
                "available_quantity": available,
                "rejection_reason": "insufficient_stock",
            },
        )
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
    cache.invalidate(_PRODUCTS_CACHE_KEY)
    cache.invalidate(_ORDERS_CACHE_KEY)

    new_stock = available - payload.quantity

    log_telemetry_event(
        "outbound_order_created",
        source="backend",
        session_id=None,
        user_id=current_user.id,
        request_id=None,
        properties={
            "order_id": exit_.id,
            "office": telemetry_office,
            "product_id": asset.id,
            "product_category": telemetry_category,
            "programme_id": asset.programme_id,
            "quantity": exit_.quantity,
            "currency": _telemetry_currency(payload.office),
            "exit_type": exit_.exit_type,
        },
    )

    if available >= asset.min_stock_threshold and new_stock < asset.min_stock_threshold:
        log_telemetry_event(
            "stock_threshold_triggered",
            source="backend",
            session_id=None,
            user_id=current_user.id,
            request_id=None,
            properties={
                "office": telemetry_office,
                "product_id": asset.id,
                "product_category": telemetry_category,
                "programme_id": asset.programme_id,
                "quantity": new_stock,
                "threshold": asset.min_stock_threshold,
                "triggered_by_order_id": exit_.id,
            },
        )

    return AssetExitRead(
        id=exit_.id,
        asset_id=exit_.asset_id,
        quantity=exit_.quantity,
        exit_type=exit_.exit_type,
        assigned_to=exit_.assigned_to,
        office=exit_.office,
        created_at=exit_.created_at,
        user_uuid=exit_.user_uuid,
        current_stock=new_stock,
    )


@router.get("/orders", response_model=list[OrderRead])
def list_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[OrderRead]:
    def _compute() -> list[OrderRead]:
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

    return cache.get_or_set(_ORDERS_CACHE_KEY, _ORDERS_TTL_SECONDS, _compute)
