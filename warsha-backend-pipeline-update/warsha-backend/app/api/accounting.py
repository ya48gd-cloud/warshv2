from datetime import date
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import (
    WorkOrder, CostLine, JournalEntry, Equipment, MaterialReservation, WorkOrderMember,
    CustomerOrder, CustomerAsset,
)

router = APIRouter(prefix="/accounting", tags=["accounting"], dependencies=[Depends(require_any)])
print_router = APIRouter(prefix="/accounting", tags=["accounting-print"], dependencies=[Depends(require_any)])


class WorkOrderCreate(BaseModel):
    code: Optional[str] = None          # اختياري — يُولَّد تلقائياً لو فارغ
    equipment_id: int
    customer_order_id: Optional[int] = None   # ربط أمر الإنتاج بطلب عميل (عميل/موقع/معدة خارجية)
    planned_cost: Decimal = Decimal("0")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    planned_end_date: Optional[date] = None
    priority: Optional[str] = "normal"
    quantity: Optional[Decimal] = Decimal("1")
    quantity_unit: Optional[str] = "قطعة"
    notes: Optional[str] = None
    description: Optional[str] = None
    customer_id: Optional[int] = None
    location_id: Optional[int] = None
    planned_length: Optional[Decimal] = None

class WorkOrderUpdate(BaseModel):
    code: Optional[str] = None
    equipment_id: int
    customer_order_id: Optional[int] = None
    customer_id: Optional[int] = None
    location_id: Optional[int] = None
    status: str = "draft"
    planned_cost: Decimal = Decimal("0")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    planned_end_date: Optional[date] = None
    priority: Optional[str] = "normal"
    quantity: Optional[Decimal] = Decimal("1")
    quantity_unit: Optional[str] = "قطعة"
    notes: Optional[str] = None
    description: Optional[str] = None
    planned_length: Optional[Decimal] = None

class CostLineCreate(BaseModel):
    work_order_id: int
    cost_type: str
    description: str
    qty: Decimal = Decimal("1")
    unit_cost: Decimal
    material_id: Optional[int] = None


async def _next_wo_code(db: AsyncSession) -> str:
    """توليد كود أمر عمل فريد تلقائياً: WO-0001, WO-0002 …"""
    count = await db.scalar(select(func.count()).select_from(WorkOrder))
    n = (count or 0) + 1
    while True:
        code = f"WO-{n:04d}"
        exists = await db.scalar(select(WorkOrder.id).where(WorkOrder.code == code))
        if not exists:
            return code
        n += 1


def wo_dict(wo: WorkOrder, eq=None, co: Optional[CustomerOrder] = None, linked_asset: Optional[CustomerAsset] = None,
            direct_customer=None, direct_location=None):
    """تحويل WorkOrder لـ dict — يقبل equipment/customer_order/linked_asset كـ parameters لتجنب lazy-load"""
    order_customer = getattr(co, "customer", None) if co else None
    order_location = getattr(co, "location", None) if co else None
    eff_customer = direct_customer or order_customer
    eff_location = direct_location or order_location
    return {
        "id": wo.id, "code": wo.code, "equipment_id": wo.equipment_id,
        "equipment_name_ar": eq.name_ar if eq else None,
        "equipment_name":    eq.name_ar if eq else None,
        "status": wo.status,
        "planned_cost": float(wo.planned_cost),
        "actual_cost": float(wo.actual_cost),
        "cost_pct": round(float(wo.actual_cost) / float(wo.planned_cost) * 100, 1)
                    if wo.planned_cost and float(wo.planned_cost) > 0 else 0,
        "start_date": str(wo.start_date) if wo.start_date else None,
        "end_date": str(wo.end_date) if wo.end_date else None,
        "planned_end_date": str(wo.planned_end_date) if wo.planned_end_date else None,
        "priority": getattr(wo, "priority", "normal") or "normal",
        "quantity": float(wo.quantity) if wo.quantity is not None else 1,
        "quantity_unit": wo.quantity_unit,
        "description": getattr(wo, "description", None) or wo.notes,
        "notes": wo.notes,
        "created_at": wo.created_at.isoformat() if wo.created_at else None,
        # ── ربط العميل/الموقع/المعدة الخارجية ──
        "customer_order_id": wo.customer_order_id,
        "customer_order_code": co.code if co else None,
        "customer_id": getattr(wo, "customer_id", None) or (eff_customer.id if eff_customer else None),
        "customer_name": eff_customer.name if eff_customer else None,
        "location_id": getattr(wo, "location_id", None) or (eff_location.id if eff_location else None),
        "location_name": eff_location.name if eff_location else None,
        "linked_asset_id": linked_asset.id if linked_asset else None,
        "linked_asset_name": linked_asset.name if linked_asset else None,
        "planned_length": float(wo.planned_length) if getattr(wo, "planned_length", None) else None,
        # معلومات المعدة المترية
        "equipment_length_min": float(eq.length_min) if eq and getattr(eq, "length_min", None) else None,
        "equipment_length_max": float(eq.length_max) if eq and getattr(eq, "length_max", None) else None,
        "equipment_calc_unit": eq.calc_unit if eq else None,
    }


