"""
ربط المدفوعات بالخزنة تلقائياً.
آمن: لو مفيش خزنة، مايعملش حاجة ومايكسرش الدفع.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from sqlalchemy import select, func
from app.models.models import Treasury, TreasuryTransaction, TreasuryCategory


def _get_tenant_id() -> int:
    """يجيب tenant_id الحالي من الـ context var، ولو مش موجود يرجع 1"""
    try:
        from app.core.database import current_tenant_id
        return current_tenant_id.get() or 1
    except Exception:
        return 1


async def _default_treasury(db):
    """يرجّع أول خزنة نقدية نشطة للـ tenant الحالي، أو None"""
    tid = _get_tenant_id()
    r = await db.execute(
        select(Treasury).where(
            Treasury.is_active == True,
            Treasury.tenant_id == tid,
        ).order_by(Treasury.kind.desc(), Treasury.id)  # cash قبل bank
    )
    return r.scalars().first()


async def _ensure_category(db, name, flow):
    tid = _get_tenant_id()
    r = await db.execute(
        select(TreasuryCategory).where(
            TreasuryCategory.name == name,
            TreasuryCategory.tenant_id == tid,
        )
    )
    c = r.scalar_one_or_none()
    if not c:
        c = TreasuryCategory(name=name, flow=flow, tenant_id=tid)
        db.add(c)
        await db.flush()
    return c


async def record_payment_in_treasury(db, *, amount, direction, source,
                                      linked_type, linked_id,
                                      category_name=None, description=None,
                                      reference=None, txn_date=None):
    """
    يسجّل حركة في الخزنة الافتراضية لو موجودة.
    بيمنع التكرار: لو الدفعة دي اتسجّلت قبل كده (نفس linked_type+linked_id) مايعيدش.
    """
    treasury = await _default_treasury(db)
    if not treasury:
        return None  # مفيش خزنة — مايعملش حاجة

    try:
        # تقريب لقرشين: العمود Numeric(14,2) — بدون التقريب الرصيد يتحدّث بقيمة غير المخزّنة
        amt = Decimal(str(amount or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not amt.is_finite() or amt <= 0:
        return None
    if direction not in ("in", "out"):
        return None

    tid = _get_tenant_id()

    # منع التكرار
    if linked_type and linked_id:
        ex = await db.execute(
            select(func.count()).select_from(TreasuryTransaction).where(
                TreasuryTransaction.linked_type == linked_type,
                TreasuryTransaction.linked_id == linked_id,
                TreasuryTransaction.source == source,
                TreasuryTransaction.tenant_id == tid,
            )
        )
        if (ex.scalar() or 0) > 0:
            return None  # متسجّلة قبل كده

    cat_id = None
    if category_name:
        cat = await _ensure_category(db, category_name, "in" if direction == "in" else "out")
        cat_id = cat.id

    txn = TreasuryTransaction(
        tenant_id=tid,
        treasury_id=treasury.id, direction=direction, amount=amt,
        category_id=cat_id, txn_date=txn_date or date.today(),
        description=description, reference=reference,
        source=source, linked_type=linked_type, linked_id=linked_id,
    )
    treasury.balance = (treasury.balance or Decimal("0")) + (amt if direction == "in" else -amt)
    db.add(txn)
    return txn
