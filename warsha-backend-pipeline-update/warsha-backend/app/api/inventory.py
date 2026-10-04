from datetime import date
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, delete as sa_delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, validator

from app.core.database import get_db
from app.api.auth import require_any
from app.services.inventory import InventoryService
from app.services.nesting import (
    build_cutting_plan, build_cutting_plan_from_stock,
    build_bar_cutting_plan, build_bar_cutting_plan_from_stock,
)
from app.models.models import (
    Material, StockMovement, BOMLine,
    MaterialBinStock, MaterialReservation, StockBatch,
    PurchaseInvoiceLine, PurchaseOrderLine, MaterialPriceHistory,
    SheetStockItem, BarStockItem, CostLine, WorkOrder, StockQuant, BinLocation,
)

# ── RBAC للكتابة ──────────────────────────────────────────────────────────
# كل راوترات المخزون كانت محمية بـ require_any بس (أي مستخدم مسجّل، حتى
# viewer) — يعني مستخدم "مشاهد" يقدر يسجّل حركات، يعكسها، يمسح مواد، إلخ
# بطلب API مباشر رغم إن الواجهة مخبية الأزرار. الأدوار اللي ليها صلاحية
# كتابة المخزون هي نفسها ROLE_WRITE['inventory'] في الفرونت (admin/production).
INVENTORY_WRITE_ROLES = ("admin", "production")
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
# حسابات بحتة (مفيش أي كتابة في الداتابيز) — مسموحة لأي مستخدم مسجّل
_READONLY_POST_PATHS = ("/inventory/cutting-plan", "/inventory/bar-cutting-plan")


async def require_inventory_write(request: Request, user: dict = Depends(require_any)) -> dict:
    if request.method.upper() in _READ_METHODS:
        return user
    path = request.url.path.rstrip("/")
    if any(path.endswith(p) for p in _READONLY_POST_PATHS):
        return user
    if user.get("role") not in INVENTORY_WRITE_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"صلاحية '{user.get('role')}' غير كافية لتعديل المخزون. مطلوب: {list(INVENTORY_WRITE_ROLES)}",
        )
    return user


router = APIRouter(prefix="/inventory", tags=["inventory"],
                   dependencies=[Depends(require_any), Depends(require_inventory_write)])


async def _next_material_code(db: AsyncSession) -> str:
    count = await db.scalar(select(func.count()).select_from(Material))
    n = (count or 0) + 1
    while True:
        code = f"MAT-{n:04d}"
        exists = await db.scalar(select(Material.id).where(Material.code == code))
        if not exists:
            return code
        n += 1


_MATERIAL_KINDS = ("general", "plate", "bar", "consumable")
_MATERIAL_POLICIES = {
    "stock_item_type": ("raw_material", "finished_good", "spare_part", "imported_equipment", "consumable"),
    "tracking": ("none", "lot", "serial"),
    "valuation_method": ("standard", "moving_average", "last_price", "fifo"),
    "removal_strategy": ("fifo", "lifo", "fefo", "manual"),
}


def _check_policy(name: str, v):
    # قيمة فاضية = سيب الافتراضي (نفس السلوك القديم لـ None)
    if v in (None, ""):
        return None
    allowed = _MATERIAL_POLICIES[name]
    if v not in allowed:
        raise ValueError(f"قيمة غير صحيحة لـ {name} — القيم المسموحة: {', '.join(allowed)}")
    return v


class MaterialCreate(BaseModel):
    code: Optional[str] = None
    name_ar: str
    name_en: Optional[str] = ""   # اختياري — يُعاد بالعربي لو فارغ
    unit: str
    unit_cost: Decimal
    reorder_level: Decimal = Decimal("0")
    category_id: Optional[int] = None   # يُستخدم أول صنف للمستأجر لو فارغ
    supplier: Optional[str] = None
    material_kind: str = "general"  # general | plate | bar | consumable
    is_plate: bool = False
    thickness_mm: Optional[Decimal] = None
    density_kg_m2: Optional[Decimal] = None
    plate_length_cm: Optional[Decimal] = None
    plate_width_cm: Optional[Decimal] = None
    plate_weight_kg: Optional[Decimal] = None
    bar_length_cm: Optional[Decimal] = None
    bar_weight_kg: Optional[Decimal] = None
    weight_per_meter_kg: Optional[Decimal] = None
    supplier_id: Optional[int] = None
    # محطة 9: سياسات الصنف — اختيارية، بقيم افتراضية آمنة لو اتسابت فاضية
    stock_item_type: Optional[str] = None   # raw_material | finished_good | spare_part | imported_equipment | consumable
    tracking: Optional[str] = None          # none | lot | serial
    valuation_method: Optional[str] = None  # standard | moving_average | last_price | fifo
    removal_strategy: Optional[str] = None  # fifo | lifo | fefo | manual

    @validator("unit_cost")
    def _unit_cost_not_negative(cls, v):
        # سعر سالب هيتسرب لمتوسط التكلفة المرجّح في كل استلام لاحق (post_receipt)
        # ويبوّظ تقييم المخزون وتكلفة البضاعة المباعة بشكل دائم وصامت.
        if v is not None and v < 0:
            raise ValueError("سعر الوحدة لا يمكن أن يكون سالباً")
        return v

    @validator("reorder_level")
    def _reorder_level_not_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("حد إعادة الطلب لا يمكن أن يكون سالباً")
        return v

    @validator("name_ar")
    def _name_ar_required(cls, v):
        if v is None or not str(v).strip():
            raise ValueError("اسم المادة مطلوب")
        if len(v) > 200:
            raise ValueError("اسم المادة طويل جداً (الحد الأقصى 200 حرف)")
        return v.strip()

    @validator("unit")
    def _unit_required(cls, v):
        if v is None or not str(v).strip():
            raise ValueError("وحدة القياس مطلوبة")
        if len(v.strip()) > 30:
            raise ValueError("وحدة القياس طويلة جداً (الحد الأقصى 30 حرف)")
        return v.strip()

    @validator("code")
    def _code_max_len(cls, v):
        if v is not None and len(v.strip()) > 50:
            raise ValueError("كود المادة طويل جداً (الحد الأقصى 50 حرف)")
        return v

    @validator("thickness_mm", "density_kg_m2", "plate_length_cm", "plate_width_cm",
               "plate_weight_kg", "bar_length_cm", "bar_weight_kg", "weight_per_meter_kg")
    def _dims_not_negative(cls, v):
        # أبعاد/أوزان سالبة كانت بتنتج كثافة أو وزن متر سالب (enrich_material_shape)
        # وبالتالي كميات صرف سالبة/مقلوبة في record_movement.
        if v is not None and v < 0:
            raise ValueError("الأبعاد والأوزان لا يمكن أن تكون سالبة")
        return v

    @validator("material_kind")
    def _valid_material_kind(cls, v):
        if v in (None, ""):
            return "general"
        if v not in _MATERIAL_KINDS:
            raise ValueError(f"نوع الخامة غير صحيح — القيم المسموحة: {', '.join(_MATERIAL_KINDS)}")
        return v

    @validator("stock_item_type", pre=True)
    def _valid_stock_item_type(cls, v):
        return _check_policy("stock_item_type", v)

    @validator("tracking", pre=True)
    def _valid_tracking(cls, v):
        return _check_policy("tracking", v)

    @validator("valuation_method", pre=True)
    def _valid_valuation_method(cls, v):
        return _check_policy("valuation_method", v)

    @validator("removal_strategy", pre=True)
    def _valid_removal_strategy(cls, v):
        return _check_policy("removal_strategy", v)


class MovementCreate(BaseModel):
    material_id: int
    movement_type: str          # in | out
    qty: Decimal                # الوزن / الكمية دائماً بالوحدة الأساسية
    unit_cost: Decimal
    reference: Optional[str] = None
    notes: Optional[str] = None
    movement_date: Optional[date] = None   # يُستخدم اليوم إن لم يُذكر
    # حقول السحب
    withdrawal_unit: Optional[str] = None     # weight | piece | plate_area
    withdrawal_type: Optional[str] = None     # legacy alias
    destination: Optional[str] = None
    destination_ref: Optional[str] = None
    pieces_count: Optional[int] = None
    weight_per_piece: Optional[Decimal] = None
    work_order_id: Optional[int] = None
    # حقول الألواح الفولاذية (للوارد: أبعاد اللوح الواحد + وزنه)
    plate_length_cm: Optional[Decimal] = None
    plate_width_cm: Optional[Decimal] = None
    plate_weight_kg: Optional[Decimal] = None  # الوزن الكلي للوح (لحساب الكثافة)
    plate_area_m2: Optional[Decimal] = None    # مساحة إجمالية (تُحسب في الفرونت إند)
    # حقول BOM الألواح (للصادر: أبعاد القطع المطلوبة)
    plate_pieces: Optional[int] = None         # عدد الألواح/القطع
    bar_length_cm: Optional[Decimal] = None
    waste_g: Optional[Decimal] = None
    customer_id: Optional[int] = None
    source: Optional[str] = None
    # المخزن اللي الحركة عليه: "in" = إلى مخزن، "out" = من مخزن.
    # لو مش متبعوت → مخزن الخامات (RAW) زي السلوك القديم بالظبط.
    warehouse_id: Optional[int] = None

    @validator("movement_type")
    def _valid_movement_type(cls, v):
        # قبل هذا الفحص، أي قيمة غير "in" كانت تُعامَل ضمنيًا كـ"out" (else
        # branch في record_movement) — خطأ إملائي في الفرونت إند (أو طلب
        # مُتلاعَب به) كان يُنقص المخزون بدل ما يزوّده من غير أي تحذير.
        if v not in ("in", "out"):
            raise ValueError("movement_type يجب أن يكون 'in' أو 'out'")
        return v

    @validator("pieces_count", "plate_pieces")
    def _counts_not_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("العدد لا يمكن أن يكون سالباً")
        return v

    @validator("weight_per_piece", "plate_length_cm", "plate_width_cm", "plate_weight_kg",
               "plate_area_m2", "bar_length_cm", "waste_g")
    def _measures_not_negative(cls, v):
        # waste_g سالب كان بيُطرح من كمية الصادر (plate_area / bar_length) فيصرف
        # أقل من المطلوب فعليًا؛ وأبعاد سالبة كانت بتقلب إشارة الكمية المحسوبة.
        if v is not None and v < 0:
            raise ValueError("الأبعاد والأوزان لا يمكن أن تكون سالبة")
        return v


