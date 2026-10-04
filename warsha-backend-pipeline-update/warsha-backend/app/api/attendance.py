"""Attendance and daily wage management"""
from datetime import date as date_type, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, model_validator
from typing import Optional, List

from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import Worker, Attendance, PayrollRun, PayrollLine

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


async def _block_viewer_writes(request: Request, user: dict = Depends(require_any)) -> dict:
    """إصلاح ثغرة صلاحيات: مستخدم "مشاهد" (viewer) كان يقدر يسجّل/يعدّل/يحذف
    حضور (وبالتالي يغيّر أجور الكشف) بطلب API مباشر (ROLE_WRITE['viewer'] = [])."""
    if request.method.upper() not in _READ_METHODS and user.get("role") == "viewer":
        raise HTTPException(403, "صلاحية 'viewer' (مشاهد) لا تسمح بالتعديل")
    return user


router = APIRouter(prefix="/attendance", tags=["attendance"],
                   dependencies=[Depends(require_any), Depends(_block_viewer_writes)])

# الحالات اللي بيفهمها توليد كشف الرواتب (workers.generate_from_attendance)
# + حالات المشاريع الخارجية (external_projects). أي قيمة تانية كانت بتتسجل
# بصمت وتتحسب "صفر أجر" من غير ما المستخدم يعرف ليه.
_VALID_STATUSES = {"present", "absent", "half", "leave", "on_project", "half_project"}
_PROJECT_STATUSES = {"on_project", "half_project"}
_MAX_OVERTIME = Decimal("24")


def _validate_status(status) -> str:
    if status not in _VALID_STATUSES:
        raise HTTPException(400, f"حالة حضور غير صالحة: {status}. القيم المسموحة: {sorted(_VALID_STATUSES)}")
    return status


def _validate_overtime(value) -> Decimal:
    try:
        ot = Decimal(str(value if value is not None else 0))
    except Exception:
        raise HTTPException(400, "ساعات العمل الإضافي غير صالحة")
    if not ot.is_finite():
        raise HTTPException(400, "ساعات العمل الإضافي غير صالحة")
    if ot < 0:
        raise HTTPException(400, "ساعات العمل الإضافي لا يمكن أن تكون سالبة")
    if ot > _MAX_OVERTIME:
        raise HTTPException(400, "ساعات العمل الإضافي لا يمكن أن تتجاوز 24 ساعة في اليوم")
    return ot.quantize(Decimal("0.1"))


async def _ensure_not_in_paid_payroll(db: AsyncSession, tid: int, the_date) -> None:
    """تعديل/حذف حضور يوم داخل فترة كشف رواتب *مصروف* كان بيغيّر أساس
    الأجر اللي اتصرف فعلاً من الخزنة واتقيّد محاسبيًا، من غير أي أثر على
    الكشف — فيطلع الحضور والرواتب المصروفة مش متطابقين."""
    paid = await db.scalar(
        select(PayrollRun.id).where(
            PayrollRun.tenant_id == tid,
            PayrollRun.status == "paid",
            PayrollRun.week_start <= the_date,
            PayrollRun.week_end >= the_date,
        ).limit(1)
    )
    if paid:
        raise HTTPException(409, f"لا يمكن تعديل حضور يوم {the_date} — داخل كشف رواتب مصروف (#{paid})")


class AttendanceIn(BaseModel):
    worker_id: int
    work_date: Optional[date_type] = None   # from attendance.py callers
    attendance_date: Optional[date_type] = None  # renamed from 'date' to avoid Pydantic v2 name collision
    status: str = "present"
    overtime_hours: Decimal = Decimal("0")
    notes: Optional[str] = None

    @model_validator(mode='before')
    @classmethod
    def accept_date_alias(cls, values):
        """Accept 'date' key from callers and map it to attendance_date"""
        if isinstance(values, dict) and 'date' in values and 'attendance_date' not in values:
            values['attendance_date'] = values.pop('date')
        return values


class BulkAttendanceIn(BaseModel):
    work_date: Optional[date_type] = None
    attendance_date: Optional[date_type] = None  # renamed from 'date' to avoid Pydantic v2 name collision
    records: List[dict]

    @model_validator(mode='before')
    @classmethod
    def accept_date_alias(cls, values):
        """Accept 'date' key from frontend and map it to attendance_date"""
        if isinstance(values, dict) and 'date' in values and 'attendance_date' not in values:
            values['attendance_date'] = values.pop('date')
        return values


