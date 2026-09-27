"""
المخازن المنطقية (Warehouse) — محطة 10
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, current_tenant_id
from app.api.auth import require_any
from app.models.models import Warehouse

router = APIRouter(prefix="/warehouses", tags=["warehouses"], dependencies=[Depends(require_any)])

# 8 مخازن افتراضية لكل tenant جديد — راجع Blueprint §9 المرحلة 2
DEFAULT_WAREHOUSES = [
    ("RAW",      "مخزن الخامات",        "raw"),
    ("SPARE",    "مخزن قطع الغيار",      "spare"),
    ("IMPORTED", "مخزن المعدات المستوردة", "imported"),
    ("WIP",      "تحت التصنيع",          "wip"),
    ("FG",       "المنتج التام",          "finished_goods"),
    ("REMNANT",  "مخزن النسالة",         "remnant"),
    ("SCRAP",    "مخزن الهالك",          "scrap"),
    ("RETURNS",  "مخزن المرتجعات",       "returns"),
]
DEFAULT_WAREHOUSE_CODES = {c for c, _, _ in DEFAULT_WAREHOUSES}


async def seed_default_warehouses(db: AsyncSession, tenant_id: int) -> int:
    """تُنشئ أي مخزن افتراضي ناقص لهذا الـ tenant (idempotent — آمن الاستدعاء المتكرر). ترجع عدد المُضاف."""
    existing = await db.execute(select(Warehouse.code).where(Warehouse.tenant_id == tenant_id))
    existing_codes = {row[0] for row in existing.all()}
    added = 0
    for code, name_ar, wtype in DEFAULT_WAREHOUSES:
        if code not in existing_codes:
            db.add(Warehouse(tenant_id=tenant_id, code=code, name_ar=name_ar, warehouse_type=wtype))
            added += 1
    return added


def warehouse_dict(w: Warehouse) -> dict:
    return {
        "id": w.id, "code": w.code, "name_ar": w.name_ar,
        "warehouse_type": w.warehouse_type, "is_active": w.is_active,
        "is_default": w.code in DEFAULT_WAREHOUSE_CODES,
    }


class WarehouseIn(BaseModel):
    code: str
    name_ar: str
    warehouse_type: str = "general"


@router.get("")
async def list_warehouses(include_inactive: bool = False, db: AsyncSession = Depends(get_db)):
    q = select(Warehouse)
    if not include_inactive:
        q = q.where(Warehouse.is_active == True)  # noqa: E712
    rows = (await db.execute(q.order_by(Warehouse.id))).scalars().all()
    return [warehouse_dict(w) for w in rows]


@router.post("", status_code=201)
async def create_warehouse(data: WarehouseIn, db: AsyncSession = Depends(get_db)):
    tenant_id = current_tenant_id.get() or 1
    code = data.code.strip().upper()
    if not code:
        raise HTTPException(400, "كود المخزن مطلوب")
    exists = await db.scalar(
        select(Warehouse.id).where(Warehouse.tenant_id == tenant_id, Warehouse.code == code)
    )
    if exists:
        raise HTTPException(400, f"كود المخزن '{code}' مستخدم بالفعل")
    w = Warehouse(tenant_id=tenant_id, code=code, name_ar=data.name_ar, warehouse_type=data.warehouse_type)
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return warehouse_dict(w)


@router.put("/{warehouse_id}")
async def update_warehouse(warehouse_id: int, data: WarehouseIn, db: AsyncSession = Depends(get_db)):
    w = await db.get(Warehouse, warehouse_id)
    if not w:
        raise HTTPException(404, "المخزن غير موجود")
    new_code = data.code.strip().upper()
    if new_code != w.code:
        # أكواد المخازن الأساسية الـ8 (RAW/WIP/FG/...) معتمَد عليها بالاسم في
        # أماكن تانية كتير (get_or_create_warehouse_bin، استلام المستورد،
        # الهالك، النسالة...) — تغيير الكود هنا هيسيب لوجيك المكان الافتراضي
        # يدوّر على كود مش موجود ويعمل BinLocation يتيم (warehouse_id=None).
        if w.code in DEFAULT_WAREHOUSE_CODES:
            raise HTTPException(
                400,
                f"'{w.name_ar}' مخزن أساسي في النظام ({w.code}) — كوده لا يمكن تغييره لأن أجزاء "
                f"أخرى من النظام تعتمد عليه بهذا الكود تحديدًا. يمكنك تعديل الاسم فقط.",
            )
        exists = await db.scalar(
            select(Warehouse.id).where(Warehouse.tenant_id == w.tenant_id, Warehouse.code == new_code)
        )
        if exists:
            raise HTTPException(400, f"كود المخزن '{new_code}' مستخدم بالفعل")
    w.code = new_code
    w.name_ar = data.name_ar
    w.warehouse_type = data.warehouse_type
    await db.commit()
    await db.refresh(w)
    return warehouse_dict(w)


@router.delete("/{warehouse_id}")
async def delete_warehouse(warehouse_id: int, db: AsyncSession = Depends(get_db)):
    w = await db.get(Warehouse, warehouse_id)
    if not w:
        raise HTTPException(404, "المخزن غير موجود")
    if w.code in DEFAULT_WAREHOUSE_CODES:
        raise HTTPException(
            400,
            f"'{w.name_ar}' مخزن أساسي في النظام ({w.code}) ولا يمكن حذفه — "
            f"يمكنك تعطيله فقط ({'معطّل بالفعل' if not w.is_active else 'استخدم زر التعطيل'}).",
        )
    # ملحوظة: فحص "فيه رصيد فعلي" بالمعنى الكامل (StockQuant/حركات مربوطة
    # بالمخزن) هيتفعّل في محطة 12 لما ربط الحركات بمصدر/وجهة يتضاف. لحد
    # كده، أي مخزن مخصّص (مش من الـ8 الأساسيين) معناه لسه ملوش أي حركة
    # ارتبطت بيه فعليًا، فالحذف الفعلي آمن.
    await db.delete(w)
    await db.commit()
    return {"message": "تم حذف المخزن"}


@router.post("/{warehouse_id}/toggle-active")
async def toggle_warehouse_active(warehouse_id: int, db: AsyncSession = Depends(get_db)):
    w = await db.get(Warehouse, warehouse_id)
    if not w:
        raise HTTPException(404, "المخزن غير موجود")
    w.is_active = not w.is_active
    await db.commit()
    return warehouse_dict(w)
