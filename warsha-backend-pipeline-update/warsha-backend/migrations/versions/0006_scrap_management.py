"""Scrap management — مخزون الهالك وبيعه

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-14

Changes:
- scrap_records: add qty_sold NUMERIC(14,3) default 0
- new table scrap_sales
- new table scrap_sale_lines
- seed chart_of_accounts: 4400 (إيرادات بيع الهالك)
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
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

    _add_column_if_missing("scrap_records", "qty_sold", sa.Numeric(14, 3), server_default="0")

    if "scrap_sales" not in existing_tables:
        op.create_table(
            "scrap_sales",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), index=True, nullable=False),
            sa.Column("code", sa.String(30), nullable=False),
            sa.Column("sale_date", sa.Date, nullable=False),
            sa.Column("buyer_name", sa.String(200), nullable=True),
            sa.Column("treasury_id", sa.Integer, sa.ForeignKey("treasuries.id"), nullable=False),
            sa.Column("payment_method", sa.String(20), nullable=False, server_default="cash"),
            sa.Column("total_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("notes", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        )

    if "scrap_sale_lines" not in existing_tables:
        op.create_table(
            "scrap_sale_lines",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), index=True, nullable=False),
            sa.Column("scrap_sale_id", sa.Integer, sa.ForeignKey("scrap_sales.id"), nullable=False),
            sa.Column("material_id", sa.Integer, sa.ForeignKey("materials.id"), nullable=False),
            sa.Column("qty", sa.Numeric(14, 3), nullable=False),
            sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
            sa.Column("total_price", sa.Numeric(14, 2), nullable=False),
        )

    # seed extra chart of accounts entry (per tenant), idempotent
    coa = sa.table(
        "chart_of_accounts",
        sa.column("id", sa.Integer),
        sa.column("tenant_id", sa.Integer),
        sa.column("code", sa.String),
        sa.column("name_ar", sa.String),
        sa.column("name_en", sa.String),
        sa.column("account_type", sa.String),
        sa.column("normal_balance", sa.String),
        sa.column("parent_id", sa.Integer),
        sa.column("is_active", sa.Boolean),
    )

    if "chart_of_accounts" in existing_tables:
        tenants = bind.execute(sa.text("SELECT id FROM tenants")).fetchall()
        for (tenant_id,) in tenants:
            existing_codes = {
                row[0] for row in bind.execute(
                    sa.text("SELECT code FROM chart_of_accounts WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                ).fetchall()
            }
            if "4000" not in existing_codes:
                continue  # chart not seeded for this tenant yet — skip

            if "4400" not in existing_codes:
                revenue_parent_id = bind.execute(
                    sa.text("SELECT id FROM chart_of_accounts WHERE tenant_id = :tid AND code = '4000'"),
                    {"tid": tenant_id},
                ).fetchone()[0]
                bind.execute(coa.insert().values(
                    tenant_id=tenant_id, code="4400", name_ar="إيرادات بيع الهالك", name_en="Scrap Sales Revenue",
                    account_type="revenue", normal_balance="credit", parent_id=revenue_parent_id, is_active=True,
                ))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = insp.get_table_names()
    if "scrap_sale_lines" in existing_tables:
        op.drop_table("scrap_sale_lines")
    if "scrap_sales" in existing_tables:
        op.drop_table("scrap_sales")
    try:
        op.drop_column("scrap_records", "qty_sold")
    except Exception:
        pass
    bind.execute(sa.text("DELETE FROM chart_of_accounts WHERE code = '4400'"))
