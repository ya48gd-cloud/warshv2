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
from pydantic import BaseModel, Field, validator

from app.core.database import get_db
from app.api.auth import require_any, require_admin_production
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


STAGE_STATUSES = ("pending", "in_progress", "done", "skipped")


class StageIn(BaseModel):
    stage_name: str = Field(..., min_length=1, max_length=100)
    stage_order: int = Field(0, ge=0)
    assigned_worker_id: Optional[int] = None
    planned_hours: Optional[Decimal] = Field(None, ge=0, le=9999)
    notes: Optional[str] = None


class StageUpdateIn(BaseModel):
    stage_name: Optional[str] = Field(None, min_length=1, max_length=100)
    status: Optional[str] = None
    assigned_worker_id: Optional[int] = None
    planned_hours: Optional[Decimal] = Field(None, ge=0, le=9999)
    actual_hours: Optional[Decimal] = Field(None, ge=0, le=9999)
    notes: Optional[str] = None

    @validator("status")
    def _status_valid(cls, v):
        if v is not None and v not in STAGE_STATUSES:
            raise ValueError(f"حالة المرحلة غير صالحة — المسموح: {', '.join(STAGE_STATUSES)}")
        return v


async def _check_worker(db: AsyncSession, worker_id: Optional[int]) -> None:
    """العامل المعيَّن لازم يكون موجود وتابع لنفس الورشة (db.get بيمر بفلتر الـtenant)."""
    if worker_id is not None and not await db.get(Worker, worker_id):
        raise HTTPException(404, "العامل غير موجود")


def _check_wo_open(wo: WorkOrder) -> None:
    if wo.status == "cancelled":
        raise HTTPException(400, f"أمر الإنتاج {wo.code} ملغي — لا يمكن تعديل مراحله")


async def _maybe_finalize_work_order(db: AsyncSession, work_order_id: int) -> None:
    """
    محطة 29 (تصليح جذري): لو كل مراحل أمر الإنتاج بقت "منتهية" أو "متخطاة"،
    كان الكود القديم بيكتفي بتغيير wo.status = "done" هنا مباشرة — من غير
    ما يمر على complete_work_order() (services/production.py). النتيجة:
    أمر إنتاج بيوصل لحالة "done" (تظهر كده في كل الشاشات) لكن من غير أي
    ترحيل حقيقي WIP→FG (مادة FG-{wo.code})، من غير إغلاق الحجوزات
    المفتوحة، من غير إضافة تكلفة العمالة الفعلية، ومن غير قيد محاسبي.

    وده بقى فجوة حرجة تحديدًا دلوقتي: فوترة أمر الإنتاج مباشرة
    (sales.py work_order_to_invoice) بقت تعتمد كليًا على وجود FG-{code}
    برصيد كافٍ — أمر إنتاج خلّص عن طريق المراحل (مش زرار "إنهاء" اليدوي)
    كان هيفضل عالق: "done" في الاسم بس مستحيل تتفوتر عليه أبدًا.

    الحل: أي مسار بيوصّل كل المراحل لحالة نهائية (إنهاء/تخطي/تعديل مباشر
    عبر PUT) بيستدعي نفس complete_work_order() اللي بيستخدمها زرار
    "إنهاء أمر الإنتاج" اليدوي بالظبط — نفس الأثر الكامل مهما كان طريق
    الوصول لحالة "كل المراحل خلصت".
    """
    wo = await db.get(WorkOrder, work_order_id)
    if not wo or wo.status == "done":
        return
    all_stages = (await db.execute(
        select(WorkOrderStage).where(WorkOrderStage.work_order_id == work_order_id)
    )).scalars().all()
    if not all_stages:
        return
    if all(st.status in ("done", "skipped") for st in all_stages):
        from app.services.production import complete_work_order
        await complete_work_order(db, work_order_id)


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


@router.post("/{work_order_id}/seed", dependencies=[Depends(require_admin_production)])
async def seed_default_stages(work_order_id: int, db: AsyncSession = Depends(get_db)):
    """إنشاء المراحل الافتراضية لأمر الإنتاج"""
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1

    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        raise HTTPException(404)
    _check_wo_open(wo)
    if wo.status == "done":
        raise HTTPException(400, f"أمر الإنتاج {wo.code} مكتمل بالفعل — لا يمكن إضافة مراحل له")

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


