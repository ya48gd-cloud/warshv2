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
from app.models.models import (
    WorkOrder, Material, MaterialReservation,
    ScrapRecord, MrpSuggestion, StockCount, StockCountLine,
    BinLocation, MaterialBinStock,
)
from app.services.production import (
    auto_reserve_materials, issue_material,
    record_scrap, complete_work_order, run_mrp, get_smart_alerts,
)

router = APIRouter(prefix="/production", tags=["production"])


# ── Schemas ────────────────────────────────────────────────────
class IssueIn(BaseModel):
    work_order_id: int
    material_id:   int
    qty:           Decimal
    stage:         str       # مرحلة الإنتاج: لحام، تجميع، ...


class ScrapIn(BaseModel):
    work_order_id: int
    material_id:   int
    qty:           Decimal
    reason:        str
    stage:         str


class BinLocationIn(BaseModel):
    code:        str
    name_ar:     Optional[str] = None
    zone:        Optional[str] = None
    shelf:       Optional[str] = None
    bin:         Optional[str] = None
    capacity_kg: Optional[float] = None
    notes:       Optional[str] = None


# ── Work Order Integration ─────────────────────────────────────

@router.post("/reserve/{work_order_id}")
async def reserve_materials(
    work_order_id: int,
    db: AsyncSession = Depends(get_db)
):
    """حجز مواد أوتوماتيك من BOM عند بدء أمر الإنتاج"""
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


@router.post("/issue")
async def issue_material_endpoint(data: IssueIn, db: AsyncSession = Depends(get_db)):
    """صرف مواد لأمر إنتاج في مرحلة معينة"""
    result = await issue_material(
        db, data.work_order_id, data.material_id, data.qty, data.stage
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/scrap")
async def record_scrap_endpoint(data: ScrapIn, db: AsyncSession = Depends(get_db)):
    """تسجيل هالك في أمر إنتاج"""
    result = await record_scrap(
        db, data.work_order_id, data.material_id, data.qty, data.reason, data.stage
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/complete/{work_order_id}")
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
        "variance_pct":  round(
            (float(wo.actual_cost or 0) - float(wo.planned_cost or 1))
            / float(wo.planned_cost or 1) * 100, 1
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
    return await run_mrp(db)


@router.post("/mrp/{suggestion_id}/approve")
async def approve_mrp(suggestion_id: int, db: AsyncSession = Depends(get_db)):
    """الموافقة على اقتراح MRP (تحويله لطلب شراء)"""
    s = await db.get(MrpSuggestion, suggestion_id)
    if not s: raise HTTPException(404)
    s.status = "ordered"
    await db.commit()
    return {"id": s.id, "status": "ordered"}


@router.post("/mrp/{suggestion_id}/ignore")
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


@router.post("/bins", status_code=201)
async def create_bin(data: BinLocationIn, db: AsyncSession = Depends(get_db)):
    b = BinLocation(**data.model_dump())
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

@router.post("/stock-count", status_code=201)
async def create_stock_count(db: AsyncSession = Depends(get_db)):
    """إنشاء جلسة جرد جديدة"""
    # جلب كل المواد وإضافتها للجرد
    mats = (await db.execute(select(Material))).scalars().all()
    count = StockCount(count_date=date.today(), status="in_progress")
    db.add(count)
    await db.flush()

    for m in mats:
        db.add(StockCountLine(
            count_id=count.id,
            material_id=m.id,
            system_qty=m.stock_qty,
            actual_qty=None,
            difference=None,
        ))
    await db.commit()
    return {"id": count.id, "date": str(count.count_date), "items": len(mats)}


@router.post("/stock-count/{count_id}/scan")
async def scan_item(
    count_id: int,
    material_id: int,
    actual_qty: float,
    db: AsyncSession = Depends(get_db)
):
    """تسجيل كمية فعلية عند الجرد"""
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


@router.post("/stock-count/{count_id}/close")
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

    adjustments = 0
    for line in lines:
        if line.difference and line.difference != 0:
            mat = await db.get(Material, line.material_id)
            if mat:
                mat.stock_qty = line.actual_qty  # تطبيق الكمية الفعلية
                adjustments += 1

    count.status = "completed"
    await db.commit()

    return {
        "count_id":    count_id,
        "status":      "completed",
        "adjustments": adjustments,
    }
