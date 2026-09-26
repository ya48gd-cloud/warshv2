"""Remnant management — إدارة نسالة الألواح والأسياخ

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-14

Changes:
- new table bar_stock_items (مكافئ 1D لـ sheet_stock_items)
- sheet_stock_items: add source_work_order_id (FK work_orders, nullable)
- bar_stock_items: add source_work_order_id (FK work_orders, nullable)
- materials: add min_remnant_value NUMERIC(10,2) nullable
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table: str, col_name: str, col_type, server_default=None):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = [c["name"] for c in insp.get_columns(table)]
    if col_name not in existing:
        op.add_column(table, sa.Column(col_name, col_type, nullable=True, server_default=server_default))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = insp.get_table_names()

    if "bar_stock_items" not in existing_tables:
        op.create_table(
            "bar_stock_items",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), index=True, nullable=False),
            sa.Column("material_id", sa.Integer, sa.ForeignKey("materials.id"), index=True, nullable=False),
            sa.Column("length_cm", sa.Numeric(10, 2), nullable=False),
            sa.Column("qty", sa.Integer, nullable=False, server_default="1"),
            sa.Column("source", sa.String(20), nullable=False, server_default="purchased"),
            sa.Column("source_work_order_id", sa.Integer, sa.ForeignKey("work_orders.id"), nullable=True),
            sa.Column("notes", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        )

    _add_column_if_missing("sheet_stock_items", "source_work_order_id", sa.Integer)
    _add_column_if_missing("materials", "min_remnant_value", sa.Numeric(10, 2))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = insp.get_table_names()
    if "bar_stock_items" in existing_tables:
        op.drop_table("bar_stock_items")
    try:
        op.drop_column("sheet_stock_items", "source_work_order_id")
    except Exception:
        pass
    try:
        op.drop_column("materials", "min_remnant_value")
    except Exception:
        pass
