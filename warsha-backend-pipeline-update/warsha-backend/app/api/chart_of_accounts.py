"""
Chart of Accounts + Trial Balance + Double-Entry Journal
دليل الحسابات + ميزان المراجعة + ربط دفعات العميل بالفواتير
"""
from datetime import date
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, validator

from app.core.database import get_db
from app.api.auth import require_any
from app.models.models import (
    ChartOfAccount, AccountingEntry, AccountingEntryLine,
    CustomerPayment, CustomerPaymentAllocation, Invoice,
    Customer,
)

router = APIRouter(prefix="/accounts", tags=["accounting_v2"], dependencies=[Depends(require_any)])


# ── Schemas ────────────────────────────────────────────────────

_VALID_ACCOUNT_TYPES = {"asset", "liability", "equity", "revenue", "expense"}
_VALID_SIDES = {"debit", "credit"}


class AccountIn(BaseModel):
    code: str
    name_ar: str
    name_en: Optional[str] = None
    account_type: str   # asset | liability | equity | revenue | expense
    normal_balance: str = "debit"
    parent_id: Optional[int] = None
    notes: Optional[str] = None

    @validator("account_type")
    def _valid_account_type(cls, v):
        # حماية: حساب بنوع غلط (أو خطأ إملائي) بيختفي بصمت من الميزانية/قائمة الدخل
        # لأن الأقسام هناك بتفلتر بالضبط على القيم دي — مايكسرش بس بيبوّظ التوازن
        if v not in _VALID_ACCOUNT_TYPES:
            raise ValueError(f"account_type يجب أن يكون أحد: {sorted(_VALID_ACCOUNT_TYPES)}")
        return v

    @validator("normal_balance")
    def _valid_normal_balance(cls, v):
        if v not in _VALID_SIDES:
            raise ValueError("normal_balance يجب أن يكون 'debit' أو 'credit'")
        return v


class EntryLineIn(BaseModel):
    account_id: int
    side: str   # debit | credit
    amount: Decimal
    notes: Optional[str] = None

    @validator("side")
    def _valid_side(cls, v):
        # حماية أساسية: لو الـ side مش "debit"/"credit" بالظبط (فرق حالة أحرف مثلاً)،
        # الـ validator العام "balanced" تحت بيحسبه صفر في الطرفين فيعدي القيد
        # كـ"متوازن" وهو مش متوازن فعلاً — ويختفي من ميزان المراجعة بصمت.
        if v not in _VALID_SIDES:
            raise ValueError("side يجب أن يكون 'debit' أو 'credit'")
        return v

    @validator("amount")
    def _positive_amount(cls, v):
        if v <= 0:
            raise ValueError("amount يجب أن يكون أكبر من صفر")
        return v


class EntryIn(BaseModel):
    entry_date: date
    reference: Optional[str] = None
    description: str
    source: str = "manual"
    source_id: Optional[int] = None
    lines: List[EntryLineIn]

    @validator("lines")
    def balanced(cls, lines):
        debits = sum(l.amount for l in lines if l.side == "debit")
        credits = sum(l.amount for l in lines if l.side == "credit")
        if abs(debits - credits) > Decimal("0.01"):
            raise ValueError(f"القيد غير متوازن: مدين={float(debits):.2f}, دائن={float(credits):.2f}")
        return lines


class AllocationIn(BaseModel):
    invoice_id: int
    amount: Decimal

    @validator("amount")
    def _positive_amount(cls, v):
        # حماية: بدون التحقق ده، amount=0 أو سالب كان يعدي من غير أي خطأ ويقدر
        # يقلل paid_amount الفاتورة أو يسجل تخصيصاً وهمياً بدون أي دفعة فعلية
        if v <= 0:
            raise ValueError("amount يجب أن يكون أكبر من صفر")
        return v


# ── Default Chart of Accounts seed ────────────────────────────

