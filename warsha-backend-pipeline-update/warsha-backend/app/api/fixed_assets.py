"""
الأصول الثابتة — تسجيل الأصول وحساب وترحيل الإهلاك الشهري
"""
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, validator

from app.core.database import get_db, current_tenant_id
from app.api.auth import require_any
from app.models.models import (
    FixedAsset, FixedAssetDepreciation, ChartOfAccount,
    AccountingEntry, AccountingEntryLine,
)

router = APIRouter(prefix="/fixed-assets", tags=["fixed_assets"], dependencies=[Depends(require_any)])


# ════════════════════════════════════════════════════
# Schemas
# ════════════════════════════════════════════════════
class FixedAssetIn(BaseModel):
    code: Optional[str] = None
    name: str
    asset_type: str = "equipment"   # equipment | vehicle | building | furniture | other
    purchase_date: date
    cost: Decimal
    salvage_value: Decimal = Decimal("0")
    useful_life_years: int = 5
    location: Optional[str] = None
    notes: Optional[str] = None
    payment_account_code: str = "1110"   # 1110 = صندوق، 1120 = بنك — الحساب اللي اتدفعت منه تكلفة الشراء


class DepreciationIn(BaseModel):
    period_year: int
    period_month: int

    @validator("period_month")
    def _valid_month(cls, v):
        if not (1 <= v <= 12):
            raise ValueError("period_month يجب أن يكون بين 1 و 12")
        return v

    @validator("period_year")
    def _valid_year(cls, v):
        if not (2000 <= v <= 2100):
            raise ValueError("period_year خارج النطاق المسموح")
        return v


class DisposeAssetIn(BaseModel):
    sale_amount: Decimal = Decimal("0")   # عائد البيع/التخريد (0 = إعدام بدون عائد)
    cash_account_code: str = "1110"       # الحساب اللي هيستلم العائد (صندوق/بنك)

    @validator("sale_amount")
    def _non_negative(cls, v):
        if v < 0:
            raise ValueError("sale_amount لا يمكن أن يكون سالباً")
        return v


