"""
SQLAlchemy models — all enum columns use String to avoid
PostgreSQL native enum casting issues.
"""
from __future__ import annotations
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    String, Numeric, Integer, Boolean, Text, DateTime, Date, JSON,
    ForeignKey, func, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


# ═════════════════════════════════════════════════════════════════════════════
# TENANT — العميل (الورشة المشتركة في الـ SaaS)
# ═════════════════════════════════════════════════════════════════════════════

class Tenant(Base):
    __tablename__ = "tenants"
    id:         Mapped[int]            = mapped_column(primary_key=True)
    name:       Mapped[str]            = mapped_column(String(200))
    slug:       Mapped[str]            = mapped_column(String(50), unique=True, index=True)  # للـ subdomain لاحقاً
    plan:       Mapped[str]            = mapped_column(String(20), default="free")           # free | pro | enterprise
    is_active:  Mapped[bool]           = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime]       = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


# محطة 10: المخازن المنطقية (Warehouse) — راجع Blueprint §3.1
# كل tenant بياخد 8 مخازن افتراضية عند أول تشغيل (RAW/SPARE/IMPORTED/WIP/FG/
# REMNANT/SCRAP/RETURNS). دي الأساس اللي هتُبنى عليه محطات لاحقة (مواقع
# تفصيلية StockLocation، وربط الحركات بمصدر/وجهة).
class Warehouse(Base):
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_warehouse_tenant_code"),)
    id:             Mapped[int]      = mapped_column(primary_key=True)
    tenant_id:      Mapped[int]      = mapped_column(ForeignKey("tenants.id"), index=True)
    code:           Mapped[str]      = mapped_column(String(20))
    name_ar:        Mapped[str]      = mapped_column(String(100))
    # warehouse_type: raw | spare | imported | wip | finished_goods | remnant | scrap | returns | general
    warehouse_type: Mapped[str]      = mapped_column(String(20), default="general")
    is_active:      Mapped[bool]     = mapped_column(Boolean, default=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── INVENTORY ─────────────────────────────────────────────────────────────────

class MaterialCategory(Base):
    __tablename__ = "material_categories"
    id:         Mapped[int]      = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    name_ar:    Mapped[str]      = mapped_column(String(120))
    name_en:    Mapped[str]      = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    materials:  Mapped[List["Material"]] = relationship(back_populates="category")


class Material(Base):
    __tablename__ = "materials"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_material_tenant_code"),)
    id:            Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:          Mapped[str]            = mapped_column(String(50), index=True)
    name_ar:       Mapped[str]            = mapped_column(String(200))
    name_en:       Mapped[str]            = mapped_column(String(200))
    unit:          Mapped[str]            = mapped_column(String(30))
    unit_cost:     Mapped[Decimal]        = mapped_column(Numeric(14, 2))
    stock_qty:     Mapped[Decimal]        = mapped_column(Numeric(14, 3), default=0)
    reorder_level: Mapped[Decimal]        = mapped_column(Numeric(14, 3), default=0)
    category_id:   Mapped[int]            = mapped_column(ForeignKey("material_categories.id"))
    supplier:      Mapped[Optional[str]]  = mapped_column(String(200))
    material_kind: Mapped[str]            = mapped_column(String(20), default="general")
    # حقول الألواح والحديد
    is_plate:      Mapped[bool]                  = mapped_column(Boolean, default=False)
    thickness_mm:  Mapped[Optional[Decimal]]     = mapped_column(Numeric(8, 2), nullable=True)
    density_kg_m2: Mapped[Optional[Decimal]]     = mapped_column(Numeric(10, 4), nullable=True)
    plate_length_cm: Mapped[Optional[Decimal]]   = mapped_column(Numeric(10, 2), nullable=True)
    plate_width_cm:  Mapped[Optional[Decimal]]   = mapped_column(Numeric(10, 2), nullable=True)
    plate_weight_kg: Mapped[Optional[Decimal]]   = mapped_column(Numeric(10, 3), nullable=True)
    bar_length_cm:   Mapped[Optional[Decimal]]   = mapped_column(Numeric(10, 2), nullable=True)
    bar_weight_kg:   Mapped[Optional[Decimal]]   = mapped_column(Numeric(10, 3), nullable=True)
    weight_per_meter_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    # حد أدنى لتسجيل النسالة (م² للألواح، سم للأسياخ) — أقل من هذا يُسجَّل كهالك نهائي مباشرة
    min_remnant_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    # حقول MRP
    min_order_qty:       Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3), nullable=True)
    lead_time_days:      Mapped[Optional[int]]     = mapped_column(Integer, nullable=True)
    last_purchase_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    supplier_id:         Mapped[Optional[int]]     = mapped_column(ForeignKey("suppliers.id", use_alter=True, name="fk_material_supplier"), nullable=True)
    # محطة 9: سياسات الصنف — حقول اختيارية بقيم افتراضية آمنة (توافق خلفي كامل)
    # stock_item_type: تصنيف لوجستي (يُستخدم لاحقًا لتوجيه المخزن الافتراضي —
    # مختلف عن material_kind اللي بيصنّف الشكل الفيزيائي لوح/سيخ/عام)
    # raw_material | finished_good | spare_part | imported_equipment | consumable
    stock_item_type: Mapped[str] = mapped_column(String(30), default="raw_material")
    # tracking: هل الصنف متتبَّع بدفعات (lot) أو أرقام تسلسلية (serial) أو لأ
    # none | lot | serial
    tracking: Mapped[str] = mapped_column(String(10), default="none")
    # valuation_method: طريقة تقييم تكلفة المخزون
    # standard (ثابتة) | moving_average (متوسط مرجّح) | last_price (آخر سعر استلام) | fifo
    # standard (ثابتة) | moving_average (متوسط مرجّح) | fifo
    valuation_method: Mapped[str] = mapped_column(String(20), default="moving_average")
    # removal_strategy: أولوية الاستهلاك عند الصرف من دفعات متعددة
    # fifo | lifo | fefo (الأقرب انتهاء أولاً) | manual
    removal_strategy: Mapped[str] = mapped_column(String(10), default="fifo")
    created_at:    Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    updated_at:    Mapped[datetime]       = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    category:      Mapped["MaterialCategory"]   = relationship(back_populates="materials")
    movements:     Mapped[List["StockMovement"]] = relationship(back_populates="material")
    bom_lines:     Mapped[List["BOMLine"]]       = relationship(back_populates="material")
    price_history: Mapped[List["MaterialPriceHistory"]] = relationship(back_populates="material")


class MaterialPriceHistory(Base):
    __tablename__ = "material_price_history"
    id:          Mapped[int]               = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    material_id: Mapped[int]               = mapped_column(ForeignKey("materials.id"))
    old_price:   Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    new_price:   Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    changed_at:  Mapped[datetime]          = mapped_column(DateTime, server_default=func.now())
    notes:       Mapped[Optional[str]]     = mapped_column(Text)
    material:    Mapped["Material"]        = relationship(back_populates="price_history")


class SheetStockItem(Base):
    """مقاس متاح من ألواح المادة (لوح قياسي أو نسالة من عملية تقطيع سابقة)"""
    __tablename__ = "sheet_stock_items"
    id:          Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:   Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    material_id: Mapped[int]            = mapped_column(ForeignKey("materials.id"), index=True)
    length_cm:   Mapped[Decimal]        = mapped_column(Numeric(10, 2))
    width_cm:    Mapped[Decimal]        = mapped_column(Numeric(10, 2))
    qty:         Mapped[int]            = mapped_column(Integer, default=1)
    source:      Mapped[str]            = mapped_column(String(20), default="purchased")  # purchased | remnant
    source_work_order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("work_orders.id"), nullable=True)
    notes:       Mapped[Optional[str]]  = mapped_column(Text)
    created_at:  Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    material: Mapped["Material"] = relationship("Material")


class BarStockItem(Base):
    """طول متاح من أسياخ/قضبان المادة (سيخ قياسي أو نسالة من عملية تقطيع سابقة)"""
    __tablename__ = "bar_stock_items"
    id:          Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:   Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    material_id: Mapped[int]            = mapped_column(ForeignKey("materials.id"), index=True)
    length_cm:   Mapped[Decimal]        = mapped_column(Numeric(10, 2))
    qty:         Mapped[int]            = mapped_column(Integer, default=1)
    source:      Mapped[str]            = mapped_column(String(20), default="purchased")  # purchased | remnant
    source_work_order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("work_orders.id"), nullable=True)
    notes:       Mapped[Optional[str]]  = mapped_column(Text)
    created_at:  Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    material: Mapped["Material"] = relationship("Material")


class StockMovement(Base):
    __tablename__ = "stock_movements"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    material_id:     Mapped[int]            = mapped_column(ForeignKey("materials.id"))
    movement_type:   Mapped[str]            = mapped_column(String(10))   # "in" | "out"
    qty:             Mapped[Decimal]        = mapped_column(Numeric(14, 3))
    unit_cost:       Mapped[Decimal]        = mapped_column(Numeric(14, 2))
    total_cost:      Mapped[Decimal]        = mapped_column(Numeric(14, 2))
    reference:       Mapped[Optional[str]]  = mapped_column(String(100))
    notes:           Mapped[Optional[str]]  = mapped_column(Text)
    movement_date:   Mapped[date]           = mapped_column(Date)
    # حقول السحب الجديدة (Migration 0006)
    withdrawal_unit: Mapped[Optional[str]]  = mapped_column(String(20))   # weight | piece
    withdrawal_qty:  Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3))
    destination:     Mapped[Optional[str]]  = mapped_column(String(50))   # workshop | customer
    destination_ref: Mapped[Optional[str]]  = mapped_column(String(100))
    customer_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("customers.id"), nullable=True)
    pieces_count:    Mapped[Optional[int]]  = mapped_column(Integer)
    weight_per_piece: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 3))
    # حقول الألواح الفولاذية
    plate_length_cm: Mapped[Optional[Decimal]]  = mapped_column(Numeric(10, 2), nullable=True)
    plate_width_cm:  Mapped[Optional[Decimal]]  = mapped_column(Numeric(10, 2), nullable=True)
    plate_area_m2:   Mapped[Optional[Decimal]]  = mapped_column(Numeric(10, 4), nullable=True)
    plate_weight_kg: Mapped[Optional[Decimal]]  = mapped_column(Numeric(10, 3), nullable=True)
    bar_length_cm:   Mapped[Optional[Decimal]]  = mapped_column(Numeric(10, 2), nullable=True)
    waste_g:         Mapped[Optional[Decimal]]  = mapped_column(Numeric(10, 2), nullable=True)
    # ربط الحركة بأمر إنتاج (إن وُجد) — يفيد في تكلفة أمر الشغل والـ traceability
    work_order_id:   Mapped[Optional[int]]   = mapped_column(ForeignKey("work_orders.id"), nullable=True, index=True)
    # مصدر الوارد (مورد / مرتجع عميل / إنتاج داخلي…)
    source:          Mapped[Optional[str]]   = mapped_column(String(100), nullable=True)
    # محطة 2: عكس الحركة بدل حذفها فعليًا (audit trail كامل)
    # state: 'posted' = حركة سارية طبيعية | 'reversed' = تم عكسها بحركة مقابلة (ولم تُحذف)
    state:                Mapped[str]           = mapped_column(String(20), default="posted")
    # لو state='reversed': رقم الحركة الجديدة اللي عكست أثر هذه الحركة
    reversed_movement_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stock_movements.id"), nullable=True)
    # محطة 3: تتبع مصدر الحركة (traceability) + رقم تسلسلي موحّد داخل كل tenant
    movement_no:      Mapped[Optional[int]]  = mapped_column(Integer, nullable=True, index=True)
    # نوع المستند المصدر للحركة: "purchase_order" | "production" | "sale_delivery" | "stock_count" | None (يدوية)
    source_doc_type:  Mapped[Optional[str]]  = mapped_column(String(50), nullable=True)
    source_doc_id:    Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    # محطة 12: مصدر ووجهة الحركة على مستوى الموقع (StockLocation) — nullable
    # للتوافق الكامل مع الحركات القديمة اللي اتسجلت قبل ما الحقلين دول يتضافوا.
    source_location_id:      Mapped[Optional[int]] = mapped_column(ForeignKey("bin_locations.id"), nullable=True)
    destination_location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("bin_locations.id"), nullable=True)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    material:        Mapped["Material"]     = relationship(back_populates="movements")


# ── ACCOUNTING ────────────────────────────────────────────────────────────────

class WorkOrder(Base):
    __tablename__ = "work_orders"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_workorder_tenant_code"),)
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:         Mapped[str]            = mapped_column(String(50))
    equipment_id: Mapped[int]            = mapped_column(ForeignKey("equipment.id"))
    customer_order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customer_orders.id"), nullable=True)
    customer_id:  Mapped[Optional[int]]  = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    location_id:  Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_locations.id"), nullable=True)
    status:       Mapped[str]            = mapped_column(String(20), default="draft")
    planned_cost: Mapped[Decimal]        = mapped_column(Numeric(16, 2), default=0)
    actual_cost:  Mapped[Decimal]        = mapped_column(Numeric(16, 2), default=0)
    start_date:      Mapped[Optional[date]] = mapped_column(Date)
    end_date:        Mapped[Optional[date]] = mapped_column(Date)
    planned_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    priority:        Mapped[str]            = mapped_column(String(20), default="normal")
    # كمية الإنتاج — تُستخدم لضرب BOM في الحساب البارامتري
    quantity:        Mapped[Decimal]        = mapped_column(Numeric(12, 4), default=1)
    quantity_unit:   Mapped[str]            = mapped_column(String(20), default="قطعة")
    notes:           Mapped[Optional[str]]  = mapped_column(Text)
    description:     Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    planned_length:  Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    equipment:       Mapped["Equipment"]       = relationship(back_populates="work_orders")
    customer_order:  Mapped[Optional["CustomerOrder"]] = relationship(back_populates="work_orders")
    customer:        Mapped[Optional["Customer"]]      = relationship("Customer", foreign_keys="WorkOrder.customer_id", lazy="noload")
    location:        Mapped[Optional["CustomerLocation"]] = relationship("CustomerLocation", foreign_keys="WorkOrder.location_id", lazy="noload")
    cost_lines:      Mapped[List["CostLine"]]  = relationship(back_populates="work_order")
    journal_entries: Mapped[List["JournalEntry"]] = relationship(back_populates="work_order")
    wo_bom_lines:    Mapped[List["WorkOrderBOMLine"]] = relationship("WorkOrderBOMLine", back_populates="work_order")
    members:         Mapped[List["WorkOrderMember"]] = relationship(back_populates="work_order")


