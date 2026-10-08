"""الفاتورة الإلكترونية (ETA) — einvoice_settings + einvoice_submissions + أعمدة eta_* على invoices/credit_notes

كل الأعمدة الجديدة nullable (بدون server default) فالسلوك القديم لا يتأثر.
الخطوات كلها محروسة (تفحص الوجود أولاً) فالترحيل آمن لو اتشغّل أكتر من مرة،
وبتستخدم sa.inspect فتشتغل على SQLite وPostgres.
"""
from alembic import op
import sqlalchemy as sa

revision = '0039'
down_revision = '0038'
branch_labels = None
depends_on = None

# (اسم العمود, النوع, مفهرس؟)
_ETA_COLS = [
    ('eta_uuid', lambda: sa.String(64), True),
    ('eta_status', lambda: sa.String(20), True),
    ('eta_submission_id', lambda: sa.String(64), False),
    ('eta_long_id', lambda: sa.String(128), False),
    ('eta_internal_id', lambda: sa.String(100), False),
    ('eta_submitted_at', lambda: sa.DateTime(), False),
    ('eta_errors', lambda: sa.JSON(), False),
]
_DOC_TABLES = ('invoices', 'credit_notes')

_SETTINGS_INDEXES = [('ix_einvoice_settings_tenant_id', ['tenant_id'])]
_SUB_INDEXES = [
    ('ix_einvoice_submissions_tenant_id', ['tenant_id']),
    ('ix_einvoice_submissions_status', ['status']),
    ('ix_einvoice_submissions_eta_uuid', ['eta_uuid']),
    ('ix_einvoice_submissions_eta_submission_id', ['eta_submission_id']),
    ('ix_einvoice_submissions_next_attempt_at', ['next_attempt_at']),
]


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _col_exists(table: str, column: str) -> bool:
    if not _table_exists(table):
        return False
    return column in {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _index_exists(table: str, index: str) -> bool:
    if not _table_exists(table):
        return False
    return index in {i['name'] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    # ── أعمدة المرآة على الفواتير والإشعارات الدائنة ──
    for table in _DOC_TABLES:
        if not _table_exists(table):
            continue
        for col, typ, indexed in _ETA_COLS:
            if not _col_exists(table, col):
                op.add_column(table, sa.Column(col, typ(), nullable=True))
            ix = f'ix_{table}_{col}'
            if indexed and not _index_exists(table, ix):
                op.create_index(ix, table, [col])

    # ── إعدادات الفاتورة الإلكترونية (صف لكل ورشة) ──
    if not _table_exists('einvoice_settings'):
        op.create_table(
            'einvoice_settings',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('environment', sa.String(10), nullable=False, server_default='preprod'),
            sa.Column('tax_registration_number', sa.String(20), nullable=True),
            sa.Column('activity_code', sa.String(10), nullable=True),
            sa.Column('issuer_name', sa.String(200), nullable=True),
            sa.Column('branch_id', sa.String(20), nullable=True, server_default='0'),
            sa.Column('governate', sa.String(100), nullable=True),
            sa.Column('region_city', sa.String(100), nullable=True),
            sa.Column('street', sa.String(200), nullable=True),
            sa.Column('building_number', sa.String(50), nullable=True),
            sa.Column('client_credential_id_enc', sa.Text(), nullable=True),
            sa.Column('client_secret_enc', sa.Text(), nullable=True),
            sa.Column('custom_id_base_url', sa.String(300), nullable=True),
            sa.Column('custom_api_base_url', sa.String(300), nullable=True),
            sa.Column('signer_mode', sa.String(10), nullable=False, server_default='none'),
            sa.Column('signer_url', sa.String(300), nullable=True),
            sa.Column('signer_token_enc', sa.Text(), nullable=True),
            sa.Column('default_item_type', sa.String(5), nullable=False, server_default='EGS'),
            sa.Column('default_item_code', sa.String(50), nullable=False, server_default='GEN'),
            sa.Column('default_unit_type', sa.String(10), nullable=False, server_default='EA'),
            sa.Column('vat_subtype', sa.String(10), nullable=False, server_default='V009'),
            sa.Column('zero_vat_subtype', sa.String(10), nullable=False, server_default='V003'),
            sa.Column('person_id_threshold', sa.Numeric(14, 2), nullable=False, server_default='50000'),
            sa.Column('max_backdate_days', sa.Integer(), nullable=False, server_default='7'),
            sa.Column('last_connection_ok', sa.Boolean(), nullable=True),
            sa.Column('last_connection_at', sa.DateTime(), nullable=True),
            sa.Column('last_connection_error', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('tenant_id', name='uq_einvoice_settings_tenant'),
        )
    for name, cols in _SETTINGS_INDEXES:
        if not _index_exists('einvoice_settings', name):
            op.create_index(name, 'einvoice_settings', cols)

    # ── صفوف التسليم (صف واحد لكل مستند) ──
    if not _table_exists('einvoice_submissions'):
        op.create_table(
            'einvoice_submissions',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
            sa.Column('doc_type', sa.String(15), nullable=False),
            sa.Column('doc_id', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(15), nullable=False),
            sa.Column('internal_id', sa.String(100), nullable=True),
            sa.Column('eta_uuid', sa.String(64), nullable=True),
            sa.Column('eta_long_id', sa.String(128), nullable=True),
            sa.Column('eta_submission_id', sa.String(64), nullable=True),
            sa.Column('payload_json', sa.JSON(), nullable=True),
            sa.Column('payload_sha256', sa.String(64), nullable=True),
            sa.Column('signer_mode_used', sa.String(10), nullable=True),
            sa.Column('environment_used', sa.String(10), nullable=True),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('next_attempt_at', sa.DateTime(), nullable=True),
            sa.Column('last_attempt_at', sa.DateTime(), nullable=True),
            sa.Column('locked_at', sa.DateTime(), nullable=True),
            sa.Column('last_error', sa.Text(), nullable=True),
            sa.Column('errors', sa.JSON(), nullable=True),
            sa.Column('history', sa.JSON(), nullable=True),
            sa.Column('submitted_at', sa.DateTime(), nullable=True),
            sa.Column('validated_at', sa.DateTime(), nullable=True),
            sa.Column('created_by_user_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('tenant_id', 'doc_type', 'doc_id', name='uq_einvoice_sub_doc'),
        )
    for name, cols in _SUB_INDEXES:
        if not _index_exists('einvoice_submissions', name):
            op.create_index(name, 'einvoice_submissions', cols)


def downgrade():
    for table, indexes in (('einvoice_submissions', _SUB_INDEXES),
                           ('einvoice_settings', _SETTINGS_INDEXES)):
        if _table_exists(table):
            for name, _cols in reversed(indexes):
                if _index_exists(table, name):
                    op.drop_index(name, table_name=table)
            op.drop_table(table)

    for table in _DOC_TABLES:
        if not _table_exists(table):
            continue
        for col, _typ, indexed in reversed(_ETA_COLS):
            ix = f'ix_{table}_{col}'
            if indexed and _index_exists(table, ix):
                op.drop_index(ix, table_name=table)
            if _col_exists(table, col):
                # SQLite القديم لا يدعم DROP COLUMN مباشرة — batch_alter_table يتولّى ذلك
                with op.batch_alter_table(table) as batch:
                    batch.drop_column(col)
