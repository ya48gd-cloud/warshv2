"""Global Material Catalog"""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = insp.get_table_names()

    if 'global_catalog' not in existing_tables:
        op.create_table(
            'global_catalog',
            sa.Column('id',          sa.Integer(),    primary_key=True),
            sa.Column('code',        sa.String(50),   nullable=False, unique=True),
            sa.Column('name_ar',     sa.String(200),  nullable=False),
            sa.Column('name_en',     sa.String(200),  nullable=True),
            sa.Column('unit',        sa.String(30),   nullable=False),
            sa.Column('category',    sa.String(100),  nullable=True),
            sa.Column('description', sa.Text(),       nullable=True),
            sa.Column('tags',        sa.String(500),  nullable=True),
            sa.Column('is_active',   sa.Boolean(),    default=True),
            sa.Column('usage_count', sa.Integer(),    default=0),
            sa.Column('created_at',  sa.DateTime(),   server_default=sa.func.now()),
        )
        op.create_index('ix_gc_code',     'global_catalog', ['code'])
        op.create_index('ix_gc_name_ar',  'global_catalog', ['name_ar'])
        op.create_index('ix_gc_category', 'global_catalog', ['category'])

def downgrade():
    op.drop_table('global_catalog')
