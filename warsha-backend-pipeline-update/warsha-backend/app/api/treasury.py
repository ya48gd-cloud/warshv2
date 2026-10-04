"""
الخزنة — إدارة الصناديق النقدية والبنوك وحركات الأموال
- خزن متعددة (نقدي/بنك)
- حركات داخل/خارج يدوية
- فئات للتصنيف
- تحويل بين الخزن
- ربط تلقائي بمدفوعات العملاء والموردين
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field, validator

from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import (
    Treasury, TreasuryCategory, TreasuryTransaction,
)

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_WRITE_ROLES = {"admin", "accountant"}


async def _block_non_finance_writes(request: Request, user: dict = Depends(require_any)) -> dict:
    """إصلاح ثغرة صلاحيات: كل مسارات الخزنة كانت محمية بـ require_any بس —
    يعني مستخدم "مشاهد" أو "إنتاج" يقدر يسجّل/يعدّل/يحذف حركات نقدية وتحويلات
    (بقيود محاسبية حقيقية) بطلب API مباشر رغم إن الواجهة مخبية الأزرار
    (canWrite('accounting') = admin/accountant فقط). القراءة متاحة للكل زي ما هي."""
    if request.method.upper() not in _READ_METHODS and user.get("role") not in _WRITE_ROLES:
        raise HTTPException(403, f"صلاحية '{user.get('role')}' لا تسمح بتعديل الخزنة")
    return user


# أقصى مبلغ يتسع له عمود Numeric(14, 2)
_MAX_AMOUNT = Decimal("999999999999.99")


def _money(v, field: str = "المبلغ", allow_zero: bool = False) -> Decimal:
    """تحويل مبلغ لـ Decimal مقرَّب لقرشين — وإلا (مثلاً 10.005) الرصيد يتحدّث
    بالقيمة غير المقرّبة بينما العمود Numeric(14,2) يخزّن المقرّبة فيختلفا."""
    try:
        d = Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field} غير صالح")
    if not d.is_finite():
        raise HTTPException(400, f"{field} غير صالح")
    if d < 0 or (d == 0 and not allow_zero):
        raise HTTPException(400, "المبلغ لازم يكون أكبر من صفر" if not allow_zero else f"{field} لا يمكن أن يكون سالباً")
    if d > _MAX_AMOUNT:
        raise HTTPException(400, f"{field} أكبر من الحد المسموح")
    return d


router = APIRouter(prefix="/treasury", tags=["treasury"],
                   dependencies=[Depends(require_any), Depends(_block_non_finance_writes)])
print_router = APIRouter(prefix="/treasury", tags=["treasury_print"], dependencies=[Depends(require_any)])


# ════════════════════════════════════════════════════
# Schemas
# ════════════════════════════════════════════════════
class TreasuryIn(BaseModel):
    name: str = Field(..., max_length=100)
    kind: str = "cash"            # cash | bank
    opening_balance: Decimal = Decimal("0")
    notes: Optional[str] = Field(None, max_length=2000)
    coa_account_id: Optional[int] = None
    # اختياري (للتعديل فقط): تعطيل/تفعيل الخزنة — رسالة الحذف بتقول "عطّلها بدل
    # الحذف" لكن ماكانش فيه أي طريقة للتعطيل. None = بدون تغيير.
    is_active: Optional[bool] = None

    @validator("kind")
    def _valid_kind(cls, v):
        # حماية: kind غلط بيكسر ترتيب _default_treasury (كاش قبل بنك) ويخلي
        # create_treasury يربط الخزنة بحساب 1110 بصمت وكأنها "cash" حتى لو مقصود بنك
        if v not in ("cash", "bank"):
            raise ValueError("kind يجب أن يكون 'cash' أو 'bank'")
        return v


class CategoryIn(BaseModel):
    name: str = Field(..., max_length=100)
    flow: str = "both"           # in | out | both
    coa_account_id: Optional[int] = None

    @validator("flow")
    def _valid_flow(cls, v):
        if v not in ("in", "out", "both"):
            raise ValueError("flow يجب أن يكون 'in' أو 'out' أو 'both'")
        return v


class TxnIn(BaseModel):
    treasury_id: int
    direction: str               # in | out
    amount: Decimal
    category_id: Optional[int] = None
    txn_date: Optional[date] = None
    description: Optional[str] = Field(None, max_length=2000)
    reference: Optional[str] = Field(None, max_length=100)
    payment_method: Optional[str] = "cash"   # cash | cheque | wallet | other
    cheque_number: Optional[str] = Field(None, max_length=50)
    cheque_date: Optional[date] = None       # تاريخ استحقاق الشيك
    cheque_bank: Optional[str] = Field(None, max_length=100)


class ChequeStatusIn(BaseModel):
    cheque_status: str   # under_collection | cleared | bounced | cancelled


class TransferIn(BaseModel):
    from_treasury_id: int
    to_treasury_id: int
    amount: Decimal
    txn_date: Optional[date] = None
    description: Optional[str] = Field(None, max_length=2000)


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
    """رقم السند التالي لكل (نوع/سنة) داخل الـ tenant.
    إصلاح: كان count(*) لكل الحركات — بعد حذف أي حركة الرقم يتكرر (سندين بنفس
    الرقم)، وكمان كان بيعدّ حركات التحويل والدفعات التلقائية وحركات الـ tenants
    التانية لو الفلتر التلقائي مش شغال. دلوقتي: أكبر رقم مستخدم + 1."""
    from app.core.database import current_tenant_id as _ctid_rn
    tid = _ctid_rn.get() or 1
    year = date.today().year
    prefix = "RC" if direction == "in" else "PY"
    stem = f"{prefix}-{year}-"
    existing = (await db.execute(
        select(TreasuryTransaction.receipt_no).where(
            TreasuryTransaction.tenant_id == tid,
            TreasuryTransaction.receipt_no.like(f"{stem}%"),
        )
    )).scalars().all()
    mx = 0
    for rn in existing:
        try:
            mx = max(mx, int(str(rn)[len(stem):]))
        except (TypeError, ValueError):
            continue
    return f"{stem}{mx + 1:04d}"


async def _validate_coa_account(db: AsyncSession, tenant_id: int, coa_id: Optional[int]) -> None:
    """حماية: coa_account_id لازم يكون حساب موجود في دليل حسابات نفس الـ tenant —
    وإلا القيود التلقائية للخزنة تترحّل على حساب tenant تاني أو حساب غير موجود."""
    if coa_id is None:
        return
    from app.models.models import ChartOfAccount
    ok = await db.scalar(
        select(ChartOfAccount.id).where(
            ChartOfAccount.id == coa_id, ChartOfAccount.tenant_id == tenant_id,
        )
    )
    if not ok:
        raise HTTPException(400, "الحساب المحدد في دليل الحسابات غير موجود")


async def _get_treasury_checked(db: AsyncSession, tenant_id: int, treasury_id: int) -> Treasury:
    t = await db.scalar(
        select(Treasury).where(Treasury.id == treasury_id, Treasury.tenant_id == tenant_id)
    )
    if not t:
        raise HTTPException(404, "الخزنة غير موجودة")
    if t.is_active is False:
        raise HTTPException(400, "الخزنة معطّلة — لا يمكن تسجيل حركات عليها")
    return t


async def _get_category_checked(db: AsyncSession, tenant_id: int,
                                category_id: Optional[int], direction: str) -> Optional[TreasuryCategory]:
    """الفئة لازم تكون من نفس الـ tenant ومتوافقة مع اتجاه الحركة
    (فئة "إيراد" ماينفعش تتسجّل على صرف والعكس — وإلا الترحيل يروح لحساب غلط)."""
    if not category_id:
        return None
    c = await db.scalar(
        select(TreasuryCategory).where(
            TreasuryCategory.id == category_id, TreasuryCategory.tenant_id == tenant_id,
        )
    )
    if not c:
        raise HTTPException(404, "الفئة غير موجودة")
    if c.flow in ("in", "out") and c.flow != direction:
        raise HTTPException(400, "الفئة المختارة لا تتوافق مع نوع الحركة (إيراد/مصروف)")
    return c


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


def _current_tid() -> int:
    from app.core.database import current_tenant_id as _ctid
    return _ctid.get() or 1


async def _balances_snapshot(db: AsyncSession, treasury_ids) -> dict:
    """رصيد كل خزنة قبل العملية — لمقارنته بعد العكس/التعديل"""
    snap = {}
    for tr_id in set(i for i in treasury_ids if i):
        t = await db.get(Treasury, tr_id)
        if t:
            snap[tr_id] = Decimal(str(t.balance or 0))
    return snap


async def _guard_not_negative(db: AsyncSession, before: dict, action: str) -> None:
    """منع الرصيد السالب الناتج عن حذف/تعديل حركة (نفس سياسة منع السحب على المكشوف):
    حذف حركة "قبض" أو تحويل اتصرف منه بالفعل كان بيخلي رصيد الخزنة بالسالب بصمت.
    نرفض فقط لو العملية نفسها نزّلت الرصيد تحت الصفر (بيانات قديمة سالبة لا تعطّل)."""
    for tr_id, prev in before.items():
        t = await db.get(Treasury, tr_id)
        if not t:
            continue
        after = Decimal(str(t.balance or 0))
        if after < 0 and after < prev:
            raise HTTPException(
                409,
                f"لا يمكن {action}: رصيد الخزنة '{t.name}' سيصبح بالسالب "
                f"({float(after):,.2f}) — المبلغ اتصرف منها بالفعل",
            )


# ════════════════════════════════════════════════════
# Treasuries (الخزن)
# ════════════════════════════════════════════════════
@router.get("/accounts")
async def list_treasuries(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Treasury).where(Treasury.tenant_id == _current_tid())
                         .order_by(Treasury.name))
    return [_t(t) for t in r.scalars().all()]


@router.post("/accounts", status_code=201)
async def create_treasury(data: TreasuryIn, db: AsyncSession = Depends(get_db)):
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(400, "اسم الخزنة مطلوب")
    from app.core.database import current_tenant_id as _ctid_tr
    tid = _ctid_tr.get() or 1
    ex = await db.execute(select(Treasury).where(Treasury.name == name, Treasury.tenant_id == tid))
    if ex.scalars().first():
        raise HTTPException(409, "خزنة بنفس الاسم موجودة")
    # الرصيد الافتتاحي: مقرّب لقرشين وغير سالب (نفس سياسة منع السحب على المكشوف)
    opening = _money(data.opening_balance, "الرصيد الافتتاحي", allow_zero=True)
    await _validate_coa_account(db, tid, data.coa_account_id)
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
        opening_balance=opening,
        balance=opening,   # الرصيد الافتتاحي يبدأ به
        notes=data.notes,
        coa_account_id=coa_id,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _t(t)


@router.put("/accounts/{treasury_id}")
async def update_treasury(treasury_id: int, data: TreasuryIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_utr
    tid = _ctid_utr.get() or 1
    t = await db.scalar(select(Treasury).where(Treasury.id == treasury_id, Treasury.tenant_id == tid))
    if not t:
        raise HTTPException(404, "الخزنة غير موجودة")
    new_name = (data.name or "").strip() or t.name
    if new_name != t.name:
        # بدون الفحص ده الاسم المكرر كان يوقع IntegrityError (500) من القيد الفريد
        dup = await db.scalar(select(Treasury.id).where(
            Treasury.tenant_id == tid, Treasury.name == new_name, Treasury.id != t.id,
        ))
        if dup:
            raise HTTPException(409, "خزنة بنفس الاسم موجودة")
    await _validate_coa_account(db, tid, data.coa_account_id)
    t.name = new_name
    # تعديل جزئي: kind له قيمة افتراضية "cash" — لو الطلب ماحددهوش كانت خزنة البنك
    # بتتحوّل لنقدية بصمت. نغيّره فقط لو اتبعت صراحةً.
    _set = getattr(data, "model_fields_set", None) or getattr(data, "__fields_set__", set())
    if "kind" in _set:
        t.kind = data.kind
    if "notes" in _set:
        t.notes = data.notes
    if data.coa_account_id is not None:
        t.coa_account_id = data.coa_account_id
    if data.is_active is not None:
        t.is_active = data.is_active
    await db.commit()
    await db.refresh(t)
    return _t(t)


@router.delete("/accounts/{treasury_id}", status_code=204)
async def delete_treasury(treasury_id: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_dtr
    t = await db.scalar(select(Treasury).where(
        Treasury.id == treasury_id, Treasury.tenant_id == (_ctid_dtr.get() or 1),
    ))
    if not t:
        raise HTTPException(404, "الخزنة غير موجودة")
    cnt = await db.execute(
        select(func.count()).select_from(TreasuryTransaction).where(
            TreasuryTransaction.treasury_id == treasury_id
        )
    )
    if (cnt.scalar() or 0) > 0:
        raise HTTPException(409, "لا يمكن حذف خزنة عليها حركات. عطّلها بدل الحذف.")
    # مبيعات الخردة بتشير للخزنة (FK) — حذفها كان يسيب سجلات يتيمة/خطأ FK
    from app.models.models import ScrapSale
    scrap_cnt = await db.scalar(
        select(func.count()).select_from(ScrapSale).where(ScrapSale.treasury_id == treasury_id)
    )
    if (scrap_cnt or 0) > 0:
        raise HTTPException(409, "لا يمكن حذف خزنة مرتبطة بمبيعات خردة. عطّلها بدل الحذف.")
    await db.delete(t)
    await db.commit()


# ════════════════════════════════════════════════════
# Categories (الفئات)
# ════════════════════════════════════════════════════
@router.get("/categories")
async def list_categories(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(TreasuryCategory).where(TreasuryCategory.tenant_id == _current_tid())
                         .order_by(TreasuryCategory.name))
    return [{"id": c.id, "name": c.name, "flow": c.flow, "coa_account_id": c.coa_account_id}
            for c in r.scalars().all()]


@router.post("/categories", status_code=201)
async def create_category(data: CategoryIn, db: AsyncSession = Depends(get_db)):
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(400, "اسم الفئة مطلوب")
    from app.core.database import current_tenant_id as _ctid_tc
    _tid_tc = _ctid_tc.get() or 1
    ex = await db.execute(select(TreasuryCategory).where(
        TreasuryCategory.name == name, TreasuryCategory.tenant_id == _tid_tc,
    ))
    if ex.scalars().first():
        raise HTTPException(409, "الفئة موجودة بالفعل")
    await _validate_coa_account(db, _tid_tc, data.coa_account_id)
    c = TreasuryCategory(tenant_id=_tid_tc, name=name, flow=data.flow,
                          coa_account_id=data.coa_account_id)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "name": c.name, "flow": c.flow, "coa_account_id": c.coa_account_id}


@router.put("/categories/{category_id}")
async def update_category(category_id: int, data: CategoryIn, db: AsyncSession = Depends(get_db)):
    """تحديث فئة — يُستخدم أساساً لربطها بحساب في دليل الحسابات لترحيل حركاتها تلقائياً"""
    from app.core.database import current_tenant_id as _ctid_uc
    _tid_uc = _ctid_uc.get() or 1
    c = await db.scalar(select(TreasuryCategory).where(
        TreasuryCategory.id == category_id, TreasuryCategory.tenant_id == _tid_uc,
    ))
    if not c:
        raise HTTPException(404, "الفئة غير موجودة")
    new_name = (data.name or "").strip() or c.name
    if new_name != c.name:
        dup = await db.scalar(select(TreasuryCategory.id).where(
            TreasuryCategory.tenant_id == _tid_uc, TreasuryCategory.name == new_name,
            TreasuryCategory.id != c.id,
        ))
        if dup:
            raise HTTPException(409, "الفئة موجودة بالفعل")
    await _validate_coa_account(db, _tid_uc, data.coa_account_id)
    # تغيير flow لـ in/out بينما الفئة عليها حركات بالاتجاه العكسي كان يخلّي
    # حركات قديمة بفئة "إيراد" على صرف (أو العكس) — نفس القاعدة المطبّقة عند الإنشاء
    if data.flow in ("in", "out") and data.flow != c.flow:
        conflicting = await db.scalar(
            select(func.count()).select_from(TreasuryTransaction).where(
                TreasuryTransaction.category_id == c.id,
                TreasuryTransaction.tenant_id == _tid_uc,
                TreasuryTransaction.direction != data.flow,
            )
        )
        if (conflicting or 0) > 0:
            raise HTTPException(409, "لا يمكن تغيير نوع الفئة: عليها حركات بالاتجاه العكسي")
    c.name = new_name
    c.flow = data.flow
    if data.coa_account_id is not None:
        c.coa_account_id = data.coa_account_id
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "name": c.name, "flow": c.flow, "coa_account_id": c.coa_account_id}


@router.delete("/categories/{category_id}", status_code=204)
async def delete_category(category_id: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_dc
    _tid_dc = _ctid_dc.get() or 1
    c = await db.scalar(select(TreasuryCategory).where(
        TreasuryCategory.id == category_id, TreasuryCategory.tenant_id == _tid_dc,
    ))
    if not c:
        raise HTTPException(404, "الفئة غير موجودة")
    # فئة عليها حركات: حذفها كان يسيب حركات بـ category_id يتيم (أو خطأ FK 500 على Postgres)
    used = await db.scalar(
        select(func.count()).select_from(TreasuryTransaction).where(
            TreasuryTransaction.category_id == category_id,
            TreasuryTransaction.tenant_id == _tid_dc,
        )
    )
    if (used or 0) > 0:
        raise HTTPException(409, "لا يمكن حذف فئة مستخدمة في حركات خزنة")
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
    tid = _current_tid()
    q = select(TreasuryTransaction).where(TreasuryTransaction.tenant_id == tid) \
        .order_by(TreasuryTransaction.txn_date.desc(), TreasuryTransaction.id.desc())
    if treasury_id:
        q = q.where(TreasuryTransaction.treasury_id == treasury_id)
    if direction in ("in", "out"):
        q = q.where(TreasuryTransaction.direction == direction)
    if category_id:
        q = q.where(TreasuryTransaction.category_id == category_id)
    rows = (await db.execute(q)).scalars().all()

    # نجيب أسماء الفئات والخزن
    cats = {c.id: c.name for c in (await db.execute(
        select(TreasuryCategory).where(TreasuryCategory.tenant_id == tid))).scalars().all()}
    treas = {t.id: t.name for t in (await db.execute(
        select(Treasury).where(Treasury.tenant_id == tid))).scalars().all()}
    return [_txn(x, cats.get(x.category_id), treas.get(x.treasury_id)) for x in rows]


@router.post("/transactions", status_code=201)
async def create_transaction(data: TxnIn, db: AsyncSession = Depends(get_db)):
    if data.direction not in ("in", "out"):
        raise HTTPException(400, "الاتجاه لازم يكون in أو out")
    amt = _money(data.amount)
    payment_method = data.payment_method or "cash"
    if payment_method not in ("cash", "cheque", "wallet", "other"):
        raise HTTPException(400, "طريقة الدفع غير صحيحة")
    cheque_status = None
    if payment_method == "cheque":
        if not (data.cheque_number or "").strip() or not data.cheque_date:
            raise HTTPException(400, "رقم الشيك وتاريخ الاستحقاق مطلوبان")
        cheque_status = "under_collection"
    from app.core.database import current_tenant_id as _ctid_ttx
    tid = _ctid_ttx.get() or 1
    # الخزنة والفئة لازم يكونوا من نفس الـ tenant، والخزنة نشطة، والفئة متوافقة مع الاتجاه
    t = await _get_treasury_checked(db, tid, data.treasury_id)
    await _get_category_checked(db, tid, data.category_id, data.direction)
    # تحذير: رصيد غير كافٍ
    if data.direction == "out":
        current = Decimal(str(t.balance or 0))
        if amt > current:
            raise HTTPException(400,
                f"الرصيد الحالي للخزنة ({float(current):,.2f}) أقل من المبلغ المطلوب صرفه ({float(amt):,.2f})")
    receipt_no = await _next_receipt_no(db, data.direction)
    x = TreasuryTransaction(
        tenant_id=tid,
        treasury_id=data.treasury_id,
        direction=data.direction,
        amount=amt,
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
    await _apply_to_balance(db, data.treasury_id, data.direction, amt)
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
    from app.core.database import current_tenant_id as _ctid_utx
    tid = _ctid_utx.get() or 1
    x = await db.scalar(select(TreasuryTransaction).where(
        TreasuryTransaction.id == txn_id, TreasuryTransaction.tenant_id == tid,
    ))
    if not x:
        raise HTTPException(404, "الحركة غير موجودة")
    if x.source != "manual":
        raise HTTPException(400, "لا يمكن تعديل حركة تلقائية مرتبطة بفاتورة أو دفعة")
    if getattr(x, "cheque_status", None) in ("cleared", "bounced"):
        raise HTTPException(400, "لا يمكن تعديل شيك مُقاص أو مرتجع")

    # الأثر القديم (على الرصيد والقيد المحاسبي) لسه فعّال إلا لو كان شيكاً مرتجعاً/ملغياً
    # (في الحالة دي يكون الأثر اتعكس بالفعل من update_cheque_status)
    old_balance_active = x.cheque_status not in ("bounced", "cancelled")

    # التحقق من المدخلات قبل أي عكس للأثر القديم
    if data.direction not in ("in", "out"):
        raise HTTPException(400, "الاتجاه لازم يكون in أو out")
    amt = _money(data.amount)
    if data.treasury_id != x.treasury_id:
        # نقل الحركة لخزنة تانية: لازم تكون من نفس الـ tenant ونشطة
        await _get_treasury_checked(db, tid, data.treasury_id)
    else:
        same_t = await db.scalar(select(Treasury.id).where(
            Treasury.id == data.treasury_id, Treasury.tenant_id == tid,
        ))
        if not same_t:
            raise HTTPException(404, "الخزنة غير موجودة")
    await _get_category_checked(db, tid, data.category_id, data.direction)
    _before_bal = await _balances_snapshot(db, [x.treasury_id, data.treasury_id])

    # عكس القيد المحاسبي القديم المرتبط بالحركة — وإلا يفضل قيد بالبيانات القديمة
    # في دليل الحسابات (COA) بعد ما نعدّل بيانات الحركة، وميزان المراجعة يبقى غير متوافق
    # مع رصيد الخزنة الفعلي
    if old_balance_active:
        from app.services.auto_journal import journal_reverse_entry
        await journal_reverse_entry(db, tid, "treasury_manual", x.id, "تعديل الحركة")

    # عكس الأثر القديم على رصيد الخزنة
    if old_balance_active:
        await _apply_to_balance(db, x.treasury_id, x.direction, x.amount, revert=True)

    # تحقق من كفاية الرصيد إن كان صرف (فقط لو الحركة لها أثر فعّال على الرصيد)
    if data.direction == "out" and old_balance_active:
        t = await db.get(Treasury, data.treasury_id)
        if t:
            current = Decimal(str(t.balance or 0))
            if amt > current:
                raise HTTPException(400,
                    f"الرصيد الحالي للخزنة ({float(current):,.2f}) أقل من المبلغ المطلوب صرفه ({float(amt):,.2f})")

    x.treasury_id = data.treasury_id
    x.direction = data.direction
    x.amount = amt
    x.category_id = data.category_id
    x.txn_date = data.txn_date or x.txn_date
    x.description = data.description
    x.reference = data.reference

    # إصلاح: الشيك الملغي أثره معكوس بالفعل — كان التعديل يضيف المبلغ الجديد للرصيد
    # من غير ما يعكس القديم، فيتضخم الرصيد بمبلغ شيك ملغي
    if old_balance_active:
        await _apply_to_balance(db, data.treasury_id, data.direction, amt)
        # تقليل مبلغ "قبض" (أو نقله لخزنة تانية) بعد ما اتصرف منه كان يخلّي الرصيد سالباً
        await _guard_not_negative(db, _before_bal, "تعديل الحركة")

    # إعادة ترحيل قيد محاسبي جديد يعكس البيانات المحدَّثة (نفس شرط الترحيل في create_transaction)
    if old_balance_active:
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

    cats = {c.id: c.name for c in (await db.execute(
        select(TreasuryCategory).where(TreasuryCategory.tenant_id == tid))).scalars().all()}
    treas = {t.id: t.name for t in (await db.execute(
        select(Treasury).where(Treasury.tenant_id == tid))).scalars().all()}
    return _txn(x, cats.get(x.category_id), treas.get(x.treasury_id))


@router.delete("/transactions/{txn_id}", status_code=204)
async def delete_transaction(txn_id: int, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_dtx
    tid = _ctid_dtx.get() or 1
    x = await db.scalar(select(TreasuryTransaction).where(
        TreasuryTransaction.id == txn_id, TreasuryTransaction.tenant_id == tid,
    ))
    if not x:
        raise HTTPException(404, "الحركة غير موجودة")
    # الحركات التلقائية (دفعة عميل/مورد، رواتب، سلف، أصول...) مرتبطة بمستند أصلي —
    # حذفها من هنا كان يعكس الرصيد ويسيب المستند الأصلي (والقيد) على حاله.
    # الواجهة أصلاً بتعرض الحذف للحركات اليدوية بس.
    if x.source not in ("manual", "transfer"):
        raise HTTPException(409, "لا يمكن حذف حركة تلقائية مرتبطة بمستند — احذف/اعكس المستند الأصلي")

    # التحويل: حذف رِجل واحدة كان يعكس قيد التحويل كله ويسيب الرِجل التانية في
    # الخزنة الأخرى فيختل الرصيدان — نحذف رِجلي التحويل معاً
    if x.source == "transfer" and x.transfer_group:
        legs = (await db.execute(
            select(TreasuryTransaction).where(
                TreasuryTransaction.transfer_group == x.transfer_group,
                TreasuryTransaction.tenant_id == tid,
            )
        )).scalars().all()
        out_leg_id = None
        _before_bal = await _balances_snapshot(db, [leg.treasury_id for leg in legs])
        for leg in legs:
            if leg.direction == "out":
                out_leg_id = leg.id
            await _apply_to_balance(db, leg.treasury_id, leg.direction, leg.amount, revert=True)
        # الخزنة المستلمة ممكن تكون صرفت مبلغ التحويل بالفعل
        await _guard_not_negative(db, _before_bal, "حذف التحويل")
        if out_leg_id:
            from app.services.auto_journal import journal_reverse_entry
            await journal_reverse_entry(db, tid, "treasury_transfer", out_leg_id, "حذف حركة التحويل")
        for leg in legs:
            await db.delete(leg)
        await db.commit()
        return

    balance_active = x.cheque_status not in ("bounced", "cancelled")
    # نعكس أثرها على الرصيد — إلا لو كان شيكاً مرتجعاً/ملغياً (أثره معكوس أصلاً)
    if balance_active:
        _before_bal = await _balances_snapshot(db, [x.treasury_id])
        await _apply_to_balance(db, x.treasury_id, x.direction, x.amount, revert=True)
        await _guard_not_negative(db, _before_bal, "حذف الحركة")

    # عكس القيد المحاسبي المرتبط بالحركة (لو كان مرحّلاً فعلياً) — وإلا يفضل قيد يتيم
    # في دليل الحسابات بعد حذف الحركة، فيختل تطابق ميزان المراجعة مع رصيد الخزينة الفعلي
    if balance_active:
        from app.services.auto_journal import journal_reverse_entry
        if x.source == "manual":
            await journal_reverse_entry(db, tid, "treasury_manual", x.id, "حذف الحركة")
        elif x.source == "transfer" and x.transfer_group:
            # القيد اتسجل مربوطاً بمعرّف حركة "الخروج" (out leg) من التحويل تحديداً
            out_leg_id = await db.scalar(
                select(TreasuryTransaction.id).where(
                    TreasuryTransaction.transfer_group == x.transfer_group,
                    TreasuryTransaction.direction == "out",
                    TreasuryTransaction.tenant_id == tid,
                )
            )
            if out_leg_id:
                await journal_reverse_entry(db, tid, "treasury_transfer", out_leg_id, "حذف حركة التحويل")

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
    tid = _current_tid()
    q = select(TreasuryTransaction).where(
        TreasuryTransaction.payment_method == "cheque",
        TreasuryTransaction.tenant_id == tid,
    ).order_by(TreasuryTransaction.cheque_date.asc())
    if cheque_status:
        q = q.where(TreasuryTransaction.cheque_status == cheque_status)
    if direction in ("in", "out"):
        q = q.where(TreasuryTransaction.direction == direction)
    rows = (await db.execute(q)).scalars().all()

    cats = {c.id: c.name for c in (await db.execute(
        select(TreasuryCategory).where(TreasuryCategory.tenant_id == tid))).scalars().all()}
    treas = {t.id: t.name for t in (await db.execute(
        select(Treasury).where(Treasury.tenant_id == tid))).scalars().all()}
    return [_txn(x, cats.get(x.category_id), treas.get(x.treasury_id)) for x in rows]


@router.patch("/cheques/{txn_id}/status")
async def update_cheque_status(txn_id: int, data: ChequeStatusIn, db: AsyncSession = Depends(get_db)):
    x = await db.scalar(select(TreasuryTransaction).where(
        TreasuryTransaction.id == txn_id, TreasuryTransaction.tenant_id == _current_tid(),
    ))
    if not x:
        raise HTTPException(404, "الحركة غير موجودة")
    if x.payment_method != "cheque":
        raise HTTPException(400, "الحركة ليست شيكاً")
    if data.cheque_status not in ("under_collection", "cleared", "bounced", "cancelled"):
        raise HTTPException(400, "حالة الشيك غير صحيحة")

    old_status = x.cheque_status
    new_status = data.cheque_status
    if old_status == new_status:
        return _txn(x)

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
        # إعادة تفعيل شيك صادر = صرف جديد من الخزنة — نفس سياسة منع السحب على المكشوف
        if x.direction == "out":
            t_chq = await db.get(Treasury, x.treasury_id)
            cur = Decimal(str((t_chq.balance if t_chq else 0) or 0))
            if Decimal(str(x.amount or 0)) > cur:
                raise HTTPException(400,
                    f"الرصيد الحالي للخزنة ({float(cur):,.2f}) أقل من مبلغ الشيك ({float(x.amount or 0):,.2f})")
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
    tamt = _money(data.amount)
    from app.core.database import current_tenant_id as _ctid_xfr
    _tid_xfr = _ctid_xfr.get() or 1
    # الخزنتين لازم يكونوا من نفس الـ tenant ونشطين (قبل كده خزنة وجهة غير موجودة/
    # من tenant تاني كانت بتتقبل لو الـ get رجع None للمصدر)
    src = await _get_treasury_checked(db, _tid_xfr, data.from_treasury_id)
    await _get_treasury_checked(db, _tid_xfr, data.to_treasury_id)
    # تحقق من رصيد المصدر
    src_bal = Decimal(str(src.balance or 0))
    if tamt > src_bal:
        raise HTTPException(400,
            f"رصيد خزنة المصدر ({float(src_bal):,.2f}) أقل من مبلغ التحويل ({float(tamt):,.2f})")
    grp = uuid4().hex
    d = data.txn_date or date.today()
    desc = data.description or "تحويل بين الخزن"

    # خروج من المصدر
    out_txn = TreasuryTransaction(
        tenant_id=_tid_xfr,
        treasury_id=data.from_treasury_id, direction="out", amount=tamt,
        txn_date=d, description=desc, source="transfer", transfer_group=grp,
    )
    await _apply_to_balance(db, data.from_treasury_id, "out", tamt)
    # دخول للهدف
    in_txn = TreasuryTransaction(
        tenant_id=_tid_xfr,
        treasury_id=data.to_treasury_id, direction="in", amount=tamt,
        txn_date=d, description=desc, source="transfer", transfer_group=grp,
    )
    await _apply_to_balance(db, data.to_treasury_id, "in", tamt)
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
    tid = _current_tid()
    treas = (await db.execute(select(Treasury).where(Treasury.tenant_id == tid))).scalars().all()
    total_balance = sum(float(t.balance or 0) for t in treas)

    total_in = (await db.execute(
        select(func.coalesce(func.sum(TreasuryTransaction.amount), 0)).where(
            TreasuryTransaction.direction == "in",
            TreasuryTransaction.source != "transfer",
            TreasuryTransaction.tenant_id == tid,
            # الشيكات المرتجعة/الملغاة أثرها معكوس من الرصيد — ماتتحسبش في الإجمالي
            or_(TreasuryTransaction.cheque_status.is_(None),
                TreasuryTransaction.cheque_status.notin_(("bounced", "cancelled"))),
        )
    )).scalar() or 0
    total_out = (await db.execute(
        select(func.coalesce(func.sum(TreasuryTransaction.amount), 0)).where(
            TreasuryTransaction.direction == "out",
            TreasuryTransaction.source != "transfer",
            TreasuryTransaction.tenant_id == tid,
            # الشيكات المرتجعة/الملغاة أثرها معكوس من الرصيد — ماتتحسبش في الإجمالي
            or_(TreasuryTransaction.cheque_status.is_(None),
                TreasuryTransaction.cheque_status.notin_(("bounced", "cancelled"))),
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
    txn = await db.scalar(select(TreasuryTransaction).where(
        TreasuryTransaction.id == txn_id, TreasuryTransaction.tenant_id == _current_tid(),
    ))
    if not txn:
        raise HTTPException(404, "الحركة غير موجودة")
    company = await _voucher_company(db)
    treasury = await db.get(Treasury, txn.treasury_id)
    treasury_name = treasury.name if treasury else "—"
    cat = await db.get(TreasuryCategory, txn.category_id) if txn.category_id else None
    cat_name = cat.name if cat else "—"
    return _voucher_html(txn, company, treasury_name, cat_name)
