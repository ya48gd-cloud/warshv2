"""
المخازن المنطقية (Warehouse) — محطة 10
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, current_tenant_id
from app.api.auth import require_any
from app.models.models import Warehouse, BinLocation, StockQuant, StockMovement

from app.api.inventory import require_inventory_write

router = APIRouter(prefix="/warehouses", tags=["warehouses"],
                   dependencies=[Depends(require_any), Depends(require_inventory_write)])

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


WAREHOUSE_TYPES = ("raw", "spare", "imported", "wip", "finished_goods",
                   "remnant", "scrap", "returns", "general")


class WarehouseIn(BaseModel):
    code: str
    name_ar: str
    warehouse_type: str = "general"

    @validator("code")
    def _code_len(cls, v):
        if v is not None and len(v.strip()) > 20:
            raise ValueError("كود المخزن طويل جداً (الحد الأقصى 20 حرف)")
        return v

    @validator("name_ar")
    def _name_required(cls, v):
        if v is None or not v.strip():
            raise ValueError("اسم المخزن مطلوب")
        if len(v.strip()) > 100:
            raise ValueError("اسم المخزن طويل جداً (الحد الأقصى 100 حرف)")
        return v.strip()

    @validator("warehouse_type")
    def _valid_type(cls, v):
        if v in (None, ""):
            return "general"
        if v not in WAREHOUSE_TYPES:
            raise ValueError(f"نوع المخزن غير صحيح — القيم المسموحة: {', '.join(WAREHOUSE_TYPES)}")
        return v


@router.get("")
async def list_warehouses(include_inactive: bool = False, db: AsyncSession = Depends(get_db)):
    q = select(Warehouse)
    if not include_inactive:
        q = q.where(Warehouse.is_active == True)  # noqa: E712
    rows = (await db.execute(q.order_by(Warehouse.id))).scalars().all()
    return [warehouse_dict(w) for w in rows]


@router.get("/stock-summary")
async def warehouses_stock_summary(include_inactive: bool = False, db: AsyncSession = Depends(get_db)):
    """
    ملخص رصيد كل مخزن: عدد الأصناف اللي ليها رصيد + القيمة الإجمالية
    (الكمية × سعر الوحدة). جمع الكميات نفسه مالوش معنى (وحدات مختلفة).
    """
    from app.models.models import Material
    tenant_id = current_tenant_id.get()
    q = select(Warehouse).where(Warehouse.tenant_id == tenant_id)
    if not include_inactive:
        q = q.where(Warehouse.is_active == True)  # noqa: E712
    warehouses = (await db.execute(q.order_by(Warehouse.id))).scalars().all()

    per_mat = (await db.execute(
        select(BinLocation.warehouse_id, StockQuant.material_id,
               func.sum(StockQuant.qty), Material.unit_cost)
        .join(BinLocation, BinLocation.id == StockQuant.location_id)
        .join(Material, Material.id == StockQuant.material_id)
        .where(StockQuant.tenant_id == tenant_id, BinLocation.tenant_id == tenant_id,
               BinLocation.warehouse_id.is_not(None))
        .group_by(BinLocation.warehouse_id, StockQuant.material_id, Material.unit_cost)
    )).all()
    agg: dict = {}
    for wid, _mid, qty, cost in per_mat:
        qty = float(qty or 0)
        if abs(qty) < 1e-9:
            continue
        a = agg.setdefault(wid, {"items_count": 0, "total_value": 0.0})
        a["items_count"] += 1
        a["total_value"] += qty * float(cost or 0)

    out = []
    for w in warehouses:
        a = agg.get(w.id, {"items_count": 0, "total_value": 0.0})
        out.append({**warehouse_dict(w), "items_count": a["items_count"],
                    "total_value": round(a["total_value"], 2)})
    return out


@router.get("/{warehouse_id}/stock")
async def warehouse_stock(warehouse_id: int, db: AsyncSession = Depends(get_db)):
    """المواد اللي ليها رصيد في مخزن معيّن (مجموع كل مواقعه) + القيمة."""
    from app.models.models import Material
    tenant_id = current_tenant_id.get()
    w = await db.scalar(
        select(Warehouse).where(Warehouse.id == warehouse_id, Warehouse.tenant_id == tenant_id)
    )
    if not w:
        raise HTTPException(404, "المخزن غير موجود")
    rows = (await db.execute(
        select(Material.id, Material.code, Material.name_ar, Material.unit,
               Material.unit_cost, func.sum(StockQuant.qty))
        .join(StockQuant, StockQuant.material_id == Material.id)
        .join(BinLocation, BinLocation.id == StockQuant.location_id)
        .where(BinLocation.warehouse_id == w.id, StockQuant.tenant_id == tenant_id,
               Material.tenant_id == tenant_id)
        .group_by(Material.id, Material.code, Material.name_ar, Material.unit, Material.unit_cost)
        .order_by(Material.name_ar)
    )).all()
    items = []
    for mid, code, name, unit, cost, qty in rows:
        qty = float(qty or 0)
        if abs(qty) < 1e-9:
            continue
        cost = float(cost or 0)
        items.append({
            "material_id": mid, "code": code, "name_ar": name, "unit": unit,
            "qty": qty, "unit_cost": cost, "value": round(qty * cost, 2),
        })
    return {
        "warehouse": warehouse_dict(w),
        "items": items,
        "items_count": len(items),
        "total_value": round(sum(i["value"] for i in items), 2),
    }


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
    # محطة 12 ربطت الحركات بالمواقع (BinLocation.warehouse_id) — مخزن
    # مخصّص عليه مواقع فيها رصيد أو حركات تاريخية مينفعش يتمسح فعليًا
    # (هيسيب مواقع/أرصدة يتيمة أو يفشل بـ FK). التعطيل هو البديل الآمن.
    bin_ids = [r[0] for r in (await db.execute(
        select(BinLocation.id).where(BinLocation.warehouse_id == w.id)
    )).all()]
    if bin_ids:
        has_stock = await db.scalar(
            select(func.count()).select_from(StockQuant).where(
                StockQuant.location_id.in_(bin_ids), StockQuant.qty != 0,
            )
        )
        has_moves = await db.scalar(
            select(func.count()).select_from(StockMovement).where(
                StockMovement.source_location_id.in_(bin_ids)
                | StockMovement.destination_location_id.in_(bin_ids)
            )
        )
        if has_stock or has_moves:
            raise HTTPException(
                400,
                f"لا يمكن حذف '{w.name_ar}' لأن عليه أرصدة أو حركات مخزون مسجلة — يمكنك تعطيله بدلاً من ذلك.",
            )
        # مواقع فاضية تمامًا: نفك ربطها بالمخزن بدل ما تفضل مشاورة على id محذوف
        for loc in (await db.execute(
            select(BinLocation).where(BinLocation.id.in_(bin_ids))
        )).scalars().all():
            loc.warehouse_id = None
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
