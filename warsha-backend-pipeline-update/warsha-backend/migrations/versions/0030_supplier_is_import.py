"""Supplier explicit is_import classification — محطة 21"""
from alembic import op
import sqlalchemy as sa

revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='" + table + "' AND column_name='" + col + "'"
    ))
    return result.fetchone() is not None


def upgrade():
    if not _col_exists('suppliers', 'is_import'):
        op.add_column('suppliers', sa.Column('is_import', sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column('suppliers', 'is_import')
