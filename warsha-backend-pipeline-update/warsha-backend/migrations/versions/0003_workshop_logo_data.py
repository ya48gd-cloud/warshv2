"""Workshop logo_data — store logo as base64 data:URI in DB.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-07

Changes:
- workshop_profile: add logo_data TEXT (base64 data:URI for auth-free print access)
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table: str, col_name: str, col_type):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = [c["name"] for c in insp.get_columns(table)]
    if col_name not in existing:
        op.add_column(table, sa.Column(col_name, col_type, nullable=True))


def upgrade() -> None:
    _add_column_if_missing("workshop_profile", "logo_data", sa.Text)


def downgrade() -> None:
    op.drop_column("workshop_profile", "logo_data")
