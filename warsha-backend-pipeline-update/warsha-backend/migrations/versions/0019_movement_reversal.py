"""Stock movement reversal tracking — state + reversed_movement_id"""
from alembic import op
import sqlalchemy as sa

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='" + table + "' AND column_name='" + col + "'"
    ))
    return result.fetchone() is not None


def upgrade():
    if not _col_exists('stock_movements', 'state'):
        op.add_column(
            'stock_movements',
            sa.Column('state', sa.String(20), nullable=False, server_default='posted'),
        )
    if not _col_exists('stock_movements', 'reversed_movement_id'):
        op.add_column(
            'stock_movements',
            sa.Column('reversed_movement_id', sa.Integer(),
                      sa.ForeignKey('stock_movements.id'), nullable=True),
        )


def downgrade():
    op.drop_column('stock_movements', 'reversed_movement_id')
    op.drop_column('stock_movements', 'state')
