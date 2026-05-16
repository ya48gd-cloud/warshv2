"""material_shapes

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("materials") as batch:
        batch.add_column(sa.Column("material_kind", sa.String(20), nullable=False, server_default="general"))
        batch.add_column(sa.Column("plate_length_cm", sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column("plate_width_cm", sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column("plate_weight_kg", sa.Numeric(10, 3), nullable=True))
        batch.add_column(sa.Column("bar_length_cm", sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column("bar_weight_kg", sa.Numeric(10, 3), nullable=True))
        batch.add_column(sa.Column("weight_per_meter_kg", sa.Numeric(10, 4), nullable=True))

    with op.batch_alter_table("stock_movements") as batch:
        batch.add_column(sa.Column("bar_length_cm", sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column("waste_g", sa.Numeric(10, 2), nullable=True))

    with op.batch_alter_table("bom_lines") as batch:
        batch.add_column(sa.Column("bar_length_cm", sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column("waste_g", sa.Numeric(10, 2), nullable=True))


def downgrade():
    with op.batch_alter_table("bom_lines") as batch:
        batch.drop_column("waste_g")
        batch.drop_column("bar_length_cm")

    with op.batch_alter_table("stock_movements") as batch:
        batch.drop_column("waste_g")
        batch.drop_column("bar_length_cm")

    with op.batch_alter_table("materials") as batch:
        batch.drop_column("weight_per_meter_kg")
        batch.drop_column("bar_weight_kg")
        batch.drop_column("bar_length_cm")
        batch.drop_column("plate_weight_kg")
        batch.drop_column("plate_width_cm")
        batch.drop_column("plate_length_cm")
        batch.drop_column("material_kind")
