"""
الخزنة — إدارة الصناديق النقدية والبنوك وحركات الأموال
- خزن متعددة (نقدي/بنك)
- حركات داخل/خارج يدوية
- فئات للتصنيف
- تحويل بين الخزن
- ربط تلقائي بمدفوعات العملاء والموردين
"""
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import (
    Treasury, TreasuryCategory, TreasuryTransaction,
)

router = APIRouter(prefix="/treasury", tags=["treasury"], dependencies=[Depends(require_any)])
print_router = APIRouter(prefix="/treasury", tags=["treasury_print"], dependencies=[Depends(require_any)])


# ════════════════════════════════════════════════════
# Schemas
# ════════════════════════════════════════════════════
class TreasuryIn(BaseModel):
    name: str
    kind: str = "cash"            # cash | bank
    opening_balance: Decimal = Decimal("0")
    notes: Optional[str] = None
    coa_account_id: Optional[int] = None


class CategoryIn(BaseModel):
    name: str
    flow: str = "both"           # in | out | both
    coa_account_id: Optional[int] = None


class TxnIn(BaseModel):
    treasury_id: int
    direction: str               # in | out
    amount: Decimal
    category_id: Optional[int] = None
    txn_date: Optional[date] = None
    description: Optional[str] = None
    reference: Optional[str] = None
    payment_method: Optional[str] = "cash"   # cash | cheque | wallet | other
    cheque_number: Optional[str] = None
    cheque_date: Optional[date] = None       # تاريخ استحقاق الشيك
    cheque_bank: Optional[str] = None


class ChequeStatusIn(BaseModel):
    cheque_status: str   # under_collection | cleared | bounced | cancelled


class TransferIn(BaseModel):
    from_treasury_id: int
    to_treasury_id: int
    amount: Decimal
    txn_date: Optional[date] = None
    description: Optional[str] = None


# ════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════
def _t(t: Treasury) -> dict:
    return {
        "id": t.id, "name": t.name, "kind": t.kind,
        "opening_balance": float(t.opening_balance or 0),
        "balance": float(t.balance or 0),
        "is_active": t.is_active, "notes": t.notes,
        "coa_account_id": t.coa_account_id,
    }


async def _fallback_category_account_id(db: AsyncSession, tenant_id: int) -> Optional[int]:
    """حساب احتياطي (5500 مصاريف إدارية وعمومية) لو الفئة بلا حساب محدَّد"""
    from app.models.models import ChartOfAccount
    return await db.scalar(
        select(ChartOfAccount.id).where(
            ChartOfAccount.tenant_id == tenant_id, ChartOfAccount.code == "5500",
        )
    )


def _txn(x: TreasuryTransaction, cat_name=None, treasury_name=None) -> dict:
    return {
        "id": x.id, "treasury_id": x.treasury_id, "treasury_name": treasury_name,
        "direction": x.direction, "amount": float(x.amount or 0),
        "category_id": x.category_id, "category_name": cat_name,
        "txn_date": str(x.txn_date), "description": x.description,
        "reference": x.reference, "source": x.source,
        "receipt_no": getattr(x, "receipt_no", None),
        "created_at": x.created_at.isoformat() if x.created_at else None,
        "payment_method": getattr(x, "payment_method", None) or "cash",
        "cheque_number": getattr(x, "cheque_number", None),
        "cheque_date": str(x.cheque_date) if getattr(x, "cheque_date", None) else None,
        "cheque_bank": getattr(x, "cheque_bank", None),
        "cheque_status": getattr(x, "cheque_status", None),
    }


async def _next_receipt_no(db: AsyncSession, direction: str) -> str:
    year = date.today().year
    count = await db.scalar(select(func.count()).select_from(TreasuryTransaction))
    n = (count or 0) + 1
    prefix = "RC" if direction == "in" else "PY"
    return f"{prefix}-{year}-{n:04d}"


async def _apply_to_balance(db, treasury_id, direction, amount, revert=False):
    """يحدّث رصيد الخزنة. revert=True يعكس العملية (للحذف)"""
    t = await db.get(Treasury, treasury_id)
    if not t:
        raise HTTPException(404, "الخزنة غير موجودة")
    amt = Decimal(str(amount))
    sign = 1 if direction == "in" else -1
    if revert:
        sign = -sign
    t.balance = (t.balance or Decimal("0")) + sign * amt
    return t