async def _load_wo_extras(db: AsyncSession, wo: WorkOrder):
    """تحميل طلب العميل (مع العميل والموقع) + الأصل المرتبط (المعدة الخارجية) لأمر إنتاج"""
    co = None
    if wo.customer_order_id:
        res = await db.execute(
            select(CustomerOrder)
            .options(selectinload(CustomerOrder.customer), selectinload(CustomerOrder.location))
            .where(CustomerOrder.id == wo.customer_order_id)
        )
        co = res.scalar_one_or_none()
    linked_asset = await db.scalar(
        select(CustomerAsset).where(CustomerAsset.source_work_order_id == wo.id)
    )
    # load direct customer/location if set on WO itself
    direct_customer = None
    direct_location = None
    if getattr(wo, "customer_id", None):
        from app.models.models import Customer, CustomerLocation
        direct_customer = await db.get(Customer, wo.customer_id)
    if getattr(wo, "location_id", None):
        from app.models.models import Customer, CustomerLocation
        direct_location = await db.get(CustomerLocation, wo.location_id)
    return co, linked_asset, direct_customer, direct_location


@router.get("/work-orders/next-code")
async def get_next_wo_code(db: AsyncSession = Depends(get_db)):
    return {"code": await _next_wo_code(db)}


@router.post("/work-orders", status_code=201)
async def create_work_order(data: WorkOrderCreate, db: AsyncSession = Depends(get_db)):
    if data.start_date and data.planned_end_date and data.planned_end_date < data.start_date:
        raise HTTPException(400, "تاريخ الانتهاء المخطط يجب أن يكون بعد تاريخ البدء")
    from app.core.database import current_tenant_id as _ctid_wo
    d = data.model_dump()
    d['tenant_id'] = _ctid_wo.get() or 1
    # توليد الكود تلقائياً لو مش موجود أو فارغ
    if not d.get("code") or not str(d["code"]).strip():
        d["code"] = await _next_wo_code(db)
    else:
        d["code"] = str(d["code"]).strip()
        exists = await db.scalar(select(WorkOrder.id).where(WorkOrder.code == d["code"]))
        if exists:
            raise HTTPException(400, f"كود أمر العمل '{d['code']}' مستخدم من قبل")

    wo = WorkOrder(**d)
    db.add(wo)
    await db.flush()  # نحتاج wo.id قبل حساب التكلفة

    # حساب planned_cost + تجميد WorkOrderBOMLine من BOM بالأسعار الحالية لحظة الإنشاء
    if wo.equipment_id:
        from app.services.equipment import EquipmentService
        from app.models.models import WorkOrderBOMLine
        from decimal import Decimal as D
        try:
            eq_check = await db.get(Equipment, wo.equipment_id)
            pl = getattr(wo, "planned_length", None)
            if eq_check and getattr(eq_check, "length_min", None) and pl:
                bom_result = await EquipmentService.calculate_bom_cost_for_length(
                    db, wo.equipment_id, D(str(pl)), D(str(wo.quantity or 1)))
            elif eq_check:
                bom_result = await EquipmentService.calculate_bom_cost(
                    db, wo.equipment_id, D(str(wo.quantity or 1)))
            else:
                bom_result = None
            if bom_result and float(bom_result.get("total_cost", 0)) > 0:
                wo.planned_cost = D(str(bom_result.get("total_cost", 0)))
                tid = _ctid_wo.get() or 1
                for line in bom_result.get("lines", []):
                    db.add(WorkOrderBOMLine(
                        tenant_id=tid, work_order_id=wo.id,
                        material_id=line.get("material_id"),
                        required_qty=D(str(line.get("qty", 0))),
                        unit_cost=D(str(line.get("unit_cost", 0))),
                        total_cost=D(str(line.get("total_cost", 0))),
                    ))
                    db.add(CostLine(
                        tenant_id=tid, work_order_id=wo.id,
                        cost_type="material",
                        description=f"{line.get('material_name', '')} — {line.get('material_code', '')}",
                        qty=D(str(line.get("qty", 0))),
                        unit_cost=D(str(line.get("unit_cost", 0))),
                        total_cost=D(str(line.get("total_cost", 0))),
                        material_id=line.get("material_id"),
                    ))
        except Exception:
            pass  # لا نوقف إنشاء الأمر لو فشل حساب BOM

    await db.commit()
    await db.refresh(wo)
    # جلب التجهيزة بشكل صريح لتجنب lazy-load خارج async
    eq = await db.get(Equipment, wo.equipment_id)
    co, linked_asset, direct_customer, direct_location = await _load_wo_extras(db, wo)
    return wo_dict(wo, eq, co, linked_asset, direct_customer, direct_location)


