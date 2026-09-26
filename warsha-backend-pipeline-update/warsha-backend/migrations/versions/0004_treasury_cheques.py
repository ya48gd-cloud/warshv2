"""Treasury cheque tracking — add payment method and cheque fields.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-13

Changes:
- treasury_transactions: add payment_method VARCHAR(20)
- treasury_transactions: add cheque_number VARCHAR(50)
- treasury_transactions: add cheque_date DATE
- treasury_transactions: add cheque_bank VARCHAR(100)
- treasury_transactions: add cheque_status VARCHAR(20)
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table: str, col_name: str, col_type):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = [c["name"] for c in insp.get_columns(table)]
    if col_name not in existing:
        op.add_column(table, sa.Column(col_name, col_type, nullable=True))


def upgrade() -> None:
    _add_column_if_missing("treasury_transactions", "payment_method", sa.String(20))
    _add_column_if_missing("treasury_transactions", "cheque_number",  sa.String(50))
    _add_column_if_missing("treasury_transactions", "cheque_date",    sa.Date)
    _add_column_if_missing("treasury_transactions", "cheque_bank",    sa.String(100))
    _add_column_if_missing("treasury_transactions", "cheque_status",  sa.String(20))


def downgrade() -> None:
    for col in ["payment_method", "cheque_number", "cheque_date", "cheque_bank", "cheque_status"]:
        try:
            op.drop_column("treasury_transactions", col)
        except Exception:
            pass
