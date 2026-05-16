from datetime import date, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_db
from app.models.models import Worker, PayrollRun, PayrollLine, Attendance, WorkerAdvance

router = APIRouter(prefix="/workers", tags=["workers"])

class WorkerIn(BaseModel):
    code: str; name: str
    job_title: Optional[str] = None; phone: Optional[str] = None
    national_id: Optional[str] = None; hire_date: Optional[date] = None
    daily_wage: Decimal; notes: Optional[str] = None

class AttendanceIn(BaseModel):
    worker_id: int
    att_date: date = None
    date: date = None          # frontend sends date
    status: str = "present"
    notes: Optional[str] = None

class AdvanceIn(BaseModel):
    worker_id: int; amount: Decimal
    advance_type: str = "advance"; date: date; notes: Optional[str] = None

class PayrollRunIn(BaseModel):
    week_start: date; week_end: date; notes: Optional[str] = None


def wd(w): return {"id":w.id,"code":w.code,"name":w.name,"job_title":w.job_title,
    "phone":w.phone,"hire_date":str(w.hire_date) if w.hire_date else None,
    "daily_wage":float(w.daily_wage or 0),"is_active":w.is_active,"notes":w.notes}

@router.get("")
async def list_workers(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Worker).where(Worker.is_active==True).order_by(Worker.name))
    return [wd(w) for w in r.scalars().all()]

@router.post("", status_code=201)
async def create_worker(data: WorkerIn, db: AsyncSession = Depends(get_db)):
    d = data.model_dump()
    # base_weekly_wage = daily_wage × 6 تلقائياً
    d['base_weekly_wage'] = data.daily_wage * 6
    w = Worker(**d)
    db.add(w); await db.commit(); await db.refresh(w); return wd(w)

@router.put("/{wid}")
async def update_worker(wid: int, data: WorkerIn, db: AsyncSession = Depends(get_db)):
    w = await db.get(Worker, wid)
    if not w: raise HTTPException(404)
    d = data.model_dump()
    d['base_weekly_wage'] = data.daily_wage * 6
    for k,v in d.items(): setattr(w,k,v)
    await db.commit(); await db.refresh(w); return wd(w)

@router.delete("/{wid}")
async def deactivate(wid: int, db: AsyncSession = Depends(get_db)):
    w = await db.get(Worker, wid)
    if not w: raise HTTPException(404)
    w.is_active = False; await db.commit(); return {"message":"تم التعطيل"}

@router.get("/{wid}/profile")
async def worker_profile(wid: int, db: AsyncSession = Depends(get_db)):
    w = await db.get(Worker, wid)
    if not w: raise HTTPException(404)
    att = (await db.execute(
        select(Attendance).where(Attendance.worker_id==wid)
        .order_by(Attendance.date.desc()).limit(60)
    )).scalars().all()
    adv = (await db.execute(
        select(WorkerAdvance).where(WorkerAdvance.worker_id==wid, WorkerAdvance.is_settled==False)
        .order_by(WorkerAdvance.date.desc())
    )).scalars().all()
    debt = sum(a.amount for a in adv if a.advance_type=="advance")
    bonus = sum(a.amount for a in adv if a.advance_type=="bonus")
    return {"worker":wd(w),"total_advance_debt":float(debt),"total_bonus":float(bonus),
        "attendance":[{"id":a.id,"date":str(a.date),"status":a.status,"notes":a.notes} for a in att],
        "advances":[{"id":a.id,"date":str(a.date),"amount":float(a.amount),"advance_type":a.advance_type,"is_settled":a.is_settled,"notes":a.notes} for a in adv]}

# ── Attendance endpoints ───────────────────────────────────────
@router.post("/attendance", status_code=201)
async def record_attendance(data: AttendanceIn, db: AsyncSession = Depends(get_db)):
    att_date = data.att_date or data.date
    if not att_date:
        raise HTTPException(400, "يرجى تحديد التاريخ")
    old = (await db.execute(
        select(Attendance).where(
            Attendance.worker_id==data.worker_id,
            Attendance.date==att_date
        )
    )).scalar_one_or_none()
    if old:
        old.status = data.status
        old.notes = data.notes
        await db.commit(); await db.refresh(old)
        return {"id":old.id,"date":str(old.date),"status":old.status}
    a = Attendance(worker_id=data.worker_id, date=att_date,
                   status=data.status, notes=data.notes)
    db.add(a); await db.commit(); await db.refresh(a)
    return {"id":a.id,"date":str(a.date),"status":a.status}

@router.delete("/attendance/{aid}")
async def del_attendance(aid: int, db: AsyncSession = Depends(get_db)):
    a = await db.get(Attendance, aid)
    if not a: raise HTTPException(404)
    await db.delete(a); await db.commit(); return {"message":"تم"}

