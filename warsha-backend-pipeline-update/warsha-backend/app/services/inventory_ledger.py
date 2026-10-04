"""
الخدمة المركزية لحركات المخزون (Inventory Ledger) — محطة 3.

الهدف: أي كود في النظام يحتاج يسجّل حركة مخزون (وارد/صادر) ويحدّث رصيد
مادة، لازم يمر من هنا بدل ما يبني StockMovement ويعدّل stock_qty يدويًا
في كل مكان لوحده. ده بيضمن:
  - رقم تسلسلي موحّد لكل حركة داخل نفس الـ tenant (movement_no).
  - تتبع مصدر الحركة (source_doc_type / source_doc_id) — مين اللي سبب
    الحركة دي (أمر شراء، أمر إنتاج، تسليم بيع، جرد... إلخ).
  - فحص واحد موحّد لمنع الصرف من كمية محجوزة (نفس فحص محطة 1)، بدل ما
    كل نقطة صرف في النظام تعمل الفحص ده لوحدها وتنسى حالة أو تانية.

هذه أول نسخة (v1): بتغطي فقط "وارد يدوي" و"صادر يدوي" (نفس المنطق اللي
كان موجود في app/api/inventory.py:record_movement قبل كده، بدون أي
تغيير في السلوك). محطات لاحقة (مشتريات، إنتاج، مبيعات، جرد) هتضيف
دوال جديدة على نفس الخدمة دي بدل ما تكرر منطقها في كل ملف.

لا يعمل commit() — ده مسؤولية الـ caller (الـ endpoint) زي ما كان الحال
قبل الريفاكتور، عشان تفضل كل الحركة جوه نفس الترانزاكشن اللي بيعملها.
"""
from datetime import date as _date
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import current_tenant_id
from app.models.models import Material, StockMovement, BinLocation, StockQuant
from app.services.inventory import get_available_qty, get_reserved_qty


async def _next_movement_no(db: AsyncSession, tenant_id: int) -> int:
    """رقم تسلسلي فريد للحركة داخل نفس الـ tenant (يبدأ من 1)."""
    result = await db.execute(
        select(func.max(StockMovement.movement_no))
        .where(StockMovement.tenant_id == tenant_id)
    )
    last = result.scalar()
    return (last or 0) + 1


async def _get_default_location(db: AsyncSession, tenant_id: int) -> BinLocation:
    """
    محطة 16/24 (تكملة، ترقيم الروادماب الأصلي) — قرار جذري: بدل ما تفضل
    "DEFAULT" موقع منفصل تمامًا عن "RAW-DEFAULT" (اللي محطات 19/21/22/24
    بتستخدمه)، بقت الدالة دي تُرجع نفس موقع RAW فعليًا. ده هو السبب الجذري
    اللي كان بيوقف أي فرض صارم لتوفر المخزون بالموقع (محطة 16) — أي مادة
    داخلة من شاشة الحركات العامة (من غير location_id صريح) كانت بتنزل في
    "DEFAULT"، بينما التحقق من التوفر لأمر الإنتاج كان (لو فُرض) هيدوّر في
    "RAW" — فبيرجع "الرصيد مش كافي" غلط حتى لو الرصيد الإجمالي فعلاً كافي.

    توحيد الموقعين في مكان واحد بيحل المشكلة من جذرها بدل ما نلف حواليها في
    كل محطة جديدة (زي ما حصل 3 مرات: محطة 16، 18، 25).
    """
    return await get_or_create_warehouse_bin(db, tenant_id, "RAW")


async def _sync_quant(
    db: AsyncSession, *, tenant_id: int, material_id: int, delta_qty: Decimal,
    lot_id: Optional[int] = None, location_id: Optional[int] = None,
) -> StockQuant:
    """
    يحدّث (أو ينشئ) صف StockQuant للمادة دي في الموقع المحدد (أو الموقع
    الافتراضي لو مفيش location_id) بنفس مقدار التغيير اللي حصل على
    Material.stock_qty، عشان SUM(StockQuant.qty) يفضل مطابق للرصيد الإجمالي.
    """
    if location_id is None:
        location = await _get_default_location(db, tenant_id)
        location_id = location.id
    quant = await db.scalar(
        select(StockQuant).where(
            StockQuant.material_id == material_id,
            StockQuant.location_id == location_id,
            StockQuant.lot_id == lot_id,
        )
    )
    if not quant:
        quant = StockQuant(
            tenant_id=tenant_id, material_id=material_id, location_id=location_id,
            lot_id=lot_id, qty=Decimal("0"),
        )
        db.add(quant)
        await db.flush()
    quant.qty = (quant.qty or Decimal("0")) + delta_qty
    return quant


