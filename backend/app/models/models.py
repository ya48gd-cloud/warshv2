"""
SQLAlchemy models — all enum columns use String to avoid
PostgreSQL native enum casting issues.
"""
from __future__ import annotations
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    String, Numeric, Integer, Boolean, Text, DateTime, Date,
    ForeignKey, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


# ── INVENTORY ─────────────────────────────────────────────────────────────────

class MaterialCategory(Base):
    __tablename__ = "material_categories"
    id:         Mapped[int]      = mapped_column(primary_key=True)
    name_ar:    Mapped[str]      = mapped_column(String(120))
    name_en:    Mapped[str]      = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    materials:  Mapped[List["Material"]] = relationship(back_populates="category")


class Material(Base):
    __tablename__ = "materials"
    id:            Mapped[int]            = mapped_column(primary_key=True)
    code:          Mapped[str]            = mapped_column(String(50), unique=True, index=True)
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
    created_at:    Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    updated_at:    Mapped[datetime]       = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    category:      Mapped["MaterialCategory"]   = relationship(back_populates="materials")
    movements:     Mapped[List["StockMovement"]] = relationship(back_populates="material")
    bom_lines:     Mapped[List["BOMLine"]]       = relationship(back_populates="material")
    price_history: Mapped[List["MaterialPriceHistory"]] = relationship(back_populates="material")


class MaterialPriceHistory(Base):
    __tablename__ = "material_price_history"
    id:          Mapped[int]               = mapped_column(primary_key=True)
    material_id: Mapped[int]               = mapped_column(ForeignKey("materials.id"))
    old_price:   Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    new_price:   Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    changed_at:  Mapped[datetime]          = mapped_column(DateTime, server_default=func.now())
    notes:       Mapped[Optional[str]]     = mapped_column(Text)
    material:    Mapped["Material"]        = relationship(back_populates="price_history")


class StockMovement(Base):
    __tablename__ = "stock_movements"
    id:              Mapped[int]            = mapped_column(primary_key=True)
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
    created_at:      Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    material:        Mapped["Material"]     = relationship(back_populates="movements")


# ── ACCOUNTING ────────────────────────────────────────────────────────────────

class WorkOrder(Base):
    __tablename__ = "work_orders"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    code:         Mapped[str]            = mapped_column(String(50), unique=True)
    equipment_id: Mapped[int]            = mapped_column(ForeignKey("equipment.id"))
    customer_order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customer_orders.id"), nullable=True)
    status:       Mapped[str]            = mapped_column(String(20), default="draft")
    planned_cost: Mapped[Decimal]        = mapped_column(Numeric(16, 2), default=0)
    actual_cost:  Mapped[Decimal]        = mapped_column(Numeric(16, 2), default=0)
    start_date:   Mapped[Optional[date]] = mapped_column(Date)
    end_date:     Mapped[Optional[date]] = mapped_column(Date)
    notes:        Mapped[Optional[str]]  = mapped_column(Text)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    equipment:       Mapped["Equipment"]       = relationship(back_populates="work_orders")
    customer_order:  Mapped[Optional["CustomerOrder"]] = relationship(back_populates="work_orders")
    cost_lines:      Mapped[List["CostLine"]]  = relationship(back_populates="work_order")
    journal_entries: Mapped[List["JournalEntry"]] = relationship(back_populates="work_order")


class CostLine(Base):
    __tablename__ = "cost_lines"
    id:            Mapped[int]           = mapped_column(primary_key=True)
    work_order_id: Mapped[int]           = mapped_column(ForeignKey("work_orders.id"))
    cost_type:     Mapped[str]           = mapped_column(String(50))   # material|labor|overhead
    description:   Mapped[str]           = mapped_column(String(300))
    qty:           Mapped[Decimal]       = mapped_column(Numeric(14, 3), default=1)
    unit_cost:     Mapped[Decimal]       = mapped_column(Numeric(14, 2))
    total_cost:    Mapped[Decimal]       = mapped_column(Numeric(14, 2))
    material_id:   Mapped[Optional[int]] = mapped_column(ForeignKey("materials.id"), nullable=True)
    created_at:    Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    work_order:    Mapped["WorkOrder"]   = relationship(back_populates="cost_lines")


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id:             Mapped[int]           = mapped_column(primary_key=True)
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

