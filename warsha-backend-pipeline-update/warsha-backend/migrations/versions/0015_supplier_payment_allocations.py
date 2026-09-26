"""Supplier payment allocations — مرآة لـ customer_payment_allocations

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-25

Changes:
- جدول جديد supplier_payment_allocations يربط دفعة مورد بفاتورة شراء بعينها،
  بدلاً من الاعتماد فقط على عمود paid_amount المحسوب حسابيًا بدون أثر.
- Backfill: لكل SupplierPayment له invoice_id موجود فعلاً، يُنشأ صف تخصيص
  مطابق لقيمة الدفعة بالكامل (الحالة الحالية: دفعة واحدة = فاتورة واحدة).
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "supplier_payment_allocations" not in insp.get_table_names():
        op.create_table(
            "supplier_payment_allocations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), index=True, nullable=False),
            sa.Column("payment_id", sa.Integer(), sa.ForeignKey("supplier_payments.id"), index=True, nullable=False),
            sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("purchase_invoices.id"), index=True, nullable=False),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )

    meta = sa.MetaData()
    sp = sa.Table("supplier_payments", meta, autoload_with=bind)
    spa = sa.Table("supplier_payment_allocations", meta, autoload_with=bind)

    existing_alloc_payment_ids = {row[0] for row in bind.execute(sa.select(spa.c.payment_id)).fetchall()}
    rows = bind.execute(
        sa.select(sp.c.id, sp.c.tenant_id, sp.c.invoice_id, sp.c.amount, sp.c.created_at)
        .where(sp.c.invoice_id.is_not(None))
    ).fetchall()
    for pid, tid, inv_id, amount, created_at in rows:
        if pid in existing_alloc_payment_ids:
            continue
        bind.execute(
            spa.insert().values(
                tenant_id=tid, payment_id=pid, invoice_id=inv_id, amount=amount,
                created_at=created_at,
            )
        )


def downgrade() -> None:
    op.drop_table("supplier_payment_allocations")
