"""Material policies — stock_item_type, tracking, valuation_method, removal_strategy — محطة 9"""
from alembic import op
import sqlalchemy as sa

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='" + table + "' AND column_name='" + col + "'"
    ))
    return result.fetchone() is not None


def upgrade():
    if not _col_exists('materials', 'stock_item_type'):
        op.add_column('materials', sa.Column(
            'stock_item_type', sa.String(30), nullable=False, server_default='raw_material'))
    if not _col_exists('materials', 'tracking'):
        op.add_column('materials', sa.Column(
            'tracking', sa.String(10), nullable=False, server_default='none'))
    if not _col_exists('materials', 'valuation_method'):
        op.add_column('materials', sa.Column(
            'valuation_method', sa.String(20), nullable=False, server_default='moving_average'))
    if not _col_exists('materials', 'removal_strategy'):
        op.add_column('materials', sa.Column(
            'removal_strategy', sa.String(10), nullable=False, server_default='fifo'))


def downgrade():
    op.drop_column('materials', 'removal_strategy')
    op.drop_column('materials', 'valuation_method')
    op.drop_column('materials', 'tracking')
    op.drop_column('materials', 'stock_item_type')
