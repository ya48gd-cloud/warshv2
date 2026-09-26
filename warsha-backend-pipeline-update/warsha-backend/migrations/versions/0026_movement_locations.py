"""Movement source/destination location — محطة 12"""
from alembic import op
import sqlalchemy as sa

revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='" + table + "' AND column_name='" + col + "'"
    ))
    return result.fetchone() is not None


def upgrade():
    if not _col_exists('stock_movements', 'source_location_id'):
        op.add_column('stock_movements', sa.Column(
            'source_location_id', sa.Integer(), sa.ForeignKey('bin_locations.id'), nullable=True))
    if not _col_exists('stock_movements', 'destination_location_id'):
        op.add_column('stock_movements', sa.Column(
            'destination_location_id', sa.Integer(), sa.ForeignKey('bin_locations.id'), nullable=True))


def downgrade():
    op.drop_column('stock_movements', 'destination_location_id')
    op.drop_column('stock_movements', 'source_location_id')
