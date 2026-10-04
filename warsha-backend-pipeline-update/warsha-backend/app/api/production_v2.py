"""
Production V2 — جدولة الإنتاج + ضبط الجودة + ربط طلبات البيع بالإنتاج
"""
from datetime import date
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, validator, Field

from app.core.database import get_db
from app.api.auth import require_any, require_admin_production
from app.models.models import (
    ProductionSchedule, QualityInspection, SalesOrderWorkOrder,
    WorkOrder, CustomerOrder, Worker, Material,
)

router = APIRouter(prefix="/production/v2", tags=["production_v2"], dependencies=[Depends(require_any)])


# ── Schemas ────────────────────────────────────────────────────

class ScheduleIn(BaseModel):
    work_order_id: int
    customer_order_id: Optional[int] = None
    scheduled_start: date
    scheduled_end: date
    priority: int = Field(5, ge=1, le=10)
    notes: Optional[str] = None

    @validator("scheduled_end")
    def end_after_start(cls, end, values):
        if "scheduled_start" in values and end < values["scheduled_start"]:
            raise ValueError("تاريخ الانتهاء يجب أن يكون بعد تاريخ البدء")
        return end


class ScheduleUpdateIn(BaseModel):
    scheduled_start: Optional[date] = None
    scheduled_end: Optional[date] = None
    actual_start: Optional[date] = None
    actual_end: Optional[date] = None
    priority: Optional[int] = Field(None, ge=1, le=10)
    status: Optional[str] = None
    notes: Optional[str] = None

    @validator("status")
    def _status_valid(cls, v):
        if v is not None and v not in SCHEDULE_STATUSES:
            raise ValueError(f"حالة الجدولة غير صالحة — المسموح: {', '.join(SCHEDULE_STATUSES)}")
        return v


class QCIn(BaseModel):
    work_order_id: Optional[int] = None
    material_id: Optional[int] = None
    inspection_date: date
    inspector_id: Optional[int] = None
    inspection_type: str = "final"   # incoming | in_process | final | outgoing
    qty_inspected: Decimal = Field(..., gt=0)
    qty_passed: Decimal = Field(..., ge=0)
    qty_failed: Decimal = Field(Decimal("0"), ge=0)
    qty_rework: Decimal = Field(Decimal("0"), ge=0)
    failure_reason: Optional[str] = None
    corrective_action: Optional[str] = None
    notes: Optional[str] = None

    @validator("inspection_type")
    def _type_valid(cls, v):
        if v not in QC_TYPES:
            raise ValueError(f"نوع الفحص غير صالح — المسموح: {', '.join(QC_TYPES)}")
        return v

    @validator("qty_rework")
    def totals_match_all(cls, rework, values):
        # validator("qty_passed") بيتنفذ قبل ما qty_failed/qty_rework يتقروا
        # (ترتيب الحقول)، فكان بيقارن passed لوحده بالمفحوص — الفحص الكامل هنا
        inspected = values.get("qty_inspected")
        passed = values.get("qty_passed")
        failed = values.get("qty_failed")
        if inspected is not None and passed is not None and failed is not None:
            if passed + failed + rework > inspected + Decimal("0.001"):
                raise ValueError("مجموع القبول+الرفض+الإعادة لا يجب أن يتجاوز الكمية المفحوصة")
        return rework

    @validator("qty_passed")
    def totals_match(cls, passed, values):
        inspected = values.get("qty_inspected", Decimal("0"))
        failed = values.get("qty_failed", Decimal("0"))
        rework = values.get("qty_rework", Decimal("0"))
        if passed + failed + rework > inspected + Decimal("0.001"):
            raise ValueError("مجموع القبول+الرفض+الإعادة لا يجب أن يتجاوز الكمية المفحوصة")
        return passed


SCHEDULE_STATUSES = ("planned", "in_progress", "completed", "delayed", "cancelled")
QC_TYPES = ("incoming", "in_process", "final", "outgoing")


class LinkOrderIn(BaseModel):
    customer_order_id: int
    work_order_id: int
    notes: Optional[str] = Field(None, max_length=300)


# ══════════════════════════════════════════════════════════════
# PRODUCTION SCHEDULING — جدولة الإنتاج
# ══════════════════════════════════════════════════════════════

