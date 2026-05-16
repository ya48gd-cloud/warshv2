from __future__ import annotations
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, ConfigDict

# ── Material ──────────────────────────────────────────────────────────────────
class MaterialBase(BaseModel):
    code: str
    name_ar: str
    name_en: str
    unit: str
    unit_cost: Decimal
    reorder_level: Decimal = Decimal("0")
    category_id: int
    supplier: Optional[str] = None
    material_kind: str = "general"
    is_plate: bool = False
    thickness_mm: Optional[Decimal] = None
    density_kg_m2: Optional[Decimal] = None
    plate_length_cm: Optional[Decimal] = None
    plate_width_cm: Optional[Decimal] = None
    plate_weight_kg: Optional[Decimal] = None
    bar_length_cm: Optional[Decimal] = None
    bar_weight_kg: Optional[Decimal] = None
    weight_per_meter_kg: Optional[Decimal] = None

class MaterialCreate(MaterialBase): pass

class MaterialOut(MaterialBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    stock_qty: Decimal
    created_at: datetime

# ── Stock Movement ─────────────────────────────────────────────────────────────
class MovementCreate(BaseModel):
    material_id: int
    movement_type: str       # "in" | "out"
    qty: Decimal
    unit_cost: Decimal
    reference: Optional[str] = None
    notes: Optional[str] = None
    movement_date: date

class MovementOut(MovementCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    total_cost: Decimal
    created_at: datetime

# ── Work Order ─────────────────────────────────────────────────────────────────
class WorkOrderCreate(BaseModel):
    code: str
    equipment_id: int
    planned_cost: Decimal = Decimal("0")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    notes: Optional[str] = None

class WorkOrderOut(WorkOrderCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    actual_cost: Decimal
    created_at: datetime

# ── Cost Line ──────────────────────────────────────────────────────────────────
class CostLineCreate(BaseModel):
    work_order_id: int
    cost_type: str
    description: str
    qty: Decimal = Decimal("1")
    unit_cost: Decimal
    material_id: Optional[int] = None

class CostLineOut(CostLineCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    total_cost: Decimal
    created_at: datetime

# ── Equipment ──────────────────────────────────────────────────────────────────
class DimensionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    dim_key: str
    dim_value: str
    unit: str

class EquipmentCreate(BaseModel):
    code: str
    name_ar: str
    name_en: str
    description: Optional[str] = None
    parent_id: Optional[int] = None
    level: int = 0
    weight_kg: Optional[Decimal] = None
    cad_drawing_no: Optional[str] = None

class EquipmentOut(EquipmentCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_active: bool
    created_at: datetime
    dimensions: List[DimensionOut] = []

class EquipmentTreeOut(EquipmentOut):
    """Recursive tree node"""
    children: List["EquipmentTreeOut"] = []
    bom_total_cost: Optional[Decimal] = None

EquipmentTreeOut.model_rebuild()

# ── BOM Line ───────────────────────────────────────────────────────────────────
class BOMLineCreate(BaseModel):
    equipment_id: int
    material_id: int
    qty: Decimal
    unit_cost: Decimal
    notes: Optional[str] = None
    dim_length_cm: Optional[Decimal] = None
    dim_width_cm: Optional[Decimal] = None
    dim_count: Optional[int] = None
    dim_area_m2: Optional[Decimal] = None
    calc_weight_kg: Optional[Decimal] = None
    bar_length_cm: Optional[Decimal] = None
    waste_g: Optional[Decimal] = None

class BOMLineOut(BOMLineCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    total_cost: Decimal
    material: Optional[MaterialOut] = None
    created_at: datetime

# ── Dashboard summary ──────────────────────────────────────────────────────────
class DashboardStats(BaseModel):
    total_materials: int
    low_stock_count: int
    open_work_orders: int
    total_equipment: int
    mtd_material_cost: Decimal
    mtd_labor_cost: Decimal
