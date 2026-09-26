"""Tax accounts — حسابات الضرائب في دليل الحسابات

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-15

Changes:
- لكل tenant لديه دليل حسابات مزروع، أضف (إن لم تكن موجودة):
  1230 ضريبة القيمة المضافة على المشتريات (مدخلات) — أصل تحت 1200
  2300 ضرائب مستحقة — خصم تحت 2000
  2310 ضريبة القيمة المضافة المستحقة (مخرجات) — خصم تحت 2300
  2320 ضريبة الخصم تحت الحساب المستحقة — خصم تحت 2300
  2330 ضريبة الدخل المستحقة (تقديرية) — خصم تحت 2300
  5800 ضريبة الدخل — مصروف تحت 5000
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (code, name_ar, parent_code, account_type, normal_balance)
NEW_ACCOUNTS = [
    ("1230", "ضريبة القيمة المضافة على المشتريات (مدخلات)", "1200", "asset", "debit"),
    ("2300", "ضرائب مستحقة", "2000", "liability", "credit"),
    ("2310", "ضريبة القيمة المضافة المستحقة (مخرجات)", "2300", "liability", "credit"),
    ("2320", "ضريبة الخصم تحت الحساب المستحقة", "2300", "liability", "credit"),
    ("2330", "ضريبة الدخل المستحقة (تقديرية)", "2300", "liability", "credit"),
    ("5800", "ضريبة الدخل", "5000", "expense", "debit"),
]


def upgrade() -> None:
    bind = op.get_bind()
    meta = sa.MetaData()
    coa = sa.Table("chart_of_accounts", meta, autoload_with=bind)

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


def downgrade() -> None:
    bind = op.get_bind()
    codes = tuple(c[0] for c in NEW_ACCOUNTS)
    bind.execute(
        sa.text("DELETE FROM chart_of_accounts WHERE code IN :codes").bindparams(
            sa.bindparam("codes", expanding=True)
        ),
        {"codes": list(codes)},
    )
