"""add users table for authentication

Revision ID: 0007
Revises: 0006
Create Date: 2024-01-07
"""
from alembic import op
import sqlalchemy as sa
import hashlib

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(50), unique=True, nullable=False),
        sa.Column('password_hash', sa.String(64), nullable=False),
        sa.Column('full_name', sa.String(200)),
        sa.Column('role', sa.String(20), server_default='admin'),   # admin | viewer | accountant
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('last_login', sa.DateTime()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    # Insert default admin user: admin / admin123
    default_hash = hashlib.sha256(b'admin123').hexdigest()
    op.execute(
        f"INSERT INTO users (username, password_hash, full_name, role) "
        f"VALUES ('admin', '{default_hash}', 'مدير النظام', 'admin')"
    )


def downgrade() -> None:
    op.drop_table('users')