@router.get("/attendance/week")
async def week_attendance(week_start: str, db: AsyncSession = Depends(get_db)):
    start = date.fromisoformat(week_start); end = start + timedelta(days=6)
    workers = (await db.execute(select(Worker).where(Worker.is_active==True))).scalars().all()
    att_recs = (await db.execute(
        select(Attendance).where(Attendance.date>=start, Attendance.date<=end)
    )).scalars().all()
    lkp = {}
    for a in att_recs:
        lkp.setdefault(a.worker_id, {})[str(a.date)] = {"status":a.status,"id":a.id}
    days = [start+timedelta(days=i) for i in range(7)]
    result = []
    for w in workers:
        days_present = Decimal("0")
        day_map = {}
        for d in days:
            ds = str(d); rec = lkp.get(w.id,{}).get(ds)
            s = rec["status"] if rec else "absent"
            day_map[ds] = {"status":s,"att_id":rec["id"] if rec else None}
            if s=="present": days_present += Decimal("1")
            elif s=="half": days_present += Decimal("0.5")
        result.append({"worker_id":w.id,"worker_name":w.name,"job_title":w.job_title,
            "daily_wage":float(w.daily_wage or 0),"days":day_map,
            "days_present":float(days_present),"gross_due":float((w.daily_wage or 0)*days_present)})
    return {"week_start":str(start),"week_end":str(end),"workers":result}

# ── Advances ───────────────────────────────────────────────────
@router.post("/advances", status_code=201)
async def add_advance(data: AdvanceIn, db: AsyncSession = Depends(get_db)):
    a = WorkerAdvance(**data.model_dump()); db.add(a); await db.commit(); await db.refresh(a)
    return {"id":a.id,"amount":float(a.amount),"advance_type":a.advance_type}

@router.delete("/advances/{aid}")
async def del_advance(aid: int, db: AsyncSession = Depends(get_db)):
    a = await db.get(WorkerAdvance, aid)
    if not a: raise HTTPException(404)
    await db.delete(a); await db.commit(); return {"message":"تم"}

# ── Payroll ────────────────────────────────────────────────────
@router.get("/payroll")
async def list_payroll(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(PayrollRun).order_by(PayrollRun.week_start.desc()).limit(20))
    return [{"id":x.id,"week_start":str(x.week_start),"week_end":str(x.week_end),
             "total_gross":float(x.total_gross),"total_net":float(x.total_net),
             "status":x.status,"paid_date":str(x.paid_date) if x.paid_date else None}
            for x in r.scalars().all()]

@router.post("/payroll", status_code=201)
async def create_payroll(data: PayrollRunIn, db: AsyncSession = Depends(get_db)):
    run = PayrollRun(**data.model_dump()); db.add(run); await db.commit(); await db.refresh(run)
    return {"id":run.id,"week_start":str(run.week_start),"week_end":str(run.week_end)}

@router.post("/payroll/{run_id}/generate")
async def generate_from_attendance(run_id: int, db: AsyncSession = Depends(get_db)):
    run = await db.get(PayrollRun, run_id)
    if not run: raise HTTPException(404)
    workers = (await db.execute(select(Worker).where(Worker.is_active==True))).scalars().all()
    att_recs = (await db.execute(
        select(Attendance).where(Attendance.date>=run.week_start, Attendance.date<=run.week_end)
    )).scalars().all()
    att_map = {}
    for a in att_recs:
        att_map.setdefault(a.worker_id, Decimal("0"))
        if a.status=="present": att_map[a.worker_id]+=Decimal("1")
        elif a.status=="half": att_map[a.worker_id]+=Decimal("0.5")
    adv_recs = (await db.execute(select(WorkerAdvance).where(WorkerAdvance.is_settled==False))).scalars().all()
    adv_map = {}
    for a in adv_recs:
        adv_map.setdefault(a.worker_id,{"d":Decimal("0"),"b":Decimal("0"),"ids":[]})
        if a.advance_type=="bonus": adv_map[a.worker_id]["b"]+=a.amount
        else: adv_map[a.worker_id]["d"]+=a.amount
        adv_map[a.worker_id]["ids"].append(a.id)
    old = (await db.execute(select(PayrollLine).where(PayrollLine.payroll_run_id==run_id))).scalars().all()
    for l in old: await db.delete(l)
    run.total_gross=run.total_deductions=run.total_net=Decimal("0")
    result = []
    for w in workers:
        days = att_map.get(w.id, Decimal("0"))
        gross = (w.daily_wage or Decimal("0")) * days
        ai = adv_map.get(w.id,{"d":Decimal("0"),"b":Decimal("0"),"ids":[]})
        net = gross + ai["b"] - ai["d"]
        db.add(PayrollLine(payroll_run_id=run_id,worker_id=w.id,days_worked=days,
            gross_amount=gross,bonus=ai["b"],deductions=ai["d"],net_amount=net))
        run.total_gross+=gross; run.total_deductions+=ai["d"]; run.total_net+=net
        for aid in ai["ids"]:
            adv = await db.get(WorkerAdvance, aid)
            if adv: adv.is_settled=True; adv.payroll_run_id=run_id
        result.append({"worker":w.name,"days":float(days),"net":float(net)})
    await db.commit()
    return {"generated":len(result),"total_net":float(run.total_net),"lines":result}