DEFAULT_ACCOUNTS = [
    # Assets
    ("1000", "الأصول", None, "asset", "debit"),
    ("1100", "النقدية والبنوك", "1000", "asset", "debit"),
    ("1110", "الصندوق", "1100", "asset", "debit"),
    ("1120", "البنك الرئيسي", "1100", "asset", "debit"),
    ("1200", "الذمم المدينة", "1000", "asset", "debit"),
    ("1210", "العملاء", "1200", "asset", "debit"),
    ("1230", "ضريبة القيمة المضافة على المشتريات (مدخلات)", "1200", "asset", "debit"),
    ("1300", "المخزون", "1000", "asset", "debit"),
    ("1310", "مخزون خام", "1300", "asset", "debit"),
    ("1320", "إنتاج تحت التشغيل (WIP)", "1300", "asset", "debit"),
    ("1330", "مخزون تام الصنع", "1300", "asset", "debit"),
    ("1400", "الأصول الثابتة", "1000", "asset", "debit"),
    ("1410", "مجمع الإهلاك", "1400", "asset", "credit"),
    # Liabilities
    ("2000", "الخصوم", None, "liability", "credit"),
    ("2100", "الذمم الدائنة", "2000", "liability", "credit"),
    ("2110", "الموردون", "2100", "liability", "credit"),
    ("2200", "المستحقات", "2000", "liability", "credit"),
    ("2210", "الأجور المستحقة", "2200", "liability", "credit"),
    ("2300", "ضرائب مستحقة", "2000", "liability", "credit"),
    ("2310", "ضريبة القيمة المضافة المستحقة (مخرجات)", "2300", "liability", "credit"),
    ("2320", "ضريبة الخصم تحت الحساب المستحقة", "2300", "liability", "credit"),
    ("2330", "ضريبة الدخل المستحقة (تقديرية)", "2300", "liability", "credit"),
    # Equity
    ("3000", "حقوق الملكية", None, "equity", "credit"),
    ("3100", "رأس المال", "3000", "equity", "credit"),
    ("3200", "الأرباح المحتجزة", "3000", "equity", "credit"),
    # Revenue
    ("4000", "الإيرادات", None, "revenue", "credit"),
    ("4100", "إيرادات المبيعات", "4000", "revenue", "credit"),
    ("4200", "إيرادات المشاريع الخارجية", "4000", "revenue", "credit"),
    ("4300", "إيرادات الخدمة الميدانية", "4000", "revenue", "credit"),
    ("4400", "إيرادات بيع الهالك", "4000", "revenue", "credit"),
    # Expenses
    ("5000", "المصروفات", None, "expense", "debit"),
    ("5100", "تكلفة المواد المباشرة", "5000", "expense", "debit"),
    ("5200", "تكلفة العمالة المباشرة", "5000", "expense", "debit"),
    ("5300", "المصروفات الصناعية الغير مباشرة", "5000", "expense", "debit"),
    ("5400", "مصاريف بيعية وتسويقية", "5000", "expense", "debit"),
    ("5500", "مصاريف إدارية وعمومية", "5000", "expense", "debit"),
    ("5600", "مصاريف هالك وإتلاف", "5000", "expense", "debit"),
    ("5700", "مصروف الإهلاك", "5000", "expense", "debit"),
    ("5800", "ضريبة الدخل", "5000", "expense", "debit"),
]


# ══════════════════════════════════════════════════════════════
# CHART OF ACCOUNTS
# ══════════════════════════════════════════════════════════════

@router.get("/chart")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1
    res = await db.execute(
        select(ChartOfAccount)
        .where(ChartOfAccount.tenant_id == tid, ChartOfAccount.is_active == True)
        .order_by(ChartOfAccount.code)
    )
    items = res.scalars().all()
    return [{
        "id": a.id, "code": a.code, "name_ar": a.name_ar, "name_en": a.name_en,
        "account_type": a.account_type, "normal_balance": a.normal_balance,
        "parent_id": a.parent_id, "is_active": a.is_active,
    } for a in items]


async def seed_default_accounts(db: AsyncSession, tenant_id: int) -> int:
    """
    زرع دليل الحسابات الافتراضي لـ tenant معيّن — بدون commit (المستدعي يتحكم في الـ transaction).
    تُستخدم من endpoint الزرع اليدوي وكذلك من signup لضمان عدم وجود ورشة بدون دليل حسابات.
    لا تفعل شيئاً لو الحسابات موجودة بالفعل لهذا الـ tenant.
    """
    count = await db.scalar(select(func.count()).select_from(ChartOfAccount).where(ChartOfAccount.tenant_id == tenant_id))
    if count and count > 0:
        return 0

    code_to_id: dict = {}
    for code, name, parent_code, acc_type, normal_bal in DEFAULT_ACCOUNTS:
        parent_id = code_to_id.get(parent_code) if parent_code else None
        acc = ChartOfAccount(
            tenant_id=tenant_id, code=code, name_ar=name,
            account_type=acc_type, normal_balance=normal_bal,
            parent_id=parent_id,
        )
        db.add(acc)
        await db.flush()
        code_to_id[code] = acc.id

    return len(DEFAULT_ACCOUNTS)


