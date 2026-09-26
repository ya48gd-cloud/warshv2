"""Supply contract payment schedule support — إتاحة خطة الأقساط للعقود

يستخدم جدول payment_schedule_templates الموجود بالفعل (migration 0022)
بـ source_type='supply_contract'. هذا الـ migration يضيف index مركّب
لتسريع الاستعلامات على عقود التوريد تحديداً.

migration 0022 أضاف:
  - ix_pst_tenant_id    على (tenant_id)
  - ix_pst_source_type  على (source_type)
  - ix_pst_source_id    على (source_id)

هذا الـ migration يضيف:
  - ix_pst_source_composite على (source_type, source_id) معاً — compound index
    أسرع من استخدام الـ single-column indexes المنفصلين في استعلامات
    WHERE source_type='supply_contract' AND source_id=X

revision: 0027
"""
from alembic import op

revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade():
    # compound index: source_type + source_id معاً
    # يسرّع: WHERE source_type='supply_contract' AND source_id=123
    op.create_index(
        'ix_pst_source_composite',
        'payment_schedule_templates',
        ['source_type', 'source_id'],
        unique=False,
    )


def downgrade():
    op.drop_index('ix_pst_source_composite', table_name='payment_schedule_templates')
