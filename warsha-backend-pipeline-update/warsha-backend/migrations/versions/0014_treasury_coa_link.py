"""Treasury <-> Chart of Accounts linkage

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-25

Changes:
- إضافة حساب 1220 "سلف عاملين" (asset/debit) تحت 1200 لكل tenant.
- إضافة عمود coa_account_id (FK nullable) لـ treasuries — تعيين تلقائي:
  خزينة kind='cash' -> حساب 1110، خزينة kind='bank' -> حساب 1120.
- إضافة عمود coa_account_id (FK nullable) لـ treasury_categories — بدون تعيين تلقائي
  (المستخدم يحددها لاحقًا من شاشة الإعدادات؛ أي فئة بلا حساب تترحّل احتياطيًا لحساب 5500).
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_ACCOUNTS = [
    ("1220", "سلف عاملين", "1200", "asset", "debit"),
]


def upgrade() -> None:
    bind = op.get_bind()
    meta = sa.MetaData()
    coa = sa.Table("chart_of_accounts", meta, autoload_with=bind)

    insp = sa.inspect(bind)
    treas_cols = [c["name"] for c in insp.get_columns("treasuries")]
    cat_cols = [c["name"] for c in insp.get_columns("treasury_categories")]

    if "coa_account_id" not in treas_cols:
        op.add_column("treasuries", sa.Column("coa_account_id", sa.Integer(), nullable=True))
    if "coa_account_id" not in cat_cols:
        op.add_column("treasury_categories", sa.Column("coa_account_id", sa.Integer(), nullable=True))

    treasuries = sa.Table("treasuries", meta, autoload_with=bind)

    tenant_ids = [row[0] for row in bind.execute(
        sa.text("SELECT DISTINCT tenant_id FROM chart_of_accounts")
    ).fetchall()]

    for tid in tenant_ids:
        existing_rows = bind.execute(
            sa.select(coa.c.id, coa.c.code).where(coa.c.tenant_id == tid)
        ).fetchall()
        code_to_id = {code: id_ for id_, code in existing_rows}

        for code, name_ar, parent_code, acc_type, normal_bal in NEW_ACCOUNTS:
            if code in code_to_id:
                continue
            parent_id = code_to_id.get(parent_code)
            result = bind.execute(
                coa.insert().values(
                    tenant_id=tid, code=code, name_ar=name_ar,
                    account_type=acc_type, normal_balance=normal_bal,
                    parent_id=parent_id, is_active=True,
                )
            )
            new_id = result.inserted_primary_key[0]
            code_to_id[code] = new_id

        cash_acc_id = code_to_id.get("1110")
        bank_acc_id = code_to_id.get("1120")

        if cash_acc_id:
            bind.execute(
                treasuries.update()
                .where(treasuries.c.tenant_id == tid, treasuries.c.kind == "cash",
                       treasuries.c.coa_account_id.is_(None))
                .values(coa_account_id=cash_acc_id)
            )
        if bank_acc_id:
            bind.execute(
                treasuries.update()
                .where(treasuries.c.tenant_id == tid, treasuries.c.kind == "bank",
                       treasuries.c.coa_account_id.is_(None))
                .values(coa_account_id=bank_acc_id)
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    treas_cols = [c["name"] for c in insp.get_columns("treasuries")]
    cat_cols = [c["name"] for c in insp.get_columns("treasury_categories")]
    if "coa_account_id" in treas_cols:
        op.drop_column("treasuries", "coa_account_id")
    if "coa_account_id" in cat_cols:
        op.drop_column("treasury_categories", "coa_account_id")
    bind.execute(
        sa.text("DELETE FROM chart_of_accounts WHERE code IN :codes").bindparams(
            sa.bindparam("codes", expanding=True)
        ),
        {"codes": [c[0] for c in NEW_ACCOUNTS]},
    )
