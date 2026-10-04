from datetime import date as _date
from decimal import Decimal
from html import escape as _esc
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.auth import require_any
from app.services.equipment import EquipmentService
from app.schemas.schemas import EquipmentCreate, BOMLineCreate, EquipmentCostLineCreate
from app.models.models import Equipment, BOMLine, EquipmentDimension, CADFile, EquipmentCostLine, EquipmentCategory

# ── RBAC للكتابة ──────────────────────────────────────────────────────────
# الراوتر كان محمي بـ require_any بس — يعني "viewer" يقدر ينشئ/يعدّل/يمسح
# معدات وبنود BOM بطلب API مباشر. الواجهة بتربط الكتابة بـ canWrite('inventory')
# (admin/production) فنطبّق نفس القاعدة هنا. القراءة مفتوحة لأي مستخدم مسجّل.
EQUIPMENT_WRITE_ROLES = ("admin", "production")
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


async def require_equipment_write(request: Request, user: dict = Depends(require_any)) -> dict:
    if request.method.upper() in _READ_METHODS:
        return user
    if user.get("role") not in EQUIPMENT_WRITE_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"صلاحية '{user.get('role')}' غير كافية لتعديل المعدات. مطلوب: {list(EQUIPMENT_WRITE_ROLES)}",
        )
    return user


router = APIRouter(prefix="/equipment", tags=["equipment"],
                   dependencies=[Depends(require_any), Depends(require_equipment_write)])
print_router = APIRouter(prefix="/equipment", tags=["equipment-print"], dependencies=[Depends(require_any)])


# ── helpers ───────────────────────────────────────────────────────────────────

def _eq_dict(eq: Equipment, include_dims: bool = False, dims: list = None) -> dict:
    return {
        "id": eq.id,
        "code": eq.code,
        "name_ar": eq.name_ar,
        "name_en": eq.name_en,
        "description": getattr(eq, "description", None),
        "level": eq.level,
        "parent_id": eq.parent_id,
        "category_id": eq.category_id,
        "weight_kg": float(eq.weight_kg) if eq.weight_kg else None,
        "cad_drawing_no": eq.cad_drawing_no,
        "is_active": eq.is_active,
        "serial_number":  getattr(eq, "serial_number",  None),
        "equip_status":   getattr(eq, "equip_status",   "active"),
        "purchase_date":  str(eq.purchase_date)  if getattr(eq, "purchase_date",  None) else None,
        "purchase_invoice_id": getattr(eq, "purchase_invoice_id", None),
        "purchase_value": float(eq.purchase_value) if getattr(eq, "purchase_value", None) else None,
        "calc_unit":      getattr(eq, "calc_unit", "piece"),
        "base_qty":       float(eq.base_qty) if getattr(eq, "base_qty", None) else 1.0,
        "length_min":     float(eq.length_min) if getattr(eq, "length_min", None) is not None else None,
        "length_max":     float(eq.length_max) if getattr(eq, "length_max", None) is not None else None,
        "created_at": eq.created_at.isoformat() if eq.created_at else None,
        "updated_at": eq.updated_at.isoformat() if eq.updated_at else None,
        "dimensions": [{"id": d.id, "dim_key": d.dim_key, "dim_value": d.dim_value, "unit": d.unit}
                       for d in (dims or [])] if include_dims else [],
    }


async def _eq_or_404(db: AsyncSession, eq_id: int) -> Equipment:
    eq = await EquipmentService.get_tenant_equipment(db, eq_id)
    if eq is None:
        raise HTTPException(404, "Equipment not found")
    return eq


def _egp(v) -> str:
    try:
        return f"{float(v):,.2f} ج"
    except Exception:
        return "0.00 ج"


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_equipment(data: EquipmentCreate, db: AsyncSession = Depends(get_db)):
    try:
        eq = await EquipmentService.create_equipment(db, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "الكود مستخدم بالفعل — اختر كوداً مختلفاً")
    return _eq_dict(eq)


