from decimal import Decimal
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import Material, StockMovement, MaterialCategory
from app.schemas.schemas import MaterialCreate, MovementCreate


class InventoryService:

    @staticmethod
    async def create_material(db: AsyncSession, data: MaterialCreate) -> Material:
        obj = Material(**data.model_dump(), stock_qty=Decimal("0"))
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def list_materials(db: AsyncSession, low_stock_only: bool = False):
        q = select(Material).options(selectinload(Material.category))
        if low_stock_only:
            q = q.where(Material.stock_qty <= Material.reorder_level)
        result = await db.execute(q)
        return result.scalars().all()

    @staticmethod
    async def get_material(db: AsyncSession, material_id: int) -> Material | None:
        result = await db.execute(
            select(Material)
            .options(selectinload(Material.category), selectinload(Material.movements))
            .where(Material.id == material_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def record_movement(db: AsyncSession, data: MovementCreate) -> StockMovement:
        total = data.qty * data.unit_cost
        movement = StockMovement(
            **data.model_dump(),
            total_cost=total,
        )
        db.add(movement)

        # Update stock_qty
        mat = await db.get(Material, data.material_id)
        if mat:
            if data.movement_type == "in":
                mat.stock_qty += data.qty
            else:
                mat.stock_qty -= data.qty
            mat.unit_cost = data.unit_cost  # FIFO simplified: update to latest cost

        await db.commit()
        await db.refresh(movement)
        return movement

    @staticmethod
    async def get_movements(db: AsyncSession, material_id: int):
        result = await db.execute(
            select(StockMovement)
            .where(StockMovement.material_id == material_id)
            .order_by(StockMovement.movement_date.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def low_stock_alerts(db: AsyncSession):
        result = await db.execute(
            select(Material).where(Material.stock_qty <= Material.reorder_level)
        )
        return result.scalars().all()
