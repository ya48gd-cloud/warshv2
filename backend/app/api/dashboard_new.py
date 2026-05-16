"""
Dashboard API — لوحة التحكم الشاملة
"""
from datetime import date, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, and_, extract
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio

from app.core.database import get_db, AsyncSessionLocal
from app.models.models import (
    Material, StockMovement, WorkOrder, CostLine,
    Invoice, Customer, MaterialReservation, ScrapRecord,
    Equipment, Worker, PayrollRun,
)
from app.services.production import get_smart_alerts, run_mrp

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def _get_kpis(db: AsyncSession) -> dict:
    today = date.today()
    month_start = today.replace(day=1)
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)

    # 1. أوامر الإنتاج النشطة
    active_wo = (await db.execute(
        select(func.count(WorkOrder.id)).where(
            WorkOrder.status.in_(['in_progress', 'draft'])
        )
    )).scalar() or 0

    # 2. قيمة المخزون (stock_qty × unit_cost)
    stock_value_row = (await db.execute(
        select(func.sum(Material.stock_qty * Material.unit_cost))
    )).scalar()
    stock_value = float(stock_value_row or 0)

    # 3. المبيعات هذا الشهر
    sales_month = (await db.execute(
        select(func.sum(Invoice.total)).where(
            Invoice.date >= month_start,
            Invoice.status != 'cancelled',
        )
    )).scalar()
    sales_month = float(sales_month or 0)

    # مبيعات الشهر السابق (للمقارنة)
    sales_last_month = (await db.execute(
        select(func.sum(Invoice.total)).where(
            Invoice.date >= last_month_start,
            Invoice.date < month_start,
        )
    )).scalar()
    sales_last_month = float(sales_last_month or 1)
    sales_trend = round((sales_month - sales_last_month) / sales_last_month * 100, 1)

    # 4. المستحقات للتحصيل
    receivables = (await db.execute(
        select(func.sum(Invoice.total - Invoice.paid_amount)).where(
            Invoice.status.in_(['unpaid', 'partial'])
        )
    )).scalar()
    receivables = float(receivables or 0)

    # 5. مواد تحتاج طلبية
    low_stock_count = (await db.execute(
        select(func.count(Material.id)).where(
            Material.stock_qty <= Material.reorder_level,
            Material.reorder_level > 0,
        )
    )).scalar() or 0

    # 6. إجمالي العمال النشطين
    active_workers = (await db.execute(
        select(func.count(Worker.id)).where(Worker.is_active == True)
    )).scalar() or 0

    # 7. تكلفة الهالك هذا الشهر
    scrap_cost = (await db.execute(
        select(func.sum(ScrapRecord.total_cost)).where(
            ScrapRecord.recorded_at >= month_start,
        )
    )).scalar()
    scrap_cost = float(scrap_cost or 0)

    # 8. أوامر إنتاج منتهية هذا الشهر
    completed_wo = (await db.execute(
        select(func.count(WorkOrder.id)).where(
            WorkOrder.status == 'done',
            WorkOrder.end_date >= month_start,
        )
    )).scalar() or 0

    return {
        "active_work_orders":   {"value": active_wo,          "label": "أوامر إنتاج نشطة",      "color": "blue",  "icon": "📋"},
        "stock_value":          {"value": stock_value,         "label": "قيمة المخزون",           "color": "teal",  "icon": "📦", "format": "currency"},
        "sales_this_month":     {"value": sales_month,         "label": "مبيعات الشهر",           "color": "green", "icon": "💰", "format": "currency", "trend": sales_trend},
        "receivables":          {"value": receivables,         "label": "مستحقات للتحصيل",        "color": "amber", "icon": "🧾", "format": "currency"},
        "low_stock_items":      {"value": low_stock_count,     "label": "مواد تحتاج طلبية",       "color": "red",   "icon": "⚠️"},
        "active_workers":       {"value": active_workers,      "label": "عدد العمال",              "color": "blue",  "icon": "👷"},
        "scrap_cost_month":     {"value": scrap_cost,          "label": "تكلفة الهالك",           "color": "red",   "icon": "🗑️", "format": "currency"},
        "completed_wo_month":   {"value": completed_wo,        "label": "أوامر منتهية الشهر",    "color": "green", "icon": "✅"},
    }


async def _get_active_work_orders(db: AsyncSession) -> list:
    wos = (await db.execute(
        select(WorkOrder).options(selectinload(WorkOrder.equipment))
        .where(WorkOrder.status.in_(['in_progress', 'draft']))
        .order_by(WorkOrder.start_date).limit(10)
    )).scalars().all()

    result = []
    for wo in wos:
        pct = 0
        if wo.planned_cost and float(wo.planned_cost) > 0:
            pct = min(100, round(float(wo.actual_cost or 0) / float(wo.planned_cost) * 100))

        # عدد الحجوزات المكتملة
        total_res = (await db.execute(
            select(func.count(MaterialReservation.id)).where(
                MaterialReservation.work_order_id == wo.id
            )
        )).scalar() or 0
        issued_res = (await db.execute(
            select(func.count(MaterialReservation.id)).where(
                MaterialReservation.work_order_id == wo.id,
                MaterialReservation.status == 'fully_issued',
            )
        )).scalar() or 0

        result.append({
            "id":            wo.id,
            "code":          wo.code,
            "equipment":     wo.equipment.name_ar if wo.equipment else "—",
            "status":        wo.status,
            "planned_cost":  float(wo.planned_cost or 0),
            "actual_cost":   float(wo.actual_cost or 0),
            "cost_pct":      pct,
            "start_date":    str(wo.start_date) if wo.start_date else None,
            "end_date":      str(wo.end_date) if wo.end_date else None,
            "reservations":  f"{issued_res}/{total_res}",
        })
    return result