class WorkOrderMember(Base):
    """عضو في فريق العمل على أمر إنتاج — ربط العمالة بأمر الإنتاج"""
    __tablename__ = "work_order_members"
    id:            Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    work_order_id: Mapped[int]            = mapped_column(ForeignKey("work_orders.id"), index=True)
    worker_id:     Mapped[int]            = mapped_column(ForeignKey("workers.id"), index=True)
    # دور العامل في هذا الأمر
    role:          Mapped[str]            = mapped_column(String(100), default="فني")
    # أجر الساعة الخاص بهذا الأمر (ممكن يختلف عن الأجر الأساسي)
    hourly_rate:   Mapped[Decimal]        = mapped_column(Numeric(10, 2), default=0)
    planned_hours: Mapped[Decimal]        = mapped_column(Numeric(8, 2), default=0)
    actual_hours:  Mapped[Decimal]        = mapped_column(Numeric(8, 2), default=0)
    notes:         Mapped[Optional[str]]  = mapped_column(Text)
    created_at:    Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    work_order:    Mapped["WorkOrder"]    = relationship(back_populates="members")
    worker:        Mapped["Worker"]       = relationship()


class CostLine(Base):
    __tablename__ = "cost_lines"
    id:            Mapped[int]           = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    work_order_id: Mapped[int]           = mapped_column(ForeignKey("work_orders.id"))
    cost_type:     Mapped[str]           = mapped_column(String(50))   # material|labor|overhead
    description:   Mapped[str]           = mapped_column(String(300))
    qty:           Mapped[Decimal]       = mapped_column(Numeric(14, 3), default=1)
    unit_cost:     Mapped[Decimal]       = mapped_column(Numeric(14, 2))
    total_cost:    Mapped[Decimal]       = mapped_column(Numeric(14, 2))
    material_id:   Mapped[Optional[int]] = mapped_column(ForeignKey("materials.id"), nullable=True)
    created_at:    Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    work_order:    Mapped["WorkOrder"]   = relationship(back_populates="cost_lines")


class WorkOrderBOMLine(Base):
    """لقطة BOM مجمّدة عند إنشاء/بدء أمر الإنتاج — تتبع الفعلي مقابل المخطط"""
    __tablename__ = "work_order_bom_lines"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:       Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    work_order_id:   Mapped[int]            = mapped_column(ForeignKey("work_orders.id"), index=True)
    material_id:     Mapped[int]            = mapped_column(ForeignKey("materials.id"), index=True)
    required_qty:    Mapped[Decimal]        = mapped_column(Numeric(14, 3))   # الكمية بعد الضرب في كمية WO
    unit_cost:       Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    total_cost:      Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    notes:           Mapped[Optional[str]]  = mapped_column(String(300))
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    work_order:      Mapped["WorkOrder"]    = relationship("WorkOrder", back_populates="wo_bom_lines")
    material:        Mapped["Material"]     = relationship("Material")


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id:             Mapped[int]           = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    work_order_id:  Mapped[Optional[int]] = mapped_column(ForeignKey("work_orders.id"), nullable=True)
    entry_type:     Mapped[str]           = mapped_column(String(50))
    debit_account:  Mapped[str]           = mapped_column(String(100))
    credit_account: Mapped[str]           = mapped_column(String(100))
    amount:         Mapped[Decimal]       = mapped_column(Numeric(16, 2))
    description:    Mapped[str]           = mapped_column(String(300))
    entry_date:     Mapped[date]          = mapped_column(Date)
    created_at:     Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    work_order:     Mapped[Optional["WorkOrder"]] = relationship(back_populates="journal_entries")


# ── EQUIPMENT / BOM ───────────────────────────────────────────────────────────

class EquipmentCategory(Base):
    __tablename__ = "equipment_categories"
    id:         Mapped[int]      = mapped_column(primary_key=True)
    tenant_id:  Mapped[int]      = mapped_column(ForeignKey("tenants.id"), index=True)
    name_ar:    Mapped[str]      = mapped_column(String(120))
    name_en:    Mapped[str]      = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    equipment:  Mapped[List["Equipment"]] = relationship(back_populates="category")


class Equipment(Base):
    __tablename__ = "equipment"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_equipment_tenant_code"),)
    id:             Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:           Mapped[str]            = mapped_column(String(50), index=True)
    name_ar:        Mapped[str]            = mapped_column(String(200))
    name_en:        Mapped[str]            = mapped_column(String(200))
    description:    Mapped[Optional[str]]  = mapped_column(Text)
    parent_id:      Mapped[Optional[int]]  = mapped_column(ForeignKey("equipment.id"), nullable=True)
    category_id:    Mapped[Optional[int]]  = mapped_column(ForeignKey("equipment_categories.id"), nullable=True)
    level:          Mapped[int]            = mapped_column(Integer, default=0)
    weight_kg:      Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 3))
    cad_drawing_no: Mapped[Optional[str]]  = mapped_column(String(100))
    cad_file_path:  Mapped[Optional[str]]  = mapped_column(String(500))
    is_active:      Mapped[bool]           = mapped_column(Boolean, default=True)
    serial_number:  Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    equip_status:   Mapped[str]            = mapped_column(String(30), default="active")
    purchase_date:  Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    purchase_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    # محطة 23 (الروادماب الأصلي): ربط اختياري بفاتورة الشراء الفعلية لمعدة
    # مستوردة/جاهزة (بدل الاعتماد على purchase_date/purchase_value كإدخال
    # يدوي حر بلا أي مرجع رسمي). لو مربوطة، الحقلين دول بيتحدَّدوا من
    # الفاتورة نفسها بدل الإدخال اليدوي — راجع EquipmentService.
    purchase_invoice_id: Mapped[Optional[int]] = mapped_column(ForeignKey("purchase_invoices.id"), nullable=True)
    # حقول الحساب البارامتري — نوع وحدة الحساب والكمية الأساسية
    calc_unit:      Mapped[str]            = mapped_column(String(20), default="piece")   # piece | meter | ton_hr | kw
    base_qty:       Mapped[Decimal]        = mapped_column(Numeric(12, 4), default=1)     # الكمية الأساسية (مثال: 1 قطعة، 8 متر)
    # مدى الطول للمعدات المترية (أصناف بحد أدنى وأقصى — مثال: بريمة صغيرة 2-4م)
    length_min:     Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    length_max:     Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    created_at:     Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    updated_at:     Mapped[datetime]       = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    parent:      Mapped[Optional["Equipment"]]  = relationship("Equipment", remote_side="Equipment.id", back_populates="children")
    children:    Mapped[List["Equipment"]]      = relationship("Equipment", back_populates="parent")
    category:    Mapped[Optional["EquipmentCategory"]] = relationship("EquipmentCategory", back_populates="equipment")
    bom_lines:   Mapped[List["BOMLine"]]        = relationship(back_populates="equipment", foreign_keys="BOMLine.equipment_id")
    dimensions:  Mapped[List["EquipmentDimension"]] = relationship(back_populates="equipment")
    work_orders: Mapped[List["WorkOrder"]]      = relationship(back_populates="equipment")
    cad_files:   Mapped[List["CADFile"]]          = relationship("CADFile", back_populates="equipment")
    cost_lines:  Mapped[List["EquipmentCostLine"]] = relationship("EquipmentCostLine", back_populates="equipment")


class EquipmentCostLine(Base):
    """تكاليف ثابتة مرتبطة بالمعدة — عمالة / كهرباء / مستهلكات — تُستخدم في الحساب البارامتري"""
    __tablename__ = "equipment_cost_lines"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:    Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    equipment_id: Mapped[int]            = mapped_column(ForeignKey("equipment.id"), index=True)
    # labor | electricity | consumable | overhead
    cost_type:    Mapped[str]            = mapped_column(String(30))
    description:  Mapped[str]            = mapped_column(String(300))
    cost_per_unit: Mapped[Decimal]       = mapped_column(Numeric(14, 2), default=0)  # التكلفة لكل وحدة calc_unit
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    equipment:    Mapped["Equipment"]    = relationship("Equipment", back_populates="cost_lines")


class BOMLine(Base):
    __tablename__ = "bom_lines"
    id:           Mapped[int]           = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    equipment_id: Mapped[int]           = mapped_column(ForeignKey("equipment.id"))
    material_id:  Mapped[int]           = mapped_column(ForeignKey("materials.id"))
    qty:          Mapped[Decimal]       = mapped_column(Numeric(14, 3))
    unit_cost:    Mapped[Decimal]       = mapped_column(Numeric(14, 2))
    total_cost:   Mapped[Decimal]       = mapped_column(Numeric(14, 2))
    notes:        Mapped[Optional[str]] = mapped_column(String(300))
    # حقول أبعاد الألواح في BOM
    dim_length_cm:  Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    dim_width_cm:   Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    dim_count:      Mapped[Optional[int]]     = mapped_column(Integer, nullable=True)
    dim_area_m2:    Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    calc_weight_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 3), nullable=True)
    bar_length_cm:  Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    waste_g:        Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    # نوع البند للمعدات المترية: fixed (ثابت) أو variable (يتناسب مع طول المعدة L)
    line_type:      Mapped[str]            = mapped_column(String(10), default="fixed")
    # عند variable: هل dim_length_cm يمثّل معدّل (سم لكل متر من L) يتناسب مع L، أم بُعد ثابت
    scale_dim_length: Mapped[bool]         = mapped_column(Boolean, default=False)
    created_at:   Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    equipment:    Mapped["Equipment"]   = relationship(back_populates="bom_lines", foreign_keys=[equipment_id])
    material:     Mapped["Material"]    = relationship(back_populates="bom_lines")


class EquipmentDimension(Base):
    __tablename__ = "equipment_dimensions"
    id:           Mapped[int]  = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    equipment_id: Mapped[int]  = mapped_column(ForeignKey("equipment.id"))
    dim_key:      Mapped[str]  = mapped_column(String(80))
    dim_value:    Mapped[str]  = mapped_column(String(80))
    unit:         Mapped[str]  = mapped_column(String(20))
    equipment:    Mapped["Equipment"] = relationship(back_populates="dimensions")


class CADFile(Base):
    """ملفات التصميم المرتبطة بالمعدات"""
    __tablename__ = "cad_files"

    id:           Mapped[int]           = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    equipment_id: Mapped[int]           = mapped_column(ForeignKey("equipment.id"), index=True)
    filename:     Mapped[str]           = mapped_column(String(255))    # اسم الملف الأصلي
    stored_name:  Mapped[str]           = mapped_column(String(255))    # الاسم المخزّن على الـ disk
    file_size:    Mapped[Optional[int]] = mapped_column(Integer)
    mime_type:    Mapped[Optional[str]] = mapped_column(String(100))
    revision:     Mapped[Optional[str]] = mapped_column(String(20))
    notes:        Mapped[Optional[str]] = mapped_column(Text)
    uploaded_at:  Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())

    equipment: Mapped["Equipment"] = relationship("Equipment", back_populates="cad_files")


# ═══════════════════════════════════════════════════════════════
# WORKERS & PAYROLL — العاملون والرواتب
# ═══════════════════════════════════════════════════════════════

class Worker(Base):
    __tablename__ = "workers"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_worker_tenant_code"),)
    id:               Mapped[int]           = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:             Mapped[str]           = mapped_column(String(30))
    name:             Mapped[str]           = mapped_column(String(200))
    job_title:        Mapped[Optional[str]] = mapped_column(String(100))
    phone:            Mapped[Optional[str]] = mapped_column(String(30))
    national_id:      Mapped[Optional[str]] = mapped_column(String(30))
    hire_date:        Mapped[Optional[date]] = mapped_column(Date)
    base_weekly_wage: Mapped[Decimal]       = mapped_column(Numeric(12,2))
    daily_wage:        Mapped[Decimal]       = mapped_column(Numeric(12,2), default=0)
    is_active:        Mapped[bool]          = mapped_column(Boolean, default=True)
    notes:            Mapped[Optional[str]] = mapped_column(Text)
    contract_type:    Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    insurance_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    contract_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at:       Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    payroll_lines:    Mapped[List["PayrollLine"]] = relationship(back_populates="worker")


class PayrollRun(Base):
    __tablename__ = "payroll_runs"
    id:               Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    week_start:       Mapped[date]           = mapped_column(Date)
    week_end:         Mapped[date]           = mapped_column(Date)
    total_gross:      Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    total_deductions: Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    total_net:        Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    status:           Mapped[str]            = mapped_column(String(20), default="draft")
    paid_date:        Mapped[Optional[date]] = mapped_column(Date)
    notes:            Mapped[Optional[str]]  = mapped_column(Text)
    created_at:       Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    lines:            Mapped[List["PayrollLine"]] = relationship(back_populates="payroll_run")


class PayrollLine(Base):
    __tablename__ = "payroll_lines"
    id:             Mapped[int]           = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    payroll_run_id: Mapped[int]           = mapped_column(ForeignKey("payroll_runs.id"), index=True)
    worker_id:      Mapped[int]           = mapped_column(ForeignKey("workers.id"), index=True)
    days_worked:    Mapped[Decimal]       = mapped_column(Numeric(4,1), default=6)
    gross_amount:   Mapped[Decimal]       = mapped_column(Numeric(12,2))
    deductions:     Mapped[Decimal]       = mapped_column(Numeric(12,2), default=0)
    bonus:          Mapped[Decimal]       = mapped_column(Numeric(12,2), default=0)
    net_amount:     Mapped[Decimal]       = mapped_column(Numeric(12,2))
    notes:          Mapped[Optional[str]] = mapped_column(Text)
    payroll_run:    Mapped["PayrollRun"]  = relationship(back_populates="lines")
    worker:         Mapped["Worker"]      = relationship(back_populates="payroll_lines")


# ═══════════════════════════════════════════════════════════════
# CUSTOMERS — العملاء
# ═══════════════════════════════════════════════════════════════

class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_customer_tenant_code"),)
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:         Mapped[str]            = mapped_column(String(30))
    name:         Mapped[str]            = mapped_column(String(200))
    phone:        Mapped[Optional[str]]  = mapped_column(String(30))
    address:      Mapped[Optional[str]]  = mapped_column(Text)
    tax_id:       Mapped[Optional[str]]  = mapped_column(String(50))
    credit_limit: Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    balance:      Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    notes:        Mapped[Optional[str]]  = mapped_column(Text)
    payment_terms: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    orders:       Mapped[List["CustomerOrder"]]   = relationship(back_populates="customer")
    payments:     Mapped[List["CustomerPayment"]] = relationship(back_populates="customer")


class CustomerLocation(Base):
    """موقع/فرع تابع لعميل — لتصنيف الفواتير والمدفوعات والكشوف"""
    __tablename__ = "customer_locations"
    id:          Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:   Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    customer_id: Mapped[int]            = mapped_column(ForeignKey("customers.id"), index=True)
    name:        Mapped[str]            = mapped_column(String(200))   # اسم الموقع (فرع المعادي، المصنع...)
    address:     Mapped[Optional[str]]  = mapped_column(Text)
    phone:       Mapped[Optional[str]]  = mapped_column(String(30))
    contact_person: Mapped[Optional[str]] = mapped_column(String(120))  # مسؤول الموقع
    notes:       Mapped[Optional[str]]  = mapped_column(Text)
    is_active:   Mapped[bool]           = mapped_column(Boolean, default=True)
    created_at:  Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())


