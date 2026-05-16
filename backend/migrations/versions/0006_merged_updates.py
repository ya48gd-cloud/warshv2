"""merged updates - attendance, payroll, and stock improvements

Revision ID: 0006
Revises: 0005
Create Date: 2024-01-06
"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. تحديث جدول العمال (إضافة الأجر اليومي)
    op.add_column('workers', sa.Column('daily_wage', sa.Numeric(12, 2), server_default='0'))
    op.execute("UPDATE workers SET daily_wage = base_weekly_wage / 6 WHERE base_weekly_wage IS NOT NULL")

    # 2. جدول الحضور والانصراف
    op.create_table('attendance',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('worker_id', sa.Integer(), sa.ForeignKey('workers.id'), nullable=False),
        sa.Column('work_date', sa.Date(), nullable=False), # تم توحيد الاسم لـ date
        sa.Column('status', sa.String(20), server_default='present'),
        sa.Column('overtime_hours', sa.Numeric(4, 1), server_default='0'),
        sa.Column('notes', sa.Text()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_attendance_worker_date', 'attendance', ['worker_id', 'work_date'], unique=True)

    # 3. جدول السلف والخصومات
    op.create_table('worker_advances',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('worker_id', sa.Integer(), sa.ForeignKey('workers.id'), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('advance_type', sa.String(20), server_default='advance'),
        sa.Column('work_date', sa.Date(), nullable=False),
        sa.Column('payroll_run_id', sa.Integer(), sa.ForeignKey('payroll_runs.id'), nullable=True),
        sa.Column('is_settled', sa.Boolean(), server_default='false'),
        sa.Column('notes', sa.Text()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # 4. تحديثات جدول حركة المخزن (Stock Movements)
    op.add_column('stock_movements', sa.Column('withdrawal_unit', sa.String(20), server_default='weight'))
    op.add_column('stock_movements', sa.Column('withdrawal_qty', sa.Numeric(14, 3)))
    op.add_column('stock_movements', sa.Column('destination', sa.String(50)))
    op.add_column('stock_movements', sa.Column('destination_ref', sa.String(100)))
    op.add_column('stock_movements', sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id'), nullable=True))
    op.add_column('stock_movements', sa.Column('pieces_count', sa.Integer()))
    op.add_column('stock_movements', sa.Column('weight_per_piece', sa.Numeric(10, 3)))

def downgrade() -> None:
    op.drop_column('stock_movements', 'weight_per_piece')
    op.drop_column('stock_movements', 'pieces_count')
    op.drop_column('stock_movements', 'customer_id')
    op.drop_column('stock_movements', 'destination_ref')
    op.drop_column('stock_movements', 'destination')
    op.drop_column('stock_movements', 'withdrawal_qty')
    op.drop_column('stock_movements', 'withdrawal_unit')
    op.drop_table('worker_advances')
    op.drop_table('attendance')
    op.drop_column('workers', 'daily_wage')