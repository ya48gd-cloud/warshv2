"""
Production Service — التكامل الأوتوماتيكي للإنتاج
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    WorkOrder, Material, BOMLine, Equipment,
    MaterialReservation, ScrapRecord, StockMovement,
    CostLine, MrpSuggestion,
)


# ═══════════════════════════════════════════════════════════════
# STEP 1: حجز المواد أوتوماتيك عند إنشاء أمر الإنتاج
# ═══════════════════════════════════════════════════════════════

async def auto_reserve_materials(db: AsyncSession, work_order_id: int) -> dict:
    """
    يقرأ BOM للمعدة → ينشئ حجوزات → يخصم من المخزن (soft lock)
    Returns: {"reserved": N, "shortages": [...], "blocked": bool}
    """
    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        return {"error": "أمر الإنتاج غير موجود"}

    # جلب BOM كاملة (recursive)
    bom_lines = await _get_full_bom(db, wo.equipment_id)

    # حذف الحجوزات القديمة لو موجودة
    old_res = (await db.execute(
        select(MaterialReservation).where(MaterialReservation.work_order_id == work_order_id)
    )).scalars().all()
    for r in old_res:
        # أعد الكمية المحجوزة للمخزن
        mat = await db.get(Material, r.material_id)
        if mat:
            mat.stock_qty += r.reserved_qty
        await db.delete(r)
    await db.flush()

    reservations_created = []
    shortages = []

    for line in bom_lines:
        mat = await db.get(Material, line['material_id'])
        if not mat:
            continue

        required = Decimal(str(line['qty']))
        available = mat.stock_qty
        can_reserve = min(required, available)

        reservation = MaterialReservation(
            work_order_id=work_order_id,
            material_id=line['material_id'],
            required_qty=required,
            reserved_qty=can_reserve,
            status='reserved' if can_reserve >= required else 'partial',
            reserved_at=date.today(),
        )
        db.add(reservation)

        # خصم مؤقت من المخزن (soft lock)
        mat.stock_qty -= can_reserve

        if can_reserve < required:
            shortages.append({
                "material_id":   mat.id,
                "material_code": mat.code,
                "material_name": mat.name_ar,
                "required":      float(required),
                "available":     float(available),
                "shortage":      float(required - can_reserve),
            })

        reservations_created.append(line['material_id'])

    await db.commit()

    return {
        "reserved":      len(reservations_created),
        "shortages":     shortages,
        "blocked":       len(shortages) > 0,
        "work_order":    wo.code,
    }


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
    """
    wo  = await db.get(WorkOrder, work_order_id)
    mat = await db.get(Material, material_id)
    if not wo or not mat:
        return {"error": "بيانات غير صحيحة"}

    # البحث عن الحجز
    res = (await db.execute(
        select(MaterialReservation).where(
            MaterialReservation.work_order_id == work_order_id,
            MaterialReservation.material_id == material_id,
        )
    )).scalar_one_or_none()

    total_cost = qty_to_issue * mat.unit_cost

    # تسجيل حركة مخزون (الخصم الفعلي)
    movement = StockMovement(
        material_id=material_id,
        movement_type="out",
        qty=qty_to_issue,
        unit_cost=mat.unit_cost,
        total_cost=total_cost,
        reference=f"{wo.code}-{stage}",
        movement_date=date.today(),
        destination="workshop",
        destination_ref=wo.code,
    )
    db.add(movement)

    # تحديث الحجز
    if res:
        res.issued_qty += qty_to_issue
        if res.issued_qty >= res.required_qty:
            res.status = "fully_issued"
        else:
            res.status = "partially_issued"

    # سطر تكلفة فعلية
    cost_line = CostLine(
        work_order_id=work_order_id,
        cost_type="material",
        description=f"{mat.name_ar} — {stage}",
        qty=qty_to_issue,
        unit_cost=mat.unit_cost,
        total_cost=total_cost,
        material_id=material_id,
    )
    db.add(cost_line)

    # تحديث التكلفة الفعلية
    wo.actual_cost += total_cost

    await db.commit()

    return {
        "issued":               float(qty_to_issue),
        "unit_cost":            float(mat.unit_cost),
        "total_cost":           float(total_cost),
        "wo_actual_cost":       float(wo.actual_cost),
        "reservation_status":   res.status if res else "no_reservation",
    }


# ═══════════════════════════════════════════════════════════════
# STEP 3: تسجيل هالك
# ═══════════════════════════════════════════════════════════════

async def record_scrap(
    db: AsyncSession,
    work_order_id: int,
    material_id: int,
    scrap_qty: Decimal,
    reason: str,
    stage: str,
) -> dict:
    """تسجيل هالك + إضافة تكلفة + تحديث الحجز"""
    wo  = await db.get(WorkOrder, work_order_id)
    mat = await db.get(Material, material_id)
    if not wo or not mat:
        return {"error": "بيانات غير صحيحة"}

    cost = scrap_qty * mat.unit_cost

    # سجل الهالك
    scrap = ScrapRecord(
        work_order_id=work_order_id,
        material_id=material_id,
        qty=scrap_qty,
        unit_cost=mat.unit_cost,
        total_cost=cost,
        reason=reason,
        stage=stage,
    )
    db.add(scrap)

    # تكلفة الهالك
    db.add(CostLine(
        work_order_id=work_order_id,
        cost_type="overhead",
        description=f"هالك: {mat.name_ar} — {reason}",
        qty=scrap_qty,
        unit_cost=mat.unit_cost,
        total_cost=cost,
    ))
    wo.actual_cost += cost

    # تحديث الحجز
    res = (await db.execute(
        select(MaterialReservation).where(
            MaterialReservation.work_order_id == work_order_id,
            MaterialReservation.material_id == material_id,
        )
    )).scalar_one_or_none()
    if res:
        res.scrap_qty += scrap_qty

    await db.commit()

    return {
        "scrap_qty":  float(scrap_qty),
        "scrap_cost": float(cost),
        "stage":      stage,
        "reason":     reason,
    }


