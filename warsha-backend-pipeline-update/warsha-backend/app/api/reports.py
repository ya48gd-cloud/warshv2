"""
Reports API — التقارير الشاملة
مخزون · إنتاج · مبيعات · مشتريات · عمالة
"""
from datetime import date, timedelta, datetime, time
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func, and_, extract, case
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, current_tenant_id
from app.api.auth import require_any
from app.services.invoice_due import get_invoice_due_status
from app.models.models import (
    Material, StockMovement, MaterialCategory,
    WorkOrder, CostLine, ScrapRecord, Equipment, WorkOrderMember,
    Invoice, InvoiceLine, Quotation, InvoicePaymentSchedule,
    PurchaseInvoice, PurchaseInvoiceLine, Supplier,
    Worker, PayrollRun, PayrollLine, Attendance,
    Customer, CustomerOrder, CustomerPayment, SupplierPayment, User,
    Treasury, TreasuryCategory, TreasuryTransaction,
    StockQuant, StockBatch, SheetStockItem, BarStockItem,
)

router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(require_any)])


def _tid() -> int:
    """يجلب tenant_id الحالي من ContextVar أو يرفع 401.

    CRITICAL: كل استعلام في هذا الملف لازم يتقيّد بالـ tenant الحالي —
    بدون هذا الفلتر أي تقرير بيرجع بيانات كل الورش مجمّعة مع بعض (تسريب
    بيانات بين المستأجرين). راجع تقرير الأمان: هذا كان مفقوداً من كل
    تقرير هنا تقريباً قبل هذا الإصلاح.
    """
    tid = current_tenant_id.get()
    if tid is None:
        raise HTTPException(401, "غير مسموح")
    return tid


# حالات الفاتورة المفتوحة (عليها رصيد مستحق). installment_scheduler بيحوّل
# الفاتورة غير المدفوعة المتأخرة لحالة "overdue" فعليًا في الداتابيز — لو
# اتفلترت بـ unpaid/partial بس، المديونيات المتأخرة (أهم جزء في الـ aging!)
# كانت بتختفي من تقارير المديونيات وإجمالي المستحقات.
OPEN_INVOICE_STATUSES = ("unpaid", "partial", "overdue")

# حركات خزينة (شيكات) اتعكس أثرها على الرصيد — لازم متتحسبش في التدفق النقدي
# وإلا الرصيد المحسوب في التقرير يختلف عن رصيد الخزنة الفعلي.
VOID_CHEQUE_STATUSES = ("bounced", "cancelled")


def _active_txn_cond():
    """شرط استبعاد حركات الخزينة المرتدّة/الملغاة (cheque_status NULL = حركة عادية)."""
    return (TreasuryTransaction.cheque_status.is_(None)) | (
        TreasuryTransaction.cheque_status.notin_(VOID_CHEQUE_STATUSES)
    )


def _check_range(date_from: Optional[date], date_to: Optional[date]) -> None:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "تاريخ البداية يجب أن يكون قبل تاريخ النهاية")


def _dt_end(d: date) -> datetime:
    """نهاية اليوم — لمقارنة date_to مع أعمدة DateTime بدون استبعاد اليوم الأخير."""
    return datetime.combine(d, time.max)


# ══════════════════════════════════════════════════════════════
# INVENTORY REPORTS — تقارير المخزون
# ══════════════════════════════════════════════════════════════

@router.get("/inventory/reconciliation")
async def inventory_reconciliation_report(
    only_mismatches: bool = True, db: AsyncSession = Depends(get_db),
):
    """
    محطة 13 (الروادماب الأصلي): تقرير تسوية شامل يقارن الرصيد المعتمَد لكل
    مادة (Material.stock_qty) بمصادر البيانات الأخرى اللي المفروض تتفق معاه:

      - StockQuant  : مجموع الرصيد عبر كل المواقع (WIP/FG/RAW/IMPORTED/...)
      - StockBatch  : مجموع الدفعات النشطة (qty_remaining) للمواد اللي بتتبع
                       بالـ lot (tracking != 'none')
      - Sheet/Bar    : الوزن المحسوب من القطع الفعلية (ألواح/أسياخ) للمواد
                       اللي معرَّفة كـ plate/bar، محوَّل من عدد×أبعاد لوزن

    أي فرق (diff) يعني إن في مسار بيعدّل الرصيد المعتمَد من غير ما يحدّث
    المصدر التاني بالتوازي — ده بالظبط نوع الفجوة اللي المحطات 14-25
    (بترقيم المشروع الحالي) كانت بتصلحها واحدة واحدة؛ التقرير ده بيوريك
    فين لسه فيه فجوات من النوع ده من غير ما تدوّر عليها يدوي.
    """
    tid = _tid()
    TOLERANCE = Decimal("0.01")

    materials = (await db.execute(select(Material).where(Material.tenant_id == tid))).scalars().all()

    quant_totals = dict((await db.execute(
        select(StockQuant.material_id, func.coalesce(func.sum(StockQuant.qty), 0))
        .where(StockQuant.tenant_id == tid)
        .group_by(StockQuant.material_id)
    )).all())

    batch_totals = dict((await db.execute(
        select(StockBatch.material_id, func.coalesce(func.sum(StockBatch.qty_remaining), 0))
        .where(StockBatch.status == "active", StockBatch.tenant_id == tid)
        .group_by(StockBatch.material_id)
    )).all())

    sheet_rows = (await db.execute(
        select(SheetStockItem.material_id, SheetStockItem.length_cm,
               SheetStockItem.width_cm, SheetStockItem.qty)
        .where(SheetStockItem.tenant_id == tid)
    )).all()
    bar_rows = (await db.execute(
        select(BarStockItem.material_id, BarStockItem.length_cm, BarStockItem.qty)
        .where(BarStockItem.tenant_id == tid)
    )).all()

    results = []
    checked = 0
    mismatched = 0

    for mat in materials:
        row = {
            "material_id": mat.id, "code": mat.code, "name_ar": mat.name_ar,
            "unit": mat.unit, "stock_qty": float(mat.stock_qty or 0),
            "checks": [],
        }
        any_mismatch = False

        # ── StockQuant (لازم يتفق دايمًا — الليدجر المركزي بيحدّث الاتنين مع بعض) ──
        quant_total = quant_totals.get(mat.id, Decimal("0")) or Decimal("0")
        quant_diff = (mat.stock_qty or Decimal("0")) - Decimal(str(quant_total))
        quant_mismatch = abs(quant_diff) > TOLERANCE
        row["checks"].append({
            "source": "StockQuant (مواقع فعلية)",
            "source_total": float(quant_total),
            "diff": float(quant_diff),
            "mismatch": quant_mismatch,
            "likely_cause": (
                "مسار بيعدّل stock_qty مباشرة من غير post_receipt/post_issue "
                "(يفوّت تسجيل الموقع)" if quant_mismatch else None
            ),
        })
        any_mismatch = any_mismatch or quant_mismatch

        # ── StockBatch (للمواد اللي بتتتبع بالـ lot بس) ──
        if getattr(mat, "tracking", "none") and mat.tracking != "none":
            batch_total = batch_totals.get(mat.id, Decimal("0")) or Decimal("0")
            batch_diff = (mat.stock_qty or Decimal("0")) - Decimal(str(batch_total))
            batch_mismatch = abs(batch_diff) > TOLERANCE
            row["checks"].append({
                "source": "StockBatch (دفعات نشطة)",
                "source_total": float(batch_total),
                "diff": float(batch_diff),
                "mismatch": batch_mismatch,
                "likely_cause": (
                    "استلام/صرف مادة متتبَّعة بالدفعة من غير تحديث qty_remaining"
                    if batch_mismatch else None
                ),
            })
            any_mismatch = any_mismatch or batch_mismatch

        # ── Sheet/Bar physical pieces (للمواد اللي هي ألواح/أسياخ فعليًا) ──
        if getattr(mat, "is_plate", False) and mat.density_kg_m2 and mat.density_kg_m2 > 0:
            physical_kg = Decimal("0")
            for m_id, length_cm, width_cm, qty in sheet_rows:
                if m_id != mat.id:
                    continue
                area_m2 = (Decimal(str(length_cm)) / 100) * (Decimal(str(width_cm)) / 100)
                physical_kg += area_m2 * mat.density_kg_m2 * Decimal(str(qty))
            physical_diff = (mat.stock_qty or Decimal("0")) - physical_kg
            physical_mismatch = abs(physical_diff) > TOLERANCE
            row["checks"].append({
                "source": "SheetStockItem (ألواح فعلية محسوبة)",
                "source_total": float(physical_kg),
                "diff": float(physical_diff),
                "mismatch": physical_mismatch,
                "likely_cause": (
                    "قطع ألواح فعلية (مقاسات) مش متسجلة أو متسجلة زيادة عن "
                    "رصيد الوزن المعتمَد" if physical_mismatch else None
                ),
            })
            any_mismatch = any_mismatch or physical_mismatch

        elif getattr(mat, "material_kind", None) == "bar" and mat.weight_per_meter_kg and mat.weight_per_meter_kg > 0:
            physical_kg = Decimal("0")
            for m_id, length_cm, qty in bar_rows:
                if m_id != mat.id:
                    continue
                physical_kg += (Decimal(str(length_cm)) / 100) * mat.weight_per_meter_kg * Decimal(str(qty))
            physical_diff = (mat.stock_qty or Decimal("0")) - physical_kg
            physical_mismatch = abs(physical_diff) > TOLERANCE
            row["checks"].append({
                "source": "BarStockItem (أسياخ فعلية محسوبة)",
                "source_total": float(physical_kg),
                "diff": float(physical_diff),
                "mismatch": physical_mismatch,
                "likely_cause": (
                    "قطع أسياخ فعلية (أطوال) مش متسجلة أو متسجلة زيادة عن "
                    "رصيد الوزن المعتمَد" if physical_mismatch else None
                ),
            })
            any_mismatch = any_mismatch or physical_mismatch

        checked += 1
        row["has_mismatch"] = any_mismatch
        if any_mismatch:
            mismatched += 1
        if any_mismatch or not only_mismatches:
            results.append(row)

    return {
        "summary": {
            "materials_checked": checked,
            "materials_with_mismatch": mismatched,
            "clean_pct": round((checked - mismatched) / checked * 100, 1) if checked else 100,
        },
        "materials": results,
    }


@router.get("/inventory/value")
async def inventory_value_report(db: AsyncSession = Depends(get_db)):
    """قيمة المخزون الإجمالية مقسمة بالفئة"""
    tid = _tid()
    res = await db.execute(
        select(
            MaterialCategory.name_ar,
            func.count(Material.id).label("item_count"),
            func.coalesce(func.sum(Material.stock_qty * Material.unit_cost), 0).label("total_value"),
        )
        .join(Material, Material.category_id == MaterialCategory.id)
        .where(MaterialCategory.tenant_id == tid, Material.tenant_id == tid)
        .group_by(MaterialCategory.id, MaterialCategory.name_ar)
        .order_by(func.sum(Material.stock_qty * Material.unit_cost).desc())
    )
    rows = res.all()

    total_value = sum(float(r.total_value) for r in rows)
    return {
        "total_value": total_value,
        "by_category": [{
            "category": r.name_ar,
            "item_count": r.item_count,
            "total_value": float(r.total_value),
            "pct": round(float(r.total_value) / total_value * 100, 1) if total_value else 0,
        } for r in rows],
    }


