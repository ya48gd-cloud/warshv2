"""
ترقية الكتالوج العام ليشمل الحقول الهندسية (سُمك، كثافة، أبعاد، وزن/متر)
وتعبئته بـ 153 مادة حقيقية بمواصفات محسوبة دقيقة
Run from: E:\heavy-erp-fixed\warsha\backend
python upgrade_catalog_engineering.py
"""
import os, json

# ── 1. Migration جديدة لإضافة الأعمدة الهندسية ──────────────
migration = '''"""Global Catalog — engineering fields (sheet/bar specs)"""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('global_catalog', sa.Column('is_plate', sa.Boolean(), nullable=True, server_default='0'))
    op.add_column('global_catalog', sa.Column('thickness_mm', sa.Numeric(8,2), nullable=True))
    op.add_column('global_catalog', sa.Column('density_kg_m2', sa.Numeric(10,4), nullable=True))
    op.add_column('global_catalog', sa.Column('plate_length_cm', sa.Numeric(10,2), nullable=True))
    op.add_column('global_catalog', sa.Column('plate_width_cm', sa.Numeric(10,2), nullable=True))
    op.add_column('global_catalog', sa.Column('plate_weight_kg', sa.Numeric(10,3), nullable=True))
    op.add_column('global_catalog', sa.Column('bar_length_cm', sa.Numeric(10,2), nullable=True))
    op.add_column('global_catalog', sa.Column('bar_weight_kg', sa.Numeric(10,3), nullable=True))
    op.add_column('global_catalog', sa.Column('weight_per_meter_kg', sa.Numeric(10,4), nullable=True))

def downgrade():
    for col in ['is_plate','thickness_mm','density_kg_m2','plate_length_cm','plate_width_cm',
                'plate_weight_kg','bar_length_cm','bar_weight_kg','weight_per_meter_kg']:
        op.drop_column('global_catalog', col)
'''
os.makedirs('migrations/versions', exist_ok=True)
open('migrations/versions/0017_catalog_engineering_fields.py', 'w', encoding='utf-8').write(migration)
print('✅ Migration 0017 created')

# ── 2. تحديث catalog.py — إضافة الحقول الهندسية ─────────────
path = 'app/api/catalog.py'
c = open(path, encoding='utf-8').read()

old_model = '''class GlobalCatalogItem(Base):
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
    created_at:  Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())'''

new_model = '''class GlobalCatalogItem(Base):
    __tablename__ = "global_catalog"
    id:          Mapped[int]              = mapped_column(primary_key=True)
    code:        Mapped[str]              = mapped_column(String(50), unique=True)
    name_ar:     Mapped[str]              = mapped_column(String(200))
    name_en:     Mapped[Optional[str]]    = mapped_column(String(200), nullable=True)
    unit:        Mapped[str]              = mapped_column(String(30))
    category:    Mapped[Optional[str]]    = mapped_column(String(100), nullable=True)
    description: Mapped[Optional[str]]    = mapped_column(Text, nullable=True)
    tags:        Mapped[Optional[str]]    = mapped_column(String(500), nullable=True)
    is_active:   Mapped[bool]             = mapped_column(Boolean, default=True)
    usage_count: Mapped[int]              = mapped_column(Integer, default=0)
    created_at:  Mapped[datetime]         = mapped_column(DateTime, server_default=func.now())
    # ── المواصفات الهندسية — تُنقل تلقائياً عند الاستيراد لمخزون الورشة ──
    is_plate:            Mapped[Optional[bool]]    = mapped_column(Boolean, default=False, nullable=True)
    thickness_mm:        Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(8, 2), nullable=True)
    density_kg_m2:       Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 4), nullable=True)
    plate_length_cm:     Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 2), nullable=True)
    plate_width_cm:      Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 2), nullable=True)
    plate_weight_kg:     Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 3), nullable=True)
    bar_length_cm:       Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 2), nullable=True)
    bar_weight_kg:        Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 3), nullable=True)
    weight_per_meter_kg: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(10, 4), nullable=True)'''

if old_model in c:
    c = c.replace(old_model, new_model)
    print('✅ GlobalCatalogItem model — engineering fields added')
else:
    print('⚠️  Model pattern not found — checking if already upgraded')
    if 'thickness_mm' in c:
        print('ℹ️  Already has engineering fields')

# إضافة import لـ sqlalchemy as sa لو مش موجود
if 'import sqlalchemy as sa' not in c:
    c = c.replace(
        'from sqlalchemy import select, func, or_, String, Integer, Boolean, Text, DateTime',
        'import sqlalchemy as sa\nfrom sqlalchemy import select, func, or_, String, Integer, Boolean, Text, DateTime'
    )

