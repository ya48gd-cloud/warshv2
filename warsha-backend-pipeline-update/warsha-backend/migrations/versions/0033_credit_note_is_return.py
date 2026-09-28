"""Credit note is_return flag (Sales Return) — restocks FG inventory + reverses COGS on issue"""
from alembic import op
import sqlalchemy as sa

revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='" + table + "' AND column_name='" + col + "'"
    ))
    return result.fetchone() is not None


def upgrade():
    if not _col_exists('credit_notes', 'is_return'):
        op.add_column('credit_notes', sa.Column(
            'is_return', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('credit_notes', 'is_return')