class CustomerOrder(Base):
    __tablename__ = "customer_orders"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_customerorder_tenant_code"),)
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:         Mapped[str]            = mapped_column(String(50))
    quotation_id: Mapped[Optional[int]]  = mapped_column(ForeignKey("quotations.id"), nullable=True)   # تحويل من عرض سعر
    customer_id:  Mapped[int]            = mapped_column(ForeignKey("customers.id"), index=True)
    location_id:  Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_locations.id"), nullable=True, index=True)
    equipment_id: Mapped[Optional[int]]  = mapped_column(ForeignKey("equipment.id"), nullable=True)
    description:  Mapped[Optional[str]]  = mapped_column(Text)
    quantity:     Mapped[int]            = mapped_column(Integer, default=1)
    unit_price:   Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    total_price:  Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    status:       Mapped[str]            = mapped_column(String(20), default="pending")
    order_date:   Mapped[Optional[date]] = mapped_column(Date)
    delivery_date: Mapped[Optional[date]] = mapped_column(Date)
    notes:        Mapped[Optional[str]]  = mapped_column(Text)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    customer:     Mapped["Customer"]     = relationship(back_populates="orders")
    location:     Mapped[Optional["CustomerLocation"]] = relationship("CustomerLocation")
    work_orders:  Mapped[List["WorkOrder"]] = relationship(back_populates="customer_order")
    lines:        Mapped[List["CustomerOrderLine"]] = relationship(
        "CustomerOrderLine", back_populates="order",
        cascade="all, delete-orphan", order_by="CustomerOrderLine.sort_order"
    )


class CustomerOrderLine(Base):
    """بند في طلب العميل (متعدد الأصناف)"""
    __tablename__ = "customer_order_lines"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:    Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    order_id:     Mapped[int]            = mapped_column(ForeignKey("customer_orders.id"), index=True)
    equipment_id: Mapped[Optional[int]]  = mapped_column(ForeignKey("equipment.id"), nullable=True)
    description:  Mapped[str]            = mapped_column(String(500))
    qty:          Mapped[Decimal]        = mapped_column(Numeric(14, 3), default=1)
    unit:         Mapped[str]            = mapped_column(String(20), default="قطعة")
    unit_price:   Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    total_price:  Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    notes:        Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    sort_order:   Mapped[int]            = mapped_column(Integer, default=0)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    order:        Mapped["CustomerOrder"] = relationship("CustomerOrder", back_populates="lines")


class CustomerPayment(Base):
    __tablename__ = "customer_payments"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    customer_id:  Mapped[int]            = mapped_column(ForeignKey("customers.id"), index=True)
    location_id:  Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_locations.id"), nullable=True, index=True)
    order_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_orders.id"), nullable=True)
    amount:       Mapped[Decimal]        = mapped_column(Numeric(14,2))
    payment_type: Mapped[str]            = mapped_column(String(20), default="cash")  # cash|transfer|cheque|bill
    payment_date: Mapped[date]           = mapped_column(Date)
    reference:    Mapped[Optional[str]]  = mapped_column(String(100))  # رقم الشيك / رقم التحويل
    notes:        Mapped[Optional[str]]  = mapped_column(Text)
    created_by:   Mapped[Optional[int]]  = mapped_column(ForeignKey("users.id"), nullable=True)  # من سجّل الدفعة
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    customer:     Mapped["Customer"]     = relationship(back_populates="payments")


class Attendance(Base):
    """سجل الحضور اليومي"""
    __tablename__ = "attendance"
    id:             Mapped[int]           = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    worker_id:      Mapped[int]           = mapped_column(ForeignKey("workers.id"), index=True)
    date:           Mapped[date]          = mapped_column(Date, name="work_date")   # DB column is work_date
    # status: present|absent|half|leave|on_project
    status:         Mapped[str]           = mapped_column(String(20), default="present")
    overtime_hours: Mapped[Decimal]       = mapped_column(Numeric(4,1), default=0)
    # لما الحالة on_project — ربط بالمشروع الخارجي
    ext_project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ext_projects.id", use_alter=True, name="fk_att_ext_project"), nullable=True)
    notes:          Mapped[Optional[str]] = mapped_column(Text)
    created_at:     Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    worker:         Mapped["Worker"]      = relationship("Worker")




class WorkerAdvance(Base):
    __tablename__ = "worker_advances"
    id:             Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    worker_id:      Mapped[int]            = mapped_column(ForeignKey("workers.id"))
    amount:         Mapped[Decimal]        = mapped_column(Numeric(12, 2))
    advance_type:   Mapped[str]            = mapped_column(String(20), default="advance")  # advance|deduction|bonus
    date:           Mapped[date]           = mapped_column(Date)
    payroll_run_id: Mapped[Optional[int]]  = mapped_column(ForeignKey("payroll_runs.id"), nullable=True)
    is_settled:     Mapped[bool]           = mapped_column(Boolean, default=False)
    notes:          Mapped[Optional[str]]  = mapped_column(Text)
    created_at:     Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    worker:         Mapped["Worker"]       = relationship("Worker")


# ═══════════════════════════════════════════════════════════════
# USERS — المستخدمون
# ═══════════════════════════════════════════════════════════════
class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "username", name="uq_user_tenant_username"),)
    id:            Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    username:      Mapped[str]            = mapped_column(String(50))
    password_hash: Mapped[str]            = mapped_column(String(255))
    full_name:     Mapped[Optional[str]]  = mapped_column(String(200))
    role:          Mapped[str]            = mapped_column(String(20), default="admin")
    is_active:     Mapped[bool]           = mapped_column(Boolean, default=True)
    last_login:    Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at:    Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())


# ═══════════════════════════════════════════════════════════════
# SALES — المبيعات
# ═══════════════════════════════════════════════════════════════
# PRICE CATALOG
# ═══════════════════════════════════════════════════════════════

class PriceCatalogItem(Base):
    """كتالوج الأسعار — منتجات وخدمات بسعر التكلفة + هامش الربح"""
    __tablename__ = "price_catalog_items"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:    Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:         Mapped[Optional[str]]  = mapped_column(String(50))
    name:         Mapped[str]            = mapped_column(String(300))
    category:     Mapped[Optional[str]]  = mapped_column(String(100))   # مثلاً: معدات، خدمات، مواد
    description:  Mapped[Optional[str]]  = mapped_column(Text)
    unit:         Mapped[str]            = mapped_column(String(20), default="قطعة")
    cost_price:   Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)   # سعر التكلفة
    margin_pct:   Mapped[Decimal]        = mapped_column(Numeric(5,2),  default=25)  # نسبة الربح %
    selling_price: Mapped[Decimal]       = mapped_column(Numeric(14,2), default=0)   # سعر البيع (محسوب تلقائي)
    # نمط التسعير: auto = يُحسب من التكلفة×الهامش | manual = سعر بيع يدوي
    price_mode:   Mapped[str]            = mapped_column(String(10), default="auto")
    # ربط اختياري بمصدر التكلفة: material | equipment | None
    source_type:  Mapped[Optional[str]]  = mapped_column(String(20), nullable=True)
    source_id:    Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    is_active:    Mapped[bool]           = mapped_column(Boolean, default=True)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    updated_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ═══════════════════════════════════════════════════════════════

class Quotation(Base):
    __tablename__ = "quotations"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_quotation_tenant_code"),)
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:         Mapped[str]            = mapped_column(String(50))
    customer_id:  Mapped[int]            = mapped_column(ForeignKey("customers.id"))
    location_id:  Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_locations.id"), nullable=True, index=True)
    date:         Mapped[date]           = mapped_column(Date)
    valid_until:  Mapped[Optional[date]] = mapped_column(Date)
    status:       Mapped[str]            = mapped_column(String(20), default="draft")
    subtotal:     Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    tax_pct:      Mapped[Decimal]        = mapped_column(Numeric(5,2),  default=0)
    tax_amount:   Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    discount_pct: Mapped[Decimal]        = mapped_column(Numeric(5,2),  default=0)
    discount_amt: Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    total:        Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    notes:        Mapped[Optional[str]]  = mapped_column(Text)
    terms:        Mapped[Optional[str]]  = mapped_column(Text)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    customer:     Mapped["Customer"]     = relationship("Customer")
    location:     Mapped[Optional["CustomerLocation"]] = relationship("CustomerLocation")
    lines:        Mapped[List["QuotationLine"]] = relationship("QuotationLine", back_populates="quotation", order_by="QuotationLine.sort_order")


class QuotationLine(Base):
    __tablename__ = "quotation_lines"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    quotation_id: Mapped[int]            = mapped_column(ForeignKey("quotations.id"), index=True)
    catalog_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("price_catalog_items.id"), nullable=True)
    item_type:    Mapped[Optional[str]]  = mapped_column(String(100))   # النوع (مجرشة، خلاط...)
    model_no:     Mapped[Optional[str]]  = mapped_column(String(100))   # الموديل (SFSP66X60...)
    description:  Mapped[str]            = mapped_column(String(500))
    qty:          Mapped[Decimal]        = mapped_column(Numeric(14,3), default=1)
    unit:         Mapped[str]            = mapped_column(String(20), default="قطعة")
    unit_price:   Mapped[Decimal]        = mapped_column(Numeric(14,2))
    total_price:  Mapped[Decimal]        = mapped_column(Numeric(14,2))
    notes:        Mapped[Optional[str]]  = mapped_column(Text)
    sort_order:   Mapped[int]            = mapped_column(Integer, default=0)
    image_data:   Mapped[Optional[str]]  = mapped_column(Text, nullable=True)  # صورة البند (base64 data:URI)
    quotation:    Mapped["Quotation"]    = relationship("Quotation", back_populates="lines")
    catalog_item: Mapped[Optional["PriceCatalogItem"]] = relationship("PriceCatalogItem")


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_invoice_tenant_code"),)
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:              Mapped[str]            = mapped_column(String(50))
    quotation_id:      Mapped[Optional[int]]  = mapped_column(ForeignKey("quotations.id"), nullable=True)
    customer_order_id: Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_orders.id"), nullable=True)  # تحويل من طلب
    work_order_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("work_orders.id"), nullable=True)       # تحويل من أمر إنتاج
    customer_id:       Mapped[int]            = mapped_column(ForeignKey("customers.id"))
    location_id:       Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_locations.id"), nullable=True, index=True)
    date:              Mapped[date]           = mapped_column(Date)
    due_date:          Mapped[Optional[date]] = mapped_column(Date)
    status:            Mapped[str]            = mapped_column(String(20), default="unpaid")
    subtotal:     Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    tax_pct:      Mapped[Decimal]        = mapped_column(Numeric(5,2),  default=0)
    tax_amount:   Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    discount_pct: Mapped[Decimal]        = mapped_column(Numeric(5,2),  default=0)
    discount_amt: Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    total:        Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    paid_amount:  Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    notes:        Mapped[Optional[str]]  = mapped_column(Text)
    terms:        Mapped[Optional[str]]  = mapped_column(Text)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    customer:     Mapped["Customer"]     = relationship("Customer")
    lines:        Mapped[List["InvoiceLine"]] = relationship("InvoiceLine", back_populates="invoice", order_by="InvoiceLine.sort_order")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id:          Mapped[int]           = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    invoice_id:  Mapped[int]           = mapped_column(ForeignKey("invoices.id"), index=True)
    item_type:   Mapped[Optional[str]] = mapped_column(String(100))
    model_no:    Mapped[Optional[str]] = mapped_column(String(100))
    description: Mapped[str]           = mapped_column(String(500))
    qty:         Mapped[Decimal]       = mapped_column(Numeric(14,3), default=1)
    unit:        Mapped[str]           = mapped_column(String(20), default="قطعة")
    unit_price:  Mapped[Decimal]       = mapped_column(Numeric(14,2))
    total_price: Mapped[Decimal]       = mapped_column(Numeric(14,2))
    notes:       Mapped[Optional[str]] = mapped_column(Text)
    sort_order:  Mapped[int]           = mapped_column(Integer, default=0)
    invoice:     Mapped["Invoice"]     = relationship("Invoice", back_populates="lines")


# محطة 4: جدولة أقساط الفاتورة (InvoicePaymentSchedule) — راجع invoice.txt
# فاتورة ممكن تتقسّم لعدة أقساط بتواريخ استحقاق مختلفة، بدل تاريخ استحقاق واحد بس.
class InvoicePaymentSchedule(Base):
    __tablename__ = "invoice_payment_schedules"
    __table_args__ = (UniqueConstraint("invoice_id", "installment_no", name="uq_invoice_installment_no"),)
    id:             Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:      Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    invoice_id:     Mapped[int]            = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    installment_no: Mapped[int]            = mapped_column(Integer)          # ترتيب القسط: 1، 2، 3...
    label:          Mapped[Optional[str]]  = mapped_column(String(200))      # "دفعة مقدمة" / "دفعة تصنيع"...
    amount_due:     Mapped[Decimal]        = mapped_column(Numeric(14, 2))
    due_date:       Mapped[date]           = mapped_column(Date)
    paid_amount:    Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    # status: pending (لم يُستحق بعد) | partial | paid | overdue (فات الاستحقاق ولسه مش متسدد بالكامل)
    status:         Mapped[str]            = mapped_column(String(20), default="pending")
    created_at:     Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    invoice:        Mapped["Invoice"]      = relationship("Invoice")


# محطة 7: قالب خطة دفع على عرض السعر أو أمر البيع — قبل ما توجد فاتورة أصلاً.
# القالب بيتخزّن كنسب مئوية + إزاحة أيام من تاريخ الفاتورة (مش تواريخ مطلقة ولا
# مبالغ مطلقة)، عشان يتقدر يتطبّق على أي فاتورة تتولد لاحقًا من المصدر ده مهما
# كان تاريخها أو إجماليها النهائي.
class PaymentScheduleTemplate(Base):
    __tablename__ = "payment_schedule_templates"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "installment_no", name="uq_pst_source_installment"),
    )
    id:             Mapped[int]           = mapped_column(primary_key=True)
    tenant_id:      Mapped[int]           = mapped_column(ForeignKey("tenants.id"), index=True)
    source_type:    Mapped[str]           = mapped_column(String(20), index=True)   # 'quotation' | 'customer_order'
    source_id:      Mapped[int]           = mapped_column(Integer, index=True)
    installment_no: Mapped[int]           = mapped_column(Integer)
    label:          Mapped[Optional[str]] = mapped_column(String(200))
    pct:            Mapped[Decimal]       = mapped_column(Numeric(5, 2))            # نسبة من إجمالي الفاتورة
    days_offset:    Mapped[int]           = mapped_column(Integer, default=0)       # أيام من تاريخ الفاتورة



# ═══════════════════════════════════════════════════════════════
# INVENTORY ENHANCED MODELS — Phase 1
# ═══════════════════════════════════════════════════════════════

