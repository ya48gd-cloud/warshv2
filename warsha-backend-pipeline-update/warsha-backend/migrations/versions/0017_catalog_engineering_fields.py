"""Global Catalog — engineering fields (sheet/bar specs)"""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None

def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='" + table + "' AND column_name='" + col + "'"
    ))
    return result.fetchone() is not None


def upgrade():
    cols = [
        ('is_plate', sa.Boolean(), {'nullable': True, 'server_default': '0'}),
        ('thickness_mm', sa.Numeric(8, 2), {'nullable': True}),
        ('density_kg_m2', sa.Numeric(10, 4), {'nullable': True}),
        ('plate_length_cm', sa.Numeric(10, 2), {'nullable': True}),
        ('plate_width_cm', sa.Numeric(10, 2), {'nullable': True}),
        ('plate_weight_kg', sa.Numeric(10, 3), {'nullable': True}),
        ('bar_length_cm', sa.Numeric(10, 2), {'nullable': True}),
        ('bar_weight_kg', sa.Numeric(10, 3), {'nullable': True}),
        ('weight_per_meter_kg', sa.Numeric(10, 4), {'nullable': True}),
    ]
    for name, coltype, kwargs in cols:
        if not _col_exists('global_catalog', name):
            op.add_column('global_catalog', sa.Column(name, coltype, **kwargs))

def downgrade():
    for col in ['is_plate','thickness_mm','density_kg_m2','plate_length_cm','plate_width_cm',
                'plate_weight_kg','bar_length_cm','bar_weight_kg','weight_per_meter_kg']:
        op.drop_column('global_catalog', col)