# ════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════
def _asset_dict(a: FixedAsset) -> dict:
    book_value = float(a.cost or 0) - float(a.accumulated_depreciation or 0)
    monthly_dep = 0.0
    if a.useful_life_years and a.useful_life_years > 0:
        monthly_dep = (float(a.cost or 0) - float(a.salvage_value or 0)) / (a.useful_life_years * 12)
    return {
        "id": a.id, "code": a.code, "name": a.name, "asset_type": a.asset_type,
        "purchase_date": str(a.purchase_date), "cost": float(a.cost or 0),
        "salvage_value": float(a.salvage_value or 0), "useful_life_years": a.useful_life_years,
        "accumulated_depreciation": float(a.accumulated_depreciation or 0),
        "book_value": book_value, "monthly_depreciation": round(monthly_dep, 2),
        "status": a.status, "location": a.location, "notes": a.notes,
        "last_depreciation_year": a.last_depreciation_year,
        "last_depreciation_month": a.last_depreciation_month,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


async def _next_code(db: AsyncSession, tid: int) -> str:
    res = await db.execute(select(FixedAsset).where(FixedAsset.tenant_id == tid))
    count = len(res.scalars().all())
    return f"FA-{count + 1:04d}"


async def _get_account(db: AsyncSession, tid: int, code: str) -> ChartOfAccount:
    res = await db.execute(
        select(ChartOfAccount).where(ChartOfAccount.tenant_id == tid, ChartOfAccount.code == code)
    )
    acc = res.scalar_one_or_none()
    if not acc:
        raise HTTPException(400, f"حساب {code} غير موجود في دليل الحسابات — رجاء عمل seed لدليل الحسابات أولاً")
    return acc


# ════════════════════════════════════════════════════
# CRUD
# ════════════════════════════════════════════════════
@router.get("")
async def list_assets(status: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    tid = current_tenant_id.get() or 1
    q = select(FixedAsset).where(FixedAsset.tenant_id == tid)
    if status:
        q = q.where(FixedAsset.status == status)
    rows = (await db.execute(q.order_by(FixedAsset.id.desc()))).scalars().all()
    return [_asset_dict(a) for a in rows]


@router.get("/{asset_id}")
async def get_asset(asset_id: int, db: AsyncSession = Depends(get_db)):
    a = await db.get(FixedAsset, asset_id)
    if not a:
        raise HTTPException(404, "الأصل غير موجود")
    res = await db.execute(
        select(FixedAssetDepreciation)
        .where(FixedAssetDepreciation.asset_id == asset_id)
        .order_by(FixedAssetDepreciation.period_year.desc(), FixedAssetDepreciation.period_month.desc())
    )
    deps = res.scalars().all()
    d = _asset_dict(a)
    d["depreciation_log"] = [{
        "id": x.id, "period_year": x.period_year, "period_month": x.period_month,
        "amount": float(x.amount), "entry_id": x.entry_id,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    } for x in deps]
    return d


@router.post("")
async def create_asset(data: FixedAssetIn, db: AsyncSession = Depends(get_db)):
    tid = current_tenant_id.get() or 1
    if data.cost <= 0:
        raise HTTPException(400, "التكلفة يجب أن تكون أكبر من صفر")
    if data.salvage_value < 0 or data.salvage_value >= data.cost:
        raise HTTPException(400, "قيمة الخردة يجب أن تكون أقل من التكلفة")
    if data.useful_life_years <= 0:
        raise HTTPException(400, "العمر الإنتاجي يجب أن يكون أكبر من صفر")

    code = data.code or await _next_code(db, tid)
    asset = FixedAsset(
        tenant_id=tid, code=code, name=data.name, asset_type=data.asset_type,
        purchase_date=data.purchase_date, cost=data.cost, salvage_value=data.salvage_value,
        useful_life_years=data.useful_life_years, location=data.location, notes=data.notes,
    )
    db.add(asset)
    await db.flush()

    # إصلاح ثغرة تكامل: شراء أصل ثابت هو صرف فعلي لفلوس (أو التزام بالسداد) —
    # كان يُسجَّل في FixedAsset فقط من غير أي قيد محاسبي مقابل، فتبقى تكلفة
    # الأصل غير ظاهرة إطلاقاً في دليل الحسابات (لا في 1400 ولا في نقص النقدية).
    # مدين: الأصول الثابتة (1400)   دائن: الصندوق/البنك (بحسب payment_account_code)
    fixed_asset_acc = await _get_account(db, tid, "1400")
    cash_acc = await _get_account(db, tid, data.payment_account_code)
    entry = AccountingEntry(
        tenant_id=tid,
        entry_date=data.purchase_date,
        reference=code,
        description=f"شراء أصل ثابت: {data.name} ({code})",
        source="fixed_asset_purchase",
        source_id=asset.id,
        posted=True,
    )
    db.add(entry)
    await db.flush()
    db.add(AccountingEntryLine(entry_id=entry.id, account_id=fixed_asset_acc.id, side="debit", amount=data.cost))
    db.add(AccountingEntryLine(entry_id=entry.id, account_id=cash_acc.id, side="credit", amount=data.cost))

    await db.commit()
    await db.refresh(asset)
    return _asset_dict(asset)


@router.patch("/{asset_id}")
async def update_asset(asset_id: int, data: FixedAssetIn, db: AsyncSession = Depends(get_db)):
    tid = current_tenant_id.get() or 1
    a = await db.get(FixedAsset, asset_id)
    if not a:
        raise HTTPException(404, "الأصل غير موجود")
    if data.cost <= 0:
        raise HTTPException(400, "التكلفة يجب أن تكون أكبر من صفر")
    if data.salvage_value < 0 or data.salvage_value >= data.cost:
        raise HTTPException(400, "قيمة الخردة يجب أن تكون أقل من التكلفة")
    if data.useful_life_years <= 0:
        raise HTTPException(400, "العمر الإنتاجي يجب أن يكون أكبر من صفر")

    # إصلاح ثغرة تكامل (نفس نمط تعديل فاتورة البيع): تغيير تكلفة الأصل بعد
    # ترحيل قيد الشراء الأصلي كان يسيب القيد القديم واقفاً على القيمة القديمة
    # للأبد بينما FixedAsset.cost يتغيّر — فيختل دليل الحسابات عن سجل الأصول.
    # نعكس قيد الشراء القديم ونسجل قيداً جديداً بالتكلفة الجديدة لو اتغيّرت.
    if data.cost != a.cost:
        from app.services.auto_journal import journal_reverse_entry
        await journal_reverse_entry(db, tid, "fixed_asset_purchase", a.id, "تعديل تكلفة الأصل")
        fixed_asset_acc = await _get_account(db, tid, "1400")
        cash_acc = await _get_account(db, tid, data.payment_account_code)
        entry = AccountingEntry(
            tenant_id=tid,
            entry_date=data.purchase_date,
            reference=data.code or a.code,
            description=f"تعديل تكلفة أصل ثابت: {data.name} ({data.code or a.code})",
            source="fixed_asset_purchase",
            source_id=a.id,
            posted=True,
        )
        db.add(entry)
        await db.flush()
        db.add(AccountingEntryLine(entry_id=entry.id, account_id=fixed_asset_acc.id, side="debit", amount=data.cost))
        db.add(AccountingEntryLine(entry_id=entry.id, account_id=cash_acc.id, side="credit", amount=data.cost))

    a.name = data.name
    a.asset_type = data.asset_type
    a.purchase_date = data.purchase_date
    a.cost = data.cost
    a.salvage_value = data.salvage_value
    a.useful_life_years = data.useful_life_years
    a.location = data.location
    a.notes = data.notes
    if data.code:
        a.code = data.code
    await db.commit()
    await db.refresh(a)
    return _asset_dict(a)


@router.delete("/{asset_id}")
async def delete_asset(asset_id: int, db: AsyncSession = Depends(get_db)):
    tid = current_tenant_id.get() or 1
    a = await db.get(FixedAsset, asset_id)
    if not a:
        raise HTTPException(404, "الأصل غير موجود")
    if a.accumulated_depreciation and float(a.accumulated_depreciation) > 0:
        raise HTTPException(400, "لا يمكن حذف أصل له قيود إهلاك مسجلة — يمكن تغيير حالته إلى 'مستبعد' بدلاً من ذلك")
    # إصلاح ثغرة تكامل: حذف الأصل كان يمسحه من FixedAsset فقط من غير ما يعكس
    # قيد شرائه الأصلي (لو كان مُرحَّلاً) — فيفضل قيد "شراء أصل" يتيم في دليل
    # الحسابات يشير لأصل غير موجود بعد الحذف.
    from app.services.auto_journal import journal_reverse_entry
    await journal_reverse_entry(db, tid, "fixed_asset_purchase", a.id, "حذف الأصل")
    await db.delete(a)
    await db.commit()
    return {"ok": True}


@router.patch("/{asset_id}/dispose")
async def dispose_asset(asset_id: int, data: DisposeAssetIn = DisposeAssetIn(), db: AsyncSession = Depends(get_db)):
    """
    استبعاد/بيع أصل ثابت — يشيله من سجل الأصول النشطة (status=disposed) ويرحّل
    قيد محاسبي حقيقي: إقفال تكلفة الأصل ومجمع إهلاكه + إثبات ربح/خسارة الاستبعاد.

    إصلاح ثغرة تكامل: قبل كده كان الـ endpoint ده بيغيّر status فقط من غير أي
    أثر محاسبي — تكلفة الأصل (1400) ومجمع إهلاكه (1410) كانا يفضلوا واقفين في
    دليل الحسابات للأبد رغم استبعاد الأصل فعلياً، وأي ربح/خسارة من البيع
    (الفرق بين عائد البيع والقيمة الدفترية) كانت تضيع تماماً من غير ترحيل.
    """
    tid = current_tenant_id.get() or 1
    a = await db.get(FixedAsset, asset_id)
    if not a:
        raise HTTPException(404, "الأصل غير موجود")
    if a.status == "disposed":
        raise HTTPException(400, "الأصل مستبعد بالفعل")

    cost = Decimal(str(a.cost or 0))
    accum_dep = Decimal(str(a.accumulated_depreciation or 0))
    book_value = cost - accum_dep
    sale_amount = data.sale_amount
    gain_or_loss = sale_amount - book_value   # موجب = ربح، سالب = خسارة

    fixed_asset_acc = await _get_account(db, tid, "1400")
    accum_dep_acc = await _get_account(db, tid, "1410")

    lines = []
    if accum_dep > 0:
        lines.append((accum_dep_acc.id, "debit", accum_dep))
    if sale_amount > 0:
        cash_acc = await _get_account(db, tid, data.cash_account_code)
        lines.append((cash_acc.id, "debit", sale_amount))
    lines.append((fixed_asset_acc.id, "credit", cost))
    if gain_or_loss > 0:
        # ربح استبعاد — نعيد استخدام حساب إيرادات بيع الهالك (4400) بدل ما نضيف
        # حساباً جديداً لمفهوم مقارب جداً (عائد غير متكرر من التخلص من أصل)
        gain_acc = await _get_account(db, tid, "4400")
        lines.append((gain_acc.id, "credit", gain_or_loss))
    elif gain_or_loss < 0:
        # خسارة استبعاد — نعيد استخدام حساب مصاريف هالك وإتلاف (5600)
        loss_acc = await _get_account(db, tid, "5600")
        lines.append((loss_acc.id, "debit", -gain_or_loss))

    entry = AccountingEntry(
        tenant_id=tid,
        entry_date=date.today(),
        reference=a.code,
        description=f"استبعاد/بيع أصل ثابت: {a.name} ({a.code})",
        source="fixed_asset_disposal",
        source_id=asset_id,
        posted=True,
    )
    db.add(entry)
    await db.flush()
    for acc_id, side, amount in lines:
        db.add(AccountingEntryLine(entry_id=entry.id, account_id=acc_id, side=side, amount=amount))

    a.status = "disposed"
    await db.commit()
    await db.refresh(a)
    return _asset_dict(a)


# ════════════════════════════════════════════════════
# Depreciation (الإهلاك)
# ════════════════════════════════════════════════════
@router.post("/{asset_id}/depreciate")
async def record_depreciation(asset_id: int, data: DepreciationIn, db: AsyncSession = Depends(get_db)):
    """تسجيل إهلاك الفترة (شهر/سنة) وترحيله كقيد محاسبي"""
    tid = current_tenant_id.get() or 1
    a = await db.get(FixedAsset, asset_id)
    if not a:
        raise HTTPException(404, "الأصل غير موجود")
    if a.status != "active":
        raise HTTPException(400, "الأصل مستبعد — لا يمكن تسجيل إهلاك له")

    # تأكد من عدم تكرار الإهلاك لنفس الفترة
    existing = (await db.execute(
        select(FixedAssetDepreciation).where(
            FixedAssetDepreciation.asset_id == asset_id,
            FixedAssetDepreciation.period_year == data.period_year,
            FixedAssetDepreciation.period_month == data.period_month,
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "تم تسجيل إهلاك هذه الفترة من قبل")

    depreciable = float(a.cost or 0) - float(a.salvage_value or 0)
    monthly_dep = depreciable / (a.useful_life_years * 12) if a.useful_life_years else 0

    remaining = depreciable - float(a.accumulated_depreciation or 0)
    amount = round(min(monthly_dep, remaining), 2)
    if amount <= 0:
        raise HTTPException(400, "الأصل مُهلك بالكامل — لا يوجد إهلاك متبقٍ")

    dep_expense_acc = await _get_account(db, tid, "5700")
    accum_dep_acc = await _get_account(db, tid, "1410")

    entry = AccountingEntry(
        tenant_id=tid,
        entry_date=date(data.period_year, data.period_month, 1),
        reference=a.code,
        description=f"إهلاك شهري — {a.name} ({data.period_year}/{data.period_month:02d})",
        source="fixed_asset_depreciation",
        source_id=asset_id,
        posted=True,
        period_year=data.period_year,
        period_month=data.period_month,
    )
    db.add(entry)
    await db.flush()

    db.add(AccountingEntryLine(entry_id=entry.id, account_id=dep_expense_acc.id, side="debit", amount=amount,
                                notes=a.name))
    db.add(AccountingEntryLine(entry_id=entry.id, account_id=accum_dep_acc.id, side="credit", amount=amount,
                                notes=a.name))

    dep_log = FixedAssetDepreciation(
        tenant_id=tid, asset_id=asset_id, period_year=data.period_year, period_month=data.period_month,
        amount=amount, entry_id=entry.id,
    )
    db.add(dep_log)

    a.accumulated_depreciation = Decimal(str(float(a.accumulated_depreciation or 0) + amount))
    a.last_depreciation_year = data.period_year
    a.last_depreciation_month = data.period_month

    await db.commit()
    await db.refresh(a)
    return _asset_dict(a)