async def _warehouse_bin_for(db: AsyncSession, warehouse_id: int, *, label: str = "المخزن"):
    """
    يحوّل warehouse_id (اختيار المستخدم) للـ BinLocation الافتراضي بتاعه
    (<CODE>-DEFAULT) — مع فحص صريح إن المخزن تابع للـ tenant الحالي وفعّال.
    يرجّع (warehouse, bin_location).
    """
    from app.core.database import current_tenant_id as _ctid_wh
    from app.models.models import Warehouse
    from app.services.inventory_ledger import get_or_create_warehouse_bin
    tenant_id = _ctid_wh.get()
    wh = await db.scalar(
        select(Warehouse).where(Warehouse.id == warehouse_id, Warehouse.tenant_id == tenant_id)
    )
    if not wh:
        raise HTTPException(404, f"{label} المحدد غير موجود")
    if not wh.is_active:
        raise HTTPException(400, f"{label} '{wh.name_ar}' معطّل — فعّله أولاً أو اختر مخزناً آخر")
    loc = await get_or_create_warehouse_bin(db, tenant_id, wh.code)
    return wh, loc


async def _warehouse_qty(db: AsyncSession, material_id: int, warehouse_id: int) -> Decimal:
    """رصيد مادة في مخزن معيّن = مجموع StockQuant على كل مواقع (bins) المخزن."""
    q = await db.scalar(
        select(func.coalesce(func.sum(StockQuant.qty), 0))
        .join(BinLocation, BinLocation.id == StockQuant.location_id)
        .where(StockQuant.material_id == material_id, BinLocation.warehouse_id == warehouse_id)
    )
    return Decimal(str(q or 0))


async def _location_qty(db: AsyncSession, material_id: int, location_id: int) -> Decimal:
    q = await db.scalar(
        select(func.coalesce(func.sum(StockQuant.qty), 0))
        .where(StockQuant.material_id == material_id, StockQuant.location_id == location_id)
    )
    return Decimal(str(q or 0))


def enrich_material_shape(payload: dict) -> dict:
    kind = payload.get("material_kind") or ("plate" if payload.get("is_plate") else "general")
    payload["material_kind"] = kind
    payload["is_plate"] = bool(payload.get("is_plate") or kind == "plate")

    if kind == "plate":
        length = payload.get("plate_length_cm")
        width = payload.get("plate_width_cm")
        weight = payload.get("plate_weight_kg")
        if length and width and weight and Decimal(str(length)) > 0 and Decimal(str(width)) > 0:
            area = (Decimal(str(length)) / 100) * (Decimal(str(width)) / 100)
            payload["density_kg_m2"] = Decimal(str(weight)) / area

    if kind == "bar":
        length = payload.get("bar_length_cm")
        weight = payload.get("bar_weight_kg")
        if length and weight and Decimal(str(length)) > 0:
            payload["weight_per_meter_kg"] = Decimal(str(weight)) / (Decimal(str(length)) / 100)

    return payload


@router.get("/materials/next-code")
async def get_next_material_code(db: AsyncSession = Depends(get_db)):
    """إرجاع الكود التالي المتاح لمادة جديدة"""
    code = await _next_material_code(db)
    return {"code": code}


@router.post("/materials", status_code=201)
async def create_material(data: MaterialCreate, db: AsyncSession = Depends(get_db)):
    payload = enrich_material_shape(data.model_dump())
    # محطة 9: لو سياسات الصنف اتسابت فاضية، سيبها لقيم SQLAlchemy الافتراضية
    # بدل ما نمرر None صراحةً (اللي كان هيكتب NULL فوق الـ default ويكسر NOT NULL)
    for _f in ("stock_item_type", "tracking", "valuation_method", "removal_strategy"):
        if payload.get(_f) is None:
            payload.pop(_f, None)
    code = (payload.get('code') or '').strip() or await _next_material_code(db)
    # تحقق من عدم تكرار الكود
    existing = await db.scalar(select(Material.id).where(Material.code == code))
    if existing:
        raise HTTPException(400, f"كود المادة '{code}' مستخدم من قبل")
    payload['code'] = code
    from app.core.database import current_tenant_id as _ctid_mat
    from app.models.models import MaterialCategory
    payload['tenant_id'] = _ctid_mat.get() or 1
    # default name_en to name_ar if empty
    if not payload.get('name_en'):
        payload['name_en'] = payload.get('name_ar', '')
    # if category_id missing, use first available category for this tenant
    if payload.get('category_id') is None:
        cat_id = await db.scalar(
            select(MaterialCategory.id)
            .where(MaterialCategory.tenant_id == payload['tenant_id'])
            .limit(1)
        )
        payload['category_id'] = cat_id or 1  # fallback 1 (seeded in tests)
    else:
        # لو category_id اتبعت صراحة، لازم يتأكد إنها فعلاً فئة تابعة لنفس
        # الـ tenant — وإلا مادة تنانت A ممكن تتربط بفئة تنانت B (رقم ID
        # صحيح بس مش بتاعه)، وبعدين تنانت B يقدر يمسح الفئة دي (عدّاد
        # موادها هيبقى صفر من وجهة نظره) ويسيب مادة تنانت A بفئة يتيمة.
        owns_cat = await db.scalar(
            select(MaterialCategory.id).where(
                MaterialCategory.id == payload['category_id'],
                MaterialCategory.tenant_id == payload['tenant_id'],
            )
        )
        if not owns_cat:
            raise HTTPException(400, "الفئة المحددة غير موجودة")
    if payload.get('supplier_id') is not None:
        from app.models.models import Supplier as _Supplier_mat
        owns_sup = await db.scalar(
            select(_Supplier_mat.id).where(
                _Supplier_mat.id == payload['supplier_id'],
                _Supplier_mat.tenant_id == payload['tenant_id'],
            )
        )
        if not owns_sup:
            raise HTTPException(400, "المورد المحدد غير موجود")
    mat = Material(**payload, stock_qty=Decimal("0"))
    db.add(mat)
    await db.commit()
    await db.refresh(mat)
    return mat


@router.get("/materials")
async def list_materials(low_stock_only: bool = False, db: AsyncSession = Depends(get_db)):
    return await InventoryService.list_materials(db, low_stock_only)


@router.get("/materials/{material_id}")
async def get_material(material_id: int, db: AsyncSession = Depends(get_db)):
    mat = await InventoryService.get_material(db, material_id)
    if not mat: raise HTTPException(404, "Material not found")
    return mat


@router.get("/materials/{material_id}/quants")
async def material_quants(material_id: int, db: AsyncSession = Depends(get_db)):
    """محطة 12: توزيع رصيد المادة على المواقع المختلفة (StockQuant لكل موقع)."""
    from app.core.database import current_tenant_id as _ctid_q
    from app.models.models import Warehouse
    tenant_id = _ctid_q.get()
    mat = await db.scalar(
        select(Material).where(Material.id == material_id, Material.tenant_id == tenant_id)
    )
    if not mat:
        raise HTTPException(404, "المادة غير موجودة")
    rows = (await db.execute(
        select(StockQuant, BinLocation.code, BinLocation.name_ar, Warehouse.id, Warehouse.name_ar, Warehouse.code)
        .join(BinLocation, BinLocation.id == StockQuant.location_id)
        .outerjoin(Warehouse, Warehouse.id == BinLocation.warehouse_id)
        .where(StockQuant.material_id == material_id, StockQuant.qty != 0,
               StockQuant.tenant_id == tenant_id)
    )).all()
    return [
        {
            "location_id": q.location_id, "location_code": code, "location_name": name_ar,
            "warehouse_id": wh_id, "warehouse_name": wh_name, "warehouse_code": wh_code,
            "lot_id": q.lot_id, "qty": float(q.qty),
        }
        for q, code, name_ar, wh_id, wh_name, wh_code in rows
    ]


@router.get("/warehouse-distribution")
async def warehouse_distribution(db: AsyncSession = Depends(get_db)):
    """
    توزيع رصيد كل المواد على المخازن في طلب واحد (لشاشة المواد):
    {material_id: [{warehouse_id, warehouse_name, warehouse_code, qty}, ...]}
    """
    from app.core.database import current_tenant_id as _ctid_d
    from app.models.models import Warehouse
    tenant_id = _ctid_d.get()
    rows = (await db.execute(
        select(StockQuant.material_id, Warehouse.id, Warehouse.name_ar, Warehouse.code,
               func.sum(StockQuant.qty))
        .join(BinLocation, BinLocation.id == StockQuant.location_id)
        .outerjoin(Warehouse, Warehouse.id == BinLocation.warehouse_id)
        .where(StockQuant.tenant_id == tenant_id)
        .group_by(StockQuant.material_id, Warehouse.id, Warehouse.name_ar, Warehouse.code)
    )).all()
    out: dict = {}
    for mid, wid, wname, wcode, qty in rows:
        if not qty:
            continue
        out.setdefault(str(mid), []).append({
            "warehouse_id": wid, "warehouse_name": wname or "بدون مخزن",
            "warehouse_code": wcode, "qty": float(qty),
        })
    return out


@router.get("/materials/{material_id}/stock-check")
async def material_stock_check(material_id: int, db: AsyncSession = Depends(get_db)):
    """
    محطة 11: فحص سريع إن Material.stock_qty مطابق لمجموع StockQuant الخاص
    بالمادة دي. تقرير التسوية الكامل (بين كل المصادر: حركات/دفعات/quants)
    هيتضاف في محطة 13 — ده فحص محلي بسيط بين المصدرين الجداد بس.
    """
    mat = await db.get(Material, material_id)
    if not mat:
        raise HTTPException(404, "المادة غير موجودة")
    quant_total = await db.scalar(
        select(func.sum(StockQuant.qty)).where(StockQuant.material_id == material_id)
    ) or Decimal("0")
    stock_qty = mat.stock_qty or Decimal("0")
    return {
        "material_id": material_id,
        "stock_qty": float(stock_qty),
        "quant_total": float(quant_total),
        "matches": abs(stock_qty - quant_total) < Decimal("0.001"),
    }


