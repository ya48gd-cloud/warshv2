"""drop native enums - use varchar instead

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-02
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old enum types if they exist (from previous runs)
    op.execute("DROP TYPE IF EXISTS movementtype CASCADE")
    op.execute("DROP TYPE IF EXISTS workorderstatus CASCADE")
    op.execute("DROP TYPE IF EXISTS journalentrytype CASCADE")


def downgrade() -> None:
    pass
