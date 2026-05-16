from datetime import date
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.models.models import WorkOrder, CostLine, JournalEntry

router = APIRouter(prefix="/accounting", tags=["accounting"])


class WorkOrderCreate(BaseModel):
    code: str
    equipment_id: int
    planned_cost: Decimal = Decimal("0")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    notes: Optional[str] = None

class WorkOrderUpdate(BaseModel):
    code: str
    equipment_id: int
    status: str = "draft"
    planned_cost: Decimal = Decimal("0")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    notes: Optional[str] = None

class CostLineCreate(BaseModel):
    work_order_id: int
    cost_type: str
    description: str
    qty: Decimal = Decimal("1")
    unit_cost: Decimal
    material_id: Optional[int] = None


def wo_dict(wo: WorkOrder):
    return {
        "id": wo.id, "code": wo.code, "equipment_id": wo.equipment_id,
        "status": wo.status,
        "planned_cost": float(wo.planned_cost),
        "actual_cost": float(wo.actual_cost),
        "start_date": str(wo.start_date) if wo.start_date else None,
        "end_date": str(wo.end_date) if wo.end_date else None,
        "notes": wo.notes,
        "created_at": wo.created_at.isoformat() if wo.created_at else None,
    }


@router.post("/work-orders", status_code=201)
async def create_work_order(data: WorkOrderCreate, db: AsyncSession = Depends(get_db)):
    wo = WorkOrder(**data.model_dump())
    db.add(wo)
    await db.commit()
    await db.refresh(wo)
    return wo_dict(wo)


@router.get("/work-orders")
async def list_work_orders(status: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    q = select(WorkOrder)
    if status:
        q = q.where(WorkOrder.status == status)
    res = await db.execute(q.order_by(WorkOrder.created_at.desc()))
    return [wo_dict(w) for w in res.scalars().all()]


@router.get("/work-orders/{wo_id}")
async def get_work_order(wo_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(WorkOrder).options(
            selectinload(WorkOrder.cost_lines),
            selectinload(WorkOrder.journal_entries),
        ).where(WorkOrder.id == wo_id)
    )
    wo = res.scalar_one_or_none()
    if not wo: raise HTTPException(404)
    d = wo_dict(wo)
    d["cost_lines"] = [{
        "id": l.id, "cost_type": l.cost_type, "description": l.description,
        "qty": float(l.qty), "unit_cost": float(l.unit_cost),
        "total_cost": float(l.total_cost),
    } for l in wo.cost_lines]
    return d


@router.put("/work-orders/{wo_id}")
async def update_work_order(wo_id: int, data: WorkOrderUpdate, db: AsyncSession = Depends(get_db)):
    wo = await db.get(WorkOrder, wo_id)
    if not wo: raise HTTPException(404)
    for k, v in data.model_dump().items():
        setattr(wo, k, v)
    await db.commit()
    await db.refresh(wo)
    return wo_dict(wo)


@router.patch("/work-orders/{wo_id}/status")
async def change_status(wo_id: int, status: str, db: AsyncSession = Depends(get_db)):
    wo = await db.get(WorkOrder, wo_id)
    if not wo: raise HTTPException(404)
    valid = ["draft", "in_progress", "done", "cancelled"]
    if status not in valid:
        raise HTTPException(400, f"Status must be one of {valid}")
    wo.status = status
    await db.commit()
    return {"id": wo_id, "status": wo.status}


@router.delete("/work-orders/{wo_id}")
async def delete_work_order(wo_id: int, db: AsyncSession = Depends(get_db)):
    wo = await db.get(WorkOrder, wo_id)
    if not wo: raise HTTPException(404)
    # Delete related lines first
    lines_res = await db.execute(select(CostLine).where(CostLine.work_order_id == wo_id))
    for l in lines_res.scalars().all():
        await db.delete(l)
    j_res = await db.execute(select(JournalEntry).where(JournalEntry.work_order_id == wo_id))
    for j in j_res.scalars().all():
        await db.delete(j)
    await db.delete(wo)
    await db.commit()
    return {"message": "تم الحذف", "id": wo_id}


@router.post("/cost-lines", status_code=201)
async def add_cost_line(data: CostLineCreate, db: AsyncSession = Depends(get_db)):
    total = data.qty * data.unit_cost
    line = CostLine(**data.model_dump(), total_cost=total)
    db.add(line)

    # Update work order actual_cost
    wo = await db.get(WorkOrder, data.work_order_id)
    if wo:
        wo.actual_cost += total

    # Auto journal entry
    mapping = {
        "material": ("WIP - مواد", "المخزون"),
        "labor":    ("WIP - عمالة", "الأجور المستحقة"),
        "overhead": ("WIP - غير مباشر", "التحميل"),
    }
    debit, credit = mapping.get(data.cost_type, ("WIP", "متنوع"))
    entry = JournalEntry(
        work_order_id=data.work_order_id,
        entry_type=data.cost_type + "_cost",
        debit_account=debit, credit_account=credit,
        amount=total, description=data.description,
        entry_date=date.today(),
    )
    db.add(entry)
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
    line = await db.get(CostLine, line_id)
    if not line: raise HTTPException(404)
    wo = await db.get(WorkOrder, line.work_order_id)
    if wo:
        wo.actual_cost -= line.total_cost
    await db.delete(line)
    await db.commit()
    return {"message": "تم الحذف", "id": line_id}


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


@router.put("/work-orders/{wo_id}/status")
async def update_work_order_status(wo_id: int, status: str,
                                    db: AsyncSession = Depends(get_db)):
    wo = await db.get(WorkOrder, wo_id)
    if not wo: raise HTTPException(404, "Work order not found")
    wo.status = status
    await db.commit()
    return {"id": wo.id, "code": wo.code, "status": wo.status}

@router.put("/work-orders/{wo_id}")
async def update_work_order(wo_id: int, data: dict,
                             db: AsyncSession = Depends(get_db)):
    wo = await db.get(WorkOrder, wo_id)
    if not wo: raise HTTPException(404)
    for k, v in data.items():
        if hasattr(wo, k): setattr(wo, k, v)
    await db.commit()
    return {"id": wo.id, "code": wo.code, "status": wo.status}

@router.delete("/work-orders/{wo_id}")
async def delete_work_order(wo_id: int, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select as sa_select
    wo = await db.get(WorkOrder, wo_id)
    if not wo: raise HTTPException(404)
    lines = (await db.execute(sa_select(CostLine).where(CostLine.work_order_id == wo_id))).scalars().all()
    for l in lines: await db.delete(l)
    await db.delete(wo)
    await db.commit()
    return {"message": "تم الحذف", "id": wo_id}

@router.delete("/cost-lines/{line_id}")
async def delete_cost_line(line_id: int, db: AsyncSession = Depends(get_db)):
    line = await db.get(CostLine, line_id)
    if not line: raise HTTPException(404)
    wo = await db.get(WorkOrder, line.work_order_id)
    if wo: wo.actual_cost -= line.total_cost
    await db.delete(line)
    await db.commit()
    return {"message": "تم الحذف"}