@router.put("/materials/{material_id}")
async def update_material(material_id: int, data: MaterialCreate, db: AsyncSession = Depends(get_db)):
    mat = await db.get(Material, material_id)
    if not mat: raise HTTPException(404)

    payload = enrich_material_shape(data.model_dump())

    # محطة 9: سياسات الصنف — لو مش مبعوتة في التعديل، سيب القيمة الحالية
    # زي ما هي (بدل ما نمسحها لـ NULL ونكسر NOT NULL)
    for _f in ("stock_item_type", "tracking", "valuation_method", "removal_strategy"):
        if payload.get(_f) is None:
            payload.pop(_f, None)

    # تحقق من عدم تكرار الكود عند التعديل
    new_code = (payload.get('code') or '').strip()
    if not new_code:
        payload['code'] = mat.code  # حافظ على الكود الموجود لو لم يُرسَل
    else:
        payload['code'] = new_code  # نفس الكود اللي اتفحص تكراره (بدون مسافات زيادة)
    if not payload.get('name_en'):
        payload['name_en'] = payload.get('name_ar', '')  # نفس سلوك الإنشاء
    if not payload.get('category_id'):
        if mat.category_id:
            payload['category_id'] = mat.category_id  # حافظ على الفئة الحالية لو لم تُرسَل
        else:
            raise HTTPException(400, "الفئة مطلوبة")
    elif payload['category_id'] != mat.category_id:
        # نفس فحص create_material: لو الفئة اتغيرت، لازم تتأكد إنها فعلاً
        # فئة تابعة لنفس الـ tenant (وإلا مادة تربط بفئة تنانت تاني).
        from app.models.models import MaterialCategory as _MatCat_upd
        owns_cat = await db.scalar(
            select(_MatCat_upd.id).where(
                _MatCat_upd.id == payload['category_id'],
                _MatCat_upd.tenant_id == mat.tenant_id,
            )
        )
        if not owns_cat:
            raise HTTPException(400, "الفئة المحددة غير موجودة")
    if payload.get('supplier_id') is not None and payload['supplier_id'] != mat.supplier_id:
        from app.models.models import Supplier as _Supplier_upd
        owns_sup = await db.scalar(
            select(_Supplier_upd.id).where(
                _Supplier_upd.id == payload['supplier_id'],
                _Supplier_upd.tenant_id == mat.tenant_id,
            )
        )
        if not owns_sup:
            raise HTTPException(400, "المورد المحدد غير موجود")
    if new_code and new_code != mat.code:
        dup = await db.scalar(select(Material.id).where(Material.code == new_code))
        if dup:
            raise HTTPException(400, f"كود المادة '{new_code}' مستخدم من قبل")

    # Preserve existing density if plate dims not fully provided in this update
    if payload.get("is_plate") or payload.get("material_kind") == "plate":
        if not payload.get("density_kg_m2") and mat.density_kg_m2:
            payload["density_kg_m2"] = mat.density_kg_m2

    # Preserve existing weight_per_meter_kg if bar dims not fully provided
    if payload.get("material_kind") == "bar":
        if not payload.get("weight_per_meter_kg") and mat.weight_per_meter_kg:
            payload["weight_per_meter_kg"] = mat.weight_per_meter_kg

    # تغيير السعر من شاشة التعديل لازم يسيب أثر في سجل الأسعار زي /price
    new_cost = payload.get("unit_cost")
    if new_cost is not None and Decimal(str(new_cost)) != Decimal(str(mat.unit_cost or 0)):
        db.add(MaterialPriceHistory(
            tenant_id=mat.tenant_id, material_id=mat.id,
            old_price=mat.unit_cost, new_price=new_cost,
        ))

    for k, v in payload.items():
        setattr(mat, k, v)
    await db.commit()
    await db.refresh(mat)
    # Return full object so frontend shows updated density
    return {
        "id":                  mat.id,
        "code":                mat.code,
        "name_ar":             mat.name_ar,
        "name_en":             mat.name_en,
        "unit":                mat.unit,
        "unit_cost":           float(mat.unit_cost or 0),
        "stock_qty":           float(mat.stock_qty or 0),
        "reorder_level":       float(mat.reorder_level or 0),
        "material_kind":       mat.material_kind,
        "is_plate":            mat.is_plate,
        "thickness_mm":        float(mat.thickness_mm) if mat.thickness_mm else None,
        "density_kg_m2":       float(mat.density_kg_m2) if mat.density_kg_m2 else None,
        "plate_length_cm":     float(mat.plate_length_cm) if mat.plate_length_cm else None,
        "plate_width_cm":      float(mat.plate_width_cm) if mat.plate_width_cm else None,
        "plate_weight_kg":     float(mat.plate_weight_kg) if mat.plate_weight_kg else None,
        "weight_per_meter_kg": float(mat.weight_per_meter_kg) if mat.weight_per_meter_kg else None,
        "bar_length_cm":       float(mat.bar_length_cm) if mat.bar_length_cm else None,
        "supplier_id":         mat.supplier_id,
        "stock_item_type":     mat.stock_item_type,
        "tracking":            mat.tracking,
        "valuation_method":    mat.valuation_method,
        "removal_strategy":    mat.removal_strategy,
    }


@router.post("/movements", status_code=201)
async def record_movement(data: MovementCreate, db: AsyncSession = Depends(get_db)):
    mat = await db.get(Material, data.material_id)
    if not mat:
        raise HTTPException(404, "Material not found")

    # المراجع الخارجية (أمر إنتاج / عميل) بتوصل كـ IDs خام من العميل — لازم
    # تكون موجودة وتابعة لنفس الـ tenant (db.get مفلتر تلقائيًا بالـ tenant)،
    # وإلا الحركة بتتربط بأمر/عميل تنانت تاني أو ID وهمي.
    if data.work_order_id is not None and not await db.get(WorkOrder, data.work_order_id):
        raise HTTPException(404, "أمر الإنتاج المحدد غير موجود")
    if data.customer_id is not None:
        from app.models.models import Customer as _Customer_mv
        if not await db.get(_Customer_mv, data.customer_id):
            raise HTTPException(404, "العميل المحدد غير موجود")

    # ── helpers ────────────────────────────────────────────────────
    def D(v):
        """تحويل آمن إلى Decimal، ويرجّع None لو القيمة فاضية"""
        if v is None or v == "":
            return None
        return Decimal(str(v))

    actual_qty       = data.qty
    withdrawal_unit  = data.withdrawal_unit or "weight"
    pieces_count     = data.pieces_count
    weight_per_piece = data.weight_per_piece
    plate_area_m2    = None
    waste_kg         = (D(data.waste_g) / 1000) if data.waste_g else Decimal("0")

    # ── الوارد (IN) ────────────────────────────────────────────────
    if data.movement_type == "in":
        length_cm    = D(data.plate_length_cm)
        width_cm     = D(data.plate_width_cm)
        plate_weight = D(data.plate_weight_kg)
        plate_pcs    = Decimal(str(pieces_count or 1))

        # وارد ألواح: يحسب المساحة والكثافة ويحدّث بيانات الماده
        if withdrawal_unit in ("plate_in", "plate_count", "plate_area") \
                and length_cm and width_cm and plate_weight and plate_weight > 0:
            area_m2 = (length_cm / 100) * (width_cm / 100)
            plate_area_m2    = area_m2 * plate_pcs
            computed_density = plate_weight / area_m2
            # تحديث بيانات الماده (الكثافة تُحسب من اللوح الواحد)
            mat.density_kg_m2   = computed_density
            mat.is_plate        = True
            mat.material_kind   = "plate"
            mat.plate_length_cm = length_cm
            mat.plate_width_cm  = width_cm
            mat.plate_weight_kg = plate_weight
            # الوزن الإجمالي الوارد = وزن اللوح × العدد
            actual_qty = plate_weight * plate_pcs

        # وارد قطع: العدد × وزن القطعة
        elif withdrawal_unit == "piece" and pieces_count and weight_per_piece:
            actual_qty = Decimal(str(pieces_count)) * D(weight_per_piece)

        # وارد قضبان (حديد): العدد × طول القضيب × الوزن لكل متر
        elif withdrawal_unit == "bar_count" and pieces_count \
                and mat.bar_length_cm and mat.weight_per_meter_kg:
            actual_qty = Decimal(str(pieces_count)) * (mat.bar_length_cm / 100) * mat.weight_per_meter_kg

        # وارد بطول قضيب محدد
        elif withdrawal_unit == "bar_length" and data.bar_length_cm and mat.weight_per_meter_kg:
            count = Decimal(str(pieces_count or 1))
            actual_qty = (D(data.bar_length_cm) / 100) * mat.weight_per_meter_kg * count
        # غير ذلك: actual_qty = data.qty (وارد بالوزن المباشر)

    # ── الصادر (OUT) ───────────────────────────────────────────────
    else:
        if withdrawal_unit == "piece" and pieces_count and weight_per_piece:
            actual_qty = Decimal(str(pieces_count)) * D(weight_per_piece)

        elif withdrawal_unit == "plate_area":
            # صادر بالمقاس: المساحة → وزن عن طريق كثافة الماده
            length_cm = D(data.plate_length_cm)
            width_cm  = D(data.plate_width_cm)
            plate_pcs = Decimal(str(data.plate_pieces or 1))
            if length_cm and width_cm:
                area_per_piece = (length_cm / 100) * (width_cm / 100)
                plate_area_m2  = area_per_piece * plate_pcs
                if mat.density_kg_m2 and mat.density_kg_m2 > 0:
                    actual_qty = (plate_area_m2 * mat.density_kg_m2) + waste_kg
                else:
                    raise HTTPException(400, "كثافة المادة (density_kg_m2) غير معرّفة — سجّل وارداً بالأبعاد أولاً لحساب الكثافة تلقائياً")

        elif withdrawal_unit == "plate_count" and pieces_count and mat.plate_weight_kg:
            actual_qty = Decimal(str(pieces_count)) * mat.plate_weight_kg

        elif withdrawal_unit == "bar_length" and data.bar_length_cm and mat.weight_per_meter_kg:
            count = Decimal(str(pieces_count or 1))
            actual_qty = (D(data.bar_length_cm) / 100) * mat.weight_per_meter_kg * count + waste_kg

        elif withdrawal_unit == "bar_count" and pieces_count \
                and mat.bar_length_cm and mat.weight_per_meter_kg:
            actual_qty = Decimal(str(pieces_count)) * (mat.bar_length_cm / 100) * mat.weight_per_meter_kg
        # غير ذلك: actual_qty = data.qty (صادر بالوزن المباشر)

    from datetime import date as _mv_date
    from app.services.inventory_ledger import post_receipt, post_issue

    extra_fields = dict(
        plate_length_cm=D(data.plate_length_cm),
        plate_width_cm=D(data.plate_width_cm),
        plate_area_m2=plate_area_m2 or D(data.plate_area_m2),
        plate_weight_kg=D(data.plate_weight_kg),
        bar_length_cm=D(data.bar_length_cm),
        waste_g=D(data.waste_g),
        reference=data.reference,
        notes=data.notes,
        withdrawal_unit=withdrawal_unit,
        destination=data.destination,
        destination_ref=data.destination_ref,
        customer_id=data.customer_id,
        pieces_count=pieces_count,
        weight_per_piece=D(weight_per_piece),
        work_order_id=data.work_order_id,
        source=data.source,
    )

    # المخزن المختار (اختياري): بيتحول للموقع الافتراضي بتاعه. من غيره →
    # post_receipt/post_issue بيستخدموا RAW زي ما كانوا دايمًا.
    location_id = None
    if data.warehouse_id is not None:
        wh, loc = await _warehouse_bin_for(db, data.warehouse_id)
        location_id = loc.id
        if data.movement_type == "out":
            wh_qty = await _warehouse_qty(db, mat.id, wh.id)
            if actual_qty > wh_qty:
                raise HTTPException(
                    400,
                    f"الرصيد في '{wh.name_ar}' غير كافٍ — المتاح: {float(wh_qty):g} {mat.unit}، "
                    f"المطلوب صرفه: {float(actual_qty):g} {mat.unit}",
                )

    if data.movement_type == "in":
        movement = await post_receipt(
            db, material=mat, qty=actual_qty, unit_cost=data.unit_cost,
            movement_date=data.movement_date or _mv_date.today(),
            destination_location_id=location_id,
            # source_doc_type=None → حركة يدوية من شاشة المخزون (زي ما كانت دايمًا)
            **extra_fields,
        )
    else:
        movement = await post_issue(
            db, material=mat, qty=actual_qty, unit_cost=data.unit_cost,
            movement_date=data.movement_date or _mv_date.today(),
            source_location_id=location_id,
            **extra_fields,
        )

    total = movement.total_cost
    await db.commit()
    await db.refresh(movement)
    return {
        "id": movement.id,
        "actual_qty": float(actual_qty),
        "plate_area_m2": float(plate_area_m2) if plate_area_m2 else None,
        "total_cost": float(total),
        "pieces_count": movement.pieces_count,
        "destination": movement.destination,
    }


