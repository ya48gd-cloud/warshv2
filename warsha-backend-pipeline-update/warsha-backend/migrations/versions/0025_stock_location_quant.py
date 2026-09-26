"""BinLocation upgrade toward StockLocation + StockQuant — محطة 11"""
from alembic import op
import sqlalchemy as sa

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = insp.get_table_names()
    existing_bin_cols = {c['name'] for c in insp.get_columns('bin_locations')} if 'bin_locations' in existing_tables else set()

    if 'warehouse_id' not in existing_bin_cols:
        op.add_column('bin_locations', sa.Column(
            'warehouse_id', sa.Integer(), sa.ForeignKey('warehouses.id'), nullable=True))
    if 'parent_id' not in existing_bin_cols:
        op.add_column('bin_locations', sa.Column(
            'parent_id', sa.Integer(), sa.ForeignKey('bin_locations.id'), nullable=True))
    if 'location_type' not in existing_bin_cols:
        op.add_column('bin_locations', sa.Column(
            'location_type', sa.String(20), nullable=False, server_default='physical'))

    if 'stock_quants' not in existing_tables:
        op.create_table(
            'stock_quants',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
            sa.Column('material_id', sa.Integer(), sa.ForeignKey('materials.id'), nullable=False),
            sa.Column('location_id', sa.Integer(), sa.ForeignKey('bin_locations.id'), nullable=False),
            sa.Column('lot_id', sa.Integer(), sa.ForeignKey('stock_batches.id'), nullable=True),
            sa.Column('qty', sa.Numeric(14, 3), nullable=False, server_default='0'),
            sa.Column('reserved_qty', sa.Numeric(14, 3), nullable=False, server_default='0'),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint('material_id', 'location_id', 'lot_id', name='uq_quant_material_location_lot'),
        )
        op.create_index('ix_stock_quants_tenant_id', 'stock_quants', ['tenant_id'])
        op.create_index('ix_stock_quants_material_id', 'stock_quants', ['material_id'])
        op.create_index('ix_stock_quants_location_id', 'stock_quants', ['location_id'])


def downgrade():
    op.drop_index('ix_stock_quants_location_id', table_name='stock_quants')
    op.drop_index('ix_stock_quants_material_id', table_name='stock_quants')
    op.drop_index('ix_stock_quants_tenant_id', table_name='stock_quants')
    op.drop_table('stock_quants')
    op.drop_column('bin_locations', 'location_type')
    op.drop_column('bin_locations', 'parent_id')
    op.drop_column('bin_locations', 'warehouse_id')