# ════════════════════════════════════════════════════
# Treasuries (الخزن)
# ════════════════════════════════════════════════════
@router.get("/accounts")
async def list_treasuries(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Treasury).order_by(Treasury.name))
    return [_t(t) for t in r.scalars().all()]


@router.post("/accounts", status_code=201)
async def create_treasury(data: TreasuryIn, db: AsyncSession = Depends(get_db)):
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(400, "اسم الخزنة مطلوب")
    ex = await db.execute(select(Treasury).where(Treasury.name == name))
    if ex.scalar_one_or_none():
        raise HTTPException(409, "خزنة بنفس الاسم موجودة")
    from app.core.database import current_tenant_id as _ctid_tr
    tid = _ctid_tr.get() or 1
    coa_id = data.coa_account_id
    if coa_id is None:
        from app.models.models import ChartOfAccount
        coa_id = await db.scalar(
            select(ChartOfAccount.id).where(
                ChartOfAccount.tenant_id == tid,
                ChartOfAccount.code == ("1120" if data.kind == "bank" else "1110"),
            )
        )
    t = Treasury(
        tenant_id=tid,
        name=name, kind=data.kind,
        opening_balance=data.opening_balance,
        balance=data.opening_balance,   # الرصيد الافتتاحي يبدأ به
        notes=data.notes,
        coa_account_id=coa_id,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _t(t)


@router.put("/accounts/{treasury_id}")
async def update_treasury(treasury_id: int, data: TreasuryIn, db: AsyncSession = Depends(get_db)):
    t = await db.get(Treasury, treasury_id)
    if not t:
        raise HTTPException(404, "الخزنة غير موجودة")
    t.name = (data.name or t.name).strip()
    t.kind = data.kind
    t.notes = data.notes
    if data.coa_account_id is not None:
        t.coa_account_id = data.coa_account_id
    await db.commit()
    await db.refresh(t)
    return _t(t)


@router.delete("/accounts/{treasury_id}", status_code=204)
async def delete_treasury(treasury_id: int, db: AsyncSession = Depends(get_db)):
    t = await db.get(Treasury, treasury_id)
    if not t:
        raise HTTPException(404, "الخزنة غير موجودة")
    cnt = await db.execute(
        select(func.count()).select_from(TreasuryTransaction).where(
            TreasuryTransaction.treasury_id == treasury_id
        )
    )
    if (cnt.scalar() or 0) > 0:
        raise HTTPException(409, "لا يمكن حذف خزنة عليها حركات. عطّلها بدل الحذف.")
    await db.delete(t)
    await db.commit()


# ════════════════════════════════════════════════════
# Categories (الفئات)
# ════════════════════════════════════════════════════
@router.get("/categories")
async def list_categories(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(TreasuryCategory).order_by(TreasuryCategory.name))
    return [{"id": c.id, "name": c.name, "flow": c.flow, "coa_account_id": c.coa_account_id}
            for c in r.scalars().all()]


@router.post("/categories", status_code=201)
async def create_category(data: CategoryIn, db: AsyncSession = Depends(get_db)):
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(400, "اسم الفئة مطلوب")
    ex = await db.execute(select(TreasuryCategory).where(TreasuryCategory.name == name))
    if ex.scalar_one_or_none():
        raise HTTPException(409, "الفئة موجودة بالفعل")
    from app.core.database import current_tenant_id as _ctid_tc
    c = TreasuryCategory(tenant_id=_ctid_tc.get() or 1, name=name, flow=data.flow,
                          coa_account_id=data.coa_account_id)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "name": c.name, "flow": c.flow, "coa_account_id": c.coa_account_id}


@router.put("/categories/{category_id}")
async def update_category(category_id: int, data: CategoryIn, db: AsyncSession = Depends(get_db)):
    """تحديث فئة — يُستخدم أساساً لربطها بحساب في دليل الحسابات لترحيل حركاتها تلقائياً"""
    c = await db.get(TreasuryCategory, category_id)
    if not c:
        raise HTTPException(404, "الفئة غير موجودة")
    c.name = (data.name or c.name).strip()
    c.flow = data.flow
    if data.coa_account_id is not None:
        c.coa_account_id = data.coa_account_id
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "name": c.name, "flow": c.flow, "coa_account_id": c.coa_account_id}


