"""Warehouses (logical) — محطة 10"""
from alembic import op
import sqlalchemy as sa

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = insp.get_table_names()

    if 'warehouses' not in existing_tables:
        op.create_table(
            'warehouses',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
            sa.Column('code', sa.String(20), nullable=False),
            sa.Column('name_ar', sa.String(100), nullable=False),
            sa.Column('warehouse_type', sa.String(20), nullable=False, server_default='general'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint('tenant_id', 'code', name='uq_warehouse_tenant_code'),
        )
        op.create_index('ix_warehouses_tenant_id', 'warehouses', ['tenant_id'])


def downgrade():
    op.drop_index('ix_warehouses_tenant_id', table_name='warehouses')
    op.drop_table('warehouses')