class BinLocation(Base):
    __tablename__ = "bin_locations"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_binlocation_tenant_code"),)
    id:          Mapped[int]               = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:        Mapped[str]               = mapped_column(String(30))
    name_ar:     Mapped[Optional[str]]     = mapped_column(String(100))
    zone:        Mapped[Optional[str]]     = mapped_column(String(20))
    shelf:       Mapped[Optional[str]]     = mapped_column(String(10))
    bin:         Mapped[Optional[str]]     = mapped_column(String(10))
    capacity_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(10,2))
    is_active:   Mapped[bool]              = mapped_column(Boolean, default=True)
    notes:       Mapped[Optional[str]]     = mapped_column(Text)
    # محطة 11: ترقية BinLocation نحو StockLocation (توسعة، مش استبدال) —
    # كل الحقول دي اختيارية عشان أي بيانات قديمة تفضل شغالة زي ما هي.
    warehouse_id: Mapped[Optional[int]]    = mapped_column(ForeignKey("warehouses.id"), nullable=True, index=True)
    parent_id:    Mapped[Optional[int]]    = mapped_column(ForeignKey("bin_locations.id"), nullable=True)
    # location_type: physical (رف/مكان فعلي) | virtual (موقع منطقي زي "قيد النقل")
    location_type: Mapped[str]             = mapped_column(String(20), default="physical")


# محطة 11: StockQuant — الرصيد الفعلي لكل (مادة × موقع × دفعة) بدل ما
# Material.stock_qty يبقى مصدر التحديث المباشر. الآن الحركات (post_receipt/
# post_issue) بتحدّث المادة والـ Quant مع بعض في نفس الترانزاكشن، وSUM(qty)
# لأي مادة المفروض يساوي Material.stock_qty دايمًا (فحص محطة 13 هيتأكد من ده رسميًا).
class StockQuant(Base):
    __tablename__ = "stock_quants"
    __table_args__ = (UniqueConstraint("material_id", "location_id", "lot_id", name="uq_quant_material_location_lot"),)
    id:           Mapped[int]           = mapped_column(primary_key=True)
    tenant_id:    Mapped[int]           = mapped_column(ForeignKey("tenants.id"), index=True)
    material_id:  Mapped[int]           = mapped_column(ForeignKey("materials.id"), index=True)
    location_id:  Mapped[int]           = mapped_column(ForeignKey("bin_locations.id"), index=True)
    lot_id:       Mapped[Optional[int]] = mapped_column(ForeignKey("stock_batches.id"), nullable=True)
    qty:          Mapped[Decimal]       = mapped_column(Numeric(14, 3), default=0)
    reserved_qty: Mapped[Decimal]       = mapped_column(Numeric(14, 3), default=0)
    updated_at:   Mapped[datetime]      = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class MaterialBinStock(Base):
    __tablename__ = "material_bin_stock"
    id:          Mapped[int]     = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    material_id: Mapped[int]     = mapped_column(ForeignKey("materials.id"))
    bin_id:      Mapped[int]     = mapped_column(ForeignKey("bin_locations.id"))
    qty:         Mapped[Decimal] = mapped_column(Numeric(14,3), default=0)
    material:    Mapped["Material"]     = relationship("Material")
    bin:         Mapped["BinLocation"]  = relationship("BinLocation")


class MaterialReservation(Base):
    __tablename__ = "material_reservations"
    id:             Mapped[int]               = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    work_order_id:  Mapped[int]               = mapped_column(ForeignKey("work_orders.id"), index=True)
    material_id:    Mapped[int]               = mapped_column(ForeignKey("materials.id"), index=True)
    required_qty:   Mapped[Decimal]           = mapped_column(Numeric(14,3))
    reserved_qty:   Mapped[Decimal]           = mapped_column(Numeric(14,3), default=0)
    issued_qty:     Mapped[Decimal]           = mapped_column(Numeric(14,3), default=0)
    returned_qty:   Mapped[Decimal]           = mapped_column(Numeric(14,3), default=0)
    scrap_qty:      Mapped[Decimal]           = mapped_column(Numeric(14,3), default=0)
    status:         Mapped[str]               = mapped_column(String(20), default="pending")
    reserved_at:    Mapped[Optional[datetime]]= mapped_column(DateTime)
    notes:          Mapped[Optional[str]]     = mapped_column(Text)
    # محطة 16 (الروادماب الأصلي): موقع الحجز الفعلي (مبدئيًا مخزن RAW دايمًا).
    # ملاحظة نطاق مهمة: ده تحصين هيكلي/تتبعي بس — التحقق من "الكمية متاحة"
    # (get_available_qty وauto_reserve_materials) لسه بيحسب من Material.stock_qty
    # الإجمالي، مش من رصيد الموقع ده تحديدًا. فرض التحقق بالموقع فعليًا محتاج
    # الأول توحيد صرف الألواح/الأسياخ (بند 18) عشان يبقوا location-aware —
    # عمل التحقق الصارم دلوقتي كان هيكرر بالظبط نفس الغلطة اللي اتصلحت في
    # محطة 25 (فرض موقع واحد بينما الرصيد الفعلي متوزّع على أكتر من موقع).
    location_id:    Mapped[Optional[int]]     = mapped_column(ForeignKey("bin_locations.id"), nullable=True)
    material:       Mapped["Material"]        = relationship("Material")
    work_order:     Mapped["WorkOrder"]       = relationship("WorkOrder")


class ScrapRecord(Base):
    __tablename__ = "scrap_records"
    id:             Mapped[int]               = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    work_order_id:  Mapped[Optional[int]]     = mapped_column(ForeignKey("work_orders.id"))
    material_id:    Mapped[Optional[int]]     = mapped_column(ForeignKey("materials.id"))
    qty:            Mapped[Decimal]           = mapped_column(Numeric(14,3))
    unit_cost:      Mapped[Optional[Decimal]] = mapped_column(Numeric(14,2))
    total_cost:     Mapped[Optional[Decimal]] = mapped_column(Numeric(14,2))
    reason:         Mapped[Optional[str]]     = mapped_column(String(200))
    stage:          Mapped[Optional[str]]     = mapped_column(String(100))
    qty_sold:       Mapped[Decimal]           = mapped_column(Numeric(14,3), default=0)
    recorded_at:    Mapped[datetime]          = mapped_column(DateTime, server_default=func.now())
    material:       Mapped[Optional["Material"]]  = relationship("Material")
    work_order:     Mapped[Optional["WorkOrder"]] = relationship("WorkOrder")


class ScrapSale(Base):
    """عملية بيع هالك/خردة"""
    __tablename__ = "scrap_sales"
    id:             Mapped[int]               = mapped_column(primary_key=True)
    tenant_id:      Mapped[int]               = mapped_column(ForeignKey("tenants.id"), index=True)
    code:           Mapped[str]               = mapped_column(String(30))
    sale_date:      Mapped[date]              = mapped_column(Date)
    buyer_name:     Mapped[Optional[str]]     = mapped_column(String(200))
    treasury_id:    Mapped[int]               = mapped_column(ForeignKey("treasuries.id"))
    payment_method: Mapped[str]               = mapped_column(String(20), default="cash")
    total_amount:   Mapped[Decimal]           = mapped_column(Numeric(14,2), default=0)
    notes:          Mapped[Optional[str]]     = mapped_column(Text)
    created_at:     Mapped[datetime]          = mapped_column(DateTime, server_default=func.now())

    treasury: Mapped["Treasury"] = relationship("Treasury")
    lines: Mapped[List["ScrapSaleLine"]] = relationship("ScrapSaleLine", back_populates="sale", cascade="all, delete-orphan")


class ScrapSaleLine(Base):
    __tablename__ = "scrap_sale_lines"
    id:             Mapped[int]               = mapped_column(primary_key=True)
    tenant_id:      Mapped[int]               = mapped_column(ForeignKey("tenants.id"), index=True)
    scrap_sale_id:  Mapped[int]               = mapped_column(ForeignKey("scrap_sales.id"))
    material_id:    Mapped[int]               = mapped_column(ForeignKey("materials.id"))
    qty:            Mapped[Decimal]           = mapped_column(Numeric(14,3))
    unit_price:     Mapped[Decimal]           = mapped_column(Numeric(14,2))
    total_price:    Mapped[Decimal]           = mapped_column(Numeric(14,2))

    sale:     Mapped["ScrapSale"] = relationship("ScrapSale", back_populates="lines")
    material: Mapped["Material"]  = relationship("Material")


class MrpSuggestion(Base):
    __tablename__ = "mrp_suggestions"
    id:             Mapped[int]               = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    material_id:    Mapped[int]               = mapped_column(ForeignKey("materials.id"))
    current_stock:  Mapped[Optional[Decimal]] = mapped_column(Numeric(14,3))
    required_qty:   Mapped[Optional[Decimal]] = mapped_column(Numeric(14,3))
    suggested_qty:  Mapped[Optional[Decimal]] = mapped_column(Numeric(14,3))
    estimated_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(14,2))
    order_by_date:  Mapped[Optional[date]]    = mapped_column(Date)
    expected_date:  Mapped[Optional[date]]    = mapped_column(Date)
    status:         Mapped[str]               = mapped_column(String(20), default="pending")
    generated_at:   Mapped[datetime]          = mapped_column(DateTime, server_default=func.now())
    material:       Mapped["Material"]        = relationship("Material")


class StockCount(Base):
    __tablename__ = "stock_counts"
    id:         Mapped[int]           = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    count_date: Mapped[date]          = mapped_column(Date)
    status:     Mapped[str]           = mapped_column(String(20), default="in_progress")
    notes:      Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    lines:      Mapped[List["StockCountLine"]] = relationship("StockCountLine", back_populates="count")


class StockCountLine(Base):
    __tablename__ = "stock_count_lines"
    id:          Mapped[int]               = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    count_id:    Mapped[int]               = mapped_column(ForeignKey("stock_counts.id"))
    material_id: Mapped[int]               = mapped_column(ForeignKey("materials.id"))
    system_qty:  Mapped[Optional[Decimal]] = mapped_column(Numeric(14,3))
    actual_qty:  Mapped[Optional[Decimal]] = mapped_column(Numeric(14,3))
    difference:  Mapped[Optional[Decimal]] = mapped_column(Numeric(14,3))
    scanned_at:  Mapped[Optional[datetime]]= mapped_column(DateTime)
    count:       Mapped["StockCount"]      = relationship("StockCount", back_populates="lines")
    material:    Mapped["Material"]        = relationship("Material")


# ═══════════════════════════════════════════════════════════════
# SUPPLIERS — الموردون
# ═══════════════════════════════════════════════════════════════

class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_supplier_tenant_code"),)
    id:            Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:          Mapped[str]            = mapped_column(String(30), index=True)
    name:          Mapped[str]            = mapped_column(String(200))
    phone:          Mapped[Optional[str]]  = mapped_column(String(30))
    email:          Mapped[Optional[str]]  = mapped_column(String(200))
    contact_person: Mapped[Optional[str]]  = mapped_column(String(200))
    address:        Mapped[Optional[str]]  = mapped_column(Text)
    tax_id:         Mapped[Optional[str]]  = mapped_column(String(50))
    payment_terms:  Mapped[Optional[str]]  = mapped_column(String(100))
    bank_account:   Mapped[Optional[str]]  = mapped_column(String(200), nullable=True)
    iban:           Mapped[Optional[str]]  = mapped_column(String(50), nullable=True)
    supplier_type:  Mapped[Optional[str]]  = mapped_column(String(30), nullable=True)
    # محطة 21: تصنيف صريح محلي/مستورد — بدل الاعتماد فقط على heuristic كلمات
    # مفتاحية في supplier_type (DEC-03 في التقرير). None = لم يُحدَّد بعد،
    # يرجع للـ heuristic كـ fallback؛ True/False = تصنيف صريح له الأولوية.
    is_import:      Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    balance:        Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    notes:          Mapped[Optional[str]]  = mapped_column(Text)
    is_active:      Mapped[bool]           = mapped_column(Boolean, default=True)
    created_at:    Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    invoices:      Mapped[List["PurchaseInvoice"]] = relationship(back_populates="supplier")


# ═══════════════════════════════════════════════════════════════
# PURCHASE INVOICES — فواتير الشراء
# ═══════════════════════════════════════════════════════════════

class PurchaseInvoice(Base):
    __tablename__ = "purchase_invoices"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_purchaseinvoice_tenant_code"),)
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:          Mapped[str]            = mapped_column(String(50), index=True)
    internal_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    supplier_id:  Mapped[Optional[int]]  = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    supplier_name: Mapped[Optional[str]] = mapped_column(String(200))
    date:         Mapped[date]           = mapped_column(Date)
    due_date:     Mapped[Optional[date]] = mapped_column(Date)
    status:       Mapped[str]            = mapped_column(String(20), default="draft")
    subtotal:     Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    tax_pct:      Mapped[Decimal]        = mapped_column(Numeric(5, 2), default=0)
    tax_amount:   Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    discount_amt: Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    total:        Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    paid_amount:  Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    notes:        Mapped[Optional[str]]  = mapped_column(Text)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    supplier:     Mapped[Optional["Supplier"]]        = relationship(back_populates="invoices")
    lines:        Mapped[List["PurchaseInvoiceLine"]] = relationship(back_populates="invoice",
                                                                      cascade="all, delete-orphan")
    attachments:  Mapped[List["FileAttachment"]]      = relationship(
        primaryjoin="and_(FileAttachment.entity_type=='purchase_invoice', "
                    "foreign(FileAttachment.entity_id)==PurchaseInvoice.id)",
        viewonly=True,
    )


class PurchaseInvoiceLine(Base):
    __tablename__ = "purchase_invoice_lines"
    id:          Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    invoice_id:  Mapped[int]            = mapped_column(ForeignKey("purchase_invoices.id"), index=True)
    material_id: Mapped[Optional[int]]  = mapped_column(ForeignKey("materials.id"), nullable=True)
    description: Mapped[str]            = mapped_column(String(500))
    qty:         Mapped[Decimal]        = mapped_column(Numeric(14, 3), default=1)
    unit:        Mapped[str]            = mapped_column(String(30), default="قطعة")
    unit_cost:   Mapped[Decimal]        = mapped_column(Numeric(14, 2))
    total_cost:  Mapped[Decimal]        = mapped_column(Numeric(14, 2))
    update_stock: Mapped[bool]          = mapped_column(Boolean, default=True)
    invoice:     Mapped["PurchaseInvoice"] = relationship(back_populates="lines")
    material:    Mapped[Optional["Material"]] = relationship("Material")


