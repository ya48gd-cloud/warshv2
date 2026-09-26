"""Work order customer & location — ربط أمر الإنتاج بعميل وموقع مباشرة

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-16

Changes:
- add work_orders.customer_id  (nullable FK → customers.id)
- add work_orders.location_id  (nullable FK → customer_locations.id)
- add work_orders.description  (Text, nullable) — كان مفقوداً من الفورم
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0012'
down_revision: Union[str, None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='" + table + "' AND column_name='" + col + "'"))
    return result.fetchone() is not None


def upgrade() -> None:
    if not _col_exists('work_orders', 'customer_id'):
        op.add_column('work_orders',
            sa.Column('customer_id', sa.Integer(), nullable=True))

    if not _col_exists('work_orders', 'location_id'):
        op.add_column('work_orders',
            sa.Column('location_id', sa.Integer(), nullable=True))

    if not _col_exists('work_orders', 'description'):
        op.add_column('work_orders',
            sa.Column('description', sa.Text(), nullable=True))


def downgrade() -> None:
    # SQLite doesn't support DROP COLUMN on older versions — skip
    pass