class TransferIn(BaseModel):
    material_id: int
    # إما مواقع (bins) صريحة، أو مخازن (بتتحول للموقع الافتراضي لكل مخزن)
    source_location_id: Optional[int] = None
    destination_location_id: Optional[int] = None
    source_warehouse_id: Optional[int] = None
    destination_warehouse_id: Optional[int] = None
    qty: Decimal
    reference: Optional[str] = None
    notes: Optional[str] = None


@router.post("/movements/transfer", status_code=201)
async def transfer_stock(data: TransferIn, db: AsyncSession = Depends(get_db)):
    """
    محطة 12: تحويل كمية من مادة بين موقعين — الرصيد الإجمالي للمادة ميتغيرش،
    بس التوزيع بين المواقع (StockQuant) بيتحدّث.
    """
    from app.services.inventory_ledger import post_transfer
    mat = await db.get(Material, data.material_id)
    if not mat:
        raise HTTPException(404, "المادة غير موجودة")

    # المصدر والوجهة بيوصلوا كـ IDs خام من العميل — post_transfer نفسه بيفحص
    # التوفر في الموقع المصدر (فبيرفض id مصدر وهمي ضمنيًا)، لكن الموقع
    # الوجهة مكانش بيتفحص خالص: أي رقم (حتى لو مش BinLocation موجود، أو
    # موجود بس لتنانت تاني) كان بيعدي، و_sync_quant كان بينشئ StockQuant
    # يتيم عليه — رصيد "منقول" فعليًا بس مالوش موقع حقيقي يظهر بيه في
    # /quants ولا ينفع يترحّل منه تاني.
    if data.qty is None or data.qty <= 0:
        raise HTTPException(400, "كمية التحويل يجب أن تكون أكبر من صفر")

    src_loc_id = data.source_location_id
    dst_loc_id = data.destination_location_id
    src_wh = dst_wh = None
    if src_loc_id is None:
        if data.source_warehouse_id is None:
            raise HTTPException(400, "حدد المخزن (أو الموقع) المحوَّل منه")
        src_wh, src_loc = await _warehouse_bin_for(db, data.source_warehouse_id, label="المخزن المحوَّل منه")
        src_loc_id = src_loc.id
    if dst_loc_id is None:
        if data.destination_warehouse_id is None:
            raise HTTPException(400, "حدد المخزن (أو الموقع) المحوَّل إليه")
        dst_wh, dst_loc = await _warehouse_bin_for(db, data.destination_warehouse_id, label="المخزن المحوَّل إليه")
        dst_loc_id = dst_loc.id
    if src_wh is not None and dst_wh is not None and src_wh.id == dst_wh.id:
        raise HTTPException(400, "المخزن المحوَّل منه والمحوَّل إليه لازم يكونوا مختلفين")

    if not await db.get(BinLocation, src_loc_id):
        raise HTTPException(404, "موقع المصدر غير موجود")
    if not await db.get(BinLocation, dst_loc_id):
        raise HTTPException(404, "موقع الوجهة غير موجود")

    if src_wh is not None:
        avail = await _location_qty(db, mat.id, src_loc_id)
        if data.qty > avail:
            raise HTTPException(
                400,
                f"الرصيد في '{src_wh.name_ar}' غير كافٍ — المتاح: {float(avail):g} {mat.unit}، "
                f"المطلوب تحويله: {float(data.qty):g} {mat.unit}",
            )

    movement = await post_transfer(
        db, material=mat, qty=data.qty,
        source_location_id=src_loc_id,
        destination_location_id=dst_loc_id,
        reference=data.reference, notes=data.notes,
    )
    await db.commit()
    await db.refresh(movement)
    return {
        "id": movement.id,
        "material_id": mat.id,
        "qty": float(movement.qty),
        "source_location_id": movement.source_location_id,
        "destination_location_id": movement.destination_location_id,
    }


async def _location_warehouse_map(db: AsyncSession):
    """{location_id: (warehouse_id, warehouse_name)} + id موقع RAW الافتراضي.

    الحركات القديمة (قبل تفعيل المواقع) مسجّلة من غير source/destination —
    الخدمة المركزية للمخزون بتعتبر أي حركة من غير موقع على مخزن الخامات
    (RAW)، وتسوية المخزون القديم حطت رصيدها هناك، فبنعرضها بنفس المنطق.
    """
    from app.models.models import Warehouse
    from app.core.database import current_tenant_id as _ctid_lw
    tid = _ctid_lw.get()
    rows = (await db.execute(
        select(BinLocation.id, BinLocation.code, Warehouse.id, Warehouse.name_ar)
        .outerjoin(Warehouse, Warehouse.id == BinLocation.warehouse_id)
        .where(BinLocation.tenant_id == tid)
    )).all()
    loc_map = {lid: (wid, wname) for lid, _code, wid, wname in rows}
    raw_loc = next((lid for lid, code, _w, _n in rows if code == "RAW-DEFAULT"), None)
    return loc_map, raw_loc


def _movement_sides(m, raw_loc):
    """(موقع المصدر، موقع الوجهة) بعد تطبيق افتراضي RAW للحركات القديمة."""
    src, dst = m.source_location_id, m.destination_location_id
    if m.movement_type == "in" and dst is None:
        dst = raw_loc
    elif m.movement_type == "out" and src is None:
        src = raw_loc
    return src, dst


def _wh_of(loc_id, loc_map):
    return loc_map.get(loc_id, (None, None)) if loc_id is not None else (None, None)


def _wh_delta(m, src, dst, loc_map, warehouse_id) -> Decimal:
    """أثر الحركة على رصيد مخزن معيّن (+ داخل له، − خارج منه)."""
    d = Decimal("0")
    if _wh_of(dst, loc_map)[0] == warehouse_id:
        d += m.qty
    if _wh_of(src, loc_map)[0] == warehouse_id:
        d -= m.qty
    return d


_DEST_AR = {"workshop": "الورشة", "customer": "عميل خارجي"}


def _movement_row(m, balance, loc_map, raw_loc, material=None, direction=None):
    src, dst = _movement_sides(m, raw_loc)
    s_wid, s_wname = _wh_of(src, loc_map)
    d_wid, d_wname = _wh_of(dst, loc_map)
    row = {
        "id": m.id, "movement_type": m.movement_type,
        "qty": float(m.qty), "unit_cost": float(m.unit_cost),
        "total_cost": float(m.total_cost),
        "movement_date": str(m.movement_date),
        "reference": m.reference, "notes": m.notes,
        "withdrawal_unit": getattr(m, "withdrawal_unit", None),
        "destination": m.destination,
        "destination_ar": _DEST_AR.get(m.destination or "", m.destination or ""),
        "destination_ref": m.destination_ref,
        "pieces_count": m.pieces_count,
        "weight_per_piece": float(m.weight_per_piece) if m.weight_per_piece else None,
        "plate_length_cm": float(m.plate_length_cm) if m.plate_length_cm else None,
        "plate_width_cm": float(m.plate_width_cm) if m.plate_width_cm else None,
        "plate_area_m2": float(m.plate_area_m2) if m.plate_area_m2 else None,
        "plate_weight_kg": float(m.plate_weight_kg) if m.plate_weight_kg else None,
        "bar_length_cm": float(m.bar_length_cm) if m.bar_length_cm else None,
        "waste_g": float(m.waste_g) if m.waste_g else None,
        "balance_after": balance,
        "state": getattr(m, "state", "posted"),
        "reversed_movement_id": m.reversed_movement_id,
        "movement_no": getattr(m, "movement_no", None),
        "source_doc_type": getattr(m, "source_doc_type", None),
        "source_doc_id": getattr(m, "source_doc_id", None),
        "source_location_id": getattr(m, "source_location_id", None),
        "destination_location_id": getattr(m, "destination_location_id", None),
        "source_warehouse_id": s_wid, "source_warehouse_name": s_wname,
        "destination_warehouse_id": d_wid, "destination_warehouse_name": d_wname,
    }
    if material is not None:
        row.update({"material_id": material.id, "material_code": material.code,
                    "material_name": material.name_ar, "unit": material.unit})
    if direction is not None:
        row["warehouse_direction"] = direction  # "in" / "out" بالنسبة للمخزن المفلتر
    return row


@router.get("/movements/{material_id}")
async def get_movements(material_id: int, warehouse_id: Optional[int] = None,
                        db: AsyncSession = Depends(get_db)):
    """حركات مادة — كلها، أو (مع warehouse_id) اللي بتمس مخزن معيّن بس
    والرصيد التراكمي بيبقى رصيد المادة في المخزن ده."""
    from app.core.database import current_tenant_id as _ctid_gm
    mat = await db.get(Material, material_id)
    if not mat or mat.tenant_id != _ctid_gm.get():
        raise HTTPException(404, "المادة غير موجودة")
    movs = (await db.execute(
        select(StockMovement)
        .where(StockMovement.material_id == material_id, StockMovement.tenant_id == mat.tenant_id)
        .order_by(StockMovement.movement_date.asc(), StockMovement.id.asc())
    )).scalars().all()
    loc_map, raw_loc = await _location_warehouse_map(db)

    # حساب الرصيد التراكمي لكل حركة (تصاعدي ثم نعكسه للعرض)
    running = Decimal("0")
    rows = []
    for m in movs:
        if warehouse_id is not None:
            src, dst = _movement_sides(m, raw_loc)
            if warehouse_id not in (_wh_of(src, loc_map)[0], _wh_of(dst, loc_map)[0]):
                continue
            delta = _wh_delta(m, src, dst, loc_map, warehouse_id)
            running += delta
            rows.append((m, float(running), "in" if delta > 0 else "out"))
            continue
        # التحويل بين المواقع (transfer) ميغيرش الرصيد الإجمالي للمادة — قبل كده
        # كان بيتحسب كصادر فيطلّع رصيد تراكمي أقل من الحقيقي بعد أي تحويل.
        if m.movement_type == "in":
            running += m.qty
        elif m.movement_type == "out":
            running -= m.qty
        rows.append((m, float(running), None))
    rows.reverse()  # الأحدث أولاً
    return [_movement_row(m, bal, loc_map, raw_loc, direction=dirn) for m, bal, dirn in rows]


