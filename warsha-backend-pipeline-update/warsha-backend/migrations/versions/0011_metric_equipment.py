"""Metric equipment costing — تسعير المعدات المترية

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-15

Changes:
- add columns equipment.length_min, equipment.length_max (نطاق طول الصنف)
- add columns bom_lines.line_type (fixed|variable), bom_lines.scale_dim_length
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    equipment_columns = [c["name"] for c in insp.get_columns("equipment")]
    if "length_min" not in equipment_columns:
        op.add_column("equipment", sa.Column("length_min", sa.Numeric(10, 2), nullable=True))
    if "length_max" not in equipment_columns:
        op.add_column("equipment", sa.Column("length_max", sa.Numeric(10, 2), nullable=True))

    bom_columns = [c["name"] for c in insp.get_columns("bom_lines")]
    if "line_type" not in bom_columns:
        op.add_column("bom_lines", sa.Column("line_type", sa.String(10), nullable=False, server_default="fixed"))
    if "scale_dim_length" not in bom_columns:
        op.add_column("bom_lines", sa.Column("scale_dim_length", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    bom_columns = [c["name"] for c in insp.get_columns("bom_lines")]
    if "scale_dim_length" in bom_columns:
        op.drop_column("bom_lines", "scale_dim_length")
    if "line_type" in bom_columns:
        op.drop_column("bom_lines", "line_type")

    equipment_columns = [c["name"] for c in insp.get_columns("equipment")]
    if "length_max" in equipment_columns:
        op.drop_column("equipment", "length_max")
    if "length_min" in equipment_columns:
        op.drop_column("equipment", "length_min")