def is_import_supplier(supplier) -> bool:
    """
    محطة 15/19/21 (الروادماب الأصلي بترقيمه): تصنيف المورد محلي/مستورد،
    يحدد أي مخزن (RAW أو IMPORTED) يستقبل بضاعته. منقولة هنا من
    purchase_orders.py عشان purchases.py (فواتير الشراء اليدوية) تقدر
    تستخدمها كمان بدل ما تكرر نفس المنطق.

    الأولوية دايمًا لتصنيف Supplier.is_import الصريح لو محدد. لو None (لسه
    محدّش صنّف المورد صراحة)، نرجع لـ heuristic كلمات مفتاحية على
    supplier_type كـ fallback (موثّق كـ DEC-03 في تقرير الـ BRD).
    """
    if not supplier:
        return False
    if supplier.is_import is not None:
        return supplier.is_import
    if not supplier.supplier_type:
        return False
    raw = supplier.supplier_type.strip()
    t = raw.lower()
    keywords_ar = ("استيراد", "مستورد", "أجنبي", "خارجي")
    keywords_en = ("import", "foreign", "overseas")
    return any(k in raw for k in keywords_ar) or any(k in t for k in keywords_en)


async def get_or_create_warehouse_bin(db: AsyncSession, tenant_id: int, warehouse_code: str) -> BinLocation:
    """
    محطة 14: يرجّع (أو ينشئ) BinLocation افتراضي واحد مربوط بالمخزن
    المنطقي (Warehouse) بكوده (RAW/WIP/FG/...).

    قبل كده كانت جداول Warehouse مجرد سجلات (8 مخازن افتراضية) من غير
    أي BinLocation فعلي مرتبط بيها، فحركات الإنتاج كانت مستحيل تسجّل
    رصيد في "WIP" أو "FG" فعليًا رغم إن اسم المخزن موجود. الدالة دي هي
    الجسر المفقود بين الاتنين.
    """
    from app.models.models import Warehouse

    bin_code = f"{warehouse_code}-DEFAULT"
    loc = await db.scalar(
        select(BinLocation).where(BinLocation.tenant_id == tenant_id, BinLocation.code == bin_code)
    )
    if loc:
        return loc

    wh = await db.scalar(
        select(Warehouse).where(Warehouse.tenant_id == tenant_id, Warehouse.code == warehouse_code)
    )
    loc = BinLocation(
        tenant_id=tenant_id,
        code=bin_code,
        name_ar=(wh.name_ar if wh else warehouse_code),
        warehouse_id=(wh.id if wh else None),
        location_type="physical",
    )
    db.add(loc)
    await db.flush()
    return loc


_COMPOSITE_PREFIX_WAREHOUSE = (
    ("WIP-", "WIP"), ("FG-", "FG"), ("SCRAP-", "SCRAP"), ("REMNANT-", "REMNANT"),
)


