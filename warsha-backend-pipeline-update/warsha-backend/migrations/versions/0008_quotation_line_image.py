"""Quotation line image_data — store per-line image as base64 data:URI in DB.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-14

Changes:
- quotation_lines: add image_data TEXT (base64 data:URI for auth-free print access)
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table: str, col_name: str, col_type):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = [c["name"] for c in insp.get_columns(table)]
    if col_name not in existing:
        op.add_column(table, sa.Column(col_name, col_type, nullable=True))


def upgrade() -> None:
    _add_column_if_missing("quotation_lines", "image_data", sa.Text)


def downgrade() -> None:
    op.drop_column("quotation_lines", "image_data")
