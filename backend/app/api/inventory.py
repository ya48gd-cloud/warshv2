from datetime import date
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.services.inventory import InventoryService
from app.models.models import Material, StockMovement

router = APIRouter(prefix="/inventory", tags=["inventory"])


class MaterialCreate(BaseModel):
    code: str
    name_ar: str
    name_en: str
    unit: str
    unit_cost: Decimal
    reorder_level: Decimal = Decimal("0")
    category_id: int
    supplier: Optional[str] = None
    material_kind: str = "general"  # general | plate | bar | consumable
    is_plate: bool = False
    thickness_mm: Optional[Decimal] = None
    density_kg_m2: Optional[Decimal] = None
    plate_length_cm: Optional[Decimal] = None
    plate_width_cm: Optional[Decimal] = None
    plate_weight_kg: Optional[Decimal] = None
    bar_length_cm: Optional[Decimal] = None
    bar_weight_kg: Optional[Decimal] = None
    weight_per_meter_kg: Optional[Decimal] = None


class MovementCreate(BaseModel):
    material_id: int
    movement_type: str          # in | out
    qty: Decimal                # الوزن / الكمية دائماً بالوحدة الأساسية
    unit_cost: Decimal
    reference: Optional[str] = None
    notes: Optional[str] = None
    movement_date: date
    # حقول السحب
    withdrawal_unit: Optional[str] = None     # weight | piece | plate_area
    withdrawal_type: Optional[str] = None     # legacy alias
    destination: Optional[str] = None
    destination_ref: Optional[str] = None
    pieces_count: Optional[int] = None
    weight_per_piece: Optional[Decimal] = None
    work_order_id: Optional[int] = None
    # حقول الألواح الفولاذية (للوارد: أبعاد اللوح الواحد + وزنه)
    plate_length_cm: Optional[Decimal] = None
    plate_width_cm: Optional[Decimal] = None
    plate_weight_kg: Optional[Decimal] = None  # الوزن الكلي للوح (لحساب الكثافة)
    # حقول BOM الألواح (للصادر: أبعاد القطع المطلوبة)
    plate_pieces: Optional[int] = None         # عدد الألواح/القطع
    bar_length_cm: Optional[Decimal] = None
    waste_g: Optional[Decimal] = None


def enrich_material_shape(payload: dict) -> dict:
    kind = payload.get("material_kind") or ("plate" if payload.get("is_plate") else "general")
    payload["material_kind"] = kind
    payload["is_plate"] = bool(payload.get("is_plate") or kind == "plate")

    if kind == "plate":
        length = payload.get("plate_length_cm")
        width = payload.get("plate_width_cm")
        weight = payload.get("plate_weight_kg")
        if length and width and weight and Decimal(str(length)) > 0 and Decimal(str(width)) > 0:
            area = (Decimal(str(length)) / 100) * (Decimal(str(width)) / 100)
            payload["density_kg_m2"] = Decimal(str(weight)) / area

    if kind == "bar":
        length = payload.get("bar_length_cm")
        weight = payload.get("bar_weight_kg")
        if length and weight and Decimal(str(length)) > 0:
            payload["weight_per_meter_kg"] = Decimal(str(weight)) / (Decimal(str(length)) / 100)

    return payload


@router.post("/materials", status_code=201)
async def create_material(data: MaterialCreate, db: AsyncSession = Depends(get_db)):
    payload = enrich_material_shape(data.model_dump())
    mat = Material(**payload, stock_qty=Decimal("0"))
    db.add(mat)
    await db.commit()
    await db.refresh(mat)
    return mat


@router.get("/materials")
async def list_materials(low_stock_only: bool = False, db: AsyncSession = Depends(get_db)):
    return await InventoryService.list_materials(db, low_stock_only)


@router.get("/materials/{material_id}")
async def get_material(material_id: int, db: AsyncSession = Depends(get_db)):
    mat = await InventoryService.get_material(db, material_id)
    if not mat: raise HTTPException(404, "Material not found")
    return mat


