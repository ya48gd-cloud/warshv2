"""
Work Order Stages API — مراحل تنفيذ أوامر الإنتاج
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import WorkOrderStage, WorkOrder, Worker

router = APIRouter(prefix="/production/stages", tags=["work_order_stages"], dependencies=[Depends(require_any)])

# المراحل الافتراضية للمعدات
DEFAULT_STAGES = [
    (1, "قطع وتشكيل"),
    (2, "لحام"),
    (3, "تجميع"),
    (4, "طلاء وتشطيب"),
    (5, "اختبار وضبط الجودة"),
    (6, "تغليف وتسليم"),
]


class StageIn(BaseModel):
    stage_name: str
    stage_order: int = 0
    assigned_worker_id: Optional[int] = None
    planned_hours: Optional[Decimal] = None
    notes: Optional[str] = None


class StageUpdateIn(BaseModel):
    stage_name: Optional[str] = None
    status: Optional[str] = None
    assigned_worker_id: Optional[int] = None
    planned_hours: Optional[Decimal] = None
    actual_hours: Optional[Decimal] = None
    notes: Optional[str] = None


def stage_dict(s: WorkOrderStage) -> dict:
    status_ar = {
        "pending": "في الانتظار", "in_progress": "جارٍ",
        "done": "منتهي", "skipped": "متجاوز"
    }
    duration = None
    if s.started_at and s.completed_at:
        duration = round((s.completed_at - s.started_at).total_seconds() / 3600, 2)
    return {
        "id": s.id,
        "work_order_id": s.work_order_id,
        "stage_name": s.stage_name,
        "stage_order": s.stage_order,
        "status": s.status,
        "status_ar": status_ar.get(s.status, s.status),
        "assigned_worker_id": s.assigned_worker_id,
        "assigned_worker_name": s.assigned_worker.name if s.assigned_worker else None,
        "planned_hours": float(s.planned_hours) if s.planned_hours is not None else None,
        "actual_hours": float(s.actual_hours) if s.actual_hours is not None else None,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        "duration_hours": duration,
        "notes": s.notes,
    }


@router.get("/{work_order_id}")
async def list_stages(work_order_id: int, db: AsyncSession = Depends(get_db)):
    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        raise HTTPException(404, "أمر الإنتاج غير موجود")
    res = await db.execute(
        select(WorkOrderStage)
        .options(selectinload(WorkOrderStage.assigned_worker))
        .where(WorkOrderStage.work_order_id == work_order_id)
        .order_by(WorkOrderStage.stage_order)
    )
    stages = res.scalars().all()
    completion_pct = 0
    if stages:
        done = sum(1 for s in stages if s.status == "done")
        completion_pct = round(done / len(stages) * 100)
    return {
        "work_order_id": work_order_id,
        "work_order_code": wo.code,
        "completion_pct": completion_pct,
        "stages": [stage_dict(s) for s in stages],
    }


@router.post("/{work_order_id}/seed")
async def seed_default_stages(work_order_id: int, db: AsyncSession = Depends(get_db)):
    """إنشاء المراحل الافتراضية لأمر الإنتاج"""
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1

    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        raise HTTPException(404)

    existing = await db.scalar(
        select(func.count()).select_from(WorkOrderStage).where(
            WorkOrderStage.work_order_id == work_order_id
        )
    )
    if existing and existing > 0:
        raise HTTPException(400, "المراحل موجودة بالفعل")

    for order, name in DEFAULT_STAGES:
        db.add(WorkOrderStage(
            tenant_id=tid, work_order_id=work_order_id,
            stage_name=name, stage_order=order, status="pending"
        ))
    await db.commit()
    return {"work_order_id": work_order_id, "stages_created": len(DEFAULT_STAGES)}


@router.post("/{work_order_id}", status_code=201)
async def create_stage(work_order_id: int, data: StageIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1
    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        raise HTTPException(404)
    stage = WorkOrderStage(
        tenant_id=tid, work_order_id=work_order_id,
        **data.model_dump()
    )
    db.add(stage)
    await db.commit()
    await db.refresh(stage)
    res = await db.execute(
        select(WorkOrderStage).options(selectinload(WorkOrderStage.assigned_worker))
        .where(WorkOrderStage.id == stage.id)
    )
    return stage_dict(res.scalar_one())


@router.patch("/{stage_id}/start")
async def start_stage(stage_id: int, db: AsyncSession = Depends(get_db)):
    s = await db.get(WorkOrderStage, stage_id)
    if not s:
        raise HTTPException(404)
    if s.status not in ("pending",):
        raise HTTPException(400, f"لا يمكن بدء مرحلة بحالة '{s.status}'")

    # تحقّق من ترتيب المراحل — لا يمكن بدء مرحلة قبل انتهاء (أو تخطّي) كل ما يسبقها
    earlier_pending = (await db.execute(
        select(WorkOrderStage).where(
            WorkOrderStage.work_order_id == s.work_order_id,
            WorkOrderStage.stage_order < s.stage_order,
            WorkOrderStage.status.not_in(("done", "skipped")),
        )
    )).scalars().all()
    if earlier_pending:
        names = "، ".join(st.stage_name for st in earlier_pending)
        raise HTTPException(400, f"لا يمكن بدء هذه المرحلة قبل إنهاء أو تخطّي: {names}")

    s.status = "in_progress"
    s.started_at = datetime.utcnow()
    # تحديث أمر العمل إلى in_progress تلقائياً
    wo = await db.get(WorkOrder, s.work_order_id)
    if wo and wo.status == "draft":
        wo.status = "in_progress"
    await db.commit()
    return {"id": stage_id, "status": "in_progress", "started_at": s.started_at.isoformat()}


@router.patch("/{stage_id}/complete")
async def complete_stage(stage_id: int, actual_hours: Optional[float] = None, db: AsyncSession = Depends(get_db)):
    s = await db.get(WorkOrderStage, stage_id)
    if not s:
        raise HTTPException(404)
    if s.status != "in_progress":
        raise HTTPException(400, f"لا يمكن إنهاء مرحلة لم تُبدَأ بعد (الحالة الحالية: '{s.status}')")
    s.status = "done"
    s.completed_at = datetime.utcnow()
    if actual_hours is not None:
        s.actual_hours = Decimal(str(actual_hours))
    elif s.started_at:
        hrs = (datetime.utcnow() - s.started_at).total_seconds() / 3600
        s.actual_hours = Decimal(str(round(hrs, 2)))

    # هل كل المراحل انتهت؟ → أمر العمل done
    wo = await db.get(WorkOrder, s.work_order_id)
    if wo:
        all_stages = (await db.execute(
            select(WorkOrderStage).where(WorkOrderStage.work_order_id == s.work_order_id)
        )).scalars().all()
        # عد المراحل التي لم تنته بعد (غير المرحلة الحالية)
        pending = [st for st in all_stages if st.id != stage_id and st.status not in ("done", "skipped")]
        if not pending:
            wo.status = "done"

    await db.commit()
    return {"id": stage_id, "status": "done", "actual_hours": float(s.actual_hours or 0)}


@router.patch("/{stage_id}/skip")
async def skip_stage(stage_id: int, reason: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    s = await db.get(WorkOrderStage, stage_id)
    if not s:
        raise HTTPException(404)
    s.status = "skipped"
    if reason:
        s.notes = reason
    await db.commit()
    return {"id": stage_id, "status": "skipped"}


@router.put("/{stage_id}")
async def update_stage(stage_id: int, data: StageUpdateIn, db: AsyncSession = Depends(get_db)):
    s = await db.get(WorkOrderStage, stage_id)
    if not s:
        raise HTTPException(404)
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(s, k, v)
    await db.commit()
    res = await db.execute(
        select(WorkOrderStage).options(selectinload(WorkOrderStage.assigned_worker))
        .where(WorkOrderStage.id == stage_id)
    )
    return stage_dict(res.scalar_one())


@router.delete("/{stage_id}")
async def delete_stage(stage_id: int, db: AsyncSession = Depends(get_db)):
    s = await db.get(WorkOrderStage, stage_id)
    if not s:
        raise HTTPException(404)
    await db.delete(s)
    await db.commit()
    return {"message": "تم الحذف", "id": stage_id}


@router.get("/dashboard/overview")
async def stages_dashboard(db: AsyncSession = Depends(get_db)):
    total = await db.scalar(select(func.count()).select_from(WorkOrderStage))
    pending = await db.scalar(select(func.count()).select_from(WorkOrderStage).where(WorkOrderStage.status == "pending"))
    in_prog = await db.scalar(select(func.count()).select_from(WorkOrderStage).where(WorkOrderStage.status == "in_progress"))
    done = await db.scalar(select(func.count()).select_from(WorkOrderStage).where(WorkOrderStage.status == "done"))
    avg_hours = await db.scalar(select(func.avg(WorkOrderStage.actual_hours)).where(WorkOrderStage.actual_hours.is_not(None)))
    return {
        "total_stages": total or 0,
        "pending": pending or 0,
        "in_progress": in_prog or 0,
        "done": done or 0,
        "avg_actual_hours": round(float(avg_hours or 0), 2),
    }
