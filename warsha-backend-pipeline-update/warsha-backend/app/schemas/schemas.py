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
    # ── حقول السحب / الإدخال المتقدمة ──────────────────────────────
    # weight | piece | plate_in | plate_count | plate_area | bar_count | bar_length
    withdrawal_unit:  Optional[str]     = None
    pieces_count:     Optional[int]     = None
    weight_per_piece: Optional[Decimal] = None
    destination:      Optional[str]     = None   # workshop | customer
    destination_ref:  Optional[str]     = None
    customer_id:      Optional[int]     = None
    # ── حقول الألواح الفولاذية ─────────────────────────────────────
    plate_length_cm:  Optional[Decimal] = None
    plate_width_cm:   Optional[Decimal] = None
    plate_weight_kg:  Optional[Decimal] = None
    plate_pieces:     Optional[int]     = None
    # ── حقول الحديد / القضبان ──────────────────────────────────────
    bar_length_cm:    Optional[Decimal] = None
    # ── الهدر ──────────────────────────────────────────────────────
    waste_g:          Optional[Decimal] = None
    # ── ربط بأمر إنتاج ومصدر الوارد ────────────────────────────────
    work_order_id:    Optional[int]     = None
    source:           Optional[str]     = None

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
    planned_end_date: Optional[date] = None
    priority: Optional[str] = "normal"
    notes: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[Decimal] = Decimal("1")
    quantity_unit: Optional[str] = "قطعة"
    customer_order_id: Optional[int] = None
    customer_id: Optional[int] = None
    location_id: Optional[int] = None

class WorkOrderOut(WorkOrderCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    actual_cost: Decimal
    created_at: datetime
    customer_name: Optional[str] = None
    location_name: Optional[str] = None

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
    code: Optional[str] = None   # يُولَّد تلقائياً إن لم يُذكر
    name_ar: str
    name_en: Optional[str] = ""
    description: Optional[str] = None
    parent_id: Optional[int] = None
    category_id: Optional[int] = None
    level: int = 0
    weight_kg: Optional[Decimal] = None
    cad_drawing_no: Optional[str] = None
    serial_number:  Optional[str]     = None
    equip_status:   Optional[str]     = "active"
    purchase_date:  Optional[date]    = None
    purchase_value: Optional[Decimal] = None
    purchase_invoice_id: Optional[int] = None  # محطة 23: ربط رسمي بفاتورة شراء حقيقية
    calc_unit:      Optional[str]     = "piece"   # piece | meter | ton_hr | kw
    base_qty:       Optional[Decimal] = Decimal("1")
    length_min:     Optional[Decimal] = None
    length_max:     Optional[Decimal] = None

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
    line_type: Optional[str] = "fixed"          # fixed | variable
    scale_dim_length: Optional[bool] = False

class BOMLineOut(BOMLineCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    total_cost: Decimal
    material: Optional[MaterialOut] = None
    created_at: datetime

# ── Equipment Cost Line ────────────────────────────────────────────────────────
class EquipmentCostLineCreate(BaseModel):
    equipment_id: Optional[int] = None  # يُملأ من مسار الـ URL
    cost_type: str       # labor | electricity | consumable | overhead
    description: str
    cost_per_unit: Decimal = Decimal("0")

class EquipmentCostLineOut(EquipmentCostLineCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime

# ── Work Order BOM Line (snapshot) ─────────────────────────────────────────────
class WorkOrderBOMLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    work_order_id: int
    material_id: int
    required_qty: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    notes: Optional[str] = None

# ── Quotation → Order conversion ───────────────────────────────────────────────
class QuotationToOrderRequest(BaseModel):
    delivery_date: Optional[date] = None
    notes: Optional[str] = None

# ── Order → WorkOrder conversion ───────────────────────────────────────────────
class OrderToWorkOrderRequest(BaseModel):
    equipment_id: int
    quantity: Decimal = Decimal("1")
    quantity_unit: str = "قطعة"
    start_date: Optional[date] = None
    planned_end_date: Optional[date] = None
    notes: Optional[str] = None

# ── WorkOrder → Invoice conversion ─────────────────────────────────────────────
class WorkOrderToInvoiceRequest(BaseModel):
    tax_pct: Decimal = Decimal("0")
    discount_pct: Decimal = Decimal("0")
    notes: Optional[str] = None

# ── Advance payment on CustomerOrder ──────────────────────────────────────────
class OrderAdvancePaymentRequest(BaseModel):
    amount: Decimal
    payment_type: str = "cash"
    payment_date: date
    treasury_id: Optional[int] = None
    reference: Optional[str] = None
    notes: Optional[str] = None

# ── Dashboard summary ──────────────────────────────────────────────────────────
class DashboardStats(BaseModel):
    total_materials: int
    low_stock_count: int
    open_work_orders: int
    total_equipment: int
    mtd_material_cost: Decimal
    mtd_labor_cost: Decimal
