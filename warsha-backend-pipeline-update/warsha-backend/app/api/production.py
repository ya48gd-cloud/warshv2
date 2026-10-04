"""
Production API — وحدة الإنتاج المتكاملة
"""
from datetime import date
from decimal import Decimal
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.api.auth import require_any, require_admin, require_admin_production
from app.models.models import (
    WorkOrder, Material, MaterialReservation,
    ScrapRecord, MrpSuggestion, StockCount, StockCountLine,
    BinLocation, MaterialBinStock, StockMovement, StockQuant,
    WorkOrderMember, Worker,
)
from app.services.production import (
    auto_reserve_materials, reserve_single_material, issue_material,
    record_scrap, complete_work_order, run_mrp, get_smart_alerts,
    get_scrap_inventory, create_scrap_sale,
    get_equipment_pipeline, get_equipment_pipeline_detail,
    backfill_equipment_pipeline,
)

router = APIRouter(prefix="/production", tags=["production"], dependencies=[Depends(require_any)])


# ── Schemas ────────────────────────────────────────────────────
class ScanIn(BaseModel):
    material_id: int
    actual_qty: float


class IssueIn(BaseModel):
    work_order_id: int
    material_id:   int
    qty:           Decimal
    stage:         str       # مرحلة الإنتاج: لحام، تجميع، ...


class ScrapIn(BaseModel):
    work_order_id: Optional[int] = None
    material_id:   int
    qty:           Decimal
    reason:        str
    stage:         Optional[str] = None


class ScrapSaleLineIn(BaseModel):
    material_id: int
    qty:         Decimal = Field(..., gt=0)
    unit_price:  Decimal = Field(..., ge=0)


class ScrapSaleIn(BaseModel):
    sale_date:      date
    buyer_name:     Optional[str] = None
    treasury_id:    int
    payment_method: str = Field("cash", max_length=20)
    notes:          Optional[str] = None
    lines:          List[ScrapSaleLineIn] = Field(..., min_length=1)


class ManualReserveIn(BaseModel):
    material_id: int
    qty:         Decimal


class BinLocationIn(BaseModel):
    code:        str = Field(..., min_length=1, max_length=30)
    name_ar:     Optional[str] = Field(None, max_length=100)
    zone:        Optional[str] = Field(None, max_length=20)
    shelf:       Optional[str] = Field(None, max_length=10)
    bin:         Optional[str] = Field(None, max_length=10)
    capacity_kg: Optional[float] = Field(None, ge=0)
    notes:       Optional[str] = None


# ── Alerts (لوحة التنبيهات) ────────────────────────────────────

@router.get("/alerts")
async def production_alerts(db: AsyncSession = Depends(get_db)):
    """تنبيهات الإنتاج الذكية — يرجّع list دايماً"""
    from app.core.database import current_tenant_id as _ctid_alerts
    _tid_alerts = _ctid_alerts.get()
    if _tid_alerts is None:
        # fail-closed: ممنوع نرجع لـ tenant 1 (كان `or 1` بيعرض تنبيهات ورشة تانية)
        raise HTTPException(401, "غير مسموح")
    try:
        result = await get_smart_alerts(db, _tid_alerts)
        return result if isinstance(result, list) else []
    except Exception:
        import logging
        logging.getLogger("warsha.production").warning("production_alerts failed", exc_info=True)
        return []


# ── محطة 17: لوحة تتبع دورة حياة المعدة (Equipment Pipeline) ───