@router.delete("/categories/{category_id}", status_code=204)
async def delete_category(category_id: int, db: AsyncSession = Depends(get_db)):
    c = await db.get(TreasuryCategory, category_id)
    if not c:
        raise HTTPException(404, "الفئة غير موجودة")
    await db.delete(c)
    await db.commit()


# ════════════════════════════════════════════════════
# Transactions (الحركات)
# ════════════════════════════════════════════════════
@router.get("/transactions")
async def list_transactions(
    treasury_id: Optional[int] = None,
    direction: Optional[str] = None,
    category_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(TreasuryTransaction).order_by(TreasuryTransaction.txn_date.desc(), TreasuryTransaction.id.desc())
    if treasury_id:
        q = q.where(TreasuryTransaction.treasury_id == treasury_id)
    if direction in ("in", "out"):
        q = q.where(TreasuryTransaction.direction == direction)
    if category_id:
        q = q.where(TreasuryTransaction.category_id == category_id)
    rows = (await db.execute(q)).scalars().all()

    # نجيب أسماء الفئات والخزن
    cats = {c.id: c.name for c in (await db.execute(select(TreasuryCategory))).scalars().all()}
    treas = {t.id: t.name for t in (await db.execute(select(Treasury))).scalars().all()}
    return [_txn(x, cats.get(x.category_id), treas.get(x.treasury_id)) for x in rows]


@router.post("/transactions", status_code=201)
async def create_transaction(data: TxnIn, db: AsyncSession = Depends(get_db)):
    if data.direction not in ("in", "out"):
        raise HTTPException(400, "الاتجاه لازم يكون in أو out")
    amt = Decimal(str(data.amount))
    if amt <= 0:
        raise HTTPException(400, "المبلغ لازم يكون أكبر من صفر")
    payment_method = data.payment_method or "cash"
    if payment_method not in ("cash", "cheque", "wallet", "other"):
        raise HTTPException(400, "طريقة الدفع غير صحيحة")
    cheque_status = None
    if payment_method == "cheque":
        if not data.cheque_number or not data.cheque_date:
            raise HTTPException(400, "رقم الشيك وتاريخ الاستحقاق مطلوبان")
        cheque_status = "under_collection"
    # تحذير: رصيد غير كافٍ
    if data.direction == "out":
        t = await db.get(Treasury, data.treasury_id)
        if t:
            current = Decimal(str(t.balance or 0))
            if amt > current:
                raise HTTPException(400,
                    f"الرصيد الحالي للخزنة ({float(current):,.2f}) أقل من المبلغ المطلوب صرفه ({float(amt):,.2f})")
    receipt_no = await _next_receipt_no(db, data.direction)
    from app.core.database import current_tenant_id as _ctid_ttx
    tid = _ctid_ttx.get() or 1
    x = TreasuryTransaction(
        tenant_id=tid,
        treasury_id=data.treasury_id,
        direction=data.direction,
        amount=data.amount,
        category_id=data.category_id,
        txn_date=data.txn_date or date.today(),
        description=data.description,
        reference=data.reference,
        source="manual",
        receipt_no=receipt_no,
        payment_method=payment_method,
        cheque_number=data.cheque_number if payment_method == "cheque" else None,
        cheque_date=data.cheque_date if payment_method == "cheque" else None,
        cheque_bank=data.cheque_bank if payment_method == "cheque" else None,
        cheque_status=cheque_status,
    )
    await _apply_to_balance(db, data.treasury_id, data.direction, data.amount)
    db.add(x)
    await db.flush()

    treasury = await db.get(Treasury, data.treasury_id)
    category = await db.get(TreasuryCategory, data.category_id) if data.category_id else None
    category_account_id = (category.coa_account_id if category else None) or \
        await _fallback_category_account_id(db, tid)
    if treasury and treasury.coa_account_id and category_account_id:
        from app.services.auto_journal import journal_treasury_manual
        await journal_treasury_manual(
            db, tid, x.id,
            data.description or f"حركة خزينة: {data.direction}",
            amt, data.direction,
            treasury.coa_account_id, category_account_id, x.txn_date,
        )

    await db.commit()
    await db.refresh(x)
    return _txn(x)


@router.put("/transactions/{txn_id}")
async def update_transaction(txn_id: int, data: TxnIn, db: AsyncSession = Depends(get_db)):
    """تعديل حركة خزينة — مسموح فقط للحركات اليدوية غير المقاصة"""
    x = await db.get(TreasuryTransaction, txn_id)
    if not x:
        raise HTTPException(404, "الحركة غير موجودة")
    if x.source != "manual":
        raise HTTPException(400, "لا يمكن تعديل حركة تلقائية مرتبطة بفاتورة أو دفعة")
    if getattr(x, "cheque_status", None) in ("cleared", "bounced"):
        raise HTTPException(400, "لا يمكن تعديل شيك مُقاص أو مرتجع")

    # عكس الأثر القديم على رصيد الخزنة
    if x.cheque_status not in ("bounced", "cancelled"):
        await _apply_to_balance(db, x.treasury_id, x.direction, x.amount, revert=True)

    amt = Decimal(str(data.amount))
    if amt <= 0:
        raise HTTPException(400, "المبلغ لازم يكون أكبر من صفر")
    if data.direction not in ("in", "out"):
        raise HTTPException(400, "الاتجاه لازم يكون in أو out")

    # تحقق من كفاية الرصيد إن كان صرف
    if data.direction == "out":
        t = await db.get(Treasury, data.treasury_id)
        if t:
            current = Decimal(str(t.balance or 0))
            if amt > current:
                raise HTTPException(400,
                    f"الرصيد الحالي للخزنة ({float(current):,.2f}) أقل من المبلغ المطلوب صرفه ({float(amt):,.2f})")

    x.treasury_id = data.treasury_id
    x.direction = data.direction
    x.amount = data.amount
    x.category_id = data.category_id
    x.txn_date = data.txn_date or x.txn_date
    x.description = data.description
    x.reference = data.reference

    await _apply_to_balance(db, data.treasury_id, data.direction, data.amount)
    await db.commit()
    await db.refresh(x)

    cats = {c.id: c.name for c in (await db.execute(select(TreasuryCategory))).scalars().all()}
    treas = {t.id: t.name for t in (await db.execute(select(Treasury))).scalars().all()}
    return _txn(x, cats.get(x.category_id), treas.get(x.treasury_id))


@router.delete("/transactions/{txn_id}", status_code=204)
async def delete_transaction(txn_id: int, db: AsyncSession = Depends(get_db)):
    x = await db.get(TreasuryTransaction, txn_id)
    if not x:
        raise HTTPException(404, "الحركة غير موجودة")
    # نعكس أثرها على الرصيد — إلا لو كان شيكاً مرتجعاً/ملغياً (أثره معكوس أصلاً)
    if x.cheque_status not in ("bounced", "cancelled"):
        await _apply_to_balance(db, x.treasury_id, x.direction, x.amount, revert=True)
    await db.delete(x)
    await db.commit()


# ════════════════════════════════════════════════════
# Cheques (الشيكات)
# ════════════════════════════════════════════════════
@router.get("/cheques")
async def list_cheques(
    cheque_status: Optional[str] = None,
    direction: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(TreasuryTransaction).where(
        TreasuryTransaction.payment_method == "cheque"
    ).order_by(TreasuryTransaction.cheque_date.asc())
    if cheque_status:
        q = q.where(TreasuryTransaction.cheque_status == cheque_status)
    if direction in ("in", "out"):
        q = q.where(TreasuryTransaction.direction == direction)
    rows = (await db.execute(q)).scalars().all()

    cats = {c.id: c.name for c in (await db.execute(select(TreasuryCategory))).scalars().all()}
    treas = {t.id: t.name for t in (await db.execute(select(Treasury))).scalars().all()}
    return [_txn(x, cats.get(x.category_id), treas.get(x.treasury_id)) for x in rows]


@router.patch("/cheques/{txn_id}/status")
async def update_cheque_status(txn_id: int, data: ChequeStatusIn, db: AsyncSession = Depends(get_db)):
    x = await db.get(TreasuryTransaction, txn_id)
    if not x:
        raise HTTPException(404, "الحركة غير موجودة")
    if x.payment_method != "cheque":
        raise HTTPException(400, "الحركة ليست شيكاً")
    if data.cheque_status not in ("under_collection", "cleared", "bounced", "cancelled"):
        raise HTTPException(400, "حالة الشيك غير صحيحة")

    old_status = x.cheque_status
    new_status = data.cheque_status

    # الشيك "تحت التحصيل" يُحتسب أصلاً في رصيد الخزنة عند إنشائه.
    # لو تحوّل لـ "مرتجع" أو "ملغي"، نعكس أثره على الرصيد.
    reverted_statuses = ("bounced", "cancelled")
    from app.core.database import current_tenant_id as _ctid_chq
    tid = _ctid_chq.get() or 1
    if old_status not in reverted_statuses and new_status in reverted_statuses:
        await _apply_to_balance(db, x.treasury_id, x.direction, x.amount, revert=True)
        # لو الشيك ده كان له قيد محاسبي يدوي مرحَّل (treasury_manual)، نعكسه — مبدأ: نعكس لا نحذف
        if x.source == "manual":
            from app.services.auto_journal import journal_reverse_entry
            await journal_reverse_entry(db, tid, "treasury_manual", x.id,
                                         "بنك" if new_status == "bounced" else "إلغاء")
    elif old_status in reverted_statuses and new_status not in reverted_statuses:
        await _apply_to_balance(db, x.treasury_id, x.direction, x.amount)
        if x.source == "manual":
            from app.services.auto_journal import journal_reverse_entry
            await journal_reverse_entry(db, tid, "treasury_manual_reversal", x.id, "إعادة تفعيل")

    x.cheque_status = new_status
    await db.commit()
    await db.refresh(x)
    return _txn(x)


# ════════════════════════════════════════════════════
# Transfer (تحويل بين الخزن)
# ════════════════════════════════════════════════════
@router.post("/transfer", status_code=201)
async def transfer(data: TransferIn, db: AsyncSession = Depends(get_db)):
    if data.from_treasury_id == data.to_treasury_id:
        raise HTTPException(400, "لا يمكن التحويل لنفس الخزنة")
    tamt = Decimal(str(data.amount))
    if tamt <= 0:
        raise HTTPException(400, "المبلغ لازم يكون أكبر من صفر")
    # تحقق من رصيد المصدر
    src = await db.get(Treasury, data.from_treasury_id)
    if src:
        src_bal = Decimal(str(src.balance or 0))
        if tamt > src_bal:
            raise HTTPException(400,
                f"رصيد خزنة المصدر ({float(src_bal):,.2f}) أقل من مبلغ التحويل ({float(tamt):,.2f})")
    grp = uuid4().hex
    d = data.txn_date or date.today()
    desc = data.description or "تحويل بين الخزن"

    from app.core.database import current_tenant_id as _ctid_xfr
    _tid_xfr = _ctid_xfr.get() or 1
    # خروج من المصدر
    out_txn = TreasuryTransaction(
        tenant_id=_tid_xfr,
        treasury_id=data.from_treasury_id, direction="out", amount=data.amount,
        txn_date=d, description=desc, source="transfer", transfer_group=grp,
    )
    await _apply_to_balance(db, data.from_treasury_id, "out", data.amount)
    # دخول للهدف
    in_txn = TreasuryTransaction(
        tenant_id=_tid_xfr,
        treasury_id=data.to_treasury_id, direction="in", amount=data.amount,
        txn_date=d, description=desc, source="transfer", transfer_group=grp,
    )
    await _apply_to_balance(db, data.to_treasury_id, "in", data.amount)
    db.add_all([out_txn, in_txn])
    await db.flush()

    src_treasury = await db.get(Treasury, data.from_treasury_id)
    dst_treasury = await db.get(Treasury, data.to_treasury_id)
    if src_treasury and dst_treasury and src_treasury.coa_account_id and dst_treasury.coa_account_id:
        from app.services.auto_journal import journal_treasury_transfer
        await journal_treasury_transfer(
            db, _tid_xfr, out_txn.id, desc, tamt,
            src_treasury.coa_account_id, dst_treasury.coa_account_id, d,
        )

    await db.commit()
    return {"message": "تم التحويل", "transfer_group": grp}


# ════════════════════════════════════════════════════
# Summary (ملخص الخزنة)
# ════════════════════════════════════════════════════
@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db)):
    treas = (await db.execute(select(Treasury))).scalars().all()
    total_balance = sum(float(t.balance or 0) for t in treas)

    total_in = (await db.execute(
        select(func.coalesce(func.sum(TreasuryTransaction.amount), 0)).where(
            TreasuryTransaction.direction == "in",
            TreasuryTransaction.source != "transfer",
        )
    )).scalar() or 0
    total_out = (await db.execute(
        select(func.coalesce(func.sum(TreasuryTransaction.amount), 0)).where(
            TreasuryTransaction.direction == "out",
            TreasuryTransaction.source != "transfer",
        )
    )).scalar() or 0

    return {
        "total_balance": float(total_balance),
        "total_in": float(total_in),
        "total_out": float(total_out),
        "net": float(total_in) - float(total_out),
        "treasuries": [_t(t) for t in treas],
        "treasury_count": len(treas),
    }



