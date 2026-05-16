"""add workers, payroll, customers, orders

Revision ID: 0004
Revises: 0003
Create Date: 2024-01-04
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ── جدول العاملين ─────────────────────────────────────────
    op.create_table('workers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(30), unique=True, nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('job_title', sa.String(100)),
        sa.Column('phone', sa.String(30)),
        sa.Column('national_id', sa.String(30)),
        sa.Column('hire_date', sa.Date()),
        sa.Column('base_weekly_wage', sa.Numeric(12,2), nullable=False),  # الأجر الأساسي الأسبوعي
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('notes', sa.Text()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # ── كشف الرواتب الأسبوعي ──────────────────────────────────
    op.create_table('payroll_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('week_start', sa.Date(), nullable=False),
        sa.Column('week_end', sa.Date(), nullable=False),
        sa.Column('total_gross', sa.Numeric(14,2), server_default='0'),
        sa.Column('total_deductions', sa.Numeric(14,2), server_default='0'),
        sa.Column('total_net', sa.Numeric(14,2), server_default='0'),
        sa.Column('status', sa.String(20), server_default='draft'),  # draft | paid
        sa.Column('paid_date', sa.Date()),
        sa.Column('notes', sa.Text()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # ── سطور الرواتب (لكل عامل في كشف) ─────────────────────
    op.create_table('payroll_lines',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('payroll_run_id', sa.Integer(), sa.ForeignKey('payroll_runs.id'), nullable=False),
        sa.Column('worker_id', sa.Integer(), sa.ForeignKey('workers.id'), nullable=False),
        sa.Column('days_worked', sa.Numeric(4,1), server_default='6'),
        sa.Column('gross_amount', sa.Numeric(12,2), nullable=False),   # الإجمالي
        sa.Column('deductions', sa.Numeric(12,2), server_default='0'), # خصومات
        sa.Column('bonus', sa.Numeric(12,2), server_default='0'),      # بدلات/حوافز
        sa.Column('net_amount', sa.Numeric(12,2), nullable=False),     # الصافي
        sa.Column('notes', sa.Text()),
    )
    op.create_index('ix_payroll_lines_run', 'payroll_lines', ['payroll_run_id'])
    op.create_index('ix_payroll_lines_worker', 'payroll_lines', ['worker_id'])

    # ── جدول العملاء ──────────────────────────────────────────
    op.create_table('customers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(30), unique=True, nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('phone', sa.String(30)),
        sa.Column('address', sa.Text()),
        sa.Column('tax_id', sa.String(50)),
        sa.Column('credit_limit', sa.Numeric(14,2), server_default='0'),
        sa.Column('balance', sa.Numeric(14,2), server_default='0'),    # الرصيد (موجب = له، سالب = عليه)
        sa.Column('notes', sa.Text()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # ── طلبات العملاء (orders) ─────────────────────────────────
    op.create_table('customer_orders',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(50), unique=True, nullable=False),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id'), nullable=False),
        sa.Column('equipment_id', sa.Integer(), sa.ForeignKey('equipment.id'), nullable=True),
        sa.Column('description', sa.Text()),
        sa.Column('quantity', sa.Integer(), server_default='1'),
        sa.Column('unit_price', sa.Numeric(14,2), server_default='0'),
        sa.Column('total_price', sa.Numeric(14,2), server_default='0'),
        sa.Column('status', sa.String(20), server_default='pending'),  # pending|in_progress|delivered|cancelled
        sa.Column('order_date', sa.Date()),
        sa.Column('delivery_date', sa.Date()),
        sa.Column('notes', sa.Text()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_orders_customer', 'customer_orders', ['customer_id'])

    # ── مدفوعات العملاء ───────────────────────────────────────
    op.create_table('customer_payments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id'), nullable=False),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('customer_orders.id'), nullable=True),
        sa.Column('amount', sa.Numeric(14,2), nullable=False),
        sa.Column('payment_type', sa.String(20), server_default='cash'),  # cash|bank|cheque
        sa.Column('payment_date', sa.Date(), nullable=False),
        sa.Column('reference', sa.String(100)),
        sa.Column('notes', sa.Text()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_payments_customer', 'customer_payments', ['customer_id'])


def downgrade() -> None:
    op.drop_table('customer_payments')
    op.drop_table('customer_orders')
    op.drop_table('customers')
    op.drop_table('payroll_lines')
    op.drop_table('payroll_runs')
    op.drop_table('workers')
