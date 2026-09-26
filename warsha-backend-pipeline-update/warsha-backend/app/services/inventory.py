from decimal import Decimal
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import Material, MaterialCategory, MaterialReservation
from app.schemas.schemas import MaterialCreate

OPEN_RESERVATION_STATUSES = ('pending', 'partial', 'reserved', 'partially_issued')


async def _reserved_by_material(db: AsyncSession) -> dict:
    """إجمالي الكميات المحجوزة (المتبقية بدون صرف) لكل مادة، من الحجوزات المفتوحة"""
    rows = (await db.execute(
        select(
            MaterialReservation.material_id,
            func.sum(MaterialReservation.reserved_qty - MaterialReservation.issued_qty),
        )
        .where(MaterialReservation.status.in_(OPEN_RESERVATION_STATUSES))
        .group_by(MaterialReservation.material_id)
    )).all()
    return {mid: (qty or Decimal("0")) for mid, qty in rows}


async def get_reserved_qty(db: AsyncSession, material_id: int) -> Decimal:
    """إجمالي الكمية المحجوزة (المتبقية بدون صرف) لمادة واحدة، من الحجوزات المفتوحة فقط.
    تُستخدم لمنع صرف/تعديل رصيد يتعدى على كمية محجوزة لأمر إنتاج آخر."""
    result = await db.execute(
        select(func.sum(MaterialReservation.reserved_qty - MaterialReservation.issued_qty))
        .where(
            MaterialReservation.material_id == material_id,
            MaterialReservation.status.in_(OPEN_RESERVATION_STATUSES),
        )
    )
    reserved = result.scalar() or Decimal("0")
    return reserved if reserved > 0 else Decimal("0")


async def get_available_qty(db: AsyncSession, material_id: int, stock_qty: Decimal) -> Decimal:
    """المتاح الفعلي = الرصيد الكلي - المحجوز لأوامر إنتاج (بغض النظر عن الأمر)."""
    reserved = await get_reserved_qty(db, material_id)
    available = (stock_qty or Decimal("0")) - reserved
    return available if available > 0 else Decimal("0")


def _attach_reserved_available(mat: Material, reserved_map: dict) -> Material:
    reserved = reserved_map.get(mat.id, Decimal("0"))
    if reserved < 0:
        reserved = Decimal("0")
    mat.reserved_qty = reserved
    mat.available_qty = mat.stock_qty - reserved
    return mat


class InventoryService:

    @staticmethod
    async def create_material(db: AsyncSession, data: MaterialCreate) -> Material:
        from app.core.database import current_tenant_id as _ctid_matsvc
        _mat = data.model_dump()
        _mat['tenant_id'] = _ctid_matsvc.get() or 1
        obj = Material(**_mat, stock_qty=Decimal("0"))
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
        materials = result.scalars().all()
        reserved_map = await _reserved_by_material(db)
        return [_attach_reserved_available(m, reserved_map) for m in materials]

    @staticmethod
    async def get_material(db: AsyncSession, material_id: int) -> Material | None:
        result = await db.execute(
            select(Material)
            .options(selectinload(Material.category), selectinload(Material.movements))
            .where(Material.id == material_id)
        )
        mat = result.scalar_one_or_none()
        if mat:
            reserved_map = await _reserved_by_material(db)
            _attach_reserved_available(mat, reserved_map)
        return mat

    @staticmethod
    async def low_stock_alerts(db: AsyncSession):
        result = await db.execute(
            select(Material).where(Material.stock_qty <= Material.reorder_level)
        )
        return result.scalars().all()