async def reconcile_unlocated_stock(db: AsyncSession) -> int:
    """
    يضمن إن SUM(StockQuant.qty) لكل مادة = Material.stock_qty.

    المخزون اللي اتسجّل قبل تفعيل المواقع (بيانات قديمة / ترحيل من Access /
    حركات من غير location) بيبقى جوه stock_qty بس من غير أي StockQuant، فكل
    حاجة بتعتمد على رصيد الموقع (الحجز لأوامر الإنتاج، التحويل بين المخازن،
    عرض رصيد كل مخزن) كانت بتشوفه صفر. الفرق الموجب بيتحط في المخزن الطبيعي
    للمادة: WIP/FG/SCRAP/REMNANT للمواد المركّبة حسب الكود، وRAW لغير كده.
    الفرق السالب (مواقع أكتر من الإجمالي) بيتساب زي ما هو ويتسجل في اللوج.
    Idempotent — آمن يتنادى مع كل تشغيل. لا يعمل commit.
    """
    import logging
    log = logging.getLogger("erp.inventory")

    located = dict((await db.execute(
        select(StockQuant.material_id, func.coalesce(func.sum(StockQuant.qty), 0))
        .group_by(StockQuant.material_id)
    )).all())
    fixed = 0
    for mat in (await db.execute(select(Material))).scalars():
        total = Decimal(str(mat.stock_qty or 0))
        gap = total - Decimal(str(located.get(mat.id, 0) or 0))
        if gap == 0:
            continue
        if gap < 0:
            log.warning("stock reconcile: material %s has more located stock than its total (%s)", mat.id, gap)
            continue
        wh_code = "RAW"
        for prefix, code in _COMPOSITE_PREFIX_WAREHOUSE:
            if (mat.code or "").startswith(prefix):
                wh_code = code
                break
        loc = await get_or_create_warehouse_bin(db, mat.tenant_id, wh_code)
        await _sync_quant(db, tenant_id=mat.tenant_id, material_id=mat.id,
                          delta_qty=gap, location_id=loc.id)
        fixed += 1
    if fixed:
        log.warning("stock reconcile: assigned un-located stock of %s materials to a warehouse", fixed)
    return fixed


async def fg_qty_to_release(db: AsyncSession, wo, fg_mat: Material, *,
                            extra_delivered: Decimal = Decimal("0"),
                            extra_invoiced: Decimal = Decimal("0")) -> Decimal:
    """
    المعدة لازم تخرج من مخزون تام الصنع (FG) مرة واحدة بس، سواء الفاتورة سبقت
    التسليم أو العكس. الفاتورة وأمر التسليم الاتنين بيخصموا FG — من غير
    التنسيق ده: فاتورة ثم تسليم = التسليم يترفض ("الرصيد غير كافٍ")، أو
    يتخصم مرتين لو فيه رصيد تاني.

    المطلوب إخراجه = max(المفوتر، المسلَّم) بعد العملية الحالية − اللي خرج فعلًا.
    """
    from app.models.models import DeliveryOrder, DeliveryOrderLine, Invoice

    delivered = Decimal("0")
    rows = (await db.execute(
        select(DeliveryOrderLine.qty)
        .join(DeliveryOrder, DeliveryOrder.id == DeliveryOrderLine.order_id)
        .where(DeliveryOrderLine.work_order_id == wo.id, DeliveryOrder.status == "delivered")
    )).scalars().all()
    for q in rows:
        try:
            delivered += Decimal(str(q).strip())
        except Exception:
            pass
    delivered += extra_delivered

    invoiced = extra_invoiced
    if await db.scalar(select(Invoice.id).where(Invoice.work_order_id == wo.id)):
        invoiced = max(invoiced, Decimal(str(wo.quantity or 1)))

    out_qty = await db.scalar(
        select(func.coalesce(func.sum(StockMovement.qty), 0)).where(
            StockMovement.material_id == fg_mat.id, StockMovement.movement_type == "out",
            StockMovement.source_doc_type.in_(("sales_invoice", "delivery")),
        )
    ) or 0
    back_qty = await db.scalar(
        select(func.coalesce(func.sum(StockMovement.qty), 0)).where(
            StockMovement.material_id == fg_mat.id, StockMovement.movement_type == "in",
            StockMovement.source_doc_type == "sales_invoice_delete",
        )
    ) or 0
    released = Decimal(str(out_qty)) - Decimal(str(back_qty))
    return max(Decimal("0"), max(invoiced, delivered) - released)


