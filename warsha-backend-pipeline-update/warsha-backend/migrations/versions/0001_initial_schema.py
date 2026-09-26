"""Initial schema — create all tables (checkfirst=True for idempotency).

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000

This migration creates every table defined in app/models/models.py.
Using checkfirst=True means it is safe to run against an existing database
that already has some or all tables — existing tables are skipped.
"""
from __future__ import annotations
from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all ORM-mapped tables that do not yet exist."""
    from app.core.database import Base
    import app.models.models  # noqa — registers all mappers

    bind = op.get_bind()
    # checkfirst=True: silently skips any table that already exists.
    # This makes the migration safe for both fresh installs and existing DBs.
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    """Drop all tables (use with caution — irreversible)."""
    from app.core.database import Base
    import app.models.models  # noqa

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