@router.post("/{work_order_id}", status_code=201, dependencies=[Depends(require_admin_production)])
async def create_stage(work_order_id: int, data: StageIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1
    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        raise HTTPException(404)
    _check_wo_open(wo)
    if wo.status == "done":
        raise HTTPException(400, f"أمر الإنتاج {wo.code} مكتمل بالفعل — لا يمكن إضافة مراحل له")
    await _check_worker(db, data.assigned_worker_id)
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


@router.patch("/{stage_id}/start", dependencies=[Depends(require_admin_production)])
async def start_stage(stage_id: int, db: AsyncSession = Depends(get_db)):
    s = await db.get(WorkOrderStage, stage_id)
    if not s:
        raise HTTPException(404)
    if s.status not in ("pending",):
        raise HTTPException(400, f"لا يمكن بدء مرحلة بحالة '{s.status}'")
    _wo_st = await db.get(WorkOrder, s.work_order_id)
    if _wo_st:
        _check_wo_open(_wo_st)

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


@router.patch("/{stage_id}/complete", dependencies=[Depends(require_admin_production)])
async def complete_stage(stage_id: int, actual_hours: Optional[float] = None, db: AsyncSession = Depends(get_db)):
    s = await db.get(WorkOrderStage, stage_id)
    if not s:
        raise HTTPException(404)
    if s.status != "in_progress":
        raise HTTPException(400, f"لا يمكن إنهاء مرحلة لم تُبدَأ بعد (الحالة الحالية: '{s.status}')")
    if actual_hours is not None and (actual_hours < 0 or actual_hours > 9999):
        raise HTTPException(400, "ساعات العمل الفعلية غير صالحة")
    s.status = "done"
    s.completed_at = datetime.utcnow()
    if actual_hours is not None:
        s.actual_hours = Decimal(str(actual_hours))
    elif s.started_at:
        hrs = (datetime.utcnow() - s.started_at).total_seconds() / 3600
        s.actual_hours = Decimal(str(round(hrs, 2)))

    # هل كل المراحل انتهت (منتهية أو متخطاة)؟ → إنهاء أمر الإنتاج بالكامل
    # (نفس مسار /production/complete تمامًا — راجع _maybe_finalize_work_order أعلاه)
    await _maybe_finalize_work_order(db, s.work_order_id)

    await db.commit()
    return {"id": stage_id, "status": "done", "actual_hours": float(s.actual_hours or 0)}


@router.patch("/{stage_id}/skip", dependencies=[Depends(require_admin_production)])
async def skip_stage(stage_id: int, reason: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    s = await db.get(WorkOrderStage, stage_id)
    if not s:
        raise HTTPException(404)
    if s.status not in ("pending", "in_progress"):
        # تخطّي مرحلة منتهية كان بيمسح حالتها "done" (والإحصائيات/نسبة الإنجاز)
        raise HTTPException(400, f"لا يمكن تخطّي مرحلة بحالة '{s.status}'")
    _wo_sk = await db.get(WorkOrder, s.work_order_id)
    if _wo_sk:
        _check_wo_open(_wo_sk)
    s.status = "skipped"
    if reason:
        s.notes = reason

    # محطة 29: تخطّي آخر مرحلة متبقية لازم يُنهي أمر الإنتاج بالكامل بالظبط
    # زي إنهاء آخر مرحلة — قبل كده كان تخطّي المرحلة الأخيرة بيسيب أمر
    # الإنتاج عالق في "in_progress" للأبد رغم إن كل المراحل خلصت/اتخطت.
    await _maybe_finalize_work_order(db, s.work_order_id)

    await db.commit()
    return {"id": stage_id, "status": "skipped"}


@router.put("/{stage_id}", dependencies=[Depends(require_admin_production)])
async def update_stage(stage_id: int, data: StageUpdateIn, db: AsyncSession = Depends(get_db)):
    s = await db.get(WorkOrderStage, stage_id)
    if not s:
        raise HTTPException(404)
    await _check_worker(db, data.assigned_worker_id)
    if data.status is not None and data.status != s.status:
        _wo_up = await db.get(WorkOrder, s.work_order_id)
        if _wo_up:
            _check_wo_open(_wo_up)
            if _wo_up.status == "done":
                raise HTTPException(400, f"أمر الإنتاج {_wo_up.code} مكتمل بالفعل — لا يمكن تغيير حالة مراحله")
    _prev_status = s.status
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(s, k, v)
    # حافظ على اتساق الطوابع الزمنية مع الحالة الجديدة
    if data.status is not None and data.status != _prev_status:
        if data.status == "in_progress" and not s.started_at:
            s.started_at = datetime.utcnow()
        if data.status == "done" and not s.completed_at:
            s.completed_at = datetime.utcnow()

    # محطة 29: PUT العام ده بيسمح بتغيير status مباشرة (بلا أي من فحوصات
    # /start أو /complete أو /skip) — لو التعديل خلّى كل المراحل منتهية/
    # متخطاة، لازم برضه يُنهي أمر الإنتاج بالكامل (مش يسيبه معلّق).
    if data.status is not None:
        await _maybe_finalize_work_order(db, s.work_order_id)

    await db.commit()
    res = await db.execute(
        select(WorkOrderStage).options(selectinload(WorkOrderStage.assigned_worker))
        .where(WorkOrderStage.id == stage_id)
    )
    return stage_dict(res.scalar_one())


@router.delete("/{stage_id}", dependencies=[Depends(require_admin_production)])
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
