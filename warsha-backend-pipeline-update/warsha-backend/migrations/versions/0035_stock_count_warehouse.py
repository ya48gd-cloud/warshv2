"""Stock count per warehouse — stock_counts.warehouse_id (NULL = legacy all-warehouse count)"""
from alembic import op
import sqlalchemy as sa

revision = '0035'
down_revision = '0034'
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='" + table + "' AND column_name='" + col + "'"
    ))
    return result.fetchone() is not None


def upgrade():
    if not _col_exists('stock_counts', 'warehouse_id'):
        op.add_column('stock_counts', sa.Column(
            'warehouse_id', sa.Integer(), sa.ForeignKey('warehouses.id'), nullable=True))
        op.create_index('ix_stock_counts_warehouse_id', 'stock_counts', ['warehouse_id'])


def downgrade():
    op.drop_index('ix_stock_counts_warehouse_id', table_name='stock_counts')
    op.drop_column('stock_counts', 'warehouse_id')
