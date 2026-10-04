"""
External Projects API — المشاريع الخارجية
نظام متكامل لإدارة مشاريع التركيب والصيانة خارج الورشة
"""
from datetime import date
from decimal import Decimal
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.api.auth import require_any, block_viewer_writes
from app.models.models import (
    ExtProject, ExtProjectMember, ExtProjectTool,
    ExtProjectMaterial, ExtProjectTrip, ExtProjectExpense,
    ExtProjectProgress, ExtProjectPayment,
    Worker, Attendance, Material, Customer,
    CustomerAsset, AssetMovement,
)

router = APIRouter(prefix="/ext-projects", tags=["external-projects"], dependencies=[Depends(require_any), Depends(block_viewer_writes)])
print_router = APIRouter(prefix="/ext-projects", tags=["ext-projects-print"], dependencies=[Depends(require_any)])


# ── helpers ────────────────────────────────────────────────────────────────

def _proj(p: ExtProject, include_costs=False) -> dict:
    base = {
        "id": p.id, "code": p.code, "title": p.title,
        "project_type": p.project_type, "status": p.status,
        "client_id": p.client_id,
        "location_id": p.location_id,
        "client_name": p.client.name if p.client else "",
        "manager_id": p.manager_id,
        "governorate": p.governorate, "city": p.city, "address": p.address,
        "distance_km": float(p.distance_km) if p.distance_km else None,
        "equipment_id": p.equipment_id, "equipment_desc": p.equipment_desc,
        "contract_value": float(p.contract_value),
        "planned_start": str(p.planned_start) if p.planned_start else None,
        "planned_end":   str(p.planned_end)   if p.planned_end   else None,
        "actual_start":  str(p.actual_start)  if p.actual_start  else None,
        "actual_end":    str(p.actual_end)    if p.actual_end    else None,
        "warranty_months": p.warranty_months,
        "warranty_end": str(p.warranty_end) if p.warranty_end else None,
        "description": p.description, "notes": p.notes,
        "created_at": str(p.created_at),
    }
    if include_costs:
        labor    = sum(float(m.actual_days) * float(m.daily_rate) for m in p.team_members)
        mats     = sum(float(m.total_cost)  for m in p.materials)
        trips    = sum(float(t.total_cost)  for t in p.trips)
        expenses = sum(float(e.amount)      for e in p.expenses)
        total    = labor + mats + trips + expenses
        paid     = sum(float(pay.paid_amount) for pay in p.payments)
        due      = sum(float(pay.due_amount)  for pay in p.payments)
        base["costs"] = {
            "labor": round(labor, 2),
            "materials": round(mats, 2),
            "trips": round(trips, 2),
            "expenses": round(expenses, 2),
            "total": round(total, 2),
            "margin": round(float(p.contract_value) - total, 2),
            "margin_pct": round((float(p.contract_value) - total) / float(p.contract_value) * 100, 1)
                          if float(p.contract_value) > 0 else 0,
            "collected": round(paid, 2),
            "outstanding": round(due - paid, 2),
        }
        # نسبة الإنجاز من آخر تقرير
        last_prog = sorted(p.progress_reports, key=lambda x: x.report_date)
        base["completion_pct"] = last_prog[-1].completion_pct if last_prog else 0
        base["team_count"] = len(p.team_members)
    return base


# ════════════════════════════════════════════════════════════
# PROJECTS CRUD
# ════════════════════════════════════════════════════════════

class ProjectIn(BaseModel):
    code: str
    title: str
    project_type: str = "installation"
    client_id: int
    location_id: Optional[int] = None
    manager_id: Optional[int] = None
    governorate: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    distance_km: Optional[Decimal] = None
    equipment_id: Optional[int] = None
    equipment_desc: Optional[str] = None
    contract_value: Decimal = Decimal("0")
    contract_ref: Optional[str] = None
    planned_start: Optional[date] = None
    planned_end: Optional[date] = None
    warranty_months: int = 12
    description: Optional[str] = None
    notes: Optional[str] = None


@router.get("")
async def list_projects(db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_lp2
    q = (select(ExtProject)
         .options(
             selectinload(ExtProject.client),
             selectinload(ExtProject.team_members),
             selectinload(ExtProject.materials),
             selectinload(ExtProject.trips),
             selectinload(ExtProject.expenses),
             selectinload(ExtProject.payments),
             selectinload(ExtProject.progress_reports),
         )
         .where(ExtProject.tenant_id == (_ctid_lp2.get() or 1))
         .order_by(ExtProject.created_at.desc()))
    rows = (await db.execute(q)).scalars().all()
    return [_proj(p, include_costs=True) for p in rows]


@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db)):
    """KPIs للصفحة الرئيسية"""
    from app.core.database import current_tenant_id as _ctid_epdash
    rows = (await db.execute(
        select(ExtProject).options(
            selectinload(ExtProject.team_members),
            selectinload(ExtProject.materials),
            selectinload(ExtProject.trips),
            selectinload(ExtProject.expenses),
            selectinload(ExtProject.payments),
        ).where(ExtProject.tenant_id == (_ctid_epdash.get() or 1))
    )).scalars().all()

    total_contract = sum(float(p.contract_value) for p in rows)
    total_cost = 0
    for p in rows:
        total_cost += (
            sum(float(m.actual_days) * float(m.daily_rate) for m in p.team_members) +
            sum(float(m.total_cost) for m in p.materials) +
            sum(float(t.total_cost) for t in p.trips) +
            sum(float(e.amount) for e in p.expenses)
        )
    total_collected = sum(float(pay.paid_amount) for p in rows for pay in p.payments)
    total_outstanding = sum(
        float(pay.due_amount) - float(pay.paid_amount)
        for p in rows for pay in p.payments
        if pay.status != "paid"
    )

    by_status = {}
    for p in rows:
        by_status[p.status] = by_status.get(p.status, 0) + 1

    return {
        "total_projects":   len(rows),
        "total_contract":   round(total_contract, 2),
        "total_cost":       round(total_cost, 2),
        "total_margin":     round(total_contract - total_cost, 2),
        "total_collected":  round(total_collected, 2),
        "total_outstanding":round(total_outstanding, 2),
        "by_status":        by_status,
    }


@router.post("", status_code=201)
async def create_project(data: ProjectIn, db: AsyncSession = Depends(get_db)):
    if data.planned_start and data.planned_end and data.planned_end < data.planned_start:
        raise HTTPException(400, "تاريخ الانتهاء المخطط يجب أن يكون بعد تاريخ البدء")
    if data.contract_value is not None and data.contract_value < 0:
        raise HTTPException(400, "قيمة العقد لا يمكن أن تكون سالبة")
    from app.core.database import current_tenant_id as _ctid_ep
    _tid_ep = _ctid_ep.get() or 1
    if data.warranty_months is not None and data.warranty_months < 0:
        raise HTTPException(400, "مدة الضمان لا يمكن أن تكون سالبة")
    if not (data.code or "").strip():
        raise HTTPException(400, "كود المشروع مطلوب")
    client = await db.get(Customer, data.client_id)
    if not client or client.tenant_id != _tid_ep:
        raise HTTPException(404, "العميل غير موجود")
    # كود المشروع فريد لكل tenant (uq_extproject_tenant_code) — كان التكرار
    # بيوصل لـ IntegrityError ويرجع 500 بدل رسالة واضحة.
    if await db.scalar(select(ExtProject.id).where(
        ExtProject.code == data.code, ExtProject.tenant_id == _tid_ep,
    )):
        raise HTTPException(409, f"كود المشروع {data.code} مستخدم من قبل")
    _ep_data = data.model_dump()
    _ep_data['tenant_id'] = _tid_ep
    p = ExtProject(**_ep_data)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    # أنشئ دفعات افتراضية (مقدم 30% + تسليم 70%)
    db.add(ExtProjectPayment(
        tenant_id=_tid_ep,
        project_id=p.id, milestone="مقدم (30%)", milestone_pct=30,
        due_amount=float(p.contract_value) * 0.3, status="pending"
    ))
    db.add(ExtProjectPayment(
        tenant_id=_tid_ep,
        project_id=p.id, milestone="عند التسليم (70%)", milestone_pct=70,
        due_amount=float(p.contract_value) * 0.7, status="pending"
    ))
    await db.commit()
    result = (await db.execute(
        select(ExtProject).options(selectinload(ExtProject.client)).where(ExtProject.id == p.id)
    )).scalar_one()
    return _proj(result)


