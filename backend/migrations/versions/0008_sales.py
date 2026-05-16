"""sales - quotations and invoices

Revision ID: 0008
Revises: 0007
Create Date: 2024-01-08
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── عروض الأسعار ─────────────────────────────────────────
    op.create_table('quotations',
        sa.Column('id',           sa.Integer(), primary_key=True),
        sa.Column('code',         sa.String(50), unique=True, nullable=False),
        sa.Column('customer_id',  sa.Integer(), sa.ForeignKey('customers.id'), nullable=False),
        sa.Column('date',         sa.Date(), nullable=False),
        sa.Column('valid_until',  sa.Date()),
        sa.Column('status',       sa.String(20), server_default='draft'),  # draft|sent|accepted|rejected
        sa.Column('subtotal',     sa.Numeric(14,2), server_default='0'),
        sa.Column('tax_pct',      sa.Numeric(5,2),  server_default='0'),   # نسبة الضريبة
        sa.Column('tax_amount',   sa.Numeric(14,2), server_default='0'),
        sa.Column('discount_pct', sa.Numeric(5,2),  server_default='0'),
        sa.Column('discount_amt', sa.Numeric(14,2), server_default='0'),
        sa.Column('total',        sa.Numeric(14,2), server_default='0'),
        sa.Column('notes',        sa.Text()),
        sa.Column('terms',        sa.Text()),      # شروط الدفع
        sa.Column('created_at',   sa.DateTime(), server_default=sa.func.now()),
    )
    # ── سطور عروض الأسعار ────────────────────────────────────
    op.create_table('quotation_lines',
        sa.Column('id',           sa.Integer(), primary_key=True),
        sa.Column('quotation_id', sa.Integer(), sa.ForeignKey('quotations.id'), nullable=False),
        sa.Column('description',  sa.String(500), nullable=False),
        sa.Column('qty',          sa.Numeric(14,3), server_default='1'),
        sa.Column('unit',         sa.String(20),  server_default='pcs'),
        sa.Column('unit_price',   sa.Numeric(14,2), nullable=False),
        sa.Column('total_price',  sa.Numeric(14,2), nullable=False),
        sa.Column('notes',        sa.Text()),
        sa.Column('sort_order',   sa.Integer(), server_default='0'),
    )
    op.create_index('ix_quot_lines_quot', 'quotation_lines', ['quotation_id'])

    # ── الفواتير ─────────────────────────────────────────────
    op.create_table('invoices',
        sa.Column('id',           sa.Integer(), primary_key=True),
        sa.Column('code',         sa.String(50), unique=True, nullable=False),
        sa.Column('quotation_id', sa.Integer(), sa.ForeignKey('quotations.id'), nullable=True),
        sa.Column('customer_id',  sa.Integer(), sa.ForeignKey('customers.id'), nullable=False),
        sa.Column('date',         sa.Date(), nullable=False),
        sa.Column('due_date',     sa.Date()),
        sa.Column('status',       sa.String(20), server_default='unpaid'),  # unpaid|partial|paid
        sa.Column('subtotal',     sa.Numeric(14,2), server_default='0'),
        sa.Column('tax_pct',      sa.Numeric(5,2),  server_default='0'),
        sa.Column('tax_amount',   sa.Numeric(14,2), server_default='0'),
        sa.Column('discount_pct', sa.Numeric(5,2),  server_default='0'),
        sa.Column('discount_amt', sa.Numeric(14,2), server_default='0'),
        sa.Column('total',        sa.Numeric(14,2), server_default='0'),
        sa.Column('paid_amount',  sa.Numeric(14,2), server_default='0'),
        sa.Column('notes',        sa.Text()),
        sa.Column('terms',        sa.Text()),
        sa.Column('created_at',   sa.DateTime(), server_default=sa.func.now()),
    )
    # ── سطور الفواتير ─────────────────────────────────────────
    op.create_table('invoice_lines',
        sa.Column('id',         sa.Integer(), primary_key=True),
        sa.Column('invoice_id', sa.Integer(), sa.ForeignKey('invoices.id'), nullable=False),
        sa.Column('description',sa.String(500), nullable=False),
        sa.Column('qty',        sa.Numeric(14,3), server_default='1'),
        sa.Column('unit',       sa.String(20),  server_default='pcs'),
        sa.Column('unit_price', sa.Numeric(14,2), nullable=False),
        sa.Column('total_price',sa.Numeric(14,2), nullable=False),
        sa.Column('notes',      sa.Text()),
        sa.Column('sort_order', sa.Integer(), server_default='0'),
    )
    op.create_index('ix_inv_lines_inv', 'invoice_lines', ['invoice_id'])


def downgrade() -> None:
    op.drop_table('invoice_lines')
    op.drop_table('invoices')
    op.drop_table('quotation_lines')
    op.drop_table('quotations')
