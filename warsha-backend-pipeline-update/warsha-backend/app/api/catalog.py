"""Global Catalog API"""
import os
from fastapi import APIRouter, Depends, HTTPException, Header, Query
import sqlalchemy as sa
from sqlalchemy import select, func, or_, String, Integer, Boolean, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
from datetime import datetime

from app.core.database import get_db
from app.api.auth import require_any
from app.services.analytics import log_event, Events
from fastapi.security import HTTPBearer as _HTTPBearer
_bearer = _HTTPBearer(auto_error=False)
from app.models.models import Base, Material, MaterialCategory

router = APIRouter(prefix="/catalog", tags=["catalog"])

from fastapi import Response
from fastapi.routing import APIRoute

@router.options("/{rest_of_path:path}")
async def preflight_handler(rest_of_path: str, response: Response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    return response

from app.api.super_admin import _active_tokens as _sa_tokens

class GlobalCatalogItem(Base):
    __tablename__ = "global_catalog"
    id:          Mapped[int]              = mapped_column(primary_key=True)
    code:        Mapped[str]              = mapped_column(String(50), unique=True)
    name_ar:     Mapped[str]              = mapped_column(String(200))
    name_en:     Mapped[Optional[str]]    = mapped_column(String(200), nullable=True)
    unit:        Mapped[str]              = mapped_column(String(30))
    category:    Mapped[Optional[str]]    = mapped_column(String(100), nullable=True)
    description: Mapped[Optional[str]]    = mapped_column(Text, nullable=True)
    tags:        Mapped[Optional[str]]    = mapped_column(String(500), nullable=True)
    is_active:   Mapped[bool]             = mapped_column(Boolean, default=True)
    usage_count: Mapped[int]              = mapped_column(Integer, default=0)
    created_at:  Mapped[datetime]         = mapped_column(DateTime, server_default=func.now())
    # ── المواصفات الهندسية — تُنقل تلقائياً عند الاستيراد لمخزون الورشة ──
    is_plate:            Mapped[Optional[bool]]    = mapped_column(Boolean, default=False, nullable=True)
    thickness_mm:        Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(8, 2), nullable=True)
    density_kg_m2:       Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 4), nullable=True)
    plate_length_cm:     Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 2), nullable=True)
    plate_width_cm:      Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 2), nullable=True)
    plate_weight_kg:     Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 3), nullable=True)
    bar_length_cm:       Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 2), nullable=True)
    bar_weight_kg:        Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 3), nullable=True)
    weight_per_meter_kg: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 4), nullable=True)


