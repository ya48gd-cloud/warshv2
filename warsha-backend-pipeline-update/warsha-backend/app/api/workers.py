from datetime import date, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import Worker, PayrollRun, PayrollLine, Attendance, WorkerAdvance, ExtProjectMember

router = APIRouter(prefix="/workers", tags=["workers"], dependencies=[Depends(require_any)])


async def _next_worker_code(db: AsyncSession, tenant_id: int) -> str:
    # لازم يتحسب لكل مستأجر (tenant) على حدة — غير كده تظهر أكواد غريبة/غير
    # متسلسلة لأي عميل جديد بمجرد وجود عملاء آخرين بعدد عمال أكبر.
    count = await db.scalar(
        select(func.count()).select_from(Worker).where(Worker.tenant_id == tenant_id)
    )
    n = (count or 0) + 1
    while True:
        code = f"EMP-{n:04d}"
        exists = await db.scalar(
            select(Worker.id).where(Worker.code == code, Worker.tenant_id == tenant_id)
        )
        if not exists:
            return code
        n += 1


class WorkerIn(BaseModel):
    code: Optional[str] = None; name: str
    job_title: Optional[str] = None; phone: Optional[str] = None
    national_id: Optional[str] = None; hire_date: Optional[date] = None
    daily_wage: Decimal; notes: Optional[str] = None
    contract_type:     Optional[str]  = None
    insurance_number:  Optional[str]  = None
    contract_end_date: Optional[date] = None

class AdvanceIn(BaseModel):
    worker_id: int; amount: Decimal
    advance_type: str = "advance"; date: date; notes: Optional[str] = None

class PayrollRunIn(BaseModel):
    week_start: date; week_end: date; notes: Optional[str] = None


def wd(w): return {"id":w.id,"code":w.code,"name":w.name,"job_title":w.job_title,
    "phone":w.phone,"hire_date":str(w.hire_date) if w.hire_date else None,
    "daily_wage":float(w.daily_wage or 0),"is_active":w.is_active,"notes":w.notes,
    "contract_type":    getattr(w,"contract_type",None),
    "insurance_number": getattr(w,"insurance_number",None),
    "contract_end_date":str(w.contract_end_date) if getattr(w,"contract_end_date",None) else None}

