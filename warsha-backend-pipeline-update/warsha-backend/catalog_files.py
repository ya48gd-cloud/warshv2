"""
Script to add Global Catalog feature
Run from: E:\heavy-erp-fixed\warsha\backend
python catalog_files.py
"""
import os, shutil

# ── 1. Migration ──────────────────────────────────────────────
migration = '''"""Global Material Catalog"""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None

def upgrade():
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
'''
open('migrations/versions/0016_global_catalog.py', 'w', encoding='utf-8').write(migration)
print('Migration created')

# ── 2. catalog.py ─────────────────────────────────────────────
catalog_api = '''"""Global Catalog API"""
import os
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy import select, func, or_, String, Integer, Boolean, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
from datetime import datetime

from app.core.database import get_db
from app.core.security import require_any
from app.models.models import Base, Material, MaterialCategory

router = APIRouter(prefix="/catalog", tags=["catalog"])

SUPER_ADMIN_USERNAME = os.getenv("SUPER_ADMIN_USERNAME", "superadmin")
SUPER_ADMIN_PASSWORD = os.getenv("SUPER_ADMIN_PASSWORD", "changeme123")
_sa_tokens: set = set()

def register_token(token: str):
    _sa_tokens.add(token)

class GlobalCatalogItem(Base):
    __tablename__ = "global_catalog"
    id:          Mapped[int]           = mapped_column(primary_key=True)
    code:        Mapped[str]           = mapped_column(String(50), unique=True)
    name_ar:     Mapped[str]           = mapped_column(String(200))
    name_en:     Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    unit:        Mapped[str]           = mapped_column(String(30))
    category:    Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags:        Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active:   Mapped[bool]          = mapped_column(Boolean, default=True)
    usage_count: Mapped[int]           = mapped_column(Integer, default=0)
    created_at:  Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())


def _verify_sa(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Super Admin فقط")
    token = authorization.split(" ", 1)[1]
    if token not in _sa_tokens:
        raise HTTPException(401, "جلسة منتهية")
    return token


class CatalogItemIn(BaseModel):
    code: str
    name_ar: str
    name_en: Optional[str] = None
    unit: str
    category: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None

class ImportIn(BaseModel):
    unit_cost: Optional[Decimal] = Decimal("0")
    stock_qty: Optional[Decimal] = Decimal("0")
    reorder_level: Optional[Decimal] = Decimal("0")
    category_name: Optional[str] = None

class BulkIn(BaseModel):
    items: List[CatalogItemIn]


@router.get("/categories")
async def get_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(GlobalCatalogItem.category)
        .where(GlobalCatalogItem.is_active == True)
        .where(GlobalCatalogItem.category != None)
        .distinct().order_by(GlobalCatalogItem.category)
    )
    return [r[0] for r in result.fetchall()]


@router.get("")
async def search_catalog(
    q: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_any),
):
    stmt = select(GlobalCatalogItem).where(GlobalCatalogItem.is_active == True)
    if q:
        stmt = stmt.where(or_(
            GlobalCatalogItem.name_ar.ilike(f"%{q}%"),
            GlobalCatalogItem.name_en.ilike(f"%{q}%"),
            GlobalCatalogItem.code.ilike(f"%{q}%"),
            GlobalCatalogItem.tags.ilike(f"%{q}%"),
        ))
    if category:
        stmt = stmt.where(GlobalCatalogItem.category == category)
    stmt = stmt.order_by(GlobalCatalogItem.usage_count.desc(), GlobalCatalogItem.name_ar).offset(offset).limit(limit)
    result = await db.execute(stmt)
    items = result.scalars().all()
    return [{"id":i.id,"code":i.code,"name_ar":i.name_ar,"name_en":i.name_en,"unit":i.unit,"category":i.category,"description":i.description,"tags":i.tags,"usage_count":i.usage_count} for i in items]


@router.post("", status_code=201)
async def add_item(data: CatalogItemIn, db: AsyncSession = Depends(get_db), _=Depends(_verify_sa)):
    existing = await db.execute(select(GlobalCatalogItem).where(GlobalCatalogItem.code == data.code))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "الكود موجود بالفعل")
    item = GlobalCatalogItem(**data.model_dump())
    db.add(item); await db.commit(); await db.refresh(item)
    return {"id": item.id, "message": "تم الإضافة"}


@router.post("/bulk", status_code=201)
async def bulk_import(data: BulkIn, db: AsyncSession = Depends(get_db), _=Depends(_verify_sa)):
    added = skipped = 0
    for d in data.items:
        ex = await db.execute(select(GlobalCatalogItem).where(GlobalCatalogItem.code == d.code))
        if ex.scalar_one_or_none(): skipped += 1; continue
        db.add(GlobalCatalogItem(**d.model_dump())); added += 1
    await db.commit()
    return {"added": added, "skipped": skipped}


@router.put("/{item_id}")
async def update_item(item_id: int, data: CatalogItemIn, db: AsyncSession = Depends(get_db), _=Depends(_verify_sa)):
    result = await db.execute(select(GlobalCatalogItem).where(GlobalCatalogItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item: raise HTTPException(404, "غير موجود")
    for k, v in data.model_dump().items(): setattr(item, k, v)
    await db.commit()
    return {"message": "تم التحديث"}


@router.delete("/{item_id}")
async def delete_item(item_id: int, db: AsyncSession = Depends(get_db), _=Depends(_verify_sa)):
    result = await db.execute(select(GlobalCatalogItem).where(GlobalCatalogItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item: raise HTTPException(404, "غير موجود")
    item.is_active = False; await db.commit()
    return {"message": "تم الحذف"}


@router.post("/{item_id}/import")
async def import_to_inventory(
    item_id: int, data: ImportIn,
    db: AsyncSession = Depends(get_db),
    current=Depends(require_any),
):
    result = await db.execute(select(GlobalCatalogItem).where(GlobalCatalogItem.id == item_id))
    cat_item = result.scalar_one_or_none()
    if not cat_item: raise HTTPException(404, "العنصر غير موجود في الكتالوج")

    tenant_id = current.tenant_id
    existing = await db.execute(select(Material).where(Material.tenant_id == tenant_id, Material.code == cat_item.code))
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"المادة {cat_item.code} موجودة بالفعل في مخزونك")

    cat_name = data.category_name or cat_item.category or "عام"
    cat_result = await db.execute(select(MaterialCategory).where(MaterialCategory.tenant_id == tenant_id, MaterialCategory.name_ar == cat_name))
    category = cat_result.scalar_one_or_none()
    if not category:
        category = MaterialCategory(tenant_id=tenant_id, name_ar=cat_name, name_en=cat_name)
        db.add(category); await db.flush()

    material = Material(
        tenant_id=tenant_id, code=cat_item.code,
        name_ar=cat_item.name_ar, name_en=cat_item.name_en or cat_item.name_ar,
        unit=cat_item.unit, unit_cost=data.unit_cost or Decimal("0"),
        stock_qty=data.stock_qty or Decimal("0"),
        reorder_level=data.reorder_level or Decimal("0"),
        category_id=category.id, material_kind="general",
    )
    db.add(material)
    cat_item.usage_count = (cat_item.usage_count or 0) + 1
    await db.commit()
    return {"message": f"تم إضافة {cat_item.name_ar} لمخزونك", "material_id": material.id}
'''
open('app/api/catalog.py', 'w', encoding='utf-8').write(catalog_api)
print('catalog.py created')

