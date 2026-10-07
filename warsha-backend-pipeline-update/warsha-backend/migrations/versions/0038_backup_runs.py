"""Backup & Restore — backup_runs (سجل عمليات النسخ الاحتياطي)"""
from alembic import op
import sqlalchemy as sa

revision = '0038'
down_revision = '0037'
branch_labels = None
depends_on = None

_INDEXES = [
    ('ix_backup_runs_tenant_id', ['tenant_id']),
    ('ix_backup_runs_kind', ['kind']),
    ('ix_backup_runs_status', ['status']),
    ('ix_backup_runs_started_at', ['started_at']),
]


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _index_exists(table: str, index: str) -> bool:
    if not _table_exists(table):
        return False
    return index in {i['name'] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    if not _table_exists('backup_runs'):
        op.create_table(
            'backup_runs',
            sa.Column('id', sa.Integer(), primary_key=True),
            # NULL = عملية على مستوى النظام
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=True),
            sa.Column('kind', sa.String(30), nullable=False),
            sa.Column('trigger', sa.String(20), nullable=False, server_default='manual'),
            sa.Column('status', sa.String(20), nullable=False, server_default='running'),
            sa.Column('db_engine', sa.String(20), nullable=False, server_default='sqlite'),
            sa.Column('file_name', sa.String(255), nullable=True),
            sa.Column('file_deleted', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('size_bytes', sa.BigInteger(), nullable=True),
            sa.Column('checksum_sha256', sa.String(64), nullable=True),
            sa.Column('integrity_ok', sa.Boolean(), nullable=True),
            sa.Column('created_by', sa.String(100), nullable=True),
            sa.Column('created_by_user_id', sa.Integer(), nullable=True),
            sa.Column('started_at', sa.DateTime(), nullable=False),
            sa.Column('finished_at', sa.DateTime(), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('details', sa.JSON(), nullable=True),
        )
    for name, cols in _INDEXES:
        if not _index_exists('backup_runs', name):
            op.create_index(name, 'backup_runs', cols)


def downgrade():
    if _table_exists('backup_runs'):
        for name, _cols in reversed(_INDEXES):
            if _index_exists('backup_runs', name):
                op.drop_index(name, table_name='backup_runs')
        op.drop_table('backup_runs')
