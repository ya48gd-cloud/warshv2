"""inventory enhanced - bin locations, reservations, scrap, mrp, barcode

Revision ID: 0009
Revises: 0008
Create Date: 2024-01-09
"""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('bin_locations',
        sa.Column('id',          sa.Integer(), primary_key=True),
        sa.Column('code',        sa.String(30), unique=True, nullable=False),
        sa.Column('name_ar',     sa.String(100)),
        sa.Column('zone',        sa.String(20)),
        sa.Column('shelf',       sa.String(10)),
        sa.Column('bin',         sa.String(10)),
        sa.Column('capacity_kg', sa.Numeric(10,2)),
        sa.Column('is_active',   sa.Boolean(), server_default='true'),
        sa.Column('notes',       sa.Text()),
    )
    op.create_table('material_bin_stock',
        sa.Column('id',          sa.Integer(), primary_key=True),
        sa.Column('material_id', sa.Integer(), sa.ForeignKey('materials.id'), nullable=False),
        sa.Column('bin_id',      sa.Integer(), sa.ForeignKey('bin_locations.id'), nullable=False),
        sa.Column('qty',         sa.Numeric(14,3), server_default='0'),
        sa.UniqueConstraint('material_id', 'bin_id', name='uq_mat_bin'),
    )
    op.create_table('material_reservations',
        sa.Column('id',             sa.Integer(), primary_key=True),
        sa.Column('work_order_id',  sa.Integer(), sa.ForeignKey('work_orders.id'), nullable=False),
        sa.Column('material_id',    sa.Integer(), sa.ForeignKey('materials.id'), nullable=False),
        sa.Column('required_qty',   sa.Numeric(14,3), nullable=False),
        sa.Column('reserved_qty',   sa.Numeric(14,3), server_default='0'),
        sa.Column('issued_qty',     sa.Numeric(14,3), server_default='0'),
        sa.Column('returned_qty',   sa.Numeric(14,3), server_default='0'),
        sa.Column('scrap_qty',      sa.Numeric(14,3), server_default='0'),
        sa.Column('status',         sa.String(20), server_default='pending'),
        sa.Column('reserved_at',    sa.DateTime()),
        sa.Column('notes',          sa.Text()),
    )
    op.create_index('ix_res_wo',  'material_reservations', ['work_order_id'])
    op.create_index('ix_res_mat', 'material_reservations', ['material_id'])
    op.create_table('scrap_records',
        sa.Column('id',             sa.Integer(), primary_key=True),
        sa.Column('work_order_id',  sa.Integer(), sa.ForeignKey('work_orders.id')),
        sa.Column('material_id',    sa.Integer(), sa.ForeignKey('materials.id')),
        sa.Column('qty',            sa.Numeric(14,3), nullable=False),
        sa.Column('unit_cost',      sa.Numeric(14,2)),
        sa.Column('total_cost',     sa.Numeric(14,2)),
        sa.Column('reason',         sa.String(200)),
        sa.Column('stage',          sa.String(100)),
        sa.Column('recorded_at',    sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table('mrp_suggestions',
        sa.Column('id',             sa.Integer(), primary_key=True),
        sa.Column('material_id',    sa.Integer(), sa.ForeignKey('materials.id')),
        sa.Column('current_stock',  sa.Numeric(14,3)),
        sa.Column('required_qty',   sa.Numeric(14,3)),
        sa.Column('suggested_qty',  sa.Numeric(14,3)),
        sa.Column('estimated_cost', sa.Numeric(14,2)),
        sa.Column('order_by_date',  sa.Date()),
        sa.Column('expected_date',  sa.Date()),
        sa.Column('status',         sa.String(20), server_default='pending'),
        sa.Column('generated_at',   sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table('stock_counts',
        sa.Column('id',         sa.Integer(), primary_key=True),
        sa.Column('count_date', sa.Date(), nullable=False),
        sa.Column('status',     sa.String(20), server_default='in_progress'),
        sa.Column('notes',      sa.Text()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table('stock_count_lines',
        sa.Column('id',          sa.Integer(), primary_key=True),
        sa.Column('count_id',    sa.Integer(), sa.ForeignKey('stock_counts.id')),
        sa.Column('material_id', sa.Integer(), sa.ForeignKey('materials.id')),
        sa.Column('system_qty',  sa.Numeric(14,3)),
        sa.Column('actual_qty',  sa.Numeric(14,3)),
        sa.Column('difference',  sa.Numeric(14,3)),
        sa.Column('scanned_at',  sa.DateTime()),
    )
    # Add columns to existing tables
    op.add_column('materials', sa.Column('barcode',             sa.String(50)))
    op.add_column('materials', sa.Column('lead_time_days',      sa.Integer(), server_default='7'))
    op.add_column('materials', sa.Column('min_order_qty',       sa.Numeric(14,3)))
    op.add_column('materials', sa.Column('last_purchase_price', sa.Numeric(14,2)))
    op.add_column('equipment',   sa.Column('in_stock', sa.Boolean(), server_default='false'))
    op.add_column('work_orders', sa.Column('customer_order_id', sa.Integer(),
                  sa.ForeignKey('customer_orders.id'), nullable=True))


def downgrade() -> None:
    op.drop_column('work_orders', 'customer_order_id')
    op.drop_column('equipment', 'in_stock')
    op.drop_column('materials', 'last_purchase_price')
    op.drop_column('materials', 'min_order_qty')
    op.drop_column('materials', 'lead_time_days')
    op.drop_column('materials', 'barcode')
    op.drop_table('stock_count_lines')
    op.drop_table('stock_counts')
    op.drop_table('mrp_suggestions')
    op.drop_table('scrap_records')
    op.drop_table('material_reservations')
    op.drop_table('material_bin_stock')
    op.drop_table('bin_locations')
