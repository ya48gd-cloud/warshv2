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
from pydantic import BaseModel

from app.core.database import get_db
from app.api.auth import require_any, require_admin, require_admin_production
from app.models.models import (
    WorkOrder, Material, MaterialReservation,
    ScrapRecord, MrpSuggestion, StockCount, StockCountLine,
    BinLocation, MaterialBinStock, StockMovement,
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
    qty:         Decimal
    unit_price:  Decimal


class ScrapSaleIn(BaseModel):
    sale_date:      date
    buyer_name:     Optional[str] = None
    treasury_id:    int
    payment_method: str = "cash"
    notes:          Optional[str] = None
    lines:          List[ScrapSaleLineIn]


class ManualReserveIn(BaseModel):
    material_id: int
    qty:         Decimal


class BinLocationIn(BaseModel):
    code:        str
    name_ar:     Optional[str] = None
    zone:        Optional[str] = None
    shelf:       Optional[str] = None
    bin:         Optional[str] = None
    capacity_kg: Optional[float] = None
    notes:       Optional[str] = None


# ── Alerts (لوحة التنبيهات) ────────────────────────────────────

@router.get("/alerts")
async def production_alerts(db: AsyncSession = Depends(get_db)):
    """تنبيهات الإنتاج الذكية — يرجّع list دايماً"""
    from app.core.database import current_tenant_id as _ctid_alerts
    try:
        result = await get_smart_alerts(db, _ctid_alerts.get() or 1)
        return result if isinstance(result, list) else []
    except Exception:
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
    return await run_mrp(db, _ctid_mrp_ep1.get() or 1)


@router.get("/mrp")
async def get_mrp_alias(db: AsyncSession = Depends(get_db)):
    """Alias for /mrp/suggestions"""
    from app.core.database import current_tenant_id as _ctid_mrp_ep2
    return await run_mrp(db, _ctid_mrp_ep2.get() or 1)


@router.post("/mrp/run", dependencies=[Depends(require_admin_production)])
async def run_mrp_endpoint(db: AsyncSession = Depends(get_db)):
    """تشغيل محرك MRP وإرجاع الاقتراحات"""
    from app.core.database import current_tenant_id as _ctid_mrp_ep3
    return await run_mrp(db, _ctid_mrp_ep3.get() or 1)


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

    # توليد كود PO تلقائي
    _tid = _ctid_mrp.get() or 1
    _count = await db.scalar(_func.count(PurchaseOrder.id).select())
    _n = (_count or 0) + 1
    while True:
        _code = f"PO-MRP-{_n:04d}"
        _ex = await db.scalar(
            select(PurchaseOrder.id).where(PurchaseOrder.code == _code)
        )
        if not _ex:
            break
        _n += 1

    mat = await db.get(Material, s.material_id)
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
    payload['tenant_id'] = _ctid_bin.get() or 1
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

@router.get("/stock-count")
async def list_stock_counts(db: AsyncSession = Depends(get_db)):
    """قائمة جلسات الجرد"""
    from sqlalchemy import func as _func
    result = await db.execute(
        select(StockCount).order_by(StockCount.created_at.desc())
    )
    counts = result.scalars().all()
    out = []
    for c in counts:
        lines_q = await db.execute(
            select(_func.count()).select_from(StockCountLine).where(StockCountLine.count_id == c.id)
        )
        scanned_q = await db.execute(
            select(_func.count()).select_from(StockCountLine).where(
                StockCountLine.count_id == c.id, StockCountLine.actual_qty.isnot(None)
            )
        )
        out.append({
            "id": c.id,
            "date": str(c.count_date),
            "status": c.status,
            "notes": c.notes,
            "total_items": lines_q.scalar() or 0,
            "scanned_items": scanned_q.scalar() or 0,
        })
    return out


@router.post("/stock-count", status_code=201, dependencies=[Depends(require_admin_production)])
async def create_stock_count(db: AsyncSession = Depends(get_db)):
    """إنشاء جلسة جرد جديدة"""
    from app.core.database import current_tenant_id as _ctid_stk
    _tid_stk = _ctid_stk.get() or 1
    # جلب كل المواد وإضافتها للجرد
    mats = (await db.execute(select(Material))).scalars().all()
    count = StockCount(tenant_id=_tid_stk, count_date=date.today(), status="in_progress")
    db.add(count)
    await db.flush()

    for m in mats:
        db.add(StockCountLine(
            tenant_id=_tid_stk,
            count_id=count.id,
            material_id=m.id,
            system_qty=m.stock_qty,
            actual_qty=None,
            difference=None,
        ))
    await db.commit()
    return {"id": count.id, "date": str(count.count_date), "items": len(mats)}


@router.post("/stock-count/{count_id}/scan", dependencies=[Depends(require_admin_production)])
async def scan_item(
    count_id: int,
    data: ScanIn,
    db: AsyncSession = Depends(get_db)
):
    """تسجيل كمية فعلية عند الجرد"""
    material_id = data.material_id
    actual_qty = data.actual_qty
    if actual_qty is None or actual_qty < 0:
        raise HTTPException(400, "الكمية الفعلية يجب أن تكون صفر أو أكبر")
    line = (await db.execute(
        select(StockCountLine).where(
            StockCountLine.count_id == count_id,
            StockCountLine.material_id == material_id,
        )
    )).scalar_one_or_none()

    if not line:
        raise HTTPException(404, "المادة غير موجودة في جلسة الجرد")

    from datetime import datetime
    line.actual_qty = Decimal(str(actual_qty))
    line.difference = line.actual_qty - (line.system_qty or 0)
    line.scanned_at = datetime.utcnow()
    await db.commit()

    return {
        "material_id": material_id,
        "system_qty":  float(line.system_qty or 0),
        "actual_qty":  actual_qty,
        "difference":  float(line.difference),
    }


@router.post("/stock-count/{count_id}/close", dependencies=[Depends(require_admin_production)])
async def close_stock_count(count_id: int, db: AsyncSession = Depends(get_db)):
    """إغلاق الجرد وتطبيق الفروقات على المخزن"""
    count = await db.get(StockCount, count_id)
    if not count: raise HTTPException(404)

    lines = (await db.execute(
        select(StockCountLine).where(
            StockCountLine.count_id == count_id,
            StockCountLine.actual_qty.is_not(None),
        )
    )).scalars().all()

    from app.core.database import current_tenant_id as _ctid_csc
    _tid_csc = _ctid_csc.get() or 1

    adjustments = 0
    for line in lines:
        if line.difference and line.difference != 0:
            mat = await db.get(Material, line.material_id)
            if mat:
                diff = line.actual_qty - (line.system_qty or 0)
                mat.stock_qty = line.actual_qty
                # تسجيل حركة تسوية لضمان تطابق سجل الحركات مع الرصيد
                db.add(StockMovement(
                    tenant_id=_tid_csc,
                    material_id=mat.id,
                    movement_type="in" if diff > 0 else "out",
                    qty=abs(diff),
                    unit_cost=mat.unit_cost,
                    total_cost=abs(diff) * mat.unit_cost,
                    reference=f"STOCKCOUNT-{count_id}",
                    notes=f"تسوية جرد #{count_id} — فرق: {float(diff):+.3f}",
                    movement_date=count.count_date,
                    withdrawal_unit="adjustment",
                ))
                adjustments += 1

    count.status = "completed"
    await db.commit()

    return {
        "count_id":    count_id,
        "status":      "completed",
        "adjustments": adjustments,
    }




# ── Auto-reserve from BOM endpoint ────────────────────────────────────────
@router.post("/auto-reserve/{work_order_id}", dependencies=[Depends(require_admin_production)])
async def auto_reserve(work_order_id: int, db: AsyncSession = Depends(get_db)):
    """Auto-reserve all BOM materials for a work order. Returns shortages."""
    result = await auto_reserve_materials(db, work_order_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


# ── Work Order Team (عمالة أمر الإنتاج) ─────────────────────────

class WOMemberIn(BaseModel):
    worker_id:     int
    role:          str = "فني"
    hourly_rate:   Decimal = Decimal("0")
    planned_hours: Decimal = Decimal("0")
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
