"""
Production Service — التكامل الأوتوماتيكي للإنتاج
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    WorkOrder, Material, BOMLine, Equipment,
    MaterialReservation, ScrapRecord, StockMovement,
    CostLine, MrpSuggestion, WorkOrderBOMLine,
    CustomerOrder as _CustomerOrder,
)


# ═══════════════════════════════════════════════════════════════
# محطة 16 (الروادماب الأصلي، مكتملة): حساب رصيد المادة بالموقع الفعلي
# ═══════════════════════════════════════════════════════════════

async def _get_raw_and_imported_qty(db: AsyncSession, tenant_id: int, material_id: int) -> Decimal:
    """
    محطة 16 (الروادماب الأصلي، مكتملة): رصيد المادة الفعلي في مواقع RAW
    وIMPORTED فقط (المكانين الشرعيين الوحيدين لمخزون مادة خام) — بدل
    Material.stock_qty المجرَّد. آمنة تُستخدم بعد توحيد DEFAULT مع RAW.
    """
    from app.models.models import StockQuant, BinLocation
    result = await db.execute(
        select(func.coalesce(func.sum(StockQuant.qty), 0))
        .join(BinLocation, BinLocation.id == StockQuant.location_id)
        .where(
            StockQuant.material_id == material_id,
            StockQuant.tenant_id == tenant_id,
            BinLocation.code.in_(["RAW-DEFAULT", "IMPORTED-DEFAULT"]),
        )
    )
    total = result.scalar() or Decimal("0")
    return Decimal(str(total))


# ═══════════════════════════════════════════════════════════════
# STEP 1: حجز المواد أوتوماتيك عند إنشاء أمر الإنتاج
# ═══════════════════════════════════════════════════════════════

async def auto_reserve_materials(db: AsyncSession, work_order_id: int) -> dict:
    """
    يقرأ BOM للمعدة → يضرب في wo.quantity (BOM بارامتري) →
    ينشئ حجوزات → يخصم من المخزن (soft lock) →
    يُنشئ snapshot في work_order_bom_lines.
    Returns: {"reserved": N, "shortages": [...], "blocked": bool}
    """
    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        return {"error": "أمر الإنتاج غير موجود"}
    if wo.status in ("done", "cancelled"):
        # حجز مواد لأمر مكتمل/ملغي كان بيعمل soft-lock على المخزون للأبد
        # (الحجوزات دي مبتتقفلش تاني) ويضخّم احتياجات MRP بلا داعي
        return {"error": f"أمر الإنتاج {wo.code} {'مكتمل' if wo.status == 'done' else 'ملغي'} — لا يمكن حجز مواد له"}

    if not wo.equipment_id:
        return {
            "error": "أمر الإنتاج لا يحتوي على معدة محددة — لا يمكن جلب BOM تلقائياً",
            "reserved": 0, "shortages": [],
        }

    # الكمية المطلوبة للإنتاج (مضروب فيها كل بند BOM)
    wo_qty = wo.quantity if wo.quantity and wo.quantity > 0 else Decimal("1")

    # إذا كان الأمر مترياً وتم تطبيق apply-parametric-bom، نستخدم WorkOrderBOMLine مباشرة
    existing_wo_bom = (await db.execute(
        select(WorkOrderBOMLine).where(WorkOrderBOMLine.work_order_id == work_order_id)
    )).scalars().all()

    use_parametric_snapshot = len(existing_wo_bom) > 0 and wo.planned_length

    if use_parametric_snapshot:
        # الكميات في WorkOrderBOMLine محسوبة بالفعل بالطول الصح — لا نضرب في wo_qty مرة أخرى
        bom_lines = [
            {'material_id': l.material_id, 'qty': float(l.required_qty), 'unit_cost': float(l.unit_cost)}
            for l in existing_wo_bom
        ]
    else:
        # جلب BOM كاملة (recursive) — الكميات هنا لبند واحد فقط
        bom_lines = await _get_full_bom(db, wo.equipment_id)

    if not bom_lines:
        return {"error": "لا توجد بنود BOM لهذه المعدة", "reserved": 0, "shortages": []}

    # حذف الحجوزات القديمة لو موجودة — لكن فقط اللي لسه ما اتصرفش/اتهلكش منها
    # حاجة خالص. محطة 27 (تصليح جذري): كانت بتتمسح كل الحجوزات بلا استثناء،
    # يعني إعادة استدعاء auto-reserve/reserve على أمر إنتاج بعد ما اتصرف
    # منه جزء فعليًا كانت بتمسح issued_qty/scrap_qty المسجّلة وتبني حجز جديد
    # بـissued_qty=0 — فأي issue_material بعد كده كان ممكن يصرف نفس الكمية
    # تاني (لأن الحجز الجديد ملوش أي أثر للصرف السابق)، رغم إن المخزون
    # اتخصم منه فعليًا مرة واحدة بس. الحل: حجز فيه أي تقدم فعلي (صرف أو
    # هالك) يتسيب زي ما هو تمامًا — مش يتمسح ولا يتكرر.
    old_res = (await db.execute(
        select(MaterialReservation).where(MaterialReservation.work_order_id == work_order_id)
    )).scalars().all()
    preserved_material_ids = set()
    for r in old_res:
        if (r.issued_qty or Decimal("0")) > 0 or (r.scrap_qty or Decimal("0")) > 0:
            preserved_material_ids.add(r.material_id)
        else:
            await db.delete(r)

    # حذف snapshot القديم فقط إذا لم نكن نستخدمه (في حالة غير المترية)
    if not use_parametric_snapshot:
        old_snap = (await db.execute(
            select(WorkOrderBOMLine).where(WorkOrderBOMLine.work_order_id == work_order_id)
        )).scalars().all()
        for s in old_snap:
            await db.delete(s)

    await db.flush()

    reservations_created = []
    shortages = []

    from app.core.database import current_tenant_id as _ctid_prod
    tid = _ctid_prod.get() or 1

    # محطة 16 (الروادماب الأصلي): موقع الحجز — مخزن RAW دايمًا حاليًا (تحصين
    # هيكلي/تتبعي، مش فرض توفر بالموقع — راجع الملاحظة في الموديل)
    from app.services.inventory_ledger import get_or_create_warehouse_bin
    raw_bin = await get_or_create_warehouse_bin(db, tid, "RAW")

    for line in bom_lines:
        mat = await db.get(Material, line['material_id'])
        if not mat:
            continue

        # لو الحجز القديم لهذه المادة فيه صرف/هالك فعلي، سايبينه زي ما هو
        # (محطة 27 أعلاه) — تكراره هنا هيعمل صف مكرر لنفس المادة بلا داعي
        if line['material_id'] in preserved_material_ids:
            continue

        # للـ snapshot المتري، الكمية جاهزة. للـ BOM العادي، نضرب في wo_qty
        required = Decimal(str(line['qty'])) if use_parametric_snapshot else Decimal(str(line['qty'])) * wo_qty

        # المتاح = المخزون الفعلي - ما هو محجوز لأوامر أخرى لم يُصرف بعد
        other_reserved = (await db.execute(
            select(MaterialReservation).where(
                MaterialReservation.material_id == line['material_id'],
                MaterialReservation.work_order_id != work_order_id,
                MaterialReservation.status.in_(['pending', 'partial', 'reserved', 'partially_issued']),
            )
        )).scalars().all()
        committed = sum((r.reserved_qty - r.issued_qty) for r in other_reserved) if other_reserved else Decimal("0")

        # محطة 16 (الروادماب الأصلي، مكتملة): التوفر بقى بيتحقق من الرصيد
        # الفعلي في مواقع RAW+IMPORTED (مش من Material.stock_qty الإجمالي
        # المجرَّد). ده بقى آمن دلوقتي بعد توحيد "DEFAULT" مع "RAW" (تصليح
        # جذري) — قبل كده كان لازم يفضل أي مادة داخلة من شاشة الحركات
        # العامة (موقع DEFAULT منفصل) هترفض غلط بحجة عدم التوفر. المواد
        # الخام مالهاش مكان شرعي تاني غير RAW/IMPORTED (الـWIP/FG/SCRAP
        # مواد تانية تمامًا بأكواد مختلفة)، فالمجموع ده أدق من stock_qty
        # المجرَّد ولو حصل انحراف/عدم اتساق (نادر بعد كل الإصلاحات السابقة).
        location_qty = await _get_raw_and_imported_qty(db, tid, mat.id)
        available = location_qty - committed
        if available < 0:
            available = Decimal("0")

        # إضافة النسالة المتاحة للمواد الأبعادية (ألواح/أسياخ)
        if mat.is_plate:
            from app.models.models import SheetStockItem as _SSI
            remnant_rows = (await db.execute(
                select(_SSI).where(_SSI.material_id == mat.id, _SSI.qty > 0)
            )).scalars().all()
            remnant_area = sum(
                Decimal(str(float(r.length_cm) * float(r.width_cm) / 10000 * r.qty))
                for r in remnant_rows
            )
            available += remnant_area
        elif mat.bar_length_cm and mat.weight_per_meter_kg:
            from app.models.models import BarStockItem as _BSI
            bar_rows = (await db.execute(
                select(_BSI).where(_BSI.material_id == mat.id, _BSI.qty > 0)
            )).scalars().all()
            remnant_weight = sum(
                Decimal(str(float(r.length_cm) / 100 * float(mat.weight_per_meter_kg) * r.qty))
                for r in bar_rows
            )
            available += remnant_weight
        can_reserve = min(required, available)

        reservation = MaterialReservation(
            tenant_id=tid,
            work_order_id=work_order_id,
            material_id=line['material_id'],
            required_qty=required,
            reserved_qty=can_reserve,
            status='reserved' if can_reserve >= required else 'partial',
            reserved_at=date.today(),
            location_id=raw_bin.id,
        )
        db.add(reservation)

        if can_reserve < required:
            shortages.append({
                "material_id":   mat.id,
                "material_code": mat.code,
                "material_name": mat.name_ar,
                "required":      float(required),
                "available":     float(available),
                "shortage":      float(required - can_reserve),
            })

        # إنشاء سطر في snapshot BOM فقط إذا لم يكن موجوداً من apply-parametric-bom
        if not use_parametric_snapshot:
            unit_cost = mat.unit_cost
            db.add(WorkOrderBOMLine(
                tenant_id=tid,
                work_order_id=work_order_id,
                material_id=line['material_id'],
                required_qty=required,
                unit_cost=unit_cost,
                total_cost=required * unit_cost,
            ))

        reservations_created.append(line['material_id'])

    await db.commit()

    return {
        "reserved":      len(reservations_created),
        "shortages":     shortages,
        "blocked":       len(shortages) > 0,
        "work_order":    wo.code,
        "quantity_used": float(wo_qty),
    }


async def reserve_single_material(db: AsyncSession, work_order_id: int, material_id: int, qty: Decimal) -> dict:
    """
    محطة 27: حجز يدوي لمادة واحدة بكمية محددة — للاستخدام من شاشة "حجز
    يدوي" في الواجهة (بخلاف auto_reserve_materials اللي بيعيد بناء كل
    حجوزات BOM دفعة واحدة). لا يمس أي حجز آخر لنفس أمر الإنتاج.

    لو فيه حجز سابق لنفس المادة على نفس الأمر، بيزوّد عليه (required_qty
    += qty) بدل ما يستبدله أو يمسحه — نفس مبدأ الحماية المطبَّق في
    auto_reserve_materials (محطة 27 أعلاه) ضد فقد أثر issued_qty/scrap_qty.
    """
    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        return {"error": "أمر الإنتاج غير موجود"}
    if wo.status in ("done", "cancelled"):
        return {"error": f"أمر الإنتاج {wo.code} {'مكتمل' if wo.status == 'done' else 'ملغي'} — لا يمكن حجز مواد له"}
    mat = await db.get(Material, material_id)
    if not mat:
        return {"error": "المادة غير موجودة"}
    if qty is None or qty <= 0:
        return {"error": "الكمية المطلوب حجزها يجب أن تكون أكبر من صفر"}

    from app.core.database import current_tenant_id as _ctid_rsm
    tid = _ctid_rsm.get() or 1
    from app.services.inventory_ledger import get_or_create_warehouse_bin
    raw_bin = await get_or_create_warehouse_bin(db, tid, "RAW")

    res = (await db.execute(
        select(MaterialReservation).where(
            MaterialReservation.work_order_id == work_order_id,
            MaterialReservation.material_id == material_id,
        )
    )).scalar_one_or_none()

    # نفس منطق التوفر المستخدم في auto_reserve_materials: الرصيد الفعلي في
    # مواقع RAW+IMPORTED ناقص أي حجز آخر (لأوامر إنتاج غير هذا الأمر) لسه مفتوح
    other_reserved = (await db.execute(
        select(MaterialReservation).where(
            MaterialReservation.material_id == material_id,
            MaterialReservation.work_order_id != work_order_id,
            MaterialReservation.status.in_(['pending', 'partial', 'reserved', 'partially_issued']),
        )
    )).scalars().all()
    committed = sum((r.reserved_qty - r.issued_qty) for r in other_reserved) if other_reserved else Decimal("0")
    location_qty = await _get_raw_and_imported_qty(db, tid, mat.id)
    available = max(location_qty - committed, Decimal("0"))

    can_reserve_extra = min(qty, available)

    if res:
        res.required_qty = (res.required_qty or Decimal("0")) + qty
        res.reserved_qty = (res.reserved_qty or Decimal("0")) + can_reserve_extra
        res.status = 'reserved' if res.reserved_qty >= res.required_qty else 'partial'
        if not res.location_id:
            res.location_id = raw_bin.id
    else:
        res = MaterialReservation(
            tenant_id=tid,
            work_order_id=work_order_id,
            material_id=material_id,
            required_qty=qty,
            reserved_qty=can_reserve_extra,
            status='reserved' if can_reserve_extra >= qty else 'partial',
            reserved_at=date.today(),
            location_id=raw_bin.id,
        )
        db.add(res)

    shortage = max(qty - can_reserve_extra, Decimal("0"))
    await db.commit()
    await db.refresh(res)

    result = {
        "reserved":      1,
        "material_id":   mat.id,
        "material_code": mat.code,
        "requested_qty": float(qty),
        "reserved_qty":  float(can_reserve_extra),
        "shortage":      float(shortage),
        "shortages":     [{
            "material_id":   mat.id,
            "material_code": mat.code,
            "material_name": mat.name_ar,
            "required":      float(qty),
            "available":     float(available),
            "shortage":      float(shortage),
        }] if shortage > 0 else [],
        "blocked":         shortage > 0,
        "work_order":      wo.code,
        "reservation_id":  res.id,
    }
    return result


async def _get_full_bom(db: AsyncSession, equipment_id: int) -> List[dict]:
    """جلب BOM بشكل recursive مع تجميع نفس المواد"""
    result = {}

    async def collect(eq_id: int):
        lines = (await db.execute(
            select(BOMLine).where(BOMLine.equipment_id == eq_id)
        )).scalars().all()
        for l in lines:
            if l.material_id in result:
                result[l.material_id]['qty'] += float(l.qty)
            else:
                result[l.material_id] = {
                    'material_id': l.material_id,
                    'qty': float(l.qty),
                }

        # البحث عن المجموعات الفرعية
        from app.models.models import Equipment
        children = (await db.execute(
            select(Equipment).where(Equipment.parent_id == eq_id)
        )).scalars().all()
        for child in children:
            await collect(child.id)

    await collect(equipment_id)
    return list(result.values())


# ═══════════════════════════════════════════════════════════════
# STEP 2: صرف مواد في مرحلة إنتاج
# ═══════════════════════════════════════════════════════════════

async def _get_or_create_remnant_material(db: AsyncSession, tid: int, mat: Material):
    """
    محطة 24 (الروادماب الأصلي، تكملة النسالة): مادة مركّبة "REMNANT-{كود
    المادة}" بتمثّل كل رصيد SheetStockItem/BarStockItem (قطع منفصلة —
    نسالة من تقطيع، أو أسياخ كاملة مشتراة كقطع) لنفس المادة، في مخزن
    REMNANT الفعلي. نفس نمط WIP/FG/SCRAP بالظبط — مادة منفصلة عشان القيمة
    دي أصلًا غير محسوبة في Material.stock_qty ولا في أي StockQuant تاني.
    """
    from app.models.models import Material as _Mat, MaterialCategory as _Cat
    code = f"REMNANT-{mat.code}"
    rem_mat = await db.scalar(select(_Mat).where(_Mat.tenant_id == tid, _Mat.code == code))
    if rem_mat:
        return rem_mat
    cat = await db.scalar(select(_Cat).where(_Cat.tenant_id == tid, _Cat.name_en == "Remnant"))
    if not cat:
        cat = _Cat(tenant_id=tid, name_ar="نسالة وقطع", name_en="Remnant")
        db.add(cat)
        await db.flush()
    rem_mat = Material(
        tenant_id=tid, code=code,
        name_ar=f"نسالة/قطع: {mat.name_ar}", name_en=f"Remnant: {mat.name_en or mat.code}",
        unit=mat.unit, unit_cost=Decimal("0"), stock_qty=Decimal("0"),
        reorder_level=Decimal("0"), category_id=cat.id,
    )
    db.add(rem_mat)
    await db.flush()
    return rem_mat


async def _receive_remnant_value(db: AsyncSession, tid: int, mat: Material, qty: Decimal, source_doc_type: str, source_doc_id: int, note: str) -> None:
    """تسجيل قطعة/نسالة جديدة كرصيد فعلي في مخزن REMNANT."""
    if qty is None or qty <= 0:
        return
    from app.services.inventory_ledger import get_or_create_warehouse_bin, post_receipt
    rem_mat = await _get_or_create_remnant_material(db, tid, mat)
    rem_bin = await get_or_create_warehouse_bin(db, tid, "REMNANT")
    await post_receipt(
        db, material=rem_mat, qty=qty, unit_cost=mat.unit_cost or Decimal("0"),
        source_doc_type=source_doc_type, source_doc_id=source_doc_id,
        destination_location_id=rem_bin.id, notes=note,
    )


async def _issue_remnant_value(db: AsyncSession, tid: int, mat: Material, qty: Decimal, source_doc_type: str, source_doc_id: int, note: str) -> None:
    """صرف/استهلاك قطعة/نسالة من مخزن REMNANT — بيتجاهل بصمت لو الرصيد مش متسجل (بيانات قديمة قبل التفعيل)."""
    if qty is None or qty <= 0:
        return
    from app.services.inventory_ledger import get_or_create_warehouse_bin, post_issue
    from app.models.models import Material as _Mat
    code = f"REMNANT-{mat.code}"
    rem_mat = await db.scalar(select(_Mat).where(_Mat.tenant_id == tid, _Mat.code == code))
    if not rem_mat or rem_mat.stock_qty < qty:
        return
    rem_bin = await get_or_create_warehouse_bin(db, tid, "REMNANT")
    await post_issue(
        db, material=rem_mat, qty=qty, unit_cost=rem_mat.unit_cost or Decimal("0"),
        source_doc_type=source_doc_type, source_doc_id=source_doc_id,
        source_location_id=rem_bin.id, notes=note,
    )


async def _consume_bulk_stock(db: AsyncSession, tid: int, mat: Material, remaining: Decimal) -> tuple[Decimal, Decimal]:
    """
    محطة 18 (الروادماب الأصلي): الخطوة المشتركة اللي كانت متكررة 3 مرات
    (مرتين في _consume_bar_qty، ومرة في _consume_plate_qty) — الرجوع للرصيد
    الإجمالي (bulk) بعد ما تخلص النسالة المتاحة. استخراجها هنا يقفل باب
    اختلاف السلوك بين المسارين لو حد عدّل واحدة ونسي التانية.

    محطة 26 (تكملة محطة 16/24): بعد ما تقرير التسوية (reports.py) أثبت إن
    Material.stock_qty وSUM(StockQuant) لازم يتفقوا دايمًا (الليدجر المركزي
    المفروض يحدّث الاتنين مع بعض)، الدالة دي كانت الاستثناء الوحيد المتبقي
    في مسار الإنتاج بيخصم من stock_qty المجرَّد مباشرة من غير ما يزامن
    StockQuant في موقع RAW — نفس فجوة محطة 15/25 (بالظبط) لسه موجودة هنا.
    بنزامنها هنا في موقع RAW (نفس الموقع اللي الحجوزات وباقي محطة 16 بتفترضه).

    يرجّع (الكمية المستهلكة فعليًا, تكلفتها).
    """
    if remaining <= 0:
        return Decimal("0"), Decimal("0")
    used = min(remaining, mat.stock_qty)
    if used <= 0:
        return Decimal("0"), Decimal("0")
    mat.stock_qty -= used
    from app.services.inventory_ledger import get_or_create_warehouse_bin, _sync_quant
    raw_bin = await get_or_create_warehouse_bin(db, tid, "RAW")
    await _sync_quant(db, tenant_id=tid, material_id=mat.id, delta_qty=-used, location_id=raw_bin.id)
    return used, used * mat.unit_cost


async def _consume_plate_qty(db: AsyncSession, mat: Material, required_m2: Decimal, tid: int) -> dict:
    """
    استهلاك كمية بالمتر المربع من مادة لوح: النسالة (الأصغر أولاً) ثم البالك.
    لا يسجّل StockMovement/CostLine — جزء مشترك تستخدمه كل مسارات الصرف
    (الصرف اليدوي لكل حجز، وتنفيذ BOM التلقائي) لمنع تكرار/تعارض منطق الخصم.
    """
    from app.models.models import SheetStockItem
    remaining = required_m2
    total_cost = Decimal("0")
    remnants_used = []

    remnants = (await db.execute(
        select(SheetStockItem)
        .where(SheetStockItem.material_id == mat.id, SheetStockItem.qty > 0)
        .order_by(SheetStockItem.length_cm * SheetStockItem.width_cm)
    )).scalars().all()

    for rem in remnants:
        if remaining <= 0:
            break
        rem_area = Decimal(str(float(rem.length_cm) * float(rem.width_cm) / 10000 * rem.qty))
        cost = min(remaining, rem_area) * mat.unit_cost
        total_cost += cost
        remaining -= rem_area
        await _issue_remnant_value(db, tid, mat, rem_area, "production_cut", rem.id, f"استهلاك نسالة لوح — {mat.name_ar}")
        await db.delete(rem)
        remnants_used.append({"material_name": mat.name_ar, "remnant_area_m2": float(rem_area), "cost": float(cost)})

    bulk_used, bulk_cost = await _consume_bulk_stock(db, tid, mat, remaining)
    total_cost += bulk_cost
    remaining -= bulk_used

    shortage_m2 = max(remaining, Decimal("0"))
    return {
        "issued_m2": required_m2 - shortage_m2,
        "cost": total_cost,
        "remnants_used": remnants_used,
        "shortage_m2": shortage_m2,
    }


async def _consume_bar_qty(db: AsyncSession, mat: Material, required_kg: Decimal, tid: int, wo=None) -> dict:
    """
    استهلاك كمية بالكيلوجرام من مادة حديد/سيخ: النسالة (الأقصر أولاً) ثم البالك،
    مع حفظ أي باقي قابل لإعادة الاستخدام كنسالة جديدة. جزء مشترك (نفس فكرة _consume_plate_qty).
    """
    from app.models.models import BarStockItem
    wpm = mat.weight_per_meter_kg
    if not wpm or wpm == 0:
        bulk_used, bulk_cost = await _consume_bulk_stock(db, tid, mat, required_kg)
        return {
            "issued_kg": bulk_used,
            "cost": bulk_cost,
            "remnants_used": [],
            "new_remnant": None,
            "shortage_kg": max(required_kg - bulk_used, Decimal("0")),
        }

    req_len_m = required_kg / wpm
    remaining = req_len_m
    total_cost = Decimal("0")
    remnants_used = []
    new_remnant = None

    bar_items = (await db.execute(
        select(BarStockItem)
        .where(BarStockItem.material_id == mat.id, BarStockItem.qty > 0)
        .order_by(BarStockItem.length_cm)
    )).scalars().all()

    for item in bar_items:
        if remaining <= 0:
            break
        item_len_m = Decimal(str(float(item.length_cm) / 100))
        total_len_m = item_len_m * item.qty

        if total_len_m <= remaining:
            used_kg = total_len_m * wpm
            total_cost += used_kg * mat.unit_cost
            remaining -= total_len_m
            await _issue_remnant_value(db, tid, mat, used_kg, "production_cut", item.id, f"استهلاك سيخ كامل — {mat.name_ar}")
            await db.delete(item)
            remnants_used.append({"material_name": mat.name_ar, "bar_cm": float(item.length_cm), "qty": item.qty, "cost": float(used_kg * mat.unit_cost)})
        else:
            needed_cm = float(remaining * 100)
            leftover_cm = float(item.length_cm) - needed_cm
            used_kg = remaining * wpm
            total_cost += used_kg * mat.unit_cost

            min_rem = float(mat.min_remnant_value or 20)

            # نصرف قيمة السيخ الأصلي كاملة من دفتر REMNANT الأول (قبل ما
            # نستقبل النسالة الجديدة) — عشان الترتيب يفضل صح حتى لو السجل
            # ده قديم من قبل تفعيل المحطة دي (رصيد REMNANT مسجَّل جزئي/صفر)
            item_len_m = Decimal(str(float(item.length_cm) / 100))
            await _issue_remnant_value(db, tid, mat, item_len_m * wpm, "production_cut", item.id, f"صرف جزئي من سيخ — {mat.name_ar}")

            if leftover_cm >= min_rem:
                from app.core.database import current_tenant_id as _ctid_bar
                new_remnant = BarStockItem(
                    tenant_id=tid, material_id=mat.id,
                    length_cm=Decimal(str(round(leftover_cm, 2))),
                    qty=1, source="remnant",
                    source_work_order_id=wo.id if wo else None,
                    notes="نسالة من عملية صرف",
                )
                db.add(new_remnant)
                await db.flush()
                leftover_kg = Decimal(str(leftover_cm / 100)) * wpm
                await _receive_remnant_value(db, tid, mat, leftover_kg, "production_cut", new_remnant.id, f"نسالة جديدة من صرف — {mat.name_ar}")

            await db.delete(item)
            remnants_used.append({"material_name": mat.name_ar, "bar_cm": float(item.length_cm), "qty": 1, "cost": float(used_kg * mat.unit_cost)})
            remaining = Decimal("0")

    bulk_used_kg = Decimal("0")
    if remaining > 0:
        remaining_kg = remaining * wpm
        bulk_used_kg, bulk_cost = await _consume_bulk_stock(db, tid, mat, remaining_kg)
        if bulk_used_kg > 0:
            total_cost += bulk_cost
            remaining -= bulk_used_kg / wpm

    shortage_kg = max(remaining * wpm, Decimal("0"))
    return {
        "issued_kg": required_kg - shortage_kg,
        "cost": total_cost,
        "remnants_used": remnants_used,
        "new_remnant": {"length_cm": round(leftover_cm, 2)} if new_remnant else None,
        "shortage_kg": shortage_kg,
    }


async def issue_material(
    db: AsyncSession,
    work_order_id: int,
    material_id: int,
    qty_to_issue: Decimal,
    stage: str,
) -> dict:
    """
    صرف مواد لأمر إنتاج في مرحلة معينة
    → تسجيل حركة مخزون
    → تحديث الحجز
    → إضافة سطر تكلفة

    مسار واحد فقط للخصم: لو المادة لوح/حديد، يستهلك النسالة أولاً ثم البالك
    (نفس المنطق المستخدم في تنفيذ BOM التلقائي عبر _consume_plate_qty/_consume_bar_qty) —
    لمنع تعارض/ازدواج الخصم بين الصرف اليدوي والتنفيذ التلقائي.
    """
    wo  = await db.get(WorkOrder, work_order_id)
    mat = await db.get(Material, material_id)
    if not wo or not mat:
        return {"error": "بيانات غير صحيحة"}
    if wo.status in ("done", "cancelled"):
        # صرف مواد لأمر مكتمل كان بيزوّد actual_cost بعد ما FG اتسجّل بتكلفة
        # وحدة ثابتة (تناقض صامت)، وبيعيد فتح WIP لأمر مقفول. لأمر ملغي: خصم
        # مخزون فعلي على أمر مش هيتنتج أصلًا.
        return {"error": f"أمر الإنتاج {wo.code} {'مكتمل' if wo.status == 'done' else 'ملغي'} — لا يمكن صرف مواد له"}
    if qty_to_issue is None or qty_to_issue <= 0:
        # محطة 29: صرف بكمية صفر/سالبة كان بيمر من غير فحص — لكمية سالبة
        # كان بيزوّد stock_qty فعليًا (بدل ما يخصم) وبينقص wo.actual_cost
        # (total_cost بيبقى سالب)، عكس كل منطق الدالة تمامًا.
        return {"error": "الكمية المطلوب صرفها يجب أن تكون أكبر من صفر"}

    # البحث عن الحجز
    res = (await db.execute(
        select(MaterialReservation).where(
            MaterialReservation.work_order_id == work_order_id,
            MaterialReservation.material_id == material_id,
        )
    )).scalar_one_or_none()

    # التحقق من أن الكمية المصروفة لا تتجاوز الكمية المحجوزة المتبقية
    if res:
        remaining_reserved = res.reserved_qty - res.issued_qty
        if qty_to_issue > remaining_reserved:
            return {"error": f"الكمية المطلوبة ({float(qty_to_issue)}) تتجاوز الكمية المحجوزة المتبقية ({float(remaining_reserved)})"}
    else:
        # لا يوجد حجز لمتابعة "المتبقي" — نمنع إعادة الصرف بفحص حركات مخزون "out" سابقة
        # لنفس المادة/الأمر (يمنع الصرف المزدوج عند تكرار تنفيذ BOM التلقائي بدون حجوزات)
        already_issued = await db.scalar(
            select(func.count()).select_from(StockMovement).where(
                StockMovement.work_order_id == work_order_id,
                StockMovement.material_id == material_id,
                StockMovement.movement_type == "out",
            )
        )
        if already_issued:
            return {"error": f"تم صرف {mat.name_ar} لهذا الأمر مسبقًا — لا حجز نشِط يسمح بصرف إضافي"}

    from app.core.database import current_tenant_id as _ctid_iss
    _tid_iss = _ctid_iss.get() or 1

    remnants_used = []
    new_remnant = None

    # فحص مسبق للتوفر (نسالة + بالك) قبل أي استهلاك فعلي: _consume_plate_qty/
    # _consume_bar_qty بيحذفوا النسالة ويخصموا البالك أثناء التنفيذ، فلو طلع
    # نقص في الآخر كانت الدالة بترجع error والتعديلات الجزئية (نسالة محذوفة +
    # stock_qty مخصوم) فاضلة في الـsession بلا حركة ولا تكلفة — وفي
    # auto_execute_bom كان أول commit لبند تالي ناجح بيثبّتها فعليًا.
    if mat.is_plate and mat.plate_length_cm and mat.plate_width_cm:
        from app.models.models import SheetStockItem as _SSI_chk
        _rem_rows = (await db.execute(
            select(_SSI_chk).where(_SSI_chk.material_id == mat.id, _SSI_chk.qty > 0)
        )).scalars().all()
        _avail_m2 = sum(
            (Decimal(str(float(r.length_cm) * float(r.width_cm) / 10000 * r.qty)) for r in _rem_rows),
            Decimal("0"),
        ) + max(mat.stock_qty or Decimal("0"), Decimal("0"))
        if _avail_m2 < qty_to_issue:
            return {"error": f"المخزون غير كافٍ (نسالة+بالك) — نقص {float(qty_to_issue - _avail_m2):.2f} م²"}
    elif mat.bar_length_cm and mat.weight_per_meter_kg:
        from app.models.models import BarStockItem as _BSI_chk
        _bar_rows = (await db.execute(
            select(_BSI_chk).where(_BSI_chk.material_id == mat.id, _BSI_chk.qty > 0)
        )).scalars().all()
        _wpm_chk = Decimal(str(mat.weight_per_meter_kg))
        _avail_kg = sum(
            (Decimal(str(float(r.length_cm) / 100)) * r.qty * _wpm_chk for r in _bar_rows),
            Decimal("0"),
        ) + max(mat.stock_qty or Decimal("0"), Decimal("0"))
        if _avail_kg + Decimal("0.0001") < qty_to_issue:
            return {"error": f"المخزون غير كافٍ (نسالة+بالك) — نقص {float(qty_to_issue - _avail_kg):.2f} كجم"}

    if mat.is_plate and mat.plate_length_cm and mat.plate_width_cm:
        consumed = await _consume_plate_qty(db, mat, qty_to_issue, _tid_iss)
        if consumed["shortage_m2"] > 0:
            return {"error": f"المخزون غير كافٍ (نسالة+بالك) — نقص {float(consumed['shortage_m2']):.2f} م²"}
        actual_qty = consumed["issued_m2"]
        total_cost = consumed["cost"]
        remnants_used = consumed["remnants_used"]
    elif mat.bar_length_cm and mat.weight_per_meter_kg:
        consumed = await _consume_bar_qty(db, mat, qty_to_issue, _tid_iss, wo=wo)
        if consumed["shortage_kg"] > 0:
            return {"error": f"المخزون غير كافٍ (نسالة+بالك) — نقص {float(consumed['shortage_kg']):.2f} كجم"}
        actual_qty = consumed["issued_kg"]
        total_cost = consumed["cost"]
        remnants_used = consumed["remnants_used"]
        new_remnant = consumed["new_remnant"]
    else:
        if mat.stock_qty < qty_to_issue:
            return {"error": f"المخزون غير كافٍ ({float(mat.stock_qty):.2f} متوفر، {float(qty_to_issue):.2f} مطلوب)"}
        actual_qty = qty_to_issue
        total_cost = qty_to_issue * mat.unit_cost
        mat.stock_qty -= qty_to_issue
        # محطة 26: زامن StockQuant في موقع RAW — نفس تصليح _consume_bulk_stock
        # (كان الخصم هنا بيمس Material.stock_qty بس، بيكسر تطابق تقرير التسوية)
        from app.services.inventory_ledger import get_or_create_warehouse_bin as _gcwb_plain, _sync_quant as _sq_plain
        _raw_bin_plain = await _gcwb_plain(db, _tid_iss, "RAW")
        await _sq_plain(db, tenant_id=_tid_iss, material_id=mat.id, delta_qty=-qty_to_issue, location_id=_raw_bin_plain.id)

    # تسجيل حركة مخزون (الخصم الفعلي)
    movement = StockMovement(
        tenant_id=_tid_iss,
        material_id=material_id,
        movement_type="out",
        qty=actual_qty,
        unit_cost=mat.unit_cost,
        total_cost=total_cost,
        reference=f"{wo.code}-{stage}",
        movement_date=date.today(),
        destination="workshop",
        destination_ref=wo.code,
        work_order_id=work_order_id,
    )
    db.add(movement)

    # تحديث الحجز إن وُجد
    if res:
        res.issued_qty += actual_qty
        if res.issued_qty >= res.required_qty:
            res.status = "fully_issued"
        else:
            res.status = "partially_issued"

    # سطر تكلفة فعلية
    cost_line = CostLine(
        tenant_id=_tid_iss,
        work_order_id=work_order_id,
        cost_type="material",
        description=f"{mat.name_ar} — {stage}",
        qty=actual_qty,
        unit_cost=mat.unit_cost,
        total_cost=total_cost,
        material_id=material_id,
    )
    db.add(cost_line)

    # تحديث التكلفة الفعلية
    wo.actual_cost += total_cost

    # محطة 14: تسجيل المعدة "تحت التصنيع" كرصيد فعلي في موقع WIP
    await _sync_wip_quant(db, wo, _tid_iss)

    # قيد محاسبي: WIP debit / Inventory credit — لازم يتسجل قبل الـ commit وفي
    # نفس الترانزاكشن. كان بعد الـ commit وجوه try/except صامت، فالقيد مكانش
    # بيتحفظ أبدًا: حساب WIP بيطلع دائن بقيمة المعدة عند الإقفال، ومخزون
    # الخامات في الميزانية بيفضل شايل مواد اتصرفت فعلًا.
    from app.services.auto_journal import journal_material_issue
    await journal_material_issue(db, _tid_iss, work_order_id, wo.code, total_cost, date.today())

    await db.commit()

    return {
        "issued":               float(actual_qty),
        "unit_cost":            float(mat.unit_cost),
        "total_cost":           float(total_cost),
        "wo_actual_cost":       float(wo.actual_cost),
        "reservation_status":   res.status if res else "no_reservation",
        "remnants_used":        remnants_used,
        "new_remnant":          new_remnant,
    }


# ═══════════════════════════════════════════════════════════════
# محطة 14: تسجيل "تحت التصنيع" كرصيد فعلي في موقع WIP
# ═══════════════════════════════════════════════════════════════

async def _sync_wip_quant(db: AsyncSession, wo: WorkOrder, tenant_id: int) -> None:
    """
    عند أول صرف مادة لأمر إنتاج، نسجّل المعدة قيد التصنيع كرصيد فعلي
    (StockQuant) في موقع WIP الحقيقي — بدل ما "تحت التصنيع" يفضل مجرد
    نص (WorkOrder.status) بلا أي أثر في نظام المخازن.

    الكمية المسجَّلة = wo.quantity (تمثيل لدفعة أمر الإنتاج ده كوحدة
    واحدة قيد التنفيذ)، وبتتسجل مرة واحدة بس (أول صرف). أي صرف بعد كده
    بيحدّث تكلفة الوحدة فقط من غير ما يضاعف الكمية المسجّلة في WIP.
    """
    from app.models.models import Material as _Mat, MaterialCategory as _Cat
    from app.services.inventory_ledger import get_or_create_warehouse_bin, post_receipt

    wip_code = f"WIP-{wo.code}"
    mat = await db.scalar(
        select(_Mat).where(_Mat.tenant_id == tenant_id, _Mat.code == wip_code)
    )

    if not mat:
        cat = await db.scalar(
            select(_Cat).where(_Cat.tenant_id == tenant_id, _Cat.name_en == "Work In Progress")
        )
        if not cat:
            cat = _Cat(tenant_id=tenant_id, name_ar="قيد التصنيع", name_en="Work In Progress")
            db.add(cat)
            await db.flush()
        mat = _Mat(
            tenant_id=tenant_id, code=wip_code,
            name_ar=f"قيد التصنيع: {wo.code}", name_en=f"WIP: {wo.code}",
            unit="وحدة", unit_cost=Decimal("0"), stock_qty=Decimal("0"),
            reorder_level=Decimal("0"), category_id=cat.id,
            stock_item_type="finished_good",
        )
        db.add(mat)
        await db.flush()

    wip_bin = await get_or_create_warehouse_bin(db, tenant_id, "WIP")

    if (mat.stock_qty or Decimal("0")) == 0:
        qty = wo.quantity or Decimal("1")
        await post_receipt(
            db, material=mat, qty=qty, unit_cost=Decimal("0"),
            source_doc_type="production", source_doc_id=wo.id,
            destination_location_id=wip_bin.id,
        )

    if mat.stock_qty and mat.stock_qty > 0:
        mat.unit_cost = (wo.actual_cost or Decimal("0")) / mat.stock_qty


# ═══════════════════════════════════════════════════════════════
# STEP 3: تسجيل هالك
# ═══════════════════════════════════════════════════════════════

async def record_scrap(
    db: AsyncSession,
    material_id: int,
    scrap_qty: Decimal,
    reason: str,
    stage: Optional[str] = None,
    work_order_id: Optional[int] = None,
) -> dict:
    """تسجيل هالك + إضافة تكلفة + تحديث الحجز (أو هالك عام بدون أمر إنتاج)"""
    mat = await db.get(Material, material_id)
    if not mat:
        return {"error": "بيانات غير صحيحة"}
    if scrap_qty is None or scrap_qty <= 0:
        # محطة 29: هالك بكمية صفر/سالبة كان بيمر من غير فحص — كمية سالبة
        # كانت بتـ"زوّد" stock_qty (max(0, stock_qty - qty سالبة) = زيادة)
        # وتنقص wo.actual_cost بدل ما تزوّده، عكس معنى "هالك" تمامًا.
        return {"error": "كمية الهالك يجب أن تكون أكبر من صفر"}

    wo = None
    if work_order_id:
        wo = await db.get(WorkOrder, work_order_id)
        if not wo:
            return {"error": "بيانات غير صحيحة"}

    cost = scrap_qty * mat.unit_cost
    stage = stage or ("هالك عام" if not wo else stage)

    from app.core.database import current_tenant_id as _ctid_sc2
    _tid_sc2 = _ctid_sc2.get() or 1

    # سجل الهالك
    scrap = ScrapRecord(
        tenant_id=_tid_sc2,
        work_order_id=work_order_id,
        material_id=material_id,
        qty=scrap_qty,
        unit_cost=mat.unit_cost,
        total_cost=cost,
        reason=reason,
        stage=stage,
    )
    db.add(scrap)

    if wo:
        # تكلفة الهالك على أمر الإنتاج
        db.add(CostLine(
            tenant_id=_tid_sc2,
            work_order_id=work_order_id,
            cost_type="overhead",
            description=f"هالك: {mat.name_ar} — {reason}",
            qty=scrap_qty,
            unit_cost=mat.unit_cost,
            total_cost=cost,
        ))
        wo.actual_cost += cost

        # تحديث الحجز (لو موجود) — وفي كل الحالات نخصم من المخزن فعليًا
        res = (await db.execute(
            select(MaterialReservation).where(
                MaterialReservation.work_order_id == work_order_id,
                MaterialReservation.material_id == material_id,
            )
        )).scalar_one_or_none()
        if res:
            res.scrap_qty += scrap_qty
        # محطة 26 (تصليح جذري): auto_reserve_materials() حجز "soft lock" بحت —
        # مبيخصمش من Material.stock_qty خالص وقت الحجز (بيأثر بس على "المتاح"
        # المحسوب via get_reserved_qty). التعليق القديم هنا كان بيفترض إن
        # الحجز خصم فعليًا ("الخصم حصل بالفعل عند الحجز") فكان بيتجاهل خصم
        # الهالك تمامًا لو المادة محجوزة — يعني مادة اتهلكت فعليًا (وسُجّل
        # لها ScrapRecord وتكلفة) كانت تفضل معدودة ضمن stock_qty للأبد. لازم
        # نخصم فعليًا هنا في كل الحالات (بغض النظر عن وجود حجز نشِط).
        _before_sc1 = mat.stock_qty
        mat.stock_qty = max(Decimal("0"), mat.stock_qty - scrap_qty)
        _delta_sc1 = mat.stock_qty - _before_sc1
        if _delta_sc1 != 0:
            from app.services.inventory_ledger import get_or_create_warehouse_bin as _gcwb_sc1, _sync_quant as _sq_sc1
            _raw_bin_sc1 = await _gcwb_sc1(db, _tid_sc2, "RAW")
            await _sq_sc1(db, tenant_id=_tid_sc2, material_id=mat.id, delta_qty=_delta_sc1, location_id=_raw_bin_sc1.id)
    else:
        # هالك عام بدون أمر إنتاج — خصم مباشر من المخزن
        _before_sc2 = mat.stock_qty
        mat.stock_qty = max(Decimal("0"), mat.stock_qty - scrap_qty)
        _delta_sc2 = mat.stock_qty - _before_sc2
        if _delta_sc2 != 0:
            from app.services.inventory_ledger import get_or_create_warehouse_bin as _gcwb_sc2, _sync_quant as _sq_sc2
            _raw_bin_sc2 = await _gcwb_sc2(db, _tid_sc2, "RAW")
            await _sq_sc2(db, tenant_id=_tid_sc2, material_id=mat.id, delta_qty=_delta_sc2, location_id=_raw_bin_sc2.id)

    # flush لاحتياج scrap.id قبل القيد المحاسبي
    await db.flush()

    # محطة 24 (الروادماب الأصلي): تسجيل الهالك كرصيد فعلي في مخزن SCRAP
    # الحقيقي — قبل كده مخزن SCRAP (من محطة 10) كان موجود كسجل بس، وكل
    # تتبع الهالك كان بيعتمد كليًا على ScrapRecord بمعزل تام عن StockQuant،
    # فمخزون الهالك كان غايب من أي تقرير موحّد بيقرا من الليدجر المركزي.
    # بنستخدم نفس نمط WIP/FG (محطة 14/15): مادة مركّبة "SCRAP-{كود المادة
    # الأصلية}" في موقع SCRAP الفعلي — مش المادة الأصلية نفسها، لأن الهالك
    # بيتباع بسعر مختلف تمامًا عن المادة الأصلية (خردة مش صاج/سيخ).
    from app.models.models import Material as _Mat, MaterialCategory as _Cat
    from app.services.inventory_ledger import get_or_create_warehouse_bin, post_receipt
    scrap_code = f"SCRAP-{mat.code}"
    scrap_mat = await db.scalar(select(_Mat).where(_Mat.tenant_id == _tid_sc2, _Mat.code == scrap_code))
    if not scrap_mat:
        scrap_cat = await db.scalar(select(_Cat).where(_Cat.tenant_id == _tid_sc2, _Cat.name_en == "Scrap"))
        if not scrap_cat:
            scrap_cat = _Cat(tenant_id=_tid_sc2, name_ar="هالك", name_en="Scrap")
            db.add(scrap_cat)
            await db.flush()
        scrap_mat = _Mat(
            tenant_id=_tid_sc2, code=scrap_code,
            name_ar=f"هالك: {mat.name_ar}", name_en=f"Scrap: {mat.name_en or mat.code}",
            unit=mat.unit, unit_cost=Decimal("0"), stock_qty=Decimal("0"),
            reorder_level=Decimal("0"), category_id=scrap_cat.id,
        )
        db.add(scrap_mat)
        await db.flush()
    scrap_bin = await get_or_create_warehouse_bin(db, _tid_sc2, "SCRAP")
    await post_receipt(
        db, material=scrap_mat, qty=scrap_qty, unit_cost=mat.unit_cost,
        source_doc_type="scrap_record", source_doc_id=scrap.id,
        destination_location_id=scrap_bin.id,
        reference=wo.code if wo else "هالك عام", notes=f"هالك: {reason}",
    )

    # قيد محاسبي: مصاريف هالك (5600) مدين | مخزون خام (1310) دائن
    if cost > 0:
        from app.services.auto_journal import journal_scrap
        from app.core.database import current_tenant_id as _ctid_sc
        _tid_sc = _ctid_sc.get() or 1
        import datetime as _dt
        await journal_scrap(
            db, _tid_sc, scrap.id,
            wo.code if wo else "هالك عام", cost,
            _dt.date.today(),
        )

    await db.commit()

    return {
        "scrap_qty":  float(scrap_qty),
        "scrap_cost": float(cost),
        "stage":      stage,
        "reason":     reason,
    }


# ═══════════════════════════════════════════════════════════════
# STEP 3.1: مخزون الهالك المتاح للبيع
# ═══════════════════════════════════════════════════════════════

async def get_scrap_inventory(db: AsyncSession, tenant_id: Optional[int] = None) -> list[dict]:
    """تجميع الهالك المسجل لكل مادة، وحساب المتاح للبيع (الكمية - المباع)"""
    # محطة 28 (تصليح جذري — أمان متعدد المستأجرين): هذا استعلام أعمدة/تجميع
    # (select(Model.col, func.sum(...))) — الفلترة التلقائية العامة عبر
    # with_loader_criteria (tenant_events.py) مصمَّمة لاستعلامات الكيان
    # الكامل (select(Model))، ومش مضمون تطبيقها بنفس الثقة على استعلام
    # أعمدة/تجميع بحت زي ده. فلتر tenant_id هنا صريح كدفاع إضافي (defense
    # in depth) بدل الاعتماد فقط على الفلتر العام.
    if tenant_id is None:
        from app.core.database import current_tenant_id as _ctid_sci
        tenant_id = _ctid_sci.get() or 1
    rows = (await db.execute(
        select(
            ScrapRecord.material_id,
            func.sum(ScrapRecord.qty).label("total_qty"),
            func.sum(ScrapRecord.qty_sold).label("total_sold"),
        )
        .where(ScrapRecord.material_id.is_not(None), ScrapRecord.tenant_id == tenant_id)
        .group_by(ScrapRecord.material_id)
    )).all()

    result = []
    for material_id, total_qty, total_sold in rows:
        available = (total_qty or Decimal("0")) - (total_sold or Decimal("0"))
        if available <= 0:
            continue
        mat = await db.get(Material, material_id)
        if not mat:
            continue
        result.append({
            "material_id":   material_id,
            "material_code": mat.code,
            "material_name": mat.name_ar,
            "unit":          mat.unit,
            "available_qty": float(available),
            "unit_cost":     float(mat.unit_cost),
            "est_value":     float(available * mat.unit_cost),
        })
    return result


# ═══════════════════════════════════════════════════════════════
# STEP 3.2: بيع الهالك
# ═══════════════════════════════════════════════════════════════

async def create_scrap_sale(db: AsyncSession, data: dict) -> dict:
    """بيع كمية من الهالك المسجل: تخصيص FIFO عبر سجلات الهالك + قبض في الخزينة + قيد محاسبي"""
    from app.models.models import ScrapSale, ScrapSaleLine, Treasury, TreasuryTransaction
    from app.core.database import current_tenant_id as _ctid
    tid = _ctid.get() or 1

    treasury = await db.get(Treasury, data["treasury_id"])
    if not treasury:
        return {"error": "الخزينة غير موجودة"}

    lines_in = data.get("lines") or []
    if not lines_in:
        return {"error": "لا يوجد سطور للبيع"}

    # توليد كود العملية
    count = await db.scalar(select(func.count()).select_from(ScrapSale))
    n = (count or 0) + 1
    while True:
        code = f"SCR-{n:04d}"
        exists = await db.scalar(select(ScrapSale.id).where(ScrapSale.code == code))
        if not exists:
            break
        n += 1

    sale = ScrapSale(
        tenant_id=tid, code=code, sale_date=data["sale_date"],
        buyer_name=data.get("buyer_name"), treasury_id=treasury.id,
        payment_method=data.get("payment_method", "cash"),
        notes=data.get("notes"),
    )
    db.add(sale)
    await db.flush()

    total_amount = Decimal("0")
    for ln in lines_in:
        material_id = ln["material_id"]
        qty = Decimal(str(ln["qty"]))
        unit_price = Decimal(str(ln["unit_price"]))
        if qty <= 0:
            continue

        # تخصيص الكمية على سجلات الهالك المتاحة (FIFO)
        records = (await db.execute(
            select(ScrapRecord).where(
                ScrapRecord.material_id == material_id,
                ScrapRecord.qty > ScrapRecord.qty_sold,
            ).order_by(ScrapRecord.recorded_at.asc())
        )).scalars().all()

        available = sum((r.qty - r.qty_sold) for r in records) if records else Decimal("0")
        if qty > available:
            await db.rollback()
            return {"error": f"الكمية المطلوبة ({qty}) أكبر من المتاح ({available}) لهذه المادة"}

        remaining = qty
        for r in records:
            if remaining <= 0:
                break
            free = r.qty - r.qty_sold
            take = min(free, remaining)
            r.qty_sold += take
            remaining -= take

        total_price = qty * unit_price
        total_amount += total_price
        db.add(ScrapSaleLine(
            tenant_id=tid, scrap_sale_id=sale.id, material_id=material_id,
            qty=qty, unit_price=unit_price, total_price=total_price,
        ))

        # محطة 24: خصم فعلي من مخزن SCRAP الحقيقي (عكس متماثل لمحطة record_scrap)
        from app.models.models import Material as _Mat
        from app.services.inventory_ledger import get_or_create_warehouse_bin, post_issue
        orig_mat = await db.get(_Mat, material_id)
        if orig_mat:
            scrap_mat = await db.scalar(select(_Mat).where(_Mat.tenant_id == tid, _Mat.code == f"SCRAP-{orig_mat.code}"))
            if scrap_mat and scrap_mat.stock_qty >= qty:
                scrap_bin = await get_or_create_warehouse_bin(db, tid, "SCRAP")
                await post_issue(
                    db, material=scrap_mat, qty=qty, unit_cost=scrap_mat.unit_cost or Decimal("0"),
                    source_doc_type="scrap_sale", source_doc_id=sale.id,
                    source_location_id=scrap_bin.id,
                    reference=code, notes=f"بيع هالك: {code}",
                )

    sale.total_amount = total_amount

    # حركة خزينة: قبض نقدية مقابل بيع الهالك
    db.add(TreasuryTransaction(
        tenant_id=tid, treasury_id=treasury.id, direction="in",
        amount=total_amount, txn_date=data["sale_date"],
        description=f"بيع هالك {sale.code}", source="other",
        linked_type="scrap_sale", linked_id=sale.id,
    ))
    treasury.balance += total_amount

    await db.flush()

    from app.services.auto_journal import journal_scrap_sale
    await journal_scrap_sale(db, tid, sale.id, sale.code, total_amount, data["sale_date"])

    await db.commit()

    return {"id": sale.id, "code": sale.code, "total_amount": float(total_amount)}


# ═══════════════════════════════════════════════════════════════
# STEP 3.5: تنفيذ BOM كامل — زر واحد يصرف كل شيء
# ═══════════════════════════════════════════════════════════════

async def auto_execute_bom(db: AsyncSession, work_order_id: int) -> dict:
    """
    تنفيذ BOM كامل لأمر الإنتاج بدون تدخل المستخدم — لكل بند يستدعي issue_material()
    (نفس الدالة التي يستخدمها زر "📤 صرف" اليدوي لكل حجز)، بحيث يكون هناك مسار خصم واحد
    فقط لكل المواد (عادية/ألواح/أسياخ)، بدلاً من تكرار منطق منفصل هنا — وهذا يمنع تعارض
    أو ازدواج الخصم بين الصرف اليدوي والتنفيذ التلقائي.
    """
    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        return {"error": "أمر الإنتاج غير موجود"}
    if not wo.equipment_id:
        return {"error": "لا توجد معدة محددة لهذا الأمر"}

    # ── جلب BOM: من الـ snapshot المجمّد أولاً ────────────────────
    wo_snap = (await db.execute(
        select(WorkOrderBOMLine).where(WorkOrderBOMLine.work_order_id == work_order_id)
    )).scalars().all()

    if wo_snap:
        bom_lines = [{"material_id": s.material_id, "qty": float(s.required_qty)} for s in wo_snap]
    else:
        wo_qty = wo.quantity if wo.quantity and wo.quantity > 0 else Decimal("1")
        raw    = await _get_full_bom(db, wo.equipment_id)
        bom_lines = [{"material_id": l["material_id"], "qty": l["qty"] * float(wo_qty)} for l in raw]

    if not bom_lines:
        return {"error": "لا توجد بنود BOM لهذا الأمر"}

    results: dict = {
        "issued_bulk":   [],
        "cut_sheet":     [],
        "cut_bar":       [],
        "shortages":     [],
        "remnants_used": [],
        "new_remnants":  [],
        "total_cost":    0.0,
    }

    for line in bom_lines:
        mat = await db.get(Material, line["material_id"])
        if not mat:
            continue
        required = Decimal(str(line["qty"]))

        # المتبقي غير المصروف بعد من الحجز (لو موجود) — لمنع صرف أكثر مما يلزم
        res = (await db.execute(
            select(MaterialReservation).where(
                MaterialReservation.work_order_id == work_order_id,
                MaterialReservation.material_id == mat.id,
            )
        )).scalar_one_or_none()
        to_issue = (res.reserved_qty - res.issued_qty) if res else required
        if to_issue <= 0:
            continue  # تم صرف هذا البند بالفعل (يدويًا أو في تشغيل سابق) — لا تصرف مرة أخرى

        out = await issue_material(db, work_order_id, mat.id, min(to_issue, required), "AUTO")
        if "error" in out:
            results["shortages"].append({
                "material_id": mat.id, "material_name": mat.name_ar,
                "required": float(required), "error": out["error"],
            })
            continue

        results["total_cost"] += out["total_cost"]
        if out.get("remnants_used"):
            results["remnants_used"] += out["remnants_used"]
        if out.get("new_remnant"):
            results["new_remnants"].append({"material_name": mat.name_ar, **out["new_remnant"]})

        entry = {"material_name": mat.name_ar, "issued": out["issued"], "unit": mat.unit, "cost": out["total_cost"]}
        if mat.is_plate and mat.plate_length_cm and mat.plate_width_cm:
            results["cut_sheet"].append({"material_name": mat.name_ar, "required_m2": float(required), "issued_m2": out["issued"], "cost": out["total_cost"]})
        elif mat.bar_length_cm and mat.weight_per_meter_kg:
            results["cut_bar"].append({"material_name": mat.name_ar, "required_kg": float(required), "issued_kg": out["issued"], "cost": out["total_cost"]})
        else:
            results["issued_bulk"].append(entry)

    return results

# ═══════════════════════════════════════════════════════════════
# STEP 4: إنهاء أمر الإنتاج
# ═══════════════════════════════════════════════════════════════

async def _release_wip_and_receive_fg(
    db: AsyncSession, wo: "WorkOrder", eq: "Equipment", tenant_id: int,
    source_doc_type: str = "production",
) -> Optional[dict]:
    """
    محطة 15 (مستخرجة كدالة قابلة لإعادة الاستخدام في محطة 18 - Backfill):
    (1) تطفئ رصيد WIP-{wo.code} لو موجود، (2) تستقبل FG-{wo.code} في موقع
    FG الحقيقي بكمية wo.quantity وتكلفة وحدة = wo.actual_cost/qty.
    """
    if not eq:
        return None
    from app.models.models import Material as _Mat, MaterialCategory as _Cat
    from sqlalchemy import select as _sel
    from app.services.inventory_ledger import get_or_create_warehouse_bin, post_receipt, post_issue

    qty_produced = wo.quantity or Decimal("1")

    # 1) إطفاء رصيد WIP الخاص بأمر الإنتاج ده (لو مسجَّل من محطة 14)
    _wip_mat = (await db.execute(
        _sel(_Mat).where(_Mat.tenant_id == tenant_id, _Mat.code == f"WIP-{wo.code}")
    )).scalar_one_or_none()
    if _wip_mat and (_wip_mat.stock_qty or Decimal("0")) > 0:
        wip_bin = await get_or_create_warehouse_bin(db, tenant_id, "WIP")
        await post_issue(
            db, material=_wip_mat, qty=_wip_mat.stock_qty, unit_cost=_wip_mat.unit_cost or Decimal("0"),
            source_doc_type=source_doc_type, source_doc_id=wo.id,
            source_location_id=wip_bin.id,
        )

    # 2) استقبال المنتج التام في موقع FG الحقيقي — مادة خاصة بهذا الأمر
    _fin_mat = (await db.execute(
        _sel(_Mat).where(_Mat.tenant_id == tenant_id, _Mat.code == f"FG-{wo.code}")
    )).scalar_one_or_none()

    if not _fin_mat:
        _fg_cat = (await db.execute(
            _sel(_Cat).where(_Cat.tenant_id == tenant_id, _Cat.name_en == "Finished Goods")
        )).scalar_one_or_none()
        if not _fg_cat:
            _fg_cat = _Cat(tenant_id=tenant_id, name_ar="تام الصنع", name_en="Finished Goods")
            db.add(_fg_cat)
            await db.flush()

        _fin_mat = _Mat(
            tenant_id=tenant_id,
            code=f"FG-{wo.code}",
            name_ar=f"تام الصنع: {eq.name_ar} ({wo.code})",
            name_en=f"FG: {eq.name_en or eq.code} ({wo.code})",
            unit="وحدة",
            unit_cost=Decimal("0"),
            stock_qty=Decimal("0"),
            reorder_level=Decimal("0"),
            category_id=_fg_cat.id,
            stock_item_type="finished_good",
        )
        db.add(_fin_mat)
        await db.flush()

    unit_cost = wo.actual_cost / qty_produced if qty_produced > 0 else wo.actual_cost
    fg_bin = await get_or_create_warehouse_bin(db, tenant_id, "FG")
    await post_receipt(
        db, material=_fin_mat, qty=qty_produced, unit_cost=unit_cost,
        source_doc_type=source_doc_type, source_doc_id=wo.id,
        destination_location_id=fg_bin.id,
        reference=f"{wo.code}-FG", notes=f"مخزون تام الصنع من أمر الإنتاج {wo.code}",
    )
    return {"finished_material_id": _fin_mat.id, "finished_stock": float(_fin_mat.stock_qty)}


async def complete_work_order(db: AsyncSession, work_order_id: int) -> dict:
    """
    إنهاء أمر الإنتاج:
    → إغلاق الحجوزات
    → إرجاع المواد الزائدة
    → تسجيل المعدة في المخزن
    → حساب الربحية
    """
    wo  = await db.get(WorkOrder, work_order_id)
    if not wo:
        return {"error": "أمر الإنتاج غير موجود"}

    # محطة 29 (تصليح جذري): لم يكن هناك أي حماية ضد استدعاء الدالة دي
    # مرتين على نفس أمر الإنتاج (نقرة مزدوجة من الواجهة، إعادة محاولة بعد
    # timeout، أو استدعاء يدوي متكرر لـ /production/complete). الاستدعاء
    # الثاني كان بيضيف تكلفة العمالة تاني فوق اللي اتضافت خلاص (مضاعفة
    # wo.actual_cost)، والأخطر إن _release_wip_and_receive_fg بتستخدم
    # post_receipt (إضافة رصيد) من غير أي فحص — فبيستقبل كمية FG-{code}
    # جديدة (= wo.quantity تاني) فوق اللي موجودة بالفعل، يعني مخزون تام
    # صنع وهمي يتضاعف بلا أي إنتاج فعلي إضافي. وده تحديدًا المخزون اللي
    # sales.py بيعتمد عليه بالكامل لفوترة أمر الإنتاج وحساب COGS.
    if wo.status == "done":
        return {"error": f"أمر الإنتاج {wo.code} مكتمل بالفعل — لا يمكن إنهاؤه مرة أخرى"}
    if wo.status == "cancelled":
        # إنهاء أمر ملغي كان بيستقبل FG-{code} (مخزون تام صنع وهمي) ويرحّل قيد WIP→FG
        return {"error": f"أمر الإنتاج {wo.code} ملغي — لا يمكن إنهاؤه"}

    eq = await db.get(Equipment, wo.equipment_id) if wo.equipment_id else None

    # إغلاق الحجوزات وإرجاع الزائد
    reservations = (await db.execute(
        select(MaterialReservation).where(
            MaterialReservation.work_order_id == work_order_id,
            MaterialReservation.status != 'closed',
        )
    )).scalars().all()

    from app.core.database import current_tenant_id as _ctid_cwo
    _tid_cwo = _ctid_cwo.get() or 1

    # محطة 26 (تصليح جذري): auto_reserve_materials() لا يخصم من
    # Material.stock_qty خالص وقت الحجز (soft lock بحت — راجع تعليق
    # MaterialReservation.location_id). فأي جزء من الحجز لم يُصرف (عبر
    # issue_material) ولم يُسجَّل هالك عليه (عبر record_scrap) أصلاً لسه
    # قاعد في stock_qty ولم يتحرك منه شيء — "إرجاعه" هنا بإضافته لـ
    # stock_qty كان يصنع مخزون وهمي (كل أمر إنتاج بيتقفل بحجز غير مصروف
    # بالكامل كان بيضخم رصيد المادة فوق الحقيقة). التصحيح: فقط نغلق الحجز
    # (نفرج عن الـsoft lock) من غير أي لمس لـstock_qty — الكمية غير
    # المصروفة أصلاً موجودة بالفعل في الرصيد ولا تحتاج أي حركة.
    returned_value = Decimal("0")
    for res in reservations:
        res.status = "closed"

    # تسجيل المعدة كمنتَجة (in_stock تمت إزالته من النموذج — نسجّل فقط حركة المخزن أدناه)
    # eq.in_stock لا يوجد في نموذج Equipment — الدليل على الإنتاج هو StockMovement من نوع FG

    # ── إضافة تكلفة العمالة الفعلية (ساعات × أجر الساعة) إلى تكلفة الأمر ──
    # كانت actual_cost تتضمن المواد فقط، فالربح المعروض كان يتضمن دائماً تكلفة العمالة كهامش مضاف بالخطأ
    from app.models.models import WorkOrderMember as _WOM
    members = (await db.execute(
        select(_WOM).where(_WOM.work_order_id == work_order_id)
    )).scalars().all()
    labor_cost = sum((m.actual_hours or Decimal("0")) * (m.hourly_rate or Decimal("0")) for m in members)
    if labor_cost > 0:
        wo.actual_cost += labor_cost
        from app.services.auto_journal import journal_labor_cost
        await journal_labor_cost(db, _tid_cwo, wo.id, wo.code, labor_cost, date.today())

    # إنهاء الأمر
    wo.status = "done"
    wo.end_date = date.today()

    # ── محطة 15: ترحيل حقيقي WIP → FG (بموقع فعلي، مش بالاسم فقط) ──────
    finished_material_id = None
    finished_stock = None
    _fg_result = await _release_wip_and_receive_fg(db, wo, eq, _tid_cwo)
    if _fg_result:
        finished_material_id = _fg_result["finished_material_id"]
        finished_stock = _fg_result["finished_stock"]

    # ── قيد محاسبي: WIP → مخزون تام الصنع ──────────────────────
    if wo.actual_cost > 0:
        from app.services.auto_journal import journal_wo_complete
        from app.core.database import current_tenant_id as _ctid_woc
        _tid_woc = _ctid_woc.get() or 1
        await journal_wo_complete(db, _tid_woc, wo.id, wo.code, wo.actual_cost, date.today())

    # حساب الربحية لو في طلب عميل
    from app.models.models import CustomerOrder
    profit = None
    if hasattr(wo, 'customer_order_id') and wo.customer_order_id:
        order = await db.get(CustomerOrder, wo.customer_order_id)
        if order:
            profit = float(order.total_price) - float(wo.actual_cost)

    # حساب انحراف التكلفة
    variance_pct = 0
    if wo.planned_cost and wo.planned_cost > 0:
        variance_pct = round(
            (float(wo.actual_cost) - float(wo.planned_cost)) / float(wo.planned_cost) * 100, 1
        )

    await db.commit()

    return {
        "status":              "completed",
        "work_order":          wo.code,
        "actual_cost":         float(wo.actual_cost),
        "planned_cost":        float(wo.planned_cost or 0),
        "variance_pct":        variance_pct,
        "returned_value":      float(returned_value),
        "estimated_profit":    profit,
        "equipment_in_stock":  finished_stock,
        "finished_material_id": finished_material_id,
    }


# ═══════════════════════════════════════════════════════════════
# MRP ENGINE — محرك التخطيط
# ═══════════════════════════════════════════════════════════════

async def run_mrp(db: AsyncSession, tenant_id: Optional[int] = None) -> List[dict]:
    """
    MRP بسيط:
    للكل مادة:
      Net = Σ(required من WOs مفتوحة) + reorder_level - stock_qty
      لو Net > 0 → اقترح طلبية
    """
    # محطة 28 (تصليح جذري — أمان متعدد المستأجرين): فلتر tenant_id صريح
    # هنا كدفاع إضافي (defense in depth) — دالة الداشبورد دي بتتنادى من
    # /dashboard/full ومسارات تجميعية تانية، فمش هنعتمد فقط على الفلتر
    # العام (with_loader_criteria في tenant_events.py) اللي بيتوقف تلقائيًا
    # لو current_tenant_id ContextVar مش موجودة في سياق التنفيذ (مثلاً لو
    # اتنفذت الدالة دي جوه executor/thread أو task منفصل مش وارث للسياق).
    if tenant_id is None:
        from app.core.database import current_tenant_id as _ctid_mrp0
        tenant_id = _ctid_mrp0.get()
    if tenant_id is None:
        # fail-closed: ممنوع الرجوع لـ tenant 1 (كان بيعرض بيانات ورشة تانية)
        return []

    # احتياجات أوامر الإنتاج المفتوحة
    open_res = (await db.execute(
        select(MaterialReservation).where(
            MaterialReservation.status.in_(['pending', 'partial', 'reserved', 'partially_issued']),
            MaterialReservation.tenant_id == tenant_id,
        ).options(selectinload(MaterialReservation.material))
    )).scalars().all()

    # تجميع الاحتياجات بالمادة + إجمالي الكميات المحجوزة (غير المصروفة) لكل مادة
    needs: dict = {}
    committed: dict = {}
    for res in open_res:
        mid = res.material_id
        net = float(res.required_qty) - float(res.issued_qty) - float(res.reserved_qty)
        if net > 0:
            needs[mid] = needs.get(mid, 0) + net
        reserved_open = float(res.reserved_qty) - float(res.issued_qty)
        if reserved_open > 0:
            committed[mid] = committed.get(mid, 0) + reserved_open

    # حذف اقتراحات قديمة pending
    old = (await db.execute(
        select(MrpSuggestion).where(
            MrpSuggestion.status == 'pending',
            MrpSuggestion.tenant_id == tenant_id,
        )
    )).scalars().all()
    for s in old:
        await db.delete(s)

    suggestions = []
    for mat_id, open_need in needs.items():
        mat = await db.get(Material, mat_id)
        if not mat or mat.tenant_id != tenant_id:
            continue

        stock     = float(mat.stock_qty)
        reorder   = float(mat.reorder_level or 0)
        reserved  = committed.get(mat_id, 0)
        available = stock - reserved
        shortage  = open_need + reorder - available

        if shortage <= 0:
            continue

        min_qty   = float(mat.min_order_qty or shortage)
        order_qty = max(shortage, min_qty)
        lead_time = int(mat.lead_time_days or 7)
        est_cost  = order_qty * float(mat.last_purchase_price or mat.unit_cost)

        sugg = MrpSuggestion(
            tenant_id=tenant_id,
            material_id=mat_id,
            current_stock=stock,
            required_qty=open_need,
            suggested_qty=order_qty,
            estimated_cost=est_cost,
            order_by_date=date.today(),
            expected_date=date.today() + timedelta(days=lead_time),
            status="pending",
        )
        db.add(sugg)

        suggestions.append({
            "material_id":   mat_id,
            "material_code": mat.code,
            "material_name": mat.name_ar,
            "current_stock": stock,
            "open_need":     open_need,
            "shortage":      shortage,
            "suggested_qty": order_qty,
            "estimated_cost": est_cost,
            "order_by":      str(date.today()),
            "expected_by":   str(date.today() + timedelta(days=lead_time)),
        })

    await db.commit()
    return sorted(suggestions, key=lambda x: x['shortage'], reverse=True)


# ═══════════════════════════════════════════════════════════════
# DASHBOARD ALERTS ENGINE
# ═══════════════════════════════════════════════════════════════

async def get_smart_alerts(db: AsyncSession, tenant_id: Optional[int] = None) -> List[dict]:
    """تنبيهات ذكية للداشبورد"""
    # محطة 28 (تصليح جذري — أمان متعدد المستأجرين): فلتر tenant_id صريح
    # على كل استعلام هنا كدفاع إضافي (defense in depth) — الدالة دي بتتنادى
    # من /dashboard/full، فمش هنعتمد فقط على الفلتر العام التلقائي
    # (with_loader_criteria) اللي بيتعطّل تلقائيًا لو current_tenant_id
    # ContextVar فاضية في سياق التنفيذ.
    if tenant_id is None:
        from app.core.database import current_tenant_id as _ctid_alerts0
        tenant_id = _ctid_alerts0.get()
    if tenant_id is None:
        # fail-closed: ممنوع الرجوع لـ tenant 1 (كان بيعرض بيانات ورشة تانية)
        return []
    alerts = []

    # 1. مواد نقص حرج (أقل من 50% من حد إعادة الطلب)
    mats = (await db.execute(
        select(Material).where(
            Material.stock_qty < Material.reorder_level * Decimal("0.5"),
            Material.reorder_level > 0,
            Material.tenant_id == tenant_id,
        ).order_by(Material.stock_qty).limit(5)
    )).scalars().all()

    for m in mats:
        shortage = float(m.reorder_level) - float(m.stock_qty)
        alerts.append({
            "level":   "critical",
            "icon":    "🔴",
            "title":   f"نقص حرج: {m.name_ar}",
            "detail":  f"المتوفر: {float(m.stock_qty):.1f} {m.unit} — النقص: {shortage:.1f}",
            "action":  "اطلب الآن",
            "link":    f"/materials/{m.id}",
        })

    # 2. أوامر إنتاج محجوبة بنقص مواد
    blocked_res = (await db.execute(
        select(MaterialReservation).where(
            MaterialReservation.status == 'partial',
            MaterialReservation.tenant_id == tenant_id,
        ).options(
            selectinload(MaterialReservation.material),
            selectinload(MaterialReservation.work_order),
        ).limit(5)
    )).scalars().all()

    for res in blocked_res:
        shortage = float(res.required_qty) - float(res.reserved_qty)
        if shortage > 0 and res.work_order and res.material:
            alerts.append({
                "level":  "warning",
                "icon":   "🟡",
                "title":  f"أمر محجوب: {res.work_order.code}",
                "detail": f"نقص في {res.material.name_ar}: {shortage:.1f} {res.material.unit}",
                "action": "عرض الأمر",
                "link":   f"/workorders/{res.work_order_id}",
            })

    # 3. فواتير متأخرة (يراعي خطط الأقساط عبر مصدر الحقيقة المركزي)
    from datetime import date as _date_alert
    from app.models.models import Invoice
    from app.services.invoice_due import get_invoice_due_status
    _today = _date_alert.today()
    # نجلب 100 فاتورة مفتوحة — مرتبة: الأقدم due_date أولاً
    # الدالة المركزية ستحدد هل فيها تأخير فعلي بغض النظر عن due_date الخام
    unpaid_invoices = (await db.execute(
        select(Invoice).where(
            Invoice.status.in_(["unpaid", "partial"]),
            Invoice.tenant_id == tenant_id,
        ).order_by(Invoice.due_date.nullsfirst()).limit(100)
    )).scalars().all()

    overdue_count = 0
    for inv in unpaid_invoices:
        if overdue_count >= 5:
            break
        due_status = await get_invoice_due_status(db, inv, _today)
        if not due_status.is_overdue:
            continue
        overdue_count += 1
        remaining = float((inv.total or 0) - (inv.paid_amount or 0))
        if due_status.has_schedule:
            detail = (
                f"متأخرة {due_status.days_overdue} يوم — "
                f"المتأخر الفعلي: {float(due_status.overdue_amount):.0f} ج "
                f"(عبر خطة أقساط)"
            )
        else:
            detail = f"متأخرة {due_status.days_overdue} يوم — المتبقي: {remaining:.0f} ج"
        alerts.append({
            "level":  "warning",
            "icon":   "🟠",
            "title":  f"فاتورة متأخرة: {inv.code}",
            "detail": detail,
            "action": "عرض التفاصيل",
            "link":   f"/installments?invoice={inv.id}",
        })

    # 4. ضمانات تنتهي خلال 30 يوم
    try:
        from app.models.models import WarrantyCertificate
        from datetime import timedelta as _td
        _soon = _today + _td(days=30)
        expiring = (await db.execute(
            select(WarrantyCertificate).where(
                WarrantyCertificate.warranty_end >= _today,
                WarrantyCertificate.warranty_end <= _soon,
                WarrantyCertificate.status == "issued",
                WarrantyCertificate.tenant_id == tenant_id,
            ).order_by(WarrantyCertificate.warranty_end).limit(5)
        )).scalars().all()
        for wc in expiring:
            days_left = (wc.warranty_end - _today).days
            alerts.append({
                "level":  "info" if days_left > 7 else "warning",
                "icon":   "🔵" if days_left > 7 else "🟡",
                "title":  f"ضمان ينتهي: {wc.product_name}",
                "detail": f"ينتهي خلال {days_left} يوم — {wc.code}",
                "action": "عرض الشهادة",
                "link":   f"/warranty-certs/{wc.id}",
            })
    except Exception:
        pass

    return alerts


# ═══════════════════════════════════════════════════════════════
# محطة 17: لوحة تتبع دورة حياة المعدة (Equipment Pipeline)
# CustomerOrder → WorkOrder → WIP → FG → DeliveryOrder
# ═══════════════════════════════════════════════════════════════

async def _wo_pipeline_row(db: AsyncSession, wo: "WorkOrder", tenant_id: int) -> dict:
    from decimal import Decimal, InvalidOperation
    from app.models.models import (
        Material as _Mat, WorkOrderStage as _Stage,
        DeliveryOrderLine as _DOL, DeliveryOrder as _DO,
    )

    wip_mat = await db.scalar(
        select(_Mat).where(_Mat.tenant_id == tenant_id, _Mat.code == f"WIP-{wo.code}")
    )
    fg_mat = await db.scalar(
        select(_Mat).where(_Mat.tenant_id == tenant_id, _Mat.code == f"FG-{wo.code}")
    )
    wip_qty = float(wip_mat.stock_qty) if wip_mat else 0.0
    fg_qty = float(fg_mat.stock_qty) if fg_mat else 0.0

    # الكمية المسلَّمة فعليًا: مجموع بنود التسليم المرتبطة بأمر الإنتاج ده
    # واللي أوامرها وصلت لحالة "delivered"
    delivery_lines = (await db.execute(
        select(_DOL, _DO.code, _DO.delivery_date)
        .join(_DO, _DO.id == _DOL.order_id)
        .where(_DOL.work_order_id == wo.id, _DO.status == "delivered")
    )).all()
    delivered_qty = Decimal("0")
    delivery_refs = []
    for line, do_code, delivery_date in delivery_lines:
        try:
            delivered_qty += Decimal(str(line.qty).strip())
        except (InvalidOperation, ValueError):
            pass
        delivery_refs.append({
            "delivery_order_code": do_code,
            "delivery_date": delivery_date.isoformat() if delivery_date else None,
        })

    # نسبة الإنجاز من مراحل الإنتاج (WorkOrderStage) لو موجودة
    stages = (await db.execute(
        select(_Stage).where(_Stage.work_order_id == wo.id)
    )).scalars().all()
    progress_percent = None
    if stages:
        done = sum(1 for s in stages if s.status == "done")
        progress_percent = round(done / len(stages) * 100)

    # تحديد حالة الـpipeline النهائية
    if wo.status != "done":
        pipeline_status = "تحت التصنيع" if wip_qty > 0 else "لم يبدأ التصنيع"
    elif float(delivered_qty) >= fg_qty and float(delivered_qty) > 0 and fg_qty == 0:
        pipeline_status = "تم التسليم بالكامل"
    elif float(delivered_qty) > 0 and fg_qty > 0:
        pipeline_status = "تم التسليم جزئياً"
    elif fg_qty > 0:
        pipeline_status = "جاهزة في المخزون (FG)"
    else:
        pipeline_status = "مكتملة (بدون رصيد FG مسجَّل)"

    equipment_name = wo.equipment.name_ar if wo.equipment else None
    customer_name = None
    customer_order_code = None
    is_stock_based = wo.customer_order_id is None
    if wo.customer_order_id and wo.customer_order:
        customer_order_code = wo.customer_order.code
        if wo.customer_order.customer:
            customer_name = wo.customer_order.customer.name

    return {
        "work_order_id":   wo.id,
        "work_order_code": wo.code,
        "equipment_id":    wo.equipment_id,
        "equipment_name":  equipment_name,
        "quantity":        float(wo.quantity or 1),
        "status":          wo.status,
        "pipeline_status": pipeline_status,
        "is_stock_based":  is_stock_based,
        "customer_order_id":   wo.customer_order_id,
        "customer_order_code": customer_order_code,
        "customer_name":       customer_name,
        "wip_qty":         wip_qty,
        "fg_qty":          fg_qty,
        "delivered_qty":   float(delivered_qty),
        "progress_percent": progress_percent,
        "start_date":      wo.start_date.isoformat() if wo.start_date else None,
        "end_date":        wo.end_date.isoformat() if wo.end_date else None,
        "deliveries":      delivery_refs,
    }


async def get_equipment_pipeline(
    db: AsyncSession, tenant_id: int, q: Optional[str] = None,
) -> List[dict]:
    """
    محطة 17: يرجّع صف واحد لكل أمر إنتاج (= معدة قيد التتبع) يجاوب على
    "المعدة X من أمر البيع Y — دلوقتي فين؟" — بربط WorkOrder مع أرصدة
    WIP/FG الحقيقية (محطة 14+15) ومع بنود التسليم الفعلية (محطة 16).
    """
    query = (
        select(WorkOrder)
        .where(WorkOrder.tenant_id == tenant_id)
        .options(
            selectinload(WorkOrder.equipment),
            selectinload(WorkOrder.customer_order).selectinload(_CustomerOrder.customer),
        )
        .order_by(WorkOrder.id.desc())
    )
    work_orders = (await db.execute(query)).scalars().all()
    rows = [await _wo_pipeline_row(db, wo, tenant_id) for wo in work_orders]

    if q:
        q_lower = q.strip().lower()
        rows = [
            r for r in rows
            if q_lower in (r["work_order_code"] or "").lower()
            or q_lower in (r["equipment_name"] or "").lower()
            or q_lower in (r["customer_name"] or "").lower()
            or q_lower in (r["customer_order_code"] or "").lower()
        ]
    return rows


async def get_equipment_pipeline_detail(db: AsyncSession, tenant_id: int, work_order_id: int) -> Optional[dict]:
    """نفس صف get_equipment_pipeline لكن لأمر إنتاج واحد بالتفصيل."""
    wo = (await db.execute(
        select(WorkOrder)
        .where(WorkOrder.id == work_order_id, WorkOrder.tenant_id == tenant_id)
        .options(
            selectinload(WorkOrder.equipment),
            selectinload(WorkOrder.customer_order).selectinload(_CustomerOrder.customer),
        )
    )).scalar_one_or_none()
    if not wo:
        return None
    return await _wo_pipeline_row(db, wo, tenant_id)


# ═══════════════════════════════════════════════════════════════
# محطة 18: Backfill لأوامر الإنتاج القديمة (قبل محطة 14/15)
# ═══════════════════════════════════════════════════════════════

async def backfill_equipment_pipeline(db: AsyncSession, tenant_id: int, dry_run: bool = True) -> dict:
    """
    أوامر الإنتاج اللي اتعملت قبل محطة 14/15 معندهاش WIP-{code}/FG-{code}
    مسجّلة، فمش هتظهر صح في لوحة الـPipeline. الدالة دي بتمشي على كل
    أوامر الإنتاج القديمة وتسجّل لها رصيد فعلي حسب حالتها الحالية:

      - status == "done"                          → تستقبل FG (وتطفئ WIP لو موجود)
      - status in (draft, in_progress) + عليها تكلفة فعلية (يبقى اتصرفلها مواد) → تسجّل WIP
      - غير كده (لسه معندهاش أي صرف)              → تتجاهل (فعلاً "لم يبدأ التصنيع")

    Idempotent تمامًا: لو المادة already موجودة بتتخطاها (skipped) — آمن
    تشغّلها أكتر من مرة أو على بيانات فيها بعض الأوامر اتعملها المحطات
    الجديدة بالفعل.

    dry_run=True (الافتراضي): بيرجّع تقرير بس من غير أي تعديل فعلي —
    شغّلها كده الأول للمراجعة، وبعدين dry_run=False للتنفيذ الفعلي.
    """
    from app.models.models import Material as _Mat

    wos = (await db.execute(
        select(WorkOrder)
        .where(WorkOrder.tenant_id == tenant_id)
        .options(selectinload(WorkOrder.equipment))
    )).scalars().all()

    report = {"wip_created": [], "fg_created": [], "skipped": [], "dry_run": dry_run}

    for wo in wos:
        existing_wip = await db.scalar(
            select(_Mat).where(_Mat.tenant_id == tenant_id, _Mat.code == f"WIP-{wo.code}")
        )
        existing_fg = await db.scalar(
            select(_Mat).where(_Mat.tenant_id == tenant_id, _Mat.code == f"FG-{wo.code}")
        )

        if wo.status == "done":
            if existing_fg:
                report["skipped"].append(wo.code)
                continue
            if dry_run:
                report["fg_created"].append(wo.code)
                continue
            await _release_wip_and_receive_fg(db, wo, wo.equipment, tenant_id, source_doc_type="production_backfill")
            report["fg_created"].append(wo.code)

        elif wo.status in ("draft", "in_progress") and (wo.actual_cost or Decimal("0")) > 0:
            if existing_wip:
                report["skipped"].append(wo.code)
                continue
            if dry_run:
                report["wip_created"].append(wo.code)
                continue
            await _sync_wip_quant(db, wo, tenant_id)
            report["wip_created"].append(wo.code)

        else:
            report["skipped"].append(wo.code)

    if not dry_run:
        await db.commit()
    return report