# تحديث CatalogItemIn schema لقبول الحقول الهندسية
old_schema = '''class CatalogItemIn(BaseModel):
    code: str
    name_ar: str
    name_en: Optional[str] = None
    unit: str
    category: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None'''

new_schema = '''class CatalogItemIn(BaseModel):
    code: str
    name_ar: str
    name_en: Optional[str] = None
    unit: str
    category: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    is_plate: Optional[bool] = False
    thickness_mm: Optional[Decimal] = None
    density_kg_m2: Optional[Decimal] = None
    plate_length_cm: Optional[Decimal] = None
    plate_width_cm: Optional[Decimal] = None
    plate_weight_kg: Optional[Decimal] = None
    bar_length_cm: Optional[Decimal] = None
    bar_weight_kg: Optional[Decimal] = None
    weight_per_meter_kg: Optional[Decimal] = None'''

if old_schema in c:
    c = c.replace(old_schema, new_schema)
    print('✅ CatalogItemIn — engineering fields added')

# تحديث search_catalog response ليشمل الحقول الهندسية
old_response = '''    return [{"id":i.id,"code":i.code,"name_ar":i.name_ar,"name_en":i.name_en,"unit":i.unit,"category":i.category,"description":i.description,"tags":i.tags,"usage_count":i.usage_count} for i in items]'''
new_response = '''    return [{
        "id":i.id,"code":i.code,"name_ar":i.name_ar,"name_en":i.name_en,"unit":i.unit,
        "category":i.category,"description":i.description,"tags":i.tags,"usage_count":i.usage_count,
        "is_plate": i.is_plate, "thickness_mm": float(i.thickness_mm) if i.thickness_mm else None,
        "plate_length_cm": float(i.plate_length_cm) if i.plate_length_cm else None,
        "plate_width_cm": float(i.plate_width_cm) if i.plate_width_cm else None,
        "plate_weight_kg": float(i.plate_weight_kg) if i.plate_weight_kg else None,
        "weight_per_meter_kg": float(i.weight_per_meter_kg) if i.weight_per_meter_kg else None,
    } for i in items]'''
if old_response in c:
    c = c.replace(old_response, new_response)
    print('✅ search_catalog response — engineering fields included')

# تحديث import_to_inventory — نقل المواصفات الهندسية للمادة الجديدة تلقائياً
old_material = '''    material = Material(
        tenant_id=tenant_id, code=cat_item.code,
        name_ar=cat_item.name_ar, name_en=cat_item.name_en or cat_item.name_ar,
        unit=cat_item.unit, unit_cost=data.unit_cost or Decimal("0"),
        stock_qty=data.stock_qty or Decimal("0"),
        reorder_level=data.reorder_level or Decimal("0"),
        category_id=category.id, material_kind="general",
    )'''
new_material = '''    material_kind = "plate" if cat_item.is_plate else ("bar" if cat_item.weight_per_meter_kg else "general")
    material = Material(
        tenant_id=tenant_id, code=cat_item.code,
        name_ar=cat_item.name_ar, name_en=cat_item.name_en or cat_item.name_ar,
        unit=cat_item.unit, unit_cost=data.unit_cost or Decimal("0"),
        stock_qty=data.stock_qty or Decimal("0"),
        reorder_level=data.reorder_level or Decimal("0"),
        category_id=category.id, material_kind=material_kind,
        is_plate=cat_item.is_plate or False,
        thickness_mm=cat_item.thickness_mm,
        density_kg_m2=cat_item.density_kg_m2,
        plate_length_cm=cat_item.plate_length_cm,
        plate_width_cm=cat_item.plate_width_cm,
        plate_weight_kg=cat_item.plate_weight_kg,
        bar_length_cm=cat_item.bar_length_cm,
        bar_weight_kg=cat_item.bar_weight_kg,
        weight_per_meter_kg=cat_item.weight_per_meter_kg,
    )'''
if old_material in c:
    c = c.replace(old_material, new_material)
    print('✅ import_to_inventory — engineering specs now transferred automatically')
else:
    print('⚠️  import_to_inventory pattern not found — check manually')

open(path, 'w', encoding='utf-8').write(c)

print('\n' + '='*50)
print('Backend upgraded! Now run:')
print('git add . && git commit -m "add engineering fields to global catalog" && git push')
print('\nThen restart/redeploy the backend, then run: python seed_catalog.py')
