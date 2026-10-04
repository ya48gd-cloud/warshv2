"""
Delivery Orders API — أوامر التوريد
GET    /delivery-orders              ← قائمة الأوامر
POST   /delivery-orders              ← إنشاء أمر جديد
GET    /delivery-orders/{id}         ← تفاصيل أمر
PATCH  /delivery-orders/{id}/status  ← تغيير الحالة
DELETE /delivery-orders/{id}         ← حذف (مسودة فقط)
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db, current_tenant_id
from app.api.auth import require_any
from app.models.models import DeliveryOrder, DeliveryOrderLine, Customer

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


async def _block_viewer_writes(request: Request, user: dict = Depends(require_any)) -> dict:
    """إصلاح ثغرة صلاحيات: مستخدم "مشاهد" (viewer) كان يقدر ينشئ/يسلّم أوامر
    توريد (بتخصم مخزون FG وتسجّل COGS) بطلب API مباشر."""
    if request.method.upper() not in _READ_METHODS and user.get("role") == "viewer":
        raise HTTPException(403, "صلاحية 'viewer' (مشاهد) لا تسمح بالتعديل")
    return user


router = APIRouter(
    prefix="/delivery-orders",
    tags=["delivery-orders"],
    dependencies=[Depends(require_any), Depends(_block_viewer_writes)],
)


# ── Schemas ────────────────────────────────────────────────────────────────────

class LineIn(BaseModel):
    description: str
    model_no:    Optional[str] = None
    qty:         str = "1"
    unit:        str = "قطعة"
    notes:       Optional[str] = None
    # محطة 16: ربط اختياري بمعدة/أمر إنتاج فعلي (عشان الخصم التلقائي من FG)
    equipment_id:  Optional[int] = None
    work_order_id: Optional[int] = None

class DeliveryOrderIn(BaseModel):
    order_date:       str
    customer_id:      int
    reference:        Optional[str] = None
    notes:            Optional[str] = None
    delivery_date:    Optional[str] = None
    delivery_address: Optional[str] = None
    received_by:      Optional[str] = None
    receiver_title:   Optional[str] = None
    receiver_phone:   Optional[str] = None
    delivery_notes:   Optional[str] = None
    vehicle_plate:    str
    vehicle_type:     Optional[str] = None
    driver_name:      str
    driver_license:   Optional[str] = None
    driver_phone:     Optional[str] = None
    lines:            List[LineIn]

class StatusIn(BaseModel):
    status: str  # confirmed / delivered / cancelled


# ── Helpers ────────────────────────────────────────────────────────────────────

def _tid() -> int:
    tid = current_tenant_id.get()
    if tid is None:
        raise HTTPException(401, "غير مسموح")
    return tid


def _order_dict(o: DeliveryOrder) -> dict:
    return {
        "id":               o.id,
        "code":             o.code,
        "status":           o.status,
        "order_date":       o.order_date.isoformat() if o.order_date else None,
        "customer_id":      o.customer_id,
        "customer_name":    o.customer.name if o.customer else "",
        "customer_phone":   o.customer.phone if o.customer else "",
        "customer_address": o.customer.address if o.customer else "",
        "reference":        o.reference,
        "notes":            o.notes,
        "delivery_date":    o.delivery_date.isoformat() if o.delivery_date else None,
        "delivery_address": o.delivery_address,
        "received_by":      o.received_by,
        "receiver_title":   o.receiver_title,
        "receiver_phone":   o.receiver_phone,
        "delivery_notes":   o.delivery_notes,
        "vehicle_plate":    o.vehicle_plate,
        "vehicle_type":     o.vehicle_type,
        "driver_name":      o.driver_name,
        "driver_license":   o.driver_license,
        "driver_phone":     o.driver_phone,
        "created_at":       o.created_at.isoformat() if o.created_at else None,
        "lines": [
            {
                "id":            l.id,
                "description":   l.description,
                "model_no":      l.model_no,
                "qty":           l.qty,
                "unit":          l.unit,
                "notes":         l.notes,
                "equipment_id":  l.equipment_id,
                "work_order_id": l.work_order_id,
            }
            for l in (o.lines or [])
        ],
    }


async def _next_code(db: AsyncSession, tid: int) -> str:
    res = await db.execute(
        select(sqlfunc.count(DeliveryOrder.id)).where(DeliveryOrder.tenant_id == tid)
    )
    n = (res.scalar() or 0) + 1
    # إصلاح: الترقيم بالعدد بس كان بيكرر كود موجود بعد حذف أي مسودة
    # (مثلاً DO-0001..DO-0003 ثم حذف DO-0002 → الجديد ياخد DO-0003 تاني).
    while True:
        code = f"DO-{n:04d}"
        exists = await db.scalar(
            select(DeliveryOrder.id).where(DeliveryOrder.tenant_id == tid, DeliveryOrder.code == code)
        )
        if not exists:
            return code
        n += 1


def _parse_date(v: Optional[str], label: str) -> Optional[date]:
    if v is None or v == "":
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise HTTPException(400, f"{label} غير صالح: '{v}'")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("")
async def list_orders(db: AsyncSession = Depends(get_db)):
    tid = _tid()
    res = await db.execute(
        select(DeliveryOrder)
        .where(DeliveryOrder.tenant_id == tid)
        .options(selectinload(DeliveryOrder.customer), selectinload(DeliveryOrder.lines))
        .order_by(DeliveryOrder.id.desc())
    )
    return [_order_dict(o) for o in res.scalars()]


@router.post("", status_code=201)
async def create_order(data: DeliveryOrderIn, db: AsyncSession = Depends(get_db)):
    tid = _tid()

    if not data.lines:
        raise HTTPException(400, "أضف بنداً واحداً على الأقل")
    if not data.driver_name.strip():
        raise HTTPException(400, "اسم السائق مطلوب")
    if not data.vehicle_plate.strip():
        raise HTTPException(400, "رقم اللوحة مطلوب")
    order_date = _parse_date(data.order_date, "تاريخ الأمر")
    if order_date is None:
        raise HTTPException(400, "تاريخ الأمر مطلوب")
    delivery_date = _parse_date(data.delivery_date, "تاريخ التسليم")
    if delivery_date and delivery_date < order_date:
        raise HTTPException(400, "تاريخ التسليم لا يمكن أن يكون قبل تاريخ الأمر")
    from decimal import Decimal as _D, InvalidOperation as _IO
    from app.models.models import WorkOrder as _WO, Equipment as _Eq
    for l in data.lines:
        if not (l.description or "").strip():
            raise HTTPException(400, "وصف كل بند مطلوب")
        # إصلاح ثغرة تحقق: ربط البند بأمر إنتاج/معدة من tenant تاني أو غير
        # موجودين كان بيتقبل، والكمية الغلط ماكانتش بتظهر غير وقت التسليم.
        if l.work_order_id is not None:
            if not await db.get(_WO, l.work_order_id):
                raise HTTPException(404, f"البند '{l.description}': أمر الإنتاج المرتبط غير موجود")
            try:
                _q = _D(str(l.qty).strip())
            except (_IO, ValueError):
                raise HTTPException(400, f"البند '{l.description}': الكمية '{l.qty}' غير رقمية")
            if _q <= 0:
                raise HTTPException(400, f"البند '{l.description}': الكمية لازم تكون أكبر من صفر")
        if l.equipment_id is not None and not await db.get(_Eq, l.equipment_id):
            raise HTTPException(404, f"البند '{l.description}': المعدة المرتبطة غير موجودة")

    # تحقق من وجود العميل
    cust = await db.get(Customer, data.customer_id)
    if not cust or cust.tenant_id != tid:
        raise HTTPException(404, "العميل غير موجود")

    code = await _next_code(db, tid)

    order = DeliveryOrder(
        tenant_id=        tid,
        code=             code,
        status=           "draft",
        order_date=       order_date,
        customer_id=      data.customer_id,
        reference=        data.reference,
        notes=            data.notes,
        delivery_date=    delivery_date,
        delivery_address= data.delivery_address,
        received_by=      data.received_by,
        receiver_title=   data.receiver_title,
        receiver_phone=   data.receiver_phone,
        delivery_notes=   data.delivery_notes,
        vehicle_plate=    data.vehicle_plate,
        vehicle_type=     data.vehicle_type,
        driver_name=      data.driver_name,
        driver_license=   data.driver_license,
        driver_phone=     data.driver_phone,
    )
    db.add(order)
    await db.flush()

    for i, l in enumerate(data.lines):
        db.add(DeliveryOrderLine(
            order_id=      order.id,
            description=   l.description,
            model_no=      l.model_no,
            qty=           l.qty,
            unit=          l.unit,
            notes=         l.notes,
            sort_order=    i,
            equipment_id=  l.equipment_id,
            work_order_id= l.work_order_id,
        ))

    await db.commit()
    await db.refresh(order)

    res = await db.execute(
        select(DeliveryOrder)
        .where(DeliveryOrder.id == order.id)
        .options(selectinload(DeliveryOrder.customer), selectinload(DeliveryOrder.lines))
    )
    return _order_dict(res.scalar_one())


@router.get("/{order_id}")
async def get_order(order_id: int, db: AsyncSession = Depends(get_db)):
    tid = _tid()
    res = await db.execute(
        select(DeliveryOrder)
        .where(DeliveryOrder.id == order_id, DeliveryOrder.tenant_id == tid)
        .options(selectinload(DeliveryOrder.customer), selectinload(DeliveryOrder.lines))
    )
    o = res.scalar_one_or_none()
    if not o:
        raise HTTPException(404, "الأمر غير موجود")
    return _order_dict(o)


# محطة 16 (مُصحَّحة): خصم فعلي من موقع FG عند تسليم بند مربوط بأمر إنتاج.
#
# قرار تصميم: تسجيل أمر تسليم كـ"delivered" وهو فعليًا مش موجود في المخزون
# (رصيد FG مش كافي) بيدي سجل محاسبي/مخزني كاذب — ده أخطر من رفض العملية.
# فالسلوك الصح: نتحقق الأول من كل البنود المرتبطة، ولو أي بند فيه مشكلة
# نرفض التحويل بالكامل (400) قبل ما نغيّر أي حاجة، بدل ما نسجّل "تسليم"
# ونرمي تحذير بعد ما الضرر يبقى حصل.
async def _check_fg_availability(db: AsyncSession, tid: int, line: DeliveryOrderLine):
    """يتحقق فقط (من غير أي تعديل) — يرجّع (fg_material, qty) لو سليم، أو يرفع HTTPException(400) لو لأ."""
    from decimal import Decimal, InvalidOperation
    from app.models.models import Material as _Mat, WorkOrder as _WO
    from sqlalchemy import select as _sel

    wo = await db.get(_WO, line.work_order_id)
    if not wo or wo.tenant_id != tid:
        raise HTTPException(400, f"البند '{line.description}': أمر الإنتاج المرتبط غير موجود")

    fg_mat = (await db.execute(
        _sel(_Mat).where(_Mat.tenant_id == tid, _Mat.code == f"FG-{wo.code}")
    )).scalar_one_or_none()
    if not fg_mat:
        raise HTTPException(
            400,
            f"لا يمكن تسليم البند '{line.description}' — أمر الإنتاج {wo.code} لسه معندوش رصيد "
            f"جاهز في المخزون (FG). التصنيع لسه مايكملش."
        )

    try:
        qty = Decimal(str(line.qty).strip())
    except (InvalidOperation, ValueError):
        raise HTTPException(400, f"البند '{line.description}': الكمية '{line.qty}' غير رقمية")

    if qty <= 0:
        raise HTTPException(400, f"البند '{line.description}': الكمية لازم تكون أكبر من صفر")
    # لو المعدة اتفوترت قبل التسليم، الفاتورة هي اللي خصمتها من FG بالفعل —
    # التسليم مش لازم يخصمها تاني (كان بيترفض بـ"الرصيد غير كافٍ")
    from app.services.inventory_ledger import fg_qty_to_release
    qty = await fg_qty_to_release(db, wo, fg_mat, extra_delivered=qty)
    if qty > (fg_mat.stock_qty or Decimal("0")):
        raise HTTPException(
            400,
            f"لا يمكن تسليم البند '{line.description}' — الكمية المطلوبة ({qty}) أكبر من "
            f"الرصيد الفعلي المتاح في المخزون ({fg_mat.stock_qty}) لأمر الإنتاج {wo.code}"
        )
    return fg_mat, qty


async def _release_fg_stock_for_line(db: AsyncSession, tid: int, order: DeliveryOrder, fg_mat, qty) -> None:
    """التنفيذ الفعلي (بعد التحقق الناجح من _check_fg_availability)."""
    from decimal import Decimal
    from app.services.inventory_ledger import get_or_create_warehouse_bin, post_issue
    from app.services.auto_journal import journal_cogs

    if not qty or qty <= 0:
        return  # اتخصمت بالفعل وقت الفاتورة
    unit_cost = fg_mat.unit_cost or Decimal("0")
    cogs_amount = qty * unit_cost  # نحسبها قبل post_issue عشان نستخدم نفس أساس التكلفة بالظبط

    fg_bin = await get_or_create_warehouse_bin(db, tid, "FG")
    await post_issue(
        db, material=fg_mat, qty=qty, unit_cost=unit_cost,
        source_doc_type="delivery", source_doc_id=order.id,
        source_location_id=fg_bin.id,
        reference=order.code, notes=f"تسليم للعميل — {order.code}",
    )

    # محطة 22 (الروادماب الأصلي): قيد COGS فعلي عند التسليم — بدل ما يفضل
    # خصم مخزون بلا أي أثر محاسبي (نفس فئة الفجوة اللي بنصلّحها من أول
    # المشروع، هنا في نقطة "خروج البضاعة" بالذات)
    await journal_cogs(db, tid, order.id, order.code, cogs_amount, date.today())


@router.patch("/{order_id}/status")
async def update_status(order_id: int, body: StatusIn, db: AsyncSession = Depends(get_db)):
    tid = _tid()
    res = await db.execute(
        select(DeliveryOrder)
        .where(DeliveryOrder.id == order_id, DeliveryOrder.tenant_id == tid)
        .options(selectinload(DeliveryOrder.customer), selectinload(DeliveryOrder.lines))
    )
    o = res.scalar_one_or_none()
    if not o:
        raise HTTPException(404, "الأمر غير موجود")

    allowed = {
        "draft":     ["confirmed", "cancelled"],
        "confirmed": ["delivered", "cancelled"],
    }
    if body.status not in allowed.get(o.status, []):
        raise HTTPException(400, f"لا يمكن التحويل من {o.status} إلى {body.status}")

    if body.status == "delivered":
        linked_lines = [l for l in (o.lines or []) if l.work_order_id]
        # المرحلة 1: تحقق فقط — من غير أي تعديل. أول مشكلة بترفض العملية كاملة.
        checked = [(line, *(await _check_fg_availability(db, tid, line))) for line in linked_lines]
        # إصلاح: كل بند كان بيتفحص لوحده مقابل نفس الرصيد — بندين على نفس أمر
        # الإنتاج (كل واحد ≤ الرصيد ومجموعهم أكبر) كانوا بيعدّوا الفحص ويقع
        # post_issue في النص بعد ما جزء اتخصم. نفحص المجموع لكل مادة FG.
        need: dict = {}
        for line, fg_mat, qty in checked:
            need.setdefault(fg_mat.id, [fg_mat, 0])
            need[fg_mat.id][1] += qty
        for fg_mat, total_qty in need.values():
            if total_qty > (fg_mat.stock_qty or 0):
                raise HTTPException(
                    400,
                    f"مجموع الكميات المطلوب تسليمها من {fg_mat.code} ({total_qty}) أكبر من "
                    f"الرصيد الفعلي المتاح ({fg_mat.stock_qty})",
                )
        # المرحلة 2: التنفيذ الفعلي — مضمون ينجح لأن التحقق عدّى بالفعل.
        for line, fg_mat, qty in checked:
            await _release_fg_stock_for_line(db, tid, o, fg_mat, qty)

    o.status = body.status
    if body.status == "delivered" and not o.delivery_date:
        o.delivery_date = date.today()

    await db.commit()
    await db.refresh(o)
    return _order_dict(o)


@router.delete("/{order_id}", status_code=204)
async def delete_order(order_id: int, db: AsyncSession = Depends(get_db)):
    tid = _tid()
    res = await db.execute(
        select(DeliveryOrder)
        .where(DeliveryOrder.id == order_id, DeliveryOrder.tenant_id == tid)
    )
    o = res.scalar_one_or_none()
    if not o:
        raise HTTPException(404, "الأمر غير موجود")
    if o.status not in ("draft", "cancelled"):
        raise HTTPException(400, "لا يمكن حذف أمر مؤكد أو مُسلَّم")
    await db.delete(o)
    await db.commit()