@router.get("/inventory/movement")
async def inventory_movement_report(
    material_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    movement_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """تقرير حركة المخزون في فترة زمنية"""
    tid = _tid()
    if not date_from:
        date_from = date.today().replace(day=1)
    if not date_to:
        date_to = date.today()
    _check_range(date_from, date_to)

    q = select(StockMovement).options(selectinload(StockMovement.material)).where(
        StockMovement.tenant_id == tid,
        StockMovement.movement_date >= date_from,
        StockMovement.movement_date <= date_to,
    )
    if material_id:
        q = q.where(StockMovement.material_id == material_id)
    if movement_type:
        q = q.where(StockMovement.movement_type == movement_type)

    res = await db.execute(q.order_by(StockMovement.movement_date.desc()))
    movements = res.scalars().all()

    total_in  = sum(float(m.total_cost) for m in movements if m.movement_type == "in")
    total_out = sum(float(m.total_cost) for m in movements if m.movement_type == "out")

    return {
        "date_from": str(date_from), "date_to": str(date_to),
        "total_in": total_in, "total_out": total_out,
        "net": total_in - total_out,
        "movements": [{
            "id": m.id,
            "date": str(m.movement_date),
            "type": m.movement_type,
            "type_ar": "وارد" if m.movement_type == "in" else "صادر",
            "material_code": m.material.code if m.material else "",
            "material_name": m.material.name_ar if m.material else "",
            "qty": float(m.qty),
            "unit_cost": float(m.unit_cost),
            "total_cost": float(m.total_cost),
            "reference": m.reference,
            "destination": m.destination,
        } for m in movements],
    }


@router.get("/inventory/slow-moving")
async def slow_moving_report(
    days: int = Query(90, ge=30, le=365),
    db: AsyncSession = Depends(get_db),
):
    """المواد الراكدة — لم يكن فيها أي حركة خلال N يوم"""
    tid = _tid()
    cutoff = date.today() - timedelta(days=days)

    active_ids_res = await db.execute(
        select(StockMovement.material_id).where(
            StockMovement.tenant_id == tid,
            StockMovement.movement_date >= cutoff,
        ).distinct()
    )
    active_ids = {r[0] for r in active_ids_res}

    all_res = await db.execute(
        select(Material)
        .options(selectinload(Material.category))
        .where(Material.tenant_id == tid, Material.stock_qty > 0)
        .order_by((Material.stock_qty * Material.unit_cost).desc())
    )
    all_mats = all_res.scalars().all()

    slow = [m for m in all_mats if m.id not in active_ids]
    total_value = sum(float(m.stock_qty) * float(m.unit_cost) for m in slow)

    return {
        "period_days": days,
        "slow_moving_count": len(slow),
        "slow_moving_value": total_value,
        "items": [{
            "id": m.id, "code": m.code, "name_ar": m.name_ar,
            "category": m.category.name_ar if m.category else "",
            "stock_qty": float(m.stock_qty),
            "unit": m.unit,
            "unit_cost": float(m.unit_cost),
            "stock_value": float(m.stock_qty) * float(m.unit_cost),
            "reorder_level": float(m.reorder_level),
        } for m in slow],
    }


@router.get("/inventory/summary")
async def inventory_summary(db: AsyncSession = Depends(get_db)):
    """ملخص شامل للمخزون"""
    tid = _tid()
    total_items   = await db.scalar(select(func.count()).select_from(Material).where(Material.tenant_id == tid))
    low_stock     = await db.scalar(
        select(func.count()).select_from(Material)
        .where(Material.tenant_id == tid, Material.stock_qty <= Material.reorder_level, Material.reorder_level > 0)
    )
    out_of_stock  = await db.scalar(
        select(func.count()).select_from(Material).where(Material.tenant_id == tid, Material.stock_qty <= 0)
    )
    total_value   = await db.scalar(
        select(func.coalesce(func.sum(Material.stock_qty * Material.unit_cost), 0))
        .where(Material.tenant_id == tid)
    )

    # حركة الشهر الحالي
    today = date.today()
    mtd_in = await db.scalar(
        select(func.coalesce(func.sum(StockMovement.total_cost), 0)).where(
            StockMovement.tenant_id == tid,
            StockMovement.movement_type == "in",
            extract("month", StockMovement.movement_date) == today.month,
            extract("year", StockMovement.movement_date) == today.year,
        )
    )
    mtd_out = await db.scalar(
        select(func.coalesce(func.sum(StockMovement.total_cost), 0)).where(
            StockMovement.tenant_id == tid,
            StockMovement.movement_type == "out",
            extract("month", StockMovement.movement_date) == today.month,
            extract("year", StockMovement.movement_date) == today.year,
        )
    )

    # جميع المواد مرتبة بالقيمة
    top_res = await db.execute(
        select(Material)
        .options(selectinload(Material.category))
        .where(Material.tenant_id == tid, Material.stock_qty > 0)
        .order_by((Material.stock_qty * Material.unit_cost).desc())
    )
    top_materials = top_res.scalars().all()

    return {
        "total_items": total_items or 0,
        "low_stock": low_stock or 0,
        "out_of_stock": out_of_stock or 0,
        "total_value": float(total_value or 0),
        "mtd_in_value": float(mtd_in or 0),
        "mtd_out_value": float(mtd_out or 0),
        "top_value_materials": [{
            "id": m.id, "code": m.code, "name_ar": m.name_ar,
            "category": m.category.name_ar if m.category else "",
            "stock_qty": float(m.stock_qty),
            "unit": m.unit, "unit_cost": float(m.unit_cost),
            "stock_value": float(m.stock_qty) * float(m.unit_cost),
        } for m in top_materials],
    }


@router.get("/inventory/transactions")
async def inventory_transactions(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """سجل كل حركات المخزون مع التفاصيل"""
    tid = _tid()
    if not date_from:
        date_from = date.today().replace(day=1)
    if not date_to:
        date_to = date.today()
    _check_range(date_from, date_to)

    res = await db.execute(
        select(StockMovement)
        .options(selectinload(StockMovement.material))
        .where(
            StockMovement.tenant_id == tid,
            StockMovement.movement_date >= date_from,
            StockMovement.movement_date <= date_to,
        )
        .order_by(StockMovement.movement_date.desc())
    )
    return [{
        "id": m.id,
        "date": str(m.movement_date),
        "type": m.movement_type,
        "type_ar": "وارد" if m.movement_type == "in" else "صادر",
        "material_id": m.material_id,
        "material_code": m.material.code if m.material else "",
        "material_name": m.material.name_ar if m.material else "",
        "qty": float(m.qty), "unit_cost": float(m.unit_cost),
        "total_cost": float(m.total_cost),
        "reference": m.reference, "notes": m.notes,
        "destination": m.destination, "destination_ref": m.destination_ref,
    } for m in res.scalars().all()]


# ══════════════════════════════════════════════════════════════
# PRODUCTION REPORTS — تقارير الإنتاج
# ══════════════════════════════════════════════════════════════

@router.get("/production/summary")
async def production_summary(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """ملخص الإنتاج في فترة"""
    tid = _tid()
    if not date_from:
        date_from = date.today().replace(day=1)
    if not date_to:
        date_to = date.today()
    _check_range(date_from, date_to)

    # تحويل date → datetime لضمان المقارنة الصحيحة مع عمود DateTime
    dt_from = datetime.combine(date_from, time.min)
    dt_to   = datetime.combine(date_to,   time.max)
    q = select(WorkOrder).where(
        WorkOrder.tenant_id == tid,
        WorkOrder.created_at >= dt_from,
        WorkOrder.created_at <= dt_to,
    )
    res = await db.execute(q)
    orders = res.scalars().all()

    by_status = {}
    for o in orders:
        by_status[o.status] = by_status.get(o.status, 0) + 1

    total_planned = sum(float(o.planned_cost) for o in orders)
    total_actual  = sum(float(o.actual_cost) for o in orders)
    done_orders   = [o for o in orders if o.status == "done"]

    variance_pct = 0
    if total_planned > 0:
        variance_pct = round((total_actual - total_planned) / total_planned * 100, 1)

    return {
        "date_from": str(date_from), "date_to": str(date_to),
        "total_orders": len(orders),
        "by_status": by_status,
        "total_planned_cost": total_planned,
        "total_actual_cost": total_actual,
        "variance_pct": variance_pct,
        "completed_count": len(done_orders),
        "orders": [{
            "id": o.id, "code": o.code, "status": o.status,
            "planned_cost": float(o.planned_cost),
            "actual_cost": float(o.actual_cost),
            "variance": float(o.actual_cost - o.planned_cost),
            "start_date": str(o.start_date) if o.start_date else None,
            "end_date": str(o.end_date) if o.end_date else None,
        } for o in orders],
    }


@router.get("/production/scrap-analysis")
async def scrap_analysis(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """تحليل الهالك بالسبب والمادة"""
    tid = _tid()
    q = select(ScrapRecord).options(
        selectinload(ScrapRecord.material),
        selectinload(ScrapRecord.work_order),
    ).where(ScrapRecord.tenant_id == tid)
    _check_range(date_from, date_to)
    if date_from:
        q = q.where(ScrapRecord.recorded_at >= datetime.combine(date_from, time.min))
    if date_to:
        # recorded_at عمود DateTime — المقارنة بـ date مباشرة كانت بتستبعد كل هالك اليوم الأخير
        q = q.where(ScrapRecord.recorded_at <= _dt_end(date_to))

    res = await db.execute(q.order_by(ScrapRecord.total_cost.desc()))
    scraps = res.scalars().all()

    by_reason: dict = {}
    by_material: dict = {}
    for s in scraps:
        reason = s.reason or "غير محدد"
        by_reason[reason] = by_reason.get(reason, 0) + float(s.total_cost or 0)
        mat_name = s.material.name_ar if s.material else "غير محدد"
        by_material[mat_name] = by_material.get(mat_name, 0) + float(s.total_cost or 0)

    return {
        "total_scrap_cost": sum(float(s.total_cost or 0) for s in scraps),
        "total_records": len(scraps),
        "by_reason": [{"reason": k, "cost": v} for k, v in sorted(by_reason.items(), key=lambda x: -x[1])],
        "by_material": [{"material": k, "cost": v} for k, v in sorted(by_material.items(), key=lambda x: -x[1])[:10]],
        "records": [{
            "id": s.id,
            "wo_code": s.work_order.code if s.work_order else "—",
            "material": s.material.name_ar if s.material else "—",
            "qty": float(s.qty), "unit_cost": float(s.unit_cost or 0),
            "total_cost": float(s.total_cost or 0),
            "reason": s.reason, "stage": s.stage,
            "date": s.recorded_at.date().isoformat() if s.recorded_at else None,
        } for s in scraps],
    }


@router.get("/profitability")
async def profitability_report(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    group_by: str = Query("work_order", pattern="^(work_order|customer|equipment)$"),
    db: AsyncSession = Depends(get_db),
):
    """تقرير الربحية التفصيلي — حسب أمر الإنتاج / العميل / المعدة"""
    tid = _tid()
    if not date_from:
        date_from = date.today().replace(day=1)
    if not date_to:
        date_to = date.today()
    _check_range(date_from, date_to)

    dt_from = datetime.combine(date_from, time.min)
    dt_to   = datetime.combine(date_to, time.max)

    q = select(WorkOrder).options(
        selectinload(WorkOrder.equipment),
        selectinload(WorkOrder.customer_order).selectinload(CustomerOrder.customer),
    ).where(
        WorkOrder.tenant_id == tid,
        WorkOrder.created_at >= dt_from,
        WorkOrder.created_at <= dt_to,
    )
    res = await db.execute(q)
    orders = res.scalars().all()

    wo_ids = [o.id for o in orders]
    revenue_by_wo: dict = {}
    if wo_ids:
        rev_res = await db.execute(
            select(Invoice.work_order_id, func.coalesce(func.sum(Invoice.total), 0))
            .where(Invoice.tenant_id == tid, Invoice.work_order_id.in_(wo_ids))
            .group_by(Invoice.work_order_id)
        )
        revenue_by_wo = {r[0]: float(r[1]) for r in rev_res.all()}

    rows = []
    for o in orders:
        cost = float(o.actual_cost) if o.actual_cost else float(o.planned_cost)
        revenue = revenue_by_wo.get(o.id, 0.0)
        profit = revenue - cost
        margin = round(profit / revenue * 100, 1) if revenue else 0
        customer_name = None
        if o.customer_order and o.customer_order.customer:
            customer_name = o.customer_order.customer.name
        rows.append({
            "work_order_id": o.id,
            "code": o.code,
            "status": o.status,
            "equipment": o.equipment.name_ar if o.equipment else "",
            "customer": customer_name or "—",
            "revenue": revenue,
            "cost": cost,
            "planned_cost": float(o.planned_cost),
            "actual_cost": float(o.actual_cost),
            "profit": profit,
            "margin_pct": margin,
            "start_date": str(o.start_date) if o.start_date else None,
            "end_date": str(o.end_date) if o.end_date else None,
        })

    total_revenue = sum(r["revenue"] for r in rows)
    total_cost = sum(r["cost"] for r in rows)
    total_profit = total_revenue - total_cost
    total_margin = round(total_profit / total_revenue * 100, 1) if total_revenue else 0

    grouped = []
    if group_by in ("customer", "equipment"):
        agg: dict = {}
        for r in rows:
            key = r[group_by] or "—"
            g = agg.setdefault(key, {"name": key, "revenue": 0.0, "cost": 0.0, "count": 0})
            g["revenue"] += r["revenue"]
            g["cost"]    += r["cost"]
            g["count"]   += 1
        for g in agg.values():
            g["profit"] = g["revenue"] - g["cost"]
            g["margin_pct"] = round(g["profit"] / g["revenue"] * 100, 1) if g["revenue"] else 0
        grouped = sorted(agg.values(), key=lambda x: -x["profit"])

    return {
        "date_from": str(date_from), "date_to": str(date_to),
        "group_by": group_by,
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "total_margin_pct": total_margin,
        "count": len(rows),
        "rows": sorted(rows, key=lambda x: -x["profit"]),
        "grouped": grouped,
    }


# ══════════════════════════════════════════════════════════════
# SALES REPORTS — تقارير المبيعات
# ══════════════════════════════════════════════════════════════

@router.get("/sales/summary")
async def sales_summary(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """ملخص المبيعات في فترة"""
    tid = _tid()
    if not date_from:
        date_from = date.today().replace(day=1)
    if not date_to:
        date_to = date.today()
    _check_range(date_from, date_to)

    inv_res = await db.execute(
        select(Invoice).options(selectinload(Invoice.customer))
        .where(Invoice.tenant_id == tid, Invoice.date >= date_from, Invoice.date <= date_to)
        .order_by(Invoice.date.desc())
    )
    invoices = inv_res.scalars().all()

    total    = sum(float(inv.total) for inv in invoices)
    paid     = sum(float(inv.paid_amount) for inv in invoices)
    unpaid   = total - paid
    by_status = {}
    for inv in invoices:
        by_status[inv.status] = by_status.get(inv.status, 0) + 1

    return {
        "date_from": str(date_from), "date_to": str(date_to),
        "total_invoices": len(invoices),
        "total_value": total,
        "total_paid": paid,
        "total_unpaid": unpaid,
        "collection_rate": round(paid / total * 100, 1) if total else 0,
        "by_status": by_status,
        "invoices": [{
            "id": inv.id, "code": inv.code,
            "customer_name": inv.customer.name if inv.customer else "",
            "date": str(inv.date), "due_date": str(inv.due_date) if inv.due_date else None,
            "total": float(inv.total), "paid_amount": float(inv.paid_amount),
            "remaining": float(inv.total - inv.paid_amount),
            "status": inv.status,
        } for inv in invoices],
    }


@router.get("/sales/aging")
async def receivables_aging(db: AsyncSession = Depends(get_db)):
    """تقرير Aging للمديونيات — مقسمة بعمر الدين (يراعي خطط الأقساط)"""
    tid = _tid()
    today = date.today()
    unpaid_res = await db.execute(
        select(Invoice).options(selectinload(Invoice.customer)).where(
            Invoice.tenant_id == tid,
            Invoice.status.in_(OPEN_INVOICE_STATUSES),
            Invoice.total > Invoice.paid_amount,
        ).order_by(Invoice.due_date)
    )
    invoices = unpaid_res.scalars().all()

    buckets = {"current": [], "1-30": [], "31-60": [], "61-90": [], "90+": []}
    for inv in invoices:
        # ── استخدام مصدر الحقيقة المركزي بدل invoice.due_date مباشرة ──
        due_status = await get_invoice_due_status(db, inv, today)
        remaining  = float(inv.total - inv.paid_amount)
        days_overdue = due_status.days_overdue if due_status.is_overdue else -(
            (due_status.effective_due_date - today).days
        )

        entry = {
            "id": inv.id, "code": inv.code,
            "customer_name": inv.customer.name if inv.customer else "",
            "date": str(inv.date),
            "due_date": str(due_status.effective_due_date),
            "remaining": remaining,
            "days_overdue": days_overdue,
            "has_schedule": due_status.has_schedule,
        }
        if not due_status.is_overdue:
            buckets["current"].append(entry)
        elif days_overdue <= 30:
            buckets["1-30"].append(entry)
        elif days_overdue <= 60:
            buckets["31-60"].append(entry)
        elif days_overdue <= 90:
            buckets["61-90"].append(entry)
        else:
            buckets["90+"].append(entry)

    totals = {k: sum(e["remaining"] for e in v) for k, v in buckets.items()}
    grand_total = sum(totals.values())

    return {
        "grand_total": grand_total,
        "totals_by_bucket": totals,
        "buckets": buckets,
    }


# محطة 6: متابعة تأخر الأقساط + تنبيه بالأقساط القادمة (راجع invoice.txt)
# ملحوظة: زي تقرير /sales/aging بالظبط، الحالة "overdue" بتتحسب ديناميكيًا وقت
# العرض ومبيتسجلش في الداتابيز — نفس فلسفة الكود الموجودة أصلاً في inv_dict/installment_dict،
# مفيش job/scheduler في المشروع أصلاً لتحديث حالات بالخلفية.
@router.get("/sales/installments/upcoming")
async def upcoming_installments(
    days: int = Query(7, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """الأقساط المستحقة خلال N يوم جاية (لسه متسددتش بالكامل) — تنبيه بالأقساط القادمة."""
    tid = _tid()
    today = date.today()
    horizon = today + timedelta(days=days)
    r = await db.execute(
        select(InvoicePaymentSchedule)
        .options(
            selectinload(InvoicePaymentSchedule.invoice).selectinload(Invoice.customer),
        )
        .where(
            InvoicePaymentSchedule.tenant_id == tid,
            InvoicePaymentSchedule.status.in_(["pending", "partial"]),
            InvoicePaymentSchedule.due_date >= today,
            InvoicePaymentSchedule.due_date <= horizon,
        )
        .order_by(InvoicePaymentSchedule.due_date)
    )
    rows = r.scalars().all()
    items = [_installment_alert_entry(ins, today) for ins in rows]
    return {
        "days": days,
        "count": len(items),
        "total_due": sum(x["remaining_amount"] for x in items),
        "items": items,
    }


@router.get("/sales/installments/overdue")
async def overdue_installments(db: AsyncSession = Depends(get_db)):
    """الأقساط اللي فات ميعاد استحقاقها ولسه متسددتش بالكامل — متابعة التأخر."""
    tid = _tid()
    today = date.today()
    r = await db.execute(
        select(InvoicePaymentSchedule)
        .options(
            selectinload(InvoicePaymentSchedule.invoice).selectinload(Invoice.customer),
        )
        .where(
            InvoicePaymentSchedule.tenant_id == tid,
            # installment_scheduler بيحوّل القسط المتأخر لـ status="overdue" فعليًا —
            # من غيرها الأقساط المتأخرة كانت بتختفي من تقرير المتأخرات بعد أول تشغيل له.
            InvoicePaymentSchedule.status.in_(["pending", "partial", "overdue"]),
            InvoicePaymentSchedule.due_date < today,
        )
        .order_by(InvoicePaymentSchedule.due_date)
    )
    rows = r.scalars().all()
    items = [_installment_alert_entry(ins, today) for ins in rows]
    return {
        "count": len(items),
        "total_overdue": sum(x["remaining_amount"] for x in items),
        "items": items,
    }


def _installment_alert_entry(ins: InvoicePaymentSchedule, today: date) -> dict:
    inv = ins.invoice
    remaining = float(ins.amount_due) - float(ins.paid_amount)
    days_overdue = (today - ins.due_date).days
    customer = inv.customer if inv else None
    return {
        "installment_id": ins.id,
        "invoice_id": ins.invoice_id,
        "invoice_code": inv.code if inv else None,
        "customer_id": customer.id if customer else None,
        "customer_name": customer.name if customer else None,
        "customer_phone": customer.phone if customer else None,
        "installment_no": ins.installment_no,
        "label": ins.label,
        "due_date": str(ins.due_date),
        "status": ins.status,
        "days_overdue": days_overdue,          # سالب = لسه ماجاش، موجب = متأخر
        "amount_due": float(ins.amount_due),
        "paid_amount": float(ins.paid_amount),
        "remaining_amount": remaining,
    }


@router.get("/sales/installments/all")
async def all_open_installments(
    filter: str = Query("all", regex="^(overdue|week|month|all)$"),
    db: AsyncSession = Depends(get_db),
):
    """
    جميع الأقساط المفتوحة في view موحد للصفحة المركزية.
    filter: overdue | week | month | all
    """
    tid = _tid()
    today = date.today()
    cond = [InvoicePaymentSchedule.tenant_id == tid,
            InvoicePaymentSchedule.status.in_(["pending", "partial", "overdue"])]

    if filter == "overdue":
        cond.append(InvoicePaymentSchedule.due_date < today)
    elif filter == "week":
        cond.append(InvoicePaymentSchedule.due_date <= today + timedelta(days=7))
    elif filter == "month":
        cond.append(InvoicePaymentSchedule.due_date <= today + timedelta(days=30))

    r = await db.execute(
        select(InvoicePaymentSchedule)
        .options(
            selectinload(InvoicePaymentSchedule.invoice).selectinload(Invoice.customer),
        )
        .where(*cond)
        .order_by(InvoicePaymentSchedule.due_date, InvoicePaymentSchedule.invoice_id)
    )
    rows = r.scalars().all()
    items = [_installment_alert_entry(ins, today) for ins in rows]
    overdue_items   = [x for x in items if x["days_overdue"] > 0]
    upcoming_items  = [x for x in items if x["days_overdue"] <= 0]
    return {
        "filter": filter,
        "overdue_count":   len(overdue_items),
        "overdue_total":   sum(x["remaining_amount"] for x in overdue_items),
        "upcoming_count":  len(upcoming_items),
        "upcoming_total":  sum(x["remaining_amount"] for x in upcoming_items),
        "total_count":     len(items),
        "items": items,
    }


@router.get("/sales/installments/badge")
async def installments_badge(db: AsyncSession = Depends(get_db)):
    """عدد الأقساط المتأخرة للـ Badge في الـ Sidebar — سريع جداً."""
    tid = _tid()
    today = date.today()
    r = await db.execute(
        select(func.count()).select_from(InvoicePaymentSchedule).where(
            InvoicePaymentSchedule.tenant_id == tid,
            InvoicePaymentSchedule.status.in_(["pending", "partial", "overdue"]),
            InvoicePaymentSchedule.due_date < today,
        )
    )
    count = r.scalar() or 0
    return {"overdue_count": count}


@router.get("/sales/by-customer")
async def sales_by_customer(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """مبيعات مقسمة بالعميل"""
    tid = _tid()
    if not date_from:
        date_from = date.today().replace(day=1)
    if not date_to:
        date_to = date.today()
    _check_range(date_from, date_to)

    res = await db.execute(
        select(
            Customer.id, Customer.name,
            func.count(Invoice.id).label("invoice_count"),
            func.coalesce(func.sum(Invoice.total), 0).label("total"),
            func.coalesce(func.sum(Invoice.paid_amount), 0).label("paid"),
        )
        .outerjoin(Invoice, and_(Invoice.customer_id == Customer.id,
                                 Invoice.tenant_id == tid,
                                 Invoice.date >= date_from, Invoice.date <= date_to))
        .where(Customer.tenant_id == tid)
        .group_by(Customer.id, Customer.name)
        .having(func.count(Invoice.id) > 0)
        .order_by(func.sum(Invoice.total).desc())
    )
    rows = res.all()
    return [{
        "customer_id": r.id, "customer_name": r.name,
        "invoice_count": r.invoice_count,
        "total": float(r.total),
        "paid": float(r.paid),
        "unpaid": float(r.total - r.paid),
    } for r in rows]


# ══════════════════════════════════════════════════════════════
# PURCHASES REPORTS — تقارير المشتريات
# ══════════════════════════════════════════════════════════════

@router.get("/purchases/summary")
async def purchases_summary(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """ملخص المشتريات في فترة"""
    tid = _tid()
    if not date_from:
        date_from = date.today().replace(day=1)
    if not date_to:
        date_to = date.today()
    _check_range(date_from, date_to)

    res = await db.execute(
        select(PurchaseInvoice).options(selectinload(PurchaseInvoice.supplier))
        .where(PurchaseInvoice.tenant_id == tid, PurchaseInvoice.date >= date_from, PurchaseInvoice.date <= date_to)
        .order_by(PurchaseInvoice.date.desc())
    )
    invoices = res.scalars().all()
    total = sum(float(inv.total) for inv in invoices)
    paid  = sum(float(inv.paid_amount) for inv in invoices)

    return {
        "date_from": str(date_from), "date_to": str(date_to),
        "total_invoices": len(invoices),
        "total_value": total,
        "total_paid": paid,
        "total_unpaid": total - paid,
        "invoices": [{
            "id": inv.id, "code": inv.code,
            "supplier_name": inv.supplier_name or (inv.supplier.name if inv.supplier else ""),
            "date": str(inv.date),
            "total": float(inv.total), "paid_amount": float(inv.paid_amount),
            "remaining": float(inv.total - inv.paid_amount),
            "status": inv.status,
        } for inv in invoices],
    }


@router.get("/purchases/by-supplier")
async def purchases_by_supplier(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """مشتريات مقسمة بالمورد"""
    tid = _tid()
    if not date_from:
        date_from = date.today().replace(day=1)
    if not date_to:
        date_to = date.today()
    _check_range(date_from, date_to)

    res = await db.execute(
        select(
            func.coalesce(PurchaseInvoice.supplier_name, Supplier.name, "غير محدد").label("supplier_name"),
            func.count(PurchaseInvoice.id).label("invoice_count"),
            func.coalesce(func.sum(PurchaseInvoice.total), 0).label("total"),
            func.coalesce(func.sum(PurchaseInvoice.paid_amount), 0).label("paid"),
        )
        .outerjoin(Supplier, Supplier.id == PurchaseInvoice.supplier_id)
        .where(PurchaseInvoice.tenant_id == tid, PurchaseInvoice.date >= date_from, PurchaseInvoice.date <= date_to)
        .group_by(func.coalesce(PurchaseInvoice.supplier_name, Supplier.name, "غير محدد"))
        .order_by(func.sum(PurchaseInvoice.total).desc())
    )
    rows = res.all()
    return [{
        "supplier_name": r.supplier_name,
        "invoice_count": r.invoice_count,
        "total": float(r.total),
        "paid": float(r.paid),
        "unpaid": float(r.total - r.paid),
    } for r in rows]


# ══════════════════════════════════════════════════════════════
# WORKERS REPORTS — تقارير العمالة
# ══════════════════════════════════════════════════════════════

@router.get("/workers/summary")
async def workers_summary(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """ملخص العمالة والرواتب في فترة"""
    tid = _tid()
    if not date_from:
        date_from = date.today().replace(day=1)
    if not date_to:
        date_to = date.today()
    _check_range(date_from, date_to)

    payroll_res = await db.execute(
        select(PayrollRun).where(
            PayrollRun.tenant_id == tid,
            PayrollRun.week_start >= date_from,
            PayrollRun.week_end <= date_to,
        )
    )
    payrolls = payroll_res.scalars().all()
    total_gross = sum(float(p.total_gross) for p in payrolls)
    total_net   = sum(float(p.total_net) for p in payrolls)

    total_workers = await db.scalar(
        select(func.count()).select_from(Worker).where(Worker.tenant_id == tid, Worker.is_active == True)
    )

    att_res = await db.execute(
        select(
            Attendance.status,
            func.count().label("cnt"),
        ).where(
            Attendance.tenant_id == tid,
            Attendance.date >= date_from,
            Attendance.date <= date_to,
        ).group_by(Attendance.status)
    )
    attendance_summary = {r.status: r.cnt for r in att_res}

    return {
        "date_from": str(date_from), "date_to": str(date_to),
        "total_active_workers": total_workers or 0,
        "payroll_runs": len(payrolls),
        "total_gross": total_gross,
        "total_net": total_net,
        "attendance": attendance_summary,
    }


@router.get("/labor-performance")
async def labor_performance_report(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """تقرير أداء العمالة: الحضور، الساعات المخططة والفعلية، تكلفة العمالة والرواتب"""
    tid = _tid()
    if not date_from:
        date_from = date.today().replace(day=1)
    if not date_to:
        date_to = date.today()
    _check_range(date_from, date_to)

    workers = (await db.execute(
        select(Worker).where(Worker.tenant_id == tid, Worker.is_active == True)
    )).scalars().all()

    # ── الحضور ──
    att_res = await db.execute(
        select(
            Attendance.worker_id,
            Attendance.status,
            func.count().label("cnt"),
        ).where(
            Attendance.tenant_id == tid,
            Attendance.date >= date_from, Attendance.date <= date_to,
        ).group_by(Attendance.worker_id, Attendance.status)
    )
    attendance: dict = {}
    for r in att_res:
        attendance.setdefault(r.worker_id, {})[r.status] = r.cnt

    # ── الساعات والتكلفة من أوامر الإنتاج ──
    wom_res = await db.execute(
        select(
            WorkOrderMember.worker_id,
            func.count(func.distinct(WorkOrderMember.work_order_id)).label("wo_count"),
            func.coalesce(func.sum(WorkOrderMember.planned_hours), 0).label("planned_hours"),
            func.coalesce(func.sum(WorkOrderMember.actual_hours), 0).label("actual_hours"),
            func.coalesce(func.sum(WorkOrderMember.actual_hours * WorkOrderMember.hourly_rate), 0).label("labor_cost"),
        ).join(WorkOrder, WorkOrder.id == WorkOrderMember.work_order_id)
         .where(WorkOrder.tenant_id == tid,
                WorkOrder.created_at >= datetime.combine(date_from, time.min),
                WorkOrder.created_at <= _dt_end(date_to))
         .group_by(WorkOrderMember.worker_id)
    )
    wom = {r.worker_id: r for r in wom_res}

    # ── الرواتب ──
    payroll_res = await db.execute(
        select(
            PayrollLine.worker_id,
            func.coalesce(func.sum(PayrollLine.gross_amount), 0).label("gross"),
            func.coalesce(func.sum(PayrollLine.net_amount), 0).label("net"),
        ).join(PayrollRun, PayrollRun.id == PayrollLine.payroll_run_id)
         .where(PayrollRun.tenant_id == tid, PayrollRun.week_start >= date_from, PayrollRun.week_end <= date_to)
         .group_by(PayrollLine.worker_id)
    )
    payroll = {r.worker_id: r for r in payroll_res}

    rows = []
    total_planned = total_actual = total_labor_cost = total_payroll_net = 0.0
    total_present = total_absent = 0
    for w in workers:
        att = attendance.get(w.id, {})
        present = att.get("present", 0) + att.get("on_project", 0)
        absent = att.get("absent", 0)
        half = att.get("half", 0)
        leave = att.get("leave", 0)
        total_days = present + absent + half + leave
        attendance_rate = round((present + half * 0.5) / total_days * 100, 1) if total_days else 0

        m = wom.get(w.id)
        planned_hours = float(m.planned_hours) if m else 0.0
        actual_hours = float(m.actual_hours) if m else 0.0
        labor_cost = float(m.labor_cost) if m else 0.0
        wo_count = m.wo_count if m else 0
        efficiency = round(planned_hours / actual_hours * 100, 1) if actual_hours else None

        p = payroll.get(w.id)
        payroll_gross = float(p.gross) if p else 0.0
        payroll_net = float(p.net) if p else 0.0

        total_planned += planned_hours
        total_actual += actual_hours
        total_labor_cost += labor_cost
        total_payroll_net += payroll_net
        total_present += present
        total_absent += absent

        rows.append({
            "worker_id": w.id, "code": w.code, "name": w.name, "job_title": w.job_title,
            "present_days": present, "absent_days": absent, "half_days": half, "leave_days": leave,
            "attendance_rate": attendance_rate,
            "work_orders": wo_count,
            "planned_hours": round(planned_hours, 2), "actual_hours": round(actual_hours, 2),
            "efficiency_pct": efficiency,
            "labor_cost": round(labor_cost, 2),
            "payroll_gross": round(payroll_gross, 2), "payroll_net": round(payroll_net, 2),
        })

    rows.sort(key=lambda r: r["actual_hours"], reverse=True)

    return {
        "date_from": str(date_from), "date_to": str(date_to),
        "total_workers": len(workers),
        "total_planned_hours": round(total_planned, 2),
        "total_actual_hours": round(total_actual, 2),
        "overall_efficiency_pct": round(total_planned / total_actual * 100, 1) if total_actual else None,
        "total_labor_cost": round(total_labor_cost, 2),
        "total_payroll_net": round(total_payroll_net, 2),
        "total_present_days": total_present,
        "total_absent_days": total_absent,
        "rows": rows,
    }


# ══════════════════════════════════════════════════════════════
# PERIOD COMPARISON — مقارنة الفترات
# ══════════════════════════════════════════════════════════════

async def _period_cogs(db: AsyncSession, tid: int, date_from: date, date_to: date) -> float:
    """تكلفة البضاعة المباعة الفعلية للفترة — من دفتر الأستاذ (حساب 5100) مباشرة،
    بدل تقدير مبني على WorkOrder.actual_cost.

    ليه التغيير: journal_cogs() بقى بيتسجل الآن من كل مسارات البيع الثلاثة
    (تسليم أمر توريد supply_contracts، فوترة أمر إنتاج مباشرة work_order_to_invoice،
    وتسليم delivery_orders) بنفس التاريخ بالظبط اللي اتسجل بيه إيراد البيع
    المقابل. الاعتماد على WorkOrder.actual_cost + WorkOrder.created_at (الكود
    القديم) كان بيفوّت تكلفة مبيعات عقود التوريد بالكامل (مفيش WorkOrder ليها
    أصلاً) وكان بيقارن تاريخ *إنشاء* أمر الإنتاج بتاريخ *فاتورة* البيع —
    فترتين مختلفتين تماماً في الغالب — فالربح المعروض كان بيبقى مبالغ فيه
    (تكلفة مفقودة) أو في توقيت غلط عن الإيراد الحقيقي.
    """
    from app.models.models import AccountingEntry, AccountingEntryLine, ChartOfAccount
    cogs_account_ids = select(ChartOfAccount.id).where(
        ChartOfAccount.tenant_id == tid, ChartOfAccount.code == "5100",
    )
    # صافي الحساب = مدين − دائن: مرتجعات المبيعات (journal_cogs_reversal) وقيود
    # العكس (journal_reverse_entry) بتسجّل 5100 في الجانب الدائن — لو حسبنا
    # المدين بس، تكلفة البضاعة المرتجعة كانت بتفضل محسوبة والربح يطلع أقل من الحقيقي.
    total = await db.scalar(
        select(func.coalesce(func.sum(case(
            (AccountingEntryLine.side == "debit", AccountingEntryLine.amount),
            else_=-AccountingEntryLine.amount,
        )), 0))
        .select_from(AccountingEntryLine)
        .join(AccountingEntry, AccountingEntry.id == AccountingEntryLine.entry_id)
        .where(
            AccountingEntry.tenant_id == tid,
            AccountingEntry.entry_date >= date_from,
            AccountingEntry.entry_date <= date_to,
            AccountingEntryLine.account_id.in_(cogs_account_ids),
        )
    )
    return float(total or 0)


async def _period_kpis(db: AsyncSession, tid: int, date_from: date, date_to: date) -> dict:
    """يحسب أهم مؤشرات الأداء لفترة معيّنة (مقصورة على tenant الحالي)"""
    sales_total = float(await db.scalar(
        select(func.coalesce(func.sum(Invoice.total), 0))
        .where(Invoice.tenant_id == tid, Invoice.date >= date_from, Invoice.date <= date_to,
               Invoice.status != "cancelled")
    ) or 0)
    collected = float(await db.scalar(
        select(func.coalesce(func.sum(Invoice.paid_amount), 0))
        .where(Invoice.tenant_id == tid, Invoice.date >= date_from, Invoice.date <= date_to,
               Invoice.status != "cancelled")
    ) or 0)
    invoices_count = await db.scalar(
        select(func.count()).select_from(Invoice)
        .where(Invoice.tenant_id == tid, Invoice.date >= date_from, Invoice.date <= date_to,
               Invoice.status != "cancelled")
    ) or 0
    purchases_total = float(await db.scalar(
        select(func.coalesce(func.sum(PurchaseInvoice.total), 0))
        .where(PurchaseInvoice.tenant_id == tid, PurchaseInvoice.date >= date_from, PurchaseInvoice.date <= date_to)
    ) or 0)

    dt_from = datetime.combine(date_from, time.min)
    dt_to = datetime.combine(date_to, time.max)
    # ملحوظة: production_cost هنا مقياس تشغيلي منفصل ("كام اتكلّف تصنيع اللي
    # خلص هذا الشهر") — مقصود يفضل مبني على WorkOrder.actual_cost/status=done،
    # مش المفروض يُستخدم كتكلفة مبيعات (COGS) لحساب الربح. راجع _period_cogs
    # تحت لحساب الربح الفعلي.
    production_cost = float(await db.scalar(
        select(func.coalesce(func.sum(WorkOrder.actual_cost), 0))
        .where(WorkOrder.tenant_id == tid, WorkOrder.status == "done",
               WorkOrder.created_at >= dt_from, WorkOrder.created_at <= dt_to)
    ) or 0)
    completed_work_orders = await db.scalar(
        select(func.count()).select_from(WorkOrder)
        .where(WorkOrder.tenant_id == tid, WorkOrder.status == "done",
               WorkOrder.created_at >= dt_from, WorkOrder.created_at <= dt_to)
    ) or 0

    cashflow_in = float(await db.scalar(
        select(func.coalesce(func.sum(TreasuryTransaction.amount), 0))
        .where(TreasuryTransaction.tenant_id == tid, TreasuryTransaction.direction == "in",
               TreasuryTransaction.txn_date >= date_from, TreasuryTransaction.txn_date <= date_to,
               _active_txn_cond())
    ) or 0)
    cashflow_out = float(await db.scalar(
        select(func.coalesce(func.sum(TreasuryTransaction.amount), 0))
        .where(TreasuryTransaction.tenant_id == tid, TreasuryTransaction.direction == "out",
               TreasuryTransaction.txn_date >= date_from, TreasuryTransaction.txn_date <= date_to,
               _active_txn_cond())
    ) or 0)

    cogs_actual = await _period_cogs(db, tid, date_from, date_to)
    profit = sales_total - cogs_actual

    return {
        "date_from": str(date_from), "date_to": str(date_to),
        "sales_total": round(sales_total, 2),
        "collected": round(collected, 2),
        "invoices_count": invoices_count,
        "purchases_total": round(purchases_total, 2),
        "production_cost": round(production_cost, 2),
        "cogs_actual": round(cogs_actual, 2),
        "completed_work_orders": completed_work_orders,
        "cashflow_in": round(cashflow_in, 2),
        "cashflow_out": round(cashflow_out, 2),
        "cashflow_net": round(cashflow_in - cashflow_out, 2),
        "profit": round(profit, 2),
    }


def _pct_change(curr: float, prev: float) -> Optional[float]:
    if prev == 0:
        return None if curr == 0 else 100.0
    return round((curr - prev) / abs(prev) * 100, 1)


@router.get("/period-comparison")
async def period_comparison_report(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    compare_from: Optional[date] = None,
    compare_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """مقارنة أهم المؤشرات بين فترتين — الفترة السابقة تُحسب تلقائيًا بنفس طول الفترة الحالية إن لم تُحدَّد"""
    tid = _tid()
    if not date_from:
        date_from = date.today().replace(day=1)
    if not date_to:
        date_to = date.today()
    _check_range(date_from, date_to)

    _check_range(compare_from, compare_to)
    if not compare_from or not compare_to:
        period_len = (date_to - date_from).days + 1
        compare_to = date_from - timedelta(days=1)
        compare_from = compare_to - timedelta(days=period_len - 1)

    current = await _period_kpis(db, tid, date_from, date_to)
    previous = await _period_kpis(db, tid, compare_from, compare_to)

    metrics = [
        ("sales_total", "إجمالي المبيعات"),
        ("collected", "المحصّل"),
        ("invoices_count", "عدد الفواتير"),
        ("purchases_total", "إجمالي المشتريات"),
        ("production_cost", "تكلفة الإنتاج"),
        ("cogs_actual", "تكلفة البضاعة المباعة الفعلية"),
        ("completed_work_orders", "أوامر إنتاج مكتملة"),
        ("cashflow_in", "تدفق نقدي داخل"),
        ("cashflow_out", "تدفق نقدي خارج"),
        ("cashflow_net", "صافي التدفق النقدي"),
        ("profit", "الربح التقديري"),
    ]
    comparison = [{
        "key": key, "label": label,
        "current": current[key], "previous": previous[key],
        "change": round(current[key] - previous[key], 2),
        "change_pct": _pct_change(current[key], previous[key]),
    } for key, label in metrics]

    return {
        "current": current,
        "previous": previous,
        "comparison": comparison,
    }


# ══════════════════════════════════════════════════════════════
# OVERALL DASHBOARD — ملخص الشركة الكامل
# ══════════════════════════════════════════════════════════════

@router.get("/dashboard")
async def executive_dashboard(db: AsyncSession = Depends(get_db)):
    """داشبورد تنفيذي شامل لجميع الأقسام"""
    tid = _tid()
    today = date.today()
    month_start = today.replace(day=1)

    # Inventory KPIs
    inv_value = await db.scalar(
        select(func.coalesce(func.sum(Material.stock_qty * Material.unit_cost), 0))
        .where(Material.tenant_id == tid)
    )
    low_stock = await db.scalar(
        select(func.count()).select_from(Material)
        .where(Material.tenant_id == tid, Material.stock_qty <= Material.reorder_level, Material.reorder_level > 0)
    )

    # Production KPIs
    open_wos    = await db.scalar(
        select(func.count()).select_from(WorkOrder)
        .where(WorkOrder.tenant_id == tid, WorkOrder.status == "in_progress")
    )
    mtd_wo_cost = await db.scalar(
        select(func.coalesce(func.sum(WorkOrder.actual_cost), 0)).where(
            WorkOrder.tenant_id == tid,
            # كان extract(month) بس — بيجمع نفس الشهر من كل السنين السابقة
            WorkOrder.created_at >= datetime.combine(month_start, time.min),
            WorkOrder.status == "done",
        )
    )

    # Sales KPIs
    mtd_sales = await db.scalar(
        select(func.coalesce(func.sum(Invoice.total), 0)).where(
            Invoice.tenant_id == tid, Invoice.date >= month_start,
            Invoice.status != "cancelled",
        )
    )
    mtd_collected = await db.scalar(
        select(func.coalesce(func.sum(Invoice.paid_amount), 0)).where(
            Invoice.tenant_id == tid, Invoice.date >= month_start,
            Invoice.status != "cancelled",
        )
    )
    total_receivable = await db.scalar(
        select(func.coalesce(func.sum(Invoice.total - Invoice.paid_amount), 0)).where(
            Invoice.tenant_id == tid, Invoice.status.in_(OPEN_INVOICE_STATUSES)
        )
    )

    # Purchases KPIs
    mtd_purchases = await db.scalar(
        select(func.coalesce(func.sum(PurchaseInvoice.total), 0)).where(
            PurchaseInvoice.tenant_id == tid, PurchaseInvoice.date >= month_start
        )
    )
    total_payable = await db.scalar(
        select(func.coalesce(func.sum(PurchaseInvoice.total - PurchaseInvoice.paid_amount), 0)).where(
            PurchaseInvoice.tenant_id == tid, PurchaseInvoice.status.in_(["received", "partial"])
        )
    )

    return {
        "date": str(today),
        "inventory": {
            "total_value": float(inv_value or 0),
            "low_stock_items": low_stock or 0,
        },
        "production": {
            "open_work_orders": open_wos or 0,
            "mtd_production_cost": float(mtd_wo_cost or 0),
        },
        "sales": {
            "mtd_invoiced": float(mtd_sales or 0),
            "mtd_collected": float(mtd_collected or 0),
            "total_receivable": float(total_receivable or 0),
            "collection_rate": round(float(mtd_collected or 0) / float(mtd_sales) * 100, 1) if float(mtd_sales or 0) else 0,
        },
        "purchases": {
            "mtd_purchases": float(mtd_purchases or 0),
            "total_payable": float(total_payable or 0),
        },
    }


# ══════════════════════════════════════════════════════════════
# CASH FLOW / TREASURY REPORT — تقرير الخزينة والتدفقات النقدية
# ══════════════════════════════════════════════════════════════

@router.get("/cashflow")
async def cashflow_report(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    treasury_id: Optional[int] = None,
    group_by: str = Query("day", pattern="^(day|week|month)$"),
    db: AsyncSession = Depends(get_db),
):
    """تقرير الخزينة والتدفقات النقدية: الداخل/الخارج لكل فترة + تجميع حسب الفئة"""
    tid = _tid()
    if not date_from:
        date_from = date.today().replace(day=1)
    if not date_to:
        date_to = date.today()
    _check_range(date_from, date_to)

    # ── الأرصدة الحالية للخزن ──
    treas_q = select(Treasury).where(Treasury.tenant_id == tid, Treasury.is_active == True)
    if treasury_id:
        treas_q = treas_q.where(Treasury.id == treasury_id)
    treasuries = (await db.execute(treas_q)).scalars().all()

    # ── الرصيد الافتتاحي: رصيد بداية الخزن + الحركات قبل date_from ──
    # الرصيد الافتتاحي لازم يشمل كل الخزن (حتى الموقوفة) لأن حركاتها السابقة
    # (prior_q تحت) محسوبة بالكامل — وإلا الرصيد المتراكم يطلع غلط.
    all_treas_q = select(func.coalesce(func.sum(Treasury.opening_balance), 0)).where(Treasury.tenant_id == tid)
    if treasury_id:
        all_treas_q = all_treas_q.where(Treasury.id == treasury_id)
    opening_balance = float(await db.scalar(all_treas_q) or 0)
    prior_q = select(
        func.coalesce(func.sum(
            case((TreasuryTransaction.direction == "in", TreasuryTransaction.amount), else_=-TreasuryTransaction.amount)
        ), 0)
    ).where(TreasuryTransaction.tenant_id == tid, TreasuryTransaction.txn_date < date_from,
            _active_txn_cond())
    if treasury_id:
        prior_q = prior_q.where(TreasuryTransaction.treasury_id == treasury_id)
    opening_balance += float(await db.scalar(prior_q) or 0)

    # ── الحركات في الفترة ──
    txn_q = (
        select(TreasuryTransaction)
        .where(TreasuryTransaction.tenant_id == tid,
               TreasuryTransaction.txn_date >= date_from, TreasuryTransaction.txn_date <= date_to,
               _active_txn_cond())
        .order_by(TreasuryTransaction.txn_date)
    )
    if treasury_id:
        txn_q = txn_q.where(TreasuryTransaction.treasury_id == treasury_id)
    txns = (await db.execute(txn_q)).scalars().all()

    cats = {c.id: c.name for c in (await db.execute(
        select(TreasuryCategory).where(TreasuryCategory.tenant_id == tid)
    )).scalars().all()}

    # ── تجميع حسب الفترة ──
    def period_key(d: date) -> str:
        if group_by == "month":
            return d.strftime("%Y-%m")
        if group_by == "week":
            iso = d.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"
        return d.strftime("%Y-%m-%d")

    periods: dict = {}
    by_category: dict = {}
    total_in = total_out = 0.0
    for t in txns:
        amt = float(t.amount or 0)
        if t.direction == "in":
            total_in += amt
        else:
            total_out += amt

        pk = period_key(t.txn_date)
        if pk not in periods:
            periods[pk] = {"period": pk, "in": 0.0, "out": 0.0}
        periods[pk]["in" if t.direction == "in" else "out"] += amt

        cat_name = cats.get(t.category_id, "بدون فئة")
        ck = (cat_name, t.direction)
        if ck not in by_category:
            by_category[ck] = {"name": cat_name, "flow": t.direction, "total": 0.0, "count": 0}
        by_category[ck]["total"] += amt
        by_category[ck]["count"] += 1

    # بناء قائمة الفترات مع رصيد متراكم
    running = opening_balance
    period_rows = []
    for pk in sorted(periods.keys()):
        p = periods[pk]
        net = p["in"] - p["out"]
        running += net
        period_rows.append({
            "period": pk, "in": round(p["in"], 2), "out": round(p["out"], 2),
            "net": round(net, 2), "balance": round(running, 2),
        })

    net_total = total_in - total_out
    return {
        "date_from": str(date_from),
        "date_to": str(date_to),
        "group_by": group_by,
        "opening_balance": round(opening_balance, 2),
        "closing_balance": round(opening_balance + net_total, 2),
        "total_in": round(total_in, 2),
        "total_out": round(total_out, 2),
        "net": round(net_total, 2),
        "periods": period_rows,
        "by_category": sorted(by_category.values(), key=lambda x: x["total"], reverse=True),
        "treasuries": [{
            "id": t.id, "name": t.name, "kind": t.kind, "balance": float(t.balance or 0),
        } for t in treasuries],
    }


# ── Analytics: Monthly Sales Breakdown ────────────────────────
@router.get("/analytics/monthly-sales")
async def monthly_sales_breakdown(db: AsyncSession = Depends(get_db)):
    """مبيعات شهرية للـ 12 شهر الماضية — للوحة التحليلات"""
    tid = _tid()
    from datetime import date as dt
    import calendar

    today_d = dt.today()
    results = []

    for m in range(11, -1, -1):
        # حساب الشهر المستهدف بشكل صحيح — بدون أخطاء رياضية
        month = today_d.month - m
        year  = today_d.year
        while month <= 0:
            month += 12
            year  -= 1

        mo_start = dt(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]   # يحسب تلقائي السنة الكبيسة
        mo_end   = dt(year, month, last_day)

        total = await db.scalar(
            select(func.coalesce(func.sum(Invoice.total), 0))
            .where(Invoice.tenant_id == tid, Invoice.date >= mo_start, Invoice.date <= mo_end,
                   Invoice.status != "cancelled")
        )
        results.append({"month": f"{year}-{month:02d}", "total": float(total or 0)})

    return {"monthly_breakdown": results}


@router.get("/analytics/production-by-type")
async def production_by_type(db: AsyncSession = Depends(get_db)):
    """هامش الربح حسب نوع أمر الإنتاج — للوحة التحليلات"""
    tid = _tid()
    from app.models.models import WorkOrder

    res = await db.execute(
        select(WorkOrder).where(WorkOrder.tenant_id == tid, WorkOrder.status == "done")
    )
    work_orders = res.scalars().all()

    # الإيراد الفعلي = إجمالي الفواتير المرتبطة بأمر الإنتاج (نفس مصدر تقرير
    # الربحية /profitability). كان الكود بيستخدم planned_cost كـ "إيراد" —
    # يعني الهامش المعروض كان مجرد انحراف التكلفة الفعلية عن المخططة، مش ربح.
    wo_ids = [wo.id for wo in work_orders]
    revenue_by_wo: dict = {}
    if wo_ids:
        rev_rows = await db.execute(
            select(Invoice.work_order_id, func.coalesce(func.sum(Invoice.total), 0))
            .where(Invoice.tenant_id == tid, Invoice.work_order_id.in_(wo_ids))
            .group_by(Invoice.work_order_id)
        )
        revenue_by_wo = {r[0]: float(r[1] or 0) for r in rev_rows.all()}

    # تجميع حسب الفئة (أول جزء من كود أمر الإنتاج)
    type_map: dict = {}
    for wo in work_orders:
        parts = (wo.code or "عام").split("-")
        category = parts[1] if len(parts) > 1 else (wo.code or "عام")
        if category not in type_map:
            type_map[category] = {"name": category, "revenue": 0.0, "cost": 0.0, "count": 0}
        type_map[category]["cost"]    += float(wo.actual_cost or 0)
        type_map[category]["revenue"] += revenue_by_wo.get(wo.id, 0.0)
        type_map[category]["count"]   += 1

    result = [
        {
            "name": v["name"],
            "revenue": v["revenue"],
            "cost": v["cost"],
            "count": v["count"],
            "margin": round((v["revenue"] - v["cost"]) / v["revenue"] * 100, 1)
                      if v["revenue"] > 0 else 0,
        }
        for v in sorted(type_map.values(), key=lambda x: x["revenue"], reverse=True)
    ]
    return {"by_type": result[:8]}


# ══════════════════════════════════════════════════════════════════════
# AGING REPORT — طباعة HTML
# ══════════════════════════════════════════════════════════════════════

from fastapi.responses import HTMLResponse

aging_print_router = APIRouter(prefix="/reports", tags=["reports_print"], dependencies=[Depends(require_any)])
export_router     = APIRouter(prefix="/reports/export", tags=["reports_export"], dependencies=[Depends(require_any)])


@aging_print_router.get("/sales/aging/print", response_class=HTMLResponse)
async def print_aging_report(db: AsyncSession = Depends(get_db)):
    """طباعة تقرير مديونيات العملاء — Aging Report"""
    tid = _tid()
    from datetime import date as _date
    import html as _h
    today = _date.today()

    unpaid_res = await db.execute(
        select(Invoice).options(selectinload(Invoice.customer)).where(
            Invoice.tenant_id == tid,
            Invoice.status.in_(OPEN_INVOICE_STATUSES),
            Invoice.total > Invoice.paid_amount,
        ).order_by(Invoice.due_date)
    )
    invoices = unpaid_res.scalars().all()

    # Fetch company
    from app.models.models import WorkshopProfile
    q = select(WorkshopProfile).where(WorkshopProfile.tenant_id == tid)
    prof_res = await db.execute(q.limit(1))
    prof = prof_res.scalar_one_or_none()
    cname = _h.escape(prof.name if prof else "ورشة معدات الاعلاف")
    # الأقواس مهمة: من غيرها `prof.mobile or ... if prof else ""` بيتقيّم
    # prof.mobile الأول فيرمي AttributeError (500) لو مفيش WorkshopProfile.
    cphone = _h.escape(((prof.mobile or prof.phone) if prof else "") or "")
    # logo_data بيتحط جوه attribute — نهرب الاقتباسات (منع حقن HTML في صفحة
    # الطباعة من قيمة مخزّنة تحتوي ").
    _logo = str((prof.logo_data or "") if prof else "")
    logo_html = (
        f'<img src="{_h.escape(_logo, quote=True)}" alt="logo" '
        'style="max-height:55px;max-width:130px;object-fit:contain;display:block;"/>'
        if _logo else ""
    )

    buckets_def = [
        ("current", "جارية (لم تستحق)", "#15803D", 0,  0),
        ("1-30",    "1–30 يوم",          "#1D4ED8", 1,  30),
        ("31-60",   "31–60 يوم",         "#D97706", 31, 60),
        ("61-90",   "61–90 يوم",         "#EA580C", 61, 90),
        ("90+",     "أكثر من 90 يوم",    "#991B1B", 91, 99999),
    ]
    buckets = {k: [] for k, *_ in buckets_def}
    for inv in invoices:
        # ── استخدام مصدر الحقيقة المركزي بدل invoice.due_date مباشرة ──
        due_status   = await get_invoice_due_status(db, inv, today)
        remaining    = float(inv.total - inv.paid_amount)
        days_overdue = due_status.days_overdue if due_status.is_overdue else -(
            (due_status.effective_due_date - today).days
        )
        entry = {
            "code": inv.code,
            "customer": inv.customer.name if inv.customer else "—",
            "date": str(inv.date),
            "due":  str(due_status.effective_due_date),
            "remaining": remaining,
            "days": days_overdue,
            "has_schedule": due_status.has_schedule,
        }
        if not due_status.is_overdue:  buckets["current"].append(entry)
        elif days_overdue <= 30:       buckets["1-30"].append(entry)
        elif days_overdue <= 60:       buckets["31-60"].append(entry)
        elif days_overdue <= 90:       buckets["61-90"].append(entry)
        else:                          buckets["90+"].append(entry)

    totals  = {k: sum(e["remaining"] for e in v) for k, v in buckets.items()}
    grand   = sum(totals.values())

    def fmt(v): return f"{v:,.2f}"

    # Build table rows per bucket
    bucket_sections = ""
    for key, label, color, *_ in buckets_def:
        rows = buckets[key]
        if not rows: continue
        bucket_sections += f"""
        <tr>
          <td colspan="6" style="background:{color};color:#fff;font-weight:800;font-size:12px;padding:8px 14px;letter-spacing:.05em;">
            {label} — {len(rows)} فاتورة — إجمالي: {fmt(totals[key])} ج.م
          </td>
        </tr>"""
        for r in rows:
            schedule_badge = (
                '<span style="background:#1D4ED8;color:#fff;border-radius:3px;padding:1px 6px;font-size:10px;">أقساط</span>'
                if r.get("has_schedule") else ""
            )
            bucket_sections += f"""
        <tr style="border-bottom:1px solid #f0f0f0;">
          <td style="padding:8px 14px;font-weight:700;color:{color}">{_h.escape(r['code'])}</td>
          <td style="padding:8px 14px;">{_h.escape(r['customer'])}</td>
          <td style="padding:8px 14px;color:#6B7280;">{r['date']}</td>
          <td style="padding:8px 14px;color:#6B7280;">{r['due']}</td>
          <td style="padding:8px 14px;text-align:center;">{schedule_badge}</td>
          <td style="padding:8px 14px;font-weight:700;color:{color};text-align:left;">{fmt(r['remaining'])} ج.م</td>
        </tr>"""

    # Summary bar
    summary_cells = ""
    for key, label, color, *_ in buckets_def:
        if totals[key] == 0: continue
        pct = totals[key] / grand * 100 if grand else 0
        summary_cells += f"""
        <div style="flex:1;min-width:120px;background:{color}18;border-top:3px solid {color};border-radius:10px;padding:12px 14px;">
          <div style="font-size:10px;color:{color};font-weight:800;margin-bottom:4px;">{label}</div>
          <div style="font-size:17px;font-weight:900;color:{color}">{fmt(totals[key])}</div>
          <div style="font-size:10px;color:#9CA3AF">{pct:.1f}% من الإجمالي</div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<title>تقرير مديونيات العملاء — {today}</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Cairo',Tahoma,Arial,sans-serif;font-size:13px;color:#1A1A2E;background:#EDECEA;direction:rtl;}}
@page{{size:A4 landscape;margin:6mm;}}
@media print{{.no-print{{display:none!important;}}body{{background:#fff;}}.page{{max-width:100%;box-shadow:none;margin:0;border-radius:0;}}}}
.page{{max-width:1050px;margin:12px auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 4px 40px rgba(0,0,0,.1);}}
.doc-header{{background:linear-gradient(135deg,#081828,#0C2340,#143558);color:#fff;padding:18px 28px;display:flex;justify-content:space-between;align-items:center;}}
.co-name{{font-size:18px;font-weight:800;}}
.co-phone{{font-size:11px;color:rgba(255,255,255,.5);margin-top:3px;}}
.report-title{{text-align:left;}}
.rt-main{{font-size:22px;font-weight:900;color:#FCD34D;}}
.rt-sub{{font-size:11px;color:rgba(255,255,255,.5);margin-top:3px;}}
.grand-bar{{background:#0D2440;padding:10px 28px;display:flex;justify-content:space-between;align-items:center;}}
.grand-label{{font-size:12px;color:rgba(255,255,255,.5);}}
.grand-value{{font-size:24px;font-weight:900;color:#FCD34D;}}
.summary-row{{padding:14px 20px;display:flex;gap:10px;flex-wrap:wrap;border-bottom:2px solid #f0f0f0;}}
table{{width:100%;border-collapse:collapse;}}
thead tr{{background:#0C2340;}}
thead th{{padding:10px 14px;font-size:11px;font-weight:700;color:rgba(255,255,255,.75);text-align:right;}}
thead th:last-child{{text-align:left;}}
.doc-footer{{background:#0C2340;color:rgba(255,255,255,.5);padding:8px 28px;text-align:center;font-size:11px;}}
.action-bar{{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:rgba(8,24,40,.9);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:7px 12px;display:flex;gap:8px;}}
.btn-print{{background:#FCD34D;color:#0C2340;border:none;border-radius:7px;padding:7px 18px;font-weight:800;font-size:13px;cursor:pointer;font-family:inherit;}}
</style>
</head>
<body onload="setTimeout(()=>window.print(),400)">
<div class="page">
  <div class="doc-header">
    <div>
      {logo_html}
      <div class="co-name">{cname}</div>
      <div class="co-phone">{cphone}</div>
    </div>
    <div class="report-title">
      <div class="rt-main">تقرير مديونيات العملاء</div>
      <div class="rt-sub">Receivables Aging Report — {today}</div>
    </div>
  </div>
  <div class="grand-bar">
    <span class="grand-label">إجمالي المديونيات المستحقة</span>
    <span class="grand-value">{fmt(grand)} ج.م</span>
  </div>
  <div class="summary-row">{summary_cells}</div>
  <table>
    <thead><tr>
      <th>رقم الفاتورة</th>
      <th>العميل</th>
      <th>تاريخ الفاتورة</th>
      <th>تاريخ الاستحقاق</th>
      <th style="text-align:center">خطة أقساط</th>
      <th style="text-align:left">المبلغ المتبقي</th>
    </tr></thead>
    <tbody>{bucket_sections}</tbody>
  </table>
  {'<div style="padding:40px;text-align:center;color:#9CA3AF;font-size:14px;">✅ لا توجد مديونيات مستحقة حالياً</div>' if grand == 0 else ""}
  <div class="doc-footer">تم إنشاء التقرير بتاريخ {today} — {cname}</div>
</div>
<div class="action-bar no-print">
  <button class="btn-print" onclick="window.print()">🖨️ طباعة / PDF</button>
</div>
</body></html>"""
    return HTMLResponse(html)


# ══════════════════════════════════════════════════════════════════════
# EXCEL / CSV EXPORT  (تصدير Excel لجميع القوائم الرئيسية)
# ══════════════════════════════════════════════════════════════════════

import csv, io
from urllib.parse import quote
from fastapi.responses import StreamingResponse


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """حماية من CSV/Formula Injection: أي نص يبدأ بـ = + - @ (اسم عميل، ملاحظة...)
    Excel بينفّذه كمعادلة (ممكن HYPERLINK/DDE خبيث). نسبقه بـ ' عشان يتعرض
    كنص. الأرقام (int/float) مش بتتأثر — القيم السالبة تفضل أرقام عادية."""
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_PREFIXES:
        return "'" + value
    return value


def _csv_response(rows: list[list], headers: list[str], filename: str) -> StreamingResponse:
    """Build a UTF-8-with-BOM CSV that Excel opens correctly."""
    buf = io.StringIO()
    buf.write('﻿')  # BOM so Excel interprets UTF-8 correctly
    w = csv.writer(buf)
    w.writerow(headers)
    for row in rows:
        w.writerow([_csv_safe(c) for c in row])
    buf.seek(0)
    # دعم أسماء ملفات بالعربية في Content-Disposition (latin-1 لا يدعمها)
    # اسم الملف ممكن يحتوي اسم عميل/مورد — نشيل أي حرف ممكن يكسر الهيدر
    # (" أو \r\n أو ; ...) عشان نمنع Header injection.
    ascii_name = ''.join(
        ch for ch in filename.encode('ascii', 'ignore').decode('ascii')
        if ch.isalnum() or ch in '._-'
    )
    if not ascii_name or ascii_name.startswith('.'):
        ascii_name = 'export.csv'
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        iter([buf.read().encode('utf-8-sig')]),
        media_type='text/csv; charset=utf-8-sig',
        headers={'Content-Disposition': disposition},
    )


@export_router.get("/materials")
async def export_materials(db: AsyncSession = Depends(get_db)):
    tid = _tid()
    rows = (await db.execute(
        select(Material).options(selectinload(Material.category))
        .where(Material.tenant_id == tid).order_by(Material.code)
    )).scalars().all()
    headers = ['الكود', 'الاسم', 'الفئة', 'الوحدة', 'الكمية', 'حد الطلب', 'سعر الوحدة', 'قيمة المخزون']
    data = [[
        m.code, m.name_ar or m.name_en or '', (m.category.name_ar if m.category else ''),
        m.unit, float(m.stock_qty), float(m.reorder_level),
        float(m.unit_cost), round(float(m.stock_qty) * float(m.unit_cost), 2),
    ] for m in rows]
    return _csv_response(data, headers, 'materials.csv')


@export_router.get("/invoices")
async def export_invoices(db: AsyncSession = Depends(get_db)):
    tid = _tid()
    rows = (await db.execute(
        select(Invoice).options(selectinload(Invoice.customer))
        .where(Invoice.tenant_id == tid).order_by(Invoice.date.desc())
    )).scalars().all()
    headers = ['الكود', 'العميل', 'التاريخ', 'تاريخ الاستحقاق', 'الإجمالي', 'المدفوع', 'المتبقي', 'الحالة']
    data = [[
        inv.code,
        inv.customer.name if inv.customer else '',
        str(inv.date),
        str(inv.due_date) if inv.due_date else '',
        float(inv.total or 0),
        float(inv.paid_amount or 0),
        round(float(inv.total or 0) - float(inv.paid_amount or 0), 2),
        inv.status,
    ] for inv in rows]
    return _csv_response(data, headers, 'invoices.csv')


@export_router.get("/customers")
async def export_customers(db: AsyncSession = Depends(get_db)):
    tid = _tid()
    rows = (await db.execute(
        select(Customer).where(Customer.tenant_id == tid).order_by(Customer.name)
    )).scalars().all()
    headers = ['الاسم', 'الهاتف', 'البريد', 'العنوان', 'الرصيد']
    data = [[
        # Customer مالوش عمود email — c.email كان بيرمي AttributeError (500) لأي تصدير فيه عميل
        c.name, c.phone or '', getattr(c, 'email', None) or '', c.address or '',
        float(c.balance or 0),
    ] for c in rows]
    return _csv_response(data, headers, 'customers.csv')


@export_router.get("/work-orders")
async def export_work_orders(db: AsyncSession = Depends(get_db)):
    tid = _tid()
    rows = (await db.execute(
        select(WorkOrder).options(selectinload(WorkOrder.equipment))
        .where(WorkOrder.tenant_id == tid).order_by(WorkOrder.created_at.desc())
    )).scalars().all()
    headers = ['الكود', 'المعدة', 'الحالة', 'التكلفة المخططة', 'التكلفة الفعلية', 'الانحراف', 'تاريخ البدء', 'تاريخ الانتهاء']
    data = [[
        wo.code,
        wo.equipment.name_ar if wo.equipment else '',
        wo.status,
        float(wo.planned_cost or 0),
        float(wo.actual_cost or 0),
        round(float(wo.planned_cost or 0) - float(wo.actual_cost or 0), 2),
        str(wo.start_date) if wo.start_date else '',
        str(wo.end_date) if wo.end_date else '',
    ] for wo in rows]
    return _csv_response(data, headers, 'work_orders.csv')


@export_router.get("/profitability")
async def export_profitability(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    group_by: str = Query("work_order", pattern="^(work_order|customer|equipment)$"),
    db: AsyncSession = Depends(get_db),
):
    report = await profitability_report(date_from, date_to, group_by, db)
    if group_by in ("customer", "equipment"):
        # تصدير نفس التجميع المعروض في الشاشة (كان التصدير بيتجاهل group_by)
        g_headers = ['العميل' if group_by == 'customer' else 'المعدة',
                     'عدد الأوامر', 'الإيراد', 'التكلفة', 'الربح', 'الهامش %']
        g_data = [[g['name'], g['count'], g['revenue'], g['cost'], g['profit'], g['margin_pct']]
                  for g in report['grouped']]
        g_data.append(['الإجمالي', report['count'], report['total_revenue'], report['total_cost'],
                       report['total_profit'], report['total_margin_pct']])
        return _csv_response(g_data, g_headers, f'profitability_by_{group_by}.csv')
    headers = ['الكود', 'العميل', 'المعدة', 'الحالة', 'الإيراد', 'التكلفة', 'الربح', 'الهامش %']
    data = [[
        r['code'], r['customer'], r['equipment'], r['status'],
        r['revenue'], r['cost'], r['profit'], r['margin_pct'],
    ] for r in report['rows']]
    data.append(['', '', '', 'الإجمالي', report['total_revenue'], report['total_cost'], report['total_profit'], report['total_margin_pct']])
    return _csv_response(data, headers, 'profitability_report.csv')


@export_router.get("/cashflow")
async def export_cashflow(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    treasury_id: Optional[int] = None,
    group_by: str = Query("day", pattern="^(day|week|month)$"),
    db: AsyncSession = Depends(get_db),
):
    report = await cashflow_report(date_from, date_to, treasury_id, group_by, db)
    headers = ['الفترة', 'داخل', 'خارج', 'صافي', 'الرصيد']
    data = [[p['period'], p['in'], p['out'], p['net'], p['balance']] for p in report['periods']]
    data.append(['الإجمالي', report['total_in'], report['total_out'], report['net'], report['closing_balance']])
    return _csv_response(data, headers, 'cashflow_report.csv')


@export_router.get("/labor-performance")
async def export_labor_performance(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    report = await labor_performance_report(date_from, date_to, db)
    headers = ['الكود', 'الاسم', 'الوظيفة', 'أيام حضور', 'أيام غياب', 'نسبة الحضور %',
               'أوامر العمل', 'ساعات مخططة', 'ساعات فعلية', 'الكفاءة %', 'تكلفة العمالة', 'صافي الراتب']
    data = [[
        r['code'], r['name'], r['job_title'] or '', r['present_days'], r['absent_days'], r['attendance_rate'],
        r['work_orders'], r['planned_hours'], r['actual_hours'],
        r['efficiency_pct'] if r['efficiency_pct'] is not None else '',
        r['labor_cost'], r['payroll_net'],
    ] for r in report['rows']]
    data.append([
        '', 'الإجمالي', '', report['total_present_days'], report['total_absent_days'], '',
        '', report['total_planned_hours'], report['total_actual_hours'],
        report['overall_efficiency_pct'] if report['overall_efficiency_pct'] is not None else '',
        report['total_labor_cost'], report['total_payroll_net'],
    ])
    return _csv_response(data, headers, 'labor_performance_report.csv')


@export_router.get("/period-comparison")
async def export_period_comparison(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    compare_from: Optional[date] = None,
    compare_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    report = await period_comparison_report(date_from, date_to, compare_from, compare_to, db)
    headers = ['المؤشر',
               f"الفترة الحالية ({report['current']['date_from']} → {report['current']['date_to']})",
               f"الفترة السابقة ({report['previous']['date_from']} → {report['previous']['date_to']})",
               'الفرق', 'نسبة التغيير %']
    data = [[
        c['label'], c['current'], c['previous'], c['change'],
        c['change_pct'] if c['change_pct'] is not None else '',
    ] for c in report['comparison']]
    return _csv_response(data, headers, 'period_comparison_report.csv')


@export_router.get("/quotations")
async def export_quotations(db: AsyncSession = Depends(get_db)):
    tid = _tid()
    rows = (await db.execute(
        select(Quotation).options(selectinload(Quotation.customer))
        .where(Quotation.tenant_id == tid).order_by(Quotation.date.desc())
    )).scalars().all()
    headers = ['الرقم', 'العميل', 'التاريخ', 'صالح حتى', 'الإجمالي', 'الحالة']
    data = [[
        q.code,
        q.customer.name if q.customer else '',
        str(q.date) if q.date else '',
        str(q.valid_until) if q.valid_until else '',
        float(q.total or 0),
        q.status,
    ] for q in rows]
    return _csv_response(data, headers, 'quotations.csv')


@export_router.get("/customer-ledger/{cust_id}")
async def export_customer_ledger(cust_id: int, date_from: Optional[date] = None,
                                  date_to: Optional[date] = None,
                                  location_id: Optional[int] = None,
                                  db: AsyncSession = Depends(get_db)):
    from app.api.customers import customer_ledger
    tid = _tid()
    # customer_ledger بيجيب العميل بـ db.get(Customer, id) من غير فلتر tenant —
    # نتأكد هنا إن العميل تابع للمستأجر الحالي قبل تصدير كشف حسابه.
    owned = await db.scalar(
        select(Customer.id).where(Customer.id == cust_id, Customer.tenant_id == tid)
    )
    if not owned:
        raise HTTPException(404, "العميل غير موجود")
    report = await customer_ledger(cust_id, date_from, date_to, location_id, db)
    headers = ['التاريخ', 'النوع', 'المرجع', 'البيان', 'مدين', 'دائن', 'الرصيد']
    data = []
    if report['date_from']:
        data.append([report['date_from'], 'رصيد افتتاحي', '', f"رصيد افتتاحي (قبل {report['date_from']})", '', '', report['opening_balance']])
    for r in report['rows']:
        data.append([r['date'], 'فاتورة' if r['type'] == 'invoice' else 'دفعة', r['ref'], r['description'],
                      r['debit'] or '', r['credit'] or '', r['balance']])
    data.append(['', '', '', 'الإجمالي', report['total_debit'], report['total_credit'], report['balance']])
    name = report['customer']['name'].replace(' ', '_')
    return _csv_response(data, headers, f'kashf_{name}.csv')


@export_router.get("/supplier-ledger/{sid}")
async def export_supplier_ledger(sid: int, date_from: Optional[date] = None,
                                  date_to: Optional[date] = None,
                                  db: AsyncSession = Depends(get_db)):
    from app.api.purchases import supplier_ledger
    _tid()
    report = await supplier_ledger(sid, date_from, date_to, db)
    headers = ['التاريخ', 'النوع', 'المرجع', 'البيان', 'مدين', 'دائن', 'الرصيد']
    data = []
    if report['date_from']:
        data.append([report['date_from'], 'رصيد افتتاحي', '', f"رصيد افتتاحي (قبل {report['date_from']})", '', '', report['opening_balance']])
    for r in report['rows']:
        data.append([r['date'], 'فاتورة' if r['type'] == 'invoice' else 'دفعة', r['ref'], r['description'],
                      r['debit'] or '', r['credit'] or '', r['balance']])
    data.append(['', '', '', 'الإجمالي', report['total_debit'], report['total_credit'], report['balance']])
    name = report['supplier']['name'].replace(' ', '_')
    return _csv_response(data, headers, f'kashf_{name}.csv')


# ══════════════════════════════════════════════════════════════
# UNIFIED PAYMENT HISTORY — سجل المدفوعات الموحد
# يجمع دفعات العملاء + دفعات الموردين مع فلتر بالتاريخ
# ══════════════════════════════════════════════════════════════

@router.get("/payments/history")
async def payment_history(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    payment_type: Optional[str] = Query(None, pattern="^(customer|supplier|all)?$"),   # customer | supplier | all
    db: AsyncSession = Depends(get_db),
):
    """سجل موحد لجميع المدفوعات — عملاء وموردين"""
    tid = _tid()
    _check_range(date_from, date_to)
    results = []

    # ── دفعات العملاء ──────────────────────────────────────────
    cq = select(CustomerPayment).options(selectinload(CustomerPayment.customer)).where(
        CustomerPayment.tenant_id == tid
    )
    if date_from:
        cq = cq.where(CustomerPayment.payment_date >= date_from)
    if date_to:
        cq = cq.where(CustomerPayment.payment_date <= date_to)
    if payment_type not in (None, "all", "customer"):
        pass  # skip customers if type = supplier
    else:
        cpays = (await db.execute(cq.order_by(CustomerPayment.payment_date.desc()))).scalars().all()
        for p in cpays:
            results.append({
                "id":           p.id,
                "type":         "customer",
                "type_ar":      "دفعة عميل",
                "direction":    "in",
                "party_name":   p.customer.name if p.customer else f"عميل #{p.customer_id}",
                "amount":       float(p.amount),
                "payment_date": str(p.payment_date),
                "payment_method": p.payment_type or "cash",
                "reference":    p.reference,
                "notes":        p.notes,
                "created_by":   p.created_by,
                "created_at":   p.created_at.isoformat() if p.created_at else None,
            })

    # ── دفعات الموردين ─────────────────────────────────────────
    if payment_type not in (None, "all", "supplier"):
        pass  # skip suppliers if type = customer
    else:
        sq = select(SupplierPayment, Supplier).join(
            Supplier, Supplier.id == SupplierPayment.supplier_id, isouter=True
        ).where(SupplierPayment.tenant_id == tid)
        if date_from:
            sq = sq.where(SupplierPayment.payment_date >= date_from)
        if date_to:
            sq = sq.where(SupplierPayment.payment_date <= date_to)
        spays = (await db.execute(sq.order_by(SupplierPayment.payment_date.desc()))).all()
        for p, sup in spays:
            results.append({
                "id":           p.id,
                "type":         "supplier",
                "type_ar":      "دفعة مورد",
                "direction":    "out",
                "party_name":   sup.name if sup else f"مورد #{p.supplier_id}",
                "amount":       float(p.amount),
                "payment_date": str(p.payment_date),
                "payment_method": p.payment_method or "cash",
                "reference":    p.reference,
                "notes":        p.notes,
                "created_by":   p.created_by,
                "created_at":   p.created_at.isoformat() if p.created_at else None,
            })

    # ترتيب نزولي بالتاريخ
    results.sort(key=lambda x: x["payment_date"], reverse=True)

    # جلب أسماء المستخدمين لـ created_by (audit trail)
    user_ids = {r.get("created_by") for r in results if r.get("created_by")}
    user_map: dict = {}
    if user_ids:
        users_rows = (await db.execute(
            select(User.id, User.full_name, User.username)
            .where(User.tenant_id == tid, User.id.in_(user_ids))
        )).all()
        user_map = {u.id: (u.full_name or u.username) for u in users_rows}
    for r in results:
        r["created_by_name"] = user_map.get(r.get("created_by"))

    total_in  = sum(r["amount"] for r in results if r["direction"] == "in")
    total_out = sum(r["amount"] for r in results if r["direction"] == "out")
    return {
        "records":   results,
        "count":     len(results),
        "total_in":  total_in,
        "total_out": total_out,
        "net":       total_in - total_out,
    }