class Equipment(Base):
    __tablename__ = "equipment"
    id:             Mapped[int]            = mapped_column(primary_key=True)
    code:           Mapped[str]            = mapped_column(String(50), unique=True, index=True)
    name_ar:        Mapped[str]            = mapped_column(String(200))
    name_en:        Mapped[str]            = mapped_column(String(200))
    description:    Mapped[Optional[str]]  = mapped_column(Text)
    parent_id:      Mapped[Optional[int]]  = mapped_column(ForeignKey("equipment.id"), nullable=True)
    level:          Mapped[int]            = mapped_column(Integer, default=0)
    weight_kg:      Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 3))
    cad_drawing_no: Mapped[Optional[str]]  = mapped_column(String(100))
    cad_file_path:  Mapped[Optional[str]]  = mapped_column(String(500))
    is_active:      Mapped[bool]           = mapped_column(Boolean, default=True)
    created_at:     Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    updated_at:     Mapped[datetime]       = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    parent:      Mapped[Optional["Equipment"]]  = relationship("Equipment", remote_side="Equipment.id", back_populates="children")
    children:    Mapped[List["Equipment"]]      = relationship("Equipment", back_populates="parent")
    bom_lines:   Mapped[List["BOMLine"]]        = relationship(back_populates="equipment", foreign_keys="BOMLine.equipment_id")
    dimensions:  Mapped[List["EquipmentDimension"]] = relationship(back_populates="equipment")
    work_orders: Mapped[List["WorkOrder"]]      = relationship(back_populates="equipment")
    cad_files:   Mapped[List["CADFile"]]          = relationship("CADFile", back_populates="equipment")


class BOMLine(Base):
    __tablename__ = "bom_lines"
    id:           Mapped[int]           = mapped_column(primary_key=True)
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
    created_at:   Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    equipment:    Mapped["Equipment"]   = relationship(back_populates="bom_lines", foreign_keys=[equipment_id])
    material:     Mapped["Material"]    = relationship(back_populates="bom_lines")


class EquipmentDimension(Base):
    __tablename__ = "equipment_dimensions"
    id:           Mapped[int]  = mapped_column(primary_key=True)
    equipment_id: Mapped[int]  = mapped_column(ForeignKey("equipment.id"))
    dim_key:      Mapped[str]  = mapped_column(String(80))
    dim_value:    Mapped[str]  = mapped_column(String(80))
    unit:         Mapped[str]  = mapped_column(String(20))
    equipment:    Mapped["Equipment"] = relationship(back_populates="dimensions")


class CADFile(Base):
    """ملفات التصميم المرتبطة بالمعدات"""
    __tablename__ = "cad_files"

    id:           Mapped[int]           = mapped_column(primary_key=True)
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
    id:               Mapped[int]           = mapped_column(primary_key=True)
    code:             Mapped[str]           = mapped_column(String(30), unique=True)
    name:             Mapped[str]           = mapped_column(String(200))
    job_title:        Mapped[Optional[str]] = mapped_column(String(100))
    phone:            Mapped[Optional[str]] = mapped_column(String(30))
    national_id:      Mapped[Optional[str]] = mapped_column(String(30))
    hire_date:        Mapped[Optional[date]] = mapped_column(Date)
    base_weekly_wage: Mapped[Decimal]       = mapped_column(Numeric(12,2))
    daily_wage:        Mapped[Decimal]       = mapped_column(Numeric(12,2), default=0)
    is_active:        Mapped[bool]          = mapped_column(Boolean, default=True)
    notes:            Mapped[Optional[str]] = mapped_column(Text)
    created_at:       Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    payroll_lines:    Mapped[List["PayrollLine"]] = relationship(back_populates="worker")


class PayrollRun(Base):
    __tablename__ = "payroll_runs"
    id:               Mapped[int]            = mapped_column(primary_key=True)
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
    id:           Mapped[int]            = mapped_column(primary_key=True)
    code:         Mapped[str]            = mapped_column(String(30), unique=True)
    name:         Mapped[str]            = mapped_column(String(200))
    phone:        Mapped[Optional[str]]  = mapped_column(String(30))
    address:      Mapped[Optional[str]]  = mapped_column(Text)
    tax_id:       Mapped[Optional[str]]  = mapped_column(String(50))
    credit_limit: Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    balance:      Mapped[Decimal]        = mapped_column(Numeric(14,2), default=0)
    notes:        Mapped[Optional[str]]  = mapped_column(Text)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    orders:       Mapped[List["CustomerOrder"]]   = relationship(back_populates="customer")
    payments:     Mapped[List["CustomerPayment"]] = relationship(back_populates="customer")