@router.get("/pipeline")
async def production_pipeline(q: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """
    "المعدة X من أمر البيع Y — دلوقتي فين؟"
    يرجّع صف لكل أمر إنتاج: CustomerOrder → WorkOrder → WIP → FG → Delivery.
    q اختياري: بحث بكود أمر الإنتاج / اسم المعدة / اسم العميل / كود طلب البيع.
    """
    from app.core.database import current_tenant_id as _ctid_pl
    tid = _ctid_pl.get() or 1
    return await get_equipment_pipeline(db, tid, q=q)


@router.get("/pipeline/{work_order_id}")
async def production_pipeline_detail(work_order_id: int, db: AsyncSession = Depends(get_db)):
    """تفاصيل دورة حياة معدة واحدة (أمر إنتاج واحد)."""
    from app.core.database import current_tenant_id as _ctid_pld
    tid = _ctid_pld.get() or 1
    row = await get_equipment_pipeline_detail(db, tid, work_order_id)
    if not row:
        raise HTTPException(404, "أمر الإنتاج غير موجود")
    return row


# ── محطة 18: Backfill لأوامر الإنتاج القديمة (عملية إدارية لمرة واحدة) ──

@router.post("/pipeline/backfill", dependencies=[Depends(require_admin)])
async def production_pipeline_backfill(dry_run: bool = True, db: AsyncSession = Depends(get_db)):
    """
    تسجّل رصيد WIP/FG فعلي لأوامر الإنتاج القديمة اللي اتعملت قبل تفعيل
    محطة 14/15. شغّلها أول مرة بـ dry_run=true (الافتراضي) للمراجعة، ثم
    dry_run=false للتنفيذ الفعلي. آمنة تتكرر (idempotent) — لا تكرر أي
    مادة موجودة بالفعل. صلاحية admin فقط لأنها عملية تعديل بيانات جماعية.
    """
    from app.core.database import current_tenant_id as _ctid_bf
    tid = _ctid_bf.get() or 1
    return await backfill_equipment_pipeline(db, tid, dry_run=dry_run)


# ── Work Order Integration ─────────────────────────────────────

@router.post("/reserve/{work_order_id}", dependencies=[Depends(require_admin_production)])
async def reserve_materials(
    work_order_id: int,
    data: Optional[ManualReserveIn] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    حجز مواد لأمر الإنتاج.

    محطة 27 (تصليح جذري): كان هذا الـendpoint نسخة مطابقة تمامًا لـ
    /auto-reserve/{id} — بيتجاهل أي جسم مُرسَل ويعيد حجز كل بنود BOM من
    الصفر (ويمسح أي حجوزات سابقة، حتى لو اتصرف منها شيء فعليًا)، بينما
    شاشة "الحجز اليدوي" في الواجهة كانت بترسل {material_id, qty} تُتجاهل
    بالكامل — عقد مكسور بين الواجهة والـbackend.

    الآن: جسم فيه material_id/qty → حجز يدوي لهذه المادة بالذات بالكمية
    المطلوبة، من غير أي أثر على حجوزات باقي المواد لنفس الأمر. من غير
    جسم (أو جسم فاضي) → نفس السلوك القديم (حجز كل بنود BOM دفعة واحدة)
    توافقًا خلفيًا مع أي استدعاء موجود بالفعل بيعتمد على السلوك ده.
    """
    if data is not None:
        result = await reserve_single_material(db, work_order_id, data.material_id, data.qty)
    else:
        result = await auto_reserve_materials(db, work_order_id)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/{work_order_id}/reservations")
async def get_reservations(work_order_id: int, db: AsyncSession = Depends(get_db)):
    """عرض حجوزات مواد أمر الإنتاج"""
    res = await db.execute(
        select(MaterialReservation)
        .options(selectinload(MaterialReservation.material))
        .where(MaterialReservation.work_order_id == work_order_id)
    )
    items = res.scalars().all()
    return [{
        "id":            r.id,
        "material_id":   r.material_id,
        "material_code": r.material.code if r.material else "",
        "material_name": r.material.name_ar if r.material else "",
        "unit":          r.material.unit if r.material else "",
        "required_qty":  float(r.required_qty),
        "reserved_qty":  float(r.reserved_qty),
        "issued_qty":    float(r.issued_qty),
        "returned_qty":  float(r.returned_qty),
        "scrap_qty":     float(r.scrap_qty),
        "remaining":     float(r.required_qty - r.issued_qty - r.scrap_qty),
        "location_id":   r.location_id,
        "status":        r.status,
        "status_ar":     {
            "pending":          "معلق",
            "partial":          "جزئي",
            "reserved":         "محجوز",
            "partially_issued": "صُرف جزئياً",
            "fully_issued":     "صُرف كاملاً",
            "closed":           "مغلق",
            "cancelled":        "ملغي",
        }.get(r.status, r.status),
    } for r in items]


@router.delete("/reservations/{reservation_id}", dependencies=[Depends(require_admin_production)])
async def cancel_reservation(reservation_id: int, db: AsyncSession = Depends(get_db)):
    """إلغاء حجز مادة لم يُصرف منها شيء بعد"""
    r = await db.get(MaterialReservation, reservation_id)
    if not r:
        raise HTTPException(404, "الحجز غير موجود")
    if float(r.issued_qty or 0) > 0:
        raise HTTPException(400, "لا يمكن إلغاء حجز تم صرف جزء منه — صرف المواد المتبقية أو تواصل مع المخزون لعكس الصرف أولاً")
    await db.delete(r)
    await db.commit()
    return {"message": "تم إلغاء الحجز", "id": reservation_id}


@router.post("/issue", dependencies=[Depends(require_admin_production)])
async def issue_material_endpoint(data: IssueIn, db: AsyncSession = Depends(get_db)):
    """صرف مواد لأمر إنتاج في مرحلة معينة"""
    result = await issue_material(
        db, data.work_order_id, data.material_id, data.qty, data.stage
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/issue-by-reservation", dependencies=[Depends(require_admin_production)])
async def issue_by_reservation(
    reservation_id: int,
    db: AsyncSession = Depends(get_db)
):
    """صرف مواد باستخدام رقم الحجز مباشرة"""
    res = await db.get(MaterialReservation, reservation_id)
    if not res:
        raise HTTPException(404, "الحجز غير موجود")
    if res.status in ("closed", "cancelled"):
        raise HTTPException(400, "الحجز مغلق أو ملغي — لا يمكن الصرف منه")
    if (res.reserved_qty or Decimal("0")) - (res.issued_qty or Decimal("0")) <= 0:
        raise HTTPException(400, "لا توجد كمية محجوزة متبقية للصرف في هذا الحجز")
    result = await issue_material(
        db,
        res.work_order_id,
        res.material_id,
        res.reserved_qty - res.issued_qty,
        "production"
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/scrap", dependencies=[Depends(require_admin_production)])
async def record_scrap_endpoint(data: ScrapIn, db: AsyncSession = Depends(get_db)):
    """تسجيل هالك — في أمر إنتاج، أو هالك عام بدون أمر إنتاج"""
    if not (data.reason or "").strip():
        raise HTTPException(400, "سبب الهالك مطلوب")
    if data.work_order_id:
        _wo_sc = await db.get(WorkOrder, data.work_order_id)
        if not _wo_sc:
            raise HTTPException(404, "أمر الإنتاج غير موجود")
        if _wo_sc.status in ("done", "cancelled"):
            # هالك على أمر مكتمل كان بيزوّد actual_cost بعد تثبيت تكلفة FG
            # (نفس فجوة محطة 29 للعمالة) — يُسجَّل كهالك عام بدون أمر بدلًا من ذلك
            raise HTTPException(400, f"أمر الإنتاج {_wo_sc.code} {'مكتمل' if _wo_sc.status == 'done' else 'ملغي'} — سجّل الهالك كهالك عام بدون أمر إنتاج")
    result = await record_scrap(
        db, data.material_id, data.qty, data.reason,
        stage=data.stage, work_order_id=data.work_order_id,
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/scrap/inventory")
async def scrap_inventory_endpoint(db: AsyncSession = Depends(get_db)):
    """مخزون الهالك المتاح للبيع (مجمّع لكل مادة)"""
    from app.core.database import current_tenant_id as _ctid_sci_ep
    return await get_scrap_inventory(db, _ctid_sci_ep.get() or 1)


@router.post("/scrap-sales", dependencies=[Depends(require_admin_production)])
async def create_scrap_sale_endpoint(data: ScrapSaleIn, db: AsyncSession = Depends(get_db)):
    """بيع كمية من الهالك المسجل"""
    result = await create_scrap_sale(db, data.model_dump())
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/scrap-sales")
async def list_scrap_sales(db: AsyncSession = Depends(get_db)):
    """سجل عمليات بيع الهالك"""
    from app.models.models import ScrapSale
    sales = (await db.execute(
        select(ScrapSale)
        .options(selectinload(ScrapSale.treasury), selectinload(ScrapSale.lines))
        .order_by(ScrapSale.sale_date.desc(), ScrapSale.id.desc()).limit(100)
    )).scalars().all()
    return [{
        "id":             s.id,
        "code":           s.code,
        "sale_date":      s.sale_date.isoformat() if s.sale_date else None,
        "buyer_name":     s.buyer_name,
        "treasury_name":  s.treasury.name if s.treasury else "—",
        "payment_method": s.payment_method,
        "total_amount":   float(s.total_amount or 0),
        "lines_count":    len(s.lines),
        "notes":          s.notes,
    } for s in sales]


@router.get("/scrap-sales/{sale_id}")
async def scrap_sale_detail(sale_id: int, db: AsyncSession = Depends(get_db)):
    """تفاصيل عملية بيع هالك"""
    from app.models.models import ScrapSale, ScrapSaleLine
    sale = (await db.execute(
        select(ScrapSale)
        .options(selectinload(ScrapSale.treasury),
                 selectinload(ScrapSale.lines).selectinload(ScrapSaleLine.material))
        .where(ScrapSale.id == sale_id)
    )).scalar_one_or_none()
    if not sale:
        raise HTTPException(404, "غير موجود")
    return {
        "id":             sale.id,
        "code":           sale.code,
        "sale_date":      sale.sale_date.isoformat() if sale.sale_date else None,
        "buyer_name":     sale.buyer_name,
        "treasury_name":  sale.treasury.name if sale.treasury else "—",
        "payment_method": sale.payment_method,
        "total_amount":   float(sale.total_amount or 0),
        "notes":          sale.notes,
        "lines": [{
            "material_id":   l.material_id,
            "material_name": l.material.name_ar if l.material else "—",
            "material_code": l.material.code if l.material else "—",
            "qty":           float(l.qty),
            "unit_price":    float(l.unit_price),
            "total_price":   float(l.total_price),
        } for l in sale.lines],
    }


@router.post("/complete/{work_order_id}", dependencies=[Depends(require_admin_production)])
async def complete_wo(work_order_id: int, db: AsyncSession = Depends(get_db)):
    """إنهاء أمر الإنتاج — إرجاع الزائد + إضافة للمخزن"""
    result = await complete_work_order(db, work_order_id)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/{work_order_id}/cost-breakdown")
async def cost_breakdown(work_order_id: int, db: AsyncSession = Depends(get_db)):
    """تحليل تكاليف أمر الإنتاج التفصيلي"""
    from app.models.models import CostLine
    wo = await db.get(WorkOrder, work_order_id)
    if not wo: raise HTTPException(404)

    lines = (await db.execute(
        select(CostLine).where(CostLine.work_order_id == work_order_id)
        .order_by(CostLine.cost_type)
    )).scalars().all()

    by_type = {"material": 0.0, "labor": 0.0, "overhead": 0.0}
    for l in lines:
        by_type[l.cost_type] = by_type.get(l.cost_type, 0) + float(l.total_cost)

    # هالك
    scrap_total = sum(
        float(s.total_cost or 0) for s in (await db.execute(
            select(ScrapRecord).where(ScrapRecord.work_order_id == work_order_id)
        )).scalars().all()
    )

    return {
        "work_order":    wo.code,
        "planned_cost":  float(wo.planned_cost or 0),
        "actual_cost":   float(wo.actual_cost or 0),
        "variance_pct": (
            round((float(wo.actual_cost or 0) - float(wo.planned_cost)) / float(wo.planned_cost) * 100, 1)
            if wo.planned_cost and float(wo.planned_cost) > 0 else None
        ),
        "by_type":       by_type,
        "scrap_cost":    scrap_total,
        "lines": [{
            "type":        l.cost_type,
            "description": l.description,
            "qty":         float(l.qty),
            "unit_cost":   float(l.unit_cost),
            "total":       float(l.total_cost),
        } for l in lines],
    }


@router.get("/scrap/report")
async def scrap_report(db: AsyncSession = Depends(get_db)):
    """تقرير الهالك الإجمالي"""
    scraps = (await db.execute(
        select(ScrapRecord)
        .options(selectinload(ScrapRecord.material),
                 selectinload(ScrapRecord.work_order))
        .order_by(ScrapRecord.recorded_at.desc()).limit(100)
    )).scalars().all()

    total_cost = sum(float(s.total_cost or 0) for s in scraps)
    return {
        "total_scrap_cost": total_cost,
        "records": [{
            "id":           s.id,
            "wo_code":      s.work_order.code if s.work_order else "—",
            "material":     s.material.name_ar if s.material else "—",
            "qty":          float(s.qty),
            "unit_cost":    float(s.unit_cost or 0),
            "total_cost":   float(s.total_cost or 0),
            "reason":       s.reason,
            "stage":        s.stage,
            "date":         s.recorded_at.date().isoformat() if s.recorded_at else None,
        } for s in scraps]
    }


# ── MRP ────────────────────────────────────────────────────────

@router.get("/mrp/suggestions")
async def get_mrp_suggestions(db: AsyncSession = Depends(get_db)):
    """اقتراحات المشتريات من محرك MRP"""
    from app.core.database import current_tenant_id as _ctid_mrp_ep1
    return await run_mrp(db, _ctid_mrp_ep1.get())  # بدون fallback لـ tenant 1 — run_mrp بيرجّع [] لو مفيش ورشة


@router.get("/mrp")
async def get_mrp_alias(db: AsyncSession = Depends(get_db)):
    """Alias for /mrp/suggestions"""
    from app.core.database import current_tenant_id as _ctid_mrp_ep2
    return await run_mrp(db, _ctid_mrp_ep2.get())  # بدون fallback لـ tenant 1 — run_mrp بيرجّع [] لو مفيش ورشة


@router.post("/mrp/run", dependencies=[Depends(require_admin_production)])
async def run_mrp_endpoint(db: AsyncSession = Depends(get_db)):
    """تشغيل محرك MRP وإرجاع الاقتراحات"""
    from app.core.database import current_tenant_id as _ctid_mrp_ep3
    return await run_mrp(db, _ctid_mrp_ep3.get())  # بدون fallback لـ tenant 1 — run_mrp بيرجّع [] لو مفيش ورشة


@router.post("/mrp/{suggestion_id}/approve", dependencies=[Depends(require_admin_production)])
async def approve_mrp(suggestion_id: int, db: AsyncSession = Depends(get_db)):
    """الموافقة على اقتراح MRP — ينشئ PurchaseOrder تلقائياً"""
    from app.models.models import PurchaseOrder, PurchaseOrderLine
    from app.core.database import current_tenant_id as _ctid_mrp
    from datetime import date as _date
    from decimal import Decimal as _Dec
    from sqlalchemy import func as _func

    s = await db.get(MrpSuggestion, suggestion_id)
    if not s:
        raise HTTPException(404)
    if s.status == "ordered":
        raise HTTPException(400, "الاقتراح تم اعتماده مسبقاً")
    if s.suggested_qty is None or _Dec(str(s.suggested_qty)) <= 0:
        raise HTTPException(400, "الكمية المقترحة غير صالحة — لا يمكن إنشاء أمر شراء بكمية صفر")

    mat = await db.get(Material, s.material_id)
    if not mat:
        raise HTTPException(400, "مادة الاقتراح غير موجودة — لا يمكن إنشاء أمر شراء")

    # توليد كود PO تلقائي
    # PurchaseOrder.code فريد على مستوى القاعدة كلها (مش لكل tenant) — فحص
    # التكرار لازم يتخطى فلتر الـtenant التلقائي، وإلا كود مستخدم عند ورشة
    # تانية بيعدّي الفحص ويفشل الـINSERT بـIntegrityError (500).
    _tid = _ctid_mrp.get() or 1
    _count = await db.scalar(_func.count(PurchaseOrder.id).select())
    _n = (_count or 0) + 1
    while True:
        _code = f"PO-MRP-{_n:04d}"
        _ex = await db.scalar(
            select(PurchaseOrder.id).where(PurchaseOrder.code == _code)
            .execution_options(skip_tenant_filter=True)
        )
        if not _ex:
            break
        _n += 1

    unit_cost = mat.last_purchase_price or mat.unit_cost or _Dec("0") if mat else _Dec("0")
    qty = _Dec(str(s.suggested_qty or 0))
    total = qty * unit_cost

    po = PurchaseOrder(
        tenant_id=_tid,
        code=_code,
        supplier_id=mat.supplier_id if mat else None,
        order_date=_date.today(),
        expected_date=s.expected_date,
        subtotal=total,
        total=total,
        status="draft",
        notes=f"أُنشئ تلقائياً من اقتراح MRP #{s.id}",
    )
    db.add(po)
    await db.flush()

    if mat:
        db.add(PurchaseOrderLine(
            order_id=po.id,
            material_id=s.material_id,
            description=mat.name_ar,
            ordered_qty=qty,
            unit=mat.unit,
            unit_cost=unit_cost,
            total_cost=total,
        ))

    s.status = "ordered"
    await db.commit()
    return {"id": s.id, "status": "ordered", "po_id": po.id, "po_code": _code}


@router.post("/mrp/{suggestion_id}/ignore", dependencies=[Depends(require_admin_production)])
async def ignore_mrp(suggestion_id: int, db: AsyncSession = Depends(get_db)):
    """تجاهل اقتراح MRP"""
    s = await db.get(MrpSuggestion, suggestion_id)
    if not s: raise HTTPException(404)
    if s.status == "ordered":
        raise HTTPException(400, "الاقتراح تم اعتماده وإنشاء أمر شراء له بالفعل — لا يمكن تجاهله")
    s.status = "ignored"
    await db.commit()
    return {"id": s.id, "status": "ignored"}


# ── Bin Locations ──────────────────────────────────────────────

@router.get("/bins")
async def list_bins(db: AsyncSession = Depends(get_db)):
    bins = (await db.execute(
        select(BinLocation).where(BinLocation.is_active == True).order_by(BinLocation.code)
    )).scalars().all()
    return [{
        "id": b.id, "code": b.code, "name_ar": b.name_ar,
        "zone": b.zone, "shelf": b.shelf, "bin": b.bin,
        "capacity_kg": float(b.capacity_kg) if b.capacity_kg else None,
    } for b in bins]


@router.post("/bins", status_code=201, dependencies=[Depends(require_admin_production)])
async def create_bin(data: BinLocationIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_bin
    payload = data.model_dump()
    payload['code'] = (payload.get('code') or '').strip()
    if not payload['code']:
        raise HTTPException(400, "كود الموقع مطلوب")
    payload['tenant_id'] = _ctid_bin.get() or 1
    # كود الموقع فريد لكل ورشة (uq_binlocation_tenant_code) — نرجّع 400 واضح
    # بدل IntegrityError (500) عند التكرار
    _dup_bin = await db.scalar(
        select(BinLocation.id).where(
            BinLocation.tenant_id == payload['tenant_id'],
            BinLocation.code == payload['code'],
        )
    )
    if _dup_bin:
        raise HTTPException(400, f"كود الموقع '{payload['code']}' مستخدم بالفعل")
    b = BinLocation(**payload)
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return {"id": b.id, "code": b.code}


@router.get("/bins/{bin_id}/stock")
async def bin_stock(bin_id: int, db: AsyncSession = Depends(get_db)):
    """محتويات موقع مخزن معين"""
    items = (await db.execute(
        select(MaterialBinStock)
        .options(selectinload(MaterialBinStock.material))
        .where(MaterialBinStock.bin_id == bin_id, MaterialBinStock.qty > 0)
    )).scalars().all()
    return [{
        "material_id":   i.material_id,
        "material_code": i.material.code,
        "material_name": i.material.name_ar,
        "qty":           float(i.qty),
        "unit":          i.material.unit,
    } for i in items]


# ── Stock Count (جرد) ──────────────────────────────────────────

class StockCountIn(BaseModel):
    warehouse_id: Optional[int] = None
    notes: Optional[str] = None


_PROD_PREFIX_WH = (("WIP-", "WIP"), ("FG-", "FG"), ("SCRAP-", "SCRAP"), ("REMNANT-", "REMNANT"))
# حساب المخزون في دليل الحسابات حسب نوع المخزن
_WH_TYPE_ACCOUNT = {"wip": "1320", "finished_goods": "1330"}
_WH_CODE_ACCOUNT = {"WIP": "1320", "FG": "1330"}


def _require_tenant():
    from app.core.database import current_tenant_id as _ctid_sc
    tid = _ctid_sc.get()
    if tid is None:
        raise HTTPException(401, "غير مسموح")
    return tid


async def _warehouse_qty_map(db: AsyncSession, tid: int, warehouse_id: int) -> dict:
    """{material_id: Decimal} — رصيد كل مادة في مخزن معيّن (مجموع مواقعه)."""
    from sqlalchemy import func as _func
    rows = (await db.execute(
        select(StockQuant.material_id, _func.coalesce(_func.sum(StockQuant.qty), 0))
        .join(BinLocation, BinLocation.id == StockQuant.location_id)
        .where(StockQuant.tenant_id == tid, BinLocation.warehouse_id == warehouse_id)
        .group_by(StockQuant.material_id)
    )).all()
    return {mid: Decimal(str(q)) for mid, q in rows}


async def _get_count(db: AsyncSession, tid: int, count_id: int) -> StockCount:
    count = await db.scalar(select(StockCount).where(StockCount.id == count_id, StockCount.tenant_id == tid))
    if not count:
        raise HTTPException(404, "جلسة الجرد غير موجودة")
    return count


async def _count_dict(db: AsyncSession, c: StockCount, wh_names: dict) -> dict:
    from sqlalchemy import func as _func
    total = await db.scalar(select(_func.count()).select_from(StockCountLine).where(StockCountLine.count_id == c.id))
    scanned = await db.scalar(select(_func.count()).select_from(StockCountLine).where(
        StockCountLine.count_id == c.id, StockCountLine.actual_qty.isnot(None)))
    return {
        "id": c.id, "date": str(c.count_date), "status": c.status, "notes": c.notes,
        "warehouse_id": c.warehouse_id,
        "warehouse_name": wh_names.get(c.warehouse_id) if c.warehouse_id else "كل المخازن",
        "total_items": total or 0, "scanned_items": scanned or 0,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/stock-count")
async def list_stock_counts(db: AsyncSession = Depends(get_db)):
    """قائمة جلسات الجرد (الأحدث أولاً)"""
    from app.models.models import Warehouse
    tid = _require_tenant()
    counts = (await db.execute(
        select(StockCount).where(StockCount.tenant_id == tid).order_by(StockCount.created_at.desc(), StockCount.id.desc())
    )).scalars().all()
    wh_names = dict((await db.execute(select(Warehouse.id, Warehouse.name_ar).where(Warehouse.tenant_id == tid))).all())
    return [await _count_dict(db, c, wh_names) for c in counts]


@router.get("/stock-count/{count_id}")
async def get_stock_count(count_id: int, db: AsyncSession = Depends(get_db)):
    """ورقة الجرد: كل صنف بالرصيد الدفتري الحالي (في المخزن ده) والكمية المعدودة."""
    from app.models.models import Warehouse
    tid = _require_tenant()
    count = await _get_count(db, tid, count_id)
    wh_names = dict((await db.execute(select(Warehouse.id, Warehouse.name_ar).where(Warehouse.tenant_id == tid))).all())
    lines = (await db.execute(
        select(StockCountLine).options(selectinload(StockCountLine.material))
        .where(StockCountLine.count_id == count.id).order_by(StockCountLine.id)
    )).scalars().all()
    live = await _warehouse_qty_map(db, tid, count.warehouse_id) if count.warehouse_id else None
    out_lines = []
    for ln in lines:
        m = ln.material
        # الجلسة المفتوحة: الدفتري = الرصيد الحالي (الإغلاق بيحسب الفرق عليه).
        # المغلقة: الدفتري = اللي اتقفلت عليه (system_qty اتحدّث وقت الإغلاق).
        if count.status == "in_progress":
            book = live.get(ln.material_id, Decimal("0")) if live is not None else Decimal(str(m.stock_qty or 0)) if m else Decimal("0")
        else:
            book = Decimal(str(ln.system_qty or 0))
        actual = ln.actual_qty
        diff = (Decimal(str(actual)) - book) if actual is not None else None
        uc = Decimal(str((m.unit_cost if m else 0) or 0))
        out_lines.append({
            "material_id": ln.material_id,
            "code": m.code if m else None, "name": m.name_ar if m else None,
            "unit": m.unit if m else None, "unit_cost": float(uc),
            "book_qty": float(book),
            "actual_qty": float(actual) if actual is not None else None,
            "difference": float(diff) if diff is not None else None,
            "difference_value": float(diff * uc) if diff is not None else None,
            "scanned_at": ln.scanned_at.isoformat() if ln.scanned_at else None,
        })
    return {**(await _count_dict(db, count, wh_names)), "lines": out_lines}


@router.post("/stock-count", status_code=201, dependencies=[Depends(require_admin_production)])
async def create_stock_count(data: Optional[StockCountIn] = None, db: AsyncSession = Depends(get_db)):
    """إنشاء جلسة جرد لمخزن معيّن (أو كل المخازن لو warehouse_id مش متبعت —
    السلوك القديم). الأصناف = اللي ليها رصيد في المخزن ده؛ أي صنف تاني يتلاقى
    فعليًا بيتضاف وقت العدّ."""
    from app.models.models import Warehouse
    tid = _require_tenant()
    data = data or StockCountIn()
    notes = (data.notes or "").strip() or None
    if notes and len(notes) > 1000:
        raise HTTPException(400, "الملاحظات طويلة جداً (الحد 1000 حرف)")

    open_counts = (await db.execute(
        select(StockCount).where(StockCount.tenant_id == tid, StockCount.status == "in_progress")
    )).scalars().all()
    # جردين مفتوحين على نفس المخزن (أو جرد "كل المخازن" مع أي جرد تاني) كانوا
    # بيسوّوا نفس الرصيد مرتين عند الإغلاق
    for oc in open_counts:
        if oc.warehouse_id is None or data.warehouse_id is None or oc.warehouse_id == data.warehouse_id:
            raise HTTPException(409, f"فيه جرد مفتوح بالفعل (#{oc.id}) يشمل نفس المخزن — أغلقه أو ألغِه الأول")

    wh = None
    if data.warehouse_id is not None:
        wh = await db.scalar(select(Warehouse).where(Warehouse.id == data.warehouse_id, Warehouse.tenant_id == tid))
        if not wh:
            raise HTTPException(404, "المخزن غير موجود")
        if not wh.is_active:
            raise HTTPException(400, "المخزن معطّل — فعّله الأول قبل جرده")

    count = StockCount(tenant_id=tid, count_date=date.today(), status="in_progress",
                       warehouse_id=wh.id if wh else None, notes=notes)
    db.add(count)
    await db.flush()

    if wh:
        qty_map = await _warehouse_qty_map(db, tid, wh.id)
        mats = (await db.execute(select(Material).where(
            Material.tenant_id == tid, Material.id.in_([mid for mid, q in qty_map.items() if q != 0])
        ))).scalars().all() if qty_map else []
        for m in mats:
            db.add(StockCountLine(tenant_id=tid, count_id=count.id, material_id=m.id,
                                  system_qty=qty_map.get(m.id, Decimal("0"))))
    else:
        mats = (await db.execute(select(Material).where(Material.tenant_id == tid))).scalars().all()
        for m in mats:
            db.add(StockCountLine(tenant_id=tid, count_id=count.id, material_id=m.id, system_qty=m.stock_qty))
    await db.commit()
    return {"id": count.id, "date": str(count.count_date), "items": len(mats),
            "warehouse_id": count.warehouse_id}


@router.post("/stock-count/{count_id}/scan", dependencies=[Depends(require_admin_production)])
async def scan_item(count_id: int, data: ScanIn, db: AsyncSession = Depends(get_db)):
    """تسجيل كمية فعلية. صنف مش في الورقة (لقيناه في المخزن ومش متسجل عليه)
    بيتضاف كسطر جديد رصيده الدفتري صفر."""
    import math
    tid = _require_tenant()
    actual_qty = data.actual_qty
    if actual_qty is None or not math.isfinite(actual_qty) or actual_qty < 0:
        raise HTTPException(400, "الكمية الفعلية يجب أن تكون صفر أو أكبر")
    if actual_qty > 1e9:
        raise HTTPException(400, "الكمية الفعلية كبيرة بشكل غير منطقي")
    count = await _get_count(db, tid, count_id)
    if count.status != "in_progress":
        raise HTTPException(400, "جلسة الجرد مغلقة — لا يمكن تسجيل كميات عليها")
    line = (await db.execute(select(StockCountLine).where(
        StockCountLine.count_id == count_id, StockCountLine.material_id == data.material_id,
    ))).scalar_one_or_none()
    if not line:
        mat = await db.scalar(select(Material).where(Material.id == data.material_id, Material.tenant_id == tid))
        if not mat:
            raise HTTPException(404, "المادة غير موجودة")
        line = StockCountLine(tenant_id=tid, count_id=count.id, material_id=mat.id, system_qty=Decimal("0"))
        db.add(line)

    from datetime import datetime
    line.actual_qty = Decimal(str(actual_qty))
    line.difference = line.actual_qty - (line.system_qty or 0)
    line.scanned_at = datetime.utcnow()
    await db.commit()
    return {
        "material_id": data.material_id,
        "system_qty": float(line.system_qty or 0),
        "actual_qty": actual_qty,
        "difference": float(line.difference),
    }


@router.delete("/stock-count/{count_id}/scan/{material_id}", dependencies=[Depends(require_admin_production)])
async def unscan_item(count_id: int, material_id: int, db: AsyncSession = Depends(get_db)):
    """مسح الكمية المعدودة لصنف (رجوعه "لم يُعد") — لتصحيح إدخال غلط قبل الإغلاق."""
    tid = _require_tenant()
    count = await _get_count(db, tid, count_id)
    if count.status != "in_progress":
        raise HTTPException(400, "جلسة الجرد مغلقة")
    line = (await db.execute(select(StockCountLine).where(
        StockCountLine.count_id == count_id, StockCountLine.material_id == material_id,
    ))).scalar_one_or_none()
    if not line:
        raise HTTPException(404, "الصنف غير موجود في جلسة الجرد")
    line.actual_qty = None
    line.difference = None
    line.scanned_at = None
    await db.commit()
    return {"material_id": material_id, "actual_qty": None}


@router.post("/stock-count/{count_id}/cancel", dependencies=[Depends(require_admin_production)])
async def cancel_stock_count(count_id: int, db: AsyncSession = Depends(get_db)):
    """إلغاء جرد مفتوح من غير أي تسوية على المخزون."""
    tid = _require_tenant()
    count = await _get_count(db, tid, count_id)
    if count.status != "in_progress":
        raise HTTPException(400, "جلسة الجرد مغلقة بالفعل")
    count.status = "cancelled"
    await db.commit()
    return {"id": count_id, "status": "cancelled"}


@router.post("/stock-count/{count_id}/close", dependencies=[Depends(require_admin_production)])
async def close_stock_count(count_id: int, db: AsyncSession = Depends(get_db)):
    """إغلاق الجرد وتطبيق الفروقات على المخزن (الأصناف المعدودة بس).

    جرد مخزن: الفرق = المعدود − رصيد المادة في المخزن ده وقت الإغلاق، والتسوية
    بتتسجل على موقع المخزن ده بالظبط. + قيد تسوية (زيادة/عجز) لكل حساب مخزون.
    """
    from app.models.models import Warehouse
    from app.services.inventory_ledger import get_or_create_warehouse_bin, _sync_quant, _next_movement_no
    from app.services.auto_journal import journal_stock_count_adjustment
    tid = _require_tenant()
    count = await _get_count(db, tid, count_id)
    if count.status != "in_progress":
        # إغلاق جلسة مغلقة بالفعل كان بيعيد تسجيل نفس حركات التسوية تاني
        raise HTTPException(400, "جلسة الجرد مغلقة بالفعل")

    wh = await db.get(Warehouse, count.warehouse_id) if count.warehouse_id else None
    wh_map = await _warehouse_qty_map(db, tid, wh.id) if wh else None
    wh_loc = await get_or_create_warehouse_bin(db, tid, wh.code) if wh else None

    lines = (await db.execute(select(StockCountLine).where(
        StockCountLine.count_id == count_id, StockCountLine.actual_qty.is_not(None),
    ))).scalars().all()

    adjustments = 0
    surplus_value = Decimal("0")
    shortage_value = Decimal("0")
    by_account: dict = {}
    for line in lines:
        mat = await db.get(Material, line.material_id)
        if not mat:
            continue
        if wh:
            book = wh_map.get(mat.id, Decimal("0"))
            loc = wh_loc
            account = _WH_TYPE_ACCOUNT.get(wh.warehouse_type, "1310")
        else:
            book = mat.stock_qty or Decimal("0")
            wh_code = next((c for pfx, c in _PROD_PREFIX_WH if (mat.code or "").startswith(pfx)), "RAW")
            loc = await get_or_create_warehouse_bin(db, tid, wh_code)
            account = _WH_CODE_ACCOUNT.get(wh_code, "1310")
        # الدفتري اللي اتقفل عليه الجرد فعلاً — عشان الورقة المغلقة تعرض الحقيقة
        line.system_qty = book
        diff = line.actual_qty - book
        line.difference = diff
        if diff == 0:
            continue
        if (mat.stock_qty or Decimal("0")) + diff < 0:
            raise HTTPException(400, f"تسوية {mat.name_ar} هتخلّي الرصيد الإجمالي سالب — راجع الكمية")
        mat.stock_qty = (mat.stock_qty or Decimal("0")) + diff
        await _sync_quant(db, tenant_id=tid, material_id=mat.id, delta_qty=diff, location_id=loc.id)
        uc = mat.unit_cost or Decimal("0")
        value = abs(diff) * uc
        db.add(StockMovement(
            tenant_id=tid, material_id=mat.id,
            movement_type="in" if diff > 0 else "out",
            qty=abs(diff), unit_cost=uc, total_cost=value,
            reference=f"STOCKCOUNT-{count_id}",
            notes=f"تسوية جرد #{count_id}{' — ' + wh.name_ar if wh else ''} — فرق: {float(diff):+.3f}",
            movement_date=count.count_date, withdrawal_unit="adjustment",
            movement_no=await _next_movement_no(db, tid),
            source_doc_type="stock_count", source_doc_id=count_id,
            destination_location_id=loc.id if diff > 0 else None,
            source_location_id=loc.id if diff < 0 else None,
        ))
        by_account[account] = by_account.get(account, Decimal("0")) + (value if diff > 0 else -value)
        if diff > 0:
            surplus_value += value
        else:
            shortage_value += value
        adjustments += 1

    for account, signed in by_account.items():
        await journal_stock_count_adjustment(db, tid, count_id, account, signed, count.count_date)

    count.status = "completed"
    await db.commit()
    return {
        "count_id": count_id, "status": "completed", "adjustments": adjustments,
        "surplus_value": float(surplus_value), "shortage_value": float(shortage_value),
        "uncounted": await db.scalar(select(func.count()).select_from(StockCountLine).where(
            StockCountLine.count_id == count_id, StockCountLine.actual_qty.is_(None))) or 0,
    }


# ── Auto-reserve from BOM endpoint ────────────────────────────────────────
@router.post("/auto-reserve/{work_order_id}", dependencies=[Depends(require_admin_production)])
async def auto_reserve(work_order_id: int, db: AsyncSession = Depends(get_db)):
    """Auto-reserve all BOM materials for a work order. Returns shortages."""
    result = await auto_reserve_materials(db, work_order_id)
    if "error" in result:
        _wo_chk = await db.get(WorkOrder, work_order_id)
        raise HTTPException(404 if not _wo_chk else 400, result["error"])
    return result


# ── Work Order Team (عمالة أمر الإنتاج) ─────────────────────────

class WOMemberIn(BaseModel):
    worker_id:     int
    role:          str = Field("فني", max_length=100)
    hourly_rate:   Decimal = Field(Decimal("0"), ge=0)
    planned_hours: Decimal = Field(Decimal("0"), ge=0)
    notes:         Optional[str] = None


@router.get("/work-orders/{wo_id}/team")
async def list_team(wo_id: int, db: AsyncSession = Depends(get_db)):
    """عرض فريق العمل المعيّن على أمر الإنتاج"""
    res = await db.execute(
        select(WorkOrderMember)
        .options(selectinload(WorkOrderMember.worker))
        .where(WorkOrderMember.work_order_id == wo_id)
    )
    items = res.scalars().all()
    return [{
        "id":            m.id,
        "worker_id":     m.worker_id,
        "worker_name":   m.worker.name if m.worker else "",
        "role":          m.role,
        "hourly_rate":   float(m.hourly_rate),
        "planned_hours": float(m.planned_hours),
        "actual_hours":  float(m.actual_hours),
        "labor_cost":    float(m.actual_hours * m.hourly_rate),
        "notes":         m.notes,
    } for m in items]


@router.post("/work-orders/{wo_id}/team", status_code=201, dependencies=[Depends(require_admin_production)])
async def add_team_member(wo_id: int, data: WOMemberIn, db: AsyncSession = Depends(get_db)):
    """إضافة/تحديث عامل في فريق أمر الإنتاج"""
    wo = await db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(404, "أمر الإنتاج غير موجود")
    if wo.status == "cancelled":
        raise HTTPException(400, "أمر الإنتاج ملغي — لا يمكن تعديل فريق العمل")
    # العامل لازم يكون موجود وتابع لنفس الورشة (db.get بيمر بفلتر الـtenant)
    if not await db.get(Worker, data.worker_id):
        raise HTTPException(404, "العامل غير موجود")
    if wo.status == "done":
        # محطة 29: أمر الإنتاج المكتمل خلاص عدّى تكلفة العمالة (actual_cost)
        # مرة واحدة وللأبد في complete_work_order()، ورصيد FG-{code} اتسجّل
        # بتكلفة وحدة ثابتة مبنية عليها. تعديل الفريق بعد كده كان بيوهم إن
        # التكلفة اتحدّثت (الرد بيرجّع labor_cost محسوب) من غير ما يمس
        # actual_cost أو FG.unit_cost فعليًا خالص — تناقض صامت.
        raise HTTPException(400, "أمر الإنتاج مكتمل بالفعل — لا يمكن تعديل فريق العمل بعد الإنهاء")

    existing = (await db.execute(
        select(WorkOrderMember).where(
            WorkOrderMember.work_order_id == wo_id,
            WorkOrderMember.worker_id == data.worker_id,
        )
    )).scalar_one_or_none()
    if existing:
        existing.role = data.role
        existing.hourly_rate = data.hourly_rate
        existing.planned_hours = data.planned_hours
        if data.notes is not None:
            existing.notes = data.notes
        await db.commit()
        return {"id": existing.id, "message": "تم التحديث"}

    from app.core.database import current_tenant_id as _ctid_wom
    _wom_data = data.model_dump()
    _wom_data['tenant_id'] = _ctid_wom.get() or 1
    m = WorkOrderMember(work_order_id=wo_id, **_wom_data)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return {"id": m.id, "message": "تمت الإضافة"}


@router.patch("/work-orders/{wo_id}/team/{mid}/hours", dependencies=[Depends(require_admin_production)])
async def update_actual_hours(wo_id: int, mid: int, actual_hours: Decimal, db: AsyncSession = Depends(get_db)):
    """تحديث ساعات العمل الفعلية لعامل"""
    if actual_hours is None or actual_hours < 0:
        raise HTTPException(400, "ساعات العمل الفعلية لا يمكن أن تكون سالبة")
    m = await db.get(WorkOrderMember, mid)
    if not m or m.work_order_id != wo_id:
        raise HTTPException(404)
    wo = await db.get(WorkOrder, wo_id)
    if wo and wo.status == "done":
        # محطة 29: نفس الفجوة أعلاه — بعد الإنهاء، actual_cost وFG.unit_cost
        # مقفولين على القيمة المحسوبة وقت الإنهاء؛ تحديث الساعات هنا كان
        # هيرجّع labor_cost جديد للواجهة من غير أي أثر حقيقي على أي حاجة.
        raise HTTPException(400, "أمر الإنتاج مكتمل بالفعل — لا يمكن تعديل ساعات العمل بعد الإنهاء")
    m.actual_hours = actual_hours
    await db.commit()
    return {"actual_hours": float(m.actual_hours), "labor_cost": float(m.actual_hours * m.hourly_rate)}


@router.delete("/work-orders/{wo_id}/team/{mid}", dependencies=[Depends(require_admin_production)])
async def remove_team_member(wo_id: int, mid: int, db: AsyncSession = Depends(get_db)):
    """إزالة عامل من فريق أمر الإنتاج"""
    m = await db.get(WorkOrderMember, mid)
    if not m or m.work_order_id != wo_id:
        raise HTTPException(404)
    wo = await db.get(WorkOrder, wo_id)
    if wo and wo.status == "done":
        raise HTTPException(400, "أمر الإنتاج مكتمل بالفعل — لا يمكن حذف عضو من فريق العمل بعد الإنهاء")
    await db.delete(m)
    await db.commit()
    return {"message": "تم الحذف"}