async def post_receipt(
    db: AsyncSession,
    *,
    material: Material,
    qty: Decimal,
    unit_cost: Decimal,
    movement_date: Optional[_date] = None,
    source_doc_type: Optional[str] = None,
    source_doc_id: Optional[int] = None,
    lot_id: Optional[int] = None,
    destination_location_id: Optional[int] = None,
    **extra_fields,
) -> StockMovement:
    """
    تسجيل حركة وارد (in) + زيادة رصيد المادة (+ الـ StockQuant المطابق له).
    المصدر الوحيد المفروض لإنشاء حركات "in" في كل النظام.

    محطة 12: destination_location_id بيحدد الموقع اللي البضاعة داخلة عليه.
    لو مش متبعوت، بيتحول تلقائيًا لموقع الاستلام الافتراضي (نفس سلوك محطة 11).
    """
    if qty is None or qty <= 0:
        raise HTTPException(400, "كمية الوارد يجب أن تكون أكبر من صفر")
    if unit_cost is not None and unit_cost < 0:
        raise HTTPException(400, "سعر الوحدة لا يمكن أن يكون سالباً")
    tenant_id = current_tenant_id.get() or material.tenant_id or 1
    if destination_location_id is None:
        default_loc = await _get_default_location(db, tenant_id)
        destination_location_id = default_loc.id

    movement = StockMovement(
        tenant_id=tenant_id,
        material_id=material.id,
        movement_type="in",
        qty=qty,
        unit_cost=unit_cost,
        total_cost=qty * unit_cost,
        movement_date=movement_date or _date.today(),
        movement_no=await _next_movement_no(db, tenant_id),
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        destination_location_id=destination_location_id,
        **extra_fields,
    )
    db.add(movement)

    # محطة 9: سياسة التقييم بقت فعلية مش بس تصنيف —
    # "moving_average" (الافتراضي): متوسط تكلفة مرجّح حقيقي.
    # "last_price": آخر سعر استلام مباشرة (سعر اليوم) — من غير أي حساب متوسط،
    # مناسب لو المورد بيغيّر سعره كل مرة والمطلوب السعر الحالي بس مش تاريخي.
    # "standard": يسيب unit_cost ثابت (بيتغير يدوي بس من شاشة تعديل المادة).
    # "fifo": الطبقات التفصيلية هتتضاف في محطة 25 — لحد كده بيتعامل زي
    # moving_average على المستوى الإجمالي عشان السعر المعروض يفضل واقعي.
    if unit_cost and unit_cost > 0:
        policy = getattr(material, "valuation_method", "moving_average") or "moving_average"
        if policy == "standard":
            pass  # السعر ثابت، مش بيتغير تلقائيًا من الاستلام
        elif policy == "last_price":
            material.unit_cost = unit_cost  # آخر سعر وارد مباشرة، بدون متوسط
        else:
            old_qty  = material.stock_qty or Decimal("0")
            old_cost = material.unit_cost or Decimal("0")
            if old_qty > 0:
                material.unit_cost = ((old_qty * old_cost) + (qty * unit_cost)) / (old_qty + qty)
            else:
                material.unit_cost = unit_cost

    material.stock_qty += qty
    # زامن StockQuant بنفس التغيير في نفس الموقع اللي الحركة راحت عليه
    await _sync_quant(db, tenant_id=tenant_id, material_id=material.id, delta_qty=qty,
                       lot_id=lot_id, location_id=destination_location_id)
    return movement


