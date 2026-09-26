"""Fixed assets — جداول الأصول الثابتة والإهلاك + حسابات الدليل

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-13

Changes:
- new table fixed_assets
- new table fixed_asset_depreciations
- seed chart_of_accounts: 1400 (الأصول الثابتة), 1410 (مجمع الإهلاك), 5700 (مصروف الإهلاك)
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = insp.get_table_names()

    if "fixed_assets" not in existing_tables:
        op.create_table(
            "fixed_assets",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), index=True, nullable=False),
            sa.Column("code", sa.String(30), index=True, nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("asset_type", sa.String(30), nullable=False, server_default="equipment"),
            sa.Column("purchase_date", sa.Date, nullable=False),
            sa.Column("cost", sa.Numeric(14, 2), nullable=False),
            sa.Column("salvage_value", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("useful_life_years", sa.Integer, nullable=False, server_default="5"),
            sa.Column("accumulated_depreciation", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("location", sa.String(200), nullable=True),
            sa.Column("notes", sa.Text, nullable=True),
            sa.Column("last_depreciation_year", sa.Integer, nullable=True),
            sa.Column("last_depreciation_month", sa.Integer, nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        )

    if "fixed_asset_depreciations" not in existing_tables:
        op.create_table(
            "fixed_asset_depreciations",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), index=True, nullable=False),
            sa.Column("asset_id", sa.Integer, sa.ForeignKey("fixed_assets.id"), index=True, nullable=False),
            sa.Column("period_year", sa.Integer, nullable=False),
            sa.Column("period_month", sa.Integer, nullable=False),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("entry_id", sa.Integer, sa.ForeignKey("accounting_entries.id"), nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        )

    # seed extra chart of accounts entries (per tenant), idempotent
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
            if "1000" not in existing_codes:
                continue  # chart not seeded for this tenant yet — skip

            def get_id(code):
                row = bind.execute(
                    sa.text("SELECT id FROM chart_of_accounts WHERE tenant_id = :tid AND code = :code"),
                    {"tid": tenant_id, "code": code},
                ).fetchone()
                return row[0] if row else None

            assets_parent_id = get_id("1000")
            expenses_parent_id = get_id("5000")

            if "1400" not in existing_codes:
                bind.execute(coa.insert().values(
                    tenant_id=tenant_id, code="1400", name_ar="الأصول الثابتة", name_en="Fixed Assets",
                    account_type="asset", normal_balance="debit", parent_id=assets_parent_id, is_active=True,
                ))
                existing_codes.add("1400")

            fixed_assets_id = get_id("1400")

            if "1410" not in existing_codes:
                bind.execute(coa.insert().values(
                    tenant_id=tenant_id, code="1410", name_ar="مجمع الإهلاك", name_en="Accumulated Depreciation",
                    account_type="asset", normal_balance="credit", parent_id=fixed_assets_id, is_active=True,
                ))

            if "5700" not in existing_codes:
                bind.execute(coa.insert().values(
                    tenant_id=tenant_id, code="5700", name_ar="مصروف الإهلاك", name_en="Depreciation Expense",
                    account_type="expense", normal_balance="debit", parent_id=expenses_parent_id, is_active=True,
                ))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = insp.get_table_names()
    if "fixed_asset_depreciations" in existing_tables:
        op.drop_table("fixed_asset_depreciations")
    if "fixed_assets" in existing_tables:
        op.drop_table("fixed_assets")
    bind.execute(sa.text("DELETE FROM chart_of_accounts WHERE code IN ('1400','1410','5700')"))
