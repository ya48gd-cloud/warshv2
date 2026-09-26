"""Equipment categories — تصنيفات المعدات

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-15

Changes:
- new table equipment_categories (id, tenant_id, name_ar, name_en, created_at)
- add column equipment.category_id (FK -> equipment_categories.id)
- seed default categories per tenant: مجارش، خلاطات، سيور نقالة، بريمات، أخرى
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_CATEGORIES = [
    ("مجارش", "Crushers"),
    ("خلاطات", "Mixers"),
    ("سيور نقالة", "Conveyors"),
    ("بريمات", "Augers"),
    ("أخرى", "Other"),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = insp.get_table_names()

    if "equipment_categories" not in existing_tables:
        op.create_table(
            "equipment_categories",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), index=True, nullable=False),
            sa.Column("name_ar", sa.String(120), nullable=False),
            sa.Column("name_en", sa.String(120), nullable=False),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        )

    existing_columns = [c["name"] for c in insp.get_columns("equipment")]
    if "category_id" not in existing_columns:
        op.add_column("equipment", sa.Column("category_id", sa.Integer, nullable=True))

    meta = sa.MetaData()
    cat_table = sa.Table("equipment_categories", meta, autoload_with=bind)

    tenant_ids = [row[0] for row in bind.execute(sa.text("SELECT DISTINCT tenant_id FROM equipment")).fetchall()]
    tenant_ids += [row[0] for row in bind.execute(sa.text("SELECT id FROM tenants")).fetchall()]
    tenant_ids = sorted(set(tenant_ids))

    for tid in tenant_ids:
        existing_names = {row[0] for row in bind.execute(
            sa.select(cat_table.c.name_ar).where(cat_table.c.tenant_id == tid)
        ).fetchall()}
        for name_ar, name_en in DEFAULT_CATEGORIES:
            if name_ar in existing_names:
                continue
            bind.execute(cat_table.insert().values(tenant_id=tid, name_ar=name_ar, name_en=name_en))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_columns = [c["name"] for c in insp.get_columns("equipment")]
    if "category_id" in existing_columns:
        op.drop_column("equipment", "category_id")
    if "equipment_categories" in insp.get_table_names():
        op.drop_table("equipment_categories")