# ══════════════════════════════════════════════════════════════
# PRINT — سند القبض / سند الصرف
# ══════════════════════════════════════════════════════════════

def _v_esc(s) -> str:
    import html as _h
    return _h.escape(str(s or ""))


async def _voucher_company(db: AsyncSession) -> dict:
    from app.core.database import current_tenant_id
    from app.models.models import WorkshopProfile
    tid = current_tenant_id.get()
    q = select(WorkshopProfile)
    if tid is not None:
        q = q.where(WorkshopProfile.tenant_id == tid)
    res = await db.execute(q.limit(1))
    prof = res.scalar_one_or_none()
    if not prof:
        return {"name": "ورشة معدات الاعلاف", "sub": "", "phone": "",
                "address": "", "tax_id": "", "commercial_reg": "",
                "footer": "شكرا لتعاملكم معنا", "logo_html": ""}
    logo_html = (
        f'<img src="{prof.logo_data}" alt="logo" '
        'style="max-height:70px;max-width:160px;object-fit:contain;"/>'
        if prof.logo_data else ""
    )
    return {
        "name":           prof.name or "ورشة معدات الاعلاف",
        "sub":            prof.description or "",
        "phone":          prof.mobile or prof.phone or "",
        "address":        prof.address or "",
        "tax_id":         prof.tax_id or "",
        "commercial_reg": prof.commercial_reg or "",
        "footer":         prof.report_footer or "شكرا لتعاملكم معنا",
        "logo_html":      logo_html,
    }