def sched_dict(s: ProductionSchedule) -> dict:
    status_ar = {
        "planned": "مخطط", "in_progress": "جاري",
        "completed": "منتهي", "delayed": "متأخر", "cancelled": "ملغي",
    }
    return {
        "id": s.id,
        "work_order_id": s.work_order_id,
        "work_order_code": s.work_order.code if s.work_order else None,
        "customer_order_id": s.customer_order_id,
        "scheduled_start": str(s.scheduled_start),
        "scheduled_end": str(s.scheduled_end),
        "actual_start": str(s.actual_start) if s.actual_start else None,
        "actual_end": str(s.actual_end) if s.actual_end else None,
        "priority": s.priority,
        "status": s.status,
        "status_ar": status_ar.get(s.status, s.status),
        "notes": s.notes,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        # تأخر؟
        "is_delayed": (
            s.status == "in_progress" and date.today() > s.scheduled_end
        ),
        "delay_days": (
            (date.today() - s.scheduled_end).days
            if s.status == "in_progress" and date.today() > s.scheduled_end
            else 0
        ),
    }


@router.get("/schedules")
async def list_schedules(
    status: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(ProductionSchedule).options(
        selectinload(ProductionSchedule.work_order),
        selectinload(ProductionSchedule.customer_order),
    )
    if status:
        q = q.where(ProductionSchedule.status == status)
    if from_date:
        q = q.where(ProductionSchedule.scheduled_start >= from_date)
    if to_date:
        q = q.where(ProductionSchedule.scheduled_end <= to_date)
    res = await db.execute(q.order_by(ProductionSchedule.priority, ProductionSchedule.scheduled_start))
    return [sched_dict(s) for s in res.scalars().all()]


@router.post("/schedules", status_code=201, dependencies=[Depends(require_admin_production)])
async def create_schedule(data: ScheduleIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1

    # التحقق من وجود أمر الإنتاج
    wo = await db.get(WorkOrder, data.work_order_id)
    if not wo:
        raise HTTPException(404, "أمر الإنتاج غير موجود")
    if wo.status == "cancelled":
        raise HTTPException(400, "أمر الإنتاج ملغي — لا يمكن جدولته")
    if data.customer_order_id is not None:
        # طلب البيع لازم يكون تابع لنفس الورشة (db.get بيمر بفلتر الـtenant)
        if not await db.get(CustomerOrder, data.customer_order_id):
            raise HTTPException(404, "طلب البيع غير موجود")

    # التحقق من عدم التداخل — نفس أمر الإنتاج لا يُجدوَل مرتين في نفس الفترة
    existing = await db.scalar(
        select(func.count()).select_from(ProductionSchedule).where(
            ProductionSchedule.work_order_id == data.work_order_id,
            ProductionSchedule.status.not_in(["completed", "cancelled"]),
        )
    )
    if existing and existing > 0:
        raise HTTPException(400, "أمر الإنتاج مجدول بالفعل ولم يُغلق بعد")

    sched = ProductionSchedule(
        tenant_id=tid,
        work_order_id=data.work_order_id,
        customer_order_id=data.customer_order_id,
        scheduled_start=data.scheduled_start,
        scheduled_end=data.scheduled_end,
        priority=data.priority,
        notes=data.notes,
        status="planned",
    )
    db.add(sched)
    await db.commit()
    await db.refresh(sched)

    res = await db.execute(
        select(ProductionSchedule)
        .options(selectinload(ProductionSchedule.work_order))
        .where(ProductionSchedule.id == sched.id)
    )
    return sched_dict(res.scalar_one())


@router.patch("/schedules/{sched_id}", dependencies=[Depends(require_admin_production)])
async def update_schedule(sched_id: int, data: ScheduleUpdateIn, db: AsyncSession = Depends(get_db)):
    sched = await db.get(ProductionSchedule, sched_id)
    if not sched:
        raise HTTPException(404)
    _upd = data.model_dump(exclude_none=True)
    _new_start = _upd.get("scheduled_start", sched.scheduled_start)
    _new_end = _upd.get("scheduled_end", sched.scheduled_end)
    if _new_start and _new_end and _new_end < _new_start:
        raise HTTPException(400, "تاريخ الانتهاء يجب أن يكون بعد تاريخ البدء")
    _new_as = _upd.get("actual_start", sched.actual_start)
    _new_ae = _upd.get("actual_end", sched.actual_end)
    if _new_as and _new_ae and _new_ae < _new_as:
        raise HTTPException(400, "تاريخ الانتهاء الفعلي يجب أن يكون بعد تاريخ البدء الفعلي")
    for k, v in _upd.items():
        setattr(sched, k, v)
    await db.commit()
    res = await db.execute(
        select(ProductionSchedule)
        .options(selectinload(ProductionSchedule.work_order))
        .where(ProductionSchedule.id == sched_id)
    )
    return sched_dict(res.scalar_one())


@router.patch("/schedules/{sched_id}/start", dependencies=[Depends(require_admin_production)])
async def start_schedule(sched_id: int, db: AsyncSession = Depends(get_db)):
    sched = await db.get(ProductionSchedule, sched_id)
    if not sched:
        raise HTTPException(404)
    if sched.status not in ("planned", "delayed"):
        raise HTTPException(400, f"لا يمكن بدء جدولة بحالة '{sched.status}'")
    _wo_chk = await db.get(WorkOrder, sched.work_order_id)
    if _wo_chk and _wo_chk.status == "cancelled":
        raise HTTPException(400, "أمر الإنتاج المرتبط ملغي — لا يمكن بدء الجدولة")
    sched.status = "in_progress"
    sched.actual_start = date.today()
    # تحديث حالة أمر الإنتاج تلقائياً
    wo = await db.get(WorkOrder, sched.work_order_id)
    if wo and wo.status == "draft":
        wo.status = "in_progress"
    await db.commit()
    return {"id": sched_id, "status": "in_progress", "actual_start": str(date.today())}


@router.patch("/schedules/{sched_id}/complete", dependencies=[Depends(require_admin_production)])
async def complete_schedule(sched_id: int, db: AsyncSession = Depends(get_db)):
    sched = await db.get(ProductionSchedule, sched_id)
    if not sched:
        raise HTTPException(404)
    if sched.status in ("completed", "cancelled"):
        raise HTTPException(400, f"لا يمكن إنهاء جدولة بحالة '{sched.status}'")
    sched.status = "completed"
    if not sched.actual_start:
        sched.actual_start = date.today()
    sched.actual_end = date.today()
    await db.commit()
    return {"id": sched_id, "status": "completed", "actual_end": str(date.today())}


@router.get("/schedules/gantt")
async def gantt_data(db: AsyncSession = Depends(get_db)):
    """بيانات Gantt لجدولة الإنتاج"""
    res = await db.execute(
        select(ProductionSchedule)
        .options(selectinload(ProductionSchedule.work_order))
        .where(ProductionSchedule.status.not_in(["cancelled"]))
        .order_by(ProductionSchedule.scheduled_start)
    )
    items = res.scalars().all()
    return [{
        "id": s.id,
        "label": s.work_order.code if s.work_order else f"WO-{s.work_order_id}",
        "start": str(s.scheduled_start),
        "end": str(s.scheduled_end),
        "actual_start": str(s.actual_start) if s.actual_start else None,
        "actual_end": str(s.actual_end) if s.actual_end else None,
        "status": s.status,
        "priority": s.priority,
        "is_delayed": date.today() > s.scheduled_end and s.status == "in_progress",
    } for s in items]


# ══════════════════════════════════════════════════════════════
# QUALITY INSPECTIONS — ضبط الجودة
# ══════════════════════════════════════════════════════════════

def qc_dict(q: QualityInspection) -> dict:
    result_ar = {"pass": "ناجح", "fail": "مرفوض", "conditional": "مشروط"}
    type_ar = {
        "incoming": "استلام مواد", "in_process": "أثناء الإنتاج",
        "final": "فحص نهائي", "outgoing": "فحص تسليم",
    }
    return {
        "id": q.id,
        "work_order_id": q.work_order_id,
        "material_id": q.material_id,
        "inspection_date": str(q.inspection_date),
        "inspector_id": q.inspector_id,
        "inspector_name": q.inspector.name if q.inspector else None,
        "inspection_type": q.inspection_type,
        "inspection_type_ar": type_ar.get(q.inspection_type, q.inspection_type),
        "qty_inspected": float(q.qty_inspected),
        "qty_passed": float(q.qty_passed),
        "qty_failed": float(q.qty_failed),
        "qty_rework": float(q.qty_rework),
        "pass_rate": round(float(q.qty_passed) / float(q.qty_inspected) * 100, 1) if q.qty_inspected else 0,
        "result": q.result,
        "result_ar": result_ar.get(q.result, q.result),
        "failure_reason": q.failure_reason,
        "corrective_action": q.corrective_action,
        "notes": q.notes,
        "created_at": q.created_at.isoformat() if q.created_at else None,
    }


async def _validate_qc_refs(db: AsyncSession, data: "QCIn") -> None:
    """كل المراجع (أمر إنتاج/مادة/مفتش) لازم تكون موجودة وتابعة لنفس الورشة."""
    if data.work_order_id is not None and not await db.get(WorkOrder, data.work_order_id):
        raise HTTPException(404, "أمر الإنتاج غير موجود")
    if data.material_id is not None and not await db.get(Material, data.material_id):
        raise HTTPException(404, "المادة غير موجودة")
    if data.inspector_id is not None and not await db.get(Worker, data.inspector_id):
        raise HTTPException(404, "المفتش (العامل) غير موجود")


@router.get("/quality")
async def list_inspections(
    work_order_id: Optional[int] = None,
    result: Optional[str] = None,
    inspection_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(QualityInspection).options(selectinload(QualityInspection.inspector))
    if work_order_id:
        q = q.where(QualityInspection.work_order_id == work_order_id)
    if result:
        q = q.where(QualityInspection.result == result)
    if inspection_type:
        q = q.where(QualityInspection.inspection_type == inspection_type)
    res = await db.execute(q.order_by(QualityInspection.inspection_date.desc()))
    return [qc_dict(qi) for qi in res.scalars().all()]


@router.post("/quality", status_code=201, dependencies=[Depends(require_admin_production)])
async def create_inspection(data: QCIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1

    await _validate_qc_refs(db, data)

    # تحديد النتيجة تلقائياً
    if data.qty_failed == 0 and data.qty_rework == 0:
        result = "pass"
    elif data.qty_passed == 0:
        result = "fail"
    else:
        result = "conditional"

    insp = QualityInspection(
        tenant_id=tid,
        work_order_id=data.work_order_id,
        material_id=data.material_id,
        inspection_date=data.inspection_date,
        inspector_id=data.inspector_id,
        inspection_type=data.inspection_type,
        qty_inspected=data.qty_inspected,
        qty_passed=data.qty_passed,
        qty_failed=data.qty_failed,
        qty_rework=data.qty_rework,
        result=result,
        failure_reason=data.failure_reason,
        corrective_action=data.corrective_action,
        notes=data.notes,
    )
    db.add(insp)
    await db.flush()

    # إذا الفحص فاشل أو مشروط → إنشاء أمر إعادة عمل تلقائياً
    rework_wo_id = None
    rework_wo_code = None
    if result in ("fail", "conditional") and insp.work_order_id:
        from app.models.models import WorkOrder as _WO
        from sqlalchemy import func as _func
        _parent_wo = await db.get(_WO, insp.work_order_id)
        if _parent_wo:
            _count = await db.scalar(select(_func.count()).select_from(_WO))
            _rw_n = (_count or 0) + 1
            # كود أمر الإنتاج فريد لكل ورشة (uq_workorder_tenant_code) — العد
            # وحده كان بيتصادم مع كود موجود بعد أي حذف (IntegrityError 500)
            while True:
                _rw_code = f"WO-RW-{_rw_n:04d}"
                if not await db.scalar(select(_WO.id).where(_WO.tenant_id == tid, _WO.code == _rw_code)):
                    break
                _rw_n += 1
            _rw = _WO(
                tenant_id=tid,
                code=_rw_code,
                equipment_id=_parent_wo.equipment_id,
                customer_order_id=_parent_wo.customer_order_id,
                status="draft",
                planned_cost=_parent_wo.planned_cost,
                notes=f"إعادة عمل من فحص جودة #{insp.id} — نتيجة: {result} — {data.failure_reason or ''}",
            )
            db.add(_rw)
            await db.flush()
            rework_wo_id = _rw.id
            rework_wo_code = _rw_code

    await db.commit()
    await db.refresh(insp)

    res = await db.execute(
        select(QualityInspection)
        .options(selectinload(QualityInspection.inspector))
        .where(QualityInspection.id == insp.id)
    )
    result_dict = qc_dict(res.scalar_one())
    if rework_wo_id:
        result_dict["rework_work_order"] = {"id": rework_wo_id, "code": rework_wo_code}
    return result_dict


@router.put("/quality/{insp_id}", dependencies=[Depends(require_admin_production)])
async def update_inspection(insp_id: int, data: QCIn, db: AsyncSession = Depends(get_db)):
    insp = await db.get(QualityInspection, insp_id)
    if not insp:
        raise HTTPException(404)
    await _validate_qc_refs(db, data)
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(insp, k, v)
    # إعادة حساب النتيجة
    if insp.qty_failed == 0 and insp.qty_rework == 0:
        insp.result = "pass"
    elif insp.qty_passed == 0:
        insp.result = "fail"
    else:
        insp.result = "conditional"
    await db.commit()
    return {"id": insp_id, "result": insp.result}


@router.delete("/quality/{insp_id}", dependencies=[Depends(require_admin_production)])
async def delete_inspection(insp_id: int, db: AsyncSession = Depends(get_db)):
    insp = await db.get(QualityInspection, insp_id)
    if not insp:
        raise HTTPException(404)
    await db.delete(insp)
    await db.commit()
    return {"message": "تم الحذف", "id": insp_id}


@router.get("/quality/dashboard")
async def qc_dashboard(db: AsyncSession = Depends(get_db)):
    total = await db.scalar(select(func.count()).select_from(QualityInspection))
    passed = await db.scalar(select(func.count()).select_from(QualityInspection).where(QualityInspection.result == "pass"))
    failed = await db.scalar(select(func.count()).select_from(QualityInspection).where(QualityInspection.result == "fail"))
    total_inspected = await db.scalar(select(func.coalesce(func.sum(QualityInspection.qty_inspected), 0)))
    total_passed = await db.scalar(select(func.coalesce(func.sum(QualityInspection.qty_passed), 0)))
    return {
        "total_inspections": total or 0,
        "passed": passed or 0,
        "failed": failed or 0,
        "pass_rate_pct": round(float(passed or 0) / float(total or 1) * 100, 1),
        "total_qty_inspected": float(total_inspected or 0),
        "total_qty_passed": float(total_passed or 0),
        "qty_pass_rate": round(float(total_passed or 0) / float(total_inspected or 1) * 100, 1),
    }


# ══════════════════════════════════════════════════════════════
# SALES ORDER → WORK ORDER LINK
# ══════════════════════════════════════════════════════════════

@router.post("/link-order", status_code=201, dependencies=[Depends(require_admin_production)])
async def link_sales_to_production(data: LinkOrderIn, db: AsyncSession = Depends(get_db)):
    """ربط طلب بيع بأمر إنتاج"""
    co = await db.get(CustomerOrder, data.customer_order_id)
    if not co:
        raise HTTPException(404, "طلب البيع غير موجود")
    wo = await db.get(WorkOrder, data.work_order_id)
    if not wo:
        raise HTTPException(404, "أمر الإنتاج غير موجود")

    # التحقق من عدم التكرار
    exists = await db.scalar(
        select(func.count()).select_from(SalesOrderWorkOrder).where(
            SalesOrderWorkOrder.customer_order_id == data.customer_order_id,
            SalesOrderWorkOrder.work_order_id == data.work_order_id,
        )
    )
    if exists:
        raise HTTPException(400, "الربط موجود بالفعل")

    link = SalesOrderWorkOrder(
        customer_order_id=data.customer_order_id,
        work_order_id=data.work_order_id,
        notes=data.notes,
    )
    db.add(link)
    await db.commit()
    return {
        "id": link.id,
        "customer_order_id": data.customer_order_id,
        "work_order_id": data.work_order_id,
        "message": "تم الربط بنجاح",
    }


@router.get("/link-order/{customer_order_id}")
async def get_linked_work_orders(customer_order_id: int, db: AsyncSession = Depends(get_db)):
    """جلب كل أوامر الإنتاج المرتبطة بطلب بيع"""
    # SalesOrderWorkOrder مالوش عمود tenant_id (فالفلتر التلقائي مش بيغطيه) —
    # لازم نتأكد إن طلب البيع نفسه تابع للورشة الحالية قبل عرض أي روابط
    if not await db.get(CustomerOrder, customer_order_id):
        raise HTTPException(404, "طلب البيع غير موجود")
    res = await db.execute(
        select(SalesOrderWorkOrder)
        .options(selectinload(SalesOrderWorkOrder.work_order))
        .where(SalesOrderWorkOrder.customer_order_id == customer_order_id)
    )
    items = res.scalars().all()
    return [{
        "link_id": i.id,
        "work_order_id": i.work_order_id,
        "work_order_code": i.work_order.code if i.work_order else None,
        "work_order_status": i.work_order.status if i.work_order else None,
        "notes": i.notes,
    } for i in items]


@router.delete("/link-order/{link_id}", dependencies=[Depends(require_admin_production)])
async def unlink_order(link_id: int, db: AsyncSession = Depends(get_db)):
    link = await db.get(SalesOrderWorkOrder, link_id)
    if not link:
        raise HTTPException(404)
    # SalesOrderWorkOrder بلا tenant_id — التحقق من ملكية الورشة عبر أمر الإنتاج
    # وطلب البيع (الاتنين بيمروا بفلتر الـtenant التلقائي)، وإلا أي مستخدم
    # كان يقدر يمسح روابط ورشة تانية بمجرد تخمين الـid
    if not await db.get(WorkOrder, link.work_order_id) or not await db.get(CustomerOrder, link.customer_order_id):
        raise HTTPException(404)
    await db.delete(link)
    await db.commit()
    return {"message": "تم إزالة الربط", "id": link_id}
