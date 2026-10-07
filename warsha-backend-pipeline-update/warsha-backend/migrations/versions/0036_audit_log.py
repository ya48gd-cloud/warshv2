"""Audit log (سجل التدقيق) — audit_logs + audit_settings"""
from alembic import op
import sqlalchemy as sa

revision = '0036'
down_revision = '0035'
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _table_exists('audit_logs'):
        op.create_table(
            'audit_logs',
            sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                      primary_key=True, autoincrement=True),
            # بدون FK عمدًا: السجل يبقى حتى لو الورشة اتشالت
            sa.Column('tenant_id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('user_name', sa.String(100), nullable=True),
            sa.Column('action', sa.String(20), nullable=False),
            sa.Column('entity_type', sa.String(60), nullable=True),
            sa.Column('entity_id', sa.Integer(), nullable=True),
            sa.Column('entity_ref', sa.String(200), nullable=True),
            sa.Column('summary', sa.String(300), nullable=True),
            sa.Column('changes', sa.JSON(), nullable=True),
            sa.Column('extra', sa.JSON(), nullable=True),
            sa.Column('ip', sa.String(64), nullable=True),
            sa.Column('path', sa.String(200), nullable=True),
            sa.Column('request_id', sa.String(36), nullable=True),
        )
        op.create_index('ix_audit_tenant_created', 'audit_logs', ['tenant_id', 'created_at'])
        op.create_index('ix_audit_tenant_entity', 'audit_logs', ['tenant_id', 'entity_type', 'entity_id'])
        op.create_index('ix_audit_tenant_user', 'audit_logs', ['tenant_id', 'user_id', 'created_at'])
        op.create_index('ix_audit_tenant_action', 'audit_logs', ['tenant_id', 'action', 'created_at'])

    if not _table_exists('audit_settings'):
        op.create_table(
            'audit_settings',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
            sa.Column('retention_days', sa.Integer(), nullable=True),
            sa.Column('updated_by', sa.Integer(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('tenant_id', name='uq_audit_setting_tenant'),
        )
        op.create_index('ix_audit_settings_tenant_id', 'audit_settings', ['tenant_id'])


def downgrade():
    if _table_exists('audit_settings'):
        op.drop_index('ix_audit_settings_tenant_id', table_name='audit_settings')
        op.drop_table('audit_settings')
    if _table_exists('audit_logs'):
        op.drop_index('ix_audit_tenant_action', table_name='audit_logs')
        op.drop_index('ix_audit_tenant_user', table_name='audit_logs')
        op.drop_index('ix_audit_tenant_entity', table_name='audit_logs')
        op.drop_index('ix_audit_tenant_created', table_name='audit_logs')
        op.drop_table('audit_logs')
