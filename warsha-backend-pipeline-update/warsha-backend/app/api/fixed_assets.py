"""
الأصول الثابتة — تسجيل الأصول وحساب وترحيل الإهلاك الشهري
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, validator

from app.core.database import get_db, current_tenant_id
from app.api.auth import require_any
from app.models.models import (
    FixedAsset, FixedAssetDepreciation, ChartOfAccount,
    AccountingEntry, AccountingEntryLine,
)

# ── RBAC للكتابة ──────────────────────────────────────────────────────────
# إصلاح ثغرة صلاحيات: أي مستخدم مسجّل (حتى viewer/production) كان يقدر
# يضيف/يعدّل/يحذف/يستبعد أصل ويرحّل إهلاك (قيود محاسبية حقيقية) بطلب API مباشر.
FIXED_ASSET_WRITE_ROLES = ("admin", "accountant")
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


async def require_fixed_asset_write(request: Request, user: dict = Depends(require_any)) -> dict:
    if request.method.upper() not in _READ_METHODS and user.get("role") not in FIXED_ASSET_WRITE_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"صلاحية '{user.get('role')}' غير كافية لتعديل الأصول الثابتة. مطلوب: {list(FIXED_ASSET_WRITE_ROLES)}",
        )
    return user


router = APIRouter(prefix="/fixed-assets", tags=["fixed_assets"],
                   dependencies=[Depends(require_any), Depends(require_fixed_asset_write)])

_VALID_ASSET_TYPES = {"equipment", "vehicle", "building", "furniture", "other"}
_CENT = Decimal("0.01")


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

    @validator("name")
    def _valid_name(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("اسم الأصل مطلوب")
        if len(v) > 200:
            raise ValueError("اسم الأصل لا يزيد عن 200 حرف")
        return v

    @validator("code")
    def _valid_code(cls, v):
        if v is None:
            return v
        v = v.strip()
        if len(v) > 30:
            raise ValueError("كود الأصل لا يزيد عن 30 حرفاً")
        return v or None

    @validator("asset_type")
    def _valid_asset_type(cls, v):
        v = (v or "equipment").strip()
        if v not in _VALID_ASSET_TYPES:
            raise ValueError(f"asset_type يجب أن يكون أحد: {sorted(_VALID_ASSET_TYPES)}")
        return v

    @validator("location")
    def _valid_location(cls, v):
        if v is not None and len(v) > 200:
            raise ValueError("الموقع لا يزيد عن 200 حرف")
        return v

    @validator("cost", "salvage_value")
    def _two_places(cls, v):
        # العمود Numeric(14,2) — نقرّب هنا عشان قيد الشراء يطابق التكلفة المخزّنة
        if v is None:
            return v
        if not v.is_finite():
            raise ValueError("قيمة غير صحيحة")
        v = v.quantize(_CENT, rounding=ROUND_HALF_UP)
        if v >= Decimal("1000000000000"):
            raise ValueError("القيمة أكبر من الحد المسموح")
        return v

    @validator("useful_life_years")
    def _valid_life(cls, v):
        if v is not None and v > 100:
            raise ValueError("العمر الإنتاجي لا يزيد عن 100 سنة")
        return v


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
    disposal_date: Optional[date] = None  # اختياري — الافتراضي اليوم

    @validator("sale_amount")
    def _non_negative(cls, v):
        if not v.is_finite():
            raise ValueError("sale_amount غير صحيح")
        if v < 0:
            raise ValueError("sale_amount لا يمكن أن يكون سالباً")
        return v.quantize(_CENT, rounding=ROUND_HALF_UP)


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
    # إصلاح: التوليد كان بالعدد فقط (count+1) — بعد حذف أصل، الكود الجديد
    # بيتكرر مع كود أصل موجود. نزوّد لحد ما نلاقي كود فاضي.
    count = await db.scalar(
        select(func.count()).select_from(FixedAsset).where(FixedAsset.tenant_id == tid)
    ) or 0
    n = count + 1
    while True:
        code = f"FA-{n:04d}"
        if not await _code_taken(db, tid, code):
            return code
        n += 1


async def _code_taken(db: AsyncSession, tid: int, code: str, exclude_id: Optional[int] = None) -> bool:
    q = select(FixedAsset.id).where(FixedAsset.tenant_id == tid, FixedAsset.code == code)
    if exclude_id is not None:
        q = q.where(FixedAsset.id != exclude_id)
    return (await db.scalar(q.limit(1))) is not None


async def _get_asset(db: AsyncSession, tid: int, asset_id: int) -> FixedAsset:
    """جلب أصل مع تحقق صريح من الـ tenant (دفاع إضافي فوق الفلتر العام)"""
    a = await db.get(FixedAsset, asset_id)
    if not a or a.tenant_id != tid:
        raise HTTPException(404, "الأصل غير موجود")
    return a


async def _get_cash_account(db: AsyncSession, tid: int, code: str, label: str) -> ChartOfAccount:
    """حساب الدفع/الاستلام لازم يكون حساب أصول (صندوق/بنك) أو التزام (مورد/دائن)،
    مش حساب الأصول الثابتة نفسه ولا مجمع الإهلاك ولا حساب إيراد/مصروف."""
    acc = await _get_account(db, tid, (code or "").strip())
    if acc.code in ("1400", "1410") or acc.account_type not in ("asset", "liability"):
        raise HTTPException(400, f"{label} يجب أن يكون حساب نقدية/بنك أو حساب دائن — {acc.code} غير مسموح")
    return acc


async def _original_payment_account_code(db: AsyncSession, tid: int, asset_id: int) -> Optional[str]:
    """كود الحساب الدائن في آخر قيد شراء للأصل (عشان تعديل التكلفة يعكس ويعيد
    القيد على نفس حساب الدفع الأصلي بدل الرجوع الصامت للصندوق 1110)."""
    entry = (await db.execute(
        select(AccountingEntry).where(
            AccountingEntry.tenant_id == tid,
            AccountingEntry.source == "fixed_asset_purchase",
            AccountingEntry.source_id == asset_id,
        ).order_by(AccountingEntry.id.desc())
    )).scalars().first()
    if not entry:
        return None
    line = (await db.execute(
        select(AccountingEntryLine).where(
            AccountingEntryLine.entry_id == entry.id, AccountingEntryLine.side == "credit",
        )
    )).scalars().first()
    if not line:
        return None
    acc = await db.get(ChartOfAccount, line.account_id)
    return acc.code if acc and acc.tenant_id == tid else None


async def _get_account(db: AsyncSession, tid: int, code: str) -> ChartOfAccount:
    res = await db.execute(
        select(ChartOfAccount).where(
            ChartOfAccount.tenant_id == tid, ChartOfAccount.code == code,
            ChartOfAccount.is_active == True,
        )
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
    tid = current_tenant_id.get() or 1
    a = await _get_asset(db, tid, asset_id)
    res = await db.execute(
        select(FixedAssetDepreciation)
        .where(FixedAssetDepreciation.asset_id == asset_id, FixedAssetDepreciation.tenant_id == tid)
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
    if data.purchase_date > date.today():
        raise HTTPException(400, "تاريخ الشراء لا يمكن أن يكون في المستقبل")

    # إصلاح ثغرة: كود الأصل المُدخل يدوياً ماكانش بيتفحص للتكرار
    if data.code and await _code_taken(db, tid, data.code):
        raise HTTPException(400, f"كود الأصل '{data.code}' مستخدم من قبل")
    # نتحقق من الحسابات قبل إنشاء أي سجل
    fixed_asset_acc = await _get_account(db, tid, "1400")
    cash_acc = await _get_cash_account(db, tid, data.payment_account_code, "حساب الدفع")

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
    a = await _get_asset(db, tid, asset_id)
    if data.cost <= 0:
        raise HTTPException(400, "التكلفة يجب أن تكون أكبر من صفر")
    if data.salvage_value < 0 or data.salvage_value >= data.cost:
        raise HTTPException(400, "قيمة الخردة يجب أن تكون أقل من التكلفة")
    if data.useful_life_years <= 0:
        raise HTTPException(400, "العمر الإنتاجي يجب أن يكون أكبر من صفر")
    if data.purchase_date > date.today():
        raise HTTPException(400, "تاريخ الشراء لا يمكن أن يكون في المستقبل")
    if data.code and data.code != a.code and await _code_taken(db, tid, data.code, exclude_id=a.id):
        raise HTTPException(400, f"كود الأصل '{data.code}' مستخدم من قبل")

    financial_change = (
        Decimal(str(data.cost)) != Decimal(str(a.cost or 0))
        or Decimal(str(data.salvage_value)) != Decimal(str(a.salvage_value or 0))
        or int(data.useful_life_years) != int(a.useful_life_years or 0)
    )
    # إصلاح ثغرة تكامل: أصل مستبعد اتقفل قيده (1400/1410 + ربح/خسارة) بالتكلفة
    # القديمة — تعديل تكلفته بعد الاستبعاد كان يعكس قيد الشراء ويعيده بقيمة
    # جديدة فيفضل رصيد 1400 معلّق لأصل مش موجود. البيانات الوصفية بس مسموحة.
    if a.status == "disposed" and financial_change:
        raise HTTPException(400, "الأصل مستبعد — لا يمكن تعديل التكلفة/الخردة/العمر الإنتاجي (يمكن تعديل البيانات الوصفية فقط)")
    # إصلاح ثغرة: تخفيض التكلفة/رفع الخردة لأقل من الإهلاك المُرحَّل فعلاً
    # بيخلي القيمة الدفترية أقل من الخردة (مجمع إهلاك أكبر من القابل للإهلاك)
    accum = Decimal(str(a.accumulated_depreciation or 0))
    if accum > 0 and (data.cost - data.salvage_value) < accum:
        raise HTTPException(
            400,
            f"الإهلاك المُرحَّل ({float(accum):,.2f}) أكبر من القيمة القابلة للإهلاك بعد التعديل — لا يمكن تطبيق التعديل",
        )

    # إصلاح ثغرة تكامل (نفس نمط تعديل فاتورة البيع): تغيير تكلفة الأصل بعد
    # ترحيل قيد الشراء الأصلي كان يسيب القيد القديم واقفاً على القيمة القديمة
    # للأبد بينما FixedAsset.cost يتغيّر — فيختل دليل الحسابات عن سجل الأصول.
    # نعكس قيد الشراء القديم ونسجل قيداً جديداً بالتكلفة الجديدة لو اتغيّرت.
    if Decimal(str(data.cost)) != Decimal(str(a.cost or 0)):
        from app.services.auto_journal import journal_reverse_entry
        # لو المستخدم ماحددش حساب الدفع صراحةً (الواجهة مابتبعتوش في التعديل)،
        # نستخدم نفس حساب الدفع اللي اتسجّل بيه الشراء الأصلي بدل 1110 الافتراضي
        pay_code = data.payment_account_code
        if "payment_account_code" not in data.model_fields_set:
            pay_code = await _original_payment_account_code(db, tid, a.id) or pay_code
        fixed_asset_acc = await _get_account(db, tid, "1400")
        cash_acc = await _get_cash_account(db, tid, pay_code, "حساب الدفع")
        await journal_reverse_entry(db, tid, "fixed_asset_purchase", a.id, "تعديل تكلفة الأصل")
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
    a = await _get_asset(db, tid, asset_id)
    if a.accumulated_depreciation and float(a.accumulated_depreciation) > 0:
        raise HTTPException(400, "لا يمكن حذف أصل له قيود إهلاك مسجلة — يمكن تغيير حالته إلى 'مستبعد' بدلاً من ذلك")
    # إصلاح ثغرة تكامل: حذف أصل مستبعد كان يعكس قيد الشراء بس ويسيب قيد
    # الاستبعاد (إقفال 1400 + ربح/خسارة) يتيم — فيطلع 1400 بالسالب
    if a.status == "disposed":
        raise HTTPException(400, "لا يمكن حذف أصل مستبعد — له قيد استبعاد مرحّل")
    has_dep_log = await db.scalar(
        select(FixedAssetDepreciation.id).where(
            FixedAssetDepreciation.asset_id == asset_id, FixedAssetDepreciation.tenant_id == tid,
        ).limit(1)
    )
    if has_dep_log:
        raise HTTPException(400, "لا يمكن حذف أصل له سجل إهلاك")
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
    a = await _get_asset(db, tid, asset_id)
    if a.status == "disposed":
        raise HTTPException(400, "الأصل مستبعد بالفعل")
    disposal_date = data.disposal_date or date.today()
    if a.purchase_date and disposal_date < a.purchase_date:
        raise HTTPException(400, "تاريخ الاستبعاد لا يمكن أن يكون قبل تاريخ الشراء")
    if disposal_date > date.today():
        raise HTTPException(400, "تاريخ الاستبعاد لا يمكن أن يكون في المستقبل")

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
        cash_acc = await _get_cash_account(db, tid, data.cash_account_code, "حساب استلام العائد")
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
        entry_date=disposal_date,
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
    a = await _get_asset(db, tid, asset_id)
    if a.status != "active":
        raise HTTPException(400, "الأصل مستبعد — لا يمكن تسجيل إهلاك له")
    # إصلاح ثغرة تحقق: كان مسموح ترحيل إهلاك لفترة قبل شراء الأصل أصلاً،
    # أو لفترة مستقبلية لسه ماجتش (يضخّم مصروف الإهلاك ويقلل القيمة الدفترية)
    period_key = (data.period_year, data.period_month)
    if a.purchase_date and period_key < (a.purchase_date.year, a.purchase_date.month):
        raise HTTPException(400, "لا يمكن تسجيل إهلاك لفترة قبل تاريخ شراء الأصل")
    _today = date.today()
    if period_key > (_today.year, _today.month):
        raise HTTPException(400, "لا يمكن تسجيل إهلاك لفترة مستقبلية")

    # تأكد من عدم تكرار الإهلاك لنفس الفترة
    existing = (await db.execute(
        select(FixedAssetDepreciation).where(
            FixedAssetDepreciation.asset_id == asset_id,
            FixedAssetDepreciation.tenant_id == tid,
            FixedAssetDepreciation.period_year == data.period_year,
            FixedAssetDepreciation.period_month == data.period_month,
        )
    )).scalars().first()
    if existing:
        raise HTTPException(400, "تم تسجيل إهلاك هذه الفترة من قبل")

    # حساب بالـ Decimal بدل float (تجنّب أخطاء التقريب التراكمية في مجمع الإهلاك)
    depreciable = Decimal(str(a.cost or 0)) - Decimal(str(a.salvage_value or 0))
    monthly_dep = (depreciable / Decimal(a.useful_life_years * 12)) if a.useful_life_years else Decimal("0")

    remaining = depreciable - Decimal(str(a.accumulated_depreciation or 0))
    amount = min(monthly_dep, remaining).quantize(_CENT, rounding=ROUND_HALF_UP)
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

    a.accumulated_depreciation = Decimal(str(a.accumulated_depreciation or 0)) + amount
    a.last_depreciation_year = data.period_year
    a.last_depreciation_month = data.period_month

    await db.commit()
    await db.refresh(a)
    return _asset_dict(a)