class SupplierPayment(Base):
    """دفعة لمورد — تتسجّل عشان تظهر في كشف الحساب التفصيلي"""
    __tablename__ = "supplier_payments"
    id:             Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:      Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    supplier_id:    Mapped[Optional[int]]  = mapped_column(ForeignKey("suppliers.id"), nullable=True, index=True)
    invoice_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("purchase_invoices.id"), nullable=True, index=True)
    amount:         Mapped[Decimal]        = mapped_column(Numeric(14, 2))
    payment_date:   Mapped[date]           = mapped_column(Date)
    payment_method: Mapped[Optional[str]]  = mapped_column(String(30), nullable=True)   # cash|transfer|cheque|bill
    reference:      Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)  # رقم الشيك / رقم التحويل
    notes:          Mapped[Optional[str]]  = mapped_column(Text)
    created_by:     Mapped[Optional[int]]  = mapped_column(ForeignKey("users.id"), nullable=True)  # من سجّل الدفعة
    created_at:     Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())


# ═══════════════════════════════════════════════════════════════
# FILE ATTACHMENTS — المرفقات
# ═══════════════════════════════════════════════════════════════

class FileAttachment(Base):
    __tablename__ = "file_attachments"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    entity_type:  Mapped[str]            = mapped_column(String(50), index=True)
    entity_id:    Mapped[int]            = mapped_column(Integer, index=True)
    original_name: Mapped[str]           = mapped_column(String(300))
    stored_name:  Mapped[str]            = mapped_column(String(300))
    file_size:    Mapped[Optional[int]]  = mapped_column(Integer)
    mime_type:    Mapped[Optional[str]]  = mapped_column(String(100))
    notes:        Mapped[Optional[str]]  = mapped_column(Text)
    uploaded_by:  Mapped[Optional[str]]  = mapped_column(String(100))
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())


# ═══════════════════════════════════════════════════════════════
# CUSTOMER ASSETS — معدات العملاء
# ═══════════════════════════════════════════════════════════════

class CustomerAsset(Base):
    """معدة مُسلَّمة لعميل — تُستخدم في الخدمة الميدانية"""
    __tablename__ = "customer_assets"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    customer_id:     Mapped[int]            = mapped_column(ForeignKey("customers.id"), index=True)
    equipment_id:    Mapped[Optional[int]]  = mapped_column(ForeignKey("equipment.id"), nullable=True)
    serial_number:   Mapped[Optional[str]]  = mapped_column(String(100))
    name:            Mapped[str]            = mapped_column(String(200))
    model:           Mapped[Optional[str]]  = mapped_column(String(100))
    delivery_date:   Mapped[Optional[date]] = mapped_column(Date)
    warranty_end:    Mapped[Optional[date]] = mapped_column(Date)
    location:        Mapped[Optional[str]]  = mapped_column(String(300))
    status:          Mapped[str]            = mapped_column(String(20), default="active")
    notes:           Mapped[Optional[str]]  = mapped_column(Text)
    # أمر الإنتاج المصدر — يربط الأصل بأمر الإنتاج الذي صنعه (Traceability)
    source_work_order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("work_orders.id"), nullable=True)
    # الموقع الحالي البنيوي للمعدة (بدل/بجانب حقل location النصي)
    current_location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customer_locations.id"), nullable=True, index=True)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    customer:        Mapped["Customer"]     = relationship("Customer")
    equipment:       Mapped[Optional["Equipment"]] = relationship("Equipment")
    source_work_order: Mapped[Optional["WorkOrder"]] = relationship("WorkOrder")
    current_location: Mapped[Optional["CustomerLocation"]] = relationship("CustomerLocation")
    service_orders:  Mapped[List["FieldServiceOrder"]] = relationship(back_populates="asset")
    movements:       Mapped[List["AssetMovement"]] = relationship(back_populates="asset", cascade="all, delete-orphan")


class AssetMovement(Base):
    """سجل حركة/تاريخ معدة العميل — تصنيع، تسليم، تركيب، نقل، صيانة، إرجاع"""
    __tablename__ = "asset_movements"
    id:                Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:         Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    asset_id:          Mapped[int]            = mapped_column(ForeignKey("customer_assets.id"), index=True)
    # manufactured | delivered | installed | relocated | maintained | returned
    event_type:        Mapped[str]            = mapped_column(String(30))
    # work_order | ext_project | field_service_order
    ref_type:          Mapped[Optional[str]]  = mapped_column(String(30), nullable=True)
    ref_id:            Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    from_location_id:  Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_locations.id"), nullable=True)
    to_location_id:    Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_locations.id"), nullable=True)
    event_date:        Mapped[date]           = mapped_column(Date, server_default=func.current_date())
    notes:             Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    created_by:        Mapped[Optional[int]]  = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at:        Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    asset:             Mapped["CustomerAsset"] = relationship("CustomerAsset", back_populates="movements")
    from_location:     Mapped[Optional["CustomerLocation"]] = relationship("CustomerLocation", foreign_keys=[from_location_id])
    to_location:       Mapped[Optional["CustomerLocation"]] = relationship("CustomerLocation", foreign_keys=[to_location_id])


# ═══════════════════════════════════════════════════════════════
# FIELD SERVICE — الخدمة الميدانية
# ═══════════════════════════════════════════════════════════════

class FieldServiceOrder(Base):
    __tablename__ = "field_service_orders"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_fieldserviceorder_tenant_code"),)
    id:             Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:           Mapped[str]            = mapped_column(String(50), index=True)
    customer_id:    Mapped[int]            = mapped_column(ForeignKey("customers.id"), index=True)
    location_id:    Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_locations.id"), nullable=True, index=True)
    asset_id:       Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_assets.id"), nullable=True)
    order_type:     Mapped[str]            = mapped_column(String(30), default="maintenance")
    title:          Mapped[str]            = mapped_column(String(300))
    description:    Mapped[Optional[str]]  = mapped_column(Text)
    priority:       Mapped[str]            = mapped_column(String(20), default="normal")
    status:         Mapped[str]            = mapped_column(String(20), default="pending")
    planned_date:   Mapped[Optional[date]] = mapped_column(Date)
    completion_date: Mapped[Optional[date]] = mapped_column(Date)
    estimated_hours: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    actual_hours:   Mapped[Optional[Decimal]]  = mapped_column(Numeric(6, 2))
    labor_cost:     Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    materials_cost: Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    total_cost:     Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    invoice_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("invoices.id"), nullable=True)
    notes:          Mapped[Optional[str]]  = mapped_column(Text)
    created_at:     Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    customer:       Mapped["Customer"]     = relationship("Customer")
    location:       Mapped[Optional["CustomerLocation"]] = relationship("CustomerLocation")
    asset:          Mapped[Optional["CustomerAsset"]] = relationship(back_populates="service_orders")
    assignments:    Mapped[List["ServiceAssignment"]] = relationship(back_populates="service_order",
                                                                      cascade="all, delete-orphan")
    visits:         Mapped[List["ServiceVisit"]]      = relationship(back_populates="service_order",
                                                                      cascade="all, delete-orphan")


class ServiceAssignment(Base):
    __tablename__ = "service_assignments"
    id:               Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    service_order_id: Mapped[int]            = mapped_column(ForeignKey("field_service_orders.id"), index=True)
    worker_id:        Mapped[int]            = mapped_column(ForeignKey("workers.id"))
    role:             Mapped[str]            = mapped_column(String(100), default="technician")
    assigned_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    notes:            Mapped[Optional[str]]  = mapped_column(Text)
    service_order:    Mapped["FieldServiceOrder"] = relationship(back_populates="assignments")
    worker:           Mapped["Worker"]           = relationship("Worker")


class ServiceVisit(Base):
    __tablename__ = "service_visits"
    id:               Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    service_order_id: Mapped[int]            = mapped_column(ForeignKey("field_service_orders.id"), index=True)
    visit_date:       Mapped[date]           = mapped_column(Date)
    arrival_time:     Mapped[Optional[str]]  = mapped_column(String(10))
    departure_time:   Mapped[Optional[str]]  = mapped_column(String(10))
    labor_hours:      Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    work_done:        Mapped[Optional[str]]  = mapped_column(Text)
    materials_used:   Mapped[Optional[str]]  = mapped_column(Text)
    next_action:      Mapped[Optional[str]]  = mapped_column(Text)
    status:           Mapped[str]            = mapped_column(String(20), default="completed")
    created_at:       Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    service_order:    Mapped["FieldServiceOrder"] = relationship(back_populates="visits")


# ══════════════════════════════════════════════════════════════
# PROJECT LABOR COSTS — تكاليف العمالة الميدانية
# ══════════════════════════════════════════════════════════════

class ProjectLaborEntry(Base):
    __tablename__ = "project_labor_entries"
    id:               Mapped[int]             = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    service_order_id: Mapped[int]             = mapped_column(ForeignKey("field_service_orders.id"), index=True)
    worker_id:        Mapped[int]             = mapped_column(ForeignKey("workers.id"))
    entry_date:       Mapped[date]            = mapped_column(Date)
    days:             Mapped[Decimal]         = mapped_column(Numeric(6, 2), default=1)
    daily_wage:       Mapped[Decimal]         = mapped_column(Numeric(10, 2), default=0)
    allowance:        Mapped[Decimal]         = mapped_column(Numeric(10, 2), default=0)   # بدل معيشة / سفر
    advance:          Mapped[Decimal]         = mapped_column(Numeric(10, 2), default=0)   # سلفة
    total_pay:        Mapped[Decimal]         = mapped_column(Numeric(10, 2), default=0)   # (days*daily_wage)+allowance
    notes:            Mapped[Optional[str]]   = mapped_column(Text)
    created_at:       Mapped[datetime]        = mapped_column(DateTime, server_default=func.now())
    service_order:    Mapped["FieldServiceOrder"] = relationship("FieldServiceOrder")
    worker:           Mapped["Worker"]            = relationship("Worker")


# ══════════════════════════════════════════════════════════════
# PROJECT TOOL WITHDRAWALS — عهدة العدة والأدوات
# ══════════════════════════════════════════════════════════════

class ProjectToolWithdrawal(Base):
    __tablename__ = "project_tool_withdrawals"
    id:               Mapped[int]             = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    service_order_id: Mapped[int]             = mapped_column(ForeignKey("field_service_orders.id"), index=True)
    material_id:      Mapped[int]             = mapped_column(ForeignKey("materials.id"))
    worker_id:        Mapped[Optional[int]]   = mapped_column(ForeignKey("workers.id"), nullable=True)   # المستلم
    qty:              Mapped[Decimal]         = mapped_column(Numeric(10, 3), default=1)
    withdrawn_date:   Mapped[date]            = mapped_column(Date)
    returned_date:    Mapped[Optional[date]]  = mapped_column(Date, nullable=True)
    returned_qty:     Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 3), nullable=True)
    damaged_qty:      Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 3), nullable=True, default=0)
    status:           Mapped[str]             = mapped_column(String(20), default="withdrawn")  # withdrawn / returned / damaged
    notes:            Mapped[Optional[str]]   = mapped_column(Text)
    created_at:       Mapped[datetime]        = mapped_column(DateTime, server_default=func.now())
    service_order:    Mapped["FieldServiceOrder"] = relationship("FieldServiceOrder")
    material:         Mapped["Material"]          = relationship("Material")
    worker:           Mapped[Optional["Worker"]]  = relationship("Worker")


# ══════════════════════════════════════════════════════════════
# WORKSHOP PROFILE — بيانات الورشة (مصدر وحيد للحقيقة للطباعة والتقارير)
# ══════════════════════════════════════════════════════════════

class WorkshopProfile(Base):
    __tablename__ = "workshop_profile"
    id:             Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    name:           Mapped[str]            = mapped_column(String(200), default="ورشة معدات الأعلاف")
    description:    Mapped[Optional[str]]  = mapped_column(String(300))
    phone:          Mapped[Optional[str]]  = mapped_column(String(50))
    mobile:         Mapped[Optional[str]]  = mapped_column(String(50))
    email:          Mapped[Optional[str]]  = mapped_column(String(120))
    tax_id:         Mapped[Optional[str]]  = mapped_column(String(50))
    commercial_reg: Mapped[Optional[str]]  = mapped_column(String(50))
    address:        Mapped[Optional[str]]  = mapped_column(Text)
    report_footer:  Mapped[Optional[str]]  = mapped_column(Text)
    # اللوجو يُخزَّن كملف على القرص؛ هنا اسم الملف المخزَّن فقط
    logo_stored_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # اللوجو كـ base64 data:URI — يُستخدم في الطباعة بدون مصادقة
    logo_data:        Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    currency:       Mapped[str]            = mapped_column(String(10), default="ج.م")
    updated_at:     Mapped[datetime]       = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


# ══════════════════════════════════════════════════════════════════════════════
# DELIVERY ORDERS — أوامر التوريد
# ══════════════════════════════════════════════════════════════════════════════

class DeliveryOrder(Base):
    __tablename__ = "delivery_orders"
    id:               Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:        Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:             Mapped[str]            = mapped_column(String(50))
    status:           Mapped[str]            = mapped_column(String(20), default="draft")  # draft/confirmed/delivered/cancelled
    order_date:       Mapped[date]           = mapped_column(Date)
    customer_id:      Mapped[int]            = mapped_column(ForeignKey("customers.id"))
    reference:        Mapped[Optional[str]]  = mapped_column(String(200))
    notes:            Mapped[Optional[str]]  = mapped_column(Text)
    delivery_date:    Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    delivery_address: Mapped[Optional[str]]  = mapped_column(Text)
    received_by:      Mapped[Optional[str]]  = mapped_column(String(200))
    receiver_title:   Mapped[Optional[str]]  = mapped_column(String(100))
    receiver_phone:   Mapped[Optional[str]]  = mapped_column(String(50))
    delivery_notes:   Mapped[Optional[str]]  = mapped_column(Text)
    vehicle_plate:    Mapped[Optional[str]]  = mapped_column(String(50))
    vehicle_type:     Mapped[Optional[str]]  = mapped_column(String(100))
    driver_name:      Mapped[Optional[str]]  = mapped_column(String(200))
    driver_license:   Mapped[Optional[str]]  = mapped_column(String(100))
    driver_phone:     Mapped[Optional[str]]  = mapped_column(String(50))
    created_at:       Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    updated_at:       Mapped[datetime]       = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    customer: Mapped["Customer"]               = relationship("Customer")
    lines:    Mapped[List["DeliveryOrderLine"]] = relationship("DeliveryOrderLine", cascade="all, delete-orphan", order_by="DeliveryOrderLine.sort_order")


class DeliveryOrderLine(Base):
    __tablename__ = "delivery_order_lines"
    id:          Mapped[int]           = mapped_column(primary_key=True)
    order_id:    Mapped[int]           = mapped_column(ForeignKey("delivery_orders.id"))
    description: Mapped[str]           = mapped_column(Text)
    model_no:    Mapped[Optional[str]] = mapped_column(String(100))
    qty:         Mapped[str]           = mapped_column(String(50), default="1")
    unit:        Mapped[str]           = mapped_column(String(50), default="قطعة")
    notes:       Mapped[Optional[str]] = mapped_column(Text)
    sort_order:  Mapped[int]           = mapped_column(Integer, default=0)
    # محطة 16: ربط بند التسليم بمعدة/أمر إنتاج فعلي — عشان التسليم يقدر
    # يخصم من رصيد FG الحقيقي بدل ما يفضل مجرد وصف نصي حر بلا أي أثر مخزني.
    equipment_id:  Mapped[Optional[int]] = mapped_column(ForeignKey("equipment.id"), nullable=True)
    work_order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("work_orders.id"), nullable=True)


