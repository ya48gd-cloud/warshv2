"""Attendance and daily wage management"""
from datetime import date, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional, List

from app.core.database import get_db
from app.models.models import Worker, Attendance, PayrollRun, PayrollLine

router = APIRouter(prefix="/attendance", tags=["attendance"])


class AttendanceIn(BaseModel):
    worker_id: int
    work_date: Optional[date] = None   # from attendance.py callers
    date: Optional[date] = None         # from workers.py callers
    status: str = "present"
    overtime_hours: Decimal = Decimal("0")
    notes: Optional[str] = None


class BulkAttendanceIn(BaseModel):
    work_date: Optional[date] = None
    date: Optional[date] = None
    records: List[dict]


@router.post("", status_code=201)
async def record_attendance(data: AttendanceIn, db: AsyncSession = Depends(get_db)):
    the_date = data.work_date or data.date
    if not the_date:
        raise HTTPException(400, "يرجى تحديد التاريخ")

    existing = (await db.execute(
        select(Attendance).where(
            Attendance.worker_id == data.worker_id,
            Attendance.date == the_date
        )
    )).scalar_one_or_none()

    if existing:
        existing.status = data.status
        existing.overtime_hours = data.overtime_hours
        existing.notes = data.notes
        await db.commit()
        return {"id": existing.id, "date": str(existing.date), "status": existing.status}

    att = Attendance(
        worker_id=data.worker_id,
        date=the_date,
        status=data.status,
        overtime_hours=data.overtime_hours,
        notes=data.notes,
    )
    db.add(att)
    await db.commit()
    await db.refresh(att)
    return {"id": att.id, "date": str(att.date), "status": att.status}


@router.post("/bulk", status_code=201)
async def bulk_attendance(data: BulkAttendanceIn, db: AsyncSession = Depends(get_db)):
    the_date = data.work_date or data.date
    if not the_date:
        raise HTTPException(400, "يرجى تحديد التاريخ")

    results = []
    for rec in data.records:
        existing = (await db.execute(
            select(Attendance).where(
                Attendance.worker_id == rec["worker_id"],
                Attendance.date == the_date
            )
        )).scalar_one_or_none()

        if existing:
            existing.status = rec.get("status", "present")
            existing.overtime_hours = Decimal(str(rec.get("overtime_hours", 0)))
        else:
            att = Attendance(
                worker_id=rec["worker_id"],
                date=the_date,
                status=rec.get("status", "present"),
                overtime_hours=Decimal(str(rec.get("overtime_hours", 0))),
            )
            db.add(att)
        results.append({"worker_id": rec["worker_id"], "status": rec.get("status")})

    await db.commit()
    return {"saved": len(results), "date": str(the_date)}


@router.get("/week")
async def get_week_attendance(week_start: date, db: AsyncSession = Depends(get_db)):
    week_end = week_start + timedelta(days=6)
    workers = (await db.execute(
        select(Worker).where(Worker.is_active == True).order_by(Worker.name)
    )).scalars().all()

    att_records = (await db.execute(
        select(Attendance).where(
            Attendance.date >= week_start,
            Attendance.date <= week_end,
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
    start = date(year, month, 1)
    end   = date(year, month, days_in_month)
    w = await db.get(Worker, worker_id)
    if not w: raise HTTPException(404)

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
