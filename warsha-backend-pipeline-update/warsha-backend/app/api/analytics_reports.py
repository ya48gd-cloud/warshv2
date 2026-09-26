"""
Analytics Reports API — تقارير مجمّعة فوق سجل أحداث المنتج (analytics_events).

الهدف: توفير نقطة وصول جاهزة لبناء لوحة تحكم أو تقرير Power BI/Python
لاحقًا، من غير ما تحتاج تكتب SQL يدوي كل مرة. مقصورة على tenant الحالي
(كل عميل يشوف بياناته بس) بخلاف endpoint الملخص العام المخصص لصاحب
النظام (super admin) للمقارنة بين كل العملاء.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, current_tenant_id
from app.api.auth import require_any
from app.models.models import AnalyticsEvent

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/summary")
async def event_summary(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _current=Depends(require_any),
):
    """عدد كل نوع حدث خلال آخر N يوم — لتينانت الحالي فقط."""
    since = datetime.utcnow() - timedelta(days=days)
    tid = current_tenant_id.get()
    q = (
        select(AnalyticsEvent.event_type, func.count().label("count"))
        .where(AnalyticsEvent.created_at >= since)
        .group_by(AnalyticsEvent.event_type)
        .order_by(func.count().desc())
    )
    if tid is not None:
        q = q.where(AnalyticsEvent.tenant_id == tid)
    rows = (await db.execute(q)).all()
    return {"since": since.isoformat(), "events": [{"event_type": r[0], "count": r[1]} for r in rows]}


@router.get("/funnel")
async def sales_funnel(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _current=Depends(require_any),
):
    """
    قمع التحويل الأساسي: عرض سعر ← طلب ← أمر إنتاج ← فاتورة ← دفعة.
    مفيد لمعرفة نسبة تحول العروض لفواتير فعلية.
    """
    since = datetime.utcnow() - timedelta(days=days)
    tid = current_tenant_id.get()
    steps = ["quotation_created", "order_created", "work_order_created", "invoice_created", "payment_received"]
    result = {}
    for step in steps:
        q = select(func.count()).select_from(AnalyticsEvent).where(
            AnalyticsEvent.event_type == step, AnalyticsEvent.created_at >= since
        )
        if tid is not None:
            q = q.where(AnalyticsEvent.tenant_id == tid)
        result[step] = await db.scalar(q) or 0
    return {"since": since.isoformat(), "funnel": result}


@router.get("/active-users")
async def active_users(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _current=Depends(require_any),
):
    """عدد المستخدمين المختلفين اللي عملوا login خلال آخر N يوم (DAU/MAU-style)."""
    since = datetime.utcnow() - timedelta(days=days)
    tid = current_tenant_id.get()
    q = select(func.count(func.distinct(AnalyticsEvent.user_id))).where(
        AnalyticsEvent.event_type == "user_login", AnalyticsEvent.created_at >= since
    )
    if tid is not None:
        q = q.where(AnalyticsEvent.tenant_id == tid)
    count = await db.scalar(q) or 0
    return {"since": since.isoformat(), "active_users": count}