# ══════════════════════════════════════════════════════════════════════════════
# EXTERNAL PROJECTS — المشاريع الخارجية (تركيب / صيانة / تشغيل خارج الورشة)
# ══════════════════════════════════════════════════════════════════════════════

class ExtProject(Base):
    """المشروع الخارجي الرئيسي"""
    __tablename__ = "ext_projects"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_extproject_tenant_code"),)
    id:               Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:        Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:             Mapped[str]            = mapped_column(String(30))
    title:            Mapped[str]            = mapped_column(String(300))
    # نوع المشروع: installation | maintenance | repair | commissioning | expansion | warranty
    project_type:     Mapped[str]            = mapped_column(String(30), default="installation")
    # الحالة: draft|approved|mobilizing|in_progress|testing|handover|warranty|closed|cancelled
    status:           Mapped[str]            = mapped_column(String(20), default="draft")
    client_id:        Mapped[int]            = mapped_column(ForeignKey("customers.id"), index=True)
    location_id:      Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_locations.id"), nullable=True, index=True)
    # مدير الموقع من جدول العمال
    manager_id:       Mapped[Optional[int]]  = mapped_column(ForeignKey("workers.id"), nullable=True)
    # موقع المشروع
    governorate:      Mapped[Optional[str]]  = mapped_column(String(100))
    city:             Mapped[Optional[str]]  = mapped_column(String(100))
    address:          Mapped[Optional[str]]  = mapped_column(Text)
    distance_km:      Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 1), nullable=True)
    # المعدات موضوع التركيب/الصيانة
    equipment_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("equipment.id"), nullable=True)
    equipment_desc:   Mapped[Optional[str]]  = mapped_column(String(300))   # وصف حر لو مش في الكتالوج
    # الوحدة الفعلية (CustomerAsset) موضوع هذا المشروع — بجانب equipment_id (الكتالوج)
    asset_id:         Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_assets.id"), nullable=True)
    # العقد
    contract_value:   Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    contract_ref:     Mapped[Optional[str]]  = mapped_column(String(100))
    # المواعيد
    planned_start:    Mapped[Optional[date]] = mapped_column(Date)
    planned_end:      Mapped[Optional[date]] = mapped_column(Date)
    actual_start:     Mapped[Optional[date]] = mapped_column(Date)
    actual_end:       Mapped[Optional[date]] = mapped_column(Date)
    # الضمان
    warranty_months:  Mapped[int]            = mapped_column(Integer, default=12)
    warranty_end:     Mapped[Optional[date]] = mapped_column(Date)
    description:      Mapped[Optional[str]]  = mapped_column(Text)
    notes:            Mapped[Optional[str]]  = mapped_column(Text)
    created_at:       Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    updated_at:       Mapped[datetime]       = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    # relationships
    client:           Mapped["Customer"]     = relationship("Customer")
    asset:            Mapped[Optional["CustomerAsset"]] = relationship("CustomerAsset")
    team_members:     Mapped[List["ExtProjectMember"]]   = relationship("ExtProjectMember", back_populates="project", cascade="all, delete-orphan")
    tools:            Mapped[List["ExtProjectTool"]]     = relationship("ExtProjectTool",   back_populates="project", cascade="all, delete-orphan")
    materials:        Mapped[List["ExtProjectMaterial"]] = relationship("ExtProjectMaterial", back_populates="project", cascade="all, delete-orphan")
    trips:            Mapped[List["ExtProjectTrip"]]     = relationship("ExtProjectTrip",   back_populates="project", cascade="all, delete-orphan")
    expenses:         Mapped[List["ExtProjectExpense"]]  = relationship("ExtProjectExpense", back_populates="project", cascade="all, delete-orphan")
    progress_reports: Mapped[List["ExtProjectProgress"]] = relationship("ExtProjectProgress", back_populates="project", cascade="all, delete-orphan")
    payments:         Mapped[List["ExtProjectPayment"]]  = relationship("ExtProjectPayment", back_populates="project", cascade="all, delete-orphan")


class ExtProjectMember(Base):
    """عضو في فريق المشروع — مرتبط بالعامل الأصلي + الرواتب"""
    __tablename__ = "ext_project_members"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:       Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id:      Mapped[int]            = mapped_column(ForeignKey("ext_projects.id"), index=True)
    worker_id:       Mapped[int]            = mapped_column(ForeignKey("workers.id"), index=True)
    # دور العامل في هذا المشروع
    role:            Mapped[str]            = mapped_column(String(100), default="فني")
    # الأجر اليومي الخاص بهذا المشروع (ممكن يختلف عن الأجر الأساسي بسبب بدل السفر)
    daily_rate:      Mapped[Decimal]        = mapped_column(Numeric(10, 2), default=0)
    # أيام العمل المخططة والفعلية
    planned_days:    Mapped[int]            = mapped_column(Integer, default=0)
    # إجمالي الأيام الفعلية (بيتحدّث تلقائياً من الحضور)
    actual_days:     Mapped[Decimal]        = mapped_column(Numeric(6, 1), default=0)
    # السلف اللي أخدها في الموقع (خارج كشف الرواتب الأساسي)
    site_advances:   Mapped[Decimal]        = mapped_column(Numeric(12, 2), default=0)
    joined_date:     Mapped[Optional[date]] = mapped_column(Date)
    left_date:       Mapped[Optional[date]] = mapped_column(Date)
    notes:           Mapped[Optional[str]]  = mapped_column(Text)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    project:         Mapped["ExtProject"]   = relationship("ExtProject", back_populates="team_members")
    worker:          Mapped["Worker"]       = relationship("Worker")


class ExtProjectTool(Base):
    """عهدة عدة وأدوات لمشروع خارجي"""
    __tablename__ = "ext_project_tools"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:       Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id:      Mapped[int]            = mapped_column(ForeignKey("ext_projects.id"), index=True)
    # اسم الأداة — حر أو مرتبط بمادة من المخزون
    tool_name:       Mapped[str]            = mapped_column(String(200))
    tool_type:       Mapped[str]            = mapped_column(String(30), default="hand_tool")
    # hand_tool | power_tool | welding | measuring | lifting | hydraulic | machine
    material_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("materials.id"), nullable=True)
    quantity:        Mapped[Decimal]        = mapped_column(Numeric(8, 2), default=1)
    unit:            Mapped[str]            = mapped_column(String(20), default="قطعة")
    condition_out:   Mapped[str]            = mapped_column(String(20), default="good")  # excellent|good|fair
    condition_in:    Mapped[Optional[str]]  = mapped_column(String(20), nullable=True)
    withdrawn_by:    Mapped[Optional[int]]  = mapped_column(ForeignKey("workers.id"), nullable=True)
    withdrawn_date:  Mapped[Optional[date]] = mapped_column(Date)
    returned_date:   Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    returned_qty:    Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2), nullable=True)
    # الحالة: withdrawn|returned|damaged|lost
    status:          Mapped[str]            = mapped_column(String(20), default="withdrawn")
    notes:           Mapped[Optional[str]]  = mapped_column(Text)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    project:         Mapped["ExtProject"]   = relationship("ExtProject", back_populates="tools")


class ExtProjectMaterial(Base):
    """مواد مستهلكة في مشروع خارجي — من مخزون أو شراء محلي"""
    __tablename__ = "ext_project_materials"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:       Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id:      Mapped[int]            = mapped_column(ForeignKey("ext_projects.id"), index=True)
    # المصدر: stock | local_purchase
    source:          Mapped[str]            = mapped_column(String(20), default="stock")
    material_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("materials.id"), nullable=True)
    description:     Mapped[str]            = mapped_column(String(300))
    quantity:        Mapped[Decimal]        = mapped_column(Numeric(10, 3))
    unit:            Mapped[str]            = mapped_column(String(30), default="قطعة")
    unit_cost:       Mapped[Decimal]        = mapped_column(Numeric(12, 2), default=0)
    total_cost:      Mapped[Decimal]        = mapped_column(Numeric(12, 2), default=0)
    usage_date:      Mapped[date]           = mapped_column(Date)
    used_by:         Mapped[Optional[int]]  = mapped_column(ForeignKey("workers.id"), nullable=True)
    receipt_ref:     Mapped[Optional[str]]  = mapped_column(String(100))
    notes:           Mapped[Optional[str]]  = mapped_column(Text)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    project:         Mapped["ExtProject"]   = relationship("ExtProject", back_populates="materials")


class ExtProjectTrip(Base):
    """رحلة مواصلات مرتبطة بمشروع خارجي"""
    __tablename__ = "ext_project_trips"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:       Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id:      Mapped[int]            = mapped_column(ForeignKey("ext_projects.id"), index=True)
    trip_date:       Mapped[date]           = mapped_column(Date)
    # النوع: team_go|team_return|equipment_go|equipment_return|materials|supervision|other
    trip_type:       Mapped[str]            = mapped_column(String(30), default="team_go")
    # وسيلة النقل: company_van|rented_car|truck|pickup|bus|public
    vehicle_type:    Mapped[str]            = mapped_column(String(30), default="company_van")
    vehicle_plate:   Mapped[Optional[str]]  = mapped_column(String(30))
    driver_name:     Mapped[Optional[str]]  = mapped_column(String(100))
    from_location:   Mapped[Optional[str]]  = mapped_column(String(200))
    to_location:     Mapped[Optional[str]]  = mapped_column(String(200))
    distance_km:     Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 1), nullable=True)
    passengers:      Mapped[int]            = mapped_column(Integer, default=1)
    fuel_cost:       Mapped[Decimal]        = mapped_column(Numeric(10, 2), default=0)
    toll_cost:       Mapped[Decimal]        = mapped_column(Numeric(10, 2), default=0)
    rental_cost:     Mapped[Decimal]        = mapped_column(Numeric(10, 2), default=0)
    other_cost:      Mapped[Decimal]        = mapped_column(Numeric(10, 2), default=0)
    total_cost:      Mapped[Decimal]        = mapped_column(Numeric(10, 2), default=0)
    notes:           Mapped[Optional[str]]  = mapped_column(Text)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    project:         Mapped["ExtProject"]   = relationship("ExtProject", back_populates="trips")


class ExtProjectExpense(Base):
    """مصاريف يومية لمشروع خارجي (أكل/مبيت/موصلات داخلية/...)"""
    __tablename__ = "ext_project_expenses"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:       Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id:      Mapped[int]            = mapped_column(ForeignKey("ext_projects.id"), index=True)
    expense_date:    Mapped[date]           = mapped_column(Date)
    # النوع: food|accommodation|transport_local|tools_small|communication|medical|electricity|protocol|other
    expense_type:    Mapped[str]            = mapped_column(String(30), default="food")
    description:     Mapped[str]            = mapped_column(String(300))
    amount:          Mapped[Decimal]        = mapped_column(Numeric(10, 2))
    persons_count:   Mapped[int]            = mapped_column(Integer, default=1)   # لحساب تكلفة الوجبة/الفرد
    paid_by:         Mapped[Optional[int]]  = mapped_column(ForeignKey("workers.id"), nullable=True)
    receipt_ref:     Mapped[Optional[str]]  = mapped_column(String(100))
    notes:           Mapped[Optional[str]]  = mapped_column(Text)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    project:         Mapped["ExtProject"]   = relationship("ExtProject", back_populates="expenses")


class ExtProjectProgress(Base):
    """تقرير تقدم يومي من الموقع"""
    __tablename__ = "ext_project_progress"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:       Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id:      Mapped[int]            = mapped_column(ForeignKey("ext_projects.id"), index=True)
    report_date:     Mapped[date]           = mapped_column(Date)
    reporter_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("workers.id"), nullable=True)
    completion_pct:  Mapped[int]            = mapped_column(Integer, default=0)
    work_done:       Mapped[Optional[str]]  = mapped_column(Text)
    issues:          Mapped[Optional[str]]  = mapped_column(Text)
    next_steps:      Mapped[Optional[str]]  = mapped_column(Text)
    client_notes:    Mapped[Optional[str]]  = mapped_column(Text)
    client_approved: Mapped[bool]           = mapped_column(Boolean, default=False)
    workers_present: Mapped[int]            = mapped_column(Integer, default=0)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    project:         Mapped["ExtProject"]   = relationship("ExtProject", back_populates="progress_reports")


class ExtProjectPayment(Base):
    """دفعات التحصيل من العميل — مقسّمة بالمراحل"""
    __tablename__ = "ext_project_payments"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:       Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id:      Mapped[int]            = mapped_column(ForeignKey("ext_projects.id"), index=True)
    milestone:       Mapped[str]            = mapped_column(String(100))   # "مقدم 30%", "عند التسليم", ...
    milestone_pct:   Mapped[int]            = mapped_column(Integer, default=0)
    due_amount:      Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    due_date:        Mapped[Optional[date]] = mapped_column(Date)
    paid_amount:     Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    paid_date:       Mapped[Optional[date]] = mapped_column(Date)
    payment_method:  Mapped[Optional[str]]  = mapped_column(String(30))   # cash|cheque|transfer
    reference:       Mapped[Optional[str]]  = mapped_column(String(100))
    # الحالة: pending|partial|paid|overdue
    status:          Mapped[str]            = mapped_column(String(20), default="pending")
    notes:           Mapped[Optional[str]]  = mapped_column(Text)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    project:         Mapped["ExtProject"]   = relationship("ExtProject", back_populates="payments")


# ═════════════════════════════════════════════════════════════════════════════
# IMPROVEMENT #1 — PURCHASE ORDERS (أوامر الشراء)
# ═════════════════════════════════════════════════════════════════════════════

class PurchaseOrder(Base):
    """أمر شراء — يسبق فاتورة الشراء ويتتبع الاستلام"""
    __tablename__ = "purchase_orders"
    id:            Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:          Mapped[str]            = mapped_column(String(50), unique=True, index=True)
    supplier_id:   Mapped[Optional[int]]  = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    supplier_name: Mapped[Optional[str]]  = mapped_column(String(200))
    order_date:    Mapped[date]           = mapped_column(Date)
    expected_date: Mapped[Optional[date]] = mapped_column(Date)
    # draft | approved | partial | received | cancelled
    status:        Mapped[str]            = mapped_column(String(20), default="draft")
    subtotal:      Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    tax_pct:       Mapped[Decimal]        = mapped_column(Numeric(5, 2), default=0)
    tax_amount:    Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    discount_amt:  Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    total:         Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    notes:         Mapped[Optional[str]]  = mapped_column(Text)
    approved_by:   Mapped[Optional[int]]  = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at:   Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at:    Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    updated_at:    Mapped[datetime]       = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    lines:    Mapped[List["PurchaseOrderLine"]] = relationship("PurchaseOrderLine", back_populates="order", cascade="all, delete-orphan")
    supplier: Mapped[Optional["Supplier"]]      = relationship("Supplier", foreign_keys=[supplier_id])