# ═══════════════════════════════════════════════════════════════
# STEP 4: إنهاء أمر الإنتاج
# ═══════════════════════════════════════════════════════════════

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

    eq = await db.get(Equipment, wo.equipment_id) if wo.equipment_id else None

    # إغلاق الحجوزات وإرجاع الزائد
    reservations = (await db.execute(
        select(MaterialReservation).where(
            MaterialReservation.work_order_id == work_order_id,
            MaterialReservation.status != 'closed',
        )
    )).scalars().all()

    returned_value = Decimal("0")
    for res in reservations:
        surplus = res.reserved_qty - res.issued_qty - res.scrap_qty
        if surplus > 0:
            mat = await db.get(Material, res.material_id)
            if mat:
                mat.stock_qty += surplus
                surplus_cost = surplus * mat.unit_cost
                returned_value += surplus_cost
                # سجل حركة الإرجاع
                db.add(StockMovement(
                    material_id=res.material_id,
                    movement_type="in",
                    qty=surplus,
                    unit_cost=mat.unit_cost,
                    total_cost=surplus_cost,
                    reference=f"{wo.code}-RETURN",
                    movement_date=date.today(),
                ))
        res.status = "closed"

    # تسجيل المعدة كتام صنع
    if eq:
        eq.in_stock = True

    # إنهاء الأمر
    wo.status = "done"
    wo.end_date = date.today()

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
        "status":           "completed",
        "work_order":       wo.code,
        "actual_cost":      float(wo.actual_cost),
        "planned_cost":     float(wo.planned_cost or 0),
        "variance_pct":     variance_pct,
        "returned_value":   float(returned_value),
        "estimated_profit": profit,
        "equipment_in_stock": eq.in_stock if eq else None,
    }


# ═══════════════════════════════════════════════════════════════
# MRP ENGINE — محرك التخطيط
# ═══════════════════════════════════════════════════════════════

async def run_mrp(db: AsyncSession) -> List[dict]:
    """
    MRP بسيط:
    للكل مادة:
      Net = Σ(required من WOs مفتوحة) + reorder_level - stock_qty
      لو Net > 0 → اقترح طلبية
    """
    # احتياجات أوامر الإنتاج المفتوحة
    open_res = (await db.execute(
        select(MaterialReservation).where(
            MaterialReservation.status.in_(['pending', 'partial', 'reserved', 'partially_issued'])
        ).options(selectinload(MaterialReservation.material))
    )).scalars().all()

    # تجميع الاحتياجات بالمادة
    needs: dict = {}
    for res in open_res:
        mid = res.material_id
        net = float(res.required_qty) - float(res.issued_qty) - float(res.reserved_qty)
        if net > 0:
            needs[mid] = needs.get(mid, 0) + net

    # حذف اقتراحات قديمة pending
    old = (await db.execute(
        select(MrpSuggestion).where(MrpSuggestion.status == 'pending')
    )).scalars().all()
    for s in old:
        await db.delete(s)

    suggestions = []
    for mat_id, open_need in needs.items():
        mat = await db.get(Material, mat_id)
        if not mat:
            continue

        stock   = float(mat.stock_qty)
        reorder = float(mat.reorder_level or 0)
        shortage = open_need + reorder - stock

        if shortage <= 0:
            continue

        min_qty   = float(mat.min_order_qty or shortage)
        order_qty = max(shortage, min_qty)
        lead_time = int(mat.lead_time_days or 7)
        est_cost  = order_qty * float(mat.last_purchase_price or mat.unit_cost)

        sugg = MrpSuggestion(
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

async def get_smart_alerts(db: AsyncSession) -> List[dict]:
    """تنبيهات ذكية للداشبورد"""
    alerts = []

    # 1. مواد نقص حرج (أقل من 50% من حد إعادة الطلب)
    mats = (await db.execute(
        select(Material).where(
            Material.stock_qty < Material.reorder_level * Decimal("0.5"),
            Material.reorder_level > 0,
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

    # 3. فواتير متأخرة
    from app.models.models import Invoice, Customer
    overdue = (await db.execute(
        select(Invoice).options(selectinload(Invoice.customer)).where(
            Invoice.due_date < date.today(),
            Invoice.status.in_(['unpaid', 'partial']),
        ).order_by(Invoice.due_date).limit(5)
    )).scalars().all()

    for inv in overdue:
        days = (date.today() - inv.due_date).days
        remaining = float(inv.total) - float(inv.paid_amount)
        alerts.append({
            "level":  "info",
            "icon":   "🔵",
            "title":  f"فاتورة متأخرة: {inv.code}",
            "detail": f"{inv.customer.name if inv.customer else ''} — متأخر {days} يوم — {remaining:,.0f} ج",
            "action": "عرض الفاتورة",
            "link":   f"/invoices/{inv.id}",
        })

    return alerts
