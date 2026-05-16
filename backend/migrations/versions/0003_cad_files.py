"""add cad_files table

Revision ID: 0003
Revises: 0002
Create Date: 2024-01-03
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('cad_files',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('equipment_id', sa.Integer(), sa.ForeignKey('equipment.id'), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),       # اسم الملف الأصلي
        sa.Column('stored_name', sa.String(255), nullable=False),    # الاسم على الـ disk
        sa.Column('file_size', sa.Integer()),                        # bytes
        sa.Column('mime_type', sa.String(100)),
        sa.Column('revision', sa.String(20)),                        # Rev1, Rev2 ...
        sa.Column('notes', sa.Text()),
        sa.Column('uploaded_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_cad_files_equipment_id', 'cad_files', ['equipment_id'])


def downgrade() -> None:
    op.drop_table('cad_files')