@router.get("/payroll/{run_id}")
async def get_payroll(run_id: int, db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(PayrollRun).options(
        selectinload(PayrollRun.lines).selectinload(PayrollLine.worker)
    ).where(PayrollRun.id==run_id))
    run = r.scalar_one_or_none()
    if not run: raise HTTPException(404)
    return {"id":run.id,"week_start":str(run.week_start),"week_end":str(run.week_end),
        "total_gross":float(run.total_gross),"total_net":float(run.total_net),
        "total_deductions":float(run.total_deductions),"status":run.status,
        "paid_date":str(run.paid_date) if run.paid_date else None,
        "lines":[{"id":l.id,"worker_id":l.worker_id,
            "worker_name":l.worker.name if l.worker else "",
            "job_title":l.worker.job_title if l.worker else "",
            "days_worked":float(l.days_worked),"gross_amount":float(l.gross_amount),
            "deductions":float(l.deductions),"bonus":float(l.bonus),
            "net_amount":float(l.net_amount)} for l in run.lines]}

@router.post("/payroll/{run_id}/pay")
async def mark_paid(run_id: int, db: AsyncSession = Depends(get_db)):
    run = await db.get(PayrollRun, run_id)
    if not run: raise HTTPException(404)
    run.status="paid"; run.paid_date=date.today(); await db.commit()
    return {"message":"تم صرف الرواتب","paid_date":str(run.paid_date)}


@router.delete("/payroll/{run_id}")
async def delete_payroll_run(run_id: int, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select as sa_select
    run = await db.get(PayrollRun, run_id)
    if not run: raise HTTPException(404)
    if run.status == "paid": raise HTTPException(400, "لا يمكن حذف كشف مصروف")
    # Delete lines first
    lines = (await db.execute(sa_select(PayrollLine).where(PayrollLine.payroll_run_id==run_id))).scalars().all()
    for l in lines: await db.delete(l)
    await db.delete(run)
    await db.commit()
    return {"message": "تم الحذف", "id": run_id}

@router.get("/{wid}/profile")
async def worker_profile(wid: int, db: AsyncSession = Depends(get_db)):
    w = await db.get(Worker, wid)
    if not w: raise HTTPException(404)
    advances = (await db.execute(
        select(WorkerAdvance).where(WorkerAdvance.worker_id==wid).order_by(WorkerAdvance.date.desc()).limit(30)
    )).scalars().all()
    lines = (await db.execute(
        select(PayrollLine, PayrollRun)
        .join(PayrollRun, PayrollLine.payroll_run_id==PayrollRun.id)
        .where(PayrollLine.worker_id==wid)
        .order_by(PayrollRun.week_start.desc()).limit(10)
    )).all()
    pending_adv   = sum(a.amount for a in advances if not a.is_settled and a.advance_type!='bonus')
    pending_bonus = sum(a.amount for a in advances if not a.is_settled and a.advance_type=='bonus')
    return {
        "worker": wd(w),
        "pending_advances": float(pending_adv),
        "pending_bonus":    float(pending_bonus),
        "advances": [{"id":a.id,"date":str(a.date),"amount":float(a.amount),
                      "advance_type":a.advance_type,"is_settled":a.is_settled,"notes":a.notes}
                     for a in advances],
        "payroll_lines": [{"week_start":str(run.week_start),"week_end":str(run.week_end),
                           "days_worked":float(line.days_worked),"gross_amount":float(line.gross_amount),
                           "deductions":float(line.deductions),"bonus":float(line.bonus),
                           "net_amount":float(line.net_amount)}
                          for line, run in lines],
    }

@router.post("/advances", status_code=201)
async def create_advance(data: AdvanceIn, db: AsyncSession = Depends(get_db)):
    adv = WorkerAdvance(
        worker_id=data.worker_id, amount=data.amount,
        advance_type=data.advance_type, date=data.date,
        notes=data.notes, is_settled=False
    )
    db.add(adv); await db.commit(); await db.refresh(adv)
    return {"id":adv.id,"amount":float(adv.amount),"advance_type":adv.advance_type}