@router.put("/materials/{material_id}")
async def update_material(material_id: int, data: MaterialCreate, db: AsyncSession = Depends(get_db)):
    mat = await db.get(Material, material_id)
    if not mat: raise HTTPException(404)
    for k, v in enrich_material_shape(data.model_dump()).items():
        setattr(mat, k, v)
    await db.commit()
    await db.refresh(mat)
    return {"id": mat.id, "code": mat.code, "name_ar": mat.name_ar,
            "unit_cost": float(mat.unit_cost), "stock_qty": float(mat.stock_qty)}


@router.post("/movements", status_code=201)
async def record_movement(data: MovementCreate, db: AsyncSession = Depends(get_db)):
    mat = await db.get(Material, data.material_id)
    if not mat:
        raise HTTPException(404, "Material not found")

    # لو السحب بالقطع - احسب الوزن الإجمالي
    actual_qty = data.qty
    withdrawal_unit = getattr(data, 'withdrawal_unit', None) or getattr(data, 'withdrawal_type', None)
    pieces_count = getattr(data, 'pieces_count', None)
    weight_per_piece = getattr(data, 'weight_per_piece', None)
    plate_area_m2 = None
    waste_kg = (Decimal(str(data.waste_g)) / 1000) if data.waste_g else Decimal("0")

    if withdrawal_unit == "piece" and pieces_count and weight_per_piece:
        actual_qty = Decimal(str(pieces_count)) * Decimal(str(weight_per_piece))

    elif withdrawal_unit == "plate_area":
        # Plate area mode: compute area → convert to kg using material density
        length_cm = getattr(data, 'plate_length_cm', None)
        width_cm  = getattr(data, 'plate_width_cm', None)
        plate_pcs = getattr(data, 'plate_pieces', None) or 1
        if length_cm and width_cm:
            area_per_piece = (Decimal(str(length_cm)) / 100) * (Decimal(str(width_cm)) / 100)
            plate_area_m2  = area_per_piece * plate_pcs
            # Use material density to convert m² → kg
            if mat.density_kg_m2 and mat.density_kg_m2 > 0:
                actual_qty = (plate_area_m2 * mat.density_kg_m2) + waste_kg
            else:
                actual_qty = data.qty  # fallback to manual qty

    elif withdrawal_unit == "plate_count" and pieces_count and mat.plate_weight_kg:
        actual_qty = Decimal(str(pieces_count)) * mat.plate_weight_kg

    elif withdrawal_unit == "bar_length" and data.bar_length_cm and mat.weight_per_meter_kg:
        count = Decimal(str(pieces_count or 1))
        actual_qty = (Decimal(str(data.bar_length_cm)) / 100) * mat.weight_per_meter_kg * count + waste_kg

    elif withdrawal_unit == "bar_count" and pieces_count and mat.bar_length_cm and mat.weight_per_meter_kg:
        actual_qty = Decimal(str(pieces_count)) * (mat.bar_length_cm / 100) * mat.weight_per_meter_kg

    # For IN with plate dims: compute area and density
    if data.movement_type == "in":
        length_cm    = getattr(data, 'plate_length_cm', None)
        width_cm     = getattr(data, 'plate_width_cm', None)
        plate_weight = getattr(data, 'plate_weight_kg', None)
        if length_cm and width_cm and plate_weight and Decimal(str(plate_weight)) > 0:
            area_m2 = (Decimal(str(length_cm)) / 100) * (Decimal(str(width_cm)) / 100)
            plate_area_m2 = area_m2
            computed_density = Decimal(str(plate_weight)) / area_m2
            # Update material density
            mat.density_kg_m2 = computed_density
            mat.is_plate = True
            mat.material_kind = "plate"
            mat.plate_length_cm = Decimal(str(length_cm))
            mat.plate_width_cm = Decimal(str(width_cm))
            mat.plate_weight_kg = Decimal(str(plate_weight))

    total = actual_qty * data.unit_cost
    movement = StockMovement(
        material_id=data.material_id,
        movement_type=data.movement_type,
        qty=actual_qty,
        unit_cost=data.unit_cost,
        plate_length_cm=getattr(data, 'plate_length_cm', None),
        plate_width_cm=getattr(data, 'plate_width_cm', None),
        plate_area_m2=plate_area_m2,
        plate_weight_kg=getattr(data, 'plate_weight_kg', None),
        bar_length_cm=getattr(data, 'bar_length_cm', None),
        waste_g=getattr(data, 'waste_g', None),
        total_cost=total,
        reference=data.reference,
        notes=getattr(data, 'notes', None),
        movement_date=data.movement_date,
        withdrawal_unit=withdrawal_unit,
        destination=getattr(data, 'destination', None),
        destination_ref=getattr(data, 'destination_ref', None),
        pieces_count=pieces_count,
        weight_per_piece=Decimal(str(weight_per_piece)) if weight_per_piece else None,
    )
    db.add(movement)

    if data.movement_type == "in":
        mat.stock_qty += actual_qty
        mat.unit_cost = data.unit_cost
    else:
        mat.stock_qty -= actual_qty

    await db.commit()
    await db.refresh(movement)
    return {
        "id": movement.id,
        "actual_qty": float(actual_qty),
        "plate_area_m2": float(plate_area_m2) if plate_area_m2 else None,
        "total_cost": float(total),
        "pieces_count": movement.pieces_count,
        "destination": movement.destination,
    }


