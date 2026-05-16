from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.equipment import EquipmentService
from app.schemas.schemas import EquipmentCreate, EquipmentOut, BOMLineCreate, BOMLineOut
from app.models.models import Equipment

router = APIRouter(prefix="/equipment", tags=["equipment"])


@router.post("", status_code=201)
async def create_equipment(data: EquipmentCreate, db: AsyncSession = Depends(get_db)):
    eq = await EquipmentService.create_equipment(db, data)
    return {"id": eq.id, "code": eq.code, "name_ar": eq.name_ar,
            "name_en": eq.name_en, "level": eq.level, "parent_id": eq.parent_id,
            "weight_kg": float(eq.weight_kg) if eq.weight_kg else None,
            "cad_drawing_no": eq.cad_drawing_no, "is_active": eq.is_active,
            "created_at": eq.created_at.isoformat() if eq.created_at else None,
            "updated_at": eq.updated_at.isoformat() if eq.updated_at else None,
            "dimensions": []}


@router.get("")
async def list_root_equipment(db: AsyncSession = Depends(get_db)):
    eqs = await EquipmentService.list_root_equipment(db)
    return [{"id": e.id, "code": e.code, "name_ar": e.name_ar,
             "name_en": e.name_en, "level": e.level, "parent_id": e.parent_id,
             "weight_kg": float(e.weight_kg) if e.weight_kg else None,
             "cad_drawing_no": e.cad_drawing_no, "is_active": e.is_active,
             "created_at": e.created_at.isoformat() if e.created_at else None,
             "updated_at": e.updated_at.isoformat() if e.updated_at else None,
             "dimensions": []} for e in eqs]


@router.get("/tree/all")
async def get_all_equipment_trees(db: AsyncSession = Depends(get_db)):
    return await EquipmentService.get_all_trees(db)


@router.get("/{eq_id}")
async def get_equipment(eq_id: int, db: AsyncSession = Depends(get_db)):
    eq = await EquipmentService.get_equipment(db, eq_id)
    if not eq:
        raise HTTPException(404, "Equipment not found")
    return {"id": eq.id, "code": eq.code, "name_ar": eq.name_ar,
            "name_en": eq.name_en, "level": eq.level, "parent_id": eq.parent_id,
            "weight_kg": float(eq.weight_kg) if eq.weight_kg else None,
            "cad_drawing_no": eq.cad_drawing_no, "is_active": eq.is_active,
            "created_at": eq.created_at.isoformat() if eq.created_at else None,
            "updated_at": eq.updated_at.isoformat() if eq.updated_at else None,
            "dimensions": [{"id": d.id, "dim_key": d.dim_key,
                            "dim_value": d.dim_value, "unit": d.unit}
                           for d in (eq.dimensions or [])]}


@router.get("/{eq_id}/tree")
async def get_equipment_tree(eq_id: int, db: AsyncSession = Depends(get_db)):
    tree = await EquipmentService.get_full_tree(db, eq_id)
    if not tree:
        raise HTTPException(404, "Equipment not found")
    return tree


@router.get("/{eq_id}/bom")
async def get_bom(eq_id: int, db: AsyncSession = Depends(get_db)):
    lines = await EquipmentService.get_bom(db, eq_id)
    return [{"id": l.id, "equipment_id": l.equipment_id,
             "material_id": l.material_id, "qty": float(l.qty),
             "unit_cost": float(l.unit_cost), "total_cost": float(l.total_cost),
             "notes": l.notes,
             "dim_length_cm": float(l.dim_length_cm) if l.dim_length_cm else None,
             "dim_width_cm": float(l.dim_width_cm) if l.dim_width_cm else None,
             "dim_count": l.dim_count,
             "dim_area_m2": float(l.dim_area_m2) if l.dim_area_m2 else None,
             "calc_weight_kg": float(l.calc_weight_kg) if l.calc_weight_kg else None,
             "bar_length_cm": float(l.bar_length_cm) if l.bar_length_cm else None,
             "waste_g": float(l.waste_g) if l.waste_g else None,
             "material": {"id": l.material.id, "code": l.material.code,
                          "name_ar": l.material.name_ar, "unit": l.material.unit,
                          "unit_cost": float(l.material.unit_cost),
                          "material_kind": l.material.material_kind, "is_plate": l.material.is_plate,
                          "density_kg_m2": float(l.material.density_kg_m2) if l.material.density_kg_m2 else None,
                          "weight_per_meter_kg": float(l.material.weight_per_meter_kg) if l.material.weight_per_meter_kg else None} if l.material else None}
            for l in lines]


