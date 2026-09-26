"""Payment schedule template on quotation/customer_order — محطة 7"""
from alembic import op
import sqlalchemy as sa

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = insp.get_table_names()

    if 'payment_schedule_templates' not in existing_tables:
        op.create_table(
            'payment_schedule_templates',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
            sa.Column('source_type', sa.String(20), nullable=False),
            sa.Column('source_id', sa.Integer(), nullable=False),
            sa.Column('installment_no', sa.Integer(), nullable=False),
            sa.Column('label', sa.String(200), nullable=True),
            sa.Column('pct', sa.Numeric(5, 2), nullable=False),
            sa.Column('days_offset', sa.Integer(), nullable=False, server_default='0'),
            sa.UniqueConstraint('source_type', 'source_id', 'installment_no', name='uq_pst_source_installment'),
        )
        op.create_index('ix_pst_tenant_id', 'payment_schedule_templates', ['tenant_id'])
        op.create_index('ix_pst_source_type', 'payment_schedule_templates', ['source_type'])
        op.create_index('ix_pst_source_id', 'payment_schedule_templates', ['source_id'])


def downgrade():
    op.drop_index('ix_pst_source_id', table_name='payment_schedule_templates')
    op.drop_index('ix_pst_source_type', table_name='payment_schedule_templates')
    op.drop_index('ix_pst_tenant_id', table_name='payment_schedule_templates')
    op.drop_table('payment_schedule_templates')
