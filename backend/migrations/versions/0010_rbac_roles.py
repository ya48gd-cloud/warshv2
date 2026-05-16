"""rbac_roles

Revision ID: 0010
Revises: 0009
Create Date: 2025-05-10

Expands users.role column to VARCHAR(20) to support:
  admin | accountant | production | viewer
Migrates any unrecognised roles to 'viewer'.
"""
from alembic import op
import sqlalchemy as sa

revision    = '0010'
down_revision = '0009'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # Widen role column from VARCHAR(10) → VARCHAR(20)
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(20)")

    # Normalise any legacy / unknown roles to 'viewer'
    op.execute("""
        UPDATE users
        SET role = 'viewer'
        WHERE role NOT IN ('admin', 'accountant', 'production', 'viewer')
    """)

    # Seed the initial admin user if users table is empty
    op.execute("""
        INSERT INTO users (username, password_hash, full_name, role, is_active)
        SELECT 'admin',
               encode(sha256('admin123'::bytea), 'hex'),
               'مدير النظام',
               'admin',
               true
        WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'admin')
    """)


def downgrade() -> None:
    # Collapse extended roles back to viewer before shrinking column
    op.execute("""
        UPDATE users
        SET role = 'viewer'
        WHERE role NOT IN ('admin', 'viewer')
    """)
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(10)")
