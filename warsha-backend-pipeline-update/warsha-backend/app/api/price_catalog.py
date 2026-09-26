"""
Price Catalog API
كتالوج الأسعار — منتجات وخدمات بسعر التكلفة + هامش الربح
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict

from ..models.models import PriceCatalogItem, Equipment, Material, WorkOrder, CostLine, BOMLine
from app.core.database import get_db
from app.api.auth import require_any, require_role

router = APIRouter(prefix="/price-catalog", tags=["price_catalog"], dependencies=[Depends(require_any)])


# ── Schemas ──────────────────────────────────────────────────────────────────

class CatalogItemIn(BaseModel):
    code:         Optional[str]     = None
    name:         str
    category:     Optional[str]     = None
    description:  Optional[str]     = None
    unit:         str               = "قطعة"
    cost_price:   Decimal           = Decimal("0")
    margin_pct:   Decimal           = Decimal("25")
    selling_price: Optional[Decimal] = None      # لو اتبعت، يبقى سعر يدوي
    price_mode:   str               = "auto"     # auto | manual
    source_type:  Optional[str]     = None       # material | equipment
    source_id:    Optional[int]     = None
    is_active:    bool              = True


class CatalogItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id:            int
    code:          Optional[str]
    name:          str
    category:      Optional[str]
    description:   Optional[str]
    unit:          str
    cost_price:    Decimal
    margin_pct:    Decimal
    selling_price: Decimal
    price_mode:    str
    source_type:   Optional[str]
    source_id:     Optional[int]
    is_active:     bool
    stale:         Optional[bool] = None  # True لو السعر المخزن ≠ السعر الحي


# ── Helpers ──────────────────────────────────────────────────────────────────

def _calc_selling(cost: Decimal, margin: Decimal) -> Decimal:
    return round(cost * (1 + margin / 100), 2)


def _resolve_price(body) -> Decimal:
    """يحدد سعر البيع: يدوي لو price_mode=manual، أو محسوب لو auto"""
    if getattr(body, "price_mode", "auto") == "manual" and body.selling_price is not None:
        return Decimal(str(body.selling_price))
    return _calc_selling(body.cost_price, body.margin_pct)


def _get_tenant(user) -> int:
    # آمن: لو التوكن قديم بدون tenant_id، نرجع للـ context أو 1
    tid = user.get("tenant_id") if isinstance(user, dict) else None
    if tid:
        return tid
    try:
        from app.core.database import current_tenant_id
        return current_tenant_id.get() or 1
    except Exception:
        return 1


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("", response_model=list[CatalogItemOut])
async def list_items(
    q: Optional[str] = None,
    category: Optional[str] = None,
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin","accountant","production","viewer"))
):
    stmt = select(PriceCatalogItem).where(
        PriceCatalogItem.tenant_id == _get_tenant(user)
    )
    if active_only:
        stmt = stmt.where(PriceCatalogItem.is_active == True)
    if category:
        stmt = stmt.where(PriceCatalogItem.category == category)
    if q:
        stmt = stmt.where(or_(
            PriceCatalogItem.name.ilike(f"%{q}%"),
            PriceCatalogItem.code.ilike(f"%{q}%"),
            PriceCatalogItem.category.ilike(f"%{q}%"),
        ))
    stmt = stmt.order_by(PriceCatalogItem.category, PriceCatalogItem.name)
    items = (await db.execute(stmt)).scalars().all()

    # جلب التكاليف الحية لبنود auto-linked بالجملة
    from sqlalchemy import func as _sf
    auto_mat_ids = [it.source_id for it in items
                    if it.price_mode == "auto" and it.source_type == "material" and it.source_id]
    auto_eq_ids  = [it.source_id for it in items
                    if it.price_mode == "auto" and it.source_type == "equipment" and it.source_id]

    live_mat: dict[int, Decimal] = {}
    if auto_mat_ids:
        rows = (await db.execute(
            select(Material.id, Material.unit_cost).where(Material.id.in_(auto_mat_ids))
        )).all()
        live_mat = {r[0]: Decimal(str(r[1] or 0)) for r in rows}

    live_eq: dict[int, Decimal] = {}
    if auto_eq_ids:
        rows = (await db.execute(
            select(BOMLine.equipment_id, _sf.coalesce(_sf.sum(BOMLine.total_cost), 0))
            .where(BOMLine.equipment_id.in_(auto_eq_ids))
            .group_by(BOMLine.equipment_id)
        )).all()
        live_eq = {r[0]: Decimal(str(r[1] or 0)) for r in rows}

    out = []
    for it in items:
        live_cost = None
        if it.price_mode == "auto" and it.source_type == "material" and it.source_id in live_mat:
            live_cost = live_mat[it.source_id]
        elif it.price_mode == "auto" and it.source_type == "equipment" and it.source_id in live_eq:
            live_cost = live_eq[it.source_id]

        cost  = live_cost if live_cost is not None else it.cost_price
        sell  = _calc_selling(cost, it.margin_pct) if live_cost is not None else it.selling_price
        stale = (live_cost is not None and abs(live_cost - it.cost_price) > Decimal("0.001"))
        out.append({
            "id": it.id, "code": it.code, "name": it.name, "category": it.category,
            "description": it.description, "unit": it.unit, "price_mode": it.price_mode,
            "source_type": it.source_type, "source_id": it.source_id, "is_active": it.is_active,
            "margin_pct": it.margin_pct, "cost_price": cost, "selling_price": sell,
            "stale": stale if stale else None,
        })
    return out


@router.get("/categories")
async def list_categories(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin","accountant","production","viewer"))
):
    result = await db.execute(
        select(PriceCatalogItem.category)
        .where(PriceCatalogItem.tenant_id == _get_tenant(user))
        .where(PriceCatalogItem.category.isnot(None))
        .distinct()
        .order_by(PriceCatalogItem.category)
    )
    return [r for r in result.scalars().all() if r]


@router.get("/all-items")
async def all_items(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin", "accountant", "production", "viewer")),
):
    """
    عرض موحّد: أصناف الكتالوج اليدوية + كل المواد + كل المعدات.
    المواد والمعدات تظهر تلقائياً بسعر بيع مبدئي (تكلفة + هامش افتراضي).
    """
    tid = _get_tenant(user)
    DEFAULT_MARGIN = Decimal("25")
    out = []

    # 1) أصناف الكتالوج اليدوية
    cat = (await db.execute(
        select(PriceCatalogItem).where(
            PriceCatalogItem.tenant_id == tid,
            PriceCatalogItem.is_active == True,
        )
    )).scalars().all()
    for it in cat:
        out.append({
            "kind": "catalog", "ref_id": it.id, "code": it.code or "",
            "name": it.name, "category": it.category or "كتالوج",
            "unit": it.unit, "cost_price": float(it.cost_price or 0),
            "margin_pct": float(it.margin_pct or 0),
            "selling_price": float(it.selling_price or 0),
            "price_mode": it.price_mode, "editable": True,
            "description": it.description or "", "description_editable": True,
        })

    # 2) كل المواد
    mats = (await db.execute(select(Material).where(Material.tenant_id == tid))).scalars().all()
    for m in mats:
        cost = Decimal(str(m.unit_cost or 0))
        out.append({
            "kind": "material", "ref_id": m.id, "code": m.code or "",
            "name": m.name_ar, "category": "مادة خام",
            "unit": m.unit, "cost_price": float(cost),
            "margin_pct": float(DEFAULT_MARGIN),
            "selling_price": float(round(cost * (1 + DEFAULT_MARGIN / 100), 2)),
            "price_mode": "auto", "editable": True,
            "description": "", "description_editable": False,
        })

    # 3) المعدات الجذرية فقط (تكلفتها من الـ BOM) — الفرعيات تظهر عند الطلب فقط
    from app.models.models import BOMLine
    from sqlalchemy import func as _f
    eqs = (await db.execute(
        select(Equipment).where(
            Equipment.tenant_id == tid, Equipment.is_active == True, Equipment.parent_id.is_(None),
        )
    )).scalars().all()
    for eq in eqs:
        bom_total = (await db.execute(
            select(_f.coalesce(_f.sum(BOMLine.total_cost), 0)).where(BOMLine.equipment_id == eq.id)
        )).scalar() or 0
        cost = Decimal(str(bom_total))
        has_children = (await db.scalar(
            select(Equipment.id).where(Equipment.parent_id == eq.id).limit(1)
        )) is not None
        out.append({
            "kind": "equipment", "ref_id": eq.id, "code": eq.code or "",
            "name": eq.name_ar, "category": "معدات",
            "unit": "طقم", "cost_price": float(cost),
            "margin_pct": float(DEFAULT_MARGIN),
            "selling_price": float(round(cost * (1 + DEFAULT_MARGIN / 100), 2)),
            "price_mode": "auto", "editable": True,
            "description": eq.description or "", "description_editable": True,
            "has_children": has_children,
        })

    return out


@router.get("/{item_id}", response_model=CatalogItemOut)
async def get_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin","accountant","production","viewer"))
):
    item = await db.get(PriceCatalogItem, item_id)
    if not item or item.tenant_id != _get_tenant(user):
        raise HTTPException(404, "not found")
    return item


@router.post("", response_model=CatalogItemOut)
async def create_item(
    body: CatalogItemIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin","accountant"))
):
    item = PriceCatalogItem(
        tenant_id     = _get_tenant(user),
        code          = body.code,
        name          = body.name,
        category      = body.category,
        description   = body.description,
        unit          = body.unit,
        cost_price    = body.cost_price,
        margin_pct    = body.margin_pct,
        selling_price = _resolve_price(body),
        price_mode    = body.price_mode,
        source_type   = body.source_type,
        source_id     = body.source_id,
        is_active     = body.is_active,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.put("/{item_id}", response_model=CatalogItemOut)
async def update_item(
    item_id: int,
    body: CatalogItemIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin","accountant"))
):
    item = await db.get(PriceCatalogItem, item_id)
    if not item or item.tenant_id != _get_tenant(user):
        raise HTTPException(404, "not found")
    for k, v in body.model_dump().items():
        if k == "selling_price":
            continue  # نتعامل معاه تحت
        setattr(item, k, v)
    item.selling_price = _resolve_price(body)
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/{item_id}/price")
async def update_price(
    item_id: int,
    cost_price: Optional[Decimal] = None,
    margin_pct:  Optional[Decimal] = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin","accountant"))
):
    """تحديث سعر التكلفة أو نسبة الربح — سعر البيع يُحسب تلقائياً"""
    item = await db.get(PriceCatalogItem, item_id)
    if not item or item.tenant_id != _get_tenant(user):
        raise HTTPException(404, "not found")
    if cost_price is not None:
        item.cost_price = cost_price
    if margin_pct is not None:
        item.margin_pct = margin_pct
    item.selling_price = _calc_selling(item.cost_price, item.margin_pct)
    await db.commit()
    return {
        "id": item.id,
        "cost_price": item.cost_price,
        "margin_pct": item.margin_pct,
        "selling_price": item.selling_price
    }


@router.post("/refresh-auto-prices")
async def refresh_auto_prices(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin", "accountant"))
):
    """يحدّث cost_price وselling_price لكل بنود الكتالوج auto-linked بمصادرها الحية"""
    tid = _get_tenant(user)
    from sqlalchemy import func as _sf
    items = (await db.execute(
        select(PriceCatalogItem).where(
            PriceCatalogItem.tenant_id == tid,
            PriceCatalogItem.price_mode == "auto",
            PriceCatalogItem.source_type.isnot(None),
            PriceCatalogItem.source_id.isnot(None),
            PriceCatalogItem.is_active == True,
        )
    )).scalars().all()

    mat_ids = [it.source_id for it in items if it.source_type == "material"]
    eq_ids  = [it.source_id for it in items if it.source_type == "equipment"]

    live_mat: dict[int, Decimal] = {}
    if mat_ids:
        rows = (await db.execute(select(Material.id, Material.unit_cost).where(Material.id.in_(mat_ids)))).all()
        live_mat = {r[0]: Decimal(str(r[1] or 0)) for r in rows}

    live_eq: dict[int, Decimal] = {}
    if eq_ids:
        rows = (await db.execute(
            select(BOMLine.equipment_id, _sf.coalesce(_sf.sum(BOMLine.total_cost), 0))
            .where(BOMLine.equipment_id.in_(eq_ids))
            .group_by(BOMLine.equipment_id)
        )).all()
        live_eq = {r[0]: Decimal(str(r[1] or 0)) for r in rows}

    updated, details = 0, []
    for it in items:
        if it.source_type == "material" and it.source_id in live_mat:
            live = live_mat[it.source_id]
        elif it.source_type == "equipment" and it.source_id in live_eq:
            live = live_eq[it.source_id]
        else:
            continue
        if abs(live - it.cost_price) > Decimal("0.001"):
            details.append({"name": it.name, "old": float(it.cost_price), "new": float(live)})
            it.cost_price    = live
            it.selling_price = _calc_selling(live, it.margin_pct)
            updated += 1

    if updated:
        await db.commit()
    return {"updated": updated, "total": len(items), "details": details}


@router.delete("/{item_id}")
async def delete_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin"))
):
    item = await db.get(PriceCatalogItem, item_id)
    if not item or item.tenant_id != _get_tenant(user):
        raise HTTPException(404, "not found")
    item.is_active = False   # soft delete
    await db.commit()
    return {"ok": True}



# ── Upsert from source ────────────────────────────────────────────────────────

class UpsertFromSource(BaseModel):
    kind: str          # material | equipment
    ref_id: int
    name: str
    unit: str = "قطعة"
    cost_price: Decimal = Decimal("0")
    margin_pct: Decimal = Decimal("25")
    selling_price: Optional[Decimal] = None
    price_mode: str = "auto"


@router.post("/upsert-from-source")
async def upsert_from_source(body: UpsertFromSource, db: AsyncSession = Depends(get_db),
                             user=Depends(require_role("admin", "accountant"))):
    """يحفظ سعر بيع لمادة/معدة كصنف كتالوج مربوط بالمصدر (يعمل/يحدّث)."""
    tid = _get_tenant(user)
    src_type = "material" if body.kind == "material" else "equipment"
    # ندوّر على صنف كتالوج موجود مربوط بنفس المصدر
    existing = (await db.execute(
        select(PriceCatalogItem).where(
            PriceCatalogItem.tenant_id == tid,
            PriceCatalogItem.source_type == src_type,
            PriceCatalogItem.source_id == body.ref_id,
        )
    )).scalar_one_or_none()

    sell = (Decimal(str(body.selling_price)) if (body.price_mode == "manual" and body.selling_price is not None)
            else _calc_selling(body.cost_price, body.margin_pct))

    if existing:
        existing.cost_price = body.cost_price
        existing.margin_pct = body.margin_pct
        existing.selling_price = sell
        existing.price_mode = body.price_mode
        existing.name = body.name
        await db.commit()
        await db.refresh(existing)
        item = existing
    else:
        item = PriceCatalogItem(
            tenant_id=tid, code=f"{src_type[:3].upper()}-{body.ref_id}",
            name=body.name, category=("مادة خام" if src_type == "material" else "معدات"),
            unit=body.unit, cost_price=body.cost_price, margin_pct=body.margin_pct,
            selling_price=sell, price_mode=body.price_mode,
            source_type=src_type, source_id=body.ref_id, is_active=True,
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
    return {"id": item.id, "selling_price": float(item.selling_price), "saved": True}


@router.get("/cost-source/{source_type}/{source_id}")
async def cost_from_source(
    source_type: str,
    source_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin", "accountant", "production", "viewer")),
):
    """يجيب التكلفة المرجعية من مادة أو من BOM بتاع معدة"""
    tid = _get_tenant(user)
    if source_type == "material":
        mat = await db.get(Material, source_id)
        if not mat or mat.tenant_id != tid:
            raise HTTPException(404, "المادة غير موجودة")
        return {
            "cost_price": float(mat.unit_cost or 0),
            "name": mat.name_ar, "unit": mat.unit,
            "source_type": "material", "source_id": mat.id,
        }
    elif source_type == "equipment":
        eq = await db.get(Equipment, source_id)
        if not eq or eq.tenant_id != tid:
            raise HTTPException(404, "المعدة غير موجودة")
        from app.models.models import BOMLine
        from sqlalchemy import func as _f
        total = (await db.execute(
            select(_f.coalesce(_f.sum(BOMLine.total_cost), 0))
            .where(BOMLine.equipment_id == source_id)
        )).scalar() or 0
        return {
            "cost_price": float(total),
            "name": eq.name_ar, "unit": "طقم",
            "source_type": "equipment", "source_id": eq.id,
        }
    raise HTTPException(400, "نوع المصدر غير صحيح")


async def _equipment_search_item(db: AsyncSession, eq: "Equipment") -> dict:
    """يبني عنصر نتيجة بحث لمعدة — يُستخدم للجذور والفرعيات على حد سواء"""
    wo_stmt = (
        select(WorkOrder.actual_cost)
        .where(WorkOrder.equipment_id == eq.id, WorkOrder.status == "done")
        .order_by(WorkOrder.id.desc())
        .limit(1)
    )
    wo_row = (await db.execute(wo_stmt)).scalar_one_or_none()
    cost = float(wo_row) if wo_row else 0.0
    has_children = (await db.scalar(
        select(Equipment.id).where(Equipment.parent_id == eq.id).limit(1)
    )) is not None
    return {
        "source":       "equipment",
        "source_label": "المعدات",
        "id":           eq.id,
        "item_type":    "معدة",
        "model_no":     eq.code,
        "name":         eq.name_ar,
        "description":  eq.description or eq.name_ar,
        "unit":         eq.calc_unit or "طقم",
        "cost_price":   cost,
        "margin_pct":   25.0,
        "unit_price":   round(cost * 1.25, 2),
        "length_min":   float(eq.length_min) if eq.length_min else None,
        "length_max":   float(eq.length_max) if eq.length_max else None,
        "has_children": has_children,
    }


@router.get("/equipment-children/{eq_id}")
async def equipment_children(
    eq_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin","accountant","production","viewer"))
):
    """فرعيات معدة معيّنة — تُجلب فقط عند طلب المستخدم صريحاً (زر 'عرض الفرعيات')"""
    tid = _get_tenant(user)
    children = (await db.execute(
        select(Equipment).where(
            Equipment.tenant_id == tid, Equipment.parent_id == eq_id, Equipment.is_active == True,
        )
    )).scalars().all()
    return [await _equipment_search_item(db, c) for c in children]


# ── Unified Search (BOM + Costing + Catalog) ────────────────────────────────

@router.get("/search/unified")
async def unified_search(
    q: str = "",
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin","accountant","production","viewer"))
):
    """
    بحث موحد يرجع نتائج من 3 مصادر:
      1. كتالوج الأسعار  → selling_price جاهز
      2. المعدات (Equipment) → من أحدث WorkOrder بتكلفته الفعلية
      3. المواد (Materials) → unit_cost كتكلفة مرجعية
    """
    tid = _get_tenant(user)
    results = []

    # ── 1. Price Catalog ──────────────────────────────────────────
    stmt = select(PriceCatalogItem).where(
        PriceCatalogItem.tenant_id == tid,
        PriceCatalogItem.is_active == True,
    )
    if q:
        stmt = stmt.where(or_(
            PriceCatalogItem.name.ilike(f"%{q}%"),
            PriceCatalogItem.code.ilike(f"%{q}%"),
            PriceCatalogItem.category.ilike(f"%{q}%"),
        ))
    stmt = stmt.order_by(PriceCatalogItem.name).limit(30)
    for it in (await db.execute(stmt)).scalars():
        results.append({
            "source":       "catalog",
            "source_label": "كتالوج الأسعار",
            "id":           it.id,
            "item_type":    it.category or "",
            "model_no":     it.code or "",
            "name":         it.name,
            "description":  it.description or it.name,
            "unit":         it.unit,
            "cost_price":   float(it.cost_price),
            "margin_pct":   float(it.margin_pct),
            "unit_price":   float(it.selling_price),
        })

    # ── 2. Equipment ─────────────────────────────────────────────
    # نعرض فقط المعدات الجذرية (بدون فرعيات) — الفرعيات تظهر فقط عند ضغط المستخدم على "عرض الفرعيات"
    eq_stmt = select(Equipment).where(
        Equipment.tenant_id == tid,
        Equipment.is_active == True,
        Equipment.parent_id.is_(None),
    )
    if q:
        eq_stmt = eq_stmt.where(or_(
            Equipment.name_ar.ilike(f"%{q}%"),
            Equipment.name_en.ilike(f"%{q}%"),
            Equipment.code.ilike(f"%{q}%"),
        ))
    eq_stmt = eq_stmt.limit(20)
    for eq in (await db.execute(eq_stmt)).scalars():
        results.append(await _equipment_search_item(db, eq))

    # ── 3. Materials ──────────────────────────────────────────────
    mat_stmt = select(Material).where(Material.tenant_id == tid)
    if q:
        mat_stmt = mat_stmt.where(or_(
            Material.name_ar.ilike(f"%{q}%"),
            Material.name_en.ilike(f"%{q}%"),
            Material.code.ilike(f"%{q}%"),
        ))
    mat_stmt = mat_stmt.limit(20)
    for mat in (await db.execute(mat_stmt)).scalars():
        cost = float(mat.unit_cost or 0)
        results.append({
            "source":       "material",
            "source_label": "المواد",
            "id":           mat.id,
            "item_type":    "مادة",
            "model_no":     mat.code,
            "name":         mat.name_ar,
            "description":  mat.name_ar,
            "unit":         mat.unit,
            "cost_price":   cost,
            "margin_pct":   20.0,
            "unit_price":   round(cost * (1 + 20.0 / 100), 2),
        })
    return results