class PurchaseOrderLine(Base):
    """بند في أمر الشراء"""
    __tablename__ = "purchase_order_lines"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:    Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    order_id:     Mapped[int]            = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    material_id:  Mapped[Optional[int]]  = mapped_column(ForeignKey("materials.id"), nullable=True)
    description:  Mapped[str]            = mapped_column(String(300))
    ordered_qty:  Mapped[Decimal]        = mapped_column(Numeric(12, 3))
    received_qty: Mapped[Decimal]        = mapped_column(Numeric(12, 3), default=0)
    unit:         Mapped[str]            = mapped_column(String(20), default="قطعة")
    unit_cost:    Mapped[Decimal]        = mapped_column(Numeric(12, 2))
    total_cost:   Mapped[Decimal]        = mapped_column(Numeric(14, 2))
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    order:    Mapped["PurchaseOrder"] = relationship("PurchaseOrder", back_populates="lines")
    material: Mapped[Optional["Material"]] = relationship("Material", foreign_keys=[material_id])


# ═════════════════════════════════════════════════════════════════════════════
# IMPROVEMENT #2 — CHART OF ACCOUNTS + ACCOUNTING ENTRIES (دليل الحسابات)
# ═════════════════════════════════════════════════════════════════════════════

class ChartOfAccount(Base):
    """دليل الحسابات — شجرة حسابات ثنائية القيد"""
    __tablename__ = "chart_of_accounts"
    id:          Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:   Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:        Mapped[str]            = mapped_column(String(20), index=True)
    name_ar:     Mapped[str]            = mapped_column(String(200))
    name_en:     Mapped[Optional[str]]  = mapped_column(String(200))
    # asset | liability | equity | revenue | expense
    account_type: Mapped[str]           = mapped_column(String(20))
    # debit | credit  (normal balance)
    normal_balance: Mapped[str]         = mapped_column(String(10), default="debit")
    parent_id:   Mapped[Optional[int]]  = mapped_column(ForeignKey("chart_of_accounts.id"), nullable=True)
    is_active:   Mapped[bool]           = mapped_column(Boolean, default=True)
    notes:       Mapped[Optional[str]]  = mapped_column(Text)
    created_at:  Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    children: Mapped[List["ChartOfAccount"]] = relationship("ChartOfAccount", back_populates="parent")
    parent:   Mapped[Optional["ChartOfAccount"]] = relationship("ChartOfAccount", back_populates="children", remote_side="ChartOfAccount.id")


class AccountingEntry(Base):
    """قيد محاسبي ثنائي (مزدوج) — يحل محل JournalEntry القديم تدريجياً"""
    __tablename__ = "accounting_entries"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:    Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    entry_date:   Mapped[date]           = mapped_column(Date, index=True)
    reference:    Mapped[Optional[str]]  = mapped_column(String(100))
    description:  Mapped[str]            = mapped_column(String(400))
    # source: manual | sales_invoice | purchase_invoice | payment | work_order
    source:       Mapped[str]            = mapped_column(String(30), default="manual")
    source_id:    Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    posted:       Mapped[bool]           = mapped_column(Boolean, default=False)
    period_year:  Mapped[Optional[int]]  = mapped_column(Integer, nullable=True, index=True)
    period_month: Mapped[Optional[int]]  = mapped_column(Integer, nullable=True, index=True)
    created_by:   Mapped[Optional[int]]  = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    lines: Mapped[List["AccountingEntryLine"]] = relationship("AccountingEntryLine", back_populates="entry", cascade="all, delete-orphan")


class AccountingEntryLine(Base):
    """سطر في القيد المحاسبي"""
    __tablename__ = "accounting_entry_lines"
    id:         Mapped[int]            = mapped_column(primary_key=True)
    entry_id:   Mapped[int]            = mapped_column(ForeignKey("accounting_entries.id"), index=True)
    account_id: Mapped[int]            = mapped_column(ForeignKey("chart_of_accounts.id"))
    # debit | credit
    side:       Mapped[str]            = mapped_column(String(10))
    amount:     Mapped[Decimal]        = mapped_column(Numeric(14, 2))
    notes:      Mapped[Optional[str]]  = mapped_column(String(300))

    entry:   Mapped["AccountingEntry"]  = relationship("AccountingEntry", back_populates="lines")
    account: Mapped["ChartOfAccount"]   = relationship("ChartOfAccount")


class CustomerPaymentAllocation(Base):
    """ربط دفعة عميل بفاتورة بعينها"""
    __tablename__ = "customer_payment_allocations"
    id:         Mapped[int]     = mapped_column(primary_key=True)
    tenant_id:  Mapped[int]     = mapped_column(ForeignKey("tenants.id"), index=True)
    payment_id: Mapped[int]     = mapped_column(ForeignKey("customer_payments.id"), index=True)
    invoice_id: Mapped[int]     = mapped_column(ForeignKey("invoices.id"), index=True)
    amount:     Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    payment: Mapped["CustomerPayment"] = relationship("CustomerPayment")
    invoice: Mapped["Invoice"]         = relationship("Invoice")


class SupplierPaymentAllocation(Base):
    """ربط دفعة مورد بفاتورة شراء بعينها — مرآة لـ CustomerPaymentAllocation"""
    __tablename__ = "supplier_payment_allocations"
    id:         Mapped[int]     = mapped_column(primary_key=True)
    tenant_id:  Mapped[int]     = mapped_column(ForeignKey("tenants.id"), index=True)
    payment_id: Mapped[int]     = mapped_column(ForeignKey("supplier_payments.id"), index=True)
    invoice_id: Mapped[int]     = mapped_column(ForeignKey("purchase_invoices.id"), index=True)
    amount:     Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    payment: Mapped["SupplierPayment"] = relationship("SupplierPayment")
    invoice: Mapped["PurchaseInvoice"]  = relationship("PurchaseInvoice")


# ═════════════════════════════════════════════════════════════════════════════
# FIXED ASSETS — الأصول الثابتة والإهلاك
# ═════════════════════════════════════════════════════════════════════════════

class FixedAsset(Base):
    """أصل ثابت — معدة/مبنى/سيارة... مع بيانات الإهلاك"""
    __tablename__ = "fixed_assets"
    id:               Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:        Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:             Mapped[str]            = mapped_column(String(30), index=True)
    name:             Mapped[str]            = mapped_column(String(200))
    # نوع الأصل: equipment | vehicle | building | furniture | other
    asset_type:       Mapped[str]            = mapped_column(String(30), default="equipment")
    purchase_date:    Mapped[date]           = mapped_column(Date)
    cost:             Mapped[Decimal]        = mapped_column(Numeric(14, 2))
    salvage_value:    Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    useful_life_years: Mapped[int]           = mapped_column(Integer, default=5)
    accumulated_depreciation: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    # active | disposed
    status:           Mapped[str]            = mapped_column(String(20), default="active")
    location:         Mapped[Optional[str]]  = mapped_column(String(200), nullable=True)
    notes:            Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    last_depreciation_year:  Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_depreciation_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at:       Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    depreciation_entries: Mapped[List["FixedAssetDepreciation"]] = relationship(
        "FixedAssetDepreciation", back_populates="asset", cascade="all, delete-orphan"
    )


class FixedAssetDepreciation(Base):
    """سجل قيود إهلاك الأصول الثابتة الشهرية/السنوية"""
    __tablename__ = "fixed_asset_depreciations"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:    Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    asset_id:     Mapped[int]            = mapped_column(ForeignKey("fixed_assets.id"), index=True)
    period_year:  Mapped[int]            = mapped_column(Integer)
    period_month: Mapped[int]            = mapped_column(Integer)
    amount:       Mapped[Decimal]        = mapped_column(Numeric(14, 2))
    entry_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("accounting_entries.id"), nullable=True)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    asset: Mapped["FixedAsset"] = relationship("FixedAsset", back_populates="depreciation_entries")
    entry: Mapped[Optional["AccountingEntry"]] = relationship("AccountingEntry")


# ═════════════════════════════════════════════════════════════════════════════
# IMPROVEMENT #3 — PRODUCTION SCHEDULING + QUALITY CONTROL
# ═════════════════════════════════════════════════════════════════════════════

class ProductionSchedule(Base):
    """جدولة الإنتاج — ربط أوامر الإنتاج بخط زمني"""
    __tablename__ = "production_schedules"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:       Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    work_order_id:   Mapped[int]            = mapped_column(ForeignKey("work_orders.id"), index=True)
    customer_order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customer_orders.id"), nullable=True)
    scheduled_start: Mapped[date]           = mapped_column(Date)
    scheduled_end:   Mapped[date]           = mapped_column(Date)
    actual_start:    Mapped[Optional[date]] = mapped_column(Date)
    actual_end:      Mapped[Optional[date]] = mapped_column(Date)
    priority:        Mapped[int]            = mapped_column(Integer, default=5)  # 1=highest
    # planned | in_progress | completed | delayed | cancelled
    status:          Mapped[str]            = mapped_column(String(20), default="planned")
    notes:           Mapped[Optional[str]]  = mapped_column(Text)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    updated_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    work_order:     Mapped["WorkOrder"]              = relationship("WorkOrder")
    customer_order: Mapped[Optional["CustomerOrder"]] = relationship("CustomerOrder")


class QualityInspection(Base):
    """تفتيش جودة — مرتبط بأمر إنتاج أو مخزون"""
    __tablename__ = "quality_inspections"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:       Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    work_order_id:   Mapped[Optional[int]]  = mapped_column(ForeignKey("work_orders.id"), nullable=True)
    material_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("materials.id"), nullable=True)
    inspection_date: Mapped[date]           = mapped_column(Date)
    inspector_id:    Mapped[Optional[int]]  = mapped_column(ForeignKey("workers.id"), nullable=True)
    # incoming | in_process | final | outgoing
    inspection_type: Mapped[str]            = mapped_column(String(20), default="final")
    qty_inspected:   Mapped[Decimal]        = mapped_column(Numeric(12, 3))
    qty_passed:      Mapped[Decimal]        = mapped_column(Numeric(12, 3), default=0)
    qty_failed:      Mapped[Decimal]        = mapped_column(Numeric(12, 3), default=0)
    qty_rework:      Mapped[Decimal]        = mapped_column(Numeric(12, 3), default=0)
    # pass | fail | conditional
    result:          Mapped[str]            = mapped_column(String(20), default="pass")
    failure_reason:  Mapped[Optional[str]]  = mapped_column(Text)
    corrective_action: Mapped[Optional[str]] = mapped_column(Text)
    notes:           Mapped[Optional[str]]  = mapped_column(Text)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    work_order: Mapped[Optional["WorkOrder"]] = relationship("WorkOrder")
    material:   Mapped[Optional["Material"]]  = relationship("Material")
    inspector:  Mapped[Optional["Worker"]]    = relationship("Worker")


class SalesOrderWorkOrder(Base):
    """ربط طلب بيع بأمر/أوامر إنتاج"""
    __tablename__ = "sales_order_work_orders"
    id:               Mapped[int] = mapped_column(primary_key=True)
    customer_order_id: Mapped[int] = mapped_column(ForeignKey("customer_orders.id"), index=True)
    work_order_id:    Mapped[int]  = mapped_column(ForeignKey("work_orders.id"), index=True)
    notes:            Mapped[Optional[str]] = mapped_column(String(300))
    created_at:       Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())

    customer_order: Mapped["CustomerOrder"] = relationship("CustomerOrder")
    work_order:     Mapped["WorkOrder"]     = relationship("WorkOrder")


# ═════════════════════════════════════════════════════════════════════════════
# WORK ORDER STAGES — مراحل أمر الإنتاج
# ═════════════════════════════════════════════════════════════════════════════

class WorkOrderStage(Base):
    """مراحل تنفيذ أمر الإنتاج (قطع، لحام، تجميع، طلاء، اختبار، تسليم)"""
    __tablename__ = "work_order_stages"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:       Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    work_order_id:   Mapped[int]            = mapped_column(ForeignKey("work_orders.id"), index=True)
    stage_name:      Mapped[str]            = mapped_column(String(100))   # قطع | لحام | تجميع | طلاء | اختبار | تسليم
    stage_order:     Mapped[int]            = mapped_column(Integer, default=0)
    # pending | in_progress | done | skipped
    status:          Mapped[str]            = mapped_column(String(20), default="pending")
    assigned_worker_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workers.id"), nullable=True)
    planned_hours:   Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)
    actual_hours:    Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)
    started_at:      Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at:    Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notes:           Mapped[Optional[str]]  = mapped_column(Text)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    work_order:      Mapped["WorkOrder"]          = relationship("WorkOrder")
    assigned_worker: Mapped[Optional["Worker"]]   = relationship("Worker")


# ═════════════════════════════════════════════════════════════════════════════
# STOCK BATCH — تتبع الدُفعات/الشحنات (Lot Tracking)
# ═════════════════════════════════════════════════════════════════════════════

class StockBatch(Base):
    """دُفعة مخزون — تتبع كل شحنة مستلمة على حدة"""
    __tablename__ = "stock_batches"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:       Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    material_id:     Mapped[int]            = mapped_column(ForeignKey("materials.id"), index=True)
    lot_number:      Mapped[str]            = mapped_column(String(100), index=True)
    supplier_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    purchase_invoice_id: Mapped[Optional[int]] = mapped_column(ForeignKey("purchase_invoices.id"), nullable=True)
    received_date:   Mapped[date]           = mapped_column(Date)
    qty_received:    Mapped[Decimal]        = mapped_column(Numeric(12, 3))
    qty_remaining:   Mapped[Decimal]        = mapped_column(Numeric(12, 3))
    unit_cost:       Mapped[Decimal]        = mapped_column(Numeric(12, 2))
    expiry_date:     Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # active | consumed | expired | returned
    status:          Mapped[str]            = mapped_column(String(20), default="active")
    notes:           Mapped[Optional[str]]  = mapped_column(Text)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    material: Mapped["Material"]           = relationship("Material")
    supplier: Mapped[Optional["Supplier"]] = relationship("Supplier")


# ═════════════════════════════════════════════════════════════════════════════
# TREASURY — الخزنة (صناديق النقدية والبنوك)
# ═════════════════════════════════════════════════════════════════════════════