@router.get("")
async def list_workers(db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_lw
    tid = _ctid_lw.get() or 1
    r = await db.execute(select(Worker).where(Worker.is_active==True, Worker.tenant_id==tid).order_by(Worker.name))
    return [wd(w) for w in r.scalars().all()]

@router.get("/next-code")
async def get_next_worker_code(db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_nc
    return {"code": await _next_worker_code(db, _ctid_nc.get() or 1)}


@router.post("", status_code=201)
async def create_worker(data: WorkerIn, db: AsyncSession = Depends(get_db)):
    if data.daily_wage is None or data.daily_wage <= 0:
        raise HTTPException(400, "الأجر اليومي يجب أن يكون أكبر من صفر")
    if data.hire_date and data.contract_end_date and data.contract_end_date < data.hire_date:
        raise HTTPException(400, "تاريخ نهاية العقد يجب أن يكون بعد تاريخ التعيين")
    from app.core.database import current_tenant_id as _ctid_wrk
    tid = _ctid_wrk.get() or 1
    d = data.model_dump()
    d['code'] = d.get('code') or await _next_worker_code(db, tid)
    # كود يدوي مكرَّر لنفس المستأجر كان بيسقط في IntegrityError غير معالَج
    # (500) بدل رسالة تحقق واضحة — القيد الفريد على (tenant_id, code) موجود
    # في الموديل لكن محدش كان بيتحقق منه قبل الإدراج.
    dup = await db.scalar(select(Worker.id).where(Worker.code == d['code'], Worker.tenant_id == tid))
    if dup:
        raise HTTPException(400, f"الكود {d['code']} مستخدم بالفعل")
    # base_weekly_wage = daily_wage × 6 تلقائياً
    d['base_weekly_wage'] = data.daily_wage * 6
    d['tenant_id'] = tid
    w = Worker(**d)
    db.add(w); await db.commit(); await db.refresh(w); return wd(w)

@router.put("/{wid}")
async def update_worker(wid: int, data: WorkerIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_uw
    tid_uw = _ctid_uw.get() or 1
    w = await db.get(Worker, wid)
    if not w or w.tenant_id != tid_uw: raise HTTPException(404)
    if data.daily_wage is None or data.daily_wage <= 0:
        raise HTTPException(400, "الأجر اليومي يجب أن يكون أكبر من صفر")
    if data.hire_date and data.contract_end_date and data.contract_end_date < data.hire_date:
        raise HTTPException(400, "تاريخ نهاية العقد يجب أن يكون بعد تاريخ التعيين")
    d = data.model_dump()
    if not d.get("code"):
        d["code"] = w.code  # حافظ على الكود الموجود لو لم يُرسَل
    else:
        dup = await db.scalar(
            select(Worker.id).where(Worker.code == d['code'], Worker.tenant_id == tid_uw, Worker.id != wid)
        )
        if dup:
            raise HTTPException(400, f"الكود {d['code']} مستخدم بالفعل")
    d['base_weekly_wage'] = data.daily_wage * 6
    for k,v in d.items(): setattr(w,k,v)
    await db.commit(); await db.refresh(w); return wd(w)

@router.delete("/{wid}")
async def deactivate(wid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_dw
    w = await db.get(Worker, wid)
    if not w or w.tenant_id != (_ctid_dw.get() or 1): raise HTTPException(404)
    w.is_active = False; await db.commit(); return {"message":"تم التعطيل"}

# ── Advances ───────────────────────────────────────────────────
_VALID_ADVANCE_TYPES = {"advance", "deduction", "bonus"}

@router.post("/advances", status_code=201)
async def add_advance(data: AdvanceIn, db: AsyncSession = Depends(get_db)):
    if data.advance_type not in _VALID_ADVANCE_TYPES:
        raise HTTPException(400, f"نوع غير صالح. القيم المسموحة: {sorted(_VALID_ADVANCE_TYPES)}")
    if data.amount is None or data.amount <= 0:
        raise HTTPException(400, "المبلغ يجب أن يكون أكبر من صفر")
    from app.core.database import current_tenant_id as _ctid_adv
    tid_adv = _ctid_adv.get() or 1
    worker = await db.get(Worker, data.worker_id)
    if not worker or worker.tenant_id != tid_adv:
        raise HTTPException(404, "العامل غير موجود")

    adv_payload = data.model_dump()
    adv_payload['tenant_id'] = tid_adv
    a = WorkerAdvance(**adv_payload); db.add(a); await db.flush()

    # ملاحظة إصلاح: "bonus" مش نقدية بتخرج من الخزنة دلوقتي — قيمتها بتتضاف
    # لصافي الراتب وتُصرف فعليًا فقط وقت دفع الكشف (pay_payroll_run). قبل كده
    # كانت الخزنة بتتصرف هنا فورًا *و كمان* الصافي وقت الدفع، يعني المكافأة
    # كانت بتتصرف مرتين فعليًا من نفس الخزنة لنفس المبلغ.
    # "deduction" زي "advance" تمامًا من ناحية الخزنة/القيد — فلوس بتخرج الآن
    # وتُسترد من الراتب لاحقًا، فلازم يتسجل لها نفس القيد المحاسبي (كان قبل
    # كده بيتسجل لـ"advance" بس، فتفضل حركة خزنة بدون أي أثر في دليل الحسابات).
    if a.advance_type != "bonus":
        from app.services.treasury_link import record_payment_in_treasury
        txn = await record_payment_in_treasury(
            db, amount=a.amount, direction="out", source="worker_advance",
            linked_type="worker_advance", linked_id=a.id,
            category_name="سلف عمال",
            description=f"سلفة عامل: {worker.name if worker else a.worker_id}",
        )
        if txn is not None:
            from app.models.models import Treasury
            treasury = await db.get(Treasury, txn.treasury_id)
            if treasury and treasury.coa_account_id:
                from app.services.auto_journal import journal_worker_advance
                await journal_worker_advance(
                    db, tid_adv, a.id,
                    worker.name if worker else str(a.worker_id),
                    a.amount, treasury.coa_account_id, a.date,
                )
    await db.commit(); await db.refresh(a)
    return {"id":a.id,"amount":float(a.amount),"advance_type":a.advance_type}

@router.delete("/advances/{aid}")
async def del_advance(aid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_da
    tid_da = _ctid_da.get() or 1
    a = await db.get(WorkerAdvance, aid)
    if not a or a.tenant_id != tid_da: raise HTTPException(404)
    if a.is_settled:
        # اتسوّت بالفعل في كشف رواتب — صافي راتب العامل اتخصم منه المبلغ ده
        # فعليًا. حذفها المباشر هنا كان هيمسح السلفة من غير ما يرجّع الخصم
        # في كشف الرواتب، يعني العامل يتخصم منه فلوس لسلفة "مبقتش موجودة".
        # لازم تُلغى تسوية الكشف نفسه أولاً (حذف/إعادة توليد الكشف).
        raise HTTPException(400, "لا يمكن حذف سلفة تم تسويتها في كشف رواتب — احذف كشف الرواتب أولاً لإلغاء التسوية")
    from app.models.models import TreasuryTransaction, Treasury
    txn = (await db.execute(
        select(TreasuryTransaction).where(
            TreasuryTransaction.linked_type == "worker_advance",
            TreasuryTransaction.linked_id == aid,
        )
    )).scalar_one_or_none()
    if txn:
        treasury = await db.get(Treasury, txn.treasury_id)
        if treasury:
            treasury.balance = (treasury.balance or Decimal("0")) + txn.amount
        await db.delete(txn)
        # عكس القيد المحاسبي المرتبط (مدين 1220 / دائن الخزنة) — كان قبل كده
        # بيتعكس رصيد الخزنة بس، ويفضل قيد يتيم في دليل الحسابات يوازيه.
        from app.services.auto_journal import journal_reverse_entry
        await journal_reverse_entry(db, tid_da, "worker_advance", aid, "حذف السلفة")
    await db.delete(a); await db.commit(); return {"message":"تم"}

# ── Payroll ────────────────────────────────────────────────────
@router.get("/payroll")
async def list_payroll(db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_lp
    tid = _ctid_lp.get() or 1
    r = await db.execute(
        select(PayrollRun).where(PayrollRun.tenant_id == tid).order_by(PayrollRun.week_start.desc()).limit(20)
    )
    runs = r.scalars().all()
    # احسب عدد العمال لكل كشف
    result = []
    for x in runs:
        lines_r = await db.execute(select(PayrollLine).where(PayrollLine.payroll_run_id == x.id))
        worker_count = len(lines_r.scalars().all())
        result.append({
            "id": x.id,
            "week_start": str(x.week_start),
            "week_end": str(x.week_end),
            "total": float(x.total_net),           # الحقل اللي الـ frontend بيقرأه
            "total_gross": float(x.total_gross),
            "total_net": float(x.total_net),
            "worker_count": worker_count,
            "status": x.status,
            "paid_date": str(x.paid_date) if x.paid_date else None,
        })
    return result

@router.post("/payroll", status_code=201)
async def create_payroll(data: PayrollRunIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_pr
    pr_payload = data.model_dump()
    pr_payload['tenant_id'] = _ctid_pr.get() or 1
    run = PayrollRun(**pr_payload); db.add(run); await db.commit(); await db.refresh(run)
    return {"id":run.id,"week_start":str(run.week_start),"week_end":str(run.week_end)}

@router.post("/payroll/{run_id}/generate")
async def generate_from_attendance(run_id: int, db: AsyncSession = Depends(get_db)):
    """
    توليد بنود الرواتب من الحضور.
    *** ربط المشاريع الخارجية بكشف الرواتب ***
    - لو العامل كان حضوره on_project يوم ما → يتحسب بأجر المشروع (daily_rate)
    - لو كان حضوره present في الورشة → يتحسب بأجر الأساسي (daily_wage)
    """
    from app.core.database import current_tenant_id as _ctid_gen
    tid = _ctid_gen.get() or 1
    run = await db.get(PayrollRun, run_id)
    if not run or run.tenant_id != tid: raise HTTPException(404)
    if run.status == "paid":
        raise HTTPException(400, "لا يمكن إعادة توليد كشف مصروف بالفعل")

    workers = (await db.execute(
        select(Worker).where(Worker.is_active==True, Worker.tenant_id==tid)
    )).scalars().all()

    # جلب كل سجلات الحضور في الأسبوع
    att_recs = (await db.execute(
        select(Attendance).where(
            Attendance.date >= run.week_start,
            Attendance.date <= run.week_end,
            Attendance.tenant_id == tid,
        )
    )).scalars().all()

    # جلب أعضاء المشاريع الخارجية النشطة لمعرفة الأجر اليومي
    # ext_project_id → worker_id → daily_rate
    ext_members = (await db.execute(
        select(ExtProjectMember).where(ExtProjectMember.tenant_id == tid)
    )).scalars().all()
    project_rates: dict[tuple, Decimal] = {
        (m.project_id, m.worker_id): m.daily_rate for m in ext_members
    }

    # بناء att_map: worker_id → {days_workshop, days_project, gross_project, overtime_hours}
    att_map: dict[int, dict] = {}
    for a in att_recs:
        if a.worker_id not in att_map:
            att_map[a.worker_id] = {
                "days_workshop": Decimal("0"),
                "days_project": Decimal("0"),
                "gross_project": Decimal("0"),
                "overtime_hours": Decimal("0"),
            }
        # ساعات العمل الإضافي تُحسب بصرف النظر عن حالة اليوم (تُدفع بأجر الورشة الأساسي)
        att_map[a.worker_id]["overtime_hours"] += (a.overtime_hours or Decimal("0"))
        if a.status == "present":
            att_map[a.worker_id]["days_workshop"] += Decimal("1")
        elif a.status == "half":
            att_map[a.worker_id]["days_workshop"] += Decimal("0.5")
        elif a.status == "on_project":
            # أجر المشروع
            rate = Decimal("0")
            if a.ext_project_id:
                rate = project_rates.get((a.ext_project_id, a.worker_id), Decimal("0"))
            att_map[a.worker_id]["days_project"] += Decimal("1")
            att_map[a.worker_id]["gross_project"] += rate
        elif a.status == "half_project":
            rate = Decimal("0")
            if a.ext_project_id:
                rate = project_rates.get((a.ext_project_id, a.worker_id), Decimal("0"))
            att_map[a.worker_id]["days_project"] += Decimal("0.5")
            att_map[a.worker_id]["gross_project"] += rate / 2

    # السلف والمكافآت
    # ملاحظة إصلاح: إعادة توليد كشف لم يُصرف بعد (بعد تعديل الحضور مثلاً) كانت
    # بتفوّت أي سلفة/مكافأة سبق ربطها بنفس الكشف ده تحديدًا في مرة توليد سابقة
    # (لأنها بقت is_settled=True فمابتترجعش في فلتر "غير مسوّاة" تحت) — نلغي
    # ربطها الأول عشان نعيد حسابها من الصفر مع باقي الكشف.
    prev_linked = (await db.execute(
        select(WorkerAdvance).where(WorkerAdvance.payroll_run_id == run_id, WorkerAdvance.tenant_id == tid)
    )).scalars().all()
    for _pl in prev_linked:
        _pl.is_settled = False
        _pl.payroll_run_id = None
    await db.flush()

    adv_recs = (await db.execute(
        select(WorkerAdvance)
        .where(WorkerAdvance.is_settled == False, WorkerAdvance.tenant_id == tid)
        .order_by(WorkerAdvance.date, WorkerAdvance.id)
    )).scalars().all()
    adv_map: dict[int, dict] = {}
    for a in adv_recs:
        bucket = adv_map.setdefault(a.worker_id, {"bonus_ids": [], "debt": []})
        if a.advance_type == "bonus":
            bucket["bonus_ids"].append(a.id)
        else:
            bucket["debt"].append(a)  # "advance" أو "deduction" — بترتيب تاريخي (الأقدم أولاً)

    # حذف البنود القديمة وإعادة الحساب
    old = (await db.execute(
        select(PayrollLine).where(PayrollLine.payroll_run_id == run_id, PayrollLine.tenant_id == tid)
    )).scalars().all()
    for l in old: await db.delete(l)

    run.total_gross = run.total_deductions = run.total_net = Decimal("0")
    result = []

    for w in workers:
        info = att_map.get(w.id, {
            "days_workshop": Decimal("0"),
            "days_project": Decimal("0"),
            "gross_project": Decimal("0"),
            "overtime_hours": Decimal("0"),
        })
        # الأجر الأساسي × أيام الورشة + أجر الساعات الإضافية (بسعر الساعة = يومية/8) + أجر المشروع × أيام المشروع
        overtime_hours = info.get("overtime_hours", Decimal("0"))
        overtime_pay   = (w.daily_wage or Decimal("0")) * overtime_hours / Decimal("8")
        gross_workshop = (w.daily_wage or Decimal("0")) * info["days_workshop"] + overtime_pay
        gross_project  = info["gross_project"]
        gross          = gross_workshop + gross_project
        total_days     = info["days_workshop"] + info["days_project"]

        ai = adv_map.get(w.id, {"bonus_ids": [], "debt": []})

        # المكافآت (bonus) تُسوَّى دائمًا فور تضمينها هنا — بصرف النظر عن وجود
        # سلف مستحقة الخصم — كانت قبل كده بتتسوّى بالصدفة فقط لو "deductible>0"
        # حصل، وإلا كانت بترجع تُحسب وتُدفع تاني في كل كشف رواتب لاحق (نفس
        # المكافأة بتتصرف أكتر من مرة).
        bonus_total = Decimal("0")
        for bid in ai["bonus_ids"]:
            b = await db.get(WorkerAdvance, bid)
            if b:
                bonus_total += b.amount
                b.is_settled = True
                b.payroll_run_id = run_id

        # نسوّي سلف/خصومات العامل (الأقدم أولاً) بالكامل فقط بقدر ما تسمح به
        # سعة الأسبوع (الإجمالي + المكافأة). لا نسوّي جزءًا من سلفة ونعلّمها
        # "مسوّاة بالكامل" — ده كان معناه ضياع باقي المبلغ المستحق للأبد (كان
        # deductible بيتحسب Capped لمنع صافي سالب، لكن كل سلف العامل كانت
        # بتتعلّم settled بغض النظر، حتى لو غُطّي جزء بسيط منها بس). بدل كده:
        # لو مافيش سعة كافية لتغطية سلفة بالكامل، نسيبها زي ما هي لحد ما تتوفر
        # سعة كافية في كشف قادم.
        capacity = gross + bonus_total
        deductible = Decimal("0")
        for adv in ai["debt"]:
            if adv.amount <= capacity:
                capacity -= adv.amount
                deductible += adv.amount
                adv.is_settled = True
                adv.payroll_run_id = run_id
            # وإلا سيبها غير مسوّاة لحد كشف لاحق فيه سعة كافية

        net = gross + bonus_total - deductible

        notes = None
        if info["days_project"] > 0:
            notes = (f"ورشة: {float(info['days_workshop'])} يوم × {float(w.daily_wage)} ج"
                     f" | مشاريع: {float(info['days_project'])} يوم × {float(gross_project/info['days_project'] if info['days_project'] else 0):.0f} ج")
        if overtime_hours > 0:
            ot_note = f"إضافي: {float(overtime_hours)} ساعة × {float((w.daily_wage or Decimal('0'))/8):.2f} ج = {float(overtime_pay):.2f} ج"
            notes = f"{notes} | {ot_note}" if notes else ot_note

        db.add(PayrollLine(
            tenant_id=tid,
            payroll_run_id=run_id, worker_id=w.id,
            days_worked=total_days,
            gross_amount=gross, bonus=bonus_total, deductions=deductible,
            net_amount=net, notes=notes,
        ))
        run.total_gross += gross
        run.total_deductions += deductible
        run.total_net += net

        result.append({
            "worker": w.name,
            "days_workshop": float(info["days_workshop"]),
            "days_project": float(info["days_project"]),
            "overtime_hours": float(overtime_hours),
            "overtime_pay": float(overtime_pay),
            "gross_workshop": float(gross_workshop),
            "gross_project": float(gross_project),
            "total_gross": float(gross),
            "net": float(net),
        })

    await db.commit()
    return {
        "run_id": run_id,
        "workers_processed": len(result),
        "total_gross": float(run.total_gross),
        "total_net": float(run.total_net),
        "lines": result,
    }


@router.post("/payroll/{run_id}/pay")
async def pay_payroll_run(run_id: int, db: AsyncSession = Depends(get_db)):
    """
    صرف الرواتب: يُنفَّذ بعد توليد الكشف ومراجعته.
    يسجل القيد المحاسبي وحركة الخزنة (صادر) ويعلّم الكشف كمصروف.
    """
    from app.core.database import current_tenant_id as _ctid_pr
    _tid_pr = _ctid_pr.get() or 1
    run = await db.get(PayrollRun, run_id)
    if not run or run.tenant_id != _tid_pr: raise HTTPException(404)
    if run.status == "paid":
        raise HTTPException(400, "هذا الكشف مصروف بالفعل")

    lines_count = (await db.execute(
        select(func.count()).select_from(PayrollLine).where(PayrollLine.payroll_run_id == run_id)
    )).scalar() or 0
    if lines_count == 0:
        raise HTTPException(400, "لا يمكن صرف كشف لم يتم توليده بعد")

    import datetime as _dt
    run_code = f"PAY-{run_id}-{run.week_start}"
    pay_date = run.week_end or _dt.date.today()

    if run.total_net > 0 or run.total_gross > 0:
        # ملاحظة إصلاح: كان بيُعاد حساب "advances_settled" من استعلام منفصل
        # (مجموع WorkerAdvance.amount الكاملة لنوع "advance" فقط، is_settled=True)
        # بدل استخدام run.total_deductions المحسوبة فعليًا في generate() — ده كان
        # بيدّي نتيجة مختلفة عن الخصم الحقيقي من صافي الراتب كل ما فيه سلفة من
        # نوع "deduction" (كانت بتتخصم من الصافي لكن مستبعدة من هذا الاستعلام)،
        # فيطلع قيد محاسبي غير متوازن مع الحقيقة الفعلية للراتب المصروف.
        # run.total_deductions هي مصدر الحقيقة الوحيد المتّسق مع ما خُصم فعلاً.
        advances_settled = run.total_deductions or Decimal("0")
        from app.services.auto_journal import journal_payroll
        await journal_payroll(
            db, _tid_pr, run_id, run_code,
            run.total_gross, run.total_net,
            pay_date, advances_settled=Decimal(str(advances_settled)),
        )
        # حركة خزنة: صرف رواتب → خارج
        from app.services.treasury_link import record_payment_in_treasury
        await record_payment_in_treasury(
            db,
            amount=run.total_net,
            direction="out",
            source="payroll",
            linked_type="payroll_run",
            linked_id=run_id,
            category_name="رواتب عمال",
            description=f"صرف رواتب كشف {run_code} — {lines_count} عامل",
            txn_date=pay_date,
        )

    run.status = "paid"
    run.paid_date = pay_date
    await db.commit()
    return {
        "run_id": run_id,
        "status": run.status,
        "paid_date": str(run.paid_date),
        "total_net": float(run.total_net),
    }


@router.get("/payroll/{run_id}")
async def get_payroll_detail(run_id: int, db: AsyncSession = Depends(get_db)):
    """تفاصيل كشف الرواتب — العمال + الأيام + الإجمالي + الصافي"""
    from app.core.database import current_tenant_id as _ctid_gpd
    run = await db.get(PayrollRun, run_id)
    if not run or run.tenant_id != (_ctid_gpd.get() or 1): raise HTTPException(404)
    lines_r = await db.execute(
        select(PayrollLine)
        .options(selectinload(PayrollLine.worker))
        .where(PayrollLine.payroll_run_id == run_id)
        .order_by(PayrollLine.worker_id)
    )
    lines = lines_r.scalars().all()
    return {
        "id":           run.id,
        "week_start":   str(run.week_start),
        "week_end":     str(run.week_end),
        "status":       run.status,
        "paid_date":    str(run.paid_date) if run.paid_date else None,
        "total_gross":  float(run.total_gross),
        "total_net":    float(run.total_net),
        "lines": [
            {
                "id":           l.id,
                "worker_id":    l.worker_id,
                "worker_name":  l.worker.name if l.worker else "—",
                "job_title":    l.worker.job_title if l.worker else None,
                "days_worked":  float(l.days_worked),
                "gross_amount": float(l.gross_amount),
                "bonus":        float(l.bonus),
                "deductions":   float(l.deductions),
                "net_amount":   float(l.net_amount),
                "notes":        l.notes,
            }
            for l in lines
        ],
    }

@router.delete("/payroll/{run_id}")
async def delete_payroll_run(run_id: int, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select as sa_select
    from app.core.database import current_tenant_id as _ctid_dpr
    run = await db.get(PayrollRun, run_id)
    if not run or run.tenant_id != (_ctid_dpr.get() or 1): raise HTTPException(404)
    if run.status == "paid": raise HTTPException(400, "لا يمكن حذف كشف مصروف")
    # أعد تفعيل السلف المرتبطة
    settled_advances = (await db.execute(
        sa_select(WorkerAdvance).where(WorkerAdvance.payroll_run_id == run_id)
    )).scalars().all()
    for adv in settled_advances:
        adv.is_settled = False
        adv.payroll_run_id = None
    lines = (await db.execute(sa_select(PayrollLine).where(PayrollLine.payroll_run_id==run_id))).scalars().all()
    for l in lines: await db.delete(l)
    await db.delete(run)
    await db.commit()
    return {"message": "تم الحذف", "id": run_id}


@router.get("/{wid}/profile")
async def worker_profile(wid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_wp
    tid = _ctid_wp.get() or 1
    w = await db.get(Worker, wid)
    if not w or w.tenant_id != tid: raise HTTPException(404)
    advances = (await db.execute(
        select(WorkerAdvance).where(WorkerAdvance.worker_id == wid, WorkerAdvance.tenant_id == tid)
        .order_by(WorkerAdvance.date.desc())
    )).scalars().all()
    payroll_lines = (await db.execute(
        select(PayrollRun, PayrollLine)
        .join(PayrollLine, PayrollLine.payroll_run_id == PayrollRun.id)
        .where(PayrollLine.worker_id == wid, PayrollLine.tenant_id == tid)
        .order_by(PayrollRun.week_start.desc())
        .limit(10)
    )).all()
    pending_adv   = sum(a.amount for a in advances if not a.is_settled and a.advance_type != "bonus")
    pending_bonus = sum(a.amount for a in advances if not a.is_settled and a.advance_type == "bonus")
    return {
        "worker": wd(w),
        "pending_advances": float(pending_adv),
        "pending_bonus":    float(pending_bonus),
        "advances": [{"id": a.id, "date": str(a.date), "amount": float(a.amount),
                      "advance_type": a.advance_type, "is_settled": a.is_settled, "notes": a.notes}
                     for a in advances],
        "payroll_lines": [{"week_start": str(run.week_start), "week_end": str(run.week_end),
                           "days_worked": float(line.days_worked), "gross_amount": float(line.gross_amount),
                           "deductions": float(line.deductions), "bonus": float(line.bonus),
                           "net_amount": float(line.net_amount), "status": run.status}
                          for run, line in payroll_lines],
    }