@router.get("/{pid}")
async def get_project(pid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_gp
    p = (await db.execute(
        select(ExtProject).options(
            selectinload(ExtProject.client),
            selectinload(ExtProject.team_members).selectinload(ExtProjectMember.worker),
            selectinload(ExtProject.tools),
            selectinload(ExtProject.materials),
            selectinload(ExtProject.trips),
            selectinload(ExtProject.expenses),
            selectinload(ExtProject.progress_reports),
            selectinload(ExtProject.payments),
        ).where(ExtProject.id == pid, ExtProject.tenant_id == (_ctid_gp.get() or 1))
    )).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "المشروع غير موجود")

    data = _proj(p, include_costs=True)

    # ── الفريق مع كشف الحساب ────────────────────────────────
    data["team"] = [{
        "id": m.id, "worker_id": m.worker_id,
        "worker_name": m.worker.name if m.worker else "",
        "worker_code": m.worker.code if m.worker else "",
        "job_title": m.worker.job_title if m.worker else "",
        "role": m.role,
        "daily_rate": float(m.daily_rate),
        "base_daily_wage": float(m.worker.daily_wage) if m.worker else 0,
        "planned_days": m.planned_days,
        "actual_days": float(m.actual_days),
        "gross_amount": round(float(m.actual_days) * float(m.daily_rate), 2),
        "site_advances": float(m.site_advances),
        "net_amount": round(float(m.actual_days) * float(m.daily_rate) - float(m.site_advances), 2),
        "joined_date": str(m.joined_date) if m.joined_date else None,
        "left_date": str(m.left_date) if m.left_date else None,
        "notes": m.notes,
    } for m in p.team_members]

    # ── العدة ───────────────────────────────────────────────
    data["tools"] = [{
        "id": t.id, "tool_name": t.tool_name, "tool_type": t.tool_type,
        "quantity": float(t.quantity), "unit": t.unit,
        "condition_out": t.condition_out, "condition_in": t.condition_in,
        "withdrawn_date": str(t.withdrawn_date) if t.withdrawn_date else None,
        "returned_date": str(t.returned_date) if t.returned_date else None,
        "returned_qty": float(t.returned_qty) if t.returned_qty else None,
        "status": t.status, "notes": t.notes,
    } for t in p.tools]

    # ── المواد ──────────────────────────────────────────────
    data["materials"] = [{
        "id": m.id, "source": m.source,
        "material_id": m.material_id, "description": m.description,
        "quantity": float(m.quantity), "unit": m.unit,
        "unit_cost": float(m.unit_cost), "total_cost": float(m.total_cost),
        "usage_date": str(m.usage_date), "notes": m.notes,
    } for m in p.materials]

    # ── الرحلات ─────────────────────────────────────────────
    data["trips"] = [{
        "id": t.id, "trip_date": str(t.trip_date),
        "trip_type": t.trip_type, "vehicle_type": t.vehicle_type,
        "vehicle_plate": t.vehicle_plate, "driver_name": t.driver_name,
        "from_location": t.from_location, "to_location": t.to_location,
        "distance_km": float(t.distance_km) if t.distance_km else None,
        "passengers": t.passengers,
        "fuel_cost": float(t.fuel_cost), "toll_cost": float(t.toll_cost),
        "rental_cost": float(t.rental_cost), "other_cost": float(t.other_cost),
        "total_cost": float(t.total_cost), "notes": t.notes,
    } for t in p.trips]

    # ── المصاريف اليومية ────────────────────────────────────
    data["expenses"] = [{
        "id": e.id, "expense_date": str(e.expense_date),
        "expense_type": e.expense_type, "description": e.description,
        "amount": float(e.amount), "persons_count": e.persons_count,
        "paid_by": e.paid_by, "receipt_ref": e.receipt_ref, "notes": e.notes,
    } for e in p.expenses]

    # ── تقارير التقدم ────────────────────────────────────────
    data["progress"] = [{
        "id": r.id, "report_date": str(r.report_date),
        "completion_pct": r.completion_pct,
        "work_done": r.work_done, "issues": r.issues,
        "next_steps": r.next_steps, "client_notes": r.client_notes,
        "client_approved": r.client_approved,
        "workers_present": r.workers_present,
    } for r in sorted(p.progress_reports, key=lambda x: x.report_date, reverse=True)]

    # ── الدفعات ──────────────────────────────────────────────
    data["payments"] = [{
        "id": pay.id, "milestone": pay.milestone,
        "milestone_pct": pay.milestone_pct,
        "due_amount": float(pay.due_amount),
        "due_date": str(pay.due_date) if pay.due_date else None,
        "paid_amount": float(pay.paid_amount),
        "paid_date": str(pay.paid_date) if pay.paid_date else None,
        "payment_method": pay.payment_method,
        "reference": pay.reference, "status": pay.status, "notes": pay.notes,
    } for pay in p.payments]

    return data