def _n2w(n: float) -> str:
    """تحويل رقم إلى كلمات عربية"""
    try:
        n = round(n, 2)
        int_part = int(n)
        dec_part = round((n - int_part) * 100)
        ones = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة",
                "ثمانية", "تسعة", "عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر",
                "أربعة عشر", "خمسة عشر", "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
        tens = ["", "", "عشرون", "ثلاثون", "أربعون", "خمسون",
                "ستون", "سبعون", "ثمانون", "تسعون"]

        def _u100(x):
            if x < 20:
                return ones[x]
            t, o = divmod(x, 10)
            return ones[o] + (" و" + tens[t] if ones[o] else tens[t])

        def _u1000(x):
            if x < 100:
                return _u100(x)
            h, r = divmod(x, 100)
            hh = ["", "مئة", "مئتان", "ثلاثمئة", "أربعمئة",
                  "خمسمئة", "ستمئة", "سبعمئة", "ثمانمئة", "تسعمئة"]
            return hh[h] + (" و" + _u100(r) if r else "")

        if int_part == 0:
            words = "صفر"
        elif int_part < 1000:
            words = _u1000(int_part)
        elif int_part < 1_000_000:
            t, r = divmod(int_part, 1000)
            words = (_u1000(t) + " ألف") + (" و" + _u1000(r) if r else "")
        elif int_part < 1_000_000_000:
            m, r = divmod(int_part, 1_000_000)
            words = (_u1000(m) + " مليون") + (" و" + _u1000(r) if r else "")
        else:
            words = str(int_part)

        result = f"فقط: {words} جنيها"
        if dec_part:
            result += f" و{_u100(dec_part)} قرشا"
        return result + " لا غير"
    except Exception:
        return f"فقط: {n:,.2f}"


