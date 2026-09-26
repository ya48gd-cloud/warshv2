"""Stock movement traceability — movement_no + source_doc_type + source_doc_id"""
from alembic import op
import sqlalchemy as sa

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='" + table + "' AND column_name='" + col + "'"
    ))
    return result.fetchone() is not None


def _index_exists(table: str, index_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(ix['name'] == index_name for ix in insp.get_indexes(table))


def upgrade():
    if not _col_exists('stock_movements', 'movement_no'):
        op.add_column(
            'stock_movements',
            sa.Column('movement_no', sa.Integer(), nullable=True),
        )
    if not _index_exists('stock_movements', 'ix_stock_movements_movement_no'):
        op.create_index(
            'ix_stock_movements_movement_no', 'stock_movements', ['movement_no'],
        )
    if not _col_exists('stock_movements', 'source_doc_type'):
        op.add_column(
            'stock_movements',
            sa.Column('source_doc_type', sa.String(50), nullable=True),
        )
    if not _col_exists('stock_movements', 'source_doc_id'):
        op.add_column(
            'stock_movements',
            sa.Column('source_doc_id', sa.Integer(), nullable=True),
        )


def downgrade():
    op.drop_column('stock_movements', 'source_doc_id')
    op.drop_column('stock_movements', 'source_doc_type')
    op.drop_index('ix_stock_movements_movement_no', table_name='stock_movements')
    op.drop_column('stock_movements', 'movement_no')