class CustomerOrder(Base):
    __tablename__ = "customer_orders"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    code:         Mapped[str]            = mapped_column(String(50), unique=True)
    customer_id:  Mapped[int]            = mapped_column(ForeignKey("customers.id"), index=True)
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
    work_orders:  Mapped[List["WorkOrder"]] = relationship(back_populates="customer_order")


class CustomerPayment(Base):
    __tablename__ = "customer_payments"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    customer_id:  Mapped[int]            = mapped_column(ForeignKey("customers.id"), index=True)
    order_id:     Mapped[Optional[int]]  = mapped_column(ForeignKey("customer_orders.id"), nullable=True)
    amount:       Mapped[Decimal]        = mapped_column(Numeric(14,2))
    payment_type: Mapped[str]            = mapped_column(String(20), default="cash")
    payment_date: Mapped[date]           = mapped_column(Date)
    reference:    Mapped[Optional[str]]  = mapped_column(String(100))
    notes:        Mapped[Optional[str]]  = mapped_column(Text)
    created_at:   Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    customer:     Mapped["Customer"]     = relationship(back_populates="payments")


class Attendance(Base):
    """سجل الحضور اليومي"""
    __tablename__ = "attendance"
    id:             Mapped[int]           = mapped_column(primary_key=True)
    worker_id:      Mapped[int]           = mapped_column(ForeignKey("workers.id"), index=True)
    date:           Mapped[date]          = mapped_column(Date)          # DB column name is 'date'
    status:         Mapped[str]           = mapped_column(String(20), default="present")
    overtime_hours: Mapped[Decimal]       = mapped_column(Numeric(4,1), default=0)
    notes:          Mapped[Optional[str]] = mapped_column(Text)
    created_at:     Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    worker:         Mapped["Worker"]      = relationship("Worker")




class WorkerAdvance(Base):
    __tablename__ = "worker_advances"
    id:             Mapped[int]            = mapped_column(primary_key=True)
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
    id:            Mapped[int]            = mapped_column(primary_key=True)
    username:      Mapped[str]            = mapped_column(String(50), unique=True)
    password_hash: Mapped[str]            = mapped_column(String(64))
    full_name:     Mapped[Optional[str]]  = mapped_column(String(200))
    role:          Mapped[str]            = mapped_column(String(20), default="admin")
    is_active:     Mapped[bool]           = mapped_column(Boolean, default=True)
    last_login:    Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at:    Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())


# ═══════════════════════════════════════════════════════════════
# SALES — المبيعات
# ═══════════════════════════════════════════════════════════════

class Quotation(Base):
    __tablename__ = "quotations"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    code:         Mapped[str]            = mapped_column(String(50), unique=True)
    customer_id:  Mapped[int]            = mapped_column(ForeignKey("customers.id"))
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
    lines:        Mapped[List["QuotationLine"]] = relationship("QuotationLine", back_populates="quotation", order_by="QuotationLine.sort_order")


class QuotationLine(Base):
    __tablename__ = "quotation_lines"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    quotation_id: Mapped[int]            = mapped_column(ForeignKey("quotations.id"), index=True)
    description:  Mapped[str]            = mapped_column(String(500))
    qty:          Mapped[Decimal]        = mapped_column(Numeric(14,3), default=1)
    unit:         Mapped[str]            = mapped_column(String(20), default="pcs")
    unit_price:   Mapped[Decimal]        = mapped_column(Numeric(14,2))
    total_price:  Mapped[Decimal]        = mapped_column(Numeric(14,2))
    notes:        Mapped[Optional[str]]  = mapped_column(Text)
    sort_order:   Mapped[int]            = mapped_column(Integer, default=0)
    quotation:    Mapped["Quotation"]    = relationship("Quotation", back_populates="lines")


class Invoice(Base):
    __tablename__ = "invoices"
    id:           Mapped[int]            = mapped_column(primary_key=True)
    code:         Mapped[str]            = mapped_column(String(50), unique=True)
    quotation_id: Mapped[Optional[int]]  = mapped_column(ForeignKey("quotations.id"), nullable=True)
    customer_id:  Mapped[int]            = mapped_column(ForeignKey("customers.id"))
    date:         Mapped[date]           = mapped_column(Date)
    due_date:     Mapped[Optional[date]] = mapped_column(Date)
    status:       Mapped[str]            = mapped_column(String(20), default="unpaid")
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
    invoice_id:  Mapped[int]           = mapped_column(ForeignKey("invoices.id"), index=True)
    description: Mapped[str]           = mapped_column(String(500))
    qty:         Mapped[Decimal]       = mapped_column(Numeric(14,3), default=1)
    unit:        Mapped[str]           = mapped_column(String(20), default="pcs")
    unit_price:  Mapped[Decimal]       = mapped_column(Numeric(14,2))
    total_price: Mapped[Decimal]       = mapped_column(Numeric(14,2))
    notes:       Mapped[Optional[str]] = mapped_column(Text)
    sort_order:  Mapped[int]           = mapped_column(Integer, default=0)
    invoice:     Mapped["Invoice"]     = relationship("Invoice", back_populates="lines")