@router.post("", status_code=201)
async def record_attendance(data: AttendanceIn, db: AsyncSession = Depends(get_db)):
    the_date = data.work_date or data.attendance_date
    if not the_date:
        raise HTTPException(400, "يرجى تحديد التاريخ")
    _validate_status(data.status)
    overtime = _validate_overtime(data.overtime_hours)

    from app.core.database import current_tenant_id as _ctid_att
    tid = _ctid_att.get() or 1
    worker = await db.get(Worker, data.worker_id)
    if not worker or worker.tenant_id != tid:
        raise HTTPException(404, "العامل غير موجود")
    await _ensure_not_in_paid_payroll(db, tid, the_date)
    existing = (await db.execute(
        select(Attendance).where(
            Attendance.worker_id == data.worker_id,
            Attendance.date == the_date,
            Attendance.tenant_id == tid,
        )
    )).scalar_one_or_none()

    if existing:
        existing.status = data.status
        existing.overtime_hours = overtime
        existing.notes = data.notes
        if data.status not in _PROJECT_STATUSES:
            existing.ext_project_id = None
        await db.commit()
        return {"id": existing.id, "worker_id": existing.worker_id, "date": str(existing.date),
                "status": existing.status, "overtime_hours": float(existing.overtime_hours or 0),
                "notes": existing.notes}

    att = Attendance(
        tenant_id=tid,
        worker_id=data.worker_id,
        date=the_date,
        status=data.status,
        overtime_hours=overtime,
        notes=data.notes,
    )
    db.add(att)
    await db.commit()
    await db.refresh(att)
    return {"id": att.id, "worker_id": att.worker_id, "date": str(att.date),
            "status": att.status, "overtime_hours": float(att.overtime_hours or 0),
            "notes": att.notes}