async def _get_low_stock(db: AsyncSession) -> list:
    mats = (await db.execute(
        select(Material).where(
            Material.stock_qty <= Material.reorder_level,
            Material.reorder_level > 0,
        ).order_by(Material.stock_qty / (Material.reorder_level + Decimal("0.01")))
        .limit(8)
    )).scalars().all()

    return [{
        "id":            m.id,
        "code":          m.code,
        "name":          m.name_ar,
        "stock_qty":     float(m.stock_qty),
        "reorder_level": float(m.reorder_level),
        "unit":          m.unit,
        "shortage":      float(m.reorder_level - m.stock_qty),
        "pct":           round(float(m.stock_qty) / float(m.reorder_level) * 100) if m.reorder_level > 0 else 0,
        "lead_time":     m.lead_time_days or 7,
        "est_cost":      float((m.reorder_level - m.stock_qty) * (m.last_purchase_price or m.unit_cost)),
    } for m in mats]


async def _get_production_chart(db: AsyncSession) -> dict:
    """إنتاجية آخر 6 أشهر"""
    rows = []
    today = date.today()
    for i in range(5, -1, -1):
        month_date = (today.replace(day=1) - timedelta(days=i * 30)).replace(day=1)
        next_month = (month_date + timedelta(days=32)).replace(day=1)

        count = (await db.execute(
            select(func.count(WorkOrder.id)).where(
                WorkOrder.status == 'done',
                WorkOrder.end_date >= month_date,
                WorkOrder.end_date < next_month,
            )
        )).scalar() or 0

        cost = (await db.execute(
            select(func.sum(WorkOrder.actual_cost)).where(
                WorkOrder.status == 'done',
                WorkOrder.end_date >= month_date,
                WorkOrder.end_date < next_month,
            )
        )).scalar()

        rows.append({
            "month":       month_date.strftime("%Y-%m"),
            "label":       month_date.strftime("%b %Y"),
            "orders":      count,
            "actual_cost": float(cost or 0),
        })
    return {"labels": [r["label"] for r in rows],
            "orders": [r["orders"] for r in rows],
            "costs":  [r["actual_cost"] for r in rows]}


async def _get_cashflow_chart(db: AsyncSession) -> dict:
    """التدفق النقدي آخر 30 يوم"""
    today = date.today()
    rows = []
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)

        inflow = (await db.execute(
            select(func.sum(StockMovement.total_cost)).where(
                StockMovement.movement_type == 'in',
                StockMovement.movement_date == d,
            )
        )).scalar() or 0

        outflow = (await db.execute(
            select(func.sum(StockMovement.total_cost)).where(
                StockMovement.movement_type == 'out',
                StockMovement.movement_date == d,
            )
        )).scalar() or 0

        rows.append({
            "date":    str(d),
            "inflow":  float(inflow),
            "outflow": float(outflow),
        })
    return {
        "labels":  [r["date"] for r in rows],
        "inflow":  [r["inflow"] for r in rows],
        "outflow": [r["outflow"] for r in rows],
    }


async def _get_cost_distribution(db: AsyncSession) -> dict:
    """توزيع التكاليف هذا الشهر"""
    month_start = date.today().replace(day=1)
    rows = (await db.execute(
        select(CostLine.cost_type, func.sum(CostLine.total_cost))
        .where(CostLine.work_order_id.is_not(None))
        .group_by(CostLine.cost_type)
    )).all()

    labels_ar = {"material": "مواد", "labor": "عمالة", "overhead": "غير مباشر"}
    return {
        "labels": [labels_ar.get(r[0], r[0]) for r in rows],
        "values": [float(r[1] or 0) for r in rows],
    }


# ── Main Dashboard Endpoint ────────────────────────────────────

@router.get("/full")
async def get_full_dashboard():
    """كل بيانات الداشبورد في request واحد متوازي — كل مهمة بجلسة مستقلة"""

    async def with_session(fn):
        async with AsyncSessionLocal() as session:
            return await fn(session)

    kpis, alerts, active_wos, low_stock, mrp = await asyncio.gather(
        with_session(_get_kpis),
        with_session(get_smart_alerts),
        with_session(_get_active_work_orders),
        with_session(_get_low_stock),
        with_session(run_mrp),
    )
    return {
        "kpis":         kpis,
        "alerts":       alerts,
        "work_orders":  active_wos,
        "low_stock":    low_stock,
        "mrp":          mrp[:5],  # top 5 فقط في الداشبورد
        "generated_at": date.today().isoformat(),
    }


@router.get("/kpis")
async def get_kpis(db: AsyncSession = Depends(get_db)):
    return await _get_kpis(db)


@router.get("/alerts")
async def get_alerts(db: AsyncSession = Depends(get_db)):
    return await get_smart_alerts(db)


@router.get("/charts/production")
async def production_chart(db: AsyncSession = Depends(get_db)):
    return await _get_production_chart(db)


@router.get("/charts/cashflow")
async def cashflow_chart(db: AsyncSession = Depends(get_db)):
    return await _get_cashflow_chart(db)


@router.get("/charts/costs")
async def cost_chart(db: AsyncSession = Depends(get_db)):
    return await _get_cost_distribution(db)
