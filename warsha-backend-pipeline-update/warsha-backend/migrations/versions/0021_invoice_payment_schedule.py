"""Invoice payment schedule (installments) — محطة 4"""
from alembic import op
import sqlalchemy as sa

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = insp.get_table_names()

    if 'invoice_payment_schedules' not in existing_tables:
        op.create_table(
            'invoice_payment_schedules',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
            sa.Column('invoice_id', sa.Integer(), sa.ForeignKey('invoices.id'), nullable=False),
            sa.Column('installment_no', sa.Integer(), nullable=False),
            sa.Column('label', sa.String(200), nullable=True),
            sa.Column('amount_due', sa.Numeric(14, 2), nullable=False),
            sa.Column('due_date', sa.Date(), nullable=False),
            sa.Column('paid_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
            sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint('invoice_id', 'installment_no', name='uq_invoice_installment_no'),
        )
        op.create_index(
            'ix_invoice_payment_schedules_tenant_id', 'invoice_payment_schedules', ['tenant_id'],
        )
        op.create_index(
            'ix_invoice_payment_schedules_invoice_id', 'invoice_payment_schedules', ['invoice_id'],
        )


def downgrade():
    op.drop_index('ix_invoice_payment_schedules_invoice_id', table_name='invoice_payment_schedules')
    op.drop_index('ix_invoice_payment_schedules_tenant_id', table_name='invoice_payment_schedules')
    op.drop_table('invoice_payment_schedules')
