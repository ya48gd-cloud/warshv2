"""initial schema

Revision ID: 0001
Revises:
Create Date: 2024-01-01
"""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('material_categories',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name_ar', sa.String(120), nullable=False),
        sa.Column('name_en', sa.String(120), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table('materials',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(50), unique=True, nullable=False, index=True),
        sa.Column('name_ar', sa.String(200), nullable=False),
        sa.Column('name_en', sa.String(200), nullable=False),
        sa.Column('unit', sa.String(30), nullable=False),
        sa.Column('unit_cost', sa.Numeric(14, 2), nullable=False),
        sa.Column('stock_qty', sa.Numeric(14, 3), server_default='0'),
        sa.Column('reorder_level', sa.Numeric(14, 3), server_default='0'),
        sa.Column('category_id', sa.Integer(), sa.ForeignKey('material_categories.id')),
        sa.Column('supplier', sa.String(200)),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table('stock_movements',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('material_id', sa.Integer(), sa.ForeignKey('materials.id'), nullable=False),
        sa.Column('movement_type', sa.String(10), nullable=False),   # "in" | "out"
        sa.Column('qty', sa.Numeric(14, 3), nullable=False),
        sa.Column('unit_cost', sa.Numeric(14, 2), nullable=False),
        sa.Column('total_cost', sa.Numeric(14, 2), nullable=False),
        sa.Column('reference', sa.String(100)),
        sa.Column('notes', sa.Text()),
        sa.Column('movement_date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table('equipment',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(50), unique=True, nullable=False, index=True),
        sa.Column('name_ar', sa.String(200), nullable=False),
        sa.Column('name_en', sa.String(200), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('equipment.id'), nullable=True),
        sa.Column('level', sa.Integer(), server_default='0'),
        sa.Column('weight_kg', sa.Numeric(10, 3)),
        sa.Column('cad_drawing_no', sa.String(100)),
        sa.Column('cad_file_path', sa.String(500)),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table('equipment_dimensions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('equipment_id', sa.Integer(), sa.ForeignKey('equipment.id'), nullable=False),
        sa.Column('dim_key', sa.String(80), nullable=False),
        sa.Column('dim_value', sa.String(80), nullable=False),
        sa.Column('unit', sa.String(20), nullable=False),
    )
    op.create_table('work_orders',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(50), unique=True, nullable=False),
        sa.Column('equipment_id', sa.Integer(), sa.ForeignKey('equipment.id'), nullable=False),
        sa.Column('status', sa.String(20), server_default='draft'),
        sa.Column('planned_cost', sa.Numeric(16, 2), server_default='0'),
        sa.Column('actual_cost', sa.Numeric(16, 2), server_default='0'),
        sa.Column('start_date', sa.Date()),
        sa.Column('end_date', sa.Date()),
        sa.Column('notes', sa.Text()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table('cost_lines',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('work_order_id', sa.Integer(), sa.ForeignKey('work_orders.id'), nullable=False),
        sa.Column('cost_type', sa.String(50), nullable=False),
        sa.Column('description', sa.String(300), nullable=False),
        sa.Column('qty', sa.Numeric(14, 3), server_default='1'),
        sa.Column('unit_cost', sa.Numeric(14, 2), nullable=False),
        sa.Column('total_cost', sa.Numeric(14, 2), nullable=False),
        sa.Column('material_id', sa.Integer(), sa.ForeignKey('materials.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table('journal_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('work_order_id', sa.Integer(), sa.ForeignKey('work_orders.id'), nullable=True),
        sa.Column('entry_type', sa.String(50), nullable=False),
        sa.Column('debit_account', sa.String(100), nullable=False),
        sa.Column('credit_account', sa.String(100), nullable=False),
        sa.Column('amount', sa.Numeric(16, 2), nullable=False),
        sa.Column('description', sa.String(300), nullable=False),
        sa.Column('entry_date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table('bom_lines',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('equipment_id', sa.Integer(), sa.ForeignKey('equipment.id'), nullable=False),
        sa.Column('material_id', sa.Integer(), sa.ForeignKey('materials.id'), nullable=False),
        sa.Column('qty', sa.Numeric(14, 3), nullable=False),
        sa.Column('unit_cost', sa.Numeric(14, 2), nullable=False),
        sa.Column('total_cost', sa.Numeric(14, 2), nullable=False),
        sa.Column('notes', sa.String(300)),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('bom_lines')
    op.drop_table('journal_entries')
    op.drop_table('cost_lines')
    op.drop_table('work_orders')
    op.drop_table('equipment_dimensions')
    op.drop_table('equipment')
    op.drop_table('stock_movements')
    op.drop_table('materials')
    op.drop_table('material_categories')