# ═══════════════════════════════════════════════════════════════
# INVENTORY ENHANCED MODELS — Phase 1
# ═══════════════════════════════════════════════════════════════

class BinLocation(Base):
    __tablename__ = "bin_locations"
    id:          Mapped[int]               = mapped_column(primary_key=True)
    code:        Mapped[str]               = mapped_column(String(30), unique=True)
    name_ar:     Mapped[Optional[str]]     = mapped_column(String(100))
    zone:        Mapped[Optional[str]]     = mapped_column(String(20))
    shelf:       Mapped[Optional[str]]     = mapped_column(String(10))
    bin:         Mapped[Optional[str]]     = mapped_column(String(10))
    capacity_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(10,2))
    is_active:   Mapped[bool]              = mapped_column(Boolean, default=True)
    notes:       Mapped[Optional[str]]     = mapped_column(Text)


class MaterialBinStock(Base):
    __tablename__ = "material_bin_stock"
    id:          Mapped[int]     = mapped_column(primary_key=True)
    material_id: Mapped[int]     = mapped_column(ForeignKey("materials.id"))
    bin_id:      Mapped[int]     = mapped_column(ForeignKey("bin_locations.id"))
    qty:         Mapped[Decimal] = mapped_column(Numeric(14,3), default=0)
    material:    Mapped["Material"]     = relationship("Material")
    bin:         Mapped["BinLocation"]  = relationship("BinLocation")


class MaterialReservation(Base):
    __tablename__ = "material_reservations"
    id:             Mapped[int]               = mapped_column(primary_key=True)
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
    material:       Mapped["Material"]        = relationship("Material")
    work_order:     Mapped["WorkOrder"]       = relationship("WorkOrder")


class ScrapRecord(Base):
    __tablename__ = "scrap_records"
    id:             Mapped[int]               = mapped_column(primary_key=True)
    work_order_id:  Mapped[Optional[int]]     = mapped_column(ForeignKey("work_orders.id"))
    material_id:    Mapped[Optional[int]]     = mapped_column(ForeignKey("materials.id"))
    qty:            Mapped[Decimal]           = mapped_column(Numeric(14,3))
    unit_cost:      Mapped[Optional[Decimal]] = mapped_column(Numeric(14,2))
    total_cost:     Mapped[Optional[Decimal]] = mapped_column(Numeric(14,2))
    reason:         Mapped[Optional[str]]     = mapped_column(String(200))
    stage:          Mapped[Optional[str]]     = mapped_column(String(100))
    recorded_at:    Mapped[datetime]          = mapped_column(DateTime, server_default=func.now())
    material:       Mapped[Optional["Material"]]  = relationship("Material")
    work_order:     Mapped[Optional["WorkOrder"]] = relationship("WorkOrder")


class MrpSuggestion(Base):
    __tablename__ = "mrp_suggestions"
    id:             Mapped[int]               = mapped_column(primary_key=True)
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
    count_date: Mapped[date]          = mapped_column(Date)
    status:     Mapped[str]           = mapped_column(String(20), default="in_progress")
    notes:      Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    lines:      Mapped[List["StockCountLine"]] = relationship("StockCountLine", back_populates="count")


class StockCountLine(Base):
    __tablename__ = "stock_count_lines"
    id:          Mapped[int]               = mapped_column(primary_key=True)
    count_id:    Mapped[int]               = mapped_column(ForeignKey("stock_counts.id"))
    material_id: Mapped[int]               = mapped_column(ForeignKey("materials.id"))
    system_qty:  Mapped[Optional[Decimal]] = mapped_column(Numeric(14,3))
    actual_qty:  Mapped[Optional[Decimal]] = mapped_column(Numeric(14,3))
    difference:  Mapped[Optional[Decimal]] = mapped_column(Numeric(14,3))
    scanned_at:  Mapped[Optional[datetime]]= mapped_column(DateTime)
    count:       Mapped["StockCount"]      = relationship("StockCount", back_populates="lines")
    material:    Mapped["Material"]        = relationship("Material")