@router.post("/chart/seed")
async def seed_chart(db: AsyncSession = Depends(get_db)):
    """زرع دليل الحسابات الافتراضي — يُنفَّذ مرة واحدة عند الإعداد"""
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1

    added = await seed_default_accounts(db, tid)
    if added == 0:
        count = await db.scalar(select(func.count()).select_from(ChartOfAccount).where(ChartOfAccount.tenant_id == tid))
        await db.commit()
        return {"message": "الحسابات موجودة بالفعل", "count": count}

    await db.commit()
    return {"message": "تم زرع دليل الحسابات", "count": added}


@router.post("/chart", status_code=201)
async def create_account(data: AccountIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1

    exists = await db.scalar(
        select(ChartOfAccount.id).where(ChartOfAccount.code == data.code, ChartOfAccount.tenant_id == tid)
    )
    if exists:
        raise HTTPException(400, f"كود الحساب {data.code} موجود بالفعل")

    acc = ChartOfAccount(tenant_id=tid, **data.model_dump())
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return {"id": acc.id, "code": acc.code, "name_ar": acc.name_ar}


@router.put("/chart/{acc_id}")
async def update_account(acc_id: int, data: AccountIn, db: AsyncSession = Depends(get_db)):
    acc = await db.get(ChartOfAccount, acc_id)
    if not acc:
        raise HTTPException(404)
    if data.code != acc.code:
        # منع كودين لنفس الحساب لنفس الـ tenant — كود مكرر يخلي _get_account_id/
        # trial-balance يلقوا حسابين بنفس الكود وده بيكسر ربط القيود التلقائية بحساب محدد
        dup = await db.scalar(
            select(ChartOfAccount.id).where(
                ChartOfAccount.code == data.code,
                ChartOfAccount.tenant_id == acc.tenant_id,
                ChartOfAccount.id != acc_id,
            )
        )
        if dup:
            raise HTTPException(400, f"كود الحساب {data.code} مستخدم بالفعل لحساب آخر")
    for k, v in data.model_dump().items():
        setattr(acc, k, v)
    await db.commit()
    return {"id": acc.id, "code": acc.code}


# ══════════════════════════════════════════════════════════════
# DOUBLE-ENTRY JOURNAL
# ══════════════════════════════════════════════════════════════

@router.get("/entries")
async def list_entries(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    source: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1
    q = select(AccountingEntry).options(selectinload(AccountingEntry.lines)).where(AccountingEntry.tenant_id == tid)
    if from_date:
        q = q.where(AccountingEntry.entry_date >= from_date)
    if to_date:
        q = q.where(AccountingEntry.entry_date <= to_date)
    if source:
        q = q.where(AccountingEntry.source == source)
    res = await db.execute(q.order_by(AccountingEntry.entry_date.desc()))
    entries = res.scalars().all()
    return [_entry_dict(e) for e in entries]


def _entry_dict(e: AccountingEntry) -> dict:
    return {
        "id": e.id, "entry_date": str(e.entry_date),
        "reference": e.reference, "description": e.description,
        "source": e.source, "source_id": e.source_id, "posted": e.posted,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "total_debit": float(sum(l.amount for l in e.lines if l.side == "debit")),
        "total_credit": float(sum(l.amount for l in e.lines if l.side == "credit")),
        "lines": [{
            "id": l.id, "account_id": l.account_id,
            "side": l.side, "amount": float(l.amount), "notes": l.notes,
        } for l in e.lines],
    }


@router.post("/entries", status_code=201)
async def create_entry(data: EntryIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1

    entry = AccountingEntry(
        tenant_id=tid,
        entry_date=data.entry_date,
        reference=data.reference,
        description=data.description,
        source=data.source,
        source_id=data.source_id,
    )
    db.add(entry)
    await db.flush()

    for ld in data.lines:
        acc = await db.get(ChartOfAccount, ld.account_id)
        if not acc:
            raise HTTPException(400, f"الحساب {ld.account_id} غير موجود")
        db.add(AccountingEntryLine(
            entry_id=entry.id,
            account_id=ld.account_id,
            side=ld.side,
            amount=ld.amount,
            notes=ld.notes,
        ))

    await db.commit()
    res = await db.execute(
        select(AccountingEntry).options(selectinload(AccountingEntry.lines)).where(AccountingEntry.id == entry.id)
    )
    return _entry_dict(res.scalar_one())


@router.patch("/entries/{entry_id}/post")
async def post_entry(entry_id: int, db: AsyncSession = Depends(get_db)):
    """ترحيل القيد — بعد الترحيل لا يمكن التعديل"""
    e = await db.get(AccountingEntry, entry_id)
    if not e:
        raise HTTPException(404)
    if e.posted:
        raise HTTPException(400, "القيد مرحّل بالفعل")
    e.posted = True
    await db.commit()
    return {"id": entry_id, "posted": True}


# ══════════════════════════════════════════════════════════════
# TRIAL BALANCE — ميزان المراجعة
# ══════════════════════════════════════════════════════════════

@router.get("/trial-balance")
async def trial_balance(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    ميزان المراجعة: لكل حساب → مجموع المدين، مجموع الدائن، الرصيد
    يستخدم accounting_entries الجديدة + journal_entries القديمة (للتوافق)
    """
    from app.core.database import current_tenant_id
    from app.models.models import JournalEntry
    tid = current_tenant_id.get() or 1

    # 1. من القيود الجديدة (accounting_entry_lines)
    q = (
        select(
            AccountingEntryLine.account_id,
            AccountingEntryLine.side,
            func.sum(AccountingEntryLine.amount).label("total"),
        )
        .join(AccountingEntry, AccountingEntry.id == AccountingEntryLine.entry_id)
        .where(AccountingEntry.tenant_id == tid, AccountingEntry.posted == True)
        .group_by(AccountingEntryLine.account_id, AccountingEntryLine.side)
    )
    if from_date:
        q = q.where(AccountingEntry.entry_date >= from_date)
    if to_date:
        q = q.where(AccountingEntry.entry_date <= to_date)

    rows = (await db.execute(q)).all()

    balances: dict[int, dict] = {}
    for account_id, side, total in rows:
        if account_id not in balances:
            balances[account_id] = {"debit": Decimal("0"), "credit": Decimal("0")}
        balances[account_id][side] = balances[account_id].get(side, Decimal("0")) + (total or Decimal("0"))

    # 2. جلب أسماء الحسابات
    accs_res = await db.execute(
        select(ChartOfAccount).where(ChartOfAccount.tenant_id == tid, ChartOfAccount.is_active == True).order_by(ChartOfAccount.code)
    )
    accs = {a.id: a for a in accs_res.scalars().all()}

    result = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")

    for acc_id, acc in accs.items():
        b = balances.get(acc_id, {"debit": Decimal("0"), "credit": Decimal("0")})
        debit = b["debit"]
        credit = b["credit"]
        raw_balance = debit - credit
        # الرصيد مُعدَّل العلامة حسب طبيعة الحساب — سالب يعني الحساب شاذ (خرج عن طبيعته)
        balance = raw_balance if acc.normal_balance == "debit" else -raw_balance
        if debit == 0 and credit == 0:
            continue
        total_debit += debit
        total_credit += credit
        result.append({
            "account_id": acc_id,
            "code": acc.code,
            "name_ar": acc.name_ar,
            "account_type": acc.account_type,
            "normal_balance": acc.normal_balance,
            "total_debit": float(debit),
            "total_credit": float(credit),
            "balance": float(balance),
            "raw_balance": float(raw_balance),
            "is_abnormal": balance < 0,
        })

    return {
        "from_date": str(from_date) if from_date else None,
        "to_date": str(to_date) if to_date else None,
        "accounts": result,
        "totals": {
            "debit": float(total_debit),
            "credit": float(total_credit),
            "balanced": abs(total_debit - total_credit) < Decimal("0.01"),
        },
    }


# ══════════════════════════════════════════════════════════════
# CUSTOMER PAYMENT ALLOCATION — ربط دفعة بفاتورة
# ══════════════════════════════════════════════════════════════

@router.post("/payments/{payment_id}/allocate")
async def allocate_payment(
    payment_id: int,
    allocations: List[AllocationIn],
    db: AsyncSession = Depends(get_db),
):
    """
    ربط دفعة عميل بفاتورة أو أكثر.
    يتحقق أن المبالغ لا تتجاوز قيمة الدفعة ولا قيمة الفاتورة المتبقية.
    """
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1

    payment = await db.get(CustomerPayment, payment_id)
    if not payment:
        raise HTTPException(404, "الدفعة غير موجودة")

    # المبالغ المخصصة مسبقاً
    already = await db.scalar(
        select(func.coalesce(func.sum(CustomerPaymentAllocation.amount), 0))
        .where(CustomerPaymentAllocation.payment_id == payment_id)
    ) or Decimal("0")

    new_total = sum(a.amount for a in allocations)
    available = payment.amount - Decimal(str(already))

    if new_total > available + Decimal("0.01"):
        raise HTTPException(400, f"المبالغ المخصصة ({float(new_total):.2f}) تتجاوز الرصيد المتاح ({float(available):.2f})")

    results = []
    for alloc in allocations:
        inv = await db.get(Invoice, alloc.invoice_id)
        if not inv:
            raise HTTPException(404, f"الفاتورة {alloc.invoice_id} غير موجودة")

        remaining_inv = inv.total - inv.paid_amount
        if alloc.amount > remaining_inv + Decimal("0.01"):
            raise HTTPException(400, f"المبلغ يتجاوز المتبقي في الفاتورة {inv.code} ({float(remaining_inv):.2f})")

        # تحديث الفاتورة
        inv.paid_amount += alloc.amount
        if inv.paid_amount >= inv.total:
            inv.status = "paid"
        elif inv.paid_amount > 0:
            inv.status = "partial"

        # محطة 5 (إصلاح ثغرة تكامل): نفس منطق توزيع الدفعة على الأقساط
        # المستخدم في شاشة الفاتورة، لازم يتطبّق هنا كمان.
        from app.api.sales import _allocate_payment_to_installments
        await _allocate_payment_to_installments(db, inv.id, alloc.amount)

        pa = CustomerPaymentAllocation(
            tenant_id=tid,
            payment_id=payment_id,
            invoice_id=alloc.invoice_id,
            amount=alloc.amount,
        )
        db.add(pa)
        results.append({"invoice_id": alloc.invoice_id, "invoice_code": inv.code, "allocated": float(alloc.amount)})

    await db.commit()
    return {"payment_id": payment_id, "allocations": results}


@router.get("/payments/{payment_id}/allocations")
async def get_allocations(payment_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(CustomerPaymentAllocation)
        .options(selectinload(CustomerPaymentAllocation.invoice))
        .where(CustomerPaymentAllocation.payment_id == payment_id)
    )
    items = res.scalars().all()
    return [{
        "id": a.id,
        "invoice_id": a.invoice_id,
        "invoice_code": a.invoice.code if a.invoice else None,
        "amount": float(a.amount),
        "created_at": a.created_at.isoformat() if a.created_at else None,
    } for a in items]


# ══════════════════════════════════════════════════════════════
# INCOME STATEMENT (قائمة الدخل المبسطة)
# ══════════════════════════════════════════════════════════════

@router.get("/income-statement")
async def income_statement(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """قائمة دخل مبسطة من accounting_entries"""
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1

    q = (
        select(
            ChartOfAccount.code,
            ChartOfAccount.name_ar,
            ChartOfAccount.account_type,
            ChartOfAccount.normal_balance,
            AccountingEntryLine.side,
            func.sum(AccountingEntryLine.amount).label("total"),
        )
        .join(AccountingEntry, AccountingEntry.id == AccountingEntryLine.entry_id)
        .join(ChartOfAccount, ChartOfAccount.id == AccountingEntryLine.account_id)
        .where(
            AccountingEntry.tenant_id == tid,
            AccountingEntry.posted == True,
            ChartOfAccount.account_type.in_(["revenue", "expense"]),
        )
        .group_by(
            ChartOfAccount.code, ChartOfAccount.name_ar,
            ChartOfAccount.account_type, ChartOfAccount.normal_balance,
            AccountingEntryLine.side,
        )
    )
    if from_date:
        q = q.where(AccountingEntry.entry_date >= from_date)
    if to_date:
        q = q.where(AccountingEntry.entry_date <= to_date)

    rows = (await db.execute(q)).all()

    revenue = Decimal("0")
    expenses = Decimal("0")
    rev_detail = {}
    exp_detail = {}

    for code, name, acc_type, normal_bal, side, total in rows:
        key = f"{code} - {name}"
        if acc_type == "revenue":
            amt = total if side == "credit" else -total
            rev_detail[key] = rev_detail.get(key, Decimal("0")) + amt
            revenue += amt
        elif acc_type == "expense":
            amt = total if side == "debit" else -total
            exp_detail[key] = exp_detail.get(key, Decimal("0")) + amt
            expenses += amt

    return {
        "from_date": str(from_date) if from_date else None,
        "to_date": str(to_date) if to_date else None,
        "revenue": float(revenue),
        "expenses": float(expenses),
        "net_income": float(revenue - expenses),
        "revenue_detail": {k: float(v) for k, v in rev_detail.items()},
        "expense_detail": {k: float(v) for k, v in exp_detail.items()},
    }


# ══════════════════════════════════════════════════════════════
# BALANCE SHEET — الميزانية العمومية
# ══════════════════════════════════════════════════════════════

@router.get("/balance-sheet")
async def balance_sheet(
    as_of_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    الميزانية العمومية: الأصول = الخصوم + حقوق الملكية
    تعمل على AccountingEntries المسجّلة في COA
    """
    from app.core.database import current_tenant_id
    from app.models.models import AccountingEntryLine, AccountingEntry, ChartOfAccount
    tid = current_tenant_id.get() or 1
    target_date = as_of_date or date.today()

    q = (
        select(
            ChartOfAccount.code,
            ChartOfAccount.name_ar,
            ChartOfAccount.account_type,
            ChartOfAccount.normal_balance,
            AccountingEntryLine.side,
            func.sum(AccountingEntryLine.amount).label("total"),
        )
        .join(AccountingEntry, AccountingEntry.id == AccountingEntryLine.entry_id)
        .join(ChartOfAccount, ChartOfAccount.id == AccountingEntryLine.account_id)
        .where(
            AccountingEntry.tenant_id == tid,
            AccountingEntry.posted == True,
            AccountingEntry.entry_date <= target_date,
        )
        .group_by(
            ChartOfAccount.code, ChartOfAccount.name_ar,
            ChartOfAccount.account_type, ChartOfAccount.normal_balance,
            AccountingEntryLine.side,
        )
    )
    rows = (await db.execute(q)).all()

    sections = {"asset": {}, "liability": {}, "equity": {}}

    for code, name, acc_type, normal_bal, side, total in rows:
        if acc_type not in sections:
            continue
        key = f"{code} — {name}"
        if key not in sections[acc_type]:
            sections[acc_type][key] = Decimal("0")
        # Normal balance determines if it adds or subtracts
        if side == normal_bal:
            sections[acc_type][key] += total or Decimal("0")
        else:
            sections[acc_type][key] -= total or Decimal("0")

    # نجمع كل القيم — بما فيها الشاذة (سالبة) — لأن استبعادها هنا يخفي عدم توازن حقيقي
    total_assets      = sum(sections["asset"].values())
    total_liabilities = sum(sections["liability"].values())
    total_equity       = sum(sections["equity"].values())

    abnormal_accounts = [
        f"{acc_type}: {key} ({float(v):,.2f})"
        for acc_type in ("asset", "liability", "equity")
        for key, v in sections[acc_type].items()
        if v < 0
    ]

    return {
        "as_of_date": str(target_date),
        "assets": {k: float(v) for k, v in sections["asset"].items() if v != 0},
        "liabilities": {k: float(v) for k, v in sections["liability"].items() if v != 0},
        "equity": {k: float(v) for k, v in sections["equity"].items() if v != 0},
        "totals": {
            "total_assets":      float(total_assets),
            "total_liabilities": float(total_liabilities),
            "total_equity":      float(total_equity),
            "balanced":          abs(total_assets - (total_liabilities + total_equity)) < Decimal("1"),
        },
        "abnormal_accounts": abnormal_accounts,
    }


# ══════════════════════════════════════════════════════════════
# VAT RETURN — تقرير ضريبة القيمة المضافة
# ══════════════════════════════════════════════════════════════

@router.get("/vat-return")
async def vat_return(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    تقرير ضريبة القيمة المضافة:
    - ضريبة المبيعات (مستحقة على العملاء)
    - ضريبة المشتريات (ضريبة مدخلات — قابلة للخصم)
    - صافي ضريبة مستحقة للمصلحة
    """
    from app.models.models import Invoice, PurchaseInvoice
    from sqlalchemy import extract

    q_sales = select(
        func.coalesce(func.sum(Invoice.tax_amount), 0).label("output_vat"),
        func.coalesce(func.sum(Invoice.subtotal), 0).label("taxable_sales"),
        func.coalesce(func.sum(Invoice.total), 0).label("total_sales"),
        func.count(Invoice.id).label("invoice_count"),
    )
    q_pur = select(
        func.coalesce(func.sum(PurchaseInvoice.tax_amount), 0).label("input_vat"),
        func.coalesce(func.sum(PurchaseInvoice.subtotal), 0).label("taxable_purchases"),
        func.coalesce(func.sum(PurchaseInvoice.total), 0).label("total_purchases"),
        func.count(PurchaseInvoice.id).label("invoice_count"),
    )

    if from_date:
        q_sales = q_sales.where(Invoice.date >= from_date)
        q_pur   = q_pur.where(PurchaseInvoice.date >= from_date)
    if to_date:
        q_sales = q_sales.where(Invoice.date <= to_date)
        q_pur   = q_pur.where(PurchaseInvoice.date <= to_date)

    # Only invoices with actual tax
    q_sales = q_sales.where(Invoice.tax_amount > 0)
    q_pur   = q_pur.where(PurchaseInvoice.tax_amount > 0)

    sales_row = (await db.execute(q_sales)).one()
    pur_row   = (await db.execute(q_pur)).one()

    output_vat  = Decimal(str(sales_row.output_vat or 0))
    input_vat   = Decimal(str(pur_row.input_vat or 0))
    net_vat_due = output_vat - input_vat

    return {
        "period": {
            "from_date": str(from_date) if from_date else None,
            "to_date":   str(to_date)   if to_date   else None,
        },
        "output_vat": {
            "description":      "ضريبة المخرجات (على المبيعات) — مستحقة للمصلحة",
            "taxable_amount":   float(sales_row.taxable_sales or 0),
            "vat_amount":       float(output_vat),
            "invoice_count":    sales_row.invoice_count or 0,
        },
        "input_vat": {
            "description":      "ضريبة المدخلات (على المشتريات) — قابلة للخصم",
            "taxable_amount":   float(pur_row.taxable_purchases or 0),
            "vat_amount":       float(input_vat),
            "invoice_count":    pur_row.invoice_count or 0,
        },
        "net_vat_due":    float(net_vat_due),
        "status":         "payable" if net_vat_due > 0 else ("refundable" if net_vat_due < 0 else "nil"),
        "status_ar":      "مستحق دفع" if net_vat_due > 0 else ("مسترد" if net_vat_due < 0 else "لا يوجد"),
    }


# ══════════════════════════════════════════════════════════════
# TAX SUMMARY — ملخص الضرائب المستحقة
# ══════════════════════════════════════════════════════════════

async def _account_balance(db: AsyncSession, tid: int, code: str, as_of_date: date) -> Decimal:
    """رصيد حساب واحد من COA حتى تاريخ معين (بالقيمة الطبيعية للحساب)"""
    row = (await db.execute(
        select(
            ChartOfAccount.normal_balance,
            AccountingEntryLine.side,
            func.sum(AccountingEntryLine.amount).label("total"),
        )
        .join(AccountingEntry, AccountingEntry.id == AccountingEntryLine.entry_id)
        .join(ChartOfAccount, ChartOfAccount.id == AccountingEntryLine.account_id)
        .where(
            AccountingEntry.tenant_id == tid,
            AccountingEntry.posted == True,
            AccountingEntry.entry_date <= as_of_date,
            ChartOfAccount.code == code,
        )
        .group_by(ChartOfAccount.normal_balance, AccountingEntryLine.side)
    )).all()

    balance = Decimal("0")
    for normal_bal, side, total in row:
        if side == normal_bal:
            balance += total or Decimal("0")
        else:
            balance -= total or Decimal("0")
    return balance


@router.get("/tax-summary")
async def tax_summary(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    income_tax_rate: float = 0.225,
    db: AsyncSession = Depends(get_db),
):
    """
    ملخص الضرائب المستحقة لمصلحة الضرائب:
    - ضريبة القيمة المضافة (صافي مستحق/مسترد للفترة)
    - ضريبة الخصم تحت الحساب المستحقة (رصيد حساب 2320)
    - تقدير ضريبة الدخل على صافي الربح للفترة
    """
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1
    as_of = to_date or date.today()

    vat = await vat_return(from_date, to_date, db)
    income = await income_statement(from_date, to_date, db)

    wht_payable = await _account_balance(db, tid, "2320", as_of)
    net_income = Decimal(str(income["net_income"]))
    income_tax_estimate = max(net_income, Decimal("0")) * Decimal(str(income_tax_rate))

    net_vat_due = Decimal(str(vat["net_vat_due"]))

    return {
        "period": {
            "from_date": str(from_date) if from_date else None,
            "to_date":   str(to_date)   if to_date   else None,
        },
        "vat": {
            "net_vat_due": float(net_vat_due),
            "status_ar": vat["status_ar"],
        },
        "wht_payable": {
            "balance": float(wht_payable),
        },
        "income_tax": {
            "net_income": float(net_income),
            "rate": income_tax_rate,
            "estimate": float(income_tax_estimate),
        },
        "totals": {
            "total_due": float(max(net_vat_due, Decimal("0")) + wht_payable + income_tax_estimate),
        },
    }


# ══════════════════════════════════════════════════════════════
# TAX PAYMENT / PROVISION — سداد ومخصصات الضرائب
# ══════════════════════════════════════════════════════════════

TAX_TYPE_ACCOUNTS = {
    "vat": "2310",
    "wht": "2320",
    "income_tax": "2330",
}


class TaxPaymentIn(BaseModel):
    tax_type: str   # vat | wht | income_tax
    amount: float
    payment_date: Optional[date] = None
    cash_account_code: str = "1110"   # 1110 = صندوق، 1120 = بنك
    notes: Optional[str] = None


class TaxProvisionIn(BaseModel):
    amount: float
    provision_date: Optional[date] = None
    notes: Optional[str] = None


@router.post("/tax-payment")
async def record_tax_payment(data: TaxPaymentIn, db: AsyncSession = Depends(get_db)):
    """تسجيل سداد ضريبة لمصلحة الضرائب: مدين حساب الضريبة المستحقة، دائن الصندوق/البنك"""
    from app.services.auto_journal import _post_entry

    if data.tax_type not in TAX_TYPE_ACCOUNTS:
        raise HTTPException(400, "نوع الضريبة غير صحيح")
    amount = Decimal(str(data.amount))
    if amount <= 0:
        raise HTTPException(400, "المبلغ يجب أن يكون أكبر من صفر")

    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1
    pay_date = data.payment_date or date.today()
    tax_account = TAX_TYPE_ACCOUNTS[data.tax_type]

    await _post_entry(
        db, tid, pay_date,
        data.notes or f"سداد ضريبة ({data.tax_type}) لمصلحة الضرائب",
        f"tax-payment-{data.tax_type}", "tax_payment", 0,
        [
            {"account_code": tax_account, "side": "debit", "amount": amount},
            {"account_code": data.cash_account_code, "side": "credit", "amount": amount},
        ],
    )
    await db.commit()
    return {"ok": True, "tax_type": data.tax_type, "amount": float(amount)}


@router.post("/tax-provision")
async def record_tax_provision(data: TaxProvisionIn, db: AsyncSession = Depends(get_db)):
    """تسجيل مخصص ضريبة الدخل التقديري: مدين مصروف ضريبة الدخل، دائن ضريبة الدخل المستحقة"""
    from app.services.auto_journal import _post_entry

    amount = Decimal(str(data.amount))
    if amount <= 0:
        raise HTTPException(400, "المبلغ يجب أن يكون أكبر من صفر")

    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1
    prov_date = data.provision_date or date.today()

    await _post_entry(
        db, tid, prov_date,
        data.notes or "مخصص ضريبة الدخل التقديري",
        "tax-provision", "tax_provision", 0,
        [
            {"account_code": "5800", "side": "debit", "amount": amount},
            {"account_code": "2330", "side": "credit", "amount": amount},
        ],
    )
    await db.commit()
    return {"ok": True, "amount": float(amount)}
