"""Equipment linked to formal purchase invoice — محطة 23 (الروادماب الأصلي)"""
from alembic import op
import sqlalchemy as sa

revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='" + table + "' AND column_name='" + col + "'"
    ))
    return result.fetchone() is not None


def upgrade():
    if not _col_exists('equipment', 'purchase_invoice_id'):
        op.add_column('equipment', sa.Column(
            'purchase_invoice_id', sa.Integer(), sa.ForeignKey('purchase_invoices.id'), nullable=True))


def downgrade():
    op.drop_column('equipment', 'purchase_invoice_id')
