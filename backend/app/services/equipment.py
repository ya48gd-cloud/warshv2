from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import Equipment, BOMLine, EquipmentDimension, Material
from app.schemas.schemas import EquipmentCreate, BOMLineCreate, EquipmentTreeOut


class EquipmentService:

    @staticmethod
    async def create_equipment(db: AsyncSession, data: EquipmentCreate) -> Equipment:
        obj = Equipment(**data.model_dump())
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def list_root_equipment(db: AsyncSession):
        """Returns top-level equipment only (no eager loading)"""
        result = await db.execute(
            select(Equipment)
            .where(Equipment.parent_id.is_(None))
            .order_by(Equipment.id)
        )
        return result.scalars().all()

    @staticmethod
    async def get_all_trees(db: AsyncSession):
        """Return every root equipment item with its full nested children."""
        roots = await EquipmentService.list_root_equipment(db)
        trees = []
        for root in roots:
            root_eq = await EquipmentService.get_equipment(db, root.id)
            if root_eq:
                trees.append(await EquipmentService._build_node(db, root_eq, 0))
        return trees

    @staticmethod
    async def get_equipment(db: AsyncSession, eq_id: int):
        result = await db.execute(
            select(Equipment)
            .options(
                selectinload(Equipment.bom_lines).selectinload(BOMLine.material),
                selectinload(Equipment.dimensions),
            )
            .where(Equipment.id == eq_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_direct_children(db: AsyncSession, parent_id: int):
        result = await db.execute(
            select(Equipment)
            .where(Equipment.parent_id == parent_id)
            .order_by(Equipment.id)
        )
        return result.scalars().all()

    @staticmethod
    async def get_full_tree(db: AsyncSession, eq_id: int):
        """Recursively build equipment tree with costs rolled up."""
        eq = await EquipmentService.get_equipment(db, eq_id)
        if not eq:
            return None
        return await EquipmentService._build_node(db, eq, 0)

    @staticmethod
    async def _build_node(db: AsyncSession, eq: Equipment, depth: int) -> dict:
        bom_cost = sum(Decimal(str(line.total_cost)) for line in (eq.bom_lines or []))

        children_result = await db.execute(
            select(Equipment)
            .where(Equipment.parent_id == eq.id)
            .order_by(Equipment.id)
        )
        children_rows = children_result.scalars().all()

        children_nodes = []
        for child in children_rows:
            child_eq = await EquipmentService.get_equipment(db, child.id)
            if child_eq:
                child_node = await EquipmentService._build_node(db, child_eq, depth + 1)
                children_nodes.append(child_node)
                bom_cost += Decimal(str(child_node.get("bom_total_cost") or 0))

        return {
            "id": eq.id,
            "code": eq.code,
            "name_ar": eq.name_ar,
            "name_en": eq.name_en,
            "description": eq.description,
            "parent_id": eq.parent_id,
            "level": eq.level,
            "weight_kg": float(eq.weight_kg) if eq.weight_kg else None,
            "cad_drawing_no": eq.cad_drawing_no,
            "cad_file_path": eq.cad_file_path,
            "is_active": eq.is_active,
            "created_at": eq.created_at.isoformat() if eq.created_at else None,
            "updated_at": eq.updated_at.isoformat() if eq.updated_at else None,
            "bom_total_cost": float(bom_cost),
            "dimensions": [{"id": d.id, "dim_key": d.dim_key, "dim_value": d.dim_value, "unit": d.unit}
                           for d in (eq.dimensions or [])],
            "children": children_nodes,
        }

    @staticmethod
    async def add_bom_line(db: AsyncSession, data: BOMLineCreate) -> BOMLine:
        payload = data.model_dump()
        material = await db.get(Material, data.material_id)
        qty = data.qty
        waste_kg = (data.waste_g or Decimal("0")) / Decimal("1000")

        if material and data.dim_length_cm and data.dim_width_cm and material.density_kg_m2:
            count = Decimal(str(data.dim_count or 1))
            area = (data.dim_length_cm / 100) * (data.dim_width_cm / 100) * count
            qty = (area * material.density_kg_m2) + waste_kg
            payload["dim_area_m2"] = area
            payload["calc_weight_kg"] = qty

        if material and data.bar_length_cm and material.weight_per_meter_kg:
            count = Decimal(str(data.dim_count or 1))
            qty = (data.bar_length_cm / 100) * material.weight_per_meter_kg * count + waste_kg
            payload["calc_weight_kg"] = qty

        payload["qty"] = qty
        total = qty * data.unit_cost
        line = BOMLine(**payload, total_cost=total)
        db.add(line)
        await db.commit()
        await db.refresh(line)
        return line

    @staticmethod
    async def get_bom(db: AsyncSession, equipment_id: int):
        result = await db.execute(
            select(BOMLine)
            .options(selectinload(BOMLine.material))
            .where(BOMLine.equipment_id == equipment_id)
        )
        return result.scalars().all()

    @staticmethod
    async def calculate_bom_cost(db: AsyncSession, equipment_id: int) -> dict:
        lines_result = await db.execute(
            select(BOMLine).options(selectinload(BOMLine.material))
            .where(BOMLine.equipment_id == equipment_id)
        )
        lines = lines_result.scalars().all()
        breakdown = {"material_cost": 0.0, "total_weight_kg": 0.0, "lines": []}
        for line in lines:
            breakdown["material_cost"] += float(line.total_cost)
            if (line.material.material_kind or "general") != "consumable":
                breakdown["total_weight_kg"] += float(line.calc_weight_kg or line.qty or 0)
            breakdown["lines"].append({
                "material_code": line.material.code,
                "material_name": line.material.name_ar,
                "qty": float(line.qty),
                "unit": line.material.unit,
                "unit_cost": float(line.unit_cost),
                "total_cost": float(line.total_cost),
                "id": line.id,
                "material_id": line.material_id,
                "equipment_id": line.equipment_id,
                "material": {
                    "id": line.material.id,
                    "code": line.material.code,
                    "name_ar": line.material.name_ar,
                    "unit": line.material.unit,
                    "unit_cost": float(line.material.unit_cost),
                    "material_kind": line.material.material_kind,
                    "is_plate": line.material.is_plate,
                    "density_kg_m2": float(line.material.density_kg_m2) if line.material.density_kg_m2 else None,
                    "weight_per_meter_kg": float(line.material.weight_per_meter_kg) if line.material.weight_per_meter_kg else None,
                },
                "dim_length_cm": float(line.dim_length_cm) if line.dim_length_cm else None,
                "dim_width_cm": float(line.dim_width_cm) if line.dim_width_cm else None,
                "dim_count": line.dim_count,
                "dim_area_m2": float(line.dim_area_m2) if line.dim_area_m2 else None,
                "calc_weight_kg": float(line.calc_weight_kg) if line.calc_weight_kg else None,
                "bar_length_cm": float(line.bar_length_cm) if line.bar_length_cm else None,
                "waste_g": float(line.waste_g) if line.waste_g else None,
            })
        breakdown["total_cost"] = breakdown["material_cost"]
        return breakdown

    @staticmethod
    async def add_dimension(db: AsyncSession, equipment_id: int,
                            dim_key: str, dim_value: str, unit: str):
        dim = EquipmentDimension(equipment_id=equipment_id, dim_key=dim_key,
                                 dim_value=dim_value, unit=unit)
        db.add(dim)
        await db.commit()
        await db.refresh(dim)
        return dim