@router.get("/movements/{material_id}")
async def get_movements(material_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StockMovement)
        .where(StockMovement.material_id == material_id)
        .order_by(StockMovement.movement_date.desc())
    )
    movs = result.scalars().all()
    dest_ar = {"workshop": "الورشة", "customer": "عميل خارجي"}
    return [{
        "id": m.id, "movement_type": m.movement_type,
        "qty": float(m.qty), "unit_cost": float(m.unit_cost),
        "total_cost": float(m.total_cost),
        "movement_date": str(m.movement_date),
        "reference": m.reference, "notes": m.notes,
        "withdrawal_unit": getattr(m, "withdrawal_unit", None),
        "destination": m.destination,
        "destination_ar": dest_ar.get(m.destination or "", m.destination or ""),
        "destination_ref": m.destination_ref,
        "pieces_count": m.pieces_count,
        "weight_per_piece": float(m.weight_per_piece) if m.weight_per_piece else None,
        "plate_length_cm": float(m.plate_length_cm) if m.plate_length_cm else None,
        "plate_width_cm": float(m.plate_width_cm) if m.plate_width_cm else None,
        "plate_area_m2": float(m.plate_area_m2) if m.plate_area_m2 else None,
        "plate_weight_kg": float(m.plate_weight_kg) if m.plate_weight_kg else None,
        "bar_length_cm": float(m.bar_length_cm) if m.bar_length_cm else None,
        "waste_g": float(m.waste_g) if m.waste_g else None,
    } for m in movs]


@router.get("/alerts/low-stock")
async def low_stock_alerts(db: AsyncSession = Depends(get_db)):
    items = await InventoryService.low_stock_alerts(db)
    return [{"id": m.id, "code": m.code, "name_ar": m.name_ar,
             "stock_qty": float(m.stock_qty), "reorder_level": float(m.reorder_level)} for m in items]


@router.put("/materials/{material_id}/price")
async def update_material_price(material_id: int, new_price: float,
                                 db: AsyncSession = Depends(get_db)):
    """تحديث سعر المادة — يُحدِّث BOM تلقائياً عبر الـ trigger"""
    mat = await db.get(Material, mat_id := material_id)
    if not mat: raise HTTPException(404, "Material not found")
    mat.unit_cost = new_price
    await db.commit()
    return {"id": mat.id, "code": mat.code, "new_price": float(mat.unit_cost)}


@router.get("/categories")
async def list_categories(db: AsyncSession = Depends(get_db)):
    """Return all material categories"""
    from app.models.models import MaterialCategory
    result = await db.execute(select(MaterialCategory).order_by(MaterialCategory.name_ar))
    cats = result.scalars().all()
    return [{"id": c.id, "name_ar": c.name_ar, "name_en": c.name_en} for c in cats]


@router.delete("/materials/{material_id}", status_code=204)
async def delete_material(material_id: int, db: AsyncSession = Depends(get_db)):
    mat = await db.get(Material, material_id)
    if not mat:
        raise HTTPException(404, "Material not found")
    await db.delete(mat)
    await db.commit()
