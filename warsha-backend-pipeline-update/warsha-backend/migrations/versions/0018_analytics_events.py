"""Analytics events — product analytics event log"""
from alembic import op
import sqlalchemy as sa

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = insp.get_table_names()

    if 'analytics_events' not in existing_tables:
        op.create_table(
            'analytics_events',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=True),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('event_type', sa.String(100), nullable=False),
            sa.Column('entity_type', sa.String(50), nullable=True),
            sa.Column('entity_id', sa.Integer(), nullable=True),
            sa.Column('event_metadata', sa.JSON(), nullable=True),
            sa.Column('source_path', sa.String(200), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index('ix_analytics_events_tenant_id', 'analytics_events', ['tenant_id'])
        op.create_index('ix_analytics_events_user_id', 'analytics_events', ['user_id'])
        op.create_index('ix_analytics_events_event_type', 'analytics_events', ['event_type'])
        op.create_index('ix_analytics_events_created_at', 'analytics_events', ['created_at'])

def downgrade():
    op.drop_index('ix_analytics_events_created_at', table_name='analytics_events')
    op.drop_index('ix_analytics_events_event_type', table_name='analytics_events')
    op.drop_index('ix_analytics_events_user_id', table_name='analytics_events')
    op.drop_index('ix_analytics_events_tenant_id', table_name='analytics_events')
    op.drop_table('analytics_events')
