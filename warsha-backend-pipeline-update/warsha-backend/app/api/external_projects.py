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
from app.api.auth import require_any
from app.models.models import (
    ExtProject, ExtProjectMember, ExtProjectTool,
    ExtProjectMaterial, ExtProjectTrip, ExtProjectExpense,
    ExtProjectProgress, ExtProjectPayment,
    Worker, Attendance, Material, Customer,
    CustomerAsset, AssetMovement,
)

router = APIRouter(prefix="/ext-projects", tags=["external-projects"], dependencies=[Depends(require_any)])
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
         .order_by(ExtProject.created_at.desc()))
    rows = (await db.execute(q)).scalars().all()
    return [_proj(p, include_costs=True) for p in rows]


@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db)):
    """KPIs للصفحة الرئيسية"""
    rows = (await db.execute(
        select(ExtProject).options(
            selectinload(ExtProject.team_members),
            selectinload(ExtProject.materials),
            selectinload(ExtProject.trips),
            selectinload(ExtProject.expenses),
            selectinload(ExtProject.payments),
        )
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
    from app.core.database import current_tenant_id as _ctid_ep
    _tid_ep = _ctid_ep.get() or 1
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
        ).where(ExtProject.id == pid)
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
    p = await db.get(ExtProject, pid)
    if not p:
        raise HTTPException(404)
    if data.planned_start and data.planned_end and data.planned_end < data.planned_start:
        raise HTTPException(400, "تاريخ الانتهاء المخطط يجب أن يكون بعد تاريخ البدء")
    for k, v in data.model_dump().items():
        setattr(p, k, v)
    await db.commit()
    return {"message": "تم التحديث"}


_VALID_PROJECT_STATUSES = {"planning", "in_progress", "handover", "closed", "cancelled"}

@router.patch("/{pid}/status")
async def update_status(pid: int, status: str, db: AsyncSession = Depends(get_db)):
    if status not in _VALID_PROJECT_STATUSES:
        raise HTTPException(400, f"حالة غير صالحة. القيم المسموحة: {sorted(_VALID_PROJECT_STATUSES)}")
    p = await db.get(ExtProject, pid)
    if not p:
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
    p = await db.get(ExtProject, pid)
    if not p:
        raise HTTPException(404)
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
    p = await db.get(ExtProject, pid)
    if not p:
        raise HTTPException(404)
    if data.daily_rate is None or data.daily_rate <= 0:
        raise HTTPException(400, "الأجر اليومي يجب أن يكون أكبر من صفر")
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
    m = await db.get(ExtProjectMember, mid)
    if not m or m.project_id != pid:
        raise HTTPException(404)
    m.site_advances = (m.site_advances or Decimal("0")) + amount
    await db.commit()
    return {"site_advances": float(m.site_advances)}


@router.delete("/{pid}/team/{mid}")
async def remove_member(pid: int, mid: int, db: AsyncSession = Depends(get_db)):
    m = await db.get(ExtProjectMember, mid)
    if not m or m.project_id != pid:
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
    يُحدّث جدول attendance الأصلي مع status='on_project' + ext_project_id
    → كشف الرواتب يقرأ منه تلقائياً بالأجر اليومي للمشروع
    """
    p = await db.get(ExtProject, pid)
    if not p:
        raise HTTPException(404)

    for rec in data.records:
        wid = rec["worker_id"]
        status = rec.get("status", "present")

        # ابحث عن سجل الحضور الحالي
        existing = (await db.execute(
            select(Attendance).where(
                Attendance.worker_id == wid,
                Attendance.date == data.work_date,
            )
        )).scalar_one_or_none()

        if existing:
            existing.status = "on_project" if status in ("present", "on_project") else status
            existing.ext_project_id = pid if status in ("present", "on_project") else None
        else:
            att_status = "on_project" if status in ("present", "on_project") else status
            from app.core.database import current_tenant_id as _ctid_epatt
            att = Attendance(
                tenant_id=_ctid_epatt.get() or 1,
                worker_id=wid,
                date=data.work_date,
                status=att_status,
                ext_project_id=pid if att_status == "on_project" else None,
            )
            db.add(att)

        # حدّث actual_days للعضو في المشروع
        if status in ("present", "on_project"):
            member = (await db.execute(
                select(ExtProjectMember).where(
                    ExtProjectMember.project_id == pid,
                    ExtProjectMember.worker_id == wid,
                )
            )).scalar_one_or_none()
            if member:
                # احسب الأيام الفعلية من حضور المشروع
                count = (await db.execute(
                    select(func.count(Attendance.id)).where(
                        Attendance.ext_project_id == pid,
                        Attendance.worker_id == wid,
                        Attendance.status == "on_project",
                    )
                )).scalar() or 0
                member.actual_days = Decimal(str(count + 1))  # +1 لليوم الحالي

        elif status == "half":
            member = (await db.execute(
                select(ExtProjectMember).where(
                    ExtProjectMember.project_id == pid,
                    ExtProjectMember.worker_id == wid,
                )
            )).scalar_one_or_none()
            if member:
                member.actual_days = (member.actual_days or Decimal("0")) + Decimal("0.5")

    await db.commit()
    return {"message": "تم تسجيل الحضور", "date": str(data.work_date)}


@router.get("/{pid}/attendance/{work_date}")
async def get_attendance(pid: int, work_date: date, db: AsyncSession = Depends(get_db)):
    """حضور الفريق في يوم معين"""
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
    _ept_data = data.model_dump()
    _ept_data['tenant_id'] = _ctid_ept.get() or 1
    t = ExtProjectTool(project_id=pid, **_ept_data)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return {"id": t.id, "message": "تمت الإضافة"}


@router.patch("/{pid}/tools/{tid}/return")
async def return_tool(pid: int, tid: int, returned_qty: Decimal,
                       condition_in: str = "good", returned_date: Optional[date] = None,
                       notes: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    t = await db.get(ExtProjectTool, tid)
    if not t or t.project_id != pid:
        raise HTTPException(404)
    t.returned_qty = returned_qty
    t.condition_in = condition_in
    t.returned_date = returned_date or date.today()
    t.status = "returned" if returned_qty >= t.quantity else "damaged"
    if notes:
        t.notes = notes
    await db.commit()
    return {"status": t.status}


@router.delete("/{pid}/tools/{tid}")
async def delete_tool(pid: int, tid: int, db: AsyncSession = Depends(get_db)):
    t = await db.get(ExtProjectTool, tid)
    if not t or t.project_id != pid:
        raise HTTPException(404)
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

    payload = data.model_dump()
    payload["total_cost"] = float(data.quantity) * float(data.unit_cost)
    # لو من المخزون — احسب السعر تلقائياً
    mat = None
    if data.source == "stock" and data.material_id:
        mat = await db.get(Material, data.material_id)
        if mat and float(data.unit_cost) == 0:
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
    m = await db.get(ExtProjectMaterial, mid)
    if not m or m.project_id != pid:
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
    payload = data.model_dump()
    payload["total_cost"] = (
        float(data.fuel_cost) + float(data.toll_cost) +
        float(data.rental_cost) + float(data.other_cost)
    )
    from app.core.database import current_tenant_id as _ctid_eptrip
    payload['tenant_id'] = _ctid_eptrip.get() or 1
    t = ExtProjectTrip(project_id=pid, **payload)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return {"id": t.id, "total_cost": float(t.total_cost)}


@router.delete("/{pid}/trips/{tid}")
async def delete_trip(pid: int, tid: int, db: AsyncSession = Depends(get_db)):
    t = await db.get(ExtProjectTrip, tid)
    if not t or t.project_id != pid:
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
    _epexp_data = data.model_dump()
    _epexp_data['tenant_id'] = _ctid_epexp.get() or 1
    e = ExtProjectExpense(project_id=pid, **_epexp_data)
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return {"id": e.id}


@router.delete("/{pid}/expenses/{eid}")
async def delete_expense(pid: int, eid: int, db: AsyncSession = Depends(get_db)):
    e = await db.get(ExtProjectExpense, eid)
    if not e or e.project_id != pid:
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
    _epprog_data = data.model_dump()
    _epprog_data['tenant_id'] = _ctid_epprog.get() or 1
    r = ExtProjectProgress(project_id=pid, **_epprog_data)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return {"id": r.id}


@router.delete("/{pid}/progress/{rid}")
async def delete_progress(pid: int, rid: int, db: AsyncSession = Depends(get_db)):
    r = await db.get(ExtProjectProgress, rid)
    if not r or r.project_id != pid:
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
    _eppmil_data = data.model_dump()
    _eppmil_data['tenant_id'] = _ctid_eppmil.get() or 1
    pay = ExtProjectPayment(project_id=pid, **_eppmil_data)
    db.add(pay)
    await db.commit()
    await db.refresh(pay)
    return {"id": pay.id}


@router.patch("/{pid}/payments/{payid}/receive")
async def receive_payment(pid: int, payid: int, data: PaymentReceiveIn,
                           db: AsyncSession = Depends(get_db)):
    """تسجيل استلام دفعة"""
    pay = await db.get(ExtProjectPayment, payid)
    if not pay or pay.project_id != pid:
        raise HTTPException(404)
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
    pay = await db.get(ExtProjectPayment, payid)
    if not pay or pay.project_id != pid:
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
    p = (await db.execute(
        select(ExtProject).options(
            selectinload(ExtProject.team_members).selectinload(ExtProjectMember.worker),
            selectinload(ExtProject.materials),
            selectinload(ExtProject.trips),
            selectinload(ExtProject.expenses),
            selectinload(ExtProject.payments),
        ).where(ExtProject.id == pid)
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


@print_router.get("/{pid}/print", response_class=HTMLResponse)
async def print_project_report(pid: int, db: AsyncSession = Depends(get_db)):
    """طباعة تقرير المشروع الخارجي الشامل"""
    p = (await db.execute(
        select(ExtProject).options(
            selectinload(ExtProject.client),
            selectinload(ExtProject.team_members).selectinload(ExtProjectMember.worker),
            selectinload(ExtProject.materials),
            selectinload(ExtProject.trips),
            selectinload(ExtProject.expenses),
            selectinload(ExtProject.payments),
            selectinload(ExtProject.progress_reports),
        ).where(ExtProject.id == pid)
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
            f"<tr><td>{i}</td><td>{m.worker.name if m.worker else ''}</td>"
            f"<td>{m.role or ''}</td><td>{float(m.actual_days):.1f}</td>"
            f"<td>{_egp(m.daily_rate)}</td><td style='font-weight:700'>{_egp(gross)}</td>"
            f"<td style='color:#E87722'>{_egp(m.site_advances)}</td></tr>"
        )

    # صفوف المواد
    mat_rows = ""
    for i, m in enumerate(p.materials, 1):
        src = "مخزن" if m.source == "stock" else "شراء محلي"
        mat_rows += (
            f"<tr><td>{i}</td><td>{m.description or ''}</td>"
            f"<td style='color:#64748B'>{src}</td>"
            f"<td>{float(m.qty):.2f} {m.unit or ''}</td>"
            f"<td style='font-weight:700'>{_egp(m.total_cost)}</td></tr>"
        )

    # صفوف المدفوعات
    pay_rows = ""
    for i, pay in enumerate(p.payments, 1):
        status_ar = {"pending": "معلق", "partial": "جزئي", "paid": "مسدَّد"}.get(pay.status, pay.status)
        pay_rows += (
            f"<tr><td>{i}</td><td>{pay.milestone or ''}</td>"
            f"<td style='color:var(--teal)'>{_egp(pay.due_amount)}</td>"
            f"<td style='color:#22C55E;font-weight:700'>{_egp(pay.paid_amount)}</td>"
            f"<td style='color:#EF4444'>{_egp(float(pay.due_amount)-float(pay.paid_amount))}</td>"
            f"<td>{status_ar}</td></tr>"
        )

    status_map = {"planning": "تخطيط", "active": "جاري", "on_hold": "موقوف",
                  "completed": "منتهي", "cancelled": "ملغي"}
    status_ar = status_map.get(p.status, p.status)

    last_prog = sorted(p.progress_reports, key=lambda x: x.report_date)
    completion = last_prog[-1].completion_pct if last_prog else 0

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<title>تقرير المشروع — {p.code}</title>
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
    <div style="font-family:monospace;font-size:11px;opacity:.6;margin-bottom:4px">{p.code}</div>
    <h1>{p.title}</h1>
    <div class="sub">
      🏢 {p.client.name if p.client else ''} &nbsp;|&nbsp; الحالة: {status_ar}
      {f"&nbsp;|&nbsp; 📍 {p.city}" if p.city else ""}
      {f"، {p.governorate}" if p.governorate else ""}
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
      <div class="info-row"><span class="info-label">إجمالي المستحق:</span><span>{_egp(sum(float(p.due_amount) for p in p.payments))}</span></div>
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
