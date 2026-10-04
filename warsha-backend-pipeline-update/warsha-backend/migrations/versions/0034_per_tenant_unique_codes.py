"""Per-tenant unique document codes

الأكواد (PO-/SC-/CN-/WCC-/WC-...) بتتولد لكل tenant، لكن الأعمدة دي كانت
UNIQUE على مستوى الجدول كله (global). الـ migration ده بيحوّلها لـ
UNIQUE(tenant_id, code) زي باقي الجداول (uq_invoice_tenant_code ...).

Tables:
  purchase_orders        code UNIQUE INDEX ix_purchase_orders_code  -> non-unique index + uq_purchaseorder_tenant_code
  supply_contracts       supply_contracts_code_key                  -> uq_supplycontract_tenant_code
  credit_notes           credit_notes_code_key                      -> uq_creditnote_tenant_code
  work_completion_certs  work_completion_certs_code_key             -> uq_workcompletioncert_tenant_code
  warranty_certificates  warranty_certificates_code_key             -> uq_warrantycert_tenant_code

Data safety: global-unique implies per-tenant-unique, so adding the new
constraint can never fail on existing rows.

Idempotent: 0001 runs Base.metadata.create_all with the *current* models, so on
a fresh DB the tables already have the new constraint and no global unique.
Existing constraint/index names are discovered via the inspector instead of
being hard-coded, so this works whatever name Postgres generated.

SQLite (desktop build) does not use alembic — app/desktop.py init_db uses
create_all, and SQLite cannot drop an unnamed column UNIQUE without a table
rebuild. Existing SQLite DBs keep the stricter global unique, which the API's
code generators already handle (they scan codes across tenants).

revision: 0034
"""
from alembic import op
import sqlalchemy as sa


revision = '0034'
down_revision = '0033'
branch_labels = None
depends_on = None


# (table, new constraint name, keep a plain non-unique index on code?)
_TABLES = [
    ('purchase_orders',       'uq_purchaseorder_tenant_code',      True),
    ('supply_contracts',      'uq_supplycontract_tenant_code',     False),
    ('credit_notes',          'uq_creditnote_tenant_code',         False),
    ('work_completion_certs', 'uq_workcompletioncert_tenant_code', False),
    ('warranty_certificates', 'uq_warrantycert_tenant_code',       False),
]


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    for table, uq_name, keep_index in _TABLES:
        if table not in existing_tables:
            continue

        # 1) drop any global UNIQUE constraint on (code) alone
        for uc in insp.get_unique_constraints(table):
            if list(uc.get('column_names') or []) == ['code'] and uc.get('name'):
                op.drop_constraint(uc['name'], table, type_='unique')

        # 2) drop any UNIQUE index on (code) alone (unique=True, index=True
        #    produces a unique index, not a constraint); remember plain ones
        has_plain_code_index = False
        for ix in insp.get_indexes(table):
            if list(ix.get('column_names') or []) != ['code']:
                continue
            if ix.get('unique'):
                # indexes backing a UNIQUE constraint were removed in step 1
                if ix.get('duplicates_constraint'):
                    continue
                op.drop_index(ix['name'], table_name=table)
            else:
                has_plain_code_index = True

        # 3) recreate a non-unique lookup index where the model declares index=True
        if keep_index and not has_plain_code_index:
            op.create_index(f'ix_{table}_code', table, ['code'], unique=False)

        # 4) add UNIQUE(tenant_id, code) if not already present
        insp_uc_names = {uc.get('name') for uc in sa.inspect(bind).get_unique_constraints(table)}
        if uq_name not in insp_uc_names:
            op.create_unique_constraint(uq_name, table, ['tenant_id', 'code'])


def downgrade():
    # NOTE: fails if two tenants have since been given the same code.
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    for table, uq_name, keep_index in _TABLES:
        if table not in existing_tables:
            continue
        uc_names = {uc.get('name') for uc in insp.get_unique_constraints(table)}
        if uq_name in uc_names:
            op.drop_constraint(uq_name, table, type_='unique')
        if keep_index:
            ix_names = {ix['name'] for ix in insp.get_indexes(table)}
            if f'ix_{table}_code' in ix_names:
                op.drop_index(f'ix_{table}_code', table_name=table)
            op.create_index(f'ix_{table}_code', table, ['code'], unique=True)
        else:
            op.create_unique_constraint(f'{table}_code_key', table, ['code'])