@router.get("")
async def list_attendance_by_date(date: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """List attendance records for a specific date (used by the Attendance page)."""
    if not date:
        raise HTTPException(400, "التاريخ مطلوب")
    try:
        parsed_date = date_type.fromisoformat(date)
    except (ValueError, TypeError):
        raise HTTPException(400, "صيغة التاريخ غير صحيحة")
    from app.core.database import current_tenant_id as _ctid_lad
    result = await db.execute(
        select(Attendance).where(
            Attendance.date == parsed_date,
            Attendance.tenant_id == (_ctid_lad.get() or 1),
        ).order_by(Attendance.worker_id)
    )
    records = result.scalars().all()
    return [
        {
            "id":             r.id,
            "worker_id":      r.worker_id,
            "date":           str(r.date),
            "status":         r.status,
            "overtime_hours": float(r.overtime_hours or 0),
            "notes":          r.notes,
        }
        for r in records
    ]


@router.post("/bulk", status_code=201)
async def bulk_attendance(data: BulkAttendanceIn, db: AsyncSession = Depends(get_db)):
    the_date = data.work_date or data.attendance_date
    if not the_date:
        raise HTTPException(400, "يرجى تحديد التاريخ")

    from app.core.database import current_tenant_id as _ctid_battat
    tid = _ctid_battat.get() or 1

    await _ensure_not_in_paid_payroll(db, tid, the_date)

    tenant_worker_ids = set((await db.execute(
        select(Worker.id).where(Worker.tenant_id == tid)
    )).scalars().all())

    # تحقق كامل قبل أي كتابة — سجل غلط في النص كان بيسيب اللي قبله متسجل
    # في الجلسة، وقيمة worker_id نصية ("5") كانت بترجع 404 رغم إن العامل موجود.
    cleaned = []
    seen = set()
    for rec in data.records:
        if not isinstance(rec, dict):
            raise HTTPException(400, "صيغة سجل الحضور غير صحيحة")
        try:
            wid = int(rec.get("worker_id"))
        except (TypeError, ValueError):
            raise HTTPException(400, f"رقم العامل غير صالح: {rec.get('worker_id')}")
        if wid not in tenant_worker_ids:
            raise HTTPException(404, f"العامل {rec.get('worker_id')} غير موجود")
        if wid in seen:
            raise HTTPException(400, f"العامل {wid} مكرر في نفس الطلب")
        seen.add(wid)
        status = _validate_status(rec.get("status", "present") or "present")
        overtime = _validate_overtime(rec.get("overtime_hours"))
        cleaned.append((rec, wid, status, overtime))

    results = []
    for rec, wid, status, overtime in cleaned:
        rec = {**rec, "worker_id": wid, "status": status}
        existing = (await db.execute(
            select(Attendance).where(
                Attendance.worker_id == rec["worker_id"],
                Attendance.date == the_date,
                Attendance.tenant_id == tid,
            )
        )).scalar_one_or_none()

        if existing:
            existing.status = rec.get("status", "present")
            existing.overtime_hours = overtime
            if rec.get("notes") is not None:
                existing.notes = rec.get("notes")
            if existing.status not in _PROJECT_STATUSES:
                existing.ext_project_id = None
        else:
            att = Attendance(
                tenant_id=tid,
                worker_id=rec["worker_id"],
                date=the_date,
                status=rec.get("status", "present"),
                overtime_hours=overtime,
            )
            db.add(att)
        results.append({"worker_id": rec["worker_id"], "status": rec.get("status")})

    await db.commit()
    return {"saved": len(results), "date": str(the_date)}


@router.get("/summary")
async def get_week_attendance_summary(week: str, db: AsyncSession = Depends(get_db)):
    """Alias for /week — used by frontend api.attendance.summary(d)"""
    try:
        week_start = date_type.fromisoformat(week)
    except (ValueError, TypeError):
        raise HTTPException(400, "صيغة التاريخ غير صحيحة")
    return await _week_attendance(week_start, db)


@router.get("/week")
async def get_week_attendance(week_start: date_type, db: AsyncSession = Depends(get_db)):
    return await _week_attendance(week_start, db)


async def _week_attendance(week_start: date_type, db: AsyncSession):
    from app.core.database import current_tenant_id as _ctid_wk
    tid = _ctid_wk.get() or 1
    week_end = week_start + timedelta(days=6)
    workers = (await db.execute(
        select(Worker).where(Worker.is_active == True, Worker.tenant_id == tid).order_by(Worker.name)
    )).scalars().all()

    att_records = (await db.execute(
        select(Attendance).where(
            Attendance.date >= week_start,
            Attendance.date <= week_end,
            Attendance.tenant_id == tid,
        )
    )).scalars().all()

    # index: worker_id -> date_str -> {status, overtime, id}
    att_map = {}
    for a in att_records:
        att_map.setdefault(a.worker_id, {})[str(a.date)] = {
            "status": a.status,
            "overtime": float(a.overtime_hours or 0),
            "id": a.id,
        }

    dates = []
    d = week_start
    while d <= week_end:
        dates.append(str(d))
        d += timedelta(days=1)

    summary = []
    for w in workers:
        w_att = att_map.get(w.id, {})
        days_present = 0.0
        total_overtime = 0.0
        for dt in dates:
            rec = w_att.get(dt)
            if rec:
                if rec["status"] == "present":  days_present += 1.0
                elif rec["status"] == "half":   days_present += 0.5
                total_overtime += rec["overtime"]

        daily = float(w.daily_wage or 0)
        gross = days_present * daily + (total_overtime * daily / 8)

        summary.append({
            "worker_id":      w.id,
            "worker_code":    w.code,
            "worker_name":    w.name,
            "job_title":      w.job_title or "",
            "daily_wage":     daily,
            "days_present":   days_present,
            "gross_amount":   round(gross, 2),
            "gross_due":      round(gross, 2),   # alias for frontend
            "attendance":     w_att,
        })

    return {
        "week_start": str(week_start),
        "week_end":   str(week_end),
        "dates":      dates,
        "workers":    summary,
    }


@router.get("/worker/{worker_id}")
async def get_worker_attendance(worker_id: int, month: int, year: int,
                                db: AsyncSession = Depends(get_db)):
    from calendar import monthrange
    if not (1 <= month <= 12) or not (1900 <= year <= 9999):
        raise HTTPException(400, "الشهر أو السنة غير صالحة")
    _, days_in_month = monthrange(year, month)
    start = date_type(year, month, 1)
    end   = date_type(year, month, days_in_month)
    from app.core.database import current_tenant_id as _ctid_gwa
    w = await db.get(Worker, worker_id)
    if not w or w.tenant_id != (_ctid_gwa.get() or 1): raise HTTPException(404)

    records = (await db.execute(
        select(Attendance).where(
            Attendance.worker_id == worker_id,
            Attendance.tenant_id == w.tenant_id,
            Attendance.date >= start,
            Attendance.date <= end,
        ).order_by(Attendance.date)
    )).scalars().all()

    total_days = sum(
        1.0 if a.status == "present" else 0.5 if a.status == "half" else 0.0
        for a in records
    )
    return {
        "worker": {"id": w.id, "name": w.name, "daily_wage": float(w.daily_wage or 0)},
        "month": month, "year": year,
        "days_present": total_days,
        "gross_pay": round(total_days * float(w.daily_wage or 0), 2),
        "records": [{"date": str(a.date), "status": a.status,
                     "overtime": float(a.overtime_hours or 0)} for a in records]
    }


@router.delete("/{att_id}", status_code=204)
async def delete_attendance(att_id: int, db: AsyncSession = Depends(get_db)):
    """حذف سجل حضور"""
    from app.core.database import current_tenant_id as _ctid_del_att
    att = await db.get(Attendance, att_id)
    if not att or att.tenant_id != (_ctid_del_att.get() or 1):
        raise HTTPException(404, "سجل الحضور غير موجود")
    await _ensure_not_in_paid_payroll(db, att.tenant_id, att.date)
    await db.delete(att)
    await db.commit()
