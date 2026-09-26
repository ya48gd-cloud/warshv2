"""Material reservation location tagging — محطة 16 (الروادماب الأصلي)"""
from alembic import op
import sqlalchemy as sa

revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='" + table + "' AND column_name='" + col + "'"
    ))
    return result.fetchone() is not None


def upgrade():
    if not _col_exists('material_reservations', 'location_id'):
        op.add_column('material_reservations', sa.Column(
            'location_id', sa.Integer(), sa.ForeignKey('bin_locations.id'), nullable=True))


def downgrade():
    op.drop_column('material_reservations', 'location_id')
