"""Attendance and daily wage management"""
from datetime import date as date_type, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, model_validator
from typing import Optional, List

from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import Worker, Attendance, PayrollRun, PayrollLine

router = APIRouter(prefix="/attendance", tags=["attendance"], dependencies=[Depends(require_any)])


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
    if data.overtime_hours is not None and data.overtime_hours < 0:
        raise HTTPException(400, "ساعات العمل الإضافي لا يمكن أن تكون سالبة")

    from app.core.database import current_tenant_id as _ctid_att
    tid = _ctid_att.get() or 1
    worker = await db.get(Worker, data.worker_id)
    if not worker or worker.tenant_id != tid:
        raise HTTPException(404, "العامل غير موجود")
    existing = (await db.execute(
        select(Attendance).where(
            Attendance.worker_id == data.worker_id,
            Attendance.date == the_date,
            Attendance.tenant_id == tid,
        )
    )).scalar_one_or_none()

    if existing:
        existing.status = data.status
        existing.overtime_hours = data.overtime_hours
        existing.notes = data.notes
        await db.commit()
        return {"id": existing.id, "worker_id": existing.worker_id, "date": str(existing.date),
                "status": existing.status, "overtime_hours": float(existing.overtime_hours or 0),
                "notes": existing.notes}

    att = Attendance(
        tenant_id=tid,
        worker_id=data.worker_id,
        date=the_date,
        status=data.status,
        overtime_hours=data.overtime_hours,
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

    tenant_worker_ids = set((await db.execute(
        select(Worker.id).where(Worker.tenant_id == tid)
    )).scalars().all())

    results = []
    for rec in data.records:
        if rec.get("worker_id") not in tenant_worker_ids:
            raise HTTPException(404, f"العامل {rec.get('worker_id')} غير موجود")
        overtime = Decimal(str(rec.get("overtime_hours") or 0))
        if overtime < 0:
            raise HTTPException(400, "ساعات العمل الإضافي لا يمكن أن تكون سالبة")
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
            "overtime": float(a.overtime_hours),
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
    _, days_in_month = monthrange(year, month)
    start = date_type(year, month, 1)
    end   = date_type(year, month, days_in_month)
    from app.core.database import current_tenant_id as _ctid_gwa
    w = await db.get(Worker, worker_id)
    if not w or w.tenant_id != (_ctid_gwa.get() or 1): raise HTTPException(404)

    records = (await db.execute(
        select(Attendance).where(
            Attendance.worker_id == worker_id,
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
                     "overtime": float(a.overtime_hours)} for a in records]
    }


@router.delete("/{att_id}", status_code=204)
async def delete_attendance(att_id: int, db: AsyncSession = Depends(get_db)):
    """حذف سجل حضور"""
    from app.core.database import current_tenant_id as _ctid_del_att
    att = await db.get(Attendance, att_id)
    if not att or att.tenant_id != (_ctid_del_att.get() or 1):
        raise HTTPException(404, "سجل الحضور غير موجود")
    await db.delete(att)
    await db.commit()
