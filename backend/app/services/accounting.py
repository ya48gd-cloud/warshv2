from decimal import Decimal
from datetime import date
from sqlalchemy import select, func, and_, extract
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import WorkOrder, CostLine, JournalEntry
from app.schemas.schemas import WorkOrderCreate, CostLineCreate


class AccountingService:

    @staticmethod
    async def create_work_order(db: AsyncSession, data: WorkOrderCreate) -> WorkOrder:
        wo = WorkOrder(**data.model_dump())
        db.add(wo)
        await db.commit()
        await db.refresh(wo)
        return wo

    @staticmethod
    async def list_work_orders(db: AsyncSession, status: str | None = None):
        q = select(WorkOrder).options(selectinload(WorkOrder.equipment))
        if status:
            q = q.where(WorkOrder.status == status)
        result = await db.execute(q.order_by(WorkOrder.created_at.desc()))
        return result.scalars().all()

    @staticmethod
    async def get_work_order(db: AsyncSession, wo_id: int) -> WorkOrder | None:
        result = await db.execute(
            select(WorkOrder)
            .options(
                selectinload(WorkOrder.cost_lines),
                selectinload(WorkOrder.equipment),
                selectinload(WorkOrder.journal_entries),
            )
            .where(WorkOrder.id == wo_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def add_cost_line(db: AsyncSession, data: CostLineCreate) -> CostLine:
        total = data.qty * data.unit_cost
        line = CostLine(**data.model_dump(), total_cost=total)
        db.add(line)

        # Update actual_cost on work order
        wo = await db.get(WorkOrder, data.work_order_id)
        if wo:
            wo.actual_cost += total

        await db.commit()
        await db.refresh(line)

        # Auto-create journal entry
        await AccountingService._auto_journal(db, line, wo)
        return line

    @staticmethod
    async def _auto_journal(db: AsyncSession, line: CostLine, wo: WorkOrder | None):
        mapping = {
            "material": ("WIP - Materials",     "Inventory"),
            "labor":    ("WIP - Labor",          "Wages Payable"),
            "overhead": ("WIP - Overhead",       "Overhead Applied"),
        }
        debit, credit = mapping.get(line.cost_type, ("WIP", "Misc"))
        entry = JournalEntry(
            work_order_id=wo.id if wo else None,
            entry_type=f"{line.cost_type}_cost",
            debit_account=debit,
            credit_account=credit,
            amount=line.total_cost,
            description=line.description,
            entry_date=date.today(),
        )
        db.add(entry)
        await db.commit()

    @staticmethod
    async def cost_variance(db: AsyncSession, wo_id: int) -> dict:
        wo = await AccountingService.get_work_order(db, wo_id)
        if not wo:
            return {}
        variance = wo.planned_cost - wo.actual_cost
        return {
            "work_order_code": wo.code,
            "planned_cost": float(wo.planned_cost),
            "actual_cost": float(wo.actual_cost),
            "variance": float(variance),
            "variance_pct": float(variance / wo.planned_cost * 100) if wo.planned_cost else 0,
            "status": "under_budget" if variance >= 0 else "over_budget",
        }

    @staticmethod
    async def dashboard_stats(db: AsyncSession) -> dict:
        from app.models.models import Material, Equipment

        total_mats = await db.scalar(select(func.count()).select_from(Material))
        low_stock  = await db.scalar(
            select(func.count()).select_from(Material)
            .where(Material.stock_qty <= Material.reorder_level)
        )
        open_wos = await db.scalar(
            select(func.count()).select_from(WorkOrder)
            .where(WorkOrder.status == "in_progress")
        )
        total_eq = await db.scalar(select(func.count()).select_from(Equipment))

        today = date.today()
        mtd_material = await db.scalar(
            select(func.coalesce(func.sum(CostLine.total_cost), 0))
            .where(
                CostLine.cost_type == "material",
                extract("month", CostLine.created_at) == today.month,
                extract("year",  CostLine.created_at) == today.year,
            )
        )
        mtd_labor = await db.scalar(
            select(func.coalesce(func.sum(CostLine.total_cost), 0))
            .where(
                CostLine.cost_type == "labor",
                extract("month", CostLine.created_at) == today.month,
                extract("year",  CostLine.created_at) == today.year,
            )
        )
        return {
            "total_materials":  total_mats or 0,
            "low_stock_count":  low_stock or 0,
            "open_work_orders": open_wos or 0,
            "total_equipment":  total_eq or 0,
            "mtd_material_cost": float(mtd_material or 0),
            "mtd_labor_cost":    float(mtd_labor or 0),
        }