_SRC_LABELS = {
    "manual": "يدوي", "customer_payment": "دفعة عميل",
    "supplier_payment": "دفعة مورد", "transfer": "تحويل داخلي",
}
_DIR_STYLE = {
    "in":  ("#0F6E56", "#E1F5EE", "سند قبض"),
    "out": ("#854F0B", "#FDF5E8", "سند صرف"),
}


def _voucher_html(txn: TreasuryTransaction, company: dict,
                  treasury_name: str, cat_name: str) -> str:
    import json as _json

    color, bg_lt, vtitle = _DIR_STYLE.get(txn.direction, ("#333", "#f9f9f7", "سند"))
    cname     = _v_esc(company.get("name", ""))
    csub      = _v_esc(company.get("sub", ""))
    cphone    = _v_esc(company.get("phone", ""))
    cfooter   = _v_esc(company.get("footer", ""))
    logo_html = company.get("logo_html", "")
    tax_id    = _v_esc(company.get("tax_id", ""))
    creg      = _v_esc(company.get("commercial_reg", ""))
    legal_h   = " | ".join(filter(None, [
        f"ب.ض: {tax_id}" if tax_id else "",
        f"س.ت: {creg}"   if creg   else "",
    ]))

    amount       = float(txn.amount or 0)
    amount_words = _n2w(amount)
    voucher_no   = f"V-{txn.id:06d}"
    txn_date     = _v_esc(str(txn.txn_date))
    description  = _v_esc(txn.description or "—")
    reference    = _v_esc(txn.reference or "—")
    source_lbl   = _SRC_LABELS.get(txn.source or "manual", txn.source or "—")
    category     = _v_esc(cat_name or "—")
    tname        = _v_esc(treasury_name)

    js_title  = _json.dumps(vtitle)
    js_no     = _json.dumps(voucher_no)
    logo_wrap = (
        f'<div style="background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);'
        f'border-radius:8px;padding:5px;">{logo_html}</div>'
        if logo_html else ""
    )
    phone_line = (
        f'<div style="font-size:11px;color:{color};margin-top:4px;font-weight:600;">{cphone}</div>'
        if cphone else ""
    )
    legal_box = (
        f'<div style="background:#f9f9f7;border-radius:8px;padding:10px 12px;'
        f'font-size:11px;color:#6b6a64;margin-bottom:16px;">'
        f'<b>البيانات القانونية:</b> {legal_h}</div>'
        if legal_h else ""
    )
    recv_label = "المستلم" if txn.direction == "in" else "المدفوع له"

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<title>{vtitle} - {voucher_no}</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Cairo',Tahoma,Arial,sans-serif;font-size:13px;color:#1a1a18;background:#EDECEA;direction:rtl;}}
@page{{size:A5;margin:8mm;}}
@media print{{.no-print{{display:none!important;}}body{{background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact;}}.voucher{{box-shadow:none;border-radius:0;max-width:100%;margin:0;}}}}
.voucher{{max-width:600px;margin:20px auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 4px 40px rgba(0,0,0,.13);}}
.v-header{{background:#0C2340;padding:20px 24px 16px;display:flex;justify-content:space-between;align-items:center;}}
.v-logo-area{{display:flex;align-items:center;gap:14px;}}
.v-company-name{{font-size:18px;font-weight:800;color:#fff;}}
.v-company-sub{{font-size:10px;color:rgba(255,255,255,.5);margin-top:2px;}}
.v-title-area{{text-align:left;}}
.v-title{{font-size:24px;font-weight:900;color:{color};line-height:1;}}
.v-num{{font-size:12px;color:rgba(255,255,255,.6);margin-top:6px;}}
.v-stripe{{height:4px;background:linear-gradient(90deg,{color},{bg_lt},{color});}}
.v-body{{padding:20px 24px;}}
.v-amount-box{{background:{bg_lt};border:2px solid {color}40;border-radius:12px;padding:16px 20px;text-align:center;margin-bottom:20px;}}
.v-amount-number{{font-size:34px;font-weight:900;color:{color};font-variant-numeric:tabular-nums;letter-spacing:-1px;}}
.v-amount-currency{{font-size:14px;color:{color};margin-right:6px;font-weight:700;}}
.v-amount-words{{font-size:13px;color:#444;margin-top:8px;font-weight:600;}}
.v-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px 20px;margin-bottom:20px;}}
.v-field{{background:#f9f9f7;border-radius:8px;padding:10px 12px;}}
.v-field-label{{font-size:10px;font-weight:700;color:#6b6a64;letter-spacing:.08em;margin-bottom:4px;}}
.v-field-value{{font-size:13px;font-weight:600;color:#1a1a18;}}
.v-sig-row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:20px;}}
.v-sig-box{{border-top:2px solid #e2e0d8;padding-top:8px;text-align:center;}}
.v-sig-title{{font-size:10px;font-weight:700;color:#6b6a64;letter-spacing:.07em;margin-bottom:28px;}}
.v-sig-line{{border-top:1px solid #ccc;margin:0 12px;padding-top:5px;font-size:10px;color:#aaa;}}
.v-footer{{background:#081828;color:rgba(255,255,255,.5);padding:10px 24px;text-align:center;font-size:11px;}}
.action-bar{{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);display:flex;gap:8px;z-index:99;background:rgba(8,24,40,.85);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:8px 12px;}}
.action-bar button{{padding:8px 18px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer;border:none;font-family:'Cairo',sans-serif;}}
.btn-print{{background:{color};color:#fff;}}
</style>
</head>
<body onload="setTimeout(()=>window.print(),400)">
<div class="voucher">
  <div class="v-header">
    <div class="v-logo-area">
      {logo_wrap}
      <div>
        <div class="v-company-name">{cname}</div>
        <div class="v-company-sub">{csub}</div>
        {phone_line}
      </div>
    </div>
    <div class="v-title-area">
      <div class="v-title">{vtitle}</div>
      <div class="v-num">رقم: {voucher_no}</div>
      <div class="v-num">{txn_date}</div>
    </div>
  </div>
  <div class="v-stripe"></div>
  <div class="v-body">
    <div class="v-amount-box">
      <div><span class="v-amount-currency">ج.م</span><span class="v-amount-number">{amount:,.2f}</span></div>
      <div class="v-amount-words">{amount_words}</div>
    </div>
    <div class="v-grid">
      <div class="v-field"><div class="v-field-label">البيان / الغرض</div><div class="v-field-value">{description}</div></div>
      <div class="v-field"><div class="v-field-label">الخزنة</div><div class="v-field-value">{tname}</div></div>
      <div class="v-field"><div class="v-field-label">الفئة</div><div class="v-field-value">{category}</div></div>
      <div class="v-field"><div class="v-field-label">المرجع</div><div class="v-field-value">{reference}</div></div>
      <div class="v-field"><div class="v-field-label">المصدر</div><div class="v-field-value">{source_lbl}</div></div>
      <div class="v-field"><div class="v-field-label">التاريخ</div><div class="v-field-value">{txn_date}</div></div>
    </div>
    {legal_box}
    <div class="v-sig-row">
      <div class="v-sig-box"><div class="v-sig-title">{recv_label}</div><div class="v-sig-line">التوقيع</div></div>
      <div class="v-sig-box"><div class="v-sig-title">المحرر</div><div class="v-sig-line">التوقيع</div></div>
      <div class="v-sig-box"><div class="v-sig-title">المراجع / المعتمد</div><div class="v-sig-line">التوقيع والختم</div></div>
    </div>
  </div>
  <div class="v-footer">{cfooter} — {cname}{" | " + legal_h if legal_h else ""}</div>
</div>
<div class="action-bar no-print">
  <button class="btn-print" onclick="(function(){{document.title={js_title}+' '+{js_no};window.print();}})()">طباعة / PDF</button>
</div>
</body></html>"""


@print_router.get("/transactions/{txn_id}/print", response_class=HTMLResponse)
async def print_voucher(txn_id: int, db: AsyncSession = Depends(get_db)):
    """طباعة سند قبض أو سند صرف"""
    txn = await db.get(TreasuryTransaction, txn_id)
    if not txn:
        raise HTTPException(404)
    company = await _voucher_company(db)
    treasury = await db.get(Treasury, txn.treasury_id)
    treasury_name = treasury.name if treasury else "—"
    cat = await db.get(TreasuryCategory, txn.category_id) if txn.category_id else None
    cat_name = cat.name if cat else "—"
    return _voucher_html(txn, company, treasury_name, cat_name)