@router.put("/{pid}")
async def update_project(pid: int, data: ProjectIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_up
    _tid_up = _ctid_up.get() or 1
    p = await db.get(ExtProject, pid)
    if not p or p.tenant_id != _tid_up:
        raise HTTPException(404)
    if data.planned_start and data.planned_end and data.planned_end < data.planned_start:
        raise HTTPException(400, "تاريخ الانتهاء المخطط يجب أن يكون بعد تاريخ البدء")
    if data.contract_value is not None and data.contract_value < 0:
        raise HTTPException(400, "قيمة العقد لا يمكن أن تكون سالبة")
    if data.warranty_months is not None and data.warranty_months < 0:
        raise HTTPException(400, "مدة الضمان لا يمكن أن تكون سالبة")
    if not (data.code or "").strip():
        raise HTTPException(400, "كود المشروع مطلوب")
    if data.client_id != p.client_id:
        client = await db.get(Customer, data.client_id)
        if not client or client.tenant_id != _tid_up:
            raise HTTPException(404, "العميل غير موجود")
    if data.code != p.code and await db.scalar(select(ExtProject.id).where(
        ExtProject.code == data.code, ExtProject.tenant_id == _tid_up, ExtProject.id != pid,
    )):
        raise HTTPException(409, f"كود المشروع {data.code} مستخدم من قبل")
    for k, v in data.model_dump().items():
        setattr(p, k, v)
    await db.commit()
    return {"message": "تم التحديث"}


_VALID_PROJECT_STATUSES = {"draft", "approved", "mobilizing", "in_progress", "testing",
                            "handover", "warranty", "closed", "cancelled"}

@router.patch("/{pid}/status")
async def update_status(pid: int, status: str, db: AsyncSession = Depends(get_db)):
    if status not in _VALID_PROJECT_STATUSES:
        raise HTTPException(400, f"حالة غير صالحة. القيم المسموحة: {sorted(_VALID_PROJECT_STATUSES)}")
    from app.core.database import current_tenant_id as _ctid_us
    p = await db.get(ExtProject, pid)
    if not p or p.tenant_id != (_ctid_us.get() or 1):
        raise HTTPException(404)
    prev_status = p.status
    p.status = status
    if status == "in_progress" and not p.actual_start:
        p.actual_start = date.today()
    if status in ("closed", "handover") and not p.actual_end:
        p.actual_end = date.today()

    # تسجيل حركة الأصل عند التركيب/النقل أو الصيانة
    if p.asset_id and status != prev_status and status in ("handover", "closed"):
        asset = await db.get(CustomerAsset, p.asset_id)
        if asset:
            if p.project_type == "installation":
                from_location_id = asset.current_location_id
                if p.location_id and p.location_id != from_location_id:
                    event_type = "relocated" if from_location_id else "installed"
                    db.add(AssetMovement(
                        tenant_id=p.tenant_id, asset_id=asset.id, event_type=event_type,
                        ref_type="ext_project", ref_id=p.id,
                        from_location_id=from_location_id, to_location_id=p.location_id,
                        event_date=p.actual_end or date.today(),
                        notes=f"تركيب/نقل عبر مشروع {p.code}",
                    ))
                    asset.current_location_id = p.location_id
            elif p.project_type in ("maintenance", "repair"):
                db.add(AssetMovement(
                    tenant_id=p.tenant_id, asset_id=asset.id, event_type="maintained",
                    ref_type="ext_project", ref_id=p.id,
                    event_date=p.actual_end or date.today(),
                    notes=f"صيانة عبر مشروع {p.code}",
                ))

    await db.commit()
    return {"id": pid, "status": status}


@router.delete("/{pid}")
async def delete_project(pid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_dp
    p = await db.get(ExtProject, pid)
    if not p or p.tenant_id != (_ctid_dp.get() or 1):
        raise HTTPException(404)
    # الحذف بيعمل cascade لكل السطور التابعة — لو فيه تحصيل فعلي أو مواد/عدة
    # اتصرفت من المخزون، الحذف كان بيمسح الأثر ده من غير أي عكس (رصيد مخزون
    # ناقص للأبد، ومبالغ محصّلة بتختفي). امنع الحذف لحد ما تتسوّى الأول.
    paid_cnt = await db.scalar(select(func.count(ExtProjectPayment.id)).where(
        ExtProjectPayment.project_id == pid, ExtProjectPayment.paid_amount > 0,
    ))
    if paid_cnt:
        raise HTTPException(409, "لا يمكن حذف مشروع عليه دفعات محصّلة — احذف/اعكس التحصيل أولاً")
    stock_cnt = await db.scalar(select(func.count(ExtProjectMaterial.id)).where(
        ExtProjectMaterial.project_id == pid, ExtProjectMaterial.source == "stock",
        ExtProjectMaterial.material_id.isnot(None),
    ))
    tool_cnt = await db.scalar(select(func.count(ExtProjectTool.id)).where(
        ExtProjectTool.project_id == pid, ExtProjectTool.material_id.isnot(None),
        ExtProjectTool.status == "withdrawn",
    ))
    if stock_cnt or tool_cnt:
        raise HTTPException(409, "لا يمكن حذف مشروع له مواد/عدة مصروفة من المخزون — احذفها أو أرجعها أولاً")
    await db.delete(p)
    await db.commit()
    return {"message": "تم الحذف"}


# ════════════════════════════════════════════════════════════
# TEAM MEMBERS — فريق العمل
# ════════════════════════════════════════════════════════════

class MemberIn(BaseModel):
    worker_id: int
    role: str = "فني"
    daily_rate: Decimal
    planned_days: int = 0
    joined_date: Optional[date] = None
    notes: Optional[str] = None


@router.post("/{pid}/team", status_code=201)
async def add_member(pid: int, data: MemberIn, db: AsyncSession = Depends(get_db)):
    # تحقق إن الورشة موجودة
    from app.core.database import current_tenant_id as _ctid_am
    p = await db.get(ExtProject, pid)
    if not p or p.tenant_id != (_ctid_am.get() or 1):
        raise HTTPException(404)
    if data.daily_rate is None or data.daily_rate <= 0:
        raise HTTPException(400, "الأجر اليومي يجب أن يكون أكبر من صفر")
    if data.planned_days is not None and data.planned_days < 0:
        raise HTTPException(400, "عدد الأيام المخططة لا يمكن أن يكون سالباً")
    worker = await db.get(Worker, data.worker_id)
    if not worker or worker.tenant_id != (_ctid_am.get() or 1):
        raise HTTPException(404, "العامل غير موجود")
    # لو العامل موجود بالفعل — update
    existing = (await db.execute(
        select(ExtProjectMember).where(
            ExtProjectMember.project_id == pid,
            ExtProjectMember.worker_id == data.worker_id
        )
    )).scalar_one_or_none()
    if existing:
        existing.role = data.role
        existing.daily_rate = data.daily_rate
        existing.planned_days = data.planned_days
        if data.joined_date:
            existing.joined_date = data.joined_date
        if data.notes:
            existing.notes = data.notes
        await db.commit()
        return {"id": existing.id, "message": "تم التحديث"}

    from app.core.database import current_tenant_id as _ctid_epm
    _epm_data = data.model_dump()
    _epm_data['tenant_id'] = _ctid_epm.get() or 1
    m = ExtProjectMember(project_id=pid, **_epm_data)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return {"id": m.id, "message": "تمت الإضافة"}


@router.patch("/{pid}/team/{mid}/advance")
async def add_site_advance(pid: int, mid: int, amount: Decimal, db: AsyncSession = Depends(get_db)):
    """تسجيل سلفة في الموقع لعامل"""
    from app.core.database import current_tenant_id as _ctid_asa
    m = await db.get(ExtProjectMember, mid)
    if not m or m.project_id != pid or m.tenant_id != (_ctid_asa.get() or 1):
        raise HTTPException(404)
    if amount is None or amount <= 0:
        raise HTTPException(400, "مبلغ السلفة يجب أن يكون أكبر من صفر")
    m.site_advances = (m.site_advances or Decimal("0")) + amount
    await db.commit()
    return {"site_advances": float(m.site_advances)}


@router.delete("/{pid}/team/{mid}")
async def remove_member(pid: int, mid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_rm
    m = await db.get(ExtProjectMember, mid)
    if not m or m.project_id != pid or m.tenant_id != (_ctid_rm.get() or 1):
        raise HTTPException(404)
    await db.delete(m)
    await db.commit()
    return {"message": "تم الحذف"}


# ════════════════════════════════════════════════════════════
# ATTENDANCE — الحضور اليومي للفريق (مرتبط بكشف الرواتب)
# ════════════════════════════════════════════════════════════

class AttBulkIn(BaseModel):
    work_date: date
    records: List[dict]  # [{worker_id, status: present|absent|half|on_project}]


@router.post("/{pid}/attendance")
async def record_attendance(pid: int, data: AttBulkIn, db: AsyncSession = Depends(get_db)):
    """
    تسجيل حضور يومي للمشروع الخارجي.
    يُحدّث جدول attendance الأصلي مع status='on_project'/'half_project' + ext_project_id
    → كشف الرواتب يقرأ منه تلقائياً بالأجر اليومي للمشروع (راجع workers.generate_from_attendance)

    ملاحظة إصلاح: قبل كده كانت أيام يوم "half" (نص يوم في المشروع) بتتسجل
    بحالة "half" وبدون ext_project_id — يعني تتحسب غلط كأنها نص يوم ورشة
    عادي بدل أجر المشروع، ومفيش أي ربط فعلي بينها وبين المشروع. وكمان
    actual_days كانت بتتحسب "عدد الأيام السابقة + 1" رغم إن autoflush في
    SQLAlchemy بيبعت سجل اليوم الحالي فعلاً قبل تنفيذ استعلام العدّ ده، يعني
    كانت بتتضاعف (double count) كل مرة، وكمان بترجعش لو صححنا يوم من
    on_project لـ absent. دلوقتي بنعيد حساب actual_days بالكامل من جدول
    الحضور الفعلي في كل مرة — مصدر حقيقة واحد ومتّسق دايماً.
    """
    from app.core.database import current_tenant_id as _ctid_epatt
    tid = _ctid_epatt.get() or 1
    p = await db.get(ExtProject, pid)
    if not p or p.tenant_id != tid:
        raise HTTPException(404)

    def _map_status(s: str) -> str:
        if s in ("present", "on_project"):
            return "on_project"
        if s == "half":
            return "half_project"
        return s  # absent | leave | ...

    # تحقق من المدخلات قبل أي كتابة: worker_id لازم يكون موجود ورقمي ويخص نفس
    # الـ tenant (كان rec["worker_id"] الناقص بيرمي KeyError → 500، وworker_id
    # لعامل مش موجود كان بيتسجل له حضور يتيم)، والحالة من القيم المعروفة.
    _allowed_att = {"present", "on_project", "half", "half_project", "absent", "leave"}
    for rec in data.records:
        if not isinstance(rec, dict) or rec.get("worker_id") is None:
            raise HTTPException(400, "كل سجل حضور يجب أن يحتوي على worker_id")
        try:
            rec["worker_id"] = int(rec["worker_id"])
        except (TypeError, ValueError):
            raise HTTPException(400, "worker_id غير صالح")
        if rec.get("status", "present") not in _allowed_att:
            raise HTTPException(400, f"حالة حضور غير صالحة: {rec.get('status')}")
        _w = await db.get(Worker, rec["worker_id"])
        if not _w or _w.tenant_id != tid:
            raise HTTPException(404, f"العامل #{rec['worker_id']} غير موجود")

    for rec in data.records:
        wid = rec["worker_id"]
        raw_status = rec.get("status", "present")
        new_status = _map_status(raw_status)
        is_project_day = new_status in ("on_project", "half_project")

        # ابحث عن سجل الحضور الحالي
        existing = (await db.execute(
            select(Attendance).where(
                Attendance.worker_id == wid,
                Attendance.date == data.work_date,
                Attendance.tenant_id == tid,
            )
        )).scalar_one_or_none()

        if existing:
            existing.status = new_status
            existing.ext_project_id = pid if is_project_day else None
        else:
            att = Attendance(
                tenant_id=tid,
                worker_id=wid,
                date=data.work_date,
                status=new_status,
                ext_project_id=pid if is_project_day else None,
            )
            db.add(att)

        # أعد حساب actual_days من سجلات الحضور الفعلية (مش تراكمي) — يضمن
        # نتيجة صحيحة سواء كانت أول مرة، تكرار لنفس اليوم، أو تصحيح لاحق
        # (زي الرجوع من on_project لـ absent).
        member = (await db.execute(
            select(ExtProjectMember).where(
                ExtProjectMember.project_id == pid,
                ExtProjectMember.worker_id == wid,
            )
        )).scalar_one_or_none()
        if member:
            full_days = (await db.execute(
                select(func.count(Attendance.id)).where(
                    Attendance.ext_project_id == pid,
                    Attendance.worker_id == wid,
                    Attendance.status == "on_project",
                )
            )).scalar() or 0
            half_days = (await db.execute(
                select(func.count(Attendance.id)).where(
                    Attendance.ext_project_id == pid,
                    Attendance.worker_id == wid,
                    Attendance.status == "half_project",
                )
            )).scalar() or 0
            member.actual_days = Decimal(str(full_days)) + Decimal(str(half_days)) * Decimal("0.5")

    await db.commit()
    return {"message": "تم تسجيل الحضور", "date": str(data.work_date)}


@router.get("/{pid}/attendance/{work_date}")
async def get_attendance(pid: int, work_date: date, db: AsyncSession = Depends(get_db)):
    """حضور الفريق في يوم معين"""
    from app.core.database import current_tenant_id as _ctid_ga2
    p = await db.get(ExtProject, pid)
    if not p or p.tenant_id != (_ctid_ga2.get() or 1):
        raise HTTPException(404)
    members = (await db.execute(
        select(ExtProjectMember).options(selectinload(ExtProjectMember.worker))
        .where(ExtProjectMember.project_id == pid)
    )).scalars().all()

    att_recs = (await db.execute(
        select(Attendance).where(
            Attendance.ext_project_id == pid,
            Attendance.date == work_date,
        )
    )).scalars().all()
    att_map = {a.worker_id: a.status for a in att_recs}

    return [{
        "worker_id": m.worker_id,
        "worker_name": m.worker.name if m.worker else "",
        "role": m.role,
        "status": att_map.get(m.worker_id, ""),
    } for m in members]


# ════════════════════════════════════════════════════════════
# TOOLS — العدة والأدوات
# ════════════════════════════════════════════════════════════

class ToolIn(BaseModel):
    tool_name: str
    tool_type: str = "hand_tool"
    material_id: Optional[int] = None
    quantity: Decimal = Decimal("1")
    unit: str = "قطعة"
    condition_out: str = "good"
    withdrawn_by: Optional[int] = None
    withdrawn_date: Optional[date] = None
    notes: Optional[str] = None


@router.post("/{pid}/tools", status_code=201)
async def add_tool(pid: int, data: ToolIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_ept
    tid_ept = _ctid_ept.get() or 1
    p = await db.get(ExtProject, pid)
    if not p or p.tenant_id != tid_ept:
        raise HTTPException(404, "المشروع غير موجود")
    if data.quantity is None or data.quantity <= 0:
        raise HTTPException(400, "الكمية يجب أن تكون أكبر من صفر")
    if data.withdrawn_by:
        _wb = await db.get(Worker, data.withdrawn_by)
        if not _wb or _wb.tenant_id != tid_ept:
            raise HTTPException(404, "العامل المستلم غير موجود")
    mat = None
    if data.material_id:
        mat = await db.get(Material, data.material_id)
        if not mat or mat.tenant_id != tid_ept:
            raise HTTPException(404, "المادة غير موجودة")

    _ept_data = data.model_dump()
    _ept_data['tenant_id'] = tid_ept
    if not _ept_data.get('withdrawn_date'):
        _ept_data['withdrawn_date'] = date.today()
    t = ExtProjectTool(project_id=pid, **_ept_data)
    db.add(t)
    await db.flush()

    # ملاحظة إصلاح: عهدة العدة المرتبطة بمادة مخزون فعلية (material_id) كانت
    # بتتسجل من غير أي خصم من رصيد المخزون — تفضل الأداة محسوبة متاحة في
    # المخزن رغم إنها فعليًا مع فريق المشروع بره الورشة.
    if mat:
        from app.services.inventory_ledger import post_issue
        await post_issue(
            db, material=mat, qty=t.quantity, unit_cost=mat.unit_cost or Decimal("0"),
            movement_date=t.withdrawn_date, source_doc_type="ext_project_tool", source_doc_id=t.id,
            reference=f"EXTPROJ-TOOL-{t.id}", notes=f"سحب عهدة عدة — مشروع خارجي #{pid}",
        )

    await db.commit()
    await db.refresh(t)
    return {"id": t.id, "message": "تمت الإضافة"}


@router.patch("/{pid}/tools/{tid}/return")
async def return_tool(pid: int, tid: int, returned_qty: Decimal,
                       condition_in: str = "good", returned_date: Optional[date] = None,
                       notes: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_rt
    tid_rt = _ctid_rt.get() or 1
    t = await db.get(ExtProjectTool, tid)
    if not t or t.project_id != pid or t.tenant_id != tid_rt:
        raise HTTPException(404)
    if t.status != "withdrawn":
        # كانت الدالة بتسمح بإعادة النداء عليها أكتر من مرة، وده كان هيسبب
        # قيد مخزون مزدوج لو الأداة مرتبطة بمادة (material_id).
        raise HTTPException(400, "تم إرجاع/تسوية هذه العهدة بالفعل")
    if returned_qty < 0:
        raise HTTPException(400, "الكمية المرتجعة لا يمكن أن تكون سالبة")
    if returned_qty > t.quantity:
        raise HTTPException(400, f"الكمية المرتجعة ({returned_qty}) أكبر من الكمية المسحوبة ({t.quantity})")
    t.returned_qty = returned_qty
    t.condition_in = condition_in
    t.returned_date = returned_date or date.today()
    # الحالة تعتمد على اكتمال الكمية المرتجعة *و* حالتها — كانت قبل كده
    # بتتجاهل condition_in تماماً، يعني أداة رجعت كاملة لكن "تالفة" كانت
    # بتتسجل status="returned" غلط.
    if returned_qty < t.quantity:
        t.status = "lost"
    elif condition_in != "good":
        t.status = "damaged"
    else:
        t.status = "returned"
    if notes:
        t.notes = notes

    # نرجّع للمخزون فقط الكمية اللي رجعت فعليًا (returned_qty) — الفرق
    # (t.quantity - returned_qty، "المفقود") يفضل مخصومًا نهائيًا كإعدام
    # (write-off) لجزء من قيمة الأصل، مش مجرد "لسه بره".
    if t.material_id and returned_qty > 0:
        mat = await db.get(Material, t.material_id)
        if mat:
            from app.services.inventory_ledger import post_receipt
            await post_receipt(
                db, material=mat, qty=returned_qty, unit_cost=mat.unit_cost or Decimal("0"),
                movement_date=t.returned_date, source_doc_type="ext_project_tool_return", source_doc_id=t.id,
                reference=f"EXTPROJ-TOOL-{t.id}", notes=f"إرجاع عهدة عدة — مشروع خارجي #{pid}",
            )

    await db.commit()
    return {"status": t.status}


@router.delete("/{pid}/tools/{tid}")
async def delete_tool(pid: int, tid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_delt
    tid_delt = _ctid_delt.get() or 1
    t = await db.get(ExtProjectTool, tid)
    if not t or t.project_id != pid or t.tenant_id != tid_delt:
        raise HTTPException(404)

    # عكس متماثل لخصم المخزون اللي حصل وقت السحب — بس لو لسه في حالة
    # withdrawn (لم يُرجَّع/يُتلَف/يُفقَد بعد، وإلا كنا هنعكس قيد اترجع فعلاً)
    if t.material_id and t.status == "withdrawn":
        mat = await db.get(Material, t.material_id)
        if mat:
            from app.services.inventory_ledger import post_receipt
            await post_receipt(
                db, material=mat, qty=t.quantity, unit_cost=mat.unit_cost or Decimal("0"),
                source_doc_type="ext_project_tool_delete", source_doc_id=t.id,
                reference=f"EXTPROJ-TOOL-{t.id}", notes=f"إلغاء سحب عهدة عدة — مشروع خارجي #{pid}",
            )

    await db.delete(t)
    await db.commit()
    return {"message": "تم الحذف"}


# ════════════════════════════════════════════════════════════
# MATERIALS — المواد المستهلكة
# ════════════════════════════════════════════════════════════

class MaterialIn(BaseModel):
    source: str = "stock"
    material_id: Optional[int] = None
    description: str
    quantity: Decimal
    unit: str = "قطعة"
    unit_cost: Decimal = Decimal("0")
    usage_date: date
    used_by: Optional[int] = None
    receipt_ref: Optional[str] = None
    notes: Optional[str] = None


@router.post("/{pid}/materials", status_code=201)
async def add_material(pid: int, data: MaterialIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_epmat
    tid = _ctid_epmat.get() or 1
    p = await db.get(ExtProject, pid)
    if not p or p.tenant_id != tid:
        raise HTTPException(404, "المشروع غير موجود")
    if data.quantity is None or data.quantity <= 0:
        raise HTTPException(400, "الكمية يجب أن تكون أكبر من صفر")
    if data.unit_cost is not None and data.unit_cost < 0:
        raise HTTPException(400, "تكلفة الوحدة لا يمكن أن تكون سالبة")
    if data.source not in ("stock", "local_purchase"):
        raise HTTPException(400, "مصدر المادة يجب أن يكون stock أو local_purchase")
    if data.used_by:
        _ub = await db.get(Worker, data.used_by)
        if not _ub or _ub.tenant_id != tid:
            raise HTTPException(404, "العامل غير موجود")

    payload = data.model_dump()
    payload["total_cost"] = float(data.quantity) * float(data.unit_cost)
    # لو من المخزون — احسب السعر تلقائياً
    mat = None
    if data.material_id:
        # لو material_id اتبعت صراحة، لازم تتأكد إنها فعلاً موجودة ومتاحة
        # لهذا المستأجر — وإلا الكود كان بيسيب material_id غريب (لمادة تنانت
        # تاني) يتخزن كما هو في ExtProjectMaterial من غير أي تحقق، فيفضل FK
        # يتيم يشاور على صف مايقدرش هذا المستأجر يشوفه أصلاً.
        mat = await db.get(Material, data.material_id)
        if not mat or mat.tenant_id != tid:
            raise HTTPException(404, "المادة غير موجودة")
    if data.source == "stock" and mat:
        if float(data.unit_cost) == 0:
            payload["unit_cost"] = float(mat.unit_cost)
            payload["total_cost"] = float(data.quantity) * float(mat.unit_cost)
            if not data.description:
                payload["description"] = mat.name_ar

    payload['tenant_id'] = tid
    m = ExtProjectMaterial(project_id=pid, **payload)
    db.add(m)

    # محطة 25: لو المادة من المخزون فعليًا، لازم تتخصم فعليًا — قبل كده كان
    # بس بيتسجل سطر تكلفة (cost line) من غير أي أثر مخزني خالص، يعني رصيد
    # المخزون كان بيفضل مسجَّل أعلى من الواقع الفعلي إلى الأبد.
    #
    # ملاحظة تصميم: مش بنحدد source_location_id هنا صراحةً — post_issue()
    # بيرجع تلقائيًا لموقع "DEFAULT" المشترك (محطة 11)، وهو نفس الموقع اللي
    # معظم المخزون الفعلي داخل فيه من شاشة "حركات المخزون" العامة. لو حددنا
    # موقع RAW الخاص بمحطة 19 (المخصص لاستلام أوامر الشراء تحديدًا) كنا
    # هنخصم من مكان المخزون مش موجود فيه فعليًا، ويبقى الرصيد سالب غلط حتى
    # لو الرصيد الكلي (Material.stock_qty) كافي. post_issue() نفسه أصلاً
    # بيعمل فحص الرصيد الكافي (نفس منطق محطة 1) فمش محتاجين نكرره هنا.
    if data.source == "stock" and mat:
        from decimal import Decimal as _D
        from app.services.inventory_ledger import post_issue
        await post_issue(
            db, material=mat, qty=data.quantity, unit_cost=_D(str(payload["unit_cost"])),
            source_doc_type="ext_project", source_doc_id=pid,
            reference=f"EXTPROJ-{pid}", notes=f"استهلاك في مشروع خارجي #{pid}",
        )

    await db.commit()
    await db.refresh(m)
    return {"id": m.id, "total_cost": float(m.total_cost)}


@router.delete("/{pid}/materials/{mid}")
async def delete_material(pid: int, mid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_delmat
    p = await db.get(ExtProject, pid)
    if not p or p.tenant_id != (_ctid_delmat.get() or 1):
        raise HTTPException(404, "المشروع غير موجود")
    m = await db.get(ExtProjectMaterial, mid)
    if not m or m.project_id != pid or m.tenant_id != p.tenant_id:
        raise HTTPException(404)

    # محطة 25: لو كانت اتخصمت فعليًا من المخزون، نرجّعها عند الحذف (عكس متماثل)
    if m.source == "stock" and m.material_id:
        mat = await db.get(Material, m.material_id)
        if mat:
            from app.services.inventory_ledger import post_receipt
            await post_receipt(
                db, material=mat, qty=m.quantity, unit_cost=m.unit_cost or Decimal("0"),
                source_doc_type="ext_project", source_doc_id=pid,
                reference=f"EXTPROJ-{pid}", notes=f"إلغاء استهلاك — مشروع خارجي #{pid}",
            )

    await db.delete(m)
    await db.commit()
    return {"message": "تم الحذف"}


# ════════════════════════════════════════════════════════════
# TRIPS — رحلات المواصلات
# ════════════════════════════════════════════════════════════

class TripIn(BaseModel):
    trip_date: date
    trip_type: str = "team_go"
    vehicle_type: str = "company_van"
    vehicle_plate: Optional[str] = None
    driver_name: Optional[str] = None
    from_location: Optional[str] = None
    to_location: Optional[str] = None
    distance_km: Optional[Decimal] = None
    passengers: int = 1
    fuel_cost: Decimal = Decimal("0")
    toll_cost: Decimal = Decimal("0")
    rental_cost: Decimal = Decimal("0")
    other_cost: Decimal = Decimal("0")
    notes: Optional[str] = None


@router.post("/{pid}/trips", status_code=201)
async def add_trip(pid: int, data: TripIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_eptrip
    tid_eptrip = _ctid_eptrip.get() or 1
    p = await db.get(ExtProject, pid)
    if not p or p.tenant_id != tid_eptrip:
        raise HTTPException(404, "المشروع غير موجود")
    for _v in (data.fuel_cost, data.toll_cost, data.rental_cost, data.other_cost):
        if _v is not None and _v < 0:
            raise HTTPException(400, "تكاليف الرحلة لا يمكن أن تكون سالبة")
    if data.distance_km is not None and data.distance_km < 0:
        raise HTTPException(400, "المسافة لا يمكن أن تكون سالبة")
    if data.passengers is not None and data.passengers < 0:
        raise HTTPException(400, "عدد الركاب لا يمكن أن يكون سالباً")
    payload = data.model_dump()
    payload["total_cost"] = (
        float(data.fuel_cost) + float(data.toll_cost) +
        float(data.rental_cost) + float(data.other_cost)
    )
    payload['tenant_id'] = tid_eptrip
    t = ExtProjectTrip(project_id=pid, **payload)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return {"id": t.id, "total_cost": float(t.total_cost)}


@router.delete("/{pid}/trips/{tid}")
async def delete_trip(pid: int, tid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_delt2
    t = await db.get(ExtProjectTrip, tid)
    if not t or t.project_id != pid or t.tenant_id != (_ctid_delt2.get() or 1):
        raise HTTPException(404)
    await db.delete(t)
    await db.commit()
    return {"message": "تم الحذف"}


# ════════════════════════════════════════════════════════════
# EXPENSES — المصاريف اليومية
# ════════════════════════════════════════════════════════════

class ExpenseIn(BaseModel):
    expense_date: date
    expense_type: str = "food"
    description: str
    amount: Decimal
    persons_count: int = 1
    paid_by: Optional[int] = None
    receipt_ref: Optional[str] = None
    notes: Optional[str] = None


@router.post("/{pid}/expenses", status_code=201)
async def add_expense(pid: int, data: ExpenseIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_epexp
    tid_epexp = _ctid_epexp.get() or 1
    p = await db.get(ExtProject, pid)
    if not p or p.tenant_id != tid_epexp:
        raise HTTPException(404, "المشروع غير موجود")
    if data.amount is None or data.amount <= 0:
        raise HTTPException(400, "المبلغ يجب أن يكون أكبر من صفر")
    if data.persons_count is not None and data.persons_count < 0:
        raise HTTPException(400, "عدد الأفراد لا يمكن أن يكون سالباً")
    if data.paid_by:
        _pb = await db.get(Worker, data.paid_by)
        if not _pb or _pb.tenant_id != tid_epexp:
            raise HTTPException(404, "العامل غير موجود")
    _epexp_data = data.model_dump()
    _epexp_data['tenant_id'] = tid_epexp
    e = ExtProjectExpense(project_id=pid, **_epexp_data)
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return {"id": e.id}


@router.delete("/{pid}/expenses/{eid}")
async def delete_expense(pid: int, eid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_dele
    e = await db.get(ExtProjectExpense, eid)
    if not e or e.project_id != pid or e.tenant_id != (_ctid_dele.get() or 1):
        raise HTTPException(404)
    await db.delete(e)
    await db.commit()
    return {"message": "تم الحذف"}


# ════════════════════════════════════════════════════════════
# PROGRESS REPORTS — تقارير التقدم
# ════════════════════════════════════════════════════════════

class ProgressIn(BaseModel):
    report_date: date
    completion_pct: int
    work_done: Optional[str] = None
    issues: Optional[str] = None
    next_steps: Optional[str] = None
    client_notes: Optional[str] = None
    client_approved: bool = False
    workers_present: int = 0
    reporter_id: Optional[int] = None


@router.post("/{pid}/progress", status_code=201)
async def add_progress(pid: int, data: ProgressIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_epprog
    tid_epprog = _ctid_epprog.get() or 1
    p = await db.get(ExtProject, pid)
    if not p or p.tenant_id != tid_epprog:
        raise HTTPException(404, "المشروع غير موجود")
    if data.completion_pct is None or not (0 <= data.completion_pct <= 100):
        raise HTTPException(400, "نسبة الإنجاز يجب أن تكون بين 0 و 100")
    if data.workers_present is not None and data.workers_present < 0:
        raise HTTPException(400, "عدد العمال الحاضرين لا يمكن أن يكون سالباً")
    if data.reporter_id:
        _rp = await db.get(Worker, data.reporter_id)
        if not _rp or _rp.tenant_id != tid_epprog:
            raise HTTPException(404, "العامل غير موجود")
    _epprog_data = data.model_dump()
    _epprog_data['tenant_id'] = tid_epprog
    r = ExtProjectProgress(project_id=pid, **_epprog_data)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return {"id": r.id}


@router.delete("/{pid}/progress/{rid}")
async def delete_progress(pid: int, rid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_delp
    r = await db.get(ExtProjectProgress, rid)
    if not r or r.project_id != pid or r.tenant_id != (_ctid_delp.get() or 1):
        raise HTTPException(404)
    await db.delete(r)
    await db.commit()
    return {"message": "تم الحذف"}


# ════════════════════════════════════════════════════════════
# PAYMENTS — التحصيل بالمراحل
# ════════════════════════════════════════════════════════════

class PaymentIn(BaseModel):
    milestone: str
    milestone_pct: int = 0
    due_amount: Decimal
    due_date: Optional[date] = None
    notes: Optional[str] = None


class PaymentReceiveIn(BaseModel):
    paid_amount: Decimal
    payment_method: str = "cash"
    reference: Optional[str] = None
    paid_date: Optional[date] = None


@router.post("/{pid}/payments", status_code=201)
async def add_payment_milestone(pid: int, data: PaymentIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_eppmil
    tid_eppmil = _ctid_eppmil.get() or 1
    p = await db.get(ExtProject, pid)
    if not p or p.tenant_id != tid_eppmil:
        raise HTTPException(404, "المشروع غير موجود")
    if data.due_amount is None or data.due_amount <= 0:
        raise HTTPException(400, "المبلغ المستحق يجب أن يكون أكبر من صفر")
    if data.milestone_pct is not None and not (0 <= data.milestone_pct <= 100):
        raise HTTPException(400, "نسبة المرحلة يجب أن تكون بين 0 و 100")
    if not (data.milestone or "").strip():
        raise HTTPException(400, "اسم المرحلة مطلوب")
    _eppmil_data = data.model_dump()
    _eppmil_data['tenant_id'] = tid_eppmil
    pay = ExtProjectPayment(project_id=pid, **_eppmil_data)
    db.add(pay)
    await db.commit()
    await db.refresh(pay)
    return {"id": pay.id}


@router.patch("/{pid}/payments/{payid}/receive")
async def receive_payment(pid: int, payid: int, data: PaymentReceiveIn,
                           db: AsyncSession = Depends(get_db)):
    """تسجيل استلام دفعة"""
    from app.core.database import current_tenant_id as _ctid_recv
    if data.paid_amount is None or data.paid_amount <= 0:
        raise HTTPException(400, "المبلغ المحصَّل يجب أن يكون أكبر من صفر")
    pay = await db.get(ExtProjectPayment, payid)
    if not pay or pay.project_id != pid or pay.tenant_id != (_ctid_recv.get() or 1):
        raise HTTPException(404)
    # منع التحصيل الزائد: كان ممكن تسجل أي مبلغ فوق المستحق بلا حد، فيتضخم
    # "المحصَّل" ويطلع "المتبقي" بالسالب في كل التقارير.
    _remaining = (pay.due_amount or Decimal("0")) - (pay.paid_amount or Decimal("0"))
    if data.paid_amount > _remaining + Decimal("0.01"):
        raise HTTPException(
            400,
            f"المبلغ المحصَّل ({data.paid_amount}) أكبر من المتبقي على المرحلة ({_remaining})",
        )
    pay.paid_amount = (pay.paid_amount or Decimal("0")) + data.paid_amount
    pay.payment_method = data.payment_method
    pay.reference = data.reference
    pay.paid_date = data.paid_date or date.today()
    pay.status = "paid" if float(pay.paid_amount) >= float(pay.due_amount) else "partial"
    await db.commit()
    return {
        "paid_amount": float(pay.paid_amount),
        "status": pay.status,
    }


@router.delete("/{pid}/payments/{payid}")
async def delete_payment(pid: int, payid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_delpay
    pay = await db.get(ExtProjectPayment, payid)
    if not pay or pay.project_id != pid or pay.tenant_id != (_ctid_delpay.get() or 1):
        raise HTTPException(404)
    await db.delete(pay)
    await db.commit()
    return {"message": "تم الحذف"}


# ════════════════════════════════════════════════════════════
# COST SUMMARY — ملخص التكاليف التفصيلي
# ════════════════════════════════════════════════════════════

@router.get("/{pid}/cost-summary")
async def cost_summary(pid: int, db: AsyncSession = Depends(get_db)):
    """ملخص تكاليف مفصّل بالتصنيف — للتقارير والطباعة"""
    from app.core.database import current_tenant_id as _ctid_cs
    p = (await db.execute(
        select(ExtProject).options(
            selectinload(ExtProject.team_members).selectinload(ExtProjectMember.worker),
            selectinload(ExtProject.materials),
            selectinload(ExtProject.trips),
            selectinload(ExtProject.expenses),
            selectinload(ExtProject.payments),
        ).where(ExtProject.id == pid, ExtProject.tenant_id == (_ctid_cs.get() or 1))
    )).scalar_one_or_none()
    if not p:
        raise HTTPException(404)

    # عمالة
    labor_lines = [{
        "worker": m.worker.name if m.worker else "",
        "role": m.role,
        "days": float(m.actual_days),
        "daily_rate": float(m.daily_rate),
        "gross": round(float(m.actual_days) * float(m.daily_rate), 2),
        "advances": float(m.site_advances),
        "net": round(float(m.actual_days) * float(m.daily_rate) - float(m.site_advances), 2),
    } for m in p.team_members]
    labor_total = sum(x["gross"] for x in labor_lines)

    # مواد
    mat_stock   = sum(float(m.total_cost) for m in p.materials if m.source == "stock")
    mat_local   = sum(float(m.total_cost) for m in p.materials if m.source == "local_purchase")
    mat_total   = mat_stock + mat_local

    # مواصلات
    trip_by_type = {}
    for t in p.trips:
        trip_by_type[t.trip_type] = trip_by_type.get(t.trip_type, 0) + float(t.total_cost)
    trip_total = sum(float(t.total_cost) for t in p.trips)

    # مصاريف يومية
    exp_by_type = {}
    for e in p.expenses:
        exp_by_type[e.expense_type] = exp_by_type.get(e.expense_type, 0) + float(e.amount)
    exp_total = sum(float(e.amount) for e in p.expenses)

    total_cost = labor_total + mat_total + trip_total + exp_total
    contract   = float(p.contract_value)
    margin     = contract - total_cost
    collected  = sum(float(pay.paid_amount) for pay in p.payments)
    outstanding= sum(float(pay.due_amount) - float(pay.paid_amount) for pay in p.payments)

    return {
        "project_code": p.code,
        "project_title": p.title,
        "contract_value": contract,
        "labor": {
            "total": round(labor_total, 2),
            "lines": labor_lines,
        },
        "materials": {
            "total": round(mat_total, 2),
            "from_stock": round(mat_stock, 2),
            "local_purchase": round(mat_local, 2),
        },
        "trips": {
            "total": round(trip_total, 2),
            "by_type": trip_by_type,
        },
        "expenses": {
            "total": round(exp_total, 2),
            "by_type": exp_by_type,
        },
        "total_cost": round(total_cost, 2),
        "margin": round(margin, 2),
        "margin_pct": round(margin / contract * 100, 1) if contract > 0 else 0,
        "collected": round(collected, 2),
        "outstanding": round(outstanding, 2),
    }


# ════════════════════════════════════════════════════════════
# PRINT — تقرير المشروع الخارجي
# ════════════════════════════════════════════════════════════

def _egp(v) -> str:
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return "0.00"


def _esc(s) -> str:
    """HTML-escape لبيانات المستخدم داخل صفحة الطباعة (منع XSS مخزَّن)."""
    import html as _h
    return _h.escape(str(s if s is not None else ""))


@print_router.get("/{pid}/print", response_class=HTMLResponse)
async def print_project_report(pid: int, db: AsyncSession = Depends(get_db)):
    """طباعة تقرير المشروع الخارجي الشامل"""
    from app.core.database import current_tenant_id as _ctid_pr
    p = (await db.execute(
        select(ExtProject).options(
            selectinload(ExtProject.client),
            selectinload(ExtProject.team_members).selectinload(ExtProjectMember.worker),
            selectinload(ExtProject.materials),
            selectinload(ExtProject.trips),
            selectinload(ExtProject.expenses),
            selectinload(ExtProject.payments),
            selectinload(ExtProject.progress_reports),
        ).where(ExtProject.id == pid, ExtProject.tenant_id == (_ctid_pr.get() or 1))
    )).scalar_one_or_none()
    if not p:
        raise HTTPException(404)

    # حسابات التكاليف
    labor_total = sum(float(m.actual_days) * float(m.daily_rate) for m in p.team_members)
    mat_total   = sum(float(m.total_cost) for m in p.materials)
    trip_total  = sum(float(t.total_cost) for t in p.trips)
    exp_total   = sum(float(e.amount) for e in p.expenses)
    total_cost  = labor_total + mat_total + trip_total + exp_total
    contract    = float(p.contract_value)
    margin      = contract - total_cost
    margin_pct  = round(margin / contract * 100, 1) if contract > 0 else 0
    collected   = sum(float(pay.paid_amount) for pay in p.payments)
    outstanding = sum(float(pay.due_amount) - float(pay.paid_amount) for pay in p.payments)

    # صفوف الفريق
    team_rows = ""
    for i, m in enumerate(p.team_members, 1):
        gross = float(m.actual_days) * float(m.daily_rate)
        team_rows += (
            f"<tr><td>{i}</td><td>{_esc(m.worker.name if m.worker else '')}</td>"
            f"<td>{_esc(m.role or '')}</td><td>{float(m.actual_days):.1f}</td>"
            f"<td>{_egp(m.daily_rate)}</td><td style='font-weight:700'>{_egp(gross)}</td>"
            f"<td style='color:#E87722'>{_egp(m.site_advances)}</td></tr>"
        )

    # صفوف المواد
    mat_rows = ""
    for i, m in enumerate(p.materials, 1):
        src = "مخزن" if m.source == "stock" else "شراء محلي"
        mat_rows += (
            f"<tr><td>{i}</td><td>{_esc(m.description or '')}</td>"
            f"<td style='color:#64748B'>{src}</td>"
            f"<td>{float(m.quantity or 0):.2f} {_esc(m.unit or '')}</td>"
            f"<td style='font-weight:700'>{_egp(m.total_cost)}</td></tr>"
        )

    # صفوف المدفوعات
    pay_rows = ""
    for i, pay in enumerate(p.payments, 1):
        status_ar = {"pending": "معلق", "partial": "جزئي", "paid": "مسدَّد"}.get(pay.status, pay.status)
        pay_rows += (
            f"<tr><td>{i}</td><td>{_esc(pay.milestone or '')}</td>"
            f"<td style='color:var(--teal)'>{_egp(pay.due_amount)}</td>"
            f"<td style='color:#22C55E;font-weight:700'>{_egp(pay.paid_amount)}</td>"
            f"<td style='color:#EF4444'>{_egp(float(pay.due_amount)-float(pay.paid_amount))}</td>"
            f"<td>{_esc(status_ar)}</td></tr>"
        )

    status_map = {"planning": "تخطيط", "active": "جاري", "on_hold": "موقوف",
                  "completed": "منتهي", "cancelled": "ملغي",
                  # الحالات الفعلية المستخدمة في _VALID_PROJECT_STATUSES
                  "draft": "مسودة", "approved": "معتمد", "mobilizing": "تجهيز",
                  "in_progress": "جاري التنفيذ", "testing": "اختبار", "handover": "تسليم",
                  "warranty": "ضمان", "closed": "مغلق"}
    status_ar = status_map.get(p.status, p.status)

    last_prog = sorted(p.progress_reports, key=lambda x: x.report_date)
    completion = last_prog[-1].completion_pct if last_prog else 0

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<title>تقرير المشروع — {_esc(p.code)}</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@300;400;500;600;700&display=swap" rel="stylesheet"/>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'IBM Plex Sans Arabic',sans-serif;background:#fff;color:#1e293b;font-size:13px;direction:rtl}}
.page{{max-width:900px;margin:0 auto;padding:24px}}
.header{{background:linear-gradient(135deg,#0B1F3A,#1a3a6e);color:#fff;padding:24px;border-radius:12px;margin-bottom:20px}}
.header h1{{font-size:20px;margin-bottom:4px}}
.header .sub{{opacity:.7;font-size:13px}}
.kpi-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}}
.kpi{{background:#f8f9fa;border:1px solid #e2e8f0;border-radius:8px;padding:12px;text-align:center}}
.kpi-label{{font-size:11px;color:#64748B;margin-bottom:4px}}
.kpi-value{{font-size:16px;font-weight:700}}
.section{{margin-bottom:20px}}
.section-title{{font-size:14px;font-weight:700;color:#0B1F3A;border-bottom:2px solid #E87722;padding-bottom:6px;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#0B1F3A;color:#fff;padding:8px 10px;text-align:right;font-weight:600}}
td{{padding:7px 10px;border-bottom:1px solid #e2e8f0}}
tr:nth-child(even){{background:#f8f9fa}}
.info-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px}}
.info-box{{background:#f8f9fa;border:1px solid #e2e8f0;border-radius:8px;padding:12px}}
.info-row{{display:flex;gap:8px;padding:4px 0;border-bottom:1px solid #e2e8f0;font-size:12px}}
.info-row:last-child{{border-bottom:none}}
.info-label{{font-weight:600;min-width:120px;color:#475569}}
.progress-bar{{background:#e2e8f0;border-radius:10px;height:14px;overflow:hidden;margin:8px 0}}
.progress-fill{{height:100%;background:linear-gradient(90deg,#E87722,#f59e0b);border-radius:10px;transition:width .3s}}
@media print{{body{{font-size:11px}}.page{{padding:10px}}}}
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div style="font-family:monospace;font-size:11px;opacity:.6;margin-bottom:4px">{_esc(p.code)}</div>
    <h1>{_esc(p.title)}</h1>
    <div class="sub">
      🏢 {_esc(p.client.name if p.client else '')} &nbsp;|&nbsp; الحالة: {_esc(status_ar)}
      {f"&nbsp;|&nbsp; 📍 {_esc(p.city)}" if p.city else ""}
      {f"، {_esc(p.governorate)}" if p.governorate else ""}
    </div>
  </div>

  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-label">قيمة العقد</div><div class="kpi-value" style="color:#0B1F3A">{_egp(contract)}</div></div>
    <div class="kpi"><div class="kpi-label">إجمالي التكاليف</div><div class="kpi-value" style="color:#F59E0B">{_egp(total_cost)}</div></div>
    <div class="kpi"><div class="kpi-label">هامش الربح</div><div class="kpi-value" style="color:{'#22C55E' if margin >= 0 else '#EF4444'}">{_egp(margin)} ({margin_pct}%)</div></div>
    <div class="kpi"><div class="kpi-label">المحصَّل</div><div class="kpi-value" style="color:#22C55E">{_egp(collected)}</div></div>
  </div>

  <div class="info-grid">
    <div class="info-box">
      <div class="section-title">بيانات المشروع</div>
      <div class="info-row"><span class="info-label">البدء المخطط:</span><span>{str(p.planned_start) if p.planned_start else '—'}</span></div>
      <div class="info-row"><span class="info-label">الانتهاء المخطط:</span><span>{str(p.planned_end) if p.planned_end else '—'}</span></div>
      <div class="info-row"><span class="info-label">البدء الفعلي:</span><span>{str(p.actual_start) if p.actual_start else '—'}</span></div>
      <div class="info-row"><span class="info-label">الانتهاء الفعلي:</span><span>{str(p.actual_end) if p.actual_end else '—'}</span></div>
      <div class="info-row"><span class="info-label">ضمان (أشهر):</span><span>{p.warranty_months or 0}</span></div>
    </div>
    <div class="info-box">
      <div class="section-title">التقدم والتحصيل</div>
      <div style="font-size:12px;color:#475569;margin-bottom:4px">نسبة الإنجاز: <b>{completion}%</b></div>
      <div class="progress-bar"><div class="progress-fill" style="width:{min(100,completion)}%"></div></div>
      <div style="height:8px"></div>
      <div class="info-row"><span class="info-label">إجمالي المستحق:</span><span>{_egp(sum(float(_pay.due_amount) for _pay in p.payments))}</span></div>
      <div class="info-row"><span class="info-label">المحصَّل:</span><span style="color:#22C55E;font-weight:700">{_egp(collected)}</span></div>
      <div class="info-row"><span class="info-label">المتبقي:</span><span style="color:#EF4444;font-weight:700">{_egp(outstanding)}</span></div>
    </div>
  </div>

  {'<div class="section"><div class="section-title">👷 الفريق والعمالة</div><table><thead><tr><th>#</th><th>الاسم</th><th>الدور</th><th>الأيام</th><th>اليومية</th><th>الإجمالي</th><th>السلف</th></tr></thead><tbody>' + team_rows + f'</tbody><tfoot><tr><td colspan="5" style="font-weight:700;text-align:left">الإجمالي</td><td style="font-weight:700;color:#0B1F3A">{_egp(labor_total)}</td><td></td></tr></tfoot></table></div>' if p.team_members else ''}

  {'<div class="section"><div class="section-title">🧱 المواد المستخدمة</div><table><thead><tr><th>#</th><th>البيان</th><th>المصدر</th><th>الكمية</th><th>التكلفة</th></tr></thead><tbody>' + mat_rows + f'</tbody><tfoot><tr><td colspan="4" style="font-weight:700;text-align:left">الإجمالي</td><td style="font-weight:700;color:#0B1F3A">{_egp(mat_total)}</td></tr></tfoot></table></div>' if p.materials else ''}

  {'<div class="section"><div class="section-title">💳 مراحل التحصيل</div><table><thead><tr><th>#</th><th>المرحلة</th><th>المستحق</th><th>المحصَّل</th><th>المتبقي</th><th>الحالة</th></tr></thead><tbody>' + pay_rows + '</tbody></table></div>' if p.payments else ''}

  <div style="margin-top:24px;border-top:1px solid #e2e8f0;padding-top:16px;display:flex;justify-content:space-around">
    <div style="text-align:center">
      <div style="font-size:11px;color:#64748B;margin-bottom:30px">مدير المشروع</div>
      <div style="border-top:1px solid #333;width:160px;padding-top:4px;font-size:11px">التوقيع</div>
    </div>
    <div style="text-align:center">
      <div style="font-size:11px;color:#64748B;margin-bottom:30px">المدير العام</div>
      <div style="border-top:1px solid #333;width:160px;padding-top:4px;font-size:11px">التوقيع</div>
    </div>
  </div>

  <script>window.onload = () => window.print()</script>
</div>
</body>
</html>"""
