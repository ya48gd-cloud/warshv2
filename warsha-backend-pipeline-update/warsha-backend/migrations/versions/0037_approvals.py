"""Approval workflow (دورة الموافقات) — approval_rules + approval_requests"""
from alembic import op
import sqlalchemy as sa

revision = '0037'
down_revision = '0036'
branch_labels = None
depends_on = None

_OPEN_WHERE = "status IN ('pending','approved')"


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _index_exists(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return False
    return any(ix.get('name') == name for ix in insp.get_indexes(table))


def _create_index(name, table, cols, **kw):
    if not _index_exists(table, name):
        op.create_index(name, table, cols, **kw)


def upgrade():
    if not _table_exists('approval_rules'):
        op.create_table(
            'approval_rules',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
            sa.Column('name', sa.String(100), nullable=False),
            sa.Column('action_key', sa.String(40), nullable=False),
            sa.Column('condition_type', sa.String(20), nullable=False, server_default='amount_gt'),
            sa.Column('threshold', sa.Numeric(14, 2), nullable=True),
            sa.Column('required_role', sa.String(20), nullable=False, server_default='admin'),
            sa.Column('valid_hours', sa.Integer(), nullable=False, server_default='24'),
            sa.Column('exempt_roles', sa.String(100), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_by', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
        )
    _create_index('ix_approval_rules_tenant_id', 'approval_rules', ['tenant_id'])
    _create_index('ix_approval_rule_tenant_action', 'approval_rules',
                  ['tenant_id', 'action_key', 'is_active'])

    if not _table_exists('approval_requests'):
        op.create_table(
            'approval_requests',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
            sa.Column('rule_id', sa.Integer(), nullable=True),
            sa.Column('action_key', sa.String(40), nullable=False),
            sa.Column('entity_type', sa.String(40), nullable=False),
            sa.Column('entity_id', sa.Integer(), nullable=True),
            sa.Column('entity_label', sa.String(200), nullable=True),
            sa.Column('fingerprint', sa.String(64), nullable=False),
            sa.Column('amount', sa.Numeric(14, 2), nullable=True),
            sa.Column('percent', sa.Numeric(7, 2), nullable=True),
            sa.Column('snapshot', sa.JSON(), nullable=True),
            sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
            sa.Column('required_role', sa.String(20), nullable=False, server_default='admin'),
            sa.Column('valid_hours', sa.Integer(), nullable=False, server_default='24'),
            sa.Column('requested_by', sa.Integer(), nullable=False),
            sa.Column('requested_by_name', sa.String(100), nullable=True),
            sa.Column('requested_by_role', sa.String(20), nullable=True),
            sa.Column('requested_at', sa.DateTime(), nullable=True),
            sa.Column('decided_by', sa.Integer(), nullable=True),
            sa.Column('decided_by_name', sa.String(100), nullable=True),
            sa.Column('decided_at', sa.DateTime(), nullable=True),
            sa.Column('decision_reason', sa.Text(), nullable=True),
            sa.Column('expires_at', sa.DateTime(), nullable=True),
            sa.Column('consumed_at', sa.DateTime(), nullable=True),
            sa.Column('consumed_by', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
        )
    _create_index('ix_approval_requests_tenant_id', 'approval_requests', ['tenant_id'])
    _create_index('ix_approval_requests_rule_id', 'approval_requests', ['rule_id'])
    _create_index('ix_approval_req_tenant_status', 'approval_requests', ['tenant_id', 'status'])
    _create_index('ix_approval_req_tenant_fp', 'approval_requests', ['tenant_id', 'fingerprint'])
    _create_index(
        'uq_approval_open_fingerprint', 'approval_requests',
        ['tenant_id', 'requested_by', 'fingerprint'], unique=True,
        postgresql_where=sa.text(_OPEN_WHERE), sqlite_where=sa.text(_OPEN_WHERE),
    )


def downgrade():
    if _table_exists('approval_requests'):
        for ix in ('uq_approval_open_fingerprint', 'ix_approval_req_tenant_fp',
                   'ix_approval_req_tenant_status', 'ix_approval_requests_rule_id',
                   'ix_approval_requests_tenant_id'):
            if _index_exists('approval_requests', ix):
                op.drop_index(ix, table_name='approval_requests')
        op.drop_table('approval_requests')
    if _table_exists('approval_rules'):
        for ix in ('ix_approval_rule_tenant_action', 'ix_approval_rules_tenant_id'):
            if _index_exists('approval_rules', ix):
                op.drop_index(ix, table_name='approval_rules')
        op.drop_table('approval_rules')