@router.get("/work-orders")
async def list_work_orders(status: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    q = select(WorkOrder).options(
        selectinload(WorkOrder.equipment),
        selectinload(WorkOrder.customer_order).selectinload(CustomerOrder.customer),
        selectinload(WorkOrder.customer_order).selectinload(CustomerOrder.location),
        selectinload(WorkOrder.customer),
        selectinload(WorkOrder.location),
    )
    if status:
        q = q.where(WorkOrder.status == status)
    res = await db.execute(q.order_by(WorkOrder.created_at.desc()))
    rows = res.scalars().all()
    wo_ids = [w.id for w in rows]
    assets = (await db.execute(
        select(CustomerAsset).where(CustomerAsset.source_work_order_id.in_(wo_ids))
    )).scalars().all() if wo_ids else []
    assets_by_wo = {a.source_work_order_id: a for a in assets}
    return [wo_dict(w, getattr(w, "equipment", None), getattr(w, "customer_order", None), assets_by_wo.get(w.id),
                    getattr(w, "customer", None), getattr(w, "location", None)) for w in rows]


@router.get("/work-orders/{wo_id}")
async def get_work_order(wo_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(WorkOrder).options(
            selectinload(WorkOrder.cost_lines),
            selectinload(WorkOrder.journal_entries),
            selectinload(WorkOrder.equipment),
            selectinload(WorkOrder.members).selectinload(WorkOrderMember.worker),
            selectinload(WorkOrder.customer_order).selectinload(CustomerOrder.customer),
            selectinload(WorkOrder.customer_order).selectinload(CustomerOrder.location),
            selectinload(WorkOrder.customer),
            selectinload(WorkOrder.location),
        ).where(WorkOrder.id == wo_id)
    )
    wo = res.scalar_one_or_none()
    if not wo: raise HTTPException(404)
    linked_asset = await db.scalar(
        select(CustomerAsset).where(CustomerAsset.source_work_order_id == wo.id)
    )
    d = wo_dict(wo, getattr(wo, "equipment", None), getattr(wo, "customer_order", None), linked_asset,
                getattr(wo, "customer", None), getattr(wo, "location", None))
    d["cost_lines"] = [{
        "id": l.id, "cost_type": l.cost_type, "description": l.description,
        "qty": float(l.qty), "unit_cost": float(l.unit_cost),
        "total_cost": float(l.total_cost),
    } for l in wo.cost_lines]
    d["team"] = [{
        "id": m.id, "worker_id": m.worker_id,
        "worker_name": m.worker.name if m.worker else "",
        "role": m.role, "hourly_rate": float(m.hourly_rate),
        "planned_hours": float(m.planned_hours), "actual_hours": float(m.actual_hours),
        "labor_cost": float(m.actual_hours * m.hourly_rate),
    } for m in wo.members]
    d["team_labor_cost"] = sum(float(m.actual_hours * m.hourly_rate) for m in wo.members)
    return d


@router.put("/work-orders/{wo_id}")
async def update_work_order(wo_id: int, data: WorkOrderUpdate, db: AsyncSession = Depends(get_db)):
    wo = await db.get(WorkOrder, wo_id)
    if not wo: raise HTTPException(404)
    if data.start_date and data.planned_end_date and data.planned_end_date < data.start_date:
        raise HTTPException(400, "تاريخ الانتهاء المخطط يجب أن يكون بعد تاريخ البدء")
    d = data.model_dump()
    if not d.get("code") or not str(d["code"]).strip():
        d.pop("code", None)
    else:
        new_code = str(d["code"]).strip()
        if new_code != wo.code:
            exists = await db.scalar(select(WorkOrder.id).where(WorkOrder.code == new_code))
            if exists:
                raise HTTPException(400, f"كود أمر العمل '{new_code}' مستخدم من قبل")
        d["code"] = new_code
    old_length = getattr(wo, "planned_length", None)
    for k, v in d.items():
        setattr(wo, k, v)

    # لو الطول اتغير في معدة مترية، أعد حساب WO BOM lines + planned_cost
    new_length = getattr(wo, "planned_length", None)
    if new_length and new_length != old_length and wo.equipment_id:
        from app.services.equipment import EquipmentService
        from app.models.models import WorkOrderBOMLine
        from decimal import Decimal as D
        try:
            eq_check = await db.get(Equipment, wo.equipment_id)
            if eq_check and getattr(eq_check, "length_min", None):
                await db.flush()
                bom_result = await EquipmentService.calculate_bom_cost_for_length(
                    db, wo.equipment_id, D(str(new_length)), D(str(wo.quantity or 1)))
                if bom_result:
                    await db.execute(
                        __import__("sqlalchemy", fromlist=["delete"]).delete(WorkOrderBOMLine)
                        .where(WorkOrderBOMLine.work_order_id == wo_id)
                    )
                    await db.execute(
                        __import__("sqlalchemy", fromlist=["delete"]).delete(CostLine)
                        .where(CostLine.work_order_id == wo_id, CostLine.cost_type == "material")
                    )
                    # إلغاء حجوزات المواد القديمة — الكميات تغيرت بتغيّر الطول
                    await db.execute(
                        __import__("sqlalchemy", fromlist=["delete"]).delete(MaterialReservation)
                        .where(MaterialReservation.work_order_id == wo_id)
                    )
                    tid = __import__("app.core.database", fromlist=["current_tenant_id"]).current_tenant_id.get() or 1
                    for line in bom_result.get("lines", []):
                        db.add(WorkOrderBOMLine(
                            tenant_id=tid, work_order_id=wo_id,
                            material_id=line.get("material_id"),
                            required_qty=D(str(line.get("qty", 0))),
                            unit_cost=D(str(line.get("unit_cost", 0))),
                            total_cost=D(str(line.get("total_cost", 0))),
                        ))
                        db.add(CostLine(
                            tenant_id=tid, work_order_id=wo_id,
                            cost_type="material",
                            description=f"{line.get('material_name', '')} — {line.get('material_code', '')}",
                            qty=D(str(line.get("qty", 0))),
                            unit_cost=D(str(line.get("unit_cost", 0))),
                            total_cost=D(str(line.get("total_cost", 0))),
                            material_id=line.get("material_id"),
                        ))
                    wo.planned_cost = D(str(bom_result.get("total_cost", 0)))
        except Exception:
            pass

    await db.commit()
    await db.refresh(wo)
    eq = await db.get(Equipment, wo.equipment_id)
    co, linked_asset, direct_customer, direct_location = await _load_wo_extras(db, wo)
    return wo_dict(wo, eq, co, linked_asset, direct_customer, direct_location)


@router.patch("/work-orders/{wo_id}/status")
async def change_status(wo_id: int, status: str, db: AsyncSession = Depends(get_db)):
    wo = await db.get(WorkOrder, wo_id)
    if not wo: raise HTTPException(404)
    valid = ["draft", "in_progress", "done", "cancelled"]
    if status not in valid:
        raise HTTPException(400, f"Status must be one of {valid}")
    wo.status = status

    # عند إلغاء الأمر: إلغاء الحجوزات المفتوحة حتى لا تؤثر على المخزون أو MRP
    if status == "cancelled":
        reservations = (await db.execute(
            select(MaterialReservation).where(
                MaterialReservation.work_order_id == wo_id,
                MaterialReservation.status.in_(['pending', 'partial', 'reserved', 'partially_issued']),
            )
        )).scalars().all()
        for res in reservations:
            res.status = "cancelled"

    await db.commit()
    return {"id": wo_id, "status": wo.status}


@router.delete("/work-orders/{wo_id}")
async def delete_work_order(wo_id: int, db: AsyncSession = Depends(get_db)):
    from app.models.models import (
        WorkOrderBOMLine, WorkOrderMember, ProductionSchedule, WorkOrderStage,
        SalesOrderWorkOrder, SheetStockItem, BarStockItem, StockMovement,
        Invoice, ScrapRecord, CustomerAsset, QualityInspection,
    )
    wo = await db.get(WorkOrder, wo_id)
    if not wo: raise HTTPException(404)

    # سجلات لا يجوز أن يبقى عمود work_order_id فيها NULL — تُحذف مع الأمر
    for model in (CostLine, JournalEntry, MaterialReservation, WorkOrderBOMLine,
                  WorkOrderMember, ProductionSchedule, WorkOrderStage, SalesOrderWorkOrder):
        rows = (await db.execute(select(model).where(model.work_order_id == wo_id))).scalars().all()
        for r in rows: await db.delete(r)

    # سجلات مرجعها اختياري — نفصلها بدل حذفها حفاظًا على تاريخها
    for model, col in (
        (SheetStockItem, "source_work_order_id"), (BarStockItem, "source_work_order_id"),
        (StockMovement, "work_order_id"), (Invoice, "work_order_id"),
        (ScrapRecord, "work_order_id"), (CustomerAsset, "source_work_order_id"),
        (QualityInspection, "work_order_id"),
    ):
        rows = (await db.execute(select(model).where(getattr(model, col) == wo_id))).scalars().all()
        for r in rows: setattr(r, col, None)

    await db.delete(wo)
    await db.commit()
    return {"message": "تم الحذف", "id": wo_id}


@router.post("/cost-lines", status_code=201)
async def add_cost_line(data: CostLineCreate, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_cl
    _tid_cl = _ctid_cl.get() or 1
    total = data.qty * data.unit_cost
    cl_payload = data.model_dump()
    cl_payload['tenant_id'] = _tid_cl
    line = CostLine(**cl_payload, total_cost=total)
    db.add(line)
    wo = await db.get(WorkOrder, data.work_order_id)
    if wo:
        wo.actual_cost += total

    # محطة 22: قيد محاسبي حقيقي عبر AccountingEntry (مش JournalEntry القديم
    # اللي مش ظاهر في ميزان المراجعة/قوائم الدخل خالص)
    from app.services.auto_journal import journal_cost_line
    await journal_cost_line(
        db, _tid_cl, data.work_order_id,
        wo.code if wo else str(data.work_order_id),
        data.cost_type, total, date.today(),
    )
    await db.commit()
    await db.refresh(line)
    return {
        "id": line.id, "work_order_id": line.work_order_id,
        "cost_type": line.cost_type, "description": line.description,
        "qty": float(line.qty), "unit_cost": float(line.unit_cost),
        "total_cost": float(line.total_cost),
    }


@router.delete("/cost-lines/{line_id}")
async def delete_cost_line(line_id: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_dcl
    _tid_dcl = _ctid_dcl.get() or 1
    line = await db.get(CostLine, line_id)
    if not line: raise HTTPException(404)
    wo = await db.get(WorkOrder, line.work_order_id)
    if wo:
        wo.actual_cost -= line.total_cost

    # محطة 22: عكس القيد المحاسبي الخاص بالبند ده تحديدًا (مش أي بند تاني)
    from app.services.auto_journal import journal_cost_line_delete
    await journal_cost_line_delete(
        db, _tid_dcl, line.work_order_id,
        wo.code if wo else str(line.work_order_id),
        line.cost_type, line.total_cost, date.today(),
    )

    await db.delete(line)
    await db.commit()
    return {"message": "تم الحذف", "id": line_id}


@router.get("/work-orders/{wo_id}/cost-lines")
async def get_cost_lines(wo_id: int, db: AsyncSession = Depends(get_db)):
    wo = await db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(404, detail="Work order not found")
    lines = (await db.execute(
        select(CostLine).where(CostLine.work_order_id == wo_id)
    )).scalars().all()
    return [
        {
            "id": l.id,
            "work_order_id": l.work_order_id,
            "cost_type": l.cost_type,
            "description": l.description,
            "qty": float(l.qty),
            "unit_cost": float(l.unit_cost),
            "total_cost": float(l.total_cost),
            "material_id": l.material_id,
        }
        for l in lines
    ]


@router.get("/work-orders/{wo_id}/journal")
async def get_journals(wo_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(JournalEntry).where(JournalEntry.work_order_id == wo_id))
    rows = res.scalars().all()
    return [{
        "id": j.id, "entry_type": j.entry_type,
        "debit_account": j.debit_account, "credit_account": j.credit_account,
        "amount": float(j.amount), "description": j.description,
        "entry_date": str(j.entry_date) if j.entry_date else None,
    } for j in rows]


@router.get("/work-orders/{wo_id}/variance")
async def cost_variance(wo_id: int, db: AsyncSession = Depends(get_db)):
    wo = await db.get(WorkOrder, wo_id)
    if not wo: return {}
    variance = float(wo.planned_cost) - float(wo.actual_cost)
    pct = variance / float(wo.planned_cost) * 100 if wo.planned_cost else 0
    return {
        "work_order_code": wo.code,
        "planned_cost": float(wo.planned_cost),
        "actual_cost": float(wo.actual_cost),
        "variance": variance,
        "variance_pct": round(pct, 1),
        "status": "under_budget" if variance >= 0 else "over_budget",
    }


@router.post("/work-orders/{wo_id}/apply-parametric-bom")
async def apply_parametric_bom(wo_id: int, db: AsyncSession = Depends(get_db)):
    """
    يحسب BOM المتري لأمر الإنتاج بناءً على planned_length المحفوظ،
    ويملأ WorkOrderBOMLine + CostLine (material) للأمر.
    يحذف البنود القديمة أولاً ثم يضيف الجديدة.
    """
    from app.models.models import WorkOrderBOMLine, Equipment
    from app.services.equipment import EquipmentService
    from decimal import Decimal as D

    wo = await db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(404, "أمر الإنتاج غير موجود")
    if not getattr(wo, "planned_length", None):
        raise HTTPException(400, "لم يُحدَّد طول التصنيع لهذا الأمر — حدّد الطول أولاً")

    eq = await db.get(Equipment, wo.equipment_id)
    if not eq or not getattr(eq, "length_min", None):
        raise HTTPException(400, "المعدة ليست مترية — لا يوجد نطاق طول محدد")

    length = D(str(wo.planned_length))
    quantity = D(str(wo.quantity or 1))

    try:
        result = await EquipmentService.calculate_bom_cost_for_length(
            db, wo.equipment_id, length, quantity
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # حذف البنود القديمة
    await db.execute(
        __import__("sqlalchemy", fromlist=["delete"]).delete(WorkOrderBOMLine)
        .where(WorkOrderBOMLine.work_order_id == wo_id)
    )
    await db.execute(
        __import__("sqlalchemy", fromlist=["delete"]).delete(CostLine)
        .where(CostLine.work_order_id == wo_id, CostLine.cost_type == "material")
    )

    from app.core.database import current_tenant_id as _ctid
    tid = _ctid.get()

    # إضافة بنود جديدة من الـ BOM المحسوب (material lines في "lines")
    total_mat_cost = D("0")
    for line in result.get("lines", []):
        mat_id = line.get("material_id")
        qty = D(str(line.get("qty", 0)))
        unit_cost = D(str(line.get("unit_cost", 0)))
        total = D(str(line.get("total_cost", 0)))
        desc = f"{line.get('material_name', '')} — {line.get('material_code', '')}"

        bom_line = WorkOrderBOMLine(
            tenant_id=tid, work_order_id=wo_id,
            material_id=mat_id, required_qty=qty,
            unit_cost=unit_cost, total_cost=total,
        )
        db.add(bom_line)

        cost_line = CostLine(
            tenant_id=tid, work_order_id=wo_id,
            cost_type="material", description=desc,
            qty=qty, unit_cost=unit_cost, total_cost=total,
            material_id=mat_id,
        )
        db.add(cost_line)
        total_mat_cost += total

    # تحديث التكلفة المخططة
    wo.planned_cost = total_mat_cost
    await db.commit()

    return {
        "ok": True,
        "planned_length": float(length),
        "lines_added": len(result.get("lines", [])),
        "total_material_cost": float(total_mat_cost),
        "materials_summary": result.get("materials_summary", []),
    }


@router.get("/work-orders/{wo_id}/bom-lines")
async def get_wo_bom_lines(wo_id: int, db: AsyncSession = Depends(get_db)):
    """جلب سطور BOM المجمّدة لأمر الإنتاج (work_order_bom_lines)."""
    from app.models.models import WorkOrderBOMLine, Material
    tid = 1
    rows = (await db.execute(
        select(WorkOrderBOMLine)
        .where(WorkOrderBOMLine.work_order_id == wo_id, WorkOrderBOMLine.tenant_id == tid)
        .order_by(WorkOrderBOMLine.id)
    )).scalars().all()

    result = []
    for r in rows:
        mat = await db.get(Material, r.material_id) if r.material_id else None
        result.append({
            "id": r.id,
            "material_id": r.material_id,
            "material_code": mat.code if mat else None,
            "material_name": mat.name_ar or mat.name_en if mat else None,
            "unit": mat.unit if mat else None,
            "required_qty": float(r.required_qty or 0),
            "unit_cost": float(r.unit_cost or 0),
            "total_cost": float(r.total_cost or 0),
        })
    total = sum(r["total_cost"] for r in result)
    return {"lines": result, "total_cost": total, "count": len(result)}


@print_router.get("/work-orders/{wo_id}/bom/print", response_class=HTMLResponse)
async def print_wo_bom(wo_id: int, db: AsyncSession = Depends(get_db)):
    """طباعة BOM الفعلية لأمر الإنتاج (المجمّدة بالطول الصح)."""
    from app.models.models import WorkOrderBOMLine, Material
    wo = await db.get(WorkOrder, wo_id)
    if not wo: raise HTTPException(404)
    eq = await db.get(Equipment, wo.equipment_id) if wo.equipment_id else None

    rows_data = (await db.execute(
        select(WorkOrderBOMLine).where(WorkOrderBOMLine.work_order_id == wo_id).order_by(WorkOrderBOMLine.id)
    )).scalars().all()

    total_cost = sum(float(r.total_cost or 0) for r in rows_data)
    rows_html = ""
    for i, r in enumerate(rows_data, 1):
        mat = await db.get(Material, r.material_id) if r.material_id else None
        rows_html += f"""<tr>
          <td style="text-align:center">{i}</td>
          <td class="font-mono">{mat.code if mat else '—'}</td>
          <td>{mat.name_ar if mat else '—'}</td>
          <td style="text-align:center">{mat.unit if mat else '—'}</td>
          <td style="text-align:center">{float(r.required_qty or 0):.3f}</td>
          <td>{_egp(r.unit_cost)}</td>
          <td class="font-bold">{_egp(r.total_cost)}</td>
        </tr>"""
    if not rows_html:
        rows_html = '<tr><td colspan="7" style="text-align:center;color:#9ca3af;padding:20px">لا توجد بنود BOM</td></tr>'

    eq_name = (eq.name_ar if eq else '') or ''
    length_badge = f'<span style="background:#dbeafe;color:#1d4ed8;padding:2px 10px;border-radius:20px;font-size:13px">📐 {float(wo.planned_length)}م</span>' if wo.planned_length else ''
    html = f"""<!DOCTYPE html><html dir="rtl" lang="ar"><head>
<meta charset="UTF-8"><title>BOM — {wo.code}</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  @page {{size:A4 landscape;margin:12mm}} *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'IBM Plex Sans Arabic',Arial,sans-serif;font-size:12px;color:#1e293b;background:#fff}}
  .header{{background:#1d4ed8;color:#fff;padding:16px 24px;display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;border-radius:8px 8px 0 0}}
  .header h1{{font-size:18px;font-weight:700}} .header .sub{{font-size:11px;opacity:.85;margin-top:3px}}
  .kpi-row{{display:flex;gap:10px;margin-bottom:14px}}
  .kpi{{flex:1;background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:10px;text-align:center}}
  .kpi .val{{font-size:15px;font-weight:700;color:#1d4ed8}} .kpi .lbl{{font-size:10px;color:#6b7280;margin-top:2px}}
  table{{width:100%;border-collapse:collapse}}
  th{{background:#1d4ed8;color:#fff;padding:7px 8px;text-align:right;font-size:10px;font-weight:600}}
  td{{padding:7px 8px;border-bottom:1px solid #f1f5f9;font-size:11px}}
  tr:nth-child(even) td{{background:#f8faff}} .font-bold{{font-weight:700}} .font-mono{{font-family:monospace;font-size:10px}}
  @media print{{*{{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}}}}
</style></head><body>
<div class="header">
  <div><h1>قائمة المواد (BOM) — {wo.code} {length_badge}</h1>
  <div class="sub">{eq_name} | التكلفة المخططة: {_egp(wo.planned_cost)}</div></div>
  <div style="font-size:11px;opacity:.85">{date.today().strftime('%Y/%m/%d')}</div>
</div>
<div class="kpi-row">
  <div class="kpi"><div class="val">{len(rows_data)}</div><div class="lbl">عدد البنود</div></div>
  <div class="kpi"><div class="val">{_egp(total_cost)}</div><div class="lbl">إجمالي تكلفة المواد</div></div>
  <div class="kpi"><div class="val">{float(wo.planned_length) if wo.planned_length else '—'} م</div><div class="lbl">طول التصنيع</div></div>
  <div class="kpi"><div class="val">{float(wo.quantity or 1)}</div><div class="lbl">الكمية</div></div>
</div>
<table><thead><tr>
  <th style="width:4%">#</th><th style="width:10%">الكود</th><th style="width:30%">اسم المادة</th>
  <th style="width:7%">الوحدة</th><th style="width:8%">الكمية</th>
  <th style="width:12%">سعر الوحدة</th><th style="width:12%">الإجمالي</th>
</tr></thead><tbody>{rows_html}</tbody></table>
<script>window.onload=()=>window.print()</script>
</body></html>"""
    return HTMLResponse(html)


@router.post("/work-orders/{wo_id}/execute-bom")
async def execute_bom_full(wo_id: int, db: AsyncSession = Depends(get_db)):
    """
    تنفيذ BOM كامل بضغطة واحدة:
    - مواد عادية: صرف مباشر
    - ألواح: استهلاك النسالة أولاً ثم البالك
    - أسياخ: استهلاك النسالة أولاً ثم البالك (مع حفظ الباقي كنسالة جديدة)
    """
    from app.services.production import auto_execute_bom
    result = await auto_execute_bom(db, wo_id)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/dashboard")
async def dashboard_stats(db: AsyncSession = Depends(get_db)):
    from app.models.models import Material, Equipment
    from sqlalchemy import extract
    today = date.today()

    total_mats = await db.scalar(select(func.count()).select_from(Material))
    low_stock  = await db.scalar(select(func.count()).select_from(Material).where(Material.stock_qty <= Material.reorder_level))
    open_wos   = await db.scalar(select(func.count()).select_from(WorkOrder).where(WorkOrder.status == "in_progress"))
    total_eq   = await db.scalar(select(func.count()).select_from(Equipment))
    mtd_mat    = await db.scalar(select(func.coalesce(func.sum(CostLine.total_cost), 0)).where(CostLine.cost_type == "material", extract("month", CostLine.created_at) == today.month))
    mtd_labor  = await db.scalar(select(func.coalesce(func.sum(CostLine.total_cost), 0)).where(CostLine.cost_type == "labor", extract("month", CostLine.created_at) == today.month))

    return {
        "total_materials": total_mats or 0,
        "low_stock_count": low_stock or 0,
        "open_work_orders": open_wos or 0,
        "total_equipment": total_eq or 0,
        "mtd_material_cost": float(mtd_mat or 0),
        "mtd_labor_cost": float(mtd_labor or 0),
    }


# ── Print Work Order ──────────────────────────────────────────────────────────

def _egp(v) -> str:
    try:
        return f"{float(v):,.2f} ج"
    except Exception:
        return "0.00 ج"


@print_router.get("/work-orders/{wo_id}/print", response_class=HTMLResponse)
async def print_work_order(wo_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(WorkOrder).options(
            selectinload(WorkOrder.cost_lines),
            selectinload(WorkOrder.equipment),
        ).where(WorkOrder.id == wo_id)
    )
    wo = res.scalar_one_or_none()
    if not wo:
        raise HTTPException(404)

    eq = getattr(wo, "equipment", None)
    eq_name = eq.name_ar if eq else "—"
    lines = wo.cost_lines or []

    # جلب العميل والموقع
    customer_name = None
    location_name = None
    if getattr(wo, "customer_id", None):
        from app.models.models import Customer, CustomerLocation
        cust = await db.get(Customer, wo.customer_id)
        customer_name = cust.name if cust else None
    if getattr(wo, "location_id", None):
        from app.models.models import CustomerLocation
        loc = await db.get(CustomerLocation, wo.location_id)
        location_name = loc.name if loc else None
    # fallback: من customer_order
    if not customer_name and wo.customer_order_id:
        from app.models.models import CustomerOrder
        co = await db.get(CustomerOrder, wo.customer_order_id)
        if co:
            from app.models.models import Customer, CustomerLocation
            if co.customer_id:
                cust = await db.get(Customer, co.customer_id)
                customer_name = cust.name if cust else None
            if co.location_id:
                loc = await db.get(CustomerLocation, co.location_id)
                location_name = loc.name if loc else None

    type_labels = {"material": "مواد", "labor": "عمالة", "overhead": "مصاريف غير مباشرة"}
    status_labels = {"draft": "مسودة", "in_progress": "جارٍ", "done": "منتهي", "cancelled": "ملغي"}
    status_colors = {"draft": "#6b7280", "in_progress": "#d97706", "done": "#059669", "cancelled": "#dc2626"}
    status_color = status_colors.get(wo.status, "#6b7280")
    status_label = status_labels.get(wo.status, wo.status)

    planned = float(wo.planned_cost or 0)
    actual  = float(wo.actual_cost or 0)
    var     = planned - actual
    var_pct = round(var / planned * 100, 1) if planned else 0

    rows_html = ""
    for l in lines:
        rows_html += f"""
        <tr>
          <td>{type_labels.get(l.cost_type, l.cost_type)}</td>
          <td>{l.description or "—"}</td>
          <td style="text-align:center">{float(l.qty)}</td>
          <td>{_egp(l.unit_cost)}</td>
          <td class="font-bold">{_egp(l.total_cost)}</td>
        </tr>"""
    if not rows_html:
        rows_html = '<tr><td colspan="5" style="text-align:center;color:#9ca3af;padding:20px">لا توجد بنود تكلفة</td></tr>'

    html = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<title>أمر إنتاج — {wo.code}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  @page {{ size: A4; margin: 15mm }}
  * {{ box-sizing: border-box; margin: 0; padding: 0 }}
  body {{ font-family: 'IBM Plex Sans Arabic', Arial, sans-serif; font-size: 13px; color: #1e293b; background: #fff }}
  .header {{ background: #1e3a5f; color: #fff; padding: 20px 24px; border-radius: 8px 8px 0 0; display: flex; justify-content: space-between; align-items: center; margin-bottom: 0 }}
  .header h1 {{ font-size: 22px; font-weight: 700 }}
  .header .meta {{ font-size: 12px; opacity: .85; line-height: 1.8 }}
  .badge {{ display: inline-block; padding: 3px 12px; border-radius: 20px; font-size: 11px; font-weight: 600; background: {status_color}22; color: {status_color}; border: 1px solid {status_color}55 }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0 }}
  .kpi {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; text-align: center }}
  .kpi .val {{ font-size: 18px; font-weight: 700; color: #1e3a5f }}
  .kpi .lbl {{ font-size: 10px; color: #6b7280; margin-top: 4px }}
  .section {{ margin: 16px 0 }}
  .section-title {{ font-size: 12px; font-weight: 600; color: #6b7280; letter-spacing: .5px; text-transform: uppercase; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; margin-bottom: 10px }}
  table {{ width: 100%; border-collapse: collapse }}
  th {{ background: #1e3a5f; color: #fff; padding: 8px 10px; text-align: right; font-size: 11px; font-weight: 600 }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #f1f5f9; font-size: 12px }}
  tr:last-child td {{ border-bottom: none }}
  tr:nth-child(even) td {{ background: #f8fafc }}
  .font-bold {{ font-weight: 700 }}
  .info-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px }}
  .info-item {{ padding: 10px; background: #f8fafc; border-radius: 6px; border: 1px solid #e2e8f0 }}
  .info-item .label {{ font-size: 10px; color: #6b7280; margin-bottom: 4px }}
  .info-item .value {{ font-size: 13px; font-weight: 600; color: #1e293b }}
  .progress-bar {{ background: #e2e8f0; border-radius: 4px; height: 8px; margin-top: 4px; overflow: hidden }}
  .progress-fill {{ background: {'#059669' if actual <= planned else '#dc2626'}; height: 100%; border-radius: 4px }}
  @media print {{ * {{ -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important }} }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>أمر الإنتاج — {wo.code}</h1>
    <div class="meta">{eq_name}</div>
  </div>
  <div style="text-align:left">
    <span class="badge">{status_label}</span>
    <div class="meta" style="margin-top:6px">تاريخ الطباعة: {date.today().strftime('%Y/%m/%d')}</div>
  </div>
</div>

<div class="kpi-grid">
  <div class="kpi"><div class="val">{_egp(planned)}</div><div class="lbl">التكلفة المخططة</div></div>
  <div class="kpi"><div class="val" style="color:{'#dc2626' if actual > planned else '#059669'}">{_egp(actual)}</div><div class="lbl">التكلفة الفعلية</div></div>
  <div class="kpi"><div class="val" style="color:{'#059669' if var >= 0 else '#dc2626'}">{_egp(abs(var))}</div><div class="lbl">{'وفر' if var >= 0 else 'تجاوز'}</div></div>
  <div class="kpi"><div class="val" style="color:{'#059669' if var >= 0 else '#dc2626'}">{var_pct}%</div><div class="lbl">نسبة {'الوفر' if var >= 0 else 'التجاوز'}</div></div>
</div>

<div class="info-grid">
  <div class="info-item"><div class="label">المعدة / المنتج</div><div class="value">{eq_name}</div></div>
  <div class="info-item"><div class="label">الحالة</div><div class="value">{status_label}</div></div>
  <div class="info-item"><div class="label">العميل</div><div class="value">{customer_name or '—'}</div></div>
  <div class="info-item"><div class="label">الموقع</div><div class="value">{location_name or '—'}</div></div>
  <div class="info-item"><div class="label">تاريخ البدء</div><div class="value">{wo.start_date or '—'}</div></div>
  <div class="info-item"><div class="label">تاريخ الانتهاء</div><div class="value">{wo.end_date or '—'}</div></div>
</div>

{'<div class="info-item" style="margin:10px 0"><div class="label">ملاحظات</div><div class="value" style="font-weight:400">' + wo.notes + '</div></div>' if wo.notes else ''}

<div class="section">
  <div class="section-title">بنود التكلفة</div>
  <div>
    <div class="progress-bar" style="margin-bottom:10px">
      <div class="progress-fill" style="width:{min(round(actual/planned*100) if planned else 0, 100)}%"></div>
    </div>
  </div>
  <table>
    <thead>
      <tr>
        <th>النوع</th>
        <th>الوصف</th>
        <th style="text-align:center">الكمية</th>
        <th>سعر الوحدة</th>
        <th>الإجمالي</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
      <tr style="background:#1e3a5f22">
        <td colspan="4" class="font-bold" style="text-align:left">الإجمالي الفعلي</td>
        <td class="font-bold">{_egp(actual)}</td>
      </tr>
    </tbody>
  </table>
</div>

<script>window.onload = () => window.print();</script>
</body>
</html>"""
    return HTMLResponse(content=html)
