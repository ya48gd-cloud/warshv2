"""Delivery order line ↔ equipment/work_order link — محطة 16"""
from alembic import op
import sqlalchemy as sa

revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='" + table + "' AND column_name='" + col + "'"
    ))
    return result.fetchone() is not None


def upgrade():
    if not _col_exists('delivery_order_lines', 'equipment_id'):
        op.add_column('delivery_order_lines', sa.Column(
            'equipment_id', sa.Integer(), sa.ForeignKey('equipment.id'), nullable=True))
    if not _col_exists('delivery_order_lines', 'work_order_id'):
        op.add_column('delivery_order_lines', sa.Column(
            'work_order_id', sa.Integer(), sa.ForeignKey('work_orders.id'), nullable=True))


def downgrade():
    op.drop_column('delivery_order_lines', 'work_order_id')
    op.drop_column('delivery_order_lines', 'equipment_id')