class Treasury(Base):
    """خزنة — صندوق نقدي أو حساب بنكي"""
    __tablename__ = "treasuries"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_treasury_tenant_name"),)
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:    Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    name:         Mapped[str]            = mapped_column(String(100))
    # cash | bank
    kind:         Mapped[str]            = mapped_column(String(20), default="cash")
    opening_balance: Mapped[Decimal]     = mapped_column(Numeric(14, 2), default=0)
    balance:      Mapped[Decimal]        = mapped_column(Numeric(14, 2), default=0)
    is_active:    Mapped[bool]           = mapped_column(Boolean, default=True)
    notes:        Mapped[Optional[str]]  = mapped_column(Text)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    # حساب النقدية/البنك المقابل في دليل الحسابات (1110 الصندوق أو 1120 البنك)
    coa_account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("chart_of_accounts.id"), nullable=True)


class TreasuryCategory(Base):
    """فئة حركة مالية (إيجار، مرتبات، خامات، كهرباء...)"""
    __tablename__ = "treasury_categories"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_treasury_cat_tenant_name"),)
    id:        Mapped[int]            = mapped_column(primary_key=True)
    tenant_id: Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    name:      Mapped[str]            = mapped_column(String(100))
    # in | out | both — الفئة بتخص دخل ولا صرف
    flow:      Mapped[str]            = mapped_column(String(10), default="both")
    created_at: Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    # الحساب المقابل في دليل الحسابات (مصروف/إيراد) — يحدده المستخدم، بلا تعيين تلقائي
    coa_account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("chart_of_accounts.id"), nullable=True)


class TreasuryTransaction(Base):
    """حركة على الخزنة — داخل أو خارج"""
    __tablename__ = "treasury_transactions"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:    Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    treasury_id:  Mapped[int]            = mapped_column(ForeignKey("treasuries.id"), index=True)
    # in (داخل) | out (خارج)
    direction:    Mapped[str]            = mapped_column(String(5))
    amount:       Mapped[Decimal]        = mapped_column(Numeric(14, 2))
    category_id:  Mapped[Optional[int]]  = mapped_column(ForeignKey("treasury_categories.id"), nullable=True)
    txn_date:     Mapped[date]           = mapped_column(Date)
    description:  Mapped[Optional[str]]  = mapped_column(Text)
    reference:    Mapped[Optional[str]]  = mapped_column(String(100))
    # manual | customer_payment | supplier_payment | transfer | other
    source:       Mapped[str]            = mapped_column(String(30), default="manual")
    # لو الحركة تحويل بين خزنتين، ده بيربط الطرفين
    transfer_group: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # لو الحركة جاية من مدفوعة عميل/مورد، نخزّن الـ id الأصلي عشان نمنع التكرار
    linked_type:  Mapped[Optional[str]]  = mapped_column(String(30), nullable=True)
    linked_id:    Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    created_by:   Mapped[Optional[int]]  = mapped_column(ForeignKey("users.id"), nullable=True)  # من سجّل الحركة
    receipt_no:   Mapped[Optional[str]]  = mapped_column(String(50), nullable=True, index=True)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    # طريقة الدفع: cash | cheque | wallet | other
    payment_method: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="cash")
    # بيانات الشيك (لو payment_method == cheque)
    cheque_number: Mapped[Optional[str]]  = mapped_column(String(50), nullable=True)
    cheque_date:   Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # تاريخ الاستحقاق
    cheque_bank:   Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    # حالة الشيك: under_collection | cleared | bounced | cancelled
    cheque_status: Mapped[Optional[str]]  = mapped_column(String(20), nullable=True)

    treasury: Mapped["Treasury"]                  = relationship("Treasury")
    category: Mapped[Optional["TreasuryCategory"]] = relationship("TreasuryCategory")


class SupplyContract(Base):
    """أمر التوريد — يُنشأ من عرض سعر أو مستقلاً"""
    __tablename__ = "supply_contracts"
    id:             Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:      Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:           Mapped[str]            = mapped_column(String(50), unique=True)
    quotation_id:   Mapped[Optional[int]]  = mapped_column(ForeignKey("quotations.id"), nullable=True)
    customer_id:    Mapped[int]            = mapped_column(ForeignKey("customers.id"))
    date:           Mapped[date]           = mapped_column(Date)
    valid_until:    Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status:         Mapped[str]            = mapped_column(String(20), default="draft")
    # شروط تجارية
    payment_terms:  Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    delivery_terms: Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    warranty_months:     Mapped[int]           = mapped_column(Integer, default=12)
    warranty_description:Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # ما يشمله الضمان
    # أسعار
    subtotal:       Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    tax_pct:        Mapped[Decimal]        = mapped_column(Numeric(5,2),  default=0)
    tax_amount:     Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    discount_amt:   Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    total:          Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    # نص الأمر
    scope_of_work:  Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    special_terms:  Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    notes:          Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    created_at:     Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    # إقرار التسليم والاستلام
    delivery_confirmed: Mapped[bool]           = mapped_column(Boolean, default=False)
    delivery_date:      Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    received_by:        Mapped[Optional[str]]  = mapped_column(String(200), nullable=True)
    receiver_title:     Mapped[Optional[str]]  = mapped_column(String(200), nullable=True)
    delivery_notes:     Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    vehicle_plate:      Mapped[Optional[str]]  = mapped_column(String(50),  nullable=True)
    vehicle_type:       Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    driver_name:        Mapped[Optional[str]]  = mapped_column(String(200), nullable=True)
    driver_license:     Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    driver_phone:       Mapped[Optional[str]]  = mapped_column(String(50),  nullable=True)

    customer:   Mapped["Customer"]                  = relationship("Customer")
    quotation:  Mapped[Optional["Quotation"]]        = relationship("Quotation")
    lines:      Mapped[List["SupplyContractLine"]]   = relationship(
        "SupplyContractLine", back_populates="contract", order_by="SupplyContractLine.sort_order"
    )


class SupplyContractLine(Base):
    __tablename__ = "supply_contract_lines"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:    Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    contract_id:  Mapped[int]            = mapped_column(ForeignKey("supply_contracts.id"), index=True)
    item_type:    Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    model_no:     Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    description:  Mapped[str]            = mapped_column(String(500))
    qty:          Mapped[Decimal]        = mapped_column(Numeric(14,3), default=1)
    unit:         Mapped[str]            = mapped_column(String(20), default="قطعة")
    unit_price:   Mapped[Decimal]        = mapped_column(Numeric(14,2))
    total_price:  Mapped[Decimal]        = mapped_column(Numeric(14,2))
    notes:        Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    sort_order:   Mapped[int]            = mapped_column(Integer, default=0)
    material_id:  Mapped[Optional[int]]  = mapped_column(ForeignKey("materials.id"), nullable=True)
    contract:     Mapped["SupplyContract"] = relationship("SupplyContract", back_populates="lines")
    material:     Mapped[Optional["Material"]] = relationship("Material", foreign_keys=[material_id])


class CreditNote(Base):
    """إشعار دائن — مرتجع مبيعات كلي أو جزئي"""
    __tablename__ = "credit_notes"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:    Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:         Mapped[str]            = mapped_column(String(50), unique=True)
    invoice_id:   Mapped[Optional[int]]  = mapped_column(ForeignKey("invoices.id"), nullable=True)
    customer_id:  Mapped[int]            = mapped_column(ForeignKey("customers.id"))
    date:         Mapped[date]           = mapped_column(Date)
    reason:       Mapped[Optional[str]]  = mapped_column(String(200), nullable=True)
    # full | partial
    return_type:  Mapped[str]            = mapped_column(String(20), default="partial")
    # هل الإشعار ده فعليًا مرتجع بضاعة حقيقي (البضاعة رجعت المخزون) — بخلاف
    # إشعار دائن بيبقى تسوية سعر/خصم/خطأ فاتورة من غير أي حركة بضاعة فعلية.
    # الاسم الشائع في أنظمة ERP لنفس المفهوم: "Sales Return" (مرتجع مبيعات).
    is_return:    Mapped[bool]           = mapped_column(Boolean, default=False)
    subtotal:     Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    tax_pct:      Mapped[Decimal]        = mapped_column(Numeric(5,2),  default=0)
    tax_amount:   Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    discount_amt: Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    total:        Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    # draft | issued | applied
    status:       Mapped[str]            = mapped_column(String(20), default="draft")
    notes:        Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    issued_at:    Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    customer: Mapped["Customer"]               = relationship("Customer")
    invoice:  Mapped[Optional["Invoice"]]      = relationship("Invoice")
    lines:    Mapped[List["CreditNoteLine"]]   = relationship(
        "CreditNoteLine", back_populates="credit_note", order_by="CreditNoteLine.sort_order"
    )


class CreditNoteLine(Base):
    __tablename__ = "credit_note_lines"
    id:              Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:       Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    credit_note_id:  Mapped[int]            = mapped_column(ForeignKey("credit_notes.id"), index=True)
    invoice_line_id: Mapped[Optional[int]]  = mapped_column(ForeignKey("invoice_lines.id"), nullable=True)
    description:     Mapped[str]            = mapped_column(String(500))
    qty:             Mapped[Decimal]        = mapped_column(Numeric(14,3), default=1)
    unit:            Mapped[str]            = mapped_column(String(20), default="قطعة")
    unit_price:      Mapped[Decimal]        = mapped_column(Numeric(14,2))
    total_price:     Mapped[Decimal]        = mapped_column(Numeric(14,2))
    sort_order:      Mapped[int]            = mapped_column(Integer, default=0)
    credit_note: Mapped["CreditNote"] = relationship("CreditNote", back_populates="lines")


class WorkCompletionCert(Base):
    """شهادة إتمام أعمال — Work Completion Certificate"""
    __tablename__ = "work_completion_certs"
    id:            Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:     Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:          Mapped[str]            = mapped_column(String(50), unique=True)
    customer_id:   Mapped[int]            = mapped_column(ForeignKey("customers.id"))
    invoice_id:    Mapped[Optional[int]]  = mapped_column(ForeignKey("invoices.id"), nullable=True)
    contract_id:   Mapped[Optional[int]]  = mapped_column(ForeignKey("supply_contracts.id"), nullable=True)
    date:          Mapped[date]           = mapped_column(Date)
    project_name:  Mapped[str]            = mapped_column(String(300))
    project_location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    start_date:    Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date:      Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    contract_value: Mapped[Decimal]       = mapped_column(Numeric(14,2), default=0)
    warranty_months: Mapped[int]          = mapped_column(Integer, default=12)
    # draft | issued
    status:        Mapped[str]            = mapped_column(String(20), default="draft")
    scope_summary: Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    notes:         Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    issued_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at:    Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    customer:  Mapped["Customer"]                    = relationship("Customer")
    invoice:   Mapped[Optional["Invoice"]]           = relationship("Invoice")
    contract:  Mapped[Optional["SupplyContract"]]    = relationship("SupplyContract")
    items:     Mapped[List["WorkCompletionItem"]]    = relationship(
        "WorkCompletionItem", back_populates="cert", order_by="WorkCompletionItem.sort_order"
    )


class WorkCompletionItem(Base):
    __tablename__ = "work_completion_items"
    id:       Mapped[int]  = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    cert_id:  Mapped[int]  = mapped_column(ForeignKey("work_completion_certs.id"), index=True)
    description: Mapped[str]          = mapped_column(String(500))
    qty:         Mapped[Decimal]      = mapped_column(Numeric(14,3), default=1)
    unit:        Mapped[str]          = mapped_column(String(30), default="وحدة")
    unit_price:  Mapped[Decimal]      = mapped_column(Numeric(14,2), default=0)
    total_price: Mapped[Decimal]      = mapped_column(Numeric(14,2), default=0)
    sort_order:  Mapped[int]          = mapped_column(Integer, default=0)
    cert: Mapped["WorkCompletionCert"] = relationship("WorkCompletionCert", back_populates="items")


class WarrantyCertificate(Base):
    """شهادة ضمان — Warranty Certificate"""
    __tablename__ = "warranty_certificates"
    id:             Mapped[int]            = mapped_column(primary_key=True)
    tenant_id:      Mapped[int]            = mapped_column(ForeignKey("tenants.id"), index=True)
    code:           Mapped[str]            = mapped_column(String(50), unique=True)
    customer_id:    Mapped[int]            = mapped_column(ForeignKey("customers.id"))
    invoice_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("invoices.id"), nullable=True)
    wcc_id:         Mapped[Optional[int]]  = mapped_column(ForeignKey("work_completion_certs.id"), nullable=True)
    date:           Mapped[date]           = mapped_column(Date)
    product_name:   Mapped[str]            = mapped_column(String(300))
    model_no:       Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    serial_no:      Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    warranty_months: Mapped[int]           = mapped_column(Integer, default=12)
    warranty_start: Mapped[date]           = mapped_column(Date)
    warranty_end:   Mapped[date]           = mapped_column(Date)
    covered_items:  Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    exclusions:     Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    service_contact: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # draft | issued
    status:         Mapped[str]            = mapped_column(String(20), default="draft")
    notes:          Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    issued_at:      Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at:     Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)
    tenant:         Mapped["Tenant"]       = relationship("Tenant", foreign_keys=[tenant_id], lazy="noload")
    customer:       Mapped["Customer"]     = relationship("Customer", foreign_keys=[customer_id], lazy="noload")
    invoice:        Mapped[Optional["Invoice"]]             = relationship("Invoice",           foreign_keys=[invoice_id], lazy="noload")
    wcc:            Mapped[Optional["WorkCompletionCert"]]  = relationship("WorkCompletionCert", foreign_keys=[wcc_id],    lazy="noload")


# ═════════════════════════════════════════════════════════════════════════════
# ANALYTICS EVENT — سجل أحداث المنتج (Product Analytics)
# جدول عام لتسجيل أي حدث مهم في رحلة العميل، يُستخدم لاحقًا لبناء تحليلات
# الاستخدام والتحويل (funnel) ومعدلات التبني والاحتفاظ بالعملاء.
# ═════════════════════════════════════════════════════════════════════════════

class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"
    id:          Mapped[int]                = mapped_column(primary_key=True)
    # tenant_id قابل لـ null عشان يسمح بتسجيل أحداث ما قبل تسجيل الورشة (مثل زيارة صفحة الهبوط لاحقًا)
    tenant_id:   Mapped[Optional[int]]       = mapped_column(ForeignKey("tenants.id"), index=True, nullable=True)
    user_id:     Mapped[Optional[int]]       = mapped_column(Integer, index=True, nullable=True)
    # اسم الحدث، مثال: user_login, quotation_created, order_created, invoice_created, payment_received
    event_type:  Mapped[str]                 = mapped_column(String(100), index=True)
    entity_type: Mapped[Optional[str]]       = mapped_column(String(50), nullable=True)
    entity_id:   Mapped[Optional[int]]       = mapped_column(Integer, nullable=True)
    # بيانات إضافية حرة الشكل (مبلغ الفاتورة، عدد الأصناف... إلخ)
    event_metadata: Mapped[Optional[dict]]   = mapped_column(JSON, nullable=True)
    # اسم الـ endpoint اللي أطلق الحدث، لتحليل استخدام الميزات (feature adoption)
    source_path: Mapped[Optional[str]]       = mapped_column(String(200), nullable=True)
    created_at:  Mapped[datetime]            = mapped_column(DateTime, server_default=func.now(), index=True)
