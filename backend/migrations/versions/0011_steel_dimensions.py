"""steel_dimensions

Revision ID: 0011
Revises: 0010
Create Date: 2025-05-13

Adds steel/plate tracking fields:
  - materials: density_kg_m2, thickness_mm  (for steel/plate materials)
  - stock_movements: plate_length_cm, plate_width_cm, plate_area_m2  (for plate-based IN movements)
               plate_pieces (for multiple-plate OUT)
  - bom_lines: dim_length_cm, dim_width_cm, dim_count, dim_area_m2, calc_weight_kg (for plate-based BOM)
"""
from alembic import op
import sqlalchemy as sa

revision      = '0011'
down_revision = '0010'
branch_labels = None
depends_on    = None


def upgrade():
    # ── Material: steel/plate properties ─────────────────────────
    with op.batch_alter_table('materials') as batch:
        batch.add_column(sa.Column('density_kg_m2',  sa.Numeric(10, 4), nullable=True))
        batch.add_column(sa.Column('thickness_mm',   sa.Numeric(8, 2),  nullable=True))
        batch.add_column(sa.Column('is_plate',       sa.Boolean(), server_default='false'))

    # ── StockMovement: plate IN dimensions ───────────────────────
    with op.batch_alter_table('stock_movements') as batch:
        batch.add_column(sa.Column('plate_length_cm', sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column('plate_width_cm',  sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column('plate_area_m2',   sa.Numeric(10, 4), nullable=True))
        batch.add_column(sa.Column('plate_weight_kg', sa.Numeric(10, 3), nullable=True))

    # ── BOMLine: plate dimensions per line ────────────────────────
    with op.batch_alter_table('bom_lines') as batch:
        batch.add_column(sa.Column('dim_length_cm',  sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column('dim_width_cm',   sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column('dim_count',      sa.Integer(),       nullable=True))
        batch.add_column(sa.Column('dim_area_m2',    sa.Numeric(10, 4), nullable=True))
        batch.add_column(sa.Column('calc_weight_kg', sa.Numeric(10, 3), nullable=True))


def downgrade():
    with op.batch_alter_table('materials') as batch:
        batch.drop_column('is_plate')
        batch.drop_column('thickness_mm')
        batch.drop_column('density_kg_m2')

    with op.batch_alter_table('stock_movements') as batch:
        batch.drop_column('plate_weight_kg')
        batch.drop_column('plate_area_m2')
        batch.drop_column('plate_width_cm')
        batch.drop_column('plate_length_cm')

    with op.batch_alter_table('bom_lines') as batch:
        batch.drop_column('calc_weight_kg')
        batch.drop_column('dim_area_m2')
        batch.drop_column('dim_count')
        batch.drop_column('dim_width_cm')
        batch.drop_column('dim_length_cm')