@router.get("/{eq_id}/bom/cost")
async def bom_cost_breakdown(eq_id: int, db: AsyncSession = Depends(get_db)):
    return await EquipmentService.calculate_bom_cost(db, eq_id)


@router.post("/bom", status_code=201)
async def add_bom_line(data: BOMLineCreate, db: AsyncSession = Depends(get_db)):
    line = await EquipmentService.add_bom_line(db, data)
    return {"id": line.id, "equipment_id": line.equipment_id,
            "material_id": line.material_id, "qty": float(line.qty),
            "unit_cost": float(line.unit_cost), "total_cost": float(line.total_cost),
            "calc_weight_kg": float(line.calc_weight_kg) if line.calc_weight_kg else None}


@router.post("/{eq_id}/dimensions")
async def add_dimension(eq_id: int, dim_key: str, dim_value: str, unit: str,
                        db: AsyncSession = Depends(get_db)):
    return await EquipmentService.add_dimension(db, eq_id, dim_key, dim_value, unit)


@router.put("/{eq_id}")
async def update_equipment(eq_id: int, data: EquipmentCreate, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import update
    eq = await db.get(Equipment, eq_id)
    if not eq:
        raise HTTPException(404, "Equipment not found")
    for key, value in data.model_dump().items():
        setattr(eq, key, value)
    await db.commit()
    await db.refresh(eq)
    return {"id": eq.id, "code": eq.code, "name_ar": eq.name_ar, "name_en": eq.name_en,
            "level": eq.level, "parent_id": eq.parent_id,
            "weight_kg": float(eq.weight_kg) if eq.weight_kg else None,
            "cad_drawing_no": eq.cad_drawing_no, "is_active": eq.is_active,
            "created_at": eq.created_at.isoformat() if eq.created_at else None,
            "updated_at": eq.updated_at.isoformat() if eq.updated_at else None,
            "dimensions": []}


@router.delete("/{eq_id}")
async def delete_equipment(eq_id: int, db: AsyncSession = Depends(get_db)):
    from app.models.models import BOMLine, EquipmentDimension, CADFile
    import os
    from pathlib import Path

    eq = await db.get(Equipment, eq_id)
    if not eq:
        raise HTTPException(404, "Equipment not found")

    # Delete CAD files from disk
    try:
        cad_result = await db.execute(
            select(CADFile).where(CADFile.equipment_id == eq_id)
        )
        cad_files = cad_result.scalars().all()
        upload_dir = Path(os.getenv("ERP_UPLOAD_DIR", "/app/uploads")) / "cad"
        for cf in cad_files:
            fp = upload_dir / cf.stored_name
            if fp.exists():
                fp.unlink()
            await db.delete(cf)
    except Exception:
        pass

    # Delete dimensions and BOM lines
    dim_result = await db.execute(select(EquipmentDimension).where(EquipmentDimension.equipment_id == eq_id))
    for d in dim_result.scalars().all():
        await db.delete(d)

    bom_result = await db.execute(select(BOMLine).where(BOMLine.equipment_id == eq_id))
    for b in bom_result.scalars().all():
        await db.delete(b)

    await db.delete(eq)
    await db.commit()
    return {"message": "تم الحذف", "id": eq_id}


@router.delete("/dimension/{dim_id}")
async def delete_dimension(dim_id: int, db: AsyncSession = Depends(get_db)):
    from app.models.models import EquipmentDimension
    dim = await db.get(EquipmentDimension, dim_id)
    if not dim:
        raise HTTPException(404, "Dimension not found")
    await db.delete(dim)
    await db.commit()
    return {"message": "تم الحذف", "id": dim_id}