async def post_issue(
    db: AsyncSession,
    *,
    material: Material,
    qty: Decimal,
    unit_cost: Decimal,
    movement_date: Optional[_date] = None,
    source_doc_type: Optional[str] = None,
    source_doc_id: Optional[int] = None,
    lot_id: Optional[int] = None,
    source_location_id: Optional[int] = None,
    **extra_fields,
) -> StockMovement:
    """
    تسجيل حركة صادر (out) + إنقاص رصيد المادة (+ الـ StockQuant المطابق له)،
    بعد فحص المتاح (الرصيد الكلي - المحجوز لأوامر إنتاج أخرى — نفس فحص محطة 1).
    المصدر الوحيد المفروض لإنشاء حركات "out" في كل النظام.

    محطة 12: source_location_id بيحدد الموقع اللي بيتصرف منه. لو مش
    متبعوت، بيتحول تلقائيًا لموقع الاستلام الافتراضي (نفس سلوك محطة 11).
    """
    if qty is None or qty <= 0:
        raise HTTPException(400, "كمية الصادر يجب أن تكون أكبر من صفر")
    if unit_cost is not None and unit_cost < 0:
        raise HTTPException(400, "سعر الوحدة لا يمكن أن يكون سالباً")
    reserved  = await get_reserved_qty(db, material.id)
    available = await get_available_qty(db, material.id, material.stock_qty)
    if qty > available:
        raise HTTPException(
            400,
            f"الكمية المتاحة غير كافية — الرصيد الكلي: {float(material.stock_qty or 0)} {material.unit}، "
            f"المحجوز لأوامر إنتاج: {float(reserved)} {material.unit}، "
            f"المتاح فعليًا: {float(available)} {material.unit}، "
            f"المطلوب صرفه: {float(qty)} {material.unit}",
        )

    tenant_id = current_tenant_id.get() or material.tenant_id or 1
    if source_location_id is None:
        default_loc = await _get_default_location(db, tenant_id)
        source_location_id = default_loc.id

    movement = StockMovement(
        tenant_id=tenant_id,
        material_id=material.id,
        movement_type="out",
        qty=qty,
        unit_cost=unit_cost,
        total_cost=qty * unit_cost,
        movement_date=movement_date or _date.today(),
        movement_no=await _next_movement_no(db, tenant_id),
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        source_location_id=source_location_id,
        **extra_fields,
    )
    db.add(movement)
    material.stock_qty -= qty
    # زامن StockQuant بنفس التغيير (بالسالب) في نفس الموقع اللي اتصرف منه
    await _sync_quant(db, tenant_id=tenant_id, material_id=material.id, delta_qty=-qty,
                       lot_id=lot_id, location_id=source_location_id)
    return movement


async def post_transfer(
    db: AsyncSession,
    *,
    material: Material,
    qty: Decimal,
    source_location_id: int,
    destination_location_id: int,
    movement_date: Optional[_date] = None,
    source_doc_type: Optional[str] = None,
    source_doc_id: Optional[int] = None,
    lot_id: Optional[int] = None,
    **extra_fields,
) -> StockMovement:
    """
    محطة 12: تحويل كمية من موقع لموقع تاني لنفس المادة، بدون أي تغيير في
    Material.stock_qty (الرصيد الإجمالي للشركة زي ما هو، بس اتوزّع مختلف
    بين المواقع). المصدر الوحيد المفروض لإنشاء حركات "transfer".
    """
    if qty is None or qty <= 0:
        raise HTTPException(400, "كمية التحويل يجب أن تكون أكبر من صفر")
    if source_location_id == destination_location_id:
        raise HTTPException(400, "موقع المصدر والوجهة لازم يكونوا مختلفين")

    # فحص المتاح في الموقع المصدر تحديدًا (مش الرصيد الكلي للمادة) —
    # التحويل ميقدرش ياخد من موقع فيه رصيد أقل من الكمية المطلوبة
    source_qty = await db.scalar(
        select(StockQuant.qty).where(
            StockQuant.material_id == material.id,
            StockQuant.location_id == source_location_id,
            StockQuant.lot_id == lot_id,
        )
    ) or Decimal("0")
    if qty > source_qty:
        raise HTTPException(
            400,
            f"الكمية المتاحة في الموقع المصدر ({float(source_qty)} {material.unit}) "
            f"أقل من كمية التحويل المطلوبة ({float(qty)} {material.unit})",
        )

    tenant_id = current_tenant_id.get() or material.tenant_id or 1
    movement = StockMovement(
        tenant_id=tenant_id,
        material_id=material.id,
        movement_type="transfer",
        qty=qty,
        unit_cost=material.unit_cost or Decimal("0"),
        total_cost=qty * (material.unit_cost or Decimal("0")),
        movement_date=movement_date or _date.today(),
        movement_no=await _next_movement_no(db, tenant_id),
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        source_location_id=source_location_id,
        destination_location_id=destination_location_id,
        **extra_fields,
    )
    db.add(movement)
    # Material.stock_qty ميتغيرش — التحويل توزيع داخلي بس، مش دخول/خروج فعلي
    await _sync_quant(db, tenant_id=tenant_id, material_id=material.id, delta_qty=-qty,
                       lot_id=lot_id, location_id=source_location_id)
    await _sync_quant(db, tenant_id=tenant_id, material_id=material.id, delta_qty=qty,
                       lot_id=lot_id, location_id=destination_location_id)
    return movement