@router.get("/warehouse-movements/{warehouse_id}")
async def get_warehouse_movements(
    warehouse_id: int,
    date_from: Optional[date] = None, date_to: Optional[date] = None,
    limit: int = 300,
    db: AsyncSession = Depends(get_db),
):
    """كل الحركات اللي دخلت أو خرجت من مخزن معيّن (كل المواد)، الأحدث أولاً."""
    from sqlalchemy import or_ as _or, and_ as _and
    from app.models.models import Warehouse
    from app.core.database import current_tenant_id as _ctid_wm
    tid = _ctid_wm.get()
    wh = await db.scalar(select(Warehouse).where(Warehouse.id == warehouse_id, Warehouse.tenant_id == tid))
    if not wh:
        raise HTTPException(404, "المخزن غير موجود")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "تاريخ البداية بعد تاريخ النهاية")
    limit = max(1, min(int(limit or 300), 1000))

    loc_map, raw_loc = await _location_warehouse_map(db)
    wh_locs = [lid for lid, (wid, _n) in loc_map.items() if wid == warehouse_id]
    if not wh_locs:
        return []
    cond = [StockMovement.source_location_id.in_(wh_locs), StockMovement.destination_location_id.in_(wh_locs)]
    if raw_loc in wh_locs:
        # الحركات القديمة من غير موقع محسوبة على RAW
        cond += [
            _and(StockMovement.movement_type == "in", StockMovement.destination_location_id.is_(None)),
            _and(StockMovement.movement_type == "out", StockMovement.source_location_id.is_(None)),
        ]
    q = select(StockMovement).where(StockMovement.tenant_id == tid, _or(*cond))
    if date_from:
        q = q.where(StockMovement.movement_date >= date_from)
    if date_to:
        q = q.where(StockMovement.movement_date <= date_to)
    movs = (await db.execute(
        q.order_by(StockMovement.movement_date.desc(), StockMovement.id.desc()).limit(limit)
    )).scalars().all()
    ids = {m.material_id for m in movs}
    mats = {x.id: x for x in (await db.execute(select(Material).where(Material.id.in_(ids)))).scalars()} if ids else {}
    out = []
    for m in movs:
        src, dst = _movement_sides(m, raw_loc)
        delta = _wh_delta(m, src, dst, loc_map, warehouse_id)
        out.append(_movement_row(m, None, loc_map, raw_loc, material=mats.get(m.material_id),
                                 direction="in" if delta > 0 else "out"))
    return out


@router.get("/alerts/low-stock")
async def low_stock_alerts(db: AsyncSession = Depends(get_db)):
    from datetime import date, timedelta
    from app.models.models import StockMovement
    from sqlalchemy import func as sqlfunc

    items = await InventoryService.low_stock_alerts(db)
    cutoff = date.today() - timedelta(days=90)

    # متوسط الاستهلاك اليومي لكل المواد قليلة المخزون في استعلام واحد
    # (GROUP BY) بدل استعلام منفصل لكل مادة جوه اللوب (N+1).
    usage_map: dict[int, float] = {}
    ids = [m.id for m in items]
    if ids:
        usage_rows = await db.execute(
            select(StockMovement.material_id, sqlfunc.coalesce(sqlfunc.sum(StockMovement.qty), 0))
            .where(
                StockMovement.material_id.in_(ids),
                StockMovement.movement_type == "out",
                StockMovement.movement_date >= cutoff,
            )
            .group_by(StockMovement.material_id)
        )
        usage_map = {mid: float(total) for mid, total in usage_rows.all()}

    result = []
    for m in items:
        total_out = usage_map.get(m.id, 0.0)
        daily_usage = round(total_out / 90, 4) if total_out > 0 else None

        result.append({
            "id": m.id,
            "code": m.code,
            "name_ar": m.name_ar,
            "stock_qty": float(m.stock_qty),
            "reorder_level": float(m.reorder_level),
            "daily_usage": daily_usage,   # None لو مافيش حركات
            "unit": m.unit,
        })
    return result


@router.put("/materials/{material_id}/price")
async def update_material_price(material_id: int, new_price: float,
                                 db: AsyncSession = Depends(get_db)):
    """تحديث سعر المادة — يُحدِّث BOM تلقائياً عبر الـ trigger"""
    mat = await db.get(Material, mat_id := material_id)
    if not mat: raise HTTPException(404, "Material not found")
    if new_price < 0:
        raise HTTPException(400, "السعر لا يمكن أن يكون سالباً")
    # float → Decimal عبر str عشان منخزّنش أخطاء تقريب الـ float في عمود Numeric
    new_price = Decimal(str(new_price))
    old_price = mat.unit_cost
    mat.unit_cost = new_price
    # سجّل التغيير في MaterialPriceHistory — الموديل موجود ومربوط بعلاقة
    # على Material من قبل، لكن محدّش كان بيكتب فيه فعليًا في أي مكان في
    # النظام (تأكدنا بالبحث الشامل)، فسعر المادة كان بيتغير من غير أي أثر تاريخي.
    db.add(MaterialPriceHistory(
        tenant_id=mat.tenant_id, material_id=mat.id,
        old_price=old_price, new_price=new_price,
    ))
    await db.commit()
    return {"id": mat.id, "code": mat.code, "new_price": float(mat.unit_cost)}


@router.get("/categories")
async def list_categories(db: AsyncSession = Depends(get_db)):
    """Return all material categories with material count"""
    from app.models.models import MaterialCategory
    result = await db.execute(select(MaterialCategory).order_by(MaterialCategory.name_ar))
    cats = result.scalars().all()
    # عدد المواد لكل الفئات في استعلام واحد (GROUP BY) بدل استعلام منفصل
    # لكل فئة (N+1) — كان بيعمل استعلام إضافي جوه اللوب لكل صف.
    counts_res = await db.execute(
        select(Material.category_id, func.count())
        .group_by(Material.category_id)
    )
    counts = {cid: cnt for cid, cnt in counts_res.all()}
    return [
        {
            "id": c.id, "name_ar": c.name_ar, "name_en": c.name_en,
            "material_count": counts.get(c.id, 0),
        }
        for c in cats
    ]


class CategoryCreate(BaseModel):
    name_ar: str
    name_en: Optional[str] = ""


@router.post("/categories", status_code=201)
async def create_category(data: CategoryCreate, db: AsyncSession = Depends(get_db)):
    """إضافة صنف جديد"""
    from app.models.models import MaterialCategory
    name_ar = (data.name_ar or "").strip()
    if not name_ar:
        raise HTTPException(400, "اسم الصنف مطلوب")
    if len(name_ar) > 120 or len((data.name_en or "").strip()) > 120:
        raise HTTPException(400, "اسم الصنف طويل جداً (الحد الأقصى 120 حرف)")
    # نتأكد إنه مش مكرر
    existing = await db.execute(
        select(MaterialCategory).where(MaterialCategory.name_ar == name_ar)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "الصنف موجود بالفعل")
    from app.core.database import current_tenant_id as _ctid_cat
    _tid_cat = _ctid_cat.get() or 1
    cat = MaterialCategory(tenant_id=_tid_cat, name_ar=name_ar, name_en=(data.name_en or name_ar).strip())
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return {"id": cat.id, "name_ar": cat.name_ar, "name_en": cat.name_en, "material_count": 0}


@router.delete("/categories/{category_id}", status_code=204)
async def delete_category(category_id: int, db: AsyncSession = Depends(get_db)):
    """حذف صنف — يمنع الحذف لو فيه مواد مرتبطة به"""
    from app.models.models import MaterialCategory
    cat = await db.get(MaterialCategory, category_id)
    if not cat:
        raise HTTPException(404, "الصنف غير موجود")
    cnt = await db.execute(
        select(func.count()).select_from(Material).where(Material.category_id == category_id)
    )
    if (cnt.scalar() or 0) > 0:
        raise HTTPException(409, "لا يمكن حذف الصنف لأنه يحتوي على مواد. انقل المواد أولاً.")
    await db.delete(cat)
    await db.commit()


@router.delete("/materials/{material_id}", status_code=204)
async def delete_material(material_id: int, db: AsyncSession = Depends(get_db)):
    mat = await db.get(Material, material_id)
    if not mat:
        raise HTTPException(404, "المادة غير موجودة")

    # نتأكد إن المادة مش مرتبطة بأي حركات قبل المسح
    async def _count(model):
        q = await db.execute(
            select(func.count()).select_from(model).where(model.material_id == material_id)
        )
        return q.scalar() or 0

    blockers = []
    if await _count(StockMovement) > 0:
        blockers.append("حركات مخزون")
    if await _count(StockBatch) > 0:
        blockers.append("دفعات مخزون")
    if await _count(MaterialBinStock) > 0:
        blockers.append("أرصدة في المخازن")
    if await _count(MaterialReservation) > 0:
        blockers.append("حجوزات")
    if await _count(PurchaseInvoiceLine) > 0:
        blockers.append("فواتير شراء")
    if await _count(PurchaseOrderLine) > 0:
        blockers.append("أوامر شراء")
    if float(mat.stock_qty or 0) != 0:
        blockers.append("رصيد حالي غير صفري")
    if await _count(SheetStockItem) > 0 or await _count(BarStockItem) > 0:
        blockers.append("مقاسات ألواح/أسياخ مسجلة")
    if await _count(CostLine) > 0:
        blockers.append("تكاليف أوامر إنتاج")

    # أي جدول تاني بيشاور على المادة (سطور فواتير بيع، عروض أسعار، هالك،
    # جرد...) — الحذف كان بيعدّي ويسيب سجلات يتيمة (أو يفشل بـ 500 على
    # Postgres بسبب الـ FK). نفحصهم كلهم بشكل عام بدل قائمة ثابتة ناقصة.
    from app.core.database import Base as _Base_del
    _checked = {StockMovement, StockBatch, MaterialBinStock, MaterialReservation,
                PurchaseInvoiceLine, PurchaseOrderLine, SheetStockItem, BarStockItem,
                CostLine, BOMLine, MaterialPriceHistory}
    _other_refs = 0
    for _mapper in _Base_del.registry.mappers:
        _cls = _mapper.class_
        if _cls in _checked or _cls is Material:
            continue
        _col = getattr(_cls, "__table__", None)
        _col = _col.c.get("material_id") if _col is not None else None
        if _col is None or not any(fk.column.table.name == "materials" for fk in _col.foreign_keys):
            continue
        _other_refs += await _count(_cls)
    if _other_refs > 0:
        blockers.append("مستندات أخرى (مبيعات/عروض أسعار/هالك/جرد...)")

    if blockers:
        raise HTTPException(
            409,
            "لا يمكن حذف المادة لأنها مرتبطة بـ: "
            + "، ".join(blockers)
            + ". يمكنك تصفية رصيدها وحذف حركاتها أولاً، أو إبقاؤها للسجل.",
        )

    # مفيش حركات — نمسح بأمان مع تنظيف الجداول التابعة اللي مالهاش قيمة لوحدها
    await db.execute(sa_delete(BOMLine).where(BOMLine.material_id == material_id))
    await db.execute(sa_delete(MaterialPriceHistory).where(MaterialPriceHistory.material_id == material_id))
    await db.delete(mat)
    await db.commit()


