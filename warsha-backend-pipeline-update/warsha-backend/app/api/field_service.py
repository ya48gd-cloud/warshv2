"""
Field Service API — الخدمة الميدانية
تتبع الفريق الخارجي: تركيب، صيانة، فحص، إصلاح
"""
from datetime import date
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import (
    FieldServiceOrder, ServiceAssignment, ServiceVisit,
    CustomerAsset, Customer, Worker, AssetMovement,
)

router = APIRouter(prefix="/field-service", tags=["field-service"], dependencies=[Depends(require_any)])


# ── Schemas ────────────────────────────────────────────────────

class AssetIn(BaseModel):
    customer_id: int
    equipment_id: Optional[int] = None
    name: str
    model: Optional[str] = None
    serial_number: Optional[str] = None
    delivery_date: Optional[date] = None
    warranty_end: Optional[date] = None
    location: Optional[str] = None
    notes: Optional[str] = None


class ServiceOrderIn(BaseModel):
    code: str
    customer_id: int
    location_id: Optional[int] = None
    asset_id: Optional[int] = None
    order_type: str = "maintenance"
    title: str
    description: Optional[str] = None
    priority: str = "normal"
    planned_date: Optional[date] = None
    estimated_hours: Optional[float] = None
    notes: Optional[str] = None


class AssignIn(BaseModel):
    worker_ids: List[int]
    role: str = "technician"
    notes: Optional[str] = None


class VisitIn(BaseModel):
    visit_date: date
    arrival_time: Optional[str] = None
    departure_time: Optional[str] = None
    labor_hours: Optional[float] = None
    work_done: Optional[str] = None
    materials_used: Optional[str] = None
    next_action: Optional[str] = None
    status: str = "completed"


_VALID_MOVEMENT_TYPES = {"manufactured", "delivered", "installed", "relocated", "maintained", "returned"}

class AssetMovementIn(BaseModel):
    event_type: str
    to_location_id: Optional[int] = None
    event_date: Optional[date] = None
    notes: Optional[str] = None


_VALID_FS_STATUSES = {"pending", "in_progress", "completed", "cancelled", "on_hold"}

class StatusUpdate(BaseModel):
    status: str
    completion_date: Optional[date] = None
    notes: Optional[str] = None

    def validate_status(self):
        if self.status not in _VALID_FS_STATUSES:
            raise ValueError(f"حالة غير صالحة: {self.status}")


# ── Helpers ────────────────────────────────────────────────────

ORDER_TYPE_AR = {
    "installation":  "تركيب",
    "maintenance":   "صيانة دورية",
    "repair":        "إصلاح طارئ",
    "inspection":    "فحص",
    "commissioning": "تشغيل أولي",
    "training":      "تدريب",
    "other":         "أخرى",
}

PRIORITY_AR = {
    "urgent": "عاجل", "high": "مرتفع",
    "normal": "عادي", "low": "منخفض",
}

STATUS_AR = {
    "pending":    "معلق",
    "assigned":   "مُكلَّف",
    "in_progress": "جاري",
    "on_hold":    "متوقف",
    "completed":  "مكتمل",
    "cancelled":  "ملغي",
}


def order_dict(o: FieldServiceOrder) -> dict:
    return {
        "id": o.id, "code": o.code,
        "customer_id": o.customer_id,
        "customer_name": o.customer.name if o.customer else "",
        "location_id": o.location_id,
        "location_name": o.location.name if getattr(o, "location", None) else None,
        "asset_id": o.asset_id,
        "asset_name": o.asset.name if o.asset else None,
        "order_type": o.order_type,
        "order_type_ar": ORDER_TYPE_AR.get(o.order_type, o.order_type),
        "title": o.title,
        "description": o.description,
        "priority": o.priority,
        "priority_ar": PRIORITY_AR.get(o.priority, o.priority),
        "status": o.status,
        "status_ar": STATUS_AR.get(o.status, o.status),
        "planned_date": str(o.planned_date) if o.planned_date else None,
        "completion_date": str(o.completion_date) if o.completion_date else None,
        "estimated_hours": float(o.estimated_hours) if o.estimated_hours else None,
        "actual_hours": float(o.actual_hours) if o.actual_hours else None,
        "labor_cost": float(o.labor_cost),
        "materials_cost": float(o.materials_cost),
        "total_cost": float(o.total_cost),
        "notes": o.notes,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "assignments": [{
            "id": a.id,
            "worker_id": a.worker_id,
            "worker_name": a.worker.name if a.worker else "",
            "role": a.role,
            "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None,
        } for a in (o.assignments or [])],
        "visits": [{
            "id": v.id,
            "visit_date": str(v.visit_date),
            "arrival_time": v.arrival_time,
            "departure_time": v.departure_time,
            "labor_hours": float(v.labor_hours) if v.labor_hours else None,
            "work_done": v.work_done,
            "materials_used": v.materials_used,
            "next_action": v.next_action,
            "status": v.status,
        } for v in (o.visits or [])],
    }