# ── 3. Update main.py ─────────────────────────────────────────
main = open('app/main.py', encoding='utf-8').read()
if 'catalog_router' not in main:
    main = main.replace(
        'from app.api.super_admin',
        'from app.api.catalog import router as catalog_router\nfrom app.api.super_admin'
    )
    main = main.replace(
        'app.include_router(super_admin_router,',
        'app.include_router(catalog_router,     prefix="/api/v1")\napp.include_router(super_admin_router,'
    )
    open('app/main.py', 'w', encoding='utf-8').write(main)
    print('main.py updated')

# ── 4. Update Materials.jsx ───────────────────────────────────
mat = open('react-src/src/pages/Materials.jsx', encoding='utf-8').read()
if 'CatalogPicker' not in mat:
    mat = "import CatalogPicker from '../components/ui/CatalogPicker'\n" + mat
    mat = mat.replace(
        "const [catModal, setCatModal]   = useState(false)",
        "const [catModal, setCatModal]   = useState(false)\n  const [showCatalog, setShowCatalog] = useState(false)"
    )
    mat = mat.replace(
        "{write && <button className=\"btn btn-primary btn-sm\" onClick={openAdd}>+ مادة جديدة</button>}",
        "{write && <button className=\"btn btn-sm\" style={{background:'var(--teal)',color:'#fff',borderColor:'var(--teal)'}} onClick={() => setShowCatalog(true)}>📦 من الكتالوج</button>}\n        {write && <button className=\"btn btn-primary btn-sm\" onClick={openAdd}>+ مادة جديدة</button>}"
    )
    mat = mat.replace(
        "{/* Modal */}",
        "{showCatalog && <CatalogPicker onImport={() => reload()} onClose={() => setShowCatalog(false)} />}\n      {/* Modal */}"
    )
    open('react-src/src/pages/Materials.jsx', 'w', encoding='utf-8').write(mat)
    print('Materials.jsx updated')

# ── 5. Copy CatalogPicker.jsx ─────────────────────────────────
os.makedirs('react-src/src/components/ui', exist_ok=True)
shutil.copy('CatalogPicker.jsx', 'react-src/src/components/ui/CatalogPicker.jsx')
print('CatalogPicker.jsx copied')

print('\nAll done! Run: git add . && git commit -m "add global catalog" && git push')