@router.delete("/movements/{movement_id}", status_code=200)
async def delete_movement(movement_id: int, db: AsyncSession = Depends(get_db)):
    """
    عكس حركة مخزون (بدل حذفها فعليًا):
    - يعكس أثرها على الرصيد.
    - يُنشئ حركة جديدة مقابلة (audit trail كامل — الحركة الأصلية تبقى في الجدول).
    - الحركة الأصلية تتحول لحالة 'reversed' وتُربط بالحركة الجديدة عبر reversed_movement_id.
    - ممنوع عكس حركة معكوسة بالفعل، وممنوع عكس حركة هي نفسها ناتجة عن عكس سابق.
    """
    mov = await db.get(StockMovement, movement_id)
    if not mov:
        raise HTTPException(404, "الحركة غير موجودة")
    if mov.state == "reversed":
        raise HTTPException(400, "هذه الحركة معكوسة بالفعل ولا يمكن عكسها مرة أخرى")
    # هل mov نفسها حركة عكسية ناتجة عن عكس حركة أخرى؟ (نبحث عن حركة تشير إليها كـ reversed_movement_id)
    is_a_reversal = (await db.execute(
        select(StockMovement.id).where(StockMovement.reversed_movement_id == mov.id)
    )).scalar_one_or_none()
    if is_a_reversal is not None:
        raise HTTPException(400, "هذه الحركة نفسها ناتجة عن عكس حركة أخرى ولا يمكن عكسها")

    if mov.source_doc_type == "reversal":
        raise HTTPException(400, "هذه الحركة نفسها ناتجة عن عكس حركة أخرى ولا يمكن عكسها")

    mat = await db.get(Material, mov.material_id)

    # ── عكس تحويل بين المواقع ──────────────────────────────────────────
    # قبل كده حركة "transfer" كانت بتقع في فرع "else" فتتعامل كصادر:
    # stock_qty كان بيزيد بكمية التحويل (رصيد وهمي) وQuant الموقع الافتراضي
    # بيزيد بدل ما الكمية ترجع من الوجهة للمصدر. العكس الصحيح = تحويل معاكس.
    if mov.movement_type == "transfer":
        if not mat:
            raise HTTPException(404, "المادة غير موجودة")
        if not mov.source_location_id or not mov.destination_location_id:
            raise HTTPException(400, "حركة التحويل ناقصة بيانات المواقع ولا يمكن عكسها")
        from app.services.inventory_ledger import post_transfer as _post_transfer_rev
        reversal = await _post_transfer_rev(
            db, material=mat, qty=mov.qty,
            source_location_id=mov.destination_location_id,
            destination_location_id=mov.source_location_id,
            source_doc_type="reversal", source_doc_id=mov.id,
            reference=f"عكس للحركة #{mov.id}",
            notes=f"تم إنشاؤها تلقائيًا كعكس لحركة #{mov.id}" + (f" — {mov.notes}" if mov.notes else ""),
        )
        await db.flush()
        mov.state = "reversed"
        mov.reversed_movement_id = reversal.id
        await db.commit()
        await db.refresh(reversal)
        return {
            "message": "تم عكس الحركة بنجاح",
            "original_movement_id": mov.id,
            "reversal_movement_id": reversal.id,
            "material_id": mov.material_id,
            "stock_qty": float(mat.stock_qty),
        }

    reverse_type = "out" if mov.movement_type == "in" else "in"

    # عكس حركة وارد = صرف فعلي — لازم يمر بنفس فحص المتاح (الرصيد - المحجوز)
    # وإلا الرصيد ممكن ينزل تحت الصفر أو ياكل كمية محجوزة لأمر إنتاج.
    if mat and mov.movement_type == "in":
        from app.services.inventory import get_available_qty as _avail_rev
        available = await _avail_rev(db, mat.id, mat.stock_qty)
        if mov.qty > available:
            raise HTTPException(
                400,
                f"لا يمكن عكس حركة الوارد — المتاح حاليًا ({float(available)} {mat.unit}) "
                f"أقل من كمية الحركة ({float(mov.qty)} {mat.unit})",
            )

    # الحركات المربوطة بدُفعة (lot) لازم رصيد الدُفعة يتعكس معاها، وإلا
    # qty_remaining للدُفعة يفضل يعرض كمية مش موجودة فعليًا (أو العكس).
    rev_batch = None
    if mov.source_doc_type in ("stock_batch", "stock_batch_issue") and mov.source_doc_id:
        rev_batch = await db.get(StockBatch, mov.source_doc_id)
        if rev_batch and mov.source_doc_type == "stock_batch":
            if (rev_batch.qty_remaining or Decimal("0")) < mov.qty:
                raise HTTPException(
                    400,
                    f"لا يمكن عكس استلام الدُفعة '{rev_batch.lot_number}' — تم صرف جزء منها "
                    f"(المتبقي {float(rev_batch.qty_remaining)} من {float(mov.qty)})",
                )
            rev_batch.qty_remaining -= mov.qty
            if rev_batch.qty_remaining <= 0:
                rev_batch.qty_remaining = Decimal("0")
                rev_batch.status = "consumed"
        elif rev_batch and mov.source_doc_type == "stock_batch_issue":
            rev_batch.qty_remaining = (rev_batch.qty_remaining or Decimal("0")) + mov.qty
            if rev_batch.status == "consumed" and rev_batch.qty_remaining > 0:
                rev_batch.status = "active"

    if mat:
        # نعكس الأثر: حركة دخول كانت زوّدت الرصيد → ننقص، والعكس
        if mov.movement_type == "in":
            mat.stock_qty -= mov.qty
        else:
            mat.stock_qty += mov.qty

    from datetime import date as _rev_date
    from app.core.database import current_tenant_id as _ctid_rev
    from app.services.inventory_ledger import _next_movement_no, _sync_quant
    rev_tenant_id = mov.tenant_id or (_ctid_rev.get() or 1)
    reversal = StockMovement(
        tenant_id=rev_tenant_id,
        material_id=mov.material_id,
        movement_type=reverse_type,
        qty=mov.qty,
        unit_cost=mov.unit_cost,
        total_cost=mov.qty * mov.unit_cost,
        reference=f"عكس للحركة #{mov.id}",
        notes=f"تم إنشاؤها تلقائيًا كعكس لحركة #{mov.id}" + (f" — {mov.notes}" if mov.notes else ""),
        movement_date=_rev_date.today(),
        withdrawal_unit=mov.withdrawal_unit,
        source=mov.source,
        state="posted",
        movement_no=await _next_movement_no(db, rev_tenant_id),
        source_doc_type="reversal",
        source_doc_id=mov.id,
        # نفس الموقع اللي الحركة الأصلية أثّرت عليه (الوارد دخل على
        # destination_location_id، والصادر خرج من source_location_id)
        source_location_id=(mov.destination_location_id if mov.movement_type == "in" else None),
        destination_location_id=(mov.source_location_id if mov.movement_type != "in" else None),
    )
    db.add(reversal)
    await db.flush()  # نحتاج reversal.id قبل ما نربطه بالحركة الأصلية

    mov.state = "reversed"
    mov.reversed_movement_id = reversal.id

    if mat:
        # محطة 11: زامن StockQuant بنفس اتجاه العكس — لو الحركة الأصلية كانت
        # مرتبطة بدُفعة (lot)، لازم العكس يرجع لنفس الـ lot مش quant عام،
        # وإلا التوزيع بين الدُفعات والرصيد العام هيبقى غلط حتى لو الإجمالي صح.
        quant_lot_id = (
            mov.source_doc_id
            if mov.source_doc_type in ("stock_batch", "stock_batch_issue")
            else None
        )
        quant_delta = -mov.qty if mov.movement_type == "in" else mov.qty
        quant_location_id = (
            mov.destination_location_id if mov.movement_type == "in" else mov.source_location_id
        )
        await _sync_quant(
            db, tenant_id=rev_tenant_id, material_id=mov.material_id,
            delta_qty=quant_delta, lot_id=quant_lot_id, location_id=quant_location_id,
        )

    await db.commit()
    await db.refresh(reversal)
    return {
        "message": "تم عكس الحركة بنجاح",
        "original_movement_id": mov.id,
        "reversal_movement_id": reversal.id,
        "material_id": mov.material_id,
        "stock_qty": float(mat.stock_qty) if mat else None,
    }


# ── مولّد مخطط التقطيع (Cutting Plan / Nesting) ─────────────────────────

class CuttingPieceIn(BaseModel):
    label: str
    width_cm: Decimal
    height_cm: Decimal
    qty: int = 1

    @validator("width_cm", "height_cm")
    def _dim_positive(cls, v):
        if v is None or v <= 0:
            raise ValueError("أبعاد القطعة يجب أن تكون أكبر من صفر")
        return v

    @validator("qty")
    def _qty_range(cls, v):
        # حد أقصى معقول يمنع طلب بملايين القطع يعلّق خوارزمية التقطيع (DoS)
        if v is None or v < 1 or v > 10000:
            raise ValueError("عدد القطع يجب أن يكون بين 1 و 10000")
        return v


class CuttingPlanRequest(BaseModel):
    material_id: Optional[int] = None
    sheet_width_cm: Optional[Decimal] = None
    sheet_height_cm: Optional[Decimal] = None
    pieces: list[CuttingPieceIn]
    allow_rotation: bool = True
    kerf_mm: Decimal = Decimal("0")
    use_stock: bool = False

    @validator("kerf_mm")
    def _kerf_not_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("سُمك القطع (kerf) لا يمكن أن يكون سالباً")
        return v