MOVEMENT_TYPE_AR = {
    "manufactured": "تصنيع",
    "delivered":    "تسليم للعميل",
    "installed":    "تركيب",
    "relocated":    "نقل لموقع آخر",
    "maintained":   "صيانة",
    "returned":     "إرجاع",
}


def asset_dict(a: CustomerAsset) -> dict:
    from datetime import date as dt
    in_warranty = (a.warranty_end and a.warranty_end >= dt.today()) if a.warranty_end else False
    return {
        "id": a.id,
        "customer_id": a.customer_id,
        "customer_name": a.customer.name if a.customer else "",
        "equipment_id": a.equipment_id,
        "name": a.name,
        "model": a.model,
        "serial_number": a.serial_number,
        "delivery_date": str(a.delivery_date) if a.delivery_date else None,
        "warranty_end": str(a.warranty_end) if a.warranty_end else None,
        "in_warranty": in_warranty,
        "location": a.location,
        "source_work_order_id": a.source_work_order_id,
        "current_location_id": a.current_location_id,
        "current_location_name": a.current_location.name if a.current_location else None,
        "status": a.status,
        "notes": a.notes,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


# ══════════════════════════════════════════════════════════════
# CUSTOMER ASSETS — معدات العملاء
# ══════════════════════════════════════════════════════════════

@router.get("/assets")
async def list_assets(
    customer_id: Optional[int] = None,
    in_warranty: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
):
    from app.core.database import current_tenant_id as _ctid_la
    q = select(CustomerAsset).options(
        selectinload(CustomerAsset.customer),
        selectinload(CustomerAsset.current_location),
    ).where(CustomerAsset.tenant_id == (_ctid_la.get() or 1))
    if customer_id:
        q = q.where(CustomerAsset.customer_id == customer_id)
    if in_warranty is True:
        q = q.where(CustomerAsset.warranty_end >= date.today())
    res = await db.execute(q.order_by(CustomerAsset.created_at.desc()))
    return [asset_dict(a) for a in res.scalars().all()]


@router.post("/assets", status_code=201)
async def create_asset(data: AssetIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_ca
    _ca_data = data.model_dump()
    _ca_data['tenant_id'] = _ctid_ca.get() or 1
    a = CustomerAsset(**_ca_data)
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return {"id": a.id, "name": a.name, "customer_id": a.customer_id}


@router.get("/assets/{aid}")
async def get_asset(aid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_ga
    res = await db.execute(
        select(CustomerAsset)
        .options(selectinload(CustomerAsset.customer),
                 selectinload(CustomerAsset.current_location),
                 selectinload(CustomerAsset.service_orders))
        .where(CustomerAsset.id == aid, CustomerAsset.tenant_id == (_ctid_ga.get() or 1))
    )
    a = res.scalar_one_or_none()
    if not a: raise HTTPException(404)
    d = asset_dict(a)

    d["source_work_order"] = None
    if a.source_work_order_id:
        from app.models.models import WorkOrder
        wo = await db.get(WorkOrder, a.source_work_order_id)
        if wo:
            d["source_work_order"] = {
                "id": wo.id, "code": wo.code,
                "status": wo.status,
                "planned_cost": float(wo.planned_cost or 0),
                "actual_cost": float(wo.actual_cost or 0),
            }

    maintenance_cost = sum(
        float(so.total_cost or 0) for so in (a.service_orders or [])
        if so.order_type in ("maintenance", "repair") and so.status == "completed"
    )
    d["maintenance_cost_total"] = maintenance_cost

    d["service_history"] = [{
        "id": so.id, "code": so.code, "title": so.title,
        "order_type": so.order_type,
        "order_type_ar": ORDER_TYPE_AR.get(so.order_type, so.order_type),
        "status": so.status,
        "status_ar": STATUS_AR.get(so.status, so.status),
        "planned_date": str(so.planned_date) if so.planned_date else None,
        "completion_date": str(so.completion_date) if so.completion_date else None,
    } for so in (a.service_orders or [])]

    mv_res = await db.execute(
        select(AssetMovement)
        .options(selectinload(AssetMovement.from_location), selectinload(AssetMovement.to_location))
        .where(AssetMovement.asset_id == aid)
        .order_by(AssetMovement.event_date, AssetMovement.id)
    )
    d["movements"] = [{
        "id": m.id,
        "event_type": m.event_type,
        "event_type_ar": MOVEMENT_TYPE_AR.get(m.event_type, m.event_type),
        "ref_type": m.ref_type,
        "ref_id": m.ref_id,
        "from_location_id": m.from_location_id,
        "from_location_name": m.from_location.name if m.from_location else None,
        "to_location_id": m.to_location_id,
        "to_location_name": m.to_location.name if m.to_location else None,
        "event_date": str(m.event_date) if m.event_date else None,
        "notes": m.notes,
    } for m in mv_res.scalars().all()]
    return d


@router.post("/assets/{aid}/movements", status_code=201)
async def add_asset_movement(aid: int, data: AssetMovementIn, db: AsyncSession = Depends(get_db)):
    if data.event_type not in _VALID_MOVEMENT_TYPES:
        raise HTTPException(400, f"نوع حركة غير صالح. القيم المسموحة: {sorted(_VALID_MOVEMENT_TYPES)}")
    from app.core.database import current_tenant_id as _ctid_aam
    a = await db.get(CustomerAsset, aid)
    if not a or a.tenant_id != (_ctid_aam.get() or 1): raise HTTPException(404)
    from_location_id = a.current_location_id
    m = AssetMovement(
        tenant_id=a.tenant_id, asset_id=a.id, event_type=data.event_type,
        ref_type="manual", ref_id=None,
        from_location_id=from_location_id, to_location_id=data.to_location_id,
        event_date=data.event_date or date.today(),
        notes=data.notes,
    )
    db.add(m)
    if data.event_type in ("installed", "relocated") and data.to_location_id:
        a.current_location_id = data.to_location_id
    await db.commit()
    await db.refresh(m)
    return {"id": m.id, "event_type": m.event_type}


@router.put("/assets/{aid}")
async def update_asset(aid: int, data: AssetIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_ua
    a = await db.get(CustomerAsset, aid)
    if not a or a.tenant_id != (_ctid_ua.get() or 1): raise HTTPException(404)
    for k, v in data.model_dump().items():
        setattr(a, k, v)
    await db.commit()
    return {"id": a.id, "name": a.name}


@router.delete("/assets/{aid}")
async def delete_asset(aid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_dela
    a = await db.get(CustomerAsset, aid)
    if not a or a.tenant_id != (_ctid_dela.get() or 1): raise HTTPException(404)
    await db.delete(a)
    await db.commit()
    return {"message": "تم الحذف", "id": aid}


# ══════════════════════════════════════════════════════════════
# SERVICE ORDERS
# ══════════════════════════════════════════════════════════════

@router.get("/orders")
async def list_service_orders(
    status: Optional[str] = None,
    customer_id: Optional[int] = None,
    order_type: Optional[str] = None,
    worker_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    from app.core.database import current_tenant_id as _ctid_lso
    q = select(FieldServiceOrder).options(
        selectinload(FieldServiceOrder.customer),
        selectinload(FieldServiceOrder.location),
        selectinload(FieldServiceOrder.asset),
        selectinload(FieldServiceOrder.assignments).selectinload(ServiceAssignment.worker),
        selectinload(FieldServiceOrder.visits),
    ).where(FieldServiceOrder.tenant_id == (_ctid_lso.get() or 1))
    if status:
        q = q.where(FieldServiceOrder.status == status)
    if customer_id:
        q = q.where(FieldServiceOrder.customer_id == customer_id)
    if order_type:
        q = q.where(FieldServiceOrder.order_type == order_type)
    if worker_id:
        q = q.join(ServiceAssignment).where(ServiceAssignment.worker_id == worker_id)

    res = await db.execute(q.order_by(FieldServiceOrder.created_at.desc()))
    return [order_dict(o) for o in res.scalars().all()]


@router.post("/orders", status_code=201)
async def create_service_order(data: ServiceOrderIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_fso
    _fso_data = data.model_dump(exclude={"estimated_hours"})
    _fso_data['tenant_id'] = _ctid_fso.get() or 1
    o = FieldServiceOrder(**_fso_data)
    if data.estimated_hours is not None:
        o.estimated_hours = Decimal(str(data.estimated_hours))
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return {"id": o.id, "code": o.code}


@router.get("/orders/{oid}")
async def get_service_order(oid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_gso
    res = await db.execute(
        select(FieldServiceOrder).options(
            selectinload(FieldServiceOrder.customer),
        selectinload(FieldServiceOrder.location),
            selectinload(FieldServiceOrder.asset),
            selectinload(FieldServiceOrder.assignments).selectinload(ServiceAssignment.worker),
            selectinload(FieldServiceOrder.visits),
        ).where(FieldServiceOrder.id == oid, FieldServiceOrder.tenant_id == (_ctid_gso.get() or 1))
    )
    o = res.scalar_one_or_none()
    if not o: raise HTTPException(404)
    return order_dict(o)


@router.put("/orders/{oid}")
async def update_service_order(oid: int, data: ServiceOrderIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_uso
    o = await db.get(FieldServiceOrder, oid)
    if not o or o.tenant_id != (_ctid_uso.get() or 1): raise HTTPException(404)
    for k, v in data.model_dump(exclude={"estimated_hours"}).items():
        if hasattr(o, k):
            setattr(o, k, v)
    if data.estimated_hours is not None:
        o.estimated_hours = Decimal(str(data.estimated_hours))
    await db.commit()
    return {"id": o.id, "code": o.code, "order_type": o.order_type}


@router.patch("/orders/{oid}/status")
async def update_order_status(oid: int, data: StatusUpdate, db: AsyncSession = Depends(get_db)):
    if data.status not in _VALID_FS_STATUSES:
        raise HTTPException(400, f"حالة غير صالحة. القيم المسموحة: {sorted(_VALID_FS_STATUSES)}")
    from app.core.database import current_tenant_id as _ctid_uos
    o = await db.get(FieldServiceOrder, oid)
    if not o or o.tenant_id != (_ctid_uos.get() or 1): raise HTTPException(404)
    prev_status = o.status
    o.status = data.status
    if data.completion_date:
        o.completion_date = data.completion_date
    elif data.status == "completed" and not o.completion_date:
        o.completion_date = date.today()
    if data.notes:
        o.notes = (o.notes or "") + f"\n[{date.today()}] {data.notes}"

    # تسجيل حركة صيانة على المعدة عند إكمال أمر خدمة ميدانية مرتبط بأصل
    if o.asset_id and data.status == "completed" and prev_status != "completed" and o.order_type in ("maintenance", "repair", "inspection"):
        db.add(AssetMovement(
            tenant_id=o.tenant_id, asset_id=o.asset_id, event_type="maintained",
            ref_type="field_service_order", ref_id=o.id,
            event_date=o.completion_date or date.today(),
            notes=f"صيانة عبر أمر خدمة ميدانية {o.code}",
        ))

    await db.commit()
    return {"id": o.id, "status": o.status, "status_ar": STATUS_AR.get(o.status, o.status)}


@router.delete("/orders/{oid}")
async def delete_service_order(oid: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_dso
    o = await db.get(FieldServiceOrder, oid)
    if not o or o.tenant_id != (_ctid_dso.get() or 1): raise HTTPException(404)
    if o.status not in ("pending", "cancelled"):
        raise HTTPException(400, "لا يمكن حذف أمر جاري — قم بإلغائه أولاً")
    await db.delete(o)
    await db.commit()
    return {"message": "تم الحذف", "id": oid}


# ── Assignments ────────────────────────────────────────────────

@router.post("/orders/{oid}/assign")
async def assign_workers(oid: int, data: AssignIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_asgn
    tid_asgn = _ctid_asgn.get() or 1
    o = await db.get(FieldServiceOrder, oid)
    if not o or o.tenant_id != tid_asgn: raise HTTPException(404)

    for wid in data.worker_ids:
        w = await db.get(Worker, wid)
        if not w or w.tenant_id != tid_asgn:
            continue
        existing = (await db.execute(
            select(ServiceAssignment).where(
                ServiceAssignment.service_order_id == oid,
                ServiceAssignment.worker_id == wid,
            )
        )).scalar_one_or_none()
        if not existing:
            db.add(ServiceAssignment(
                tenant_id=tid_asgn,
                service_order_id=oid,
                worker_id=wid,
                role=data.role,
                notes=data.notes,
            ))

    if o.status == "pending":
        o.status = "assigned"
    await db.commit()
    return {"message": f"تم تعيين {len(data.worker_ids)} فني", "status": o.status}


@router.delete("/orders/{oid}/assign/{assignment_id}")
async def remove_assignment(oid: int, assignment_id: int, db: AsyncSession = Depends(get_db)):
    a = await db.get(ServiceAssignment, assignment_id)
    if not a or a.service_order_id != oid: raise HTTPException(404)
    await db.delete(a)
    await db.commit()
    return {"message": "تم إلغاء التعيين"}


# ── Visits ─────────────────────────────────────────────────────

@router.post("/orders/{oid}/visit", status_code=201)
async def record_visit(oid: int, data: VisitIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_sv
    tid_sv = _ctid_sv.get() or 1
    o = await db.get(FieldServiceOrder, oid)
    if not o or o.tenant_id != tid_sv: raise HTTPException(404)

    visit = ServiceVisit(
        tenant_id=tid_sv,
        service_order_id=oid,
        visit_date=data.visit_date,
        arrival_time=data.arrival_time,
        departure_time=data.departure_time,
        labor_hours=Decimal(str(data.labor_hours)) if data.labor_hours else None,
        work_done=data.work_done,
        materials_used=data.materials_used,
        next_action=data.next_action,
        status=data.status,
    )
    db.add(visit)

    if data.labor_hours:
        if o.actual_hours is None:
            o.actual_hours = Decimal("0")
        o.actual_hours += Decimal(str(data.labor_hours))

    if o.status == "assigned":
        o.status = "in_progress"

    await db.commit()
    await db.refresh(visit)
    return {
        "id": visit.id, "visit_date": str(visit.visit_date),
        "labor_hours": float(visit.labor_hours) if visit.labor_hours else None,
        "service_order_status": o.status,
    }


@router.get("/orders/{oid}/visits")
async def list_visits(oid: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(ServiceVisit)
        .where(ServiceVisit.service_order_id == oid)
        .order_by(ServiceVisit.visit_date.desc())
    )
    return [{
        "id": v.id, "visit_date": str(v.visit_date),
        "arrival_time": v.arrival_time, "departure_time": v.departure_time,
        "labor_hours": float(v.labor_hours) if v.labor_hours else None,
        "work_done": v.work_done, "materials_used": v.materials_used,
        "next_action": v.next_action, "status": v.status,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    } for v in res.scalars().all()]


# ── Dashboard & Reports ────────────────────────────────────────

@router.get("/dashboard")
async def field_service_dashboard(db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_dash
    tid = _ctid_dash.get() or 1
    total       = await db.scalar(select(func.count()).select_from(FieldServiceOrder).where(FieldServiceOrder.tenant_id == tid))
    pending     = await db.scalar(select(func.count()).select_from(FieldServiceOrder).where(FieldServiceOrder.status == "pending", FieldServiceOrder.tenant_id == tid))
    in_progress = await db.scalar(select(func.count()).select_from(FieldServiceOrder).where(FieldServiceOrder.status == "in_progress", FieldServiceOrder.tenant_id == tid))
    completed   = await db.scalar(select(func.count()).select_from(FieldServiceOrder).where(FieldServiceOrder.status == "completed", FieldServiceOrder.tenant_id == tid))
    total_assets    = await db.scalar(select(func.count()).select_from(CustomerAsset).where(CustomerAsset.tenant_id == tid))
    warranty_expiring = await db.scalar(
        select(func.count()).select_from(CustomerAsset).where(
            CustomerAsset.warranty_end >= date.today(),
            CustomerAsset.tenant_id == tid,
        )
    )

    by_type_res = await db.execute(
        select(FieldServiceOrder.order_type, func.count())
        .where(FieldServiceOrder.tenant_id == tid)
        .group_by(FieldServiceOrder.order_type)
    )
    by_type = [{"type": t, "type_ar": ORDER_TYPE_AR.get(t, t), "count": c} for t, c in by_type_res]

    upcoming_res = await db.execute(
        select(FieldServiceOrder).options(
            selectinload(FieldServiceOrder.customer),
        selectinload(FieldServiceOrder.location),
            selectinload(FieldServiceOrder.asset),
        ).where(
            FieldServiceOrder.planned_date >= date.today(),
            FieldServiceOrder.status.in_(["pending", "assigned"]),
            FieldServiceOrder.tenant_id == tid,
        ).order_by(FieldServiceOrder.planned_date).limit(10)
    )
    upcoming = [{
        "id": o.id, "code": o.code, "title": o.title,
        "customer_name": o.customer.name if o.customer else "",
        "planned_date": str(o.planned_date),
        "priority": o.priority,
        "priority_ar": PRIORITY_AR.get(o.priority, o.priority),
        "status_ar": STATUS_AR.get(o.status, o.status),
    } for o in upcoming_res.scalars().all()]

    return {
        "total": total or 0,
        "pending": pending or 0,
        "in_progress": in_progress or 0,
        "completed": completed or 0,
        "total_assets": total_assets or 0,
        "assets_in_warranty": warranty_expiring or 0,
        "by_type": by_type,
        "upcoming_visits": upcoming,
    }


@router.get("/calendar")
async def service_calendar(
    month: Optional[int] = None,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """تقويم الزيارات الميدانية"""
    from sqlalchemy import extract
    from app.core.database import current_tenant_id as _ctid_cal
    today = date.today()
    m = month or today.month
    y = year or today.year

    res = await db.execute(
        select(FieldServiceOrder).options(
            selectinload(FieldServiceOrder.customer),
        selectinload(FieldServiceOrder.location),
            selectinload(FieldServiceOrder.assignments).selectinload(ServiceAssignment.worker),
        ).where(
            extract("month", FieldServiceOrder.planned_date) == m,
            extract("year",  FieldServiceOrder.planned_date) == y,
            FieldServiceOrder.tenant_id == (_ctid_cal.get() or 1),
        ).order_by(FieldServiceOrder.planned_date)
    )
    orders = res.scalars().all()
    return [{
        "id": o.id, "code": o.code, "title": o.title,
        "order_type_ar": ORDER_TYPE_AR.get(o.order_type, o.order_type),
        "customer_name": o.customer.name if o.customer else "",
        "planned_date": str(o.planned_date),
        "status": o.status, "status_ar": STATUS_AR.get(o.status, o.status),
        "priority": o.priority,
        "team": [a.worker.name for a in o.assignments if a.worker],
    } for o in orders]


@router.get("/workers/{worker_id}/schedule")
async def worker_schedule(worker_id: int, db: AsyncSession = Depends(get_db)):
    """جدول عمل فني معين"""
    from app.core.database import current_tenant_id as _ctid_ws
    tid = _ctid_ws.get() or 1
    w = await db.get(Worker, worker_id)
    if not w or w.tenant_id != tid: raise HTTPException(404)

    res = await db.execute(
        select(FieldServiceOrder).options(
            selectinload(FieldServiceOrder.customer),
        selectinload(FieldServiceOrder.location),
            selectinload(FieldServiceOrder.asset),
        ).join(ServiceAssignment).where(
            ServiceAssignment.worker_id == worker_id,
            FieldServiceOrder.status.in_(["assigned", "in_progress", "pending"]),
            FieldServiceOrder.tenant_id == tid,
        ).order_by(FieldServiceOrder.planned_date)
    )
    orders = res.scalars().all()
    return {
        "worker_id": worker_id,
        "worker_name": w.name,
        "assignments": [{
            "id": o.id, "code": o.code, "title": o.title,
            "order_type_ar": ORDER_TYPE_AR.get(o.order_type, o.order_type),
            "customer_name": o.customer.name if o.customer else "",
            "asset_name": o.asset.name if o.asset else None,
            "location": o.asset.location if o.asset else None,
            "planned_date": str(o.planned_date) if o.planned_date else None,
            "status_ar": STATUS_AR.get(o.status, o.status),
            "priority_ar": PRIORITY_AR.get(o.priority, o.priority),
        } for o in orders],
    }


# ══════════════════════════════════════════════════════════════
# LABOR ENTRIES — تكاليف العمالة
# ══════════════════════════════════════════════════════════════

from app.models.models import ProjectLaborEntry, ProjectToolWithdrawal, Material

class LaborEntryIn(BaseModel):
    worker_id:   int
    entry_date:  date
    days:        float = 1
    daily_wage:  float = 0
    allowance:   float = 0   # بدل معيشة / سفر / إقامة
    advance:     float = 0   # سلفة
    notes:       Optional[str] = None


class ToolWithdrawalIn(BaseModel):
    material_id:    int
    worker_id:      Optional[int] = None
    qty:            float = 1
    withdrawn_date: date
    notes:          Optional[str] = None


class ToolReturnIn(BaseModel):
    returned_date: date
    returned_qty:  float
    damaged_qty:   float = 0
    notes:         Optional[str] = None


# ── GET labor entries for an order ────────────────────────────
@router.get("/orders/{oid}/labor")
async def list_labor(oid: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(ProjectLaborEntry)
        .options(selectinload(ProjectLaborEntry.worker))
        .where(ProjectLaborEntry.service_order_id == oid)
        .order_by(ProjectLaborEntry.entry_date.desc())
    )
    entries = res.scalars().all()
    return [{
        "id":           e.id,
        "worker_id":    e.worker_id,
        "worker_name":  e.worker.name if e.worker else "",
        "entry_date":   str(e.entry_date),
        "days":         float(e.days),
        "daily_wage":   float(e.daily_wage),
        "allowance":    float(e.allowance),
        "advance":      float(e.advance),
        "total_pay":    float(e.total_pay),
        "notes":        e.notes,
    } for e in entries]


# ── POST add labor entry ───────────────────────────────────────
@router.post("/orders/{oid}/labor", status_code=201)
async def add_labor(oid: int, data: LaborEntryIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_ple
    tid_ple = _ctid_ple.get() or 1
    o = await db.get(FieldServiceOrder, oid)
    if not o or o.tenant_id != tid_ple: raise HTTPException(404)
    if data.days is None or data.days <= 0:
        raise HTTPException(400, "عدد الأيام يجب أن يكون أكبر من صفر")
    if data.daily_wage is not None and data.daily_wage < 0:
        raise HTTPException(400, "الأجر اليومي لا يمكن أن يكون سالباً")

    total = Decimal(str(data.days)) * Decimal(str(data.daily_wage)) + Decimal(str(data.allowance))
    entry = ProjectLaborEntry(
        tenant_id=tid_ple,
        service_order_id=oid,
        worker_id=data.worker_id,
        entry_date=data.entry_date,
        days=Decimal(str(data.days)),
        daily_wage=Decimal(str(data.daily_wage)),
        allowance=Decimal(str(data.allowance)),
        advance=Decimal(str(data.advance)),
        total_pay=total,
        notes=data.notes,
    )
    db.add(entry)

    # تحديث labor_cost و total_cost في أمر الخدمة
    o.labor_cost = (o.labor_cost or Decimal("0")) + total
    o.total_cost = (o.labor_cost or Decimal("0")) + (o.materials_cost or Decimal("0"))

    await db.commit()
    await db.refresh(entry)
    return {
        "id": entry.id,
        "total_pay": float(entry.total_pay),
        "order_labor_cost": float(o.labor_cost),
        "order_total_cost":  float(o.total_cost),
    }


# ── DELETE labor entry ─────────────────────────────────────────
@router.delete("/orders/{oid}/labor/{entry_id}")
async def delete_labor(oid: int, entry_id: int, db: AsyncSession = Depends(get_db)):
    e = await db.get(ProjectLaborEntry, entry_id)
    if not e or e.service_order_id != oid: raise HTTPException(404)
    o = await db.get(FieldServiceOrder, oid)
    if o:
        o.labor_cost = max(Decimal("0"), (o.labor_cost or Decimal("0")) - e.total_pay)
        o.total_cost = (o.labor_cost or Decimal("0")) + (o.materials_cost or Decimal("0"))
    await db.delete(e)
    await db.commit()
    return {"message": "تم الحذف"}


# ══════════════════════════════════════════════════════════════
# TOOL WITHDRAWALS — عهدة العدة والأدوات
# ══════════════════════════════════════════════════════════════

# ── GET withdrawals for an order ──────────────────────────────
@router.get("/orders/{oid}/tools")
async def list_tools(oid: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(ProjectToolWithdrawal)
        .options(
            selectinload(ProjectToolWithdrawal.material),
            selectinload(ProjectToolWithdrawal.worker),
        )
        .where(ProjectToolWithdrawal.service_order_id == oid)
        .order_by(ProjectToolWithdrawal.withdrawn_date.desc())
    )
    rows = res.scalars().all()
    return [{
        "id":             r.id,
        "material_id":    r.material_id,
        "material_name":  r.material.name if r.material else "",
        "material_unit":  r.material.unit if r.material else "",
        "worker_id":      r.worker_id,
        "worker_name":    r.worker.name if r.worker else "",
        "qty":            float(r.qty),
        "withdrawn_date": str(r.withdrawn_date),
        "returned_date":  str(r.returned_date) if r.returned_date else None,
        "returned_qty":   float(r.returned_qty) if r.returned_qty else None,
        "damaged_qty":    float(r.damaged_qty) if r.damaged_qty else 0,
        "status":         r.status,
        "notes":          r.notes,
    } for r in rows]


# ── POST withdraw tool ─────────────────────────────────────────
@router.post("/orders/{oid}/tools", status_code=201)
async def withdraw_tool(oid: int, data: ToolWithdrawalIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_ptw
    tid_ptw = _ctid_ptw.get() or 1
    o = await db.get(FieldServiceOrder, oid)
    if not o or o.tenant_id != tid_ptw: raise HTTPException(404)
    mat = await db.get(Material, data.material_id)
    if not mat: raise HTTPException(404, "المادة غير موجودة")
    if data.qty is None or data.qty <= 0:
        raise HTTPException(400, "الكمية يجب أن تكون أكبر من صفر")

    row = ProjectToolWithdrawal(
        tenant_id=tid_ptw,
        service_order_id=oid,
        material_id=data.material_id,
        worker_id=data.worker_id,
        qty=Decimal(str(data.qty)),
        withdrawn_date=data.withdrawn_date,
        status="withdrawn",
        notes=data.notes,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "status": row.status}


# ── PATCH return tool ──────────────────────────────────────────
@router.patch("/orders/{oid}/tools/{wid}/return")
async def return_tool(oid: int, wid: int, data: ToolReturnIn, db: AsyncSession = Depends(get_db)):
    row = await db.get(ProjectToolWithdrawal, wid)
    if not row or row.service_order_id != oid: raise HTTPException(404)

    returned_qty = Decimal(str(data.returned_qty))
    damaged_qty  = Decimal(str(data.damaged_qty))
    if returned_qty < 0 or damaged_qty < 0:
        raise HTTPException(400, "الكمية لا يمكن أن تكون سالبة")
    # مجموع المرتجع + التالف ما يزيدش عن الكمية المسحوبة أصلاً
    if returned_qty + damaged_qty > row.qty:
        raise HTTPException(400, f"إجمالي المرتجع والتالف ({returned_qty + damaged_qty}) أكبر من الكمية المسحوبة ({row.qty})")

    row.returned_date = data.returned_date
    row.returned_qty  = returned_qty
    row.damaged_qty   = damaged_qty
    row.status = "damaged" if damaged_qty > 0 else "returned"
    if data.notes:
        row.notes = (row.notes or "") + f"\n{data.notes}"

    await db.commit()
    return {"id": row.id, "status": row.status}


# ── DELETE withdrawal ──────────────────────────────────────────
@router.delete("/orders/{oid}/tools/{wid}")
async def delete_withdrawal(oid: int, wid: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ProjectToolWithdrawal, wid)
    if not row or row.service_order_id != oid: raise HTTPException(404)
    if row.status != "withdrawn":
        raise HTTPException(400, "لا يمكن حذف عهدة مرتجعة أو تالفة")
    await db.delete(row)
    await db.commit()
    return {"message": "تم الحذف"}