@router.post("/{eq_id}/duplicate", status_code=201)
async def duplicate_equipment(eq_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    """نسخ معدة مع كل فرعياتها وبنود BOM الخاصة بكل مستوى، بكود جديد"""
    new_code = (data or {}).get("code")
    if not isinstance(new_code, str) or not new_code.strip():
        raise HTTPException(400, "أدخل كوداً جديداً للمعدة المنسوخة")
    try:
        eq = await EquipmentService.duplicate_equipment(db, eq_id, new_code, (data or {}).get("name_ar"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "الكود مستخدم بالفعل — اختر كوداً مختلفاً")
    return _eq_dict(eq)


@router.get("")
async def list_root_equipment(db: AsyncSession = Depends(get_db)):
    eqs = await EquipmentService.list_root_equipment(db)
    return [_eq_dict(e) for e in eqs]


@router.get("/list-all")
async def list_all_equipment(db: AsyncSession = Depends(get_db)):
    """Flat list of all equipment — used by dropdowns."""
    from app.core.database import current_tenant_id as _ctid_lae
    result = await db.execute(
        select(Equipment).where(Equipment.tenant_id == (_ctid_lae.get() or 1)).order_by(Equipment.level, Equipment.id)
    )
    eqs = result.scalars().all()
    return [{"id": e.id, "code": e.code, "name_ar": e.name_ar,
             "name_en": e.name_en, "level": e.level, "parent_id": e.parent_id,
             "category_id": e.category_id,
             "calc_unit": getattr(e, "calc_unit", "piece"),
             "base_qty": float(e.base_qty) if getattr(e, "base_qty", None) else 1.0,
             "length_min": float(e.length_min) if getattr(e, "length_min", None) is not None else None,
             "length_max": float(e.length_max) if getattr(e, "length_max", None) is not None else None,
             "is_active": e.is_active} for e in eqs]


# ── Categories ────────────────────────────────────────────────────────────────

@router.get("/categories/list")
async def list_equipment_categories(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func
    from app.core.database import current_tenant_id as _ctid_lec
    tid = _ctid_lec.get() or 1
    result = await db.execute(
        select(EquipmentCategory).where(EquipmentCategory.tenant_id == tid).order_by(EquipmentCategory.name_ar)
    )
    cats = result.scalars().all()
    out = []
    for c in cats:
        cnt = await db.execute(select(func.count()).select_from(Equipment)
                               .where(Equipment.category_id == c.id, Equipment.tenant_id == tid))
        out.append({"id": c.id, "name_ar": c.name_ar, "name_en": c.name_en, "equipment_count": cnt.scalar() or 0})
    return out


@router.post("/categories", status_code=201)
async def create_equipment_category(data: dict, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id
    name_ar = (data.get("name_ar") or "").strip()
    if not name_ar:
        raise HTTPException(400, "اسم التصنيف مطلوب")
    name_en_in = data.get("name_en")
    if len(name_ar) > 120 or (isinstance(name_en_in, str) and len(name_en_in.strip()) > 120):
        raise HTTPException(400, "اسم التصنيف أطول من المسموح (120 حرف)")
    tid = current_tenant_id.get() or 1
    existing = await db.execute(
        select(EquipmentCategory).where(EquipmentCategory.name_ar == name_ar, EquipmentCategory.tenant_id == tid)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "التصنيف موجود بالفعل")
    cat = EquipmentCategory(tenant_id=tid, name_ar=name_ar, name_en=(str(data.get("name_en") or "").strip() or name_ar))
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return {"id": cat.id, "name_ar": cat.name_ar, "name_en": cat.name_en, "equipment_count": 0}


@router.delete("/categories/{category_id}", status_code=204)
async def delete_equipment_category(category_id: int, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func
    from app.core.database import current_tenant_id as _ctid_dec
    cat = await db.get(EquipmentCategory, category_id)
    if not cat or cat.tenant_id != (_ctid_dec.get() or 1):
        raise HTTPException(404, "التصنيف غير موجود")
    cnt = await db.execute(select(func.count()).select_from(Equipment)
                           .where(Equipment.category_id == category_id, Equipment.tenant_id == cat.tenant_id))
    if (cnt.scalar() or 0) > 0:
        raise HTTPException(409, "لا يمكن حذف التصنيف لأنه يحتوي على معدات. نقل المعدات أولاً.")
    await db.delete(cat)
    await db.commit()


@router.get("/tree/all")
async def get_all_equipment_trees(db: AsyncSession = Depends(get_db)):
    return await EquipmentService.get_all_trees(db)


@router.get("/{eq_id}")
async def get_equipment(eq_id: int, db: AsyncSession = Depends(get_db)):
    eq = await EquipmentService.get_equipment(db, eq_id)
    if not eq:
        raise HTTPException(404, "Equipment not found")
    return _eq_dict(eq, include_dims=True, dims=eq.dimensions)


@router.get("/{eq_id}/tree")
async def get_equipment_tree(eq_id: int, db: AsyncSession = Depends(get_db)):
    tree = await EquipmentService.get_full_tree(db, eq_id)
    if not tree:
        raise HTTPException(404, "Equipment not found")
    return tree


@router.put("/{eq_id}")
async def update_equipment(eq_id: int, data: EquipmentCreate, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_ue
    eq = await db.get(Equipment, eq_id)
    if not eq or eq.tenant_id != (_ctid_ue.get() or 1):
        raise HTTPException(404, "Equipment not found")
    try:
        # الحقول اللي ما اتبعتتش تفضل بقيمتها الحالية (بدل ما تتمسح لـ default
        # الـ schema) — نبني payload كامل من القيم الحالية + المُرسَل فقط.
        sent = data.model_dump(exclude_unset=True)
        payload = {k: getattr(eq, k, None) for k in EquipmentCreate.model_fields}
        payload.update(sent)
        if not payload.get("code"):
            payload["code"] = eq.code  # حافظ على الكود الموجود لو لم يُرسَل
        old_parent_id = eq.parent_id
        # تحقق: أب من نفس الـ tenant وبدون حلقة، تصنيف صالح، قيم غير سالبة.
        # الـ level لا يُقبل من الفرونت — يُحسب من الأب.
        try:
            await EquipmentService.validate_equipment_payload(db, payload, eq.tenant_id, eq_id=eq.id, existing=eq)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not payload.get("name_en"):
            payload["name_en"] = payload.get("name_ar", "")

        # محطة 23: نفس منطق الإنشاء — الفاتورة المرتبطة هي مصدر الحقيقة
        if payload.get("purchase_invoice_id"):
            from app.models.models import PurchaseInvoice
            inv = await db.get(PurchaseInvoice, payload["purchase_invoice_id"])
            if not inv or inv.tenant_id != eq.tenant_id:
                raise HTTPException(400, "فاتورة الشراء المرتبطة غير موجودة")
            payload["purchase_date"] = inv.date
            payload["purchase_value"] = inv.total

        for key, value in payload.items():
            if hasattr(eq, key):
                setattr(eq, key, value)
        if eq.parent_id != old_parent_id:
            await EquipmentService.refresh_descendant_levels(db, eq)
        await db.commit()
        await db.refresh(eq)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "الكود مستخدم بالفعل")
    return _eq_dict(eq)


@router.patch("/{eq_id}/description")
async def update_equipment_description(eq_id: int, body: dict, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_ued
    eq = await db.get(Equipment, eq_id)
    if not eq or eq.tenant_id != (_ctid_ued.get() or 1):
        raise HTTPException(404, "Equipment not found")
    desc = (body or {}).get("description")
    if desc is not None and not isinstance(desc, str):
        raise HTTPException(400, "الوصف يجب أن يكون نصاً")
    eq.description = desc or None
    await db.commit()
    return {"id": eq.id, "description": eq.description}


@router.delete("/{eq_id}")
async def delete_equipment(eq_id: int, db: AsyncSession = Depends(get_db)):
    import os
    from pathlib import Path
    from app.core.database import current_tenant_id as _ctid_de
    eq = await db.get(Equipment, eq_id)
    if not eq or eq.tenant_id != (_ctid_de.get() or 1):
        raise HTTPException(404, "Equipment not found")
    tid = eq.tenant_id

    # ممنوع حذف معدة ليها فرعيات (كانت بتتيتّم وتختفي من الشجرة) أو مستخدمة
    # في مستندات (أوامر شغل/طلبيات/أصول عملاء/أذون تسليم/مشاريع/كتالوج أسعار).
    from sqlalchemy import func as _f
    from app.models.models import (
        WorkOrder, CustomerOrder, CustomerOrderLine, CustomerAsset,
        DeliveryOrderLine, ExtProject, PriceCatalogItem,
    )
    kids = await db.scalar(select(_f.count()).select_from(Equipment)
                           .where(Equipment.parent_id == eq_id, Equipment.tenant_id == tid))
    if kids:
        raise HTTPException(409, f"لا يمكن حذف المعدة لأنها تحتوي على {kids} معدة فرعية — احذف الفرعيات أو انقلها أولاً")
    dependents = [
        (WorkOrder, WorkOrder.equipment_id, "أوامر شغل"),
        (CustomerOrder, CustomerOrder.equipment_id, "طلبيات عملاء"),
        (CustomerOrderLine, CustomerOrderLine.equipment_id, "بنود طلبيات"),
        (CustomerAsset, CustomerAsset.equipment_id, "أصول عملاء"),
        (DeliveryOrderLine, DeliveryOrderLine.equipment_id, "أذون تسليم"),
        (ExtProject, ExtProject.equipment_id, "مشاريع خارجية"),
    ]
    for Model, col, label in dependents:
        conds = [col == eq_id]
        if hasattr(Model, "tenant_id"):  # DeliveryOrderLine مفيهاش tenant_id (equipment_id نفسه فريد عالمياً)
            conds.append(Model.tenant_id == tid)
        n = await db.scalar(select(_f.count()).select_from(Model).where(*conds))
        if n:
            raise HTTPException(409, f"لا يمكن حذف المعدة لأنها مستخدمة في {n} من {label}")
    n = await db.scalar(select(_f.count()).select_from(PriceCatalogItem).where(
        PriceCatalogItem.source_type == "equipment", PriceCatalogItem.source_id == eq_id,
        PriceCatalogItem.tenant_id == tid))
    if n:
        raise HTTPException(409, f"لا يمكن حذف المعدة لأنها مربوطة بـ {n} من بنود كتالوج الأسعار")

    cad_result = await db.execute(select(CADFile).where(CADFile.equipment_id == eq_id, CADFile.tenant_id == tid))
    cad_files = cad_result.scalars().all()
    stored_names = [cf.stored_name for cf in cad_files if cf.stored_name]
    for cf in cad_files:
        await db.delete(cf)

    # Delete dimensions, BOM lines, cost lines
    for Model, col in [(EquipmentDimension, EquipmentDimension.equipment_id),
                       (BOMLine, BOMLine.equipment_id),
                       (EquipmentCostLine, EquipmentCostLine.equipment_id)]:
        res = await db.execute(select(Model).where(col == eq_id, Model.tenant_id == tid))
        for obj in res.scalars().all():
            await db.delete(obj)

    await db.delete(eq)
    await db.commit()

    # حذف ملفات CAD من الـ disk بعد نجاح الـ commit فقط (مش قبله)، وباسم
    # الملف فقط — بدون أي مسار — عشان مفيش path traversal من stored_name.
    upload_dir = (Path(os.getenv("ERP_UPLOAD_DIR", "/app/uploads")) / "cad").resolve()
    for name in stored_names:
        try:
            fp = (upload_dir / Path(name).name).resolve()
            if fp.parent == upload_dir and fp.is_file():
                fp.unlink()
        except Exception:
            pass
    return {"message": "تم الحذف", "id": eq_id}


# ── BOM endpoints ─────────────────────────────────────────────────────────────

@router.get("/{eq_id}/bom")
async def get_bom(eq_id: int, db: AsyncSession = Depends(get_db)):
    await _eq_or_404(db, eq_id)
    try:
        lines = await EquipmentService.get_bom(db, eq_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return [
        {
            "id": l.id, "equipment_id": l.equipment_id,
            "material_id": l.material_id, "qty": float(l.qty),
            "unit_cost": float(l.unit_cost), "total_cost": float(l.total_cost),
            "notes": l.notes,
            "dim_length_cm": float(l.dim_length_cm) if l.dim_length_cm else None,
            "dim_width_cm": float(l.dim_width_cm) if l.dim_width_cm else None,
            "dim_count": l.dim_count,
            "dim_area_m2": float(l.dim_area_m2) if l.dim_area_m2 else None,
            "calc_weight_kg": float(l.calc_weight_kg) if l.calc_weight_kg else None,
            "bar_length_cm": float(l.bar_length_cm) if l.bar_length_cm else None,
            "waste_g": float(l.waste_g) if l.waste_g else None,
            "line_type": l.line_type,
            "scale_dim_length": l.scale_dim_length,
            "material": {
                "id": l.material.id, "code": l.material.code,
                "name_ar": l.material.name_ar, "unit": l.material.unit,
                "unit_cost": float(l.material.unit_cost),
                "material_kind": l.material.material_kind,
                "is_plate": l.material.is_plate,
                "density_kg_m2": float(l.material.density_kg_m2) if l.material.density_kg_m2 else None,
                "weight_per_meter_kg": float(l.material.weight_per_meter_kg) if l.material.weight_per_meter_kg else None,
            } if l.material else None,
        }
        for l in lines
    ]


@router.get("/{eq_id}/bom/cost")
async def bom_cost_breakdown(eq_id: int, db: AsyncSession = Depends(get_db)):
    """BOM cost for quantity=1 (standard)."""
    await _eq_or_404(db, eq_id)
    try:
        return await EquipmentService.calculate_bom_cost(db, eq_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{eq_id}/bom/parametric")
async def bom_parametric(eq_id: int, quantity: float = 1.0, db: AsyncSession = Depends(get_db)):
    """BOM cost multiplied by quantity — for parametric/variable equipment."""
    await _eq_or_404(db, eq_id)
    if quantity is None or quantity <= 0:
        raise HTTPException(400, "الكمية يجب أن تكون أكبر من صفر")
    try:
        return await EquipmentService.calculate_bom_cost(db, eq_id, Decimal(str(quantity)))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{eq_id}/bom/parametric-length")
async def bom_parametric_length(eq_id: int, length: float, quantity: float = 1.0, db: AsyncSession = Depends(get_db)):
    """BOM cost for a chosen length L within [length_min, length_max] — معدات مترية (بريمات/سيور/كتاين)."""
    await _eq_or_404(db, eq_id)
    if quantity is None or quantity <= 0:
        raise HTTPException(400, "الكمية يجب أن تكون أكبر من صفر")
    try:
        return await EquipmentService.calculate_bom_cost_for_length(db, eq_id, Decimal(str(length)), Decimal(str(quantity)))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/bom", status_code=201)
async def add_bom_line(data: BOMLineCreate, db: AsyncSession = Depends(get_db)):
    try:
        line = await EquipmentService.add_bom_line(db, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": line.id, "equipment_id": line.equipment_id,
        "material_id": line.material_id, "qty": float(line.qty),
        "unit_cost": float(line.unit_cost), "total_cost": float(line.total_cost),
        "calc_weight_kg": float(line.calc_weight_kg) if line.calc_weight_kg else None,
    }


@router.patch("/bom/{line_id}")
async def update_bom_line(line_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_ubl
    line = await db.get(BOMLine, line_id)
    if not line or line.tenant_id != (_ctid_ubl.get() or 1):
        raise HTTPException(404, "BOM line not found")
    allowed = {'qty', 'unit_cost', 'notes', 'bar_length_cm', 'dim_length_cm',
               'dim_width_cm', 'dim_count', 'line_type', 'scale_dim_length'}
    numeric = {'qty', 'unit_cost', 'bar_length_cm', 'dim_length_cm', 'dim_width_cm'}
    clean = {}
    for k, v in (data or {}).items():
        if k not in allowed:
            continue
        if v is not None and k in numeric:
            try:
                v = Decimal(str(v))
            except Exception:
                raise HTTPException(400, f"قيمة {k} غير صالحة")
            if not v.is_finite():
                raise HTTPException(400, f"قيمة {k} غير صالحة")
        elif v is not None and k == 'dim_count':
            try:
                v = int(v)
            except Exception:
                raise HTTPException(400, "العدد يجب أن يكون رقماً صحيحاً")
        elif v is not None and k == 'scale_dim_length':
            v = bool(v)
        elif v is not None and k in ('notes', 'line_type') and not isinstance(v, str):
            raise HTTPException(400, f"قيمة {k} يجب أن تكون نصاً")
        clean[k] = v
    if 'qty' in clean and (clean['qty'] is None or clean['qty'] <= 0):
        raise HTTPException(400, "الكمية يجب أن تكون أكبر من صفر")
    if 'unit_cost' in clean and clean['unit_cost'] is None:
        raise HTTPException(400, "سعر الوحدة مطلوب")
    if 'line_type' in clean and clean['line_type'] is None:
        clean['line_type'] = 'fixed'
    try:
        EquipmentService.validate_bom_values(clean)
    except ValueError as e:
        raise HTTPException(400, str(e))
    for k, v in clean.items():
        if hasattr(line, k):
            setattr(line, k, v)
    from decimal import Decimal as D
    line.total_cost = D(str(line.qty or 0)) * D(str(line.unit_cost or 0))
    await db.commit()
    await db.refresh(line)
    return {"id": line.id, "qty": float(line.qty), "unit_cost": float(line.unit_cost),
            "total_cost": float(line.total_cost), "notes": line.notes}


@router.delete("/bom/{line_id}")
async def delete_bom_line(line_id: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_dbl
    line = await db.get(BOMLine, line_id)
    if not line or line.tenant_id != (_ctid_dbl.get() or 1):
        raise HTTPException(404, "BOM line not found")
    await db.delete(line)
    await db.commit()
    return {"message": "تم الحذف", "id": line_id}


# ── Dimensions ────────────────────────────────────────────────────────────────

@router.post("/{eq_id}/dimensions")
async def add_dimension(eq_id: int, dim_key: str, dim_value: str, unit: str,
                        db: AsyncSession = Depends(get_db)):
    try:
        dim = await EquipmentService.add_dimension(db, eq_id, dim_key, dim_value, unit)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": dim.id, "equipment_id": dim.equipment_id, "dim_key": dim.dim_key,
            "dim_value": dim.dim_value, "unit": dim.unit}


@router.delete("/dimension/{dim_id}")
async def delete_dimension(dim_id: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_dd
    dim = await db.get(EquipmentDimension, dim_id)
    if not dim or dim.tenant_id != (_ctid_dd.get() or 1):
        raise HTTPException(404, "Dimension not found")
    await db.delete(dim)
    await db.commit()
    return {"message": "تم الحذف", "id": dim_id}


# ── EquipmentCostLine CRUD ────────────────────────────────────────────────────

@router.get("/{eq_id}/cost-lines")
async def list_cost_lines(eq_id: int, db: AsyncSession = Depends(get_db)):
    await _eq_or_404(db, eq_id)
    lines = await EquipmentService.list_cost_lines(db, eq_id)
    return [
        {
            "id": l.id,
            "equipment_id": l.equipment_id,
            "cost_type": l.cost_type,
            "description": l.description,
            "cost_per_unit": float(l.cost_per_unit),
        }
        for l in lines
    ]


@router.post("/{eq_id}/cost-lines", status_code=201)
async def add_cost_line(eq_id: int, data: EquipmentCostLineCreate,
                        db: AsyncSession = Depends(get_db)):
    if data.equipment_id != eq_id:
        data = data.model_copy(update={"equipment_id": eq_id})
    try:
        line = await EquipmentService.add_cost_line(
            db, eq_id, data.cost_type, data.description, data.cost_per_unit
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": line.id,
        "equipment_id": line.equipment_id,
        "cost_type": line.cost_type,
        "description": line.description,
        "cost_per_unit": float(line.cost_per_unit),
    }


@router.delete("/cost-lines/{line_id}")
async def delete_cost_line(line_id: int, db: AsyncSession = Depends(get_db)):
    try:
        await EquipmentService.delete_cost_line(db, line_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"message": "تم الحذف", "id": line_id}


# ── Print BOM ─────────────────────────────────────────────────────────────────

@print_router.get("/{eq_id}/bom/print", response_class=HTMLResponse)
async def print_bom(eq_id: int, length: float = None, db: AsyncSession = Depends(get_db)):
    eq = await _eq_or_404(db, eq_id)

    try:
        if eq.length_min is not None and length is not None:
            result = await EquipmentService.calculate_bom_cost_for_length(db, eq_id, Decimal(str(length)))
        else:
            result = await EquipmentService.calculate_bom_cost(db, eq_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    lines = result.get("lines", [])
    cost_lines = result.get("cost_lines", [])
    materials_summary = result.get("materials_summary", [])
    total_weight = sum(l.get("calc_weight_kg") or 0 for l in lines)
    material_cost = result.get("material_cost", 0.0)
    fixed_cost = result.get("fixed_cost", 0.0)
    total_cost = material_cost + fixed_cost

    def _dims_html(l):
        if l.get("dim_length_cm") and l.get("dim_width_cm"):
            area = f' — {l["dim_area_m2"]} م²' if l.get("dim_area_m2") else ''
            return f'{l.get("dim_count") or 1} × {l["dim_length_cm"]}×{l["dim_width_cm"]} سم{area}'
        if l.get("bar_length_cm"):
            return f'{l.get("dim_count") or 1} × {l["bar_length_cm"]} سم'
        return '—'

    # تجميع البنود حسب المعدة (أب/فرعي) — لتظهر القطع تحت كل مكوّن في الشجرة
    by_eq = {}
    eq_order = []
    for l in lines:
        eqi = l.get("equipment") or {}
        eqid = eqi.get("id", 0)
        if eqid not in by_eq:
            by_eq[eqid] = {"info": eqi, "rows": []}
            eq_order.append(eqid)
        by_eq[eqid]["rows"].append(l)

    sections_html = ""
    for eqid in eq_order:
        grp = by_eq[eqid]
        info = grp["info"]
        sections_html += f"""
        <tr class="eq-row">
          <td colspan="8">{'— ' * (info.get('level') or 0)}<b>{_esc(str(info.get('code') or '—'))}</b> — {_esc(str(info.get('name_ar') or '—'))}</td>
        </tr>"""
        for l in grp["rows"]:
            mat = l.get("material") or {}
            line_type_lbl = 'متغير/م' if l.get("line_type") == 'variable' else 'ثابت'
            sections_html += f"""
        <tr>
          <td class="font-mono">{_esc(str(mat.get('code') or '—'))}</td>
          <td>{_esc(str(mat.get('name_ar') or '—'))}</td>
          <td style="text-align:center">{round(l.get('qty', 0), 3)}</td>
          <td style="text-align:center">{_esc(str(mat.get('unit') or '—'))}</td>
          <td style="text-align:center">{_esc(_dims_html(l))}</td>
          <td>{_egp(l.get('unit_cost', 0))}</td>
          <td class="font-bold">{_egp(l.get('total_cost', 0))}</td>
          <td style="text-align:center">{line_type_lbl}</td>
        </tr>"""
    if not sections_html:
        sections_html = '<tr><td colspan="8" style="text-align:center;color:#9ca3af;padding:20px">لا توجد بنود في قائمة المواد</td></tr>'

    summary_rows_html = ""
    for s in materials_summary:
        warn = f'<div style="color:#b91c1c;font-size:9px;margin-top:2px">⚠ {_esc(str(s["oversized_warning"]))}</div>' if s.get("oversized_warning") else ''
        frac = f' ({s["fraction_pct"]}%)' if s.get("fraction_pct") else ''
        summary_rows_html += f"""
        <tr>
          <td class="font-mono">{_esc(str(s['material_code'] or ''))}</td>
          <td>{_esc(str(s['material_name'] or ''))}</td>
          <td class="font-bold">{s['count']} {_esc(str(s['unit_label'] or ''))}{frac}{warn}</td>
        </tr>"""

    length_badge = f'<div class="sub">عند طول مُختار: {length} {_esc(eq.calc_unit or "")}</div>' if length is not None else ''

    html = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<title>تقرير تصنيع — {_esc(eq.name_ar or '')}</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  @page {{ size: A4 landscape; margin: 12mm }}
  * {{ box-sizing: border-box; margin: 0; padding: 0 }}
  body {{ font-family: 'IBM Plex Sans Arabic', Arial, sans-serif; font-size: 12px; color: #1e293b; background: #fff }}
  .header {{ background: #064e3b; color: #fff; padding: 18px 24px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; border-radius: 8px 8px 0 0 }}
  .header h1 {{ font-size: 20px; font-weight: 700 }}
  .header .sub {{ font-size: 11px; opacity: .85; margin-top: 4px }}
  .kpi-row {{ display: flex; gap: 12px; margin-bottom: 16px }}
  .kpi {{ flex: 1; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 12px; text-align: center }}
  .kpi .val {{ font-size: 16px; font-weight: 700; color: #064e3b }}
  .kpi .lbl {{ font-size: 10px; color: #6b7280; margin-top: 3px }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 18px }}
  th {{ background: #064e3b; color: #fff; padding: 7px 8px; text-align: right; font-size: 10px; font-weight: 600 }}
  td {{ padding: 7px 8px; border-bottom: 1px solid #f1f5f9; font-size: 11px }}
  tr:last-child td {{ border-bottom: none }}
  tr.eq-row td {{ background: #ecfdf5; font-size: 11px; padding: 6px 8px }}
  .font-bold {{ font-weight: 700 }}
  .font-mono {{ font-family: monospace; font-size: 10px; color: #374151 }}
  h2 {{ font-size: 13px; margin: 0 0 8px; color: #064e3b }}
  @media print {{ * {{ -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important }} }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>تقرير تصنيع تفصيلي</h1>
    <div class="sub">{_esc(eq.code or '')} — {_esc(eq.name_ar or '')}</div>
    {length_badge}
  </div>
  <div style="text-align:left;font-size:11px;opacity:.85">تاريخ الطباعة: {_date.today().strftime('%Y/%m/%d')}</div>
</div>

<div class="kpi-row">
  <div class="kpi"><div class="val">{len(lines)}</div><div class="lbl">عدد البنود</div></div>
  <div class="kpi"><div class="val">{_egp(material_cost)}</div><div class="lbl">تكلفة المواد</div></div>
  <div class="kpi"><div class="val">{_egp(fixed_cost)}</div><div class="lbl">تكاليف ثابتة</div></div>
  <div class="kpi"><div class="val">{_egp(total_cost)}</div><div class="lbl">الإجمالي</div></div>
  <div class="kpi"><div class="val">{round(total_weight, 2)} كجم</div><div class="lbl">الوزن الكلي المحسوب</div></div>
</div>

<h2>قائمة المواد والمقاسات (لكل مكوّن)</h2>
<table>
  <thead>
    <tr>
      <th style="width:9%">الكود</th>
      <th style="width:18%">اسم المادة</th>
      <th style="width:8%">الكمية/الوزن</th>
      <th style="width:7%">الوحدة</th>
      <th style="width:24%">المقاسات (للتصنيع)</th>
      <th style="width:10%">سعر الوحدة</th>
      <th style="width:10%">الإجمالي</th>
      <th style="width:8%">النوع</th>
    </tr>
  </thead>
  <tbody>
    {sections_html}
  </tbody>
</table>

{f'''<h2>ملخص المواد المطلوبة (ألواح/أسياخ كاملة)</h2>
<table>
  <thead><tr><th style="width:15%">الكود</th><th style="width:45%">المادة</th><th>الإجمالي المطلوب</th></tr></thead>
  <tbody>{summary_rows_html}</tbody>
</table>''' if summary_rows_html else ''}

<script>window.onload = () => window.print();</script>
</body>
</html>"""
    return HTMLResponse(content=html)