@router.post("/cutting-plan")
async def cutting_plan(data: CuttingPlanRequest, db: AsyncSession = Depends(get_db)):
    if not data.pieces:
        raise HTTPException(400, "أضف قطعة واحدة على الأقل")

    kerf_cm = float(data.kerf_mm) / 10.0
    pieces = [
        {"label": p.label, "width": float(p.width_cm), "height": float(p.height_cm), "qty": p.qty}
        for p in data.pieces
    ]

    # ── استخدام مخزون الألواح/النسالة المتاحة بمقاسات متعددة ──────────
    if data.use_stock:
        if not data.material_id:
            raise HTTPException(400, "حدد المادة لاستخدام المخزون المتاح")
        rows = (await db.execute(
            select(SheetStockItem).where(
                SheetStockItem.material_id == data.material_id,
                SheetStockItem.qty > 0,
            )
        )).scalars().all()

        stock_items = [
            {"id": r.id, "length": float(r.length_cm), "width": float(r.width_cm),
             "qty": r.qty, "source": r.source}
            for r in rows
        ]

        # احتياطي: لو المادة عندها مخزون عام (m² عبر density_kg_m2) وفيه مقاس لوح افتراضي مسجّل
        # على المادة، نضيف "ألواح افتراضية" منه — بدل ما نوقف المستخدم لمجرد عدم تسجيل مقاس صريح
        mat = await db.get(Material, data.material_id)
        if mat and mat.plate_length_cm and mat.plate_width_cm and mat.density_kg_m2 and mat.stock_qty:
            sheet_area_m2 = (float(mat.plate_length_cm) / 100) * (float(mat.plate_width_cm) / 100)
            sheet_weight_kg = sheet_area_m2 * float(mat.density_kg_m2)
            if sheet_weight_kg > 0:
                virtual_qty = int(float(mat.stock_qty) // sheet_weight_kg)
                if virtual_qty > 0:
                    stock_items.append({
                        "id": None, "length": float(mat.plate_length_cm), "width": float(mat.plate_width_cm),
                        "qty": virtual_qty, "source": "bulk",
                    })

        if not stock_items:
            raise HTTPException(400, "لا توجد مقاسات ألواح/نسالة مسجّلة لهذه المادة في المخزون، ولا مقاس لوح افتراضي محدد عليها لاستخدام المخزون العام")
        try:
            result = build_cutting_plan_from_stock(
                stock_items, pieces, allow_rotation=data.allow_rotation, kerf=kerf_cm,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        return result

    # ── مقاس لوح واحد (يدوي أو من بيانات المادة) ──────────────────────
    sheet_w = data.sheet_width_cm
    sheet_h = data.sheet_height_cm

    if data.material_id:
        mat = await db.get(Material, data.material_id)
        if not mat:
            raise HTTPException(404, "المادة غير موجودة")
        sheet_w = sheet_w or mat.plate_length_cm
        sheet_h = sheet_h or mat.plate_width_cm

    if not sheet_w or not sheet_h:
        raise HTTPException(400, "مقاس اللوح غير محدد — حدده يدويًا أو اختر مادة من نوع لوح لها مقاس مسجّل")

    try:
        result = build_cutting_plan(
            float(sheet_w), float(sheet_h), pieces,
            allow_rotation=data.allow_rotation, kerf=kerf_cm,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    return result


# ── مولّد مخطط تقطيع القضبان/الحديد (Bar Cutting Plan / 1D) ─────────────

class BarPieceIn(BaseModel):
    label: str
    length_cm: Decimal
    qty: int = 1

    @validator("length_cm")
    def _len_positive(cls, v):
        if v is None or v <= 0:
            raise ValueError("طول القطعة يجب أن يكون أكبر من صفر")
        return v

    @validator("qty")
    def _qty_range(cls, v):
        if v is None or v < 1 or v > 10000:
            raise ValueError("عدد القطع يجب أن يكون بين 1 و 10000")
        return v


class BarCuttingPlanRequest(BaseModel):
    material_id: Optional[int] = None
    bar_length_cm: Optional[Decimal] = None
    pieces: list[BarPieceIn]
    kerf_mm: Decimal = Decimal("0")
    use_stock: bool = False

    @validator("kerf_mm")
    def _kerf_not_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("سُمك القطع (kerf) لا يمكن أن يكون سالباً")
        return v


@router.post("/bar-cutting-plan")
async def bar_cutting_plan(data: BarCuttingPlanRequest, db: AsyncSession = Depends(get_db)):
    if not data.pieces:
        raise HTTPException(400, "أضف قطعة واحدة على الأقل")

    kerf_cm = float(data.kerf_mm) / 10.0
    pieces = [
        {"label": p.label, "length": float(p.length_cm), "qty": p.qty}
        for p in data.pieces
    ]

    # ── استخدام مخزون الأسياخ/النسالة المتاحة بأطوال متعددة ──────────
    if data.use_stock:
        if not data.material_id:
            raise HTTPException(400, "حدد المادة لاستخدام المخزون المتاح")
        rows = (await db.execute(
            select(BarStockItem).where(
                BarStockItem.material_id == data.material_id,
                BarStockItem.qty > 0,
            )
        )).scalars().all()

        stock_items = [
            {"id": r.id, "length": float(r.length_cm), "qty": r.qty, "source": r.source}
            for r in rows
        ]

        # احتياطي: لو المادة عندها مخزون عام بالكيلوجرام (stock_qty) وطول سيخ افتراضي محدد عليها،
        # نضيف "أسياخ افتراضية" منه — بدل ما نوقف المستخدم لمجرد عدم تسجيل طول صريح بالمخزون التفصيلي
        mat = await db.get(Material, data.material_id)
        if mat and mat.bar_length_cm and mat.weight_per_meter_kg and mat.stock_qty:
            bar_weight_kg = (float(mat.bar_length_cm) / 100) * float(mat.weight_per_meter_kg)
            if bar_weight_kg > 0:
                virtual_qty = int(float(mat.stock_qty) // bar_weight_kg)
                if virtual_qty > 0:
                    stock_items.append({
                        "id": None, "length": float(mat.bar_length_cm),
                        "qty": virtual_qty, "source": "bulk",
                    })

        if not stock_items:
            raise HTTPException(400, "لا توجد أطوال أسياخ/نسالة مسجّلة لهذه المادة في المخزون، ولا طول سيخ افتراضي محدد عليها لاستخدام المخزون العام")
        try:
            result = build_bar_cutting_plan_from_stock(stock_items, pieces, kerf=kerf_cm)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return result

    bar_length = data.bar_length_cm
    if data.material_id:
        mat = await db.get(Material, data.material_id)
        if not mat:
            raise HTTPException(404, "المادة غير موجودة")
        bar_length = bar_length or mat.bar_length_cm

    if not bar_length:
        raise HTTPException(400, "طول القضيب غير محدد — حدده يدويًا أو اختر مادة من نوع حديد لها طول مسجّل")

    try:
        result = build_bar_cutting_plan(float(bar_length), pieces, kerf=kerf_cm)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return result


# ── تنفيذ مخطط التقطيع: تحويل الهالك المتبقي إلى نسالة أو هالك نهائي ───────

class CommitWasteItem(BaseModel):
    kind: str  # "sheet" | "bar"
    length_cm: Decimal
    width_cm: Optional[Decimal] = None  # مطلوب لـ kind="sheet"
    qty: int = 1
    action: str  # "remnant" | "scrap"

    @validator("kind")
    def _valid_kind(cls, v):
        if v not in ("sheet", "bar"):
            raise ValueError("kind يجب أن يكون 'sheet' أو 'bar'")
        return v

    @validator("action")
    def _valid_action(cls, v):
        # قيمة غير معروفة كانت بتتجاهل بصمت (لا نسالة ولا هالك)
        if v not in ("remnant", "scrap"):
            raise ValueError("action يجب أن يكون 'remnant' أو 'scrap'")
        return v

    @validator("length_cm", "width_cm")
    def _dims_not_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("الأبعاد لا يمكن أن تكون سالبة")
        return v

    @validator("qty")
    def _qty_positive(cls, v):
        if v is None or v < 1:
            raise ValueError("العدد يجب أن يكون 1 على الأقل")
        return v


class CuttingPlanCommitRequest(BaseModel):
    material_id: int
    work_order_id: Optional[int] = None
    reservation_id: Optional[int] = None  # لو مُمرَّر: يُحدِّث حجز المادة ويمنع الصرف المزدوج
    used_stock_item_ids: list[int] = []
    consumed_qty_from_main_stock: Decimal = Decimal("0")
    waste_items: list[CommitWasteItem] = []


@router.post("/cutting-plan/commit")
async def commit_cutting_plan(data: CuttingPlanCommitRequest, db: AsyncSession = Depends(get_db)):
    mat = await db.get(Material, data.material_id)
    if not mat:
        raise HTTPException(404, "المادة غير موجودة")

    from app.core.database import current_tenant_id as _ctid_cc
    from app.services.production import record_scrap
    from app.services.inventory_ledger import post_issue, _next_movement_no
    tid = _ctid_cc.get() or 1

    if data.work_order_id is not None and not await db.get(WorkOrder, data.work_order_id):
        raise HTTPException(404, "أمر الإنتاج المحدد غير موجود")

    res = None
    if data.reservation_id:
        res = await db.get(MaterialReservation, data.reservation_id)
        if not res:
            raise HTTPException(404, "الحجز غير موجود")
        if res.material_id != data.material_id:
            # لو الحجز تابع لمادة مختلفة عن material_id المُرسلة، كان الكود
            # بيخصم فعليًا من مخزون data.material_id (post_issue/stock items)
            # لكن يعلّم حجز مادة تانية تمامًا كـ"مصروف" (res.issued_qty يزيد) —
            # فيسيب المادة الحقيقية المحجوزة لأمر الإنتاج من غير أي صرف فعلي
            # رغم إن حجزها بيظهر مُنفَّذ، ويستهلك مخزون مادة مش لها علاقة بالأمر.
            raise HTTPException(400, "الحجز المحدد لا يخص هذه المادة")
        remaining_reserved = res.reserved_qty - res.issued_qty
        if remaining_reserved <= 0:
            raise HTTPException(400, f"تم صرف {mat.name_ar} لهذا الحجز بالكامل من قبل — لا يمكن الصرف مرة أخرى")

    result = {"remnants_created": [], "scrap_records": [], "stock_items_consumed": 0, "main_stock_deducted": 0.0}
    total_issued_qty = Decimal("0")
    total_issued_cost = Decimal("0")

    # 1) خصم الكمية المستهلكة من المخزون الأساسي (ألواح/أسياخ قياسية كاملة)
    #    عبر post_issue — المصدر الوحيد المفروض لحركات "out" في كل النظام —
    #    بدل تعديل stock_qty يدويًا وإنشاء StockMovement خام. قبل هذا الإصلاح
    #    كان الكود بيعمل max(0, stock_qty - qty) فيمتص أي عجز في الصمت بدل
    #    ما يرفض الطلب، ومكنش بيفحص المتاح الفعلي (المحجوز لأوامر تانية)،
    #    ومكنش بيدي movement_no تسلسلي ولا يزامن StockQuant — فيسبب عدم
    #    تطابق دائم مع /materials/{id}/stock-check.
    if data.consumed_qty_from_main_stock and data.consumed_qty_from_main_stock > 0:
        qty = data.consumed_qty_from_main_stock
        movement = await post_issue(
            db, material=mat, qty=qty, unit_cost=mat.unit_cost,
            movement_date=date.today(),
            reference="مخطط تقطيع", notes="استهلاك ألواح/أسياخ قياسية لعملية تقطيع",
            work_order_id=data.work_order_id,
            source_doc_type="cutting_plan", source_doc_id=data.work_order_id,
        )
        cost = movement.total_cost
        result["main_stock_deducted"] = float(qty)
        total_issued_qty += qty
        total_issued_cost += cost

    # 2) تصفير عناصر مخزون الألواح/الأسياخ (نسالة أو قياسي) المُستهلكة بالكامل
    # - dedupe: نفس الـ id مكرر في الطلب كان بيتحسب مرتين في total_issued_qty
    #   (db.get بيرجّع نفس الكائن المعلَّم للحذف قبل الـ flush).
    # - لازم العنصر يكون تابع لنفس المادة — قبل كده أي id (حتى لو لوح مادة
    #   تانية) كان بيتمسح ويتحسب على المادة دي.
    # - SheetStockItem وBarStockItem جدولين منفصلين وممكن يتكرر نفس الـ id في
    #   الاتنين؛ نبدأ بالجدول المناسب لنوع المادة (سيخ ← أسياخ، غير كده ← ألواح).
    _bar_first = (mat.material_kind == "bar")
    _order = (BarStockItem, SheetStockItem) if _bar_first else (SheetStockItem, BarStockItem)
    for item_id in dict.fromkeys(data.used_stock_item_ids):
        item = None
        is_sheet = False
        for _model in _order:
            _cand = await db.get(_model, item_id)
            if _cand is not None and _cand.material_id == mat.id:
                item = _cand
                is_sheet = (_model is SheetStockItem)
                break
        if item:
            if is_sheet:
                item_qty = (item.length_cm * item.width_cm / Decimal("10000")) * item.qty
            else:
                item_qty = (item.length_cm / Decimal("100")) * item.qty * (mat.weight_per_meter_kg or 1)
            total_issued_qty += item_qty
            total_issued_cost += item_qty * mat.unit_cost
            await db.delete(item)
            result["stock_items_consumed"] += 1

    # 2.5) لو الحجز ممرّر: تسجيل صرف موحَّد (حركة مخزون + سطر تكلفة + تحديث الحجز) —
    #      نفس مسار issue_material، يمنع وجود أكثر من نقطة خصم لنفس عملية التقطيع
    if res and total_issued_qty > 0:
        issued_now = min(total_issued_qty, res.reserved_qty - res.issued_qty)
        cost_now = (issued_now / total_issued_qty) * total_issued_cost if total_issued_qty else Decimal("0")
        wo = await db.get(WorkOrder, res.work_order_id)
        # ملحوظة: هذه حركة تسجيلية (audit trail) للكمية اللي اتخصمت بالفعل
        # أعلاه (من المخزون الأساسي في 1، أو من عناصر النسالة في 2) — من
        # عمد ميتمّش خصم Material.stock_qty هنا تاني لتفادي الخصم المزدوج
        # (زي ما التعليق الأصلي بيوضّح). لكن لازم برضه ياخد movement_no
        # تسلسلي زي كل حركة تانية في النظام (كان بيتسجّل NULL قبل كده).
        # لو الخصم الفعلي اتسجل بالفعل كحركة "out" في الخطوة 1 (post_issue)،
        # حركة تسجيلية تانية بنوع "out" كانت بتظهر في كشف حركة المادة كصرف
        # مكرر (صرف 90 + صرف 60 بينما الرصيد نزل 90 بس). نسجّلها بس لما
        # الاستهلاك كان كله من عناصر نسالة (الخطوة 2) من غير حركة أساسية.
        if not result["main_stock_deducted"]:
            db.add(StockMovement(
                tenant_id=tid, material_id=mat.id, movement_type="out",
                qty=issued_now, unit_cost=mat.unit_cost, total_cost=cost_now,
                reference=f"{wo.code}-CUT" if wo else "مخطط تقطيع", notes="استهلاك نسالة عبر مخطط التقطيع",
                movement_date=date.today(), work_order_id=res.work_order_id,
                movement_no=await _next_movement_no(db, tid),
                source_doc_type="cutting_plan_reservation", source_doc_id=res.id,
            ))
        db.add(CostLine(
            tenant_id=tid, work_order_id=res.work_order_id, cost_type="material",
            description=f"{mat.name_ar} — تقطيع", qty=issued_now,
            unit_cost=mat.unit_cost, total_cost=cost_now, material_id=mat.id,
        ))
        res.issued_qty += issued_now
        res.status = "fully_issued" if res.issued_qty >= res.required_qty else "partially_issued"
        if wo:
            wo.actual_cost += cost_now
            # نفس اللي بيعمله issue_material: قيمة "تحت التصنيع" في موقع WIP +
            # قيد WIP مدين / مخزون خام دائن. من غيرهم حساب WIP بيطلع دائن
            # بقيمة المواد دي عند إقفال الأمر، ومخزون الخامات بيفضل متضخّم.
            from app.services.production import _sync_wip_quant
            from app.services.auto_journal import journal_material_issue
            await _sync_wip_quant(db, wo, tid)
            await journal_material_issue(db, tid, wo.id, wo.code, cost_now, date.today())
        result["reservation_status"] = res.status
        result["issued_qty"] = float(issued_now)

    # 3) معالجة الهالك المتبقي: حفظ كنسالة أو تسجيل كهالك نهائي
    for w in data.waste_items:
        if w.action == "remnant":
            if w.kind == "sheet":
                item = SheetStockItem(
                    tenant_id=tid, material_id=mat.id,
                    length_cm=w.length_cm, width_cm=w.width_cm or Decimal("0"),
                    qty=w.qty, source="remnant", source_work_order_id=data.work_order_id,
                    notes="نسالة من مخطط تقطيع",
                )
                rem_value = (w.length_cm * (w.width_cm or Decimal("0")) / Decimal("10000")) * w.qty
            else:
                item = BarStockItem(
                    tenant_id=tid, material_id=mat.id,
                    length_cm=w.length_cm, qty=w.qty, source="remnant",
                    source_work_order_id=data.work_order_id, notes="نسالة من مخطط تقطيع",
                )
                rem_value = (w.length_cm / Decimal("100")) * w.qty * (mat.weight_per_meter_kg or Decimal("1"))
            db.add(item)
            await db.flush()
            from app.services.production import _receive_remnant_value
            await _receive_remnant_value(
                db, tid, mat, rem_value, "cutting_plan", item.id,
                f"نسالة من مخطط تقطيع — أمر إنتاج {data.work_order_id or '—'}",
            )
            result["remnants_created"].append({
                "id": item.id, "kind": w.kind, "length_cm": float(w.length_cm),
                "width_cm": float(w.width_cm) if w.width_cm else None, "qty": w.qty,
            })
        elif w.action == "scrap":
            if w.kind == "sheet":
                scrap_qty = (w.length_cm * (w.width_cm or Decimal("0")) / Decimal("10000")) * w.qty
            else:
                scrap_qty = (w.length_cm / Decimal("100")) * w.qty
            if scrap_qty > 0:
                sres = await record_scrap(
                    db, mat.id, scrap_qty,
                    reason="هالك متبقي من مخطط تقطيع", work_order_id=data.work_order_id,
                )
                result["scrap_records"].append(sres)

    await db.commit()
    return result


# ── مخزون مقاسات الألواح/النسالة (SheetStockItem) ───────────────────────

class SheetStockItemIn(BaseModel):
    material_id: int
    length_cm: Decimal
    width_cm: Decimal
    qty: int = 1
    source: str = "purchased"  # purchased | remnant
    notes: Optional[str] = None

    @validator("length_cm", "width_cm")
    def _dims_positive(cls, v):
        if v is None or v <= 0:
            raise ValueError("الأبعاد يجب أن تكون أكبر من صفر")
        return v

    @validator("qty")
    def _qty_positive(cls, v):
        if v is None or v < 1:
            raise ValueError("العدد يجب أن يكون 1 على الأقل")
        return v

    @validator("source")
    def _valid_source(cls, v):
        if v not in ("purchased", "remnant"):
            raise ValueError("المصدر يجب أن يكون 'purchased' أو 'remnant'")
        return v


@router.get("/sheet-stock")
async def list_sheet_stock(material_id: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    q = select(SheetStockItem)
    if material_id:
        q = q.where(SheetStockItem.material_id == material_id)
    rows = (await db.execute(q.order_by(SheetStockItem.id.desc()))).scalars().all()
    return [
        {
            "id": r.id, "material_id": r.material_id,
            "length_cm": float(r.length_cm), "width_cm": float(r.width_cm),
            "qty": r.qty, "source": r.source, "notes": r.notes,
        }
        for r in rows
    ]


@router.post("/sheet-stock", status_code=201)
async def create_sheet_stock(data: SheetStockItemIn, db: AsyncSession = Depends(get_db)):
    mat = await db.get(Material, data.material_id)
    if not mat:
        raise HTTPException(404, "المادة غير موجودة")
    from app.core.database import current_tenant_id as _ctid_ss
    item = SheetStockItem(
        tenant_id=_ctid_ss.get() or 1,
        material_id=data.material_id,
        length_cm=data.length_cm, width_cm=data.width_cm,
        qty=data.qty, source=data.source, notes=data.notes,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"id": item.id}


@router.delete("/sheet-stock/{item_id}", status_code=204)
async def delete_sheet_stock(item_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(SheetStockItem, item_id)
    if not item:
        raise HTTPException(404, "العنصر غير موجود")
    await db.delete(item)
    await db.commit()


# ── مخزون أطوال الأسياخ/النسالة (BarStockItem) ───────────────────────────

class BarStockItemIn(BaseModel):
    material_id: int
    length_cm: Decimal
    qty: int = 1
    source: str = "purchased"  # purchased | remnant
    notes: Optional[str] = None

    @validator("length_cm")
    def _len_positive(cls, v):
        if v is None or v <= 0:
            raise ValueError("الطول يجب أن يكون أكبر من صفر")
        return v

    @validator("qty")
    def _qty_positive(cls, v):
        if v is None or v < 1:
            raise ValueError("العدد يجب أن يكون 1 على الأقل")
        return v

    @validator("source")
    def _valid_source(cls, v):
        if v not in ("purchased", "remnant"):
            raise ValueError("المصدر يجب أن يكون 'purchased' أو 'remnant'")
        return v


@router.get("/bar-stock")
async def list_bar_stock(material_id: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    q = select(BarStockItem)
    if material_id:
        q = q.where(BarStockItem.material_id == material_id)
    rows = (await db.execute(q.order_by(BarStockItem.id.desc()))).scalars().all()
    return [
        {
            "id": r.id, "material_id": r.material_id,
            "length_cm": float(r.length_cm),
            "qty": r.qty, "source": r.source, "notes": r.notes,
        }
        for r in rows
    ]


@router.post("/bar-stock", status_code=201)
async def create_bar_stock(data: BarStockItemIn, db: AsyncSession = Depends(get_db)):
    mat = await db.get(Material, data.material_id)
    if not mat:
        raise HTTPException(404, "المادة غير موجودة")
    from app.core.database import current_tenant_id as _ctid_bs
    item = BarStockItem(
        tenant_id=_ctid_bs.get() or 1,
        material_id=data.material_id,
        length_cm=data.length_cm,
        qty=data.qty, source=data.source, notes=data.notes,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"id": item.id}


@router.delete("/bar-stock/{item_id}", status_code=204)
async def delete_bar_stock(item_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(BarStockItem, item_id)
    if not item:
        raise HTTPException(404, "العنصر غير موجود")
    await db.delete(item)
    await db.commit()
