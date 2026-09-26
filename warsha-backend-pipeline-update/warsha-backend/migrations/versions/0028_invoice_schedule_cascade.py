"""
invoice_payment_schedules CASCADE — إضافة ON DELETE CASCADE لـ invoice_id

قبل هذا الـ migration، حذف الفاتورة كان يعتمد على sa_delete() اليدوي
في الكود لحذف الأقساط أولاً. هذا الـ migration يضيف الحماية على مستوى
قاعدة البيانات كـ safety net إضافي.

revision: 0028
"""
from alembic import op
import sqlalchemy as sa


revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None


def upgrade():
    # PostgreSQL: نعيد إنشاء الـ FK مع CASCADE
    # SQLite: لا تدعم ALTER CONSTRAINT — نتركه كما هو (الكود بيعمل sa_delete يدوي)
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # احذف الـ FK القديم وأنشئ واحد جديد مع CASCADE
        op.drop_constraint(
            'invoice_payment_schedules_invoice_id_fkey',
            'invoice_payment_schedules',
            type_='foreignkey',
        )
        op.create_foreign_key(
            'invoice_payment_schedules_invoice_id_fkey',
            'invoice_payment_schedules', 'invoices',
            ['invoice_id'], ['id'],
            ondelete='CASCADE',
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.drop_constraint(
            'invoice_payment_schedules_invoice_id_fkey',
            'invoice_payment_schedules',
            type_='foreignkey',
        )
        op.create_foreign_key(
            'invoice_payment_schedules_invoice_id_fkey',
            'invoice_payment_schedules', 'invoices',
            ['invoice_id'], ['id'],
        )