def _verify_sa(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Super Admin فقط")
    token = authorization.split(" ", 1)[1]
    if token not in _sa_tokens:
        raise HTTPException(401, "جلسة منتهية — سجل دخول مرة أخرى")
    return token


class CatalogItemIn(BaseModel):
    code: str
    name_ar: str
    name_en: Optional[str] = None
    unit: str
    category: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    is_plate: Optional[bool] = False
    thickness_mm: Optional[Decimal] = None
    density_kg_m2: Optional[Decimal] = None
    plate_length_cm: Optional[Decimal] = None
    plate_width_cm: Optional[Decimal] = None
    plate_weight_kg: Optional[Decimal] = None
    bar_length_cm: Optional[Decimal] = None
    bar_weight_kg: Optional[Decimal] = None
    weight_per_meter_kg: Optional[Decimal] = None

class ImportIn(BaseModel):
    unit_cost: Optional[Decimal] = Decimal("0")
    stock_qty: Optional[Decimal] = Decimal("0")
    reorder_level: Optional[Decimal] = Decimal("0")
    category_name: Optional[str] = None

class BulkIn(BaseModel):
    items: List[CatalogItemIn]


@router.get("/categories")
async def get_categories(db: AsyncSession = Depends(get_db), _creds=Depends(_bearer)):
    result = await db.execute(
        select(GlobalCatalogItem.category)
        .where(GlobalCatalogItem.is_active == True)
        .where(GlobalCatalogItem.category != None)
        .distinct().order_by(GlobalCatalogItem.category)
    )
    return [r[0] for r in result.fetchall()]


@router.get("")
async def search_catalog(
    q: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(GlobalCatalogItem).where(GlobalCatalogItem.is_active == True)
    if q:
        stmt = stmt.where(or_(
            GlobalCatalogItem.name_ar.ilike(f"%{q}%"),
            GlobalCatalogItem.name_en.ilike(f"%{q}%"),
            GlobalCatalogItem.code.ilike(f"%{q}%"),
            GlobalCatalogItem.tags.ilike(f"%{q}%"),
        ))
    if category:
        stmt = stmt.where(GlobalCatalogItem.category == category)
    stmt = stmt.order_by(GlobalCatalogItem.usage_count.desc(), GlobalCatalogItem.name_ar).offset(offset).limit(limit)
    result = await db.execute(stmt)
    items = result.scalars().all()
    return [{
        "id":i.id,"code":i.code,"name_ar":i.name_ar,"name_en":i.name_en,"unit":i.unit,
        "category":i.category,"description":i.description,"tags":i.tags,"usage_count":i.usage_count,
        "is_plate": i.is_plate, "thickness_mm": float(i.thickness_mm) if i.thickness_mm else None,
        "plate_length_cm": float(i.plate_length_cm) if i.plate_length_cm else None,
        "plate_width_cm": float(i.plate_width_cm) if i.plate_width_cm else None,
        "plate_weight_kg": float(i.plate_weight_kg) if i.plate_weight_kg else None,
        "weight_per_meter_kg": float(i.weight_per_meter_kg) if i.weight_per_meter_kg else None,
    } for i in items]


@router.post("", status_code=201)
async def add_item(data: CatalogItemIn, db: AsyncSession = Depends(get_db), _=Depends(_verify_sa)):
    existing = await db.execute(select(GlobalCatalogItem).where(GlobalCatalogItem.code == data.code))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "الكود موجود بالفعل")
    item = GlobalCatalogItem(**data.model_dump())
    db.add(item); await db.commit(); await db.refresh(item)
    return {"id": item.id, "message": "تم الإضافة"}


@router.post("/bulk", status_code=201)
async def bulk_import(data: BulkIn, db: AsyncSession = Depends(get_db), _=Depends(_verify_sa)):
    added = skipped = 0
    for d in data.items:
        ex = await db.execute(select(GlobalCatalogItem).where(GlobalCatalogItem.code == d.code))
        if ex.scalar_one_or_none(): skipped += 1; continue
        db.add(GlobalCatalogItem(**d.model_dump())); added += 1
    await db.commit()
    return {"added": added, "skipped": skipped}


@router.put("/{item_id}")
async def update_item(item_id: int, data: CatalogItemIn, db: AsyncSession = Depends(get_db), _=Depends(_verify_sa)):
    result = await db.execute(select(GlobalCatalogItem).where(GlobalCatalogItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item: raise HTTPException(404, "غير موجود")
    for k, v in data.model_dump().items(): setattr(item, k, v)
    await db.commit()
    return {"message": "تم التحديث"}


@router.delete("/{item_id}")
async def delete_item(item_id: int, db: AsyncSession = Depends(get_db), _=Depends(_verify_sa)):
    result = await db.execute(select(GlobalCatalogItem).where(GlobalCatalogItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item: raise HTTPException(404, "غير موجود")
    item.is_active = False; await db.commit()
    return {"message": "تم الحذف"}


@router.post("/{item_id}/import")
async def import_to_inventory(
    item_id: int, data: ImportIn,
    db: AsyncSession = Depends(get_db),
    current=Depends(require_any),
):
    result = await db.execute(select(GlobalCatalogItem).where(GlobalCatalogItem.id == item_id))
    cat_item = result.scalar_one_or_none()
    if not cat_item: raise HTTPException(404, "العنصر غير موجود في الكتالوج")

    tenant_id = current.get("tenant_id")
    existing = await db.execute(select(Material).where(Material.tenant_id == tenant_id, Material.code == cat_item.code))
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"المادة {cat_item.code} موجودة بالفعل في مخزونك")

    if data.unit_cost is not None and data.unit_cost < 0:
        raise HTTPException(400, "سعر الوحدة لا يمكن أن يكون سالباً")
    if data.stock_qty is not None and data.stock_qty < 0:
        raise HTTPException(400, "الكمية الافتتاحية لا يمكن أن تكون سالبة")
    if data.reorder_level is not None and data.reorder_level < 0:
        raise HTTPException(400, "حد إعادة الطلب لا يمكن أن يكون سالباً")

    cat_name = data.category_name or cat_item.category or "عام"
    cat_result = await db.execute(select(MaterialCategory).where(MaterialCategory.tenant_id == tenant_id, MaterialCategory.name_ar == cat_name))
    category = cat_result.scalar_one_or_none()
    if not category:
        category = MaterialCategory(tenant_id=tenant_id, name_ar=cat_name, name_en=cat_name)
        db.add(category); await db.flush()

    material_kind = "plate" if cat_item.is_plate else ("bar" if cat_item.weight_per_meter_kg else "general")
    opening_qty = data.stock_qty or Decimal("0")
    material = Material(
        tenant_id=tenant_id, code=cat_item.code,
        name_ar=cat_item.name_ar, name_en=cat_item.name_en or cat_item.name_ar,
        unit=cat_item.unit, unit_cost=data.unit_cost or Decimal("0"),
        # محطة (round-2 gap fix): الرصيد الافتتاحي بيتسجّل عن طريق
        # post_receipt تحت (مش هنا مباشرة) عشان يعمل StockMovement +
        # يزامن StockQuant — قبل كده كان Material.stock_qty بيتحدد هنا
        # مباشرة من غير أي حركة أو Quant، فيبقى فيه رصيد "شبح" (phantom)
        # مطابقتش أبداً مع /materials/{id}/stock-check ولا traceability.
        stock_qty=Decimal("0"),
        reorder_level=data.reorder_level or Decimal("0"),
        category_id=category.id, material_kind=material_kind,
        is_plate=cat_item.is_plate or False,
        thickness_mm=cat_item.thickness_mm,
        density_kg_m2=cat_item.density_kg_m2,
        plate_length_cm=cat_item.plate_length_cm,
        plate_width_cm=cat_item.plate_width_cm,
        plate_weight_kg=cat_item.plate_weight_kg,
        bar_length_cm=cat_item.bar_length_cm,
        bar_weight_kg=cat_item.bar_weight_kg,
        weight_per_meter_kg=cat_item.weight_per_meter_kg,
    )
    db.add(material)
    await db.flush()  # نحتاج material.id قبل ما نسجل حركة استلام عليه

    if opening_qty > 0:
        from app.services.inventory_ledger import post_receipt
        await post_receipt(
            db, material=material, qty=opening_qty, unit_cost=data.unit_cost or Decimal("0"),
            source_doc_type="catalog_import", source_doc_id=cat_item.id,
            reference=f"استيراد من الكتالوج العام — {cat_item.code}",
        )

    cat_item.usage_count = (cat_item.usage_count or 0) + 1
    await db.commit()
    await log_event(Events.CATALOG_ITEM_IMPORTED, tenant_id=tenant_id, entity_type="material",
                     entity_id=material.id, metadata={"catalog_item_id": item_id, "code": cat_item.code},
                     source_path=f"/api/v1/catalog/{item_id}/import")
    return {"message": f"تم إضافة {cat_item.name_ar} لمخزونك", "material_id": material.id}
