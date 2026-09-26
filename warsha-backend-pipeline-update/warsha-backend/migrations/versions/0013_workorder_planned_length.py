"""Work order planned length — ربط طول التصنيع بأمر الإنتاج للمعدات المترية

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-16
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='" + table + "' AND column_name='" + col + "'"))
    return result.fetchone() is not None


def upgrade() -> None:
    if not _col_exists('work_orders', 'planned_length'):
        op.add_column('work_orders',
            sa.Column('planned_length', sa.Numeric(10, 2), nullable=True))


def downgrade() -> None:
    pass
