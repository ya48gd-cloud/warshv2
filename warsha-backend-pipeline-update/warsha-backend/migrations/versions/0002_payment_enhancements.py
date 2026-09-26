"""Payment enhancements — add payment tracking fields.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-07

Changes:
- supplier_payments: add payment_method VARCHAR(30), reference VARCHAR(100)
- customer_payments: add created_by INT FK users.id
- supplier_payments: add created_by INT FK users.id
- treasury_transactions: add created_by INT FK users.id
- supply_contracts: add warranty_description TEXT
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table: str, col_name: str, col_type):
    """Add column only if it doesn't already exist (idempotent)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = [c["name"] for c in insp.get_columns(table)]
    if col_name not in existing:
        op.add_column(table, sa.Column(col_name, col_type, nullable=True))


def upgrade() -> None:
    # supplier_payments — طريقة الدفع والمرجع
    _add_column_if_missing("supplier_payments", "payment_method", sa.String(30))
    _add_column_if_missing("supplier_payments", "reference",      sa.String(100))

    # created_by — من سجّل الدفعة
    _add_column_if_missing("customer_payments",    "created_by", sa.Integer)
    _add_column_if_missing("supplier_payments",    "created_by", sa.Integer)
    _add_column_if_missing("treasury_transactions","created_by", sa.Integer)

    # supply_contracts — وصف الضمان
    _add_column_if_missing("supply_contracts", "warranty_description", sa.Text)

    # إضافة FK constraints (اختياري — يُتجاهل لو كانت SQLite)
    try:
        op.create_foreign_key(
            "fk_custpay_created_by",
            "customer_payments", "users",
            ["created_by"], ["id"],
            ondelete="SET NULL",
        )
    except Exception:
        pass
    try:
        op.create_foreign_key(
            "fk_suppay_created_by",
            "supplier_payments", "users",
            ["created_by"], ["id"],
            ondelete="SET NULL",
        )
    except Exception:
        pass
    try:
        op.create_foreign_key(
            "fk_treasury_created_by",
            "treasury_transactions", "users",
            ["created_by"], ["id"],
            ondelete="SET NULL",
        )
    except Exception:
        pass


def downgrade() -> None:
    # حذف الـ FK constraints أولاً
    for fk in ["fk_custpay_created_by", "fk_suppay_created_by", "fk_treasury_created_by"]:
        try:
            op.drop_constraint(fk, fk.split("_")[1] + "_payments" if "pay" in fk else "treasury_transactions")
        except Exception:
            pass

    # حذف الأعمدة
    for table, col in [
        ("supplier_payments",     "payment_method"),
        ("supplier_payments",     "reference"),
        ("customer_payments",     "created_by"),
        ("supplier_payments",     "created_by"),
        ("treasury_transactions", "created_by"),
        ("supply_contracts",      "warranty_description"),
    ]:
        try:
            op.drop_column(table, col)
        except Exception:
            pass
