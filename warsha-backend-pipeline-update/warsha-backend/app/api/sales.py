"""
Sales API — عروض الأسعار والفواتير
"""
from datetime import date, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, delete as sa_delete, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional, List

from app.core.database import get_db
from app.api.auth import require_any, get_current_user, require_admin_accountant, require_admin
from app.models.models import (
    Quotation, QuotationLine, Invoice, InvoiceLine, Customer,
    CustomerOrder, CustomerOrderLine, CustomerPayment,
    WorkOrder, Equipment, WorkshopProfile, InvoicePaymentSchedule,
    PaymentScheduleTemplate,
)
from app.services.invoice_due import sync_invoice_due_date
from app.services.approvals import require_approval, to_dec
from app.services.auto_journal import (
    journal_sales_invoice_create, journal_sales_invoice_payment,
    journal_sales_invoice_delete, journal_cogs,
)
from app.services.treasury_link import record_payment_in_treasury
from app.services.analytics import log_event, Events
from app.schemas.schemas import (
    QuotationToOrderRequest, OrderToWorkOrderRequest,
    WorkOrderToInvoiceRequest, OrderAdvancePaymentRequest,
)

from fastapi import Request as _Request

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


async def _block_viewer_writes(request: _Request, user: dict = Depends(require_any)) -> dict:
    """إصلاح ثغرة صلاحيات: كل مسارات المبيعات كانت محمية بـ require_any بس —
    يعني مستخدم "مشاهد" (viewer) يقدر ينشئ/يعدّل/يحذف فواتير ويسجّل دفعات
    بطلب API مباشر رغم إن الواجهة مخبية الأزرار (ROLE_WRITE['viewer'] = [])."""
    if request.method.upper() not in _READ_METHODS and user.get("role") == "viewer":
        raise HTTPException(403, "صلاحية 'viewer' (مشاهد) لا تسمح بالتعديل")
    return user


router = APIRouter(prefix="/sales", tags=["sales"],
                   dependencies=[Depends(require_any), Depends(_block_viewer_writes)])
# Print endpoints — no auth required (opened directly in browser)
print_router = APIRouter(prefix="/sales", tags=["sales_print"], dependencies=[Depends(require_any)])


def _validate_doc_lines(lines, tax_pct=None, discount_pct=None) -> None:
    """تحقق مشترك لبنود المستند (عرض سعر/فاتورة): كمية موجبة، سعر غير سالب،
    ونسب ضريبة/خصم بين 0 و100 — كانت القيم السالبة بتعدّي وتنتج إجمالي سالب
    يتسجّل في رصيد العميل ودفتر الأستاذ."""
    for l in lines or []:
        if l.qty is None or Decimal(str(l.qty)) <= 0:
            raise HTTPException(400, "كمية كل بند يجب أن تكون أكبر من صفر")
        if l.unit_price is None or Decimal(str(l.unit_price)) < 0:
            raise HTTPException(400, "سعر الوحدة لا يمكن أن يكون سالباً")
    for name, v in (("الضريبة", tax_pct), ("الخصم", discount_pct)):
        if v is not None and (Decimal(str(v)) < 0 or Decimal(str(v)) > 100):
            raise HTTPException(400, f"نسبة {name} يجب أن تكون بين 0 و100")


def _logo_to_b64(prof) -> str | None:
    """يُرجع logo_data (base64 data:URI) المخزّن في DB مباشرةً.
    fallback: يقرأ من الديسك لو logo_data فاضية (لوجوهات محمّلة قبل التحديث)."""
    if not prof:
        return None
    if getattr(prof, "logo_data", None):
        return prof.logo_data
    # ── fallback للوجوهات القديمة المخزّنة قبل عمود logo_data ──
    import base64, os, mimetypes
    if not prof.logo_stored_name:
        return None
    logo_dir = os.getenv("ERP_UPLOAD_DIR", "/app/uploads")
    path = os.path.join(logo_dir, "workshop", prof.logo_stored_name)
    try:
        with open(path, "rb") as f:
            data = f.read()
        mime, _ = mimetypes.guess_type(path)
        mime = mime or "image/png"
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except Exception:
        return None


async def _check_credit_limit(db: AsyncSession, customer_id: int, new_amount: Decimal) -> None:
    """
    Helper مشترك: يُطلق HTTPException 400 لو الفاتورة الجديدة ستتجاوز حد الائتمان.
    الصفر يعني غير مفعّل — يُستدعى من كل مسارات إنشاء الفاتورة.
    """
    if not customer_id:
        return
    cust = await db.get(Customer, customer_id)
    if not cust or not cust.credit_limit or cust.credit_limit <= 0:
        return
    new_balance = cust.balance + new_amount
    if new_balance > cust.credit_limit:
        raise HTTPException(
            400,
            f"تجاوز حد الائتمان — رصيد العميل الحالي: {float(cust.balance):,.2f} ج، "
            f"قيمة الفاتورة: {float(new_amount):,.2f} ج، "
            f"المجموع: {float(new_balance):,.2f} ج، "
            f"الحد المسموح: {float(cust.credit_limit):,.2f} ج"
        )


async def get_company_info(db: AsyncSession) -> dict:
    """يجلب بيانات الورشة من قاعدة البيانات مع فلتر tenant صريح لمنع تسرب البيانات."""
    import base64, os
    from sqlalchemy import select
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get()
    q = select(WorkshopProfile)
    if tid is not None:
        q = q.where(WorkshopProfile.tenant_id == tid)
    res = await db.execute(q.limit(1))
    prof = res.scalar_one_or_none()
    if not prof:
        return {
            "name": "ورشة معدات الأعلاف",
            "sub": "تصنيع وتوريد معدات الأعلاف والتصنيع الغذائي",
            "phone": "", "address": "", "email": "", "tax_id": "",
            "commercial_reg": "", "footer": "شكراً لتعاملكم معنا",
            "logo_url": None, "logo_b64": None,
        }
    # رقم التواصل: الموبايل أولاً ثم الأرضي
    phone = prof.mobile or prof.phone or ""
    return {
        "name":           prof.name or "ورشة معدات الأعلاف",
        "sub":            prof.description or "",
        "phone":          phone,
        "address":        prof.address or "",
        "email":          prof.email or "",
        "tax_id":         prof.tax_id or "",
        "commercial_reg": prof.commercial_reg or "",
        "footer":         prof.report_footer or "شكراً لتعاملكم معنا",
        "logo_url":       "/api/v1/workshop/profile/logo" if prof.logo_stored_name else None,
        "logo_b64":       _logo_to_b64(prof),
        "website":        getattr(prof, "website", "") or "",
        "manager":        getattr(prof, "manager_name", "") or "",
        "currency":       getattr(prof, "currency", "") or "جنيه مصري",
        "brand_color":    getattr(prof, "brand_color", "") or "#E87722",
    }


# ── Schemas ────────────────────────────────────────────────────
class LineIn(BaseModel):
    item_type:   Optional[str] = None
    model_no:    Optional[str] = None
    description: str
    qty: Decimal = Decimal("1")
    unit: str = "قطعة"
    unit_price: Decimal
    notes: Optional[str] = None
    sort_order: int = 0
    image_data: Optional[str] = None


class QuotationIn(BaseModel):
    code: str
    customer_id: int
    location_id: Optional[int] = None
    date: date
    valid_until: Optional[date] = None
    tax_pct: Decimal = Decimal("0")
    discount_pct: Decimal = Decimal("0")
    notes: Optional[str] = None
    terms: Optional[str] = None
    lines: List[LineIn] = []


class InvoiceIn(BaseModel):
    code: Optional[str] = None   # فارغ = يُولَّد تلقائياً
    customer_id: int
    location_id: Optional[int] = None
    quotation_id: Optional[int] = None
    work_order_id: Optional[int] = None
    date: date
    due_date: Optional[date] = None
    tax_pct: Decimal = Decimal("0")
    discount_pct: Decimal = Decimal("0")
    notes: Optional[str] = None
    terms: Optional[str] = None
    lines: List[LineIn] = []


class InvoicePaymentIn(BaseModel):
    amount: float
    treasury_id: Optional[int] = None      # الخزنة المستلِمة (اختياري — يستخدم الافتراضية لو فارغ)
    payment_date: Optional[date] = None
    payment_type: Optional[str] = "cash"   # cash | transfer | cheque | bill
    reference: Optional[str] = None        # رقم الشيك / رقم التحويل
    notes: Optional[str] = None


# محطة 4: جدولة أقساط الفاتورة
class InstallmentIn(BaseModel):
    label: Optional[str] = None
    due_date: date
    amount: Optional[Decimal] = None   # مبلغ ثابت — استخدم amount أو pct، مش الاتنين
    pct: Optional[Decimal] = None      # نسبة مئوية من إجمالي الفاتورة (0-100)


class PaymentScheduleIn(BaseModel):
    installments: List[InstallmentIn]


# محطة 7: قالب خطة دفع على عرض السعر/أمر البيع
class TemplateInstallmentIn(BaseModel):
    label: Optional[str] = None
    pct: Decimal                     # نسبة من إجمالي الفاتورة (0-100)
    days_offset: int = 0             # أيام من تاريخ الفاتورة (0 = يوم الفاتورة نفسه)


class PaymentScheduleTemplateIn(BaseModel):
    installments: List[TemplateInstallmentIn]


class InstallmentPatchIn(BaseModel):
    """تعديل جزئي لقسط واحد — كل حقل اختياري."""
    due_date:   Optional[date]    = None
    amount_due: Optional[Decimal] = None
    label:      Optional[str]     = None


async def _next_invoice_code(db: AsyncSession) -> str:
    from sqlalchemy import func as _f
    count = await db.scalar(select(_f.count()).select_from(Invoice))
    n = (count or 0) + 1
    while True:
        code = f"INV-{n:04d}"
        exists = await db.scalar(select(Invoice.id).where(Invoice.code == code))
        if not exists:
            return code
        n += 1


def calc_totals(lines, tax_pct, discount_pct):
    subtotal = sum(Decimal(str(l.qty)) * Decimal(str(l.unit_price)) for l in lines)
    discount_amt = subtotal * Decimal(str(discount_pct)) / 100
    taxable = subtotal - discount_amt
    tax_amount = taxable * Decimal(str(tax_pct)) / 100
    total = taxable + tax_amount
    return subtotal, discount_amt, tax_amount, total


def quot_dict(q: Quotation):
    sm = {"draft": "مسودة", "sent": "مُرسل", "accepted": "مقبول", "rejected": "مرفوض"}
    return {
        "id": q.id, "code": q.code,
        "customer_id": q.customer_id,
        "customer_name": q.customer.name if q.customer else "",
        "customer_phone": q.customer.phone if q.customer else "",
        "customer_address": q.customer.address if q.customer else "",
        "customer_tax_id": q.customer.tax_id if q.customer else "",
        "location_id": q.location_id,
        "location_name": q.location.name if getattr(q, "location", None) else None,
        "date": str(q.date), "valid_until": str(q.valid_until) if q.valid_until else None,
        "status": q.status, "status_ar": sm.get(q.status, q.status),
        "subtotal": float(q.subtotal), "tax_pct": float(q.tax_pct),
        "tax_amount": float(q.tax_amount), "discount_pct": float(q.discount_pct),
        "discount_amt": float(q.discount_amt), "total": float(q.total),
        "notes": q.notes, "terms": q.terms,
        "created_at": q.created_at.isoformat() if q.created_at else None,
        "lines": [{"id": l.id, "description": l.description, "qty": float(l.qty),
                   "unit": l.unit, "unit_price": float(l.unit_price),
                   "total_price": float(l.total_price), "notes": l.notes,
                   "sort_order": l.sort_order,
                   "item_type": getattr(l, 'item_type', None) or '',
                   "model_no":  getattr(l, 'model_no',  None) or '',
                   "image_data": getattr(l, 'image_data', None) or ''} for l in q.lines],
    }


def inv_dict(inv: Invoice, has_schedule: Optional[bool] = None):
    sm = {"unpaid": "غير مدفوعة", "partial": "مدفوعة جزئياً", "paid": "مدفوعة", "overdue": "متأخرة"}
    remaining = float(inv.total) - float(inv.paid_amount)
    # احسب overdue ديناميكياً — لا تغيّر DB بل أضفها للعرض فقط
    # الفاتورة المدفوعة جزئياً لا تتحول لـ overdue في العرض — لها أولوية
    effective_status = inv.status
    if (
        inv.status not in ("paid", "cancelled", "partial")
        and inv.due_date
        and inv.due_date < date.today()
        and float(inv.paid_amount) == 0
    ):
        effective_status = "overdue"
    d = {
        "id": inv.id, "code": inv.code,
        "quotation_id": inv.quotation_id,
        "customer_order_id": getattr(inv, "customer_order_id", None),
        "work_order_id": getattr(inv, "work_order_id", None),
        "customer_id": inv.customer_id,
        "customer_name": inv.customer.name if inv.customer else "",
        "customer_phone": inv.customer.phone if inv.customer else "",
        "customer_address": inv.customer.address if inv.customer else "",
        "customer_tax_id": inv.customer.tax_id if inv.customer else "",
        "date": str(inv.date), "due_date": str(inv.due_date) if inv.due_date else None,
        "status": effective_status, "status_ar": sm.get(effective_status, effective_status),
        "subtotal": float(inv.subtotal), "tax_pct": float(inv.tax_pct),
        "tax_amount": float(inv.tax_amount), "discount_pct": float(inv.discount_pct),
        "discount_amt": float(inv.discount_amt), "total": float(inv.total),
        "paid_amount": float(inv.paid_amount), "remaining": remaining,
        "notes": inv.notes, "terms": inv.terms,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "lines": [{"id": l.id,
                   "item_type":   getattr(l, "item_type", None) or "",
                   "model_no":    getattr(l, "model_no",  None) or "",
                   "description": l.description, "qty": float(l.qty),
                   "unit": l.unit, "unit_price": float(l.unit_price),
                   "total_price": float(l.total_price), "notes": l.notes,
                   "image_data": getattr(l, "image_data", None) or ""} for l in inv.lines],
    }
    if has_schedule is not None:
        # تحسين UX (محطة 4/6): تعرف من قائمة الفواتير مباشرة لو فيه خطة
        # أقساط من غير ما تحتاج تفتح كل فاتورة لوحدها عشان تعرف
        d["has_payment_schedule"] = has_schedule
    return d


# ══ QUOTATIONS ═══════════════════════════════════════════════

@router.get("/quotations")
async def list_quotations(db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(Quotation).options(selectinload(Quotation.customer), selectinload(Quotation.lines), selectinload(Quotation.location))
        .order_by(Quotation.created_at.desc())
    )
    return [quot_dict(q) for q in r.scalars().all()]


@router.post("/quotations", status_code=201)
async def create_quotation(data: QuotationIn, db: AsyncSession = Depends(get_db)):
    if not data.lines:
        raise HTTPException(400, "أضف بنداً واحداً على الأقل")
    _validate_doc_lines(data.lines, data.tax_pct, data.discount_pct)
    if not (data.code or "").strip():
        raise HTTPException(400, "رقم عرض السعر مطلوب")
    if await db.scalar(select(Quotation.id).where(Quotation.code == data.code)):
        raise HTTPException(400, f"رقم عرض السعر '{data.code}' مستخدم بالفعل")
    if data.date and data.valid_until and data.valid_until < data.date:
        raise HTTPException(400, "تاريخ صلاحية العرض يجب أن يكون بعد تاريخ الإصدار")
    # إصلاح ثغرة تحقق: كان ممكن تتبعت quotation لعميل رقمه مش موجود أصلاً
    # (أو تابع لـ tenant تاني — with_loader_criteria بيرجّعه None بصمت)، فيتولد
    # عرض سعر بمرجع عميل يتيم/عابر للمستأجرين من غير أي خطأ للمستخدم.
    if not await db.get(Customer, data.customer_id):
        raise HTTPException(404, "العميل غير موجود")
    from app.core.database import current_tenant_id as _ctid_cq
    _tid_cq = _ctid_cq.get() or 1
    subtotal, disc_amt, tax_amt, total = calc_totals(data.lines, data.tax_pct, data.discount_pct)
    q = Quotation(
        tenant_id=_tid_cq,
        code=data.code, customer_id=data.customer_id, location_id=data.location_id, date=data.date,
        valid_until=data.valid_until, tax_pct=data.tax_pct, discount_pct=data.discount_pct,
        subtotal=subtotal, discount_amt=disc_amt, tax_amount=tax_amt, total=total,
        notes=data.notes, terms=data.terms,
    )
    db.add(q)
    await db.flush()
    for i, l in enumerate(data.lines):
        tp = Decimal(str(l.qty)) * Decimal(str(l.unit_price))
        db.add(QuotationLine(tenant_id=_tid_cq, quotation_id=q.id,
            item_type=l.item_type, model_no=l.model_no,
            description=l.description, qty=l.qty, unit=l.unit,
            unit_price=l.unit_price, total_price=tp,
            notes=l.notes, sort_order=l.sort_order or i,
            image_data=l.image_data))
    await db.commit()
    await db.refresh(q)
    await log_event(Events.QUOTATION_CREATED, tenant_id=_tid_cq, entity_type="quotation",
                     entity_id=q.id, metadata={"total": float(q.total)}, source_path="/api/v1/sales/quotations")
    return {"id": q.id, "code": q.code, "total": float(q.total)}


@router.get("/quotations/{qid}")
async def get_quotation(qid: int, db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(Quotation).options(selectinload(Quotation.customer), selectinload(Quotation.lines), selectinload(Quotation.location))
        .where(Quotation.id == qid)
    )
    q = r.scalar_one_or_none()
    if not q: raise HTTPException(404)
    return quot_dict(q)


@router.put("/quotations/{qid}")
async def update_quotation(qid: int, data: QuotationIn, db: AsyncSession = Depends(get_db)):
    """تحديث عرض السعر كاملاً — البنود + الإجماليات"""
    r = await db.execute(
        select(Quotation).options(selectinload(Quotation.lines)).where(Quotation.id == qid)
    )
    q = r.scalar_one_or_none()
    if not q:
        raise HTTPException(404, "عرض السعر غير موجود")
    if not data.lines:
        raise HTTPException(400, "أضف بنداً واحداً على الأقل")
    if data.date and data.valid_until and data.valid_until < data.date:
        raise HTTPException(400, "تاريخ صلاحية العرض يجب أن يكون بعد تاريخ الإصدار")
    _validate_doc_lines(data.lines, data.tax_pct, data.discount_pct)
    # نفس فحص create_quotation: عميل غير موجود/تابع لـ tenant تاني كان بيتقبل بصمت هنا
    if not await db.get(Customer, data.customer_id):
        raise HTTPException(404, "العميل غير موجود")
    new_code = (data.code or "").strip() or q.code
    if new_code != q.code and await db.scalar(
        select(Quotation.id).where(Quotation.code == new_code, Quotation.id != qid)
    ):
        raise HTTPException(400, f"رقم عرض السعر '{new_code}' مستخدم بالفعل")

    subtotal, disc_amt, tax_amt, total = calc_totals(data.lines, data.tax_pct, data.discount_pct)

    q.code          = new_code
    q.customer_id   = data.customer_id
    q.location_id   = data.location_id
    q.date          = data.date
    q.valid_until   = data.valid_until
    q.tax_pct       = data.tax_pct
    q.discount_pct  = data.discount_pct
    q.subtotal      = subtotal
    q.discount_amt  = disc_amt
    q.tax_amount    = tax_amt
    q.total         = total
    q.notes         = data.notes
    q.terms         = data.terms

    for old_line in q.lines:
        await db.delete(old_line)
    await db.flush()

    from app.core.database import current_tenant_id as _ctid_uq
    _tid_uq = _ctid_uq.get() or 1
    for i, l in enumerate(data.lines):
        tp = Decimal(str(l.qty)) * Decimal(str(l.unit_price))
        db.add(QuotationLine(
            tenant_id=_tid_uq,
            quotation_id=q.id,
            item_type=l.item_type, model_no=l.model_no,
            description=l.description, qty=l.qty, unit=l.unit,
            unit_price=l.unit_price, total_price=tp,
            notes=l.notes, sort_order=l.sort_order or i,
            image_data=l.image_data,
        ))

    await db.commit()
    return {"id": q.id, "code": q.code, "total": float(q.total)}


VALID_QUOTATION_STATUSES = {"draft", "sent", "accepted", "rejected"}

@router.put("/quotations/{qid}/status")
async def update_quotation_status(qid: int, status: str, db: AsyncSession = Depends(get_db)):
    if status not in VALID_QUOTATION_STATUSES:
        raise HTTPException(400, f"حالة غير صالحة. القيم المسموحة: {sorted(VALID_QUOTATION_STATUSES)}")
    q = await db.get(Quotation, qid)
    if not q: raise HTTPException(404)
    q.status = status
    await db.commit()
    return {"id": q.id, "status": q.status}


@router.delete("/quotations/{qid}")
async def delete_quotation(qid: int, db: AsyncSession = Depends(get_db)):
    q = await db.get(Quotation, qid)
    if not q: raise HTTPException(404)
    # إصلاح ثغرة تكامل: حذف عرض سعر اتحوّل لفاتورة/طلب كان بيسيب مرجع يتيم
    # (quotation_id) في الفاتورة/الطلب — ويكسر الـ FK على Postgres.
    if await db.scalar(select(Invoice.id).where(Invoice.quotation_id == qid).limit(1)):
        raise HTTPException(400, "لا يمكن حذف عرض سعر تم تحويله لفاتورة")
    if await db.scalar(select(CustomerOrder.id).where(CustomerOrder.quotation_id == qid).limit(1)):
        raise HTTPException(400, "لا يمكن حذف عرض سعر تم تحويله لطلب عميل")
    lines = (await db.execute(select(QuotationLine).where(QuotationLine.quotation_id==qid))).scalars().all()
    for l in lines: await db.delete(l)
    await db.execute(sa_delete(PaymentScheduleTemplate).where(
        PaymentScheduleTemplate.source_type == "quotation", PaymentScheduleTemplate.source_id == qid))
    await db.delete(q)
    await db.commit()
    return {"message": "تم الحذف"}


@router.post("/quotations/{qid}/payment-schedule-template", status_code=201)
async def create_quotation_payment_template(
    qid: int, data: PaymentScheduleTemplateIn, replace: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """محطة 7: تعريف قالب خطة دفع على عرض السعر — نسب %  + إزاحة أيام من تاريخ الفاتورة المستقبلية."""
    q = await db.get(Quotation, qid)
    if not q: raise HTTPException(404, "عرض السعر غير موجود")
    from app.core.database import current_tenant_id as _ctid_qt
    rows = await _save_payment_schedule_template(
        db, "quotation", qid, _ctid_qt.get() or 1, data.installments, replace)
    return [template_dict(r) for r in rows]


@router.get("/quotations/{qid}/payment-schedule-template")
async def get_quotation_payment_template(qid: int, db: AsyncSession = Depends(get_db)):
    q = await db.get(Quotation, qid)
    if not q: raise HTTPException(404, "عرض السعر غير موجود")
    rows = (await db.execute(
        select(PaymentScheduleTemplate)
        .where(PaymentScheduleTemplate.source_type == "quotation", PaymentScheduleTemplate.source_id == qid)
        .order_by(PaymentScheduleTemplate.installment_no)
    )).scalars().all()
    return [template_dict(r) for r in rows]


@router.post("/quotations/{qid}/to-invoice")
async def quotation_to_invoice(qid: int, invoice_code: str, db: AsyncSession = Depends(get_db),
                                current_user: dict = Depends(get_current_user)):
    """تحويل عرض سعر إلى فاتورة"""
    from app.core.database import current_tenant_id as _ctid_q2i
    _tid_q2i = _ctid_q2i.get() or 1
    r = await db.execute(
        select(Quotation).options(selectinload(Quotation.lines)).where(Quotation.id == qid)
    )
    q = r.scalar_one_or_none()
    if not q: raise HTTPException(404)
    if q.status == "rejected":
        raise HTTPException(400, "لا يمكن تحويل عرض مرفوض")
    invoice_code = (invoice_code or "").strip() or await _next_invoice_code(db)
    # إصلاح ثغرة تكامل: نفس عرض السعر كان ممكن يتحوّل لفاتورة أكتر من مرة
    # (أو يتحوّل لفاتورة بعد ما اتحوّل لطلب عميل هيتفوتر هو كمان) — فوترة
    # مزدوجة لنفس البيع في رصيد العميل ودفتر الأستاذ.
    prev_inv = await db.scalar(select(Invoice.code).where(Invoice.quotation_id == qid).limit(1))
    if prev_inv:
        raise HTTPException(400, f"تم تحويل هذا العرض لفاتورة مسبقاً — رقم الفاتورة: {prev_inv}")
    prev_order = await db.scalar(select(CustomerOrder.code).where(CustomerOrder.quotation_id == qid).limit(1))
    if prev_order:
        raise HTTPException(400, f"تم تحويل هذا العرض لطلب عميل مسبقاً — رقم الطلب: {prev_order}")
    # تحقق من عدم تكرار الكود
    existing = await db.scalar(select(Invoice.id).where(Invoice.code == invoice_code))
    if existing:
        raise HTTPException(400, f"رقم الفاتورة '{invoice_code}' مستخدم بالفعل")
    # تحقق حد الائتمان
    await _check_credit_limit(db, q.customer_id, q.total)
    # دورة الموافقات: خصم على الفاتورة (نفس قاعدة create_invoice حتى لا يُتحايل عبر التحويل)
    _q_pct, _q_amt, _q_total = to_dec(q.discount_pct), to_dec(q.discount_amt), to_dec(q.total)
    if _q_pct > 0 or _q_amt > 0:
        await require_approval(
            db, current_user, "invoice_discount",
            entity_type="quotation", entity_id=qid, entity_label=str(q.code),
            metrics={"percent": _q_pct, "amount": _q_amt},
            params={"quotation_id": qid, "discount_pct": _q_pct, "total": _q_total},
        )
    inv = Invoice(
        tenant_id=_tid_q2i,
        code=invoice_code, customer_id=q.customer_id, location_id=q.location_id, quotation_id=qid,
        date=date.today(), tax_pct=q.tax_pct, discount_pct=q.discount_pct,
        subtotal=q.subtotal, discount_amt=q.discount_amt,
        tax_amount=q.tax_amount, total=q.total,
        notes=q.notes, terms=q.terms,
    )
    db.add(inv)
    await db.flush()
    for l in q.lines:
        db.add(InvoiceLine(tenant_id=_tid_q2i, invoice_id=inv.id,
            item_type=getattr(l, "item_type", None),
            model_no=getattr(l, "model_no", None),
            description=l.description,
            qty=l.qty, unit=l.unit, unit_price=l.unit_price,
            total_price=l.total_price, notes=l.notes, sort_order=l.sort_order))
    q.status = "accepted"

    # إصلاح ثغرة تكامل (رصدها فريق المنصة/الإدارة): هذا المسار كان الوحيد بين
    # كل مسارات إنشاء الفاتورة (create_invoice، work_order_to_invoice) اللي
    # مابيسجّلش قيد محاسبي للإيراد (journal_sales_invoice_create) ولا يحدّث
    # رصيد العميل — فيفضل البيع عبر تحويل عرض سعر → فاتورة غير مرئي تمامًا
    # في دفتر الأستاذ (حساب 4100) وفي رصيد العميل (AR) رغم إن الفاتورة
    # فعليًا موجودة ومرئية في شاشات الفواتير.
    cust = await db.get(Customer, q.customer_id)
    if cust:
        cust.balance = cust.balance + inv.total
    await journal_sales_invoice_create(db, _tid_q2i, inv.id, inv.code, inv.tax_amount, inv.total, inv.date)

    # محطة 7: لو عرض السعر عنده قالب خطة دفع، طبّقه على الفاتورة الجديدة تلقائيًا
    await _materialize_payment_schedule_from_template(db, "quotation", qid, inv, _tid_q2i)
    await db.commit()
    return {"id": inv.id, "code": inv.code, "total": float(inv.total)}


# ══ INVOICES ══════════════════════════════════════════════════

@router.get("/invoices")
async def list_invoices(db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(Invoice).options(selectinload(Invoice.customer), selectinload(Invoice.lines))
        .order_by(Invoice.created_at.desc())
    )
    invoices = r.scalars().all()
    # استعلام واحد بس لكل الفواتير (مش N+1) عشان نعرف مين عنده خطة أقساط
    sched_ids = set((await db.execute(
        select(InvoicePaymentSchedule.invoice_id).distinct()
    )).scalars().all())
    return [inv_dict(inv, has_schedule=(inv.id in sched_ids)) for inv in invoices]


@router.get("/invoices/next-code")
async def get_next_invoice_code_ep(db: AsyncSession = Depends(get_db)):
    return {"code": await _next_invoice_code(db)}


async def _issue_fg_for_wo_invoice(db: AsyncSession, tid: int, wo: WorkOrder, inv: Invoice, qty: Decimal) -> None:
    """خصم المعدة من مخزون تام الصنع (FG-{wo.code}) + قيد COGS لفاتورة أمر إنتاج.

    مشترك بين /work-orders/{id}/to-invoice وبين /invoices لما يتبعت
    work_order_id (زرار "إنشاء فاتورة" في شاشة أمر الإنتاج بيستخدم التاني) —
    قبل كده المسار التاني كان بيسجل الإيراد بس: المعدة تفضل في FG للأبد
    ومفيش تكلفة بضاعة مباعة، فالربح بيطلع أعلى من الحقيقة بقيمة المعدة كلها.
    """
    from app.models.models import Material as _FGMat
    from app.services.inventory_ledger import get_or_create_warehouse_bin as _get_fg_bin, post_issue as _post_fg_issue
    fg_mat = (await db.execute(
        select(_FGMat).where(_FGMat.tenant_id == tid, _FGMat.code == f"FG-{wo.code}")
    )).scalar_one_or_none()
    if not fg_mat:
        raise HTTPException(
            400,
            f"لا يمكن إصدار فاتورة لأمر الإنتاج {wo.code} — المعدة لسه مش في مخزون تام الصنع. "
            f"أنهِ أمر الإنتاج (الحالة: منتهي) الأول.",
        )
    # لو المعدة اتسلّمت بالفعل بأمر تسليم (وخرجت من FG هناك) منخصمهاش تاني
    from app.services.inventory_ledger import fg_qty_to_release
    qty = await fg_qty_to_release(db, wo, fg_mat, extra_invoiced=qty)
    if qty <= 0:
        return
    if (fg_mat.stock_qty or Decimal("0")) < qty:
        raise HTTPException(
            400,
            f"لا يمكن إصدار فاتورة لأمر الإنتاج {wo.code} — رصيد مخزون تام الصنع غير كافٍ "
            f"(المتاح {fg_mat.stock_qty}، المطلوب {qty}). أنهِ أمر الإنتاج الأول.",
        )
    fg_unit_cost = fg_mat.unit_cost or Decimal("0")
    fg_bin = await _get_fg_bin(db, tid, "FG")
    await _post_fg_issue(
        db, material=fg_mat, qty=qty, unit_cost=fg_unit_cost,
        source_doc_type="sales_invoice", source_doc_id=inv.id,
        source_location_id=fg_bin.id,
        reference=inv.code, notes=f"فاتورة مبيعات من أمر الإنتاج — {inv.code}",
    )
    await journal_cogs(db, tid, inv.id, inv.code, qty * fg_unit_cost, date.today())


@router.post("/invoices", status_code=201)
async def create_invoice(data: InvoiceIn, db: AsyncSession = Depends(get_db),
                         current_user: dict = Depends(get_current_user)):
    if not data.lines:
        raise HTTPException(400, "أضف بنداً واحداً على الأقل")
    # إصلاح ثغرة تحقق: إنشاء فاتورة لعميل رقمه غير موجود (أو تابع لـ tenant
    # تاني) كان ينجح بصمت — الفاتورة تتسجل بمرجع عميل يتيم، ورصيد العميل
    # (وحد الائتمان) لا يتحدث لأنه ببساطة مش موجود، من غير ما ينتبه المستخدم.
    if not await db.get(Customer, data.customer_id):
        raise HTTPException(404, "العميل غير موجود")
    _validate_doc_lines(data.lines, data.tax_pct, data.discount_pct)
    from app.core.database import current_tenant_id as _ctid
    _tid = _ctid.get() or 1
    code = (data.code or '').strip() or await _next_invoice_code(db)
    if data.quotation_id is not None and not await db.get(Quotation, data.quotation_id):
        raise HTTPException(404, "عرض السعر المرجعي غير موجود")
    if data.work_order_id is not None:
        # إصلاح ثغرة تكامل: ربط الفاتورة بأمر إنتاج غير موجود/تابع لـ tenant
        # تاني، أو فوترة نفس أمر الإنتاج مرتين (نفس فحص work_order_to_invoice)
        if not await db.get(WorkOrder, data.work_order_id):
            raise HTTPException(404, "أمر الإنتاج المرتبط غير موجود")
        dup_wo_inv = await db.scalar(select(Invoice.id).where(Invoice.work_order_id == data.work_order_id))
        if dup_wo_inv:
            raise HTTPException(400, f"تم إصدار فاتورة لأمر الإنتاج ده مسبقًا — رقم الفاتورة: {dup_wo_inv}")
    subtotal, disc_amt, tax_amt, total = calc_totals(data.lines, data.tax_pct, data.discount_pct)

    # ── تحقق حد الائتمان (credit_limit) ──
    await _check_credit_limit(db, data.customer_id, total)
    # إصلاح ثغرة تحقق: رقم فاتورة مكرر كان بيوصل لقيد UNIQUE في الداتابيز
    # فيرجع 500 بدل رسالة واضحة
    if await db.scalar(select(Invoice.id).where(Invoice.code == code)):
        raise HTTPException(400, f"رقم الفاتورة '{code}' مستخدم بالفعل")
    # دورة الموافقات: خصم على الفاتورة فوق حد (نسبة/قيمة). بدون خصم لا يُستدعى أصلاً.
    _c_pct, _c_amt = to_dec(data.discount_pct), to_dec(disc_amt)
    if _c_pct > 0 or _c_amt > 0:
        await require_approval(
            db, current_user, "invoice_discount",
            entity_type="invoice", entity_id=None, entity_label=str(code),
            metrics={"percent": _c_pct, "amount": _c_amt},
            params={"customer_id": data.customer_id, "discount_pct": _c_pct,
                    "subtotal": to_dec(subtotal), "tax_pct": to_dec(data.tax_pct),
                    "work_order_id": data.work_order_id, "quotation_id": data.quotation_id},
        )
    inv = Invoice(
        tenant_id=_tid,
        code=code, customer_id=data.customer_id, quotation_id=data.quotation_id,
        work_order_id=data.work_order_id,
        location_id=data.location_id,
        date=data.date, due_date=data.due_date, tax_pct=data.tax_pct,
        discount_pct=data.discount_pct, subtotal=subtotal,
        discount_amt=disc_amt, tax_amount=tax_amt, total=total,
        notes=data.notes, terms=data.terms,
    )
    db.add(inv)
    await db.flush()
    for i, l in enumerate(data.lines):
        tp = Decimal(str(l.qty)) * Decimal(str(l.unit_price))
        db.add(InvoiceLine(tenant_id=_tid, invoice_id=inv.id,
            item_type=getattr(l, "item_type", None),
            model_no=getattr(l, "model_no", None),
            description=l.description,
            qty=l.qty, unit=l.unit, unit_price=l.unit_price,
            total_price=tp, notes=l.notes, sort_order=l.sort_order or i))
    # رصيد العميل: موجب = مديون (العميل عليه مبلغ)
    cust = await db.get(Customer, data.customer_id)
    if cust:
        cust.balance = cust.balance + total
    await journal_sales_invoice_create(db, _tid, inv.id, inv.code, tax_amt, total, data.date)
    if data.work_order_id is not None:
        _wo_inv = await db.get(WorkOrder, data.work_order_id)
        await _issue_fg_for_wo_invoice(
            db, _tid, _wo_inv, inv, _wo_inv.quantity if _wo_inv.quantity else Decimal("1"))
    await db.commit()
    await db.refresh(inv)
    await log_event(Events.INVOICE_CREATED, tenant_id=_tid, entity_type="invoice",
                     entity_id=inv.id, metadata={"total": float(inv.total)}, source_path="/api/v1/sales/invoices")
    return {"id": inv.id, "code": inv.code, "total": float(inv.total)}


@router.get("/invoices/{iid}")
async def get_invoice(iid: int, db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(Invoice).options(selectinload(Invoice.customer), selectinload(Invoice.lines))
        .where(Invoice.id == iid)
    )
    inv = r.scalar_one_or_none()
    if not inv: raise HTTPException(404)
    return inv_dict(inv)


@router.put("/invoices/{iid}")
async def update_invoice(iid: int, data: InvoiceIn, db: AsyncSession = Depends(get_db),
                         current_user: dict = Depends(get_current_user)):
    inv = await db.get(Invoice, iid)
    if not inv: raise HTTPException(404)
    if not data.lines:
        raise HTTPException(400, "أضف بنداً واحداً على الأقل")
    _validate_doc_lines(data.lines, data.tax_pct, data.discount_pct)
    # إصلاح: code اختياري في InvoiceIn — التعديل من غير code كان بيكتب NULL
    # على رقم الفاتورة. نحافظ على الرقم الحالي، ونمنع التكرار لو اتغيّر.
    new_code = (data.code or "").strip() or inv.code
    if new_code != inv.code and await db.scalar(
        select(Invoice.id).where(Invoice.code == new_code, Invoice.id != iid)
    ):
        raise HTTPException(400, f"رقم الفاتورة '{new_code}' مستخدم بالفعل")
    # إصلاح ثغرة تحقق: نقل الفاتورة لعميل رقمه غير موجود (أو تابع لـ tenant
    # تاني) كان ينجح بصمت — الفاتورة تتحول لمرجع عميل يتيم ورصيده الجديد
    # لا يتحدث لأنه ببساطة مش موجود.
    if not await db.get(Customer, data.customer_id):
        raise HTTPException(404, "العميل غير موجود")

    # أعد حساب الإجماليات الجديدة الأول (من غير أي تعديل فعلي بعد) عشان نتحقق
    # من عدم تعارضها مع خطة أقساط موجودة قبل ما نلمس أي حاجة في الداتابيز
    subtotal, disc_amt, tax_amt, new_total = calc_totals(data.lines, data.tax_pct, data.discount_pct)

    # إصلاح ثغرة تحقق: تخفيض إجمالي الفاتورة لأقل من المبلغ المسدد فعلاً كان
    # بيخليها "مدفوعة" بمبلغ مسدد أكبر من إجماليها (دفع زائد مخفي)
    if new_total < (inv.paid_amount or Decimal("0")):
        raise HTTPException(
            400,
            f"الإجمالي الجديد ({float(new_total):,.2f}) أقل من المبلغ المسدد على الفاتورة "
            f"({float(inv.paid_amount):,.2f})",
        )
    # نقل فاتورة عليها دفعات لعميل تاني كان بيسيب الدفعات مسجّلة على العميل القديم
    if data.customer_id != inv.customer_id and (inv.paid_amount or 0) > 0:
        raise HTTPException(400, "لا يمكن نقل فاتورة عليها دفعات مسجّلة لعميل آخر")

    # إصلاح ثغرة تكامل (بعد تحليل المحطات 1-7): لو الفاتورة عندها خطة أقساط
    # بالفعل، وتعديل البنود هيغيّر الإجمالي، امنع التعديل بدل ما نسيب الخطة
    # القديمة تفضل ظاهرة بمجموع غلط من غير أي تحذير للمستخدم.
    schedule_total = await db.scalar(
        select(func.sum(InvoicePaymentSchedule.amount_due))
        .where(InvoicePaymentSchedule.invoice_id == iid)
    )
    if schedule_total is not None and abs(Decimal(str(schedule_total)) - new_total) > Decimal("0.02"):
        raise HTTPException(
            400,
            f"لا يمكن تعديل بنود الفاتورة — الإجمالي الجديد ({float(new_total):,.2f}) "
            f"يختلف عن مجموع خطة الأقساط الحالية ({float(schedule_total):,.2f}). "
            f"عدّل خطة الأقساط أولاً (أو احذفها/استبدلها) ثم عدّل الفاتورة.",
        )

    # دورة الموافقات: رفع/تغيير الخصم (الجديد > 0 ويختلف عن المخزَّن). تعديل البنود/الملاحظات
    # من غير تغيير الخصم لا يحتاج موافقة. قبل أول تعديل (رصيد العميل تحته).
    _u_pct, _u_amt = to_dec(data.discount_pct), to_dec(disc_amt)
    if (_u_pct > 0 or _u_amt > 0) and (
        _u_pct != to_dec(inv.discount_pct) or _u_amt != to_dec(inv.discount_amt)
    ):
        await require_approval(
            db, current_user, "invoice_discount",
            entity_type="invoice", entity_id=iid, entity_label=str(inv.code),
            metrics={"percent": _u_pct, "amount": _u_amt},
            params={"invoice_id": iid, "discount_pct": _u_pct, "subtotal": to_dec(subtotal)},
        )

    # رصيد العميل القديم: نرجّع الإجمالي الكامل لأنه خُصم كاملاً عند الإنشاء
    # (الدفعات المسجّلة أضافت للرصيد بشكل منفصل فلا نحتاج معالجتها هنا)
    old_cust = await db.get(Customer, inv.customer_id)
    if old_cust:
        old_cust.balance = old_cust.balance - inv.total

    # إصلاح ثغرة تكامل: تعديل فاتورة موجودة (زيادة الإجمالي أو نقل الفاتورة
    # لعميل تاني) كان الوحيد بين كل مسارات إنشاء/تعديل الفاتورة اللي مابيتحققش
    # من حد الائتمان إطلاقًا. الفحص هنا بعد خصم الإجمالي القديم من رصيد العميل
    # فوق مباشرة، فـ new_total هو الزيادة الحقيقية على رصيده بعد التعديل.
    await _check_credit_limit(db, data.customer_id, new_total)

    # إصلاح ثغرة تكامل: تعديل بنود/إجمالي الفاتورة كان يغيّر subtotal/tax/total
    # في الجدول من غير ما يعكس القيد المحاسبي القديم أو يسجّل قيد جديد بالمبلغ
    # الصحيح — فيفضل دفتر الأستاذ (Chart of Accounts) واقف على القيمة الأصلية
    # القديمة للأبد رغم إن الفاتورة اتغيّرت فعليًا. نعكس القيد القديم بالقيم
    # القديمة (نفس آلية الحذف) قبل ما نلمس أي حقل، ثم نسجّل قيد جديد بالقيم
    # الجديدة بعد التعديل — تمامًا زي create/delete.
    from app.core.database import current_tenant_id as _ctid_updj
    _tid_updj = _ctid_updj.get() or 1
    await journal_sales_invoice_delete(
        db, _tid_updj, inv.id, inv.code, inv.tax_amount, inv.total, inv.date)

    # احذف السطور القديمة
    old_lines = (await db.execute(select(InvoiceLine).where(InvoiceLine.invoice_id == iid))).scalars().all()
    for l in old_lines:
        await db.delete(l)

    # الإجماليات (اتحسبت فوق قبل أي تعديل عشان فحص خطة الأقساط)
    total = new_total
    inv.code = new_code
    inv.customer_id = data.customer_id
    inv.quotation_id = data.quotation_id
    inv.date = data.date
    inv.due_date = data.due_date
    inv.tax_pct = data.tax_pct
    inv.discount_pct = data.discount_pct
    inv.subtotal = subtotal
    inv.discount_amt = disc_amt
    inv.tax_amount = tax_amt
    inv.total = total
    inv.notes = data.notes
    inv.terms = data.terms

    from app.core.database import current_tenant_id as _ctid_upd
    _tid_upd = _ctid_upd.get() or 1
    await db.flush()
    for i, l in enumerate(data.lines):
        tp = Decimal(str(l.qty)) * Decimal(str(l.unit_price))
        db.add(InvoiceLine(tenant_id=_tid_upd, invoice_id=inv.id,
            item_type=getattr(l, "item_type", None),
            model_no=getattr(l, "model_no", None),
            description=l.description,
            qty=l.qty, unit=l.unit, unit_price=l.unit_price,
            total_price=tp, notes=l.notes, sort_order=l.sort_order or i))

    # حدّث حالة الدفع بناءً على الإجمالي الجديد
    if inv.paid_amount >= inv.total:
        inv.status = "paid"
    elif inv.paid_amount > 0:
        inv.status = "partial"
    else:
        inv.status = "unpaid"

    # طبّق الرصيد الجديد على العميل
    new_cust = await db.get(Customer, data.customer_id)
    if new_cust:
        new_cust.balance = new_cust.balance + total

    # قيد محاسبي جديد بالقيم بعد التعديل (يكمل عكس القيد القديم فوق)
    await journal_sales_invoice_create(db, _tid_updj, inv.id, inv.code, tax_amt, total, data.date)

    await db.commit()
    await db.refresh(inv)
    return {"id": inv.id, "code": inv.code, "total": float(inv.total)}


@router.post("/invoices/{iid}/payment")
async def record_invoice_payment(iid: int, data: InvoicePaymentIn, db: AsyncSession = Depends(get_db),
                                 current_user: dict = Depends(get_current_user)):
    inv = await db.get(Invoice, iid)
    if not inv: raise HTTPException(404)
    amount = Decimal(str(data.amount))
    remaining = inv.total - inv.paid_amount
    if amount <= 0:
        raise HTTPException(400, "المبلغ يجب أن يكون أكبر من صفر")
    if amount > remaining:
        raise HTTPException(400, f"المبلغ المدخل ({float(amount):,.2f}) أكبر من المتبقي ({float(remaining):,.2f})")
    inv.paid_amount = inv.paid_amount + amount
    if inv.paid_amount >= inv.total:
        inv.status = "paid"
    elif inv.paid_amount > 0:
        inv.status = "partial"
    # محطة 5: لو الفاتورة عندها خطة أقساط، وزّع المبلغ تلقائيًا على أقدم قسط مستحق
    touched_installments = await _allocate_payment_to_installments(db, inv.id, amount)
    touched_installments_out = [installment_dict(x) for x in touched_installments]
    # دفعة تُقلّص ما يستحق على العميل
    cust = await db.get(Customer, inv.customer_id)
    if cust:
        cust.balance = cust.balance - amount
    from app.core.database import current_tenant_id as _ctid2
    _tid2 = _ctid2.get() or 1
    # سجّل CustomerPayment لتتبع طريقة الدفع والمرجع
    pay_record = CustomerPayment(
        tenant_id=_tid2,
        customer_id=inv.customer_id,
        amount=amount,
        payment_type=data.payment_type or "cash",
        payment_date=data.payment_date or inv.date,
        reference=data.reference,
        notes=data.notes,
        created_by=int(current_user.get('sub', 0)) or None,
    )
    db.add(pay_record)
    await db.flush()   # نحتاج pay_record.id قبل ربطه بالخزنة وبالفاتورة

    # ربط الدفعة بالفاتورة — بدون ده كشف حساب العميل كان يحسب نفس المبلغ مرتين
    # (مرة من invoice.paid_amount، ومرة كـ"دفعة غير مخصصة" لعدم وجود تخصيص)
    from app.models.models import CustomerPaymentAllocation
    db.add(CustomerPaymentAllocation(
        tenant_id=_tid2, payment_id=pay_record.id,
        invoice_id=inv.id, amount=amount,
    ))

    # ── ربط بالخزنة (إصلاح Bug: كانت Treasury balance لا تتحدث) ──
    await record_payment_in_treasury(
        db,
        amount=amount,
        direction="in",
        source="customer_payment",
        linked_type="customer_payment",
        linked_id=pay_record.id,
        category_name="تحصيل من عملاء",
        description=f"سداد فاتورة {inv.code}",
        reference=data.reference,
        txn_date=data.payment_date or date.today(),
    )

    # القيد يتسجّل بتاريخ الدفعة الفعلي (زي حركة الخزنة فوق) مش تاريخ الفاتورة —
    # وإلا التحصيل يظهر في فترة محاسبية غلط في ميزان المراجعة
    await journal_sales_invoice_payment(db, _tid2, inv.id, inv.code, amount,
                                        data.payment_date or date.today())
    await db.commit()
    await log_event(Events.PAYMENT_RECEIVED, tenant_id=_tid2, entity_type="invoice", entity_id=inv.id,
                     metadata={"amount": float(amount), "status": inv.status}, source_path=f"/api/v1/sales/invoices/{iid}/payment")
    return {"paid_amount": float(inv.paid_amount), "remaining": float(inv.total - inv.paid_amount),
            "status": inv.status, "installments_updated": touched_installments_out}


# ══ محطة 4: جدولة أقساط الفاتورة (InvoicePaymentSchedule) ═══════════

def installment_dict(ins: InvoicePaymentSchedule):
    sm = {"pending": "لم يستحق بعد", "partial": "مدفوع جزئياً", "paid": "مدفوع", "overdue": "متأخر"}
    remaining = float(ins.amount_due) - float(ins.paid_amount)
    # نفس أسلوب inv_dict: overdue بيتحسب ديناميكياً للعرض بس، مفيش تعديل على DB هنا.
    # تقارير التنبيه/التأخر التفصيلية (محطة 6) في /api/v1/reports/sales/installments/*
    effective_status = ins.status
    if ins.status != "paid" and ins.due_date < date.today() and remaining > 0:
        effective_status = "overdue"
    return {
        "id": ins.id,
        "invoice_id": ins.invoice_id,
        "installment_no": ins.installment_no,
        "label": ins.label,
        "amount_due": float(ins.amount_due),
        "due_date": str(ins.due_date),
        "paid_amount": float(ins.paid_amount),
        "remaining_amount": remaining,
        "status": effective_status, "status_ar": sm.get(effective_status, effective_status),
    }


# محطة 5: توزيع تلقائي لدفعة على أقساط الفاتورة (لو عندها خطة أقساط)
# التوزيع FIFO: أقدم قسط لسه مستحق (pending/partial) أولاً بترتيب تاريخ الاستحقاق،
# ولو المبلغ زاد يكمل تلقائيًا على اللي بعده. لو الفاتورة من غير خطة أقساط، الدالة
# مش بتعمل حاجة خالص (توافق خلفي كامل مع السلوك القديم).
async def _allocate_payment_to_installments(db: AsyncSession, invoice_id: int, amount: Decimal) -> list:
    r = await db.execute(
        select(InvoicePaymentSchedule)
        .where(
            InvoicePaymentSchedule.invoice_id == invoice_id,
            InvoicePaymentSchedule.status.in_(("pending", "partial")),
        )
        .order_by(InvoicePaymentSchedule.due_date, InvoicePaymentSchedule.installment_no)
    )
    open_installments = r.scalars().all()
    remaining_payment = amount
    touched = []
    for ins in open_installments:
        if remaining_payment <= 0:
            break
        room = ins.amount_due - ins.paid_amount
        if room <= 0:
            continue
        applied = min(remaining_payment, room)
        ins.paid_amount = ins.paid_amount + applied
        ins.status = "paid" if ins.paid_amount >= ins.amount_due else "partial"
        remaining_payment -= applied
        touched.append(ins)
    return touched


# ══ محطة 7: قالب خطة دفع على عرض السعر/أمر البيع ═════════════════════

def template_dict(t: PaymentScheduleTemplate) -> dict:
    return {
        "id": t.id, "source_type": t.source_type, "source_id": t.source_id,
        "installment_no": t.installment_no, "label": t.label,
        "pct": float(t.pct), "days_offset": t.days_offset,
    }


async def _save_payment_schedule_template(
    db: AsyncSession, source_type: str, source_id: int, tenant_id: int,
    installments: list, replace: bool,
) -> list:
    if not installments:
        raise HTTPException(400, "لازم تحدد قسط واحد على الأقل")
    total_pct = sum(Decimal(str(i.pct)) for i in installments)
    if abs(total_pct - Decimal("100")) > Decimal("0.5"):
        raise HTTPException(400, f"مجموع نسب القالب لازم يساوي 100% تقريبًا (المجموع الحالي: {float(total_pct)}%)")

    existing_ids = (await db.execute(
        select(PaymentScheduleTemplate.id).where(
            PaymentScheduleTemplate.source_type == source_type,
            PaymentScheduleTemplate.source_id == source_id,
        )
    )).scalars().all()
    if existing_ids and not replace:
        raise HTTPException(409, "يوجد قالب خطة دفع بالفعل — استخدم ?replace=true لاستبداله")
    if existing_ids:
        await db.execute(sa_delete(PaymentScheduleTemplate).where(
            PaymentScheduleTemplate.source_type == source_type,
            PaymentScheduleTemplate.source_id == source_id,
        ))

    rows = []
    for idx, line in enumerate(installments, start=1):
        row = PaymentScheduleTemplate(
            tenant_id=tenant_id, source_type=source_type, source_id=source_id,
            installment_no=idx, label=line.label,
            pct=Decimal(str(line.pct)), days_offset=line.days_offset,
        )
        db.add(row)
        rows.append(row)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return rows


async def _materialize_payment_schedule_from_template(
    db: AsyncSession, source_type: str, source_id: int, invoice: Invoice, tenant_id: int,
) -> bool:
    """
    لو المصدر (عرض سعر / أمر بيع) عنده قالب خطة دفع، يولّد منه InvoicePaymentSchedule
    فعلية على الفاتورة الجديدة — النسب تتحول لمبالغ (بضبط تقريب على آخر قسط عشان
    المجموع يساوي إجمالي الفاتورة بالظبط)، والأيام تتحول لتواريخ استحقاق فعلية
    بالنسبة لتاريخ الفاتورة الحقيقي. بيرجع True لو لقى قالب وطبّقه، False لو مفيش.
    """
    tmpl_rows = (await db.execute(
        select(PaymentScheduleTemplate)
        .where(
            PaymentScheduleTemplate.source_type == source_type,
            PaymentScheduleTemplate.source_id == source_id,
        )
        .order_by(PaymentScheduleTemplate.installment_no)
    )).scalars().all()
    if not tmpl_rows:
        return False

    # لو الفاتورة عندها خطة أقساط فعلية بالفعل (مثلاً اتضافت يدوي)، متكتبش فوقها
    has_schedule = await db.scalar(
        select(InvoicePaymentSchedule.id).where(InvoicePaymentSchedule.invoice_id == invoice.id).limit(1)
    )
    if has_schedule:
        return False

    amounts = [(invoice.total * t.pct / Decimal("100")).quantize(Decimal("0.01")) for t in tmpl_rows]
    # فرق التقريب (لو موجود) يتصحح على آخر قسط عشان المجموع يساوي إجمالي الفاتورة بالظبط
    diff = invoice.total - sum(amounts)
    if amounts:
        amounts[-1] = amounts[-1] + diff

    for t, amt in zip(tmpl_rows, amounts):
        db.add(InvoicePaymentSchedule(
            tenant_id=tenant_id, invoice_id=invoice.id, installment_no=t.installment_no,
            label=t.label, amount_due=amt,
            due_date=invoice.date + timedelta(days=t.days_offset),
        ))
    return True


@router.post("/invoices/{iid}/payment-schedule", status_code=201)
async def create_payment_schedule(iid: int, data: PaymentScheduleIn, replace: bool = False,
                                   db: AsyncSession = Depends(get_db),
                                   _auth: dict = Depends(require_admin_accountant)):
    """
    إنشاء خطة أقساط للفاتورة. كل قسط يُحدَّد بمبلغ ثابت (amount) أو نسبة (pct من 0-100)،
    ومجموع الأقساط لازم يساوي إجمالي الفاتورة (بسماحية فرق تقريب 0.02).
    لو الفاتورة عندها خطة أقساط بالفعل، لازم تمرّر ?replace=true عشان تستبدلها.
    """
    inv = await db.get(Invoice, iid)
    if not inv:
        raise HTTPException(404, "الفاتورة غير موجودة")
    if not data.installments:
        raise HTTPException(400, "لازم تحدد قسط واحد على الأقل")

    existing_ids = (await db.execute(
        select(InvoicePaymentSchedule.id).where(InvoicePaymentSchedule.invoice_id == iid)
    )).scalars().all()
    if existing_ids and not replace:
        raise HTTPException(409, "الفاتورة عندها خطة أقساط بالفعل — استخدم ?replace=true لاستبدالها")

    if existing_ids and replace:
        # حماية: منع استبدال خطة عليها دفعات فعلية مسجّلة — الاستبدال كان بيمسح
        # الأقساط القديمة بكل paid_amount المسجّل عليها بصمت (فلوس العميل تفضل
        # محفوظة في Invoice.paid_amount لكن الربط بأي قسط اتسدد بيضيع تمامًا).
        paid_sum = (await db.execute(
            select(func.sum(InvoicePaymentSchedule.paid_amount))
            .where(InvoicePaymentSchedule.invoice_id == iid)
        )).scalar() or Decimal("0")
        if paid_sum > 0:
            raise HTTPException(
                400,
                f"لا يمكن استبدال الخطة — عليها دفعات مسجّلة بالفعل بمبلغ {float(paid_sum):,.2f}. "
                f"عدّل الأقساط اللي لسه متسددتش بس، أو راجع الدفعات المسجّلة قبل الاستبدال الكامل.",
            )

    from app.core.database import current_tenant_id as _ctid_sch
    tenant_id = _ctid_sch.get() or 1

    computed = []
    total_computed = Decimal("0")
    for line in data.installments:
        if line.amount is not None and line.pct is not None:
            raise HTTPException(400, "حدد amount أو pct لكل قسط، مش الاتنين مع بعض")
        if line.amount is not None:
            amt = Decimal(str(line.amount))
        elif line.pct is not None:
            amt = (inv.total * Decimal(str(line.pct)) / Decimal("100")).quantize(Decimal("0.01"))
        else:
            raise HTTPException(400, "كل قسط لازم يكون له amount أو pct")
        if amt <= 0:
            raise HTTPException(400, "قيمة القسط يجب أن تكون أكبر من صفر")
        if line.pct is not None and Decimal(str(line.pct)) > 100:
            raise HTTPException(400, "نسبة القسط لا يمكن أن تتجاوز 100%")
        computed.append((line, amt))
        total_computed += amt

    diff = abs(total_computed - inv.total)
    if diff > Decimal("0.02"):
        raise HTTPException(
            400,
            f"مجموع الأقساط ({float(total_computed):,.2f}) لازم يساوي إجمالي الفاتورة "
            f"({float(inv.total):,.2f}) — الفرق: {float(diff):,.2f}",
        )

    if existing_ids:
        await db.execute(sa_delete(InvoicePaymentSchedule).where(InvoicePaymentSchedule.invoice_id == iid))

    rows = []
    for idx, (line, amt) in enumerate(computed, start=1):
        row = InvoicePaymentSchedule(
            tenant_id=tenant_id, invoice_id=iid, installment_no=idx,
            label=line.label, amount_due=amt, due_date=line.due_date,
        )
        db.add(row)
        rows.append(row)

    # إصلاح ثغرة تكامل: لو الفاتورة عليها دفعات مسجّلة قبل إنشاء الخطة، الأقساط
    # الجديدة كانت بتتولد كلها "pending" — فالدفعات اللاحقة تتوزّع على أول
    # الأقساط وآخرها يفضل مفتوح للأبد رغم إن الفاتورة نفسها اتقفلت مدفوعة.
    # نوزّع المبلغ المسدد مسبقًا على الأقساط (FIFO) بنفس المنطق المركزي.
    if (inv.paid_amount or Decimal("0")) > 0:
        await db.flush()
        await _allocate_payment_to_installments(db, iid, Decimal(str(inv.paid_amount)))

    await db.commit()
    for row in rows:
        await db.refresh(row)

    # ── شبكة الأمان: حدّث invoice.due_date ليعكس أقرب قسط مفتوح ──
    await db.refresh(inv)
    await sync_invoice_due_date(db, inv)
    await db.commit()

    return [installment_dict(r) for r in rows]


@router.patch("/invoices/{iid}/payment-schedule/{installment_id}")
async def patch_installment(
    iid: int, installment_id: int,
    data: "InstallmentPatchIn",
    db: AsyncSession = Depends(get_db),
    _auth: dict = Depends(require_admin_accountant),
):
    """
    تعديل قسط واحد فقط (تاريخ أو مبلغ أو كلاهما) بدون استبدال الخطة كلها.
    الشرط: القسط لازم يكون لسه ما اتسدّدش بالكامل.
    """
    ins = await db.get(InvoicePaymentSchedule, installment_id)
    if not ins or ins.invoice_id != iid:
        raise HTTPException(404, "القسط غير موجود")
    if ins.status == "paid":
        raise HTTPException(400, "القسط متسدد بالكامل — لا يمكن تعديله")

    inv = await db.get(Invoice, iid)
    if not inv:
        raise HTTPException(404, "الفاتورة غير موجودة")

    if data.due_date is not None:
        ins.due_date = data.due_date
    if data.amount_due is not None:
        if data.amount_due <= 0:
            raise HTTPException(400, "قيمة القسط يجب أن تكون أكبر من صفر")
        if Decimal(str(data.amount_due)) < (ins.paid_amount or Decimal("0")):
            raise HTTPException(
                400,
                f"قيمة القسط الجديدة أقل من المبلغ المسدد عليه بالفعل ({float(ins.paid_amount):,.2f})",
            )
        # تحقق أن مجموع الأقساط لا يتجاوز إجمالي الفاتورة
        other_sum = (await db.execute(
            select(func.sum(InvoicePaymentSchedule.amount_due))
            .where(
                InvoicePaymentSchedule.invoice_id == iid,
                InvoicePaymentSchedule.id != installment_id,
            )
        )).scalar() or Decimal("0")
        new_total = other_sum + Decimal(str(data.amount_due))
        diff = abs(new_total - inv.total)
        if diff > Decimal("0.02"):
            raise HTTPException(
                400,
                f"بعد التعديل مجموع الأقساط ({float(new_total):,.2f}) سيختلف عن إجمالي الفاتورة "
                f"({float(inv.total):,.2f}) — الفرق: {float(diff):,.2f}",
            )
        ins.amount_due = Decimal(str(data.amount_due))
        # إعادة حساب الحالة
        if ins.paid_amount >= ins.amount_due:
            ins.status = "paid"
        elif ins.paid_amount > 0:
            ins.status = "partial"
        elif ins.due_date < date.today():
            ins.status = "overdue"
        else:
            ins.status = "pending"

    if data.label is not None:
        ins.label = data.label

    await db.flush()
    await sync_invoice_due_date(db, inv)
    await db.commit()
    await db.refresh(ins)
    return installment_dict(ins)


@router.get("/invoices/{iid}/payment-schedule")
async def get_payment_schedule(iid: int, db: AsyncSession = Depends(get_db)):
    inv = await db.get(Invoice, iid)
    if not inv:
        raise HTTPException(404, "الفاتورة غير موجودة")
    r = await db.execute(
        select(InvoicePaymentSchedule)
        .where(InvoicePaymentSchedule.invoice_id == iid)
        .order_by(InvoicePaymentSchedule.installment_no)
    )
    rows = r.scalars().all()
    return [installment_dict(x) for x in rows]


@router.post("/invoices/{iid}/payment-schedule/{installment_id}/pay", status_code=200)
async def pay_installment(
    iid: int,
    installment_id: int,
    data: "InvoicePaymentIn",
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    تسجيل دفعة مباشرة من صف القسط (الفجوة 3 من التقرير).
    يُسدد القسط المحدد أولاً، ثم لو في زيادة تنتقل للقسط التالي.
    يعادل record_invoice_payment لكن بنقطة دخول من القسط نفسه.
    """
    ins = await db.get(InvoicePaymentSchedule, installment_id)
    if not ins or ins.invoice_id != iid:
        raise HTTPException(404, "القسط غير موجود")
    if ins.status == "paid":
        raise HTTPException(400, "القسط مسدد بالكامل بالفعل")

    inv = await db.get(Invoice, iid)
    if not inv:
        raise HTTPException(404, "الفاتورة غير موجودة")

    amount = Decimal(str(data.amount))
    remaining_invoice = inv.total - inv.paid_amount
    if amount <= 0:
        raise HTTPException(400, "المبلغ يجب أن يكون أكبر من صفر")
    if amount > remaining_invoice:
        raise HTTPException(
            400,
            f"المبلغ ({float(amount):,.2f}) أكبر من المتبقي على الفاتورة ({float(remaining_invoice):,.2f})"
        )

    # تحديث الفاتورة
    inv.paid_amount = inv.paid_amount + amount
    if inv.paid_amount >= inv.total:
        inv.status = "paid"
    elif inv.paid_amount > 0:
        inv.status = "partial"

    # توزيع على الأقساط (نفس المنطق المركزي — يبدأ من القسط المحدد)
    touched = await _allocate_payment_to_installments(db, inv.id, amount)

    # رصيد العميل
    cust = await db.get(Customer, inv.customer_id)
    if cust:
        cust.balance = cust.balance - amount

    from app.core.database import current_tenant_id as _ctid_pi
    _tid_pi = _ctid_pi.get() or 1
    from app.models.models import CustomerPaymentAllocation

    pay_record = CustomerPayment(
        tenant_id=_tid_pi,
        customer_id=inv.customer_id,
        amount=amount,
        payment_type=data.payment_type or "cash",
        payment_date=data.payment_date or inv.date,
        reference=data.reference,
        notes=data.notes or f"سداد قسط {ins.installment_no} — {ins.label or ''}",
        created_by=int(current_user.get("sub", 0)) or None,
    )
    db.add(pay_record)
    await db.flush()

    db.add(CustomerPaymentAllocation(
        tenant_id=_tid_pi, payment_id=pay_record.id,
        invoice_id=inv.id, amount=amount,
    ))

    await record_payment_in_treasury(
        db, amount=amount, direction="in",
        source="customer_payment", linked_type="customer_payment",
        linked_id=pay_record.id, category_name="تحصيل من عملاء",
        description=f"سداد قسط {ins.installment_no} — فاتورة {inv.code}",
        reference=data.reference,
        txn_date=data.payment_date or date.today(),
    )

    # إصلاح ثغرة تكامل (رصدها فريق المحاسبة/الخزينة): هذا المسار بيستقبل
    # فلوس فعلية في الخزنة (record_payment_in_treasury فوق) بالظبط زي
    # record_invoice_payment، لكن كان ناقصه القيد المحاسبي المقابل في دفتر
    # الأستاذ — فيفضل رصيد الخزنة يزيد والـtrial balance واقف من غير ما
    # يعكس الكاش المُحصَّل عبر سداد الأقساط المباشر.
    await journal_sales_invoice_payment(db, _tid_pi, inv.id, inv.code, amount,
                                        data.payment_date or date.today())

    await db.commit()
    return {
        "invoice_id": iid,
        "installment_id": installment_id,
        "amount_paid": float(amount),
        "invoice_status": inv.status,
        "touched_installments": [installment_dict(x) for x in touched],
    }


@router.post("/admin/run-installment-scheduler")
async def run_installment_scheduler_now(
    _auth: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    تشغيل فوري لـ scheduler الأقساط — admin فقط.
    يحدث حالة كل الأقساط المتأخرة فوراً بدون انتظار الـ cron.
    """
    from app.services.installment_scheduler import _run_once
    result = await _run_once(db)
    return {"message": "تم تحديث حالة الأقساط", **result}


@router.delete("/invoices/{iid}")
async def delete_invoice(iid: int, db: AsyncSession = Depends(get_db),
                         current_user: dict = Depends(get_current_user)):
    inv = await db.get(Invoice, iid)
    if not inv: raise HTTPException(404)
    # إصلاح ثغرة تكامل: حذف فاتورة عليها دفعات مسجّلة كان يعكس القيد المحاسبي
    # للفاتورة بالكامل (بما فيها الجزء المدفوع) من غير ما يمس قيد الدفعة نفسه
    # أو سجلات CustomerPayment/الخزنة المرتبطة — فيفضل حساب العملاء (AR) في
    # دفتر الأستاذ غير متزن بقيمة المدفوع، والدفعة تفضل يتيمة بلا فاتورة.
    # امنع الحذف طالما فيه مبلغ مسدد، واطلب استرجاعه/عكسه أولاً.
    if inv.paid_amount and inv.paid_amount > 0:
        raise HTTPException(
            400,
            f"لا يمكن حذف الفاتورة — عليها مبلغ مسدد {float(inv.paid_amount):,.2f}. "
            f"يجب عكس/استرجاع الدفعات المسجّلة على هذه الفاتورة أولاً.",
        )
    # إصلاح ثغرة تكامل: فاتورة عليها إشعارات دائنة (حتى لو مسودة) كان حذفها
    # بيسيب الإشعار بمرجع فاتورة يتيم (ويكسر الـ FK على Postgres).
    from app.models.models import CreditNote as _CN, StockMovement as _SM, Material as _Mat
    cn_code = await db.scalar(
        select(_CN.code).where(_CN.invoice_id == iid, _CN.status != "cancelled").limit(1)
    )
    if cn_code:
        raise HTTPException(400, f"لا يمكن حذف الفاتورة — مرتبط بها إشعار دائن {cn_code}. ألغِه أو احذفه أولاً.")

    from app.core.database import current_tenant_id as _ctid_del
    _tid_del = _ctid_del.get() or 1

    # دورة الموافقات: حذف/إلغاء فاتورة صادرة. بعد كل الحراس وقبل أي عكس (مخزون/قيد/رصيد)،
    # فالعكس الكامل بيتنفذ مرة واحدة بعد الموافقة وما فيش حالة نصف منفّذة.
    _del_total = to_dec(inv.total)
    await require_approval(
        db, current_user, "invoice_delete",
        entity_type="invoice", entity_id=iid, entity_label=str(inv.code),
        metrics={"amount": _del_total},
        params={"invoice_id": iid, "total": _del_total},
    )

    # إصلاح ثغرة تكامل: فاتورة أمر الإنتاج (work_order_to_invoice) بتخصم رصيد
    # تام الصنع (FG) وتسجّل قيد COGS — حذفها كان بيعكس الإيراد بس ويسيب
    # البضاعة "مباعة" في المخزون والتكلفة في دفتر الأستاذ للأبد. نرجّع نفس
    # الكميات للمخزون ونعكس قيد التكلفة بنفس القيمة.
    fg_issues = (await db.execute(
        select(_SM).where(
            _SM.source_doc_type == "sales_invoice", _SM.source_doc_id == iid,
            _SM.movement_type == "out",
        )
    )).scalars().all()
    if fg_issues:
        from app.services.inventory_ledger import get_or_create_warehouse_bin, post_receipt
        from app.services.auto_journal import journal_cogs_reversal
        fg_bin = await get_or_create_warehouse_bin(db, _tid_del, "FG")
        reversed_cost = Decimal("0")
        for mv in fg_issues:
            mat = await db.get(_Mat, mv.material_id)
            if not mat:
                continue
            await post_receipt(
                db, material=mat, qty=mv.qty, unit_cost=mv.unit_cost or Decimal("0"),
                source_doc_type="sales_invoice_delete", source_doc_id=iid,
                destination_location_id=fg_bin.id,
                reference=inv.code, notes=f"إرجاع مخزون — حذف الفاتورة {inv.code}",
            )
            reversed_cost += Decimal(str(mv.qty)) * Decimal(str(mv.unit_cost or 0))
        await journal_cogs_reversal(db, _tid_del, inv.id, inv.code, reversed_cost, date.today())

    lines = (await db.execute(select(InvoiceLine).where(InvoiceLine.invoice_id==iid))).scalars().all()
    for l in lines: await db.delete(l)
    await db.execute(sa_delete(InvoicePaymentSchedule).where(InvoicePaymentSchedule.invoice_id == iid))
    # عكس الجزء غير المدفوع فقط من رصيد العميل
    # (الجزء المدفوع انعكس بالفعل عند تسجيل كل دفعة)
    unpaid = inv.total - inv.paid_amount
    cust = await db.get(Customer, inv.customer_id)
    if cust and unpaid > 0:
        cust.balance = cust.balance - unpaid
    # قيد عكسي في دفتر الأستاذ
    await journal_sales_invoice_delete(db, _tid_del, inv.id, inv.code, inv.tax_amount, inv.total, inv.date)
    await db.delete(inv)
    await db.commit()
    return {"message": "تم الحذف"}


# ══ DOCUMENT FLOW CONVERSION ════════════════════════════════════

async def _next_order_code(db: AsyncSession) -> str:
    from sqlalchemy import func as _f
    count = await db.scalar(select(_f.count()).select_from(CustomerOrder))
    n = (count or 0) + 1
    while True:
        code = f"ORD-{n:04d}"
        exists = await db.scalar(select(CustomerOrder.id).where(CustomerOrder.code == code))
        if not exists:
            return code
        n += 1


async def _next_wo_code(db: AsyncSession) -> str:
    from sqlalchemy import func as _f
    count = await db.scalar(select(_f.count()).select_from(WorkOrder))
    n = (count or 0) + 1
    while True:
        code = f"WO-{n:04d}"
        exists = await db.scalar(select(WorkOrder.id).where(WorkOrder.code == code))
        if not exists:
            return code
        n += 1


@router.post("/quotations/{qid}/to-order", status_code=201)
async def quotation_to_order(qid: int, data: QuotationToOrderRequest,
                             db: AsyncSession = Depends(get_db)):
    """
    تحويل عرض سعر → طلب عميل (CustomerOrder).
    يحذف العلاقة القديمة لو وُجدت ثم ينشئ طلباً جديداً.
    """
    from app.core.database import current_tenant_id as _ctid_o2
    tid = _ctid_o2.get() or 1

    # تحقق من وجود العرض
    q = (await db.execute(
        select(Quotation)
        .options(selectinload(Quotation.lines))
        .where(Quotation.id == qid)
    )).scalar_one_or_none()
    if not q:
        raise HTTPException(404, "عرض السعر غير موجود")

    # منع تحويل عرض مرفوض
    if q.status == "rejected":
        raise HTTPException(400, "لا يمكن تحويل عرض مرفوض")

    # تحقق من وجود طلب مرتبط مسبقاً
    existing_order = (await db.scalar(
        select(CustomerOrder.id).where(CustomerOrder.quotation_id == qid)
    ))
    if existing_order:
        raise HTTPException(400, f"تم تحويل هذا العرض مسبقاً — رقم الطلب: {existing_order}")
    existing_inv = await db.scalar(select(Invoice.code).where(Invoice.quotation_id == qid).limit(1))
    if existing_inv:
        raise HTTPException(400, f"تم تحويل هذا العرض لفاتورة مسبقاً — رقم الفاتورة: {existing_inv}")

    code = await _next_order_code(db)
    order = CustomerOrder(
        tenant_id=tid,
        code=code,
        customer_id=q.customer_id,
        location_id=q.location_id,
        quotation_id=qid,
        total_price=q.total,
        unit_price=q.subtotal,
        quantity=1,
        status="pending",
        order_date=date.today(),
        delivery_date=data.delivery_date,
        notes=data.notes or q.notes,
        description=q.terms,
    )
    db.add(order)
    await db.flush()  # نحتاج order.id لبنود الطلب

    # نسخ بنود العرض كبنود طلب
    for i, l in enumerate(q.lines or []):
        db.add(CustomerOrderLine(
            tenant_id=tid,
            order_id=order.id,
            description=l.description,
            qty=l.qty,
            unit=l.unit,
            unit_price=l.unit_price,
            total_price=l.total_price,
            notes=l.notes,
            sort_order=l.sort_order or i,
        ))

    # تحديث حالة العرض
    q.status = "accepted"

    await db.commit()
    await log_event(Events.ORDER_CREATED, tenant_id=tid, entity_type="order", entity_id=order.id,
                     metadata={"total": float(order.total_price), "from_quotation_id": qid},
                     source_path=f"/api/v1/sales/quotations/{qid}/to-order")
    return {"id": order.id, "code": order.code, "total": float(order.total_price)}


@router.post("/orders/{order_id}/payment-schedule-template", status_code=201)
async def create_order_payment_template(
    order_id: int, data: PaymentScheduleTemplateIn, replace: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """محطة 7: تعريف قالب خطة دفع على أمر البيع — نفس فكرة قالب عرض السعر، بس أقرب للفاتورة النهائية."""
    order = await db.get(CustomerOrder, order_id)
    if not order: raise HTTPException(404, "أمر البيع غير موجود")
    from app.core.database import current_tenant_id as _ctid_ot
    rows = await _save_payment_schedule_template(
        db, "customer_order", order_id, _ctid_ot.get() or 1, data.installments, replace)
    return [template_dict(r) for r in rows]


@router.get("/orders/{order_id}/payment-schedule-template")
async def get_order_payment_template(order_id: int, db: AsyncSession = Depends(get_db)):
    order = await db.get(CustomerOrder, order_id)
    if not order: raise HTTPException(404, "أمر البيع غير موجود")
    rows = (await db.execute(
        select(PaymentScheduleTemplate)
        .where(PaymentScheduleTemplate.source_type == "customer_order", PaymentScheduleTemplate.source_id == order_id)
        .order_by(PaymentScheduleTemplate.installment_no)
    )).scalars().all()
    return [template_dict(r) for r in rows]


@router.post("/orders/{order_id}/to-work-order", status_code=201)
async def order_to_work_order(order_id: int, data: OrderToWorkOrderRequest,
                              db: AsyncSession = Depends(get_db)):
    """
    تحويل طلب عميل → أمر إنتاج (WorkOrder).
    ينشئ WO مرتبطاً بالطلب والمعدة، ويحسب planned_cost من BOM البارامتري.
    """
    from app.core.database import current_tenant_id as _ctid_wo
    tid = _ctid_wo.get() or 1

    order = await db.get(CustomerOrder, order_id)
    if not order:
        raise HTTPException(404, "الطلب غير موجود")
    if order.status in ("cancelled", "delivered"):
        raise HTTPException(400, f"لا يمكن إنشاء أمر إنتاج لطلب حالته {order.status}")
    if data.quantity is None or Decimal(str(data.quantity)) <= 0:
        raise HTTPException(400, "كمية الإنتاج يجب أن تكون أكبر من صفر")
    if data.start_date and data.planned_end_date and data.planned_end_date < data.start_date:
        raise HTTPException(400, "تاريخ الانتهاء المخطط لا يمكن أن يكون قبل تاريخ البدء")

    eq = await db.get(Equipment, data.equipment_id)
    if not eq:
        raise HTTPException(404, "المعدة غير موجودة")

    # حساب التكلفة المخططة من BOM البارامتري
    planned_cost = Decimal("0")
    try:
        from app.services.equipment import EquipmentService
        bom = await EquipmentService.calculate_bom_cost(db, data.equipment_id, data.quantity)
        planned_cost = Decimal(str(bom.get("total_cost", 0)))
    except Exception:
        pass  # التكلفة اختيارية — مايوقفش إنشاء الأمر

    code = await _next_wo_code(db)
    wo = WorkOrder(
        tenant_id=tid,
        code=code,
        equipment_id=data.equipment_id,
        customer_order_id=order_id,
        quantity=data.quantity,
        quantity_unit=data.quantity_unit,
        planned_cost=planned_cost,
        status="draft",
        start_date=data.start_date,
        planned_end_date=data.planned_end_date,
        notes=data.notes or order.notes,
    )
    db.add(wo)

    # تحديث حالة الطلب
    order.status = "in_production"

    await db.commit()
    await db.refresh(wo)
    await log_event(Events.WORK_ORDER_CREATED, tenant_id=tid, entity_type="work_order", entity_id=wo.id,
                     metadata={"planned_cost": float(wo.planned_cost), "customer_order_id": order_id},
                     source_path=f"/api/v1/sales/orders/{order_id}/to-work-order")
    return {
        "id": wo.id, "code": wo.code,
        "planned_cost": float(wo.planned_cost),
        "customer_order_id": wo.customer_order_id,
    }


@router.post("/work-orders/{wo_id}/to-invoice", status_code=201)
async def work_order_to_invoice(wo_id: int, data: WorkOrderToInvoiceRequest,
                                db: AsyncSession = Depends(get_db),
                                current_user: dict = Depends(get_current_user)):
    """
    تحويل أمر إنتاج → فاتورة مبيعات.
    يستخدم actual_cost أو planned_cost كسعر البند الوحيد للفاتورة.
    """
    from app.core.database import current_tenant_id as _ctid_inv
    tid = _ctid_inv.get() or 1

    # جلب الأمر مع المعدة والطلب
    wo = (await db.execute(
        select(WorkOrder)
        .options(
            selectinload(WorkOrder.equipment),
            selectinload(WorkOrder.customer_order),
        )
        .where(WorkOrder.id == wo_id)
    )).scalar_one_or_none()
    if not wo:
        raise HTTPException(404, "أمر الإنتاج غير موجود")

    if not wo.customer_order_id or not wo.customer_order:
        raise HTTPException(400, "أمر الإنتاج غير مرتبط بطلب عميل — لا يمكن إنشاء فاتورة تلقائية")
    if wo.customer_order.status == "cancelled":
        raise HTTPException(400, "طلب العميل المرتبط بأمر الإنتاج ملغي — لا يمكن فوترته")
    for _nm, _v in (("الضريبة", data.tax_pct), ("الخصم", data.discount_pct)):
        if _v is not None and (Decimal(str(_v)) < 0 or Decimal(str(_v)) > 100):
            raise HTTPException(400, f"نسبة {_nm} يجب أن تكون بين 0 و100")

    # محطة 23: منع فوترة نفس أمر الإنتاج مرتين (كان مفيش أي تحقق قبل كده)
    existing_invoice = await db.scalar(
        select(Invoice.id).where(Invoice.work_order_id == wo_id)
    )
    if existing_invoice:
        raise HTTPException(400, f"تم إصدار فاتورة لأمر الإنتاج ده مسبقًا — رقم الفاتورة: {existing_invoice}")

    # السعر الأساسي: يفضل total_price الطلب، لو صفر يأخذ التكلفة الفعلية أو المخططة
    order_price = Decimal(str(wo.customer_order.total_price or 0))
    base_price = order_price if order_price > 0 else (
        wo.actual_cost if wo.actual_cost > 0 else wo.planned_cost
    )

    subtotal = base_price
    disc_amt = subtotal * data.discount_pct / 100
    taxable = subtotal - disc_amt
    tax_amt = taxable * data.tax_pct / 100
    total = taxable + tax_amt

    code = await _next_invoice_code(db)
    eq_name = wo.equipment.name_ar if wo.equipment else f"معدة #{wo.equipment_id}"

    # تحقق حد الائتمان قبل إنشاء الفاتورة
    await _check_credit_limit(db, wo.customer_order.customer_id, total)

    # دورة الموافقات: خصم على فاتورة أمر الإنتاج (نفس قاعدة create_invoice)
    _w_pct, _w_amt, _w_total = to_dec(data.discount_pct), to_dec(disc_amt), to_dec(total)
    if _w_pct > 0 or _w_amt > 0:
        await require_approval(
            db, current_user, "invoice_discount",
            entity_type="work_order", entity_id=wo_id, entity_label=str(wo.code),
            metrics={"percent": _w_pct, "amount": _w_amt},
            params={"work_order_id": wo_id, "discount_pct": _w_pct, "total": _w_total},
        )

    inv = Invoice(
        tenant_id=tid,
        code=code,
        customer_id=wo.customer_order.customer_id,
        location_id=wo.customer_order.location_id,
        quotation_id=wo.customer_order.quotation_id,
        customer_order_id=wo.customer_order_id,
        work_order_id=wo.id,
        date=date.today(),
        tax_pct=data.tax_pct,
        discount_pct=data.discount_pct,
        subtotal=subtotal,
        discount_amt=disc_amt,
        tax_amount=tax_amt,
        total=total,
        notes=data.notes or f"فاتورة أمر إنتاج {wo.code}",
    )
    db.add(inv)
    await db.flush()

    # بند واحد: اسم المعدة × كمية الإنتاج
    qty = wo.quantity if wo.quantity else Decimal("1")
    unit_price = base_price / qty if qty > 0 else base_price
    db.add(InvoiceLine(
        tenant_id=tid,
        invoice_id=inv.id,
        description=f"تصنيع وتوريد: {eq_name}",
        qty=qty,
        unit=wo.quantity_unit or "وحدة",
        unit_price=unit_price,
        total_price=base_price,
        sort_order=0,
    ))

    # تحديث رصيد العميل
    cust = await db.get(Customer, wo.customer_order.customer_id)
    if cust:
        cust.balance = cust.balance + total

    # قيد محاسبي
    await journal_sales_invoice_create(db, tid, inv.id, inv.code, tax_amt, total, date.today())

    # خصم فعلي من مخزون تام الصنع (FG-{wo.code}) + قيد COGS — نفس النمط بالظبط
    # المستخدم في delivery_orders.py._release_fg_stock_for_line. قبل التصليح
    # ده، فوترة أمر إنتاج كانت بتسجّل إيراد كامل من غير ما تخصم أي مخزون
    # ومن غير أي قيد تكلفة بضاعة مباعة — رصيد FG بيفضل واقف في المخزون للأبد
    # (مخزون وهمي) والربح المعروض بيبقى أعلى من الحقيقة بقيمة التكلفة كاملة.
    await _issue_fg_for_wo_invoice(db, tid, wo, inv, qty)

    # محطة 23: ما بنلمسش wo.status هنا. كان بيتحط "billed" وده كان بيمسح
    # حالة "done" اللي تقارير كتير بتفلتر بيها (dashboard_new.py, reports.py,
    # price_catalog.py) — يعني أي أمر إنتاج اتفوتر كان بيختفي من كل تقارير
    # "الإنتاج المكتمل" رغم إنه فعلاً مكتمل ومفوتر. حالة الفوترة دلوقتي
    # بتتحدد من وجود Invoice.work_order_id (التحقق فوق) مش من status.

    # محطة 7: قالب خطة الدفع — أولوية لقالب أمر البيع، وإلا نجرب قالب عرض السعر المرتبط بيه
    applied = await _materialize_payment_schedule_from_template(
        db, "customer_order", wo.customer_order_id, inv, tid)
    if not applied and wo.customer_order.quotation_id:
        await _materialize_payment_schedule_from_template(
            db, "quotation", wo.customer_order.quotation_id, inv, tid)

    await db.commit()
    await db.refresh(inv)
    return {"id": inv.id, "code": inv.code, "total": float(inv.total)}


@router.post("/orders/{order_id}/advance-payment", status_code=201)
async def order_advance_payment(order_id: int, data: OrderAdvancePaymentRequest,
                                db: AsyncSession = Depends(get_db),
                                current_user: dict = Depends(get_current_user)):
    """
    تسجيل دفعة مقدمة على طلب عميل.
    ينشئ CustomerPayment مرتبطاً بالطلب + يحدّث الخزنة.
    """
    from app.core.database import current_tenant_id as _ctid_adv
    tid = _ctid_adv.get() or 1

    order = await db.get(CustomerOrder, order_id)
    if not order:
        raise HTTPException(404, "الطلب غير موجود")
    if order.status == "cancelled":
        raise HTTPException(400, "لا يمكن تسجيل دفعة مقدمة على طلب ملغي")

    amount = Decimal(str(data.amount))
    if amount <= 0:
        raise HTTPException(400, "المبلغ يجب أن يكون أكبر من صفر")

    # إنشاء سجل الدفعة مرتبطاً بالطلب
    pay_record = CustomerPayment(
        tenant_id=tid,
        customer_id=order.customer_id,
        order_id=order_id,
        amount=amount,
        payment_type=data.payment_type,
        payment_date=data.payment_date,
        reference=data.reference,
        notes=data.notes or f"دفعة مقدمة على طلب {order.code}",
        created_by=int(current_user.get("sub", 0)) or None,
    )
    db.add(pay_record)

    # تحديث رصيد العميل (دفعة تقلص الرصيد المديون)
    cust = await db.get(Customer, order.customer_id)
    if cust:
        cust.balance = cust.balance - amount

    await db.flush()  # نحتاج pay_record.id للخزنة

    # ربط بالخزنة — التحقق اختياري (إذا حُدِّدت خزنة بعينها)
    if data.treasury_id is not None:
        from app.models.models import Treasury
        treasury = await db.get(Treasury, data.treasury_id)
        if not treasury:
            raise HTTPException(404, "الخزنة غير موجودة")

    txn = await record_payment_in_treasury(
        db,
        amount=amount,
        direction="in",
        source="customer_payment",
        linked_type="customer_payment",
        linked_id=pay_record.id,
        category_name="دفعة مقدمة من عملاء",
        description=f"دفعة مقدمة — طلب {order.code}",
        reference=data.reference,
        txn_date=data.payment_date,
    )
    if txn is not None:
        from app.models.models import Treasury
        treasury_acc = await db.get(Treasury, txn.treasury_id)
        if treasury_acc and treasury_acc.coa_account_id:
            from app.services.auto_journal import journal_customer_advance
            await journal_customer_advance(
                db, tid, pay_record.id,
                cust.name if cust else str(order.customer_id),
                amount, treasury_acc.coa_account_id, data.payment_date,
            )

    await db.commit()
    await db.refresh(pay_record)
    return {
        "id": pay_record.id,
        "amount": float(pay_record.amount),
        "order_id": order_id,
        "customer_balance": float(cust.balance) if cust else None,
    }


# ══ PRINT ENDPOINTS ═══════════════════════════════════════════

def number_to_arabic_words(n: float, currency: str = "جنيه") -> str:
    """تحويل الرقم لكلمات عربية مبسطة"""
    import html as _html
    try:
        n = round(n, 2)
        integer = int(n)
        cents = round((n - integer) * 100)
        result = f"{integer:,} {_html.escape(currency)}"
        if cents > 0:
            result += f" و{cents}/100"
        return result
    except Exception:
        return str(n)


def _esc(s) -> str:
    """HTML-escape a value safely — prevents XSS"""
    import html as _html
    return _html.escape(str(s or ""))




def build_print_html(doc_type: str, doc: dict, company: dict, schedule: list = None) -> str:  # noqa: C901
    is_quotation = doc_type == "quotation"
    # force-reload marker
    title = "عرض أسعار" if is_quotation else "فاتورة مبيعات"

    accent    = company.get("brand_color", "#E87722")
    accent_dk = "#B85A10"

    status_map = {
        "draft":    ("مسودة",        "#888"),
        "sent":     ("مُرسل",        "#185FA5"),
        "accepted": ("مقبول",        "#1a7a3c"),
        "rejected": ("مرفوض",        "#A32D2D"),
        "unpaid":   ("غير مدفوعة",   "#854F0B"),
        "partial":  ("جزئي",         "#185FA5"),
        "paid":     ("مدفوعة",       "#1a7a3c"),
    }
    st_label, st_color = status_map.get(doc.get("status", ""), ("—", "#888"))

    # ── Lines HTML (items table) ─────────────────────────────────
    lines_html = ""
    for i, line in enumerate(doc.get("lines", [])):
        type_cell  = _esc(line.get("item_type", "") or "بند")
        model_cell = _esc(line.get("model_no",  "") or "")
        desc       = line["description"]
        raw_notes  = line.get("notes") or ""
        img_html   = ""
        notes_text = raw_notes
        if raw_notes.startswith("IMG:"):
            sep        = raw_notes.find("||")
            img_data   = raw_notes[4:sep] if sep > 0 else raw_notes[4:]
            notes_text = raw_notes[sep + 2:] if sep > 0 else ""
            # XSS: السماح فقط بـ data URIs للصور — رفض أي مصدر آخر
            if img_data.startswith("data:image/"):
                img_html = f'<img src="{_esc(img_data)}" class="row-img"/>'
        if not img_html:
            line_img = line.get("image_data") or ""
            if line_img.startswith("data:image/"):
                img_html = f'<img src="{_esc(line_img)}" class="row-img"/>'
        clean_notes = notes_text.split("تكلفة:")[0].strip() if notes_text else ""

        specs_lines = [_esc(t) for t in (desc or "").split("\n") if t.strip()]
        if clean_notes:
            specs_lines.append(_esc(clean_notes))
        if specs_lines:
            specs_html = '<ul class="specs-list">' + "".join(f"<li>{s}</li>" for s in specs_lines) + "</ul>"
        else:
            specs_html = '<span style="color:#ccc">—</span>'

        lines_html += (
            '<tr>'
            f'<td>{i + 1}</td>'
            f'<td>{img_html or "—"}</td>'
            f'<td class="col-name">{type_cell}</td>'
            f'<td>{model_cell or "—"}</td>'
            f'<td class="col-specs">{specs_html}</td>'
            f'<td>{line["qty"]} {_esc(line["unit"])}</td>'
            f'<td>{line["unit_price"]:,.0f}</td>'
            f'<td class="price">{line["total_price"]:,.0f}</td>'
            '</tr>'
        )

    # ── Totals ────────────────────────────────────────────────
    subtotal  = doc.get("subtotal", 0)
    disc_pct  = doc.get("discount_pct", 0)
    disc_amt  = doc.get("discount_amt", 0)
    tax_pct   = doc.get("tax_pct", 0)
    tax_amt   = doc.get("tax_amount", 0)
    total     = doc.get("total", 0)
    paid      = doc.get("paid_amount", 0)
    remaining = doc.get("remaining", total)
    currency  = company.get("currency", "جنيه مصري")

    # JS vars — use json.dumps for safe embedding (handles backslashes, newlines, </script>, etc.)
    import json as _json
    # XSS: json.dumps لوحده مابيهربش "</script>" — نهرّب < > & كـ unicode escapes
    def _jsd(v):
        return (_json.dumps(v).replace("<", "\\u003c").replace(">", "\\u003e")
                .replace("&", "\\u0026"))
    js_customer = _jsd(doc.get("customer_name") or "")
    js_code     = _jsd(doc.get("code") or "")
    js_total    = _jsd(f"{total:,.0f} {currency}")
    js_company  = _jsd(company.get("name") or "")
    js_date     = _jsd(str(doc.get("date") or ""))

    totals_rows = (
        f'<div class="totals-row">'
        f'<span class="totals-label">الإجمالي قبل الخصم</span>'
        f'<span class="totals-value">{subtotal:,.0f}</span></div>'
    )
    if disc_pct > 0:
        totals_rows += (
            f'<div class="totals-row">'
            f'<span class="totals-label">الخصم ({disc_pct}%)</span>'
            f'<span class="totals-value disc">−{disc_amt:,.0f}</span></div>'
        )
    if tax_pct > 0:
        totals_rows += (
            f'<div class="totals-row">'
            f'<span class="totals-label">ضريبة ({tax_pct}%)</span>'
            f'<span class="totals-value tax">+{tax_amt:,.0f}</span></div>'
        )
    if not is_quotation and paid > 0:
        totals_rows += (
            f'<div class="totals-row">'
            f'<span class="totals-label" style="color:#1a7a3c">المدفوع</span>'
            f'<span class="totals-value" style="color:#1a7a3c">{paid:,.0f}</span></div>'
            f'<div class="totals-row">'
            f'<span class="totals-label" style="color:#854F0B;font-weight:700">المتبقي</span>'
            f'<span class="totals-value" style="color:#854F0B">{remaining:,.0f}</span></div>'
        )

    # ── Logo ──────────────────────────────────────────────────
    logo_src  = company.get("logo_b64") or company.get("logo_url")

    # ── Legal / dates ─────────────────────────────────────────
    legal_parts = []
    if company.get("tax_id"):         legal_parts.append(f'ب.ض: {_esc(company["tax_id"])}')
    if company.get("commercial_reg"): legal_parts.append(f'س.ت: {_esc(company["commercial_reg"])}')
    legal_html = " | ".join(legal_parts)

    sub_date = ""
    if is_quotation and doc.get("valid_until"):
        sub_date = f'العرض صالح حتى: <b>{_esc(doc["valid_until"])}</b>'
    elif not is_quotation and doc.get("due_date"):
        sub_date = f'تاريخ الاستحقاق: <b style="color:#854F0B">{_esc(doc["due_date"])}</b>'

    quote_title_html = ""
    if is_quotation and str(doc.get("notes", "")).startswith("عنوان:"):
        qt = _esc(doc["notes"].replace("عنوان:", "").strip())
        quote_title_html = (
            f'<div style="text-align:center;font-size:17px;font-weight:800;'
            f'color:#1a1a1a;margin-bottom:16px;text-decoration:underline;">{qt}</div>'
        )

    notes_box = ""
    if doc.get("notes") and not str(doc.get("notes", "")).startswith("عنوان:"):
        notes_box = f'<div class="notes-box"><b>ملاحظات:</b><br>{_esc(doc["notes"])}</div>'
    terms_box = (f'<div class="notes-box" style="margin-top:8px">'
                 f'<b>شروط الدفع:</b><br>{_esc(doc["terms"])}</div>') if doc.get("terms") else ""

    # ── جدول السداد (الأقساط) — يظهر فقط لو الفاتورة عندها خطة ──
    schedule_html = ""
    if schedule and not is_quotation:
        STATUS_AR = {"pending": "قادم", "partial": "جزئي", "paid": "مدفوع", "overdue": "متأخر"}
        STATUS_CLR = {"pending": "#1D4ED8", "partial": "#D97706", "paid": "#15803D", "overdue": "#991B1B"}
        rows_s = ""
        for ins in schedule:
            st = ins.status
            st_label_s = STATUS_AR.get(st, st)
            st_color_s = STATUS_CLR.get(st, "#555")
            remaining_s = float(ins.amount_due) - float(ins.paid_amount)
            rows_s += (
                f"<tr style='border-bottom:1px solid #f0f0f0'>"
                f"<td style='padding:7px 10px;text-align:center'>{ins.installment_no}</td>"
                f"<td style='padding:7px 10px'>{_esc(ins.label or '')}</td>"
                f"<td style='padding:7px 10px;text-align:center'>{ins.due_date}</td>"
                f"<td style='padding:7px 10px;text-align:left;font-weight:700'>{float(ins.amount_due):,.2f}</td>"
                f"<td style='padding:7px 10px;text-align:left;color:#1a7a3c'>{float(ins.paid_amount):,.2f}</td>"
                f"<td style='padding:7px 10px;text-align:left;color:#854F0B;font-weight:700'>{remaining_s:,.2f}</td>"
                f"<td style='padding:7px 10px;text-align:center'>"
                f"<span style='background:{st_color_s};color:#fff;border-radius:4px;padding:2px 8px;font-size:11px'>{st_label_s}</span>"
                f"</td>"
                f"</tr>"
            )
        schedule_html = f"""
<div style="margin-top:18px;page-break-inside:avoid;">
  <div style="font-weight:800;font-size:13px;margin-bottom:8px;color:#1a1a1a;border-right:4px solid {accent};padding-right:8px;">
    جدول السداد المتفق عليه
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:12px;direction:rtl;">
    <thead>
      <tr style="background:{accent};color:#fff;">
        <th style="padding:8px 10px;text-align:center">قسط</th>
        <th style="padding:8px 10px">التسمية</th>
        <th style="padding:8px 10px;text-align:center">تاريخ الاستحقاق</th>
        <th style="padding:8px 10px;text-align:left">المبلغ</th>
        <th style="padding:8px 10px;text-align:left">المدفوع</th>
        <th style="padding:8px 10px;text-align:left">المتبقي</th>
        <th style="padding:8px 10px;text-align:center">الحالة</th>
      </tr>
    </thead>
    <tbody>{rows_s}</tbody>
  </table>
</div>"""

    amount_words = (f'<div class="amount-words">فقط: {number_to_arabic_words(total, currency)}</div>'
                    if total > 0 else "")

    footer_items = " &nbsp;|&nbsp; ".join(filter(None, [
        f'📞 {_esc(company.get("phone"))}' if company.get("phone") else "",
        f'✉️ {_esc(company.get("email"))}' if company.get("email") else "",
        f'🌐 {_esc(company.get("website"))}' if company.get("website") else "",
        f'📍 {_esc(company.get("address"))}' if company.get("address") else "",
        legal_html,
    ]))

    # توقيعات العميل/الشركة: لا تظهر في عرض السعر (التوقيع الفعلي في عقد الاتفاق)
    sig_row = "" if is_quotation else (
        '<div class="sig-row">'
        '<div class="sig-box"><div class="sig-box-title">اعتماد العميل</div>'
        '<div class="sig-line">التوقيع والختم</div></div>'
        '<div class="sig-box"><div class="sig-box-title">اعتماد الشركة</div>'
        '<div class="sig-line">التوقيع والختم</div></div>'
        '</div>'
    )

    terms_col = ""
    if is_quotation:
        # شروط وأحكام عامة من ملف الشركة، تُعرض كعمود ثالث بجانب الإجمالي
        company_terms = _esc(company.get("report_footer") or "")
        if company_terms:
            terms_col = (
                '<div class="notes-box" style="height:100%;">'
                '<b>شروط العرض والتعاقد</b>'
                f'<div style="font-size:11px;color:#444;line-height:1.7;white-space:pre-wrap;">{company_terms}</div>'
                '</div>'
            )

    # ── JavaScript block (no f-string to avoid truncation) ────
    js_block = (
        "<script>\n"
        "function downloadPDF() {\n"
        f"  document.title = {js_code};\n"
        "  window.print();\n"
        "  setTimeout(function(){ document.title = document.querySelector('title').dataset.orig || document.title; }, 2000);\n"
        "}\n\n"
        "function shareWhatsApp() {\n"
        f"  var customer = {js_customer};\n"
        f"  var code     = {js_code};\n"
        f"  var total    = {js_total};\n"
        f"  var company  = {js_company};\n"
        f"  var docDate  = {js_date};\n"
        "  var link = window.location.href;\n"
        "  var msg = 'السادة / ' + customer + ' \\u{1F44B}\\n\\n';\n"
        "  msg += 'يسعدنا تقديم عرض الأسعار التالي:\\n';\n"
        "  msg += '\\u2501\\u2501\\u2501\\u2501\\u2501\\u2501\\u2501\\u2501\\n';\n"
        "  msg += '\\uD83D\\uDCCB رقم العرض: ' + code + '\\n';\n"
        "  msg += '\\uD83D\\uDCC5 التاريخ: ' + docDate + '\\n';\n"
        "  msg += '\\uD83D\\uDCB0 إجمالي السعر: ' + total + '\\n';\n"
        "  msg += '\\u2501\\u2501\\u2501\\u2501\\u2501\\u2501\\u2501\\u2501\\n';\n"
        "  msg += 'التفاصيل الكاملة:\\n' + link + '\\n\\n';\n"
        "  msg += 'مع تحيات ' + company;\n"
        "  window.open('https://wa.me/?text=' + encodeURIComponent(msg), '_blank');\n"
        "}\n\n"
        "function copyLink(btn) {\n"
        "  navigator.clipboard.writeText(window.location.href).then(function() {\n"
        "    var orig = btn.textContent;\n"
        "    btn.textContent = '\\u2705 تم النسخ!';\n"
        "    btn.style.background = '#059669';\n"
        "    setTimeout(function() { btn.textContent = orig; btn.style.background = ''; }, 2000);\n"
        "  });\n"
        "}\n"
        "</script>\n"
    )

    cname   = _esc(company.get("name", "الشركة"))
    ctagline= _esc(company.get("sub", ""))
    cphone  = _esc(company.get("phone", ""))
    cemail  = _esc(company.get("email", ""))
    caddr   = _esc(company.get("address", ""))

    party_from = (
        '<div class="party-label">مُقدَّم من</div>'
        f'<div class="party-name">{cname}</div>'
        + "".join(f'<div class="party-detail">{v}</div>' for v in [cphone, cemail, caddr] if v)
    )
    cust_phone = _esc(doc.get("customer_phone") or "")
    cust_addr  = _esc(doc.get("customer_address") or "")
    cust_tax   = _esc(doc.get("customer_tax_id") or "")
    cust_extra = [cust_phone, cust_addr]
    if cust_tax:
        cust_extra.append(f"الرقم الضريبي: {cust_tax}")
    party_to = (
        '<div class="party-label">السادة /</div>'
        f'<div class="party-name">{_esc(doc.get("customer_name",""))}</div>'
        + "".join(f'<div class="party-detail">{v}</div>' for v in cust_extra if v)
    )

    # ── Logo HTML — white-background box so any logo format works ──
    if logo_src:
        logo_html = (
            f'<img src="{_esc(logo_src)}" alt="logo" '
            f'style="max-height:90px;max-width:180px;object-fit:contain;display:block;"/>'
        )
    else:
        logo_html = ""

    html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<title>{title} — {_esc(doc.get("code") or "")}</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
:root{{--navy:#0C2340;--navy-dk:#081828;--gold:#C8A550;--gold-lt:rgba(200,165,80,.12);--gold-bd:rgba(200,165,80,.3);--surface:#F8F7F4;--border:#E8E6E0;--text:#1A1A2E;--muted:#7A7870;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Cairo',Tahoma,Arial,sans-serif;font-size:13px;color:var(--text);background:#EDECEA;direction:rtl;line-height:1.4;}}
@page{{size:A4 landscape;margin:6mm 10mm;}}
@media print{{
  .no-print{{display:none!important;}}
  body{{-webkit-print-color-adjust:exact;print-color-adjust:exact;background:#fff;}}
  .page{{max-width:100%;box-shadow:none;border-radius:0;margin:0;}}
}}
.page{{max-width:1120px;margin:16px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 60px rgba(0,0,0,.13);}}

/* ── Header (company info only) ── */
.doc-header{{
  background:linear-gradient(135deg,var(--navy-dk) 0%,var(--navy) 60%,#143558 100%);
  color:#fff;padding:12px 30px;
  display:flex;justify-content:space-between;align-items:center;
  position:relative;overflow:hidden;
}}
.doc-header::after{{
  content:'';position:absolute;top:0;left:0;right:0;bottom:0;
  background:url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.02'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
  pointer-events:none;
}}
.company-block{{display:flex;align-items:center;gap:18px;position:relative;z-index:1;text-align:right;}}
.logo-wrap{{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:6px;}}
.company-name{{font-size:22px;font-weight:800;line-height:1.2;letter-spacing:-.3px;}}
.company-tagline{{font-size:11px;color:rgba(255,255,255,.5);margin-top:3px;font-weight:400;}}
.company-phone{{font-size:12px;color:var(--gold);margin-top:6px;font-weight:600;}}

/* ── Accent stripe ── */
.accent-stripe{{height:4px;background:linear-gradient(90deg,var(--gold) 0%,#E8C060 50%,var(--gold) 100%);}}

/* ── Quote info bar (document type, number, date, status) ── */
.status-bar{{
  background:#0D2440;padding:6px 30px;
  display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:10px;
}}
.doc-type{{font-size:20px;font-weight:900;color:var(--gold);line-height:1;letter-spacing:-.5px;text-align:center;justify-self:center;}}
.doc-badges{{display:flex;align-items:center;gap:8px;justify-self:start;}}
.doc-badge{{display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 12px;}}
.doc-badge-label{{font-size:11px;color:rgba(255,255,255,.5);}}
.doc-badge-val{{font-size:12.5px;color:#fff;font-weight:700;}}
.status-right{{display:flex;align-items:center;gap:14px;justify-self:end;}}
.status-pill{{
  display:inline-flex;align-items:center;gap:6px;
  background:rgba(78,202,160,.15);border:1px solid rgba(78,202,160,.3);
  color:#4ECAA0;font-size:11px;font-weight:700;
  padding:3px 14px;border-radius:99px;letter-spacing:.03em;
}}
.status-pill-warn{{background:rgba(200,165,80,.15);border-color:var(--gold-bd);color:var(--gold);}}
.status-pill-danger{{background:rgba(226,96,96,.15);border-color:rgba(226,96,96,.3);color:#E26060;}}
.status-pill-gray{{background:rgba(150,150,150,.15);border-color:rgba(150,150,150,.3);color:#aaa;}}
.status-dot{{width:6px;height:6px;background:currentColor;border-radius:50%;}}
.valid-date{{font-size:11px;color:rgba(255,255,255,.4);}}
.valid-date b{{color:rgba(255,255,255,.7);}}

/* ── Body ── */
.body{{padding:8px 24px;}}

/* ── Parties ── */
.parties{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:6px;}}
.party-box{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:8px 14px;border-top:3px solid var(--gold);}}
.party-label{{font-size:9px;font-weight:800;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;margin-bottom:4px;}}
.party-name{{font-size:13px;font-weight:800;color:var(--text);margin-bottom:2px;}}
.party-detail{{font-size:10.5px;color:var(--muted);line-height:1.5;}}

/* ── Items table ── */
.items-table{{width:100%;border-collapse:collapse;margin-bottom:6px;font-size:11.5px;table-layout:fixed;}}
.items-table th{{background:var(--navy);color:#fff;padding:6px 8px;font-size:10.5px;font-weight:700;text-align:center;}}
.items-table td{{padding:5px 8px;border-bottom:1px solid var(--border);vertical-align:middle;text-align:center;word-wrap:break-word;overflow-wrap:anywhere;}}
.items-table tbody tr:nth-child(even){{background:#FAFAF8;}}
.items-table .col-name{{text-align:right;font-weight:700;}}
.items-table .col-specs{{text-align:right;font-size:11px;color:#444240;line-height:1.55;}}
.items-table .specs-list{{margin:0;padding:0 14px 0 0;}}
.items-table .specs-list li{{margin-bottom:1px;}}
.items-table .row-img{{width:92px;height:76px;object-fit:cover;border-radius:5px;border:1px solid var(--border);}}
.items-table .price{{font-weight:800;color:var(--navy);}}
.items-table th:nth-child(1),.items-table td:nth-child(1){{width:28px;}}
.items-table th:nth-child(2),.items-table td:nth-child(2){{width:100px;}}
.items-table th:nth-child(3),.items-table td:nth-child(3){{width:120px;}}
.items-table th:nth-child(4),.items-table td:nth-child(4){{width:80px;}}
.items-table th:nth-child(6),.items-table td:nth-child(6){{width:75px;}}
.items-table th:nth-child(7),.items-table td:nth-child(7){{width:80px;}}
.items-table th:nth-child(8),.items-table td:nth-child(8){{width:90px;}}

/* ── Totals + notes ── */
.bottom-section{{display:grid;grid-template-columns:1fr 1fr 220px;gap:10px;margin-bottom:4px;align-items:stretch;}}
.notes-box{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:8px 12px;font-size:11px;color:#3A3830;margin-bottom:6px;}}
.notes-box b{{font-size:9.5px;font-weight:800;color:var(--muted);letter-spacing:.08em;text-transform:uppercase;display:block;margin-bottom:3px;}}
.amount-words{{background:var(--gold-lt);border:1px solid var(--gold-bd);border-radius:10px;padding:7px 12px;font-size:11.5px;color:#7A5A10;font-weight:700;margin-bottom:6px;}}
.totals-box{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:10px;}}
.totals-row{{display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px dashed var(--border);font-size:11.5px;}}
.totals-row:last-child{{border-bottom:none;}}
.totals-label{{color:var(--muted);}}
.totals-value{{font-weight:600;color:var(--text);font-variant-numeric:tabular-nums;}}
.totals-value.disc{{color:#C0392B;}}
.totals-value.tax{{color:var(--muted);}}
.grand-row{{
  margin-top:8px;background:linear-gradient(135deg,var(--navy-dk),var(--navy));
  border-radius:10px;padding:8px 14px;
  display:flex;justify-content:space-between;align-items:center;
}}
.grand-label{{font-size:12px;font-weight:600;color:rgba(255,255,255,.65);}}
.grand-value{{font-size:19px;font-weight:900;color:var(--gold);letter-spacing:-.5px;}}
.grand-currency{{font-size:11px;font-weight:600;color:rgba(200,165,80,.6);margin-left:5px;}}

/* ── Signature row ── */
.sig-row{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:10px;}}
.sig-box{{border-top:2px solid var(--border);padding-top:6px;text-align:center;}}
.sig-box-title{{font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.07em;margin-bottom:14px;}}
.sig-line{{border-top:1px solid #CCC;margin:0 20px;padding-top:4px;font-size:11px;color:#aaa;}}

/* ── Footer ── */
.doc-footer{{
  background:linear-gradient(90deg,var(--navy-dk),var(--navy));
  color:rgba(255,255,255,.6);padding:5px 30px;text-align:center;font-size:11px;
}}
.doc-footer b{{color:var(--gold);}}

/* ── Terms page ── */
.terms-title{{font-size:15px;font-weight:800;color:var(--navy);border-bottom:2px solid var(--gold);padding-bottom:7px;margin-bottom:16px;}}

/* ── Action bar ── */
.action-bar{{
  position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
  display:flex;gap:8px;z-index:99;
  background:rgba(8,24,40,.85);backdrop-filter:blur(12px);
  border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:8px 12px;
}}
.action-bar button{{
  padding:8px 18px;border-radius:8px;font-size:13px;font-weight:700;
  cursor:pointer;border:none;font-family:'Cairo',sans-serif;transition:opacity .15s;
}}
.action-bar button:hover{{opacity:.85;}}
.btn-print{{background:var(--gold);color:#fff;}}
.btn-wa{{background:#25D366;color:#fff;}}
.btn-copy{{background:rgba(255,255,255,.1);color:rgba(255,255,255,.8);border:1px solid rgba(255,255,255,.15)!important;}}
</style>
</head>
<body onload="setTimeout(()=>window.print(),400)">
<div class="page" id="printable">

  <div class="doc-header">
    <div class="company-block">
      <div>
        <div class="company-name">{cname}</div>
        <div class="company-tagline">{ctagline}</div>
        <div class="company-phone">{cphone}</div>
      </div>
    </div>
    {'<div class="logo-wrap">' + logo_html + '</div>' if logo_html else ''}
  </div>
  <div class="accent-stripe"></div>

  <div class="status-bar">
    <div class="doc-badges">
      <div class="doc-badge">
        <span class="doc-badge-label">رقم:</span>
        <span class="doc-badge-val">{_esc(doc.get("code") or "")}</span>
      </div>
      <div class="doc-badge">
        <span class="doc-badge-label">التاريخ:</span>
        <span class="doc-badge-val">{_esc(str(doc.get("date") or ""))}</span>
      </div>
    </div>
    <div class="doc-type">{title}</div>
    <div class="status-right">
      <span class="valid-date">{sub_date}</span>
      <span class="status-pill {'' if doc.get('status') in ('accepted','paid') else ('status-pill-warn' if doc.get('status') in ('sent','partial','unpaid') else ('status-pill-danger' if doc.get('status')=='rejected' else 'status-pill-gray'))}">
        <span class="status-dot"></span>{st_label}
      </span>
    </div>
  </div>

  <div class="body">

    <div class="parties">
      <div class="party-box">{party_from}</div>
      <div class="party-box">{party_to}</div>
    </div>

    {quote_title_html}

    <table class="items-table">
      <thead>
        <tr>
          <th>م</th><th>الصورة</th><th>اسم المعدة</th><th>الموديل</th>
          <th>المواصفات الفنية</th><th>الكمية</th><th>سعر الوحدة</th><th>الإجمالي {currency}</th>
        </tr>
      </thead>
      <tbody>{lines_html}</tbody>
    </table>

    <div class="bottom-section">
      <div class="notes-area">
        {amount_words}
        {notes_box}
        {terms_box}
        {schedule_html}
      </div>
      {terms_col}
      <div class="totals-box">
        {totals_rows}
        <div class="grand-row">
          <span class="grand-label">إجمالي السعر</span>
          <span class="grand-value"><span class="grand-currency">{currency}</span>{total:,.0f}</span>
        </div>
      </div>
    </div>

    {sig_row}

  </div>

  <div class="doc-footer">
    {footer_items if footer_items else f'<b>{cname}</b>'}
  </div>

</div>

<div class="action-bar no-print">
  <button class="btn-print" onclick="window.print()">🖨️ طباعة / PDF</button>
  <button class="btn-wa"    onclick="shareWhatsApp()">💬 واتساب</button>
  <button class="btn-copy"  onclick="copyLink(this)">🔗 نسخ الرابط</button>
</div>

{js_block}
</body></html>"""
    return html


@print_router.get("/quotations/{qid}/print", response_class=HTMLResponse)
async def print_quotation(qid: int, db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(Quotation).options(selectinload(Quotation.customer), selectinload(Quotation.lines), selectinload(Quotation.location))
        .where(Quotation.id == qid)
    )
    q = r.scalar_one_or_none()
    if not q: raise HTTPException(404)
    company = await get_company_info(db)
    return build_print_html("quotation", quot_dict(q), company)


@print_router.get("/invoices/{iid}/print", response_class=HTMLResponse)
async def print_invoice(iid: int, db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(Invoice).options(selectinload(Invoice.customer), selectinload(Invoice.lines))
        .where(Invoice.id == iid)
    )
    inv = r.scalar_one_or_none()
    if not inv: raise HTTPException(404)

    # جلب خطة الأقساط (لو موجودة)
    schedule_res = await db.execute(
        select(InvoicePaymentSchedule)
        .where(InvoicePaymentSchedule.invoice_id == iid)
        .order_by(InvoicePaymentSchedule.installment_no)
    )
    schedule = schedule_res.scalars().all()

    company = await get_company_info(db)
    return build_print_html("invoice", inv_dict(inv), company, schedule=schedule)


# ── عقد الاتفاق — يُولَّد من عرض سعر ────────────────────────────────
@print_router.get("/quotations/{qid}/agreement", response_class=HTMLResponse)
async def print_agreement(qid: int, db: AsyncSession = Depends(get_db)):
    """طباعة عقد الاتفاق من عرض سعر"""
    import html as _h

    r = await db.execute(
        select(Quotation).options(selectinload(Quotation.customer), selectinload(Quotation.lines), selectinload(Quotation.location))
        .where(Quotation.id == qid)
    )
    q = r.scalar_one_or_none()
    if not q:
        raise HTTPException(404)

    co = await get_company_info(db)
    esc = lambda s: _h.escape(str(s or ""))

    co_name  = esc(co["name"])
    co_sub   = esc(co["sub"])
    co_phone = esc(co["phone"])
    co_addr  = esc(co["address"])
    co_tax   = esc(co["tax_id"])
    co_reg   = esc(co["commercial_reg"])
    logo_b64 = co.get("logo_b64")
    logo_html = (
        f'<img src="{esc(logo_b64)}" alt="logo" style="max-height:80px;max-width:160px;object-fit:contain;"/>'
        if logo_b64 else ""
    )

    cust_name  = esc(q.customer.name    if q.customer else "—")
    cust_phone = esc(q.customer.phone   if q.customer else "")
    cust_addr  = esc(q.customer.address if q.customer else "")
    cust_tax   = esc(q.customer.tax_id  if q.customer else "")

    code_v   = esc(q.code)
    date_v   = esc(str(q.date))
    valid_v  = esc(str(q.valid_until)) if q.valid_until else "غير محدد"
    notes_v  = esc(q.notes  or "")
    terms_v  = esc(q.terms  or "")

    # بنود
    rows_html = ""
    for i, l in enumerate(sorted(q.lines, key=lambda x: x.sort_order or 0)):
        img_html = ""
        ln = l.notes or ""
        if ln.startswith("IMG:"):
            sep = ln.find("||")
            b64 = ln[4:sep] if sep > 0 else ln[4:]
            img_html = "" if not b64.startswith("data:image/") else f'<img src="{esc(b64)}" style="width:60px;height:46px;object-fit:contain;border-radius:4px;vertical-align:middle;margin-left:6px;"/>'
        desc_text = esc(l.description)
        model_text = f"<br/><small style='color:#888;font-size:10px'>{esc(l.model_no)}</small>" if l.model_no else ""
        type_text  = f"<div style='font-size:10px;color:#555;margin-bottom:2px'>{esc(l.item_type)}</div>" if l.item_type else ""
        bg = "#fafafa" if i % 2 == 0 else "#fff"
        rows_html += (
            f"<tr style='background:{bg}'>"
            f"<td style='padding:9px 10px;text-align:center;color:#999;font-size:12px'>{i+1}</td>"
            f"<td style='padding:9px 10px'>{type_text}{img_html}<b>{desc_text}</b>{model_text}</td>"
            f"<td style='padding:9px 10px;text-align:center'>{float(l.qty):g} {esc(l.unit)}</td>"
            f"<td style='padding:9px 10px;text-align:left;font-variant-numeric:tabular-nums'>{float(l.unit_price):,.2f}</td>"
            f"<td style='padding:9px 10px;text-align:left;font-weight:700;font-variant-numeric:tabular-nums'>{float(l.total_price):,.2f}</td>"
            "</tr>"
        )

    sub_v  = float(q.subtotal)
    disc_v = float(q.discount_amt)
    tax_v  = float(q.tax_amount)
    tot_v  = float(q.total)
    tax_pct = float(q.tax_pct)
    disc_pct = float(q.discount_pct)

    totals_rows = f"<tr><td>الإجمالي قبل الخصم</td><td style='text-align:left;font-weight:600'>{sub_v:,.2f}</td></tr>"
    if disc_v > 0:
        totals_rows += f"<tr><td>خصم ({disc_pct:.0f}%)</td><td style='text-align:left;color:#c0392b'>({disc_v:,.2f})</td></tr>"
    if tax_v > 0:
        totals_rows += f"<tr><td>ضريبة ({tax_pct:.0f}%)</td><td style='text-align:left;color:#555'>+ {tax_v:,.2f}</td></tr>"

    legal_parts = []
    if co_tax: legal_parts.append(f"ب.ض: {co_tax}")
    if co_reg: legal_parts.append(f"س.ت: {co_reg}")
    legal_str = " | ".join(legal_parts)

    terms_html = f"<div style='white-space:pre-wrap;font-size:12px;line-height:1.7'>{terms_v}</div>" if terms_v else ""
    notes_html = f"<div style='white-space:pre-wrap;font-size:12px;line-height:1.7'>{notes_v}</div>" if notes_v else ""

    html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<title>عقد اتفاق — {code_v}</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
:root{{--navy:#0C2340;--gold:#C8A550;--surface:#F8F7F4;--border:#E8E6E0;--text:#1A1A2E;--muted:#7A7870;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Cairo',Tahoma,Arial,sans-serif;font-size:13px;color:var(--text);background:#EDECEA;direction:rtl;line-height:1.7;}}
@page{{size:A4;margin:10mm 8mm;}}
@media print{{.no-print{{display:none!important;}}body{{background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact;}}.page{{max-width:100%;box-shadow:none;border-radius:0;margin:0;}}}}
.page{{max-width:820px;margin:16px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 60px rgba(0,0,0,.13);}}
.doc-header{{background:linear-gradient(135deg,#081828 0%,var(--navy) 60%,#143558 100%);color:#fff;padding:22px 28px 18px;display:flex;justify-content:space-between;align-items:center;}}
.company-block{{display:flex;align-items:center;gap:16px;}}
.company-name{{font-size:20px;font-weight:800;}}
.company-sub{{font-size:11px;color:rgba(255,255,255,.5);margin-top:2px;}}
.company-phone{{font-size:12px;color:var(--gold);margin-top:5px;font-weight:600;}}
.doc-meta{{text-align:left;}}
.doc-type{{font-size:24px;font-weight:900;color:var(--gold);}}
.doc-badge{{display:inline-flex;align-items:center;gap:6px;margin-top:6px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:4px 12px;}}
.badge-label{{font-size:11px;color:rgba(255,255,255,.5);}}
.badge-val{{font-size:12px;color:#fff;font-weight:700;}}
.gold-stripe{{height:4px;background:linear-gradient(90deg,var(--gold),rgba(200,165,80,.4),var(--gold));}}
.body{{padding:22px 28px;}}
.section-title{{font-size:11px;font-weight:800;color:var(--muted);letter-spacing:.1em;margin-bottom:8px;border-bottom:1px solid var(--border);padding-bottom:5px;}}
.parties{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:18px;}}
.party-box{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 18px;border-top:3px solid var(--gold);}}
.party-label{{font-size:10px;font-weight:800;color:var(--muted);letter-spacing:.1em;margin-bottom:6px;}}
.party-name{{font-size:15px;font-weight:800;margin-bottom:4px;}}
.party-detail{{font-size:11.5px;color:var(--muted);}}
.intro-box{{background:#F0F4FF;border:1px solid #DBEAFE;border-radius:10px;padding:14px 18px;margin-bottom:18px;font-size:12.5px;line-height:1.9;}}
table.items{{width:100%;border-collapse:collapse;margin-bottom:18px;border-radius:10px;overflow:hidden;border:1px solid var(--border);}}
.items thead tr{{background:var(--navy);}}
.items thead th{{padding:9px 10px;font-size:11px;font-weight:700;color:rgba(255,255,255,.75);text-align:right;}}
.items thead th:last-child,.items thead th:nth-last-child(2){{text-align:left;}}
.items tbody td{{border-bottom:1px solid var(--border);font-size:12.5px;}}
.items tbody tr:last-child td{{border-bottom:none;}}
.totals-wrap{{display:flex;justify-content:flex-end;margin-bottom:18px;}}
table.totals{{width:240px;border-collapse:collapse;}}
.totals td{{padding:5px 8px;font-size:12.5px;border-bottom:1px dashed var(--border);}}
.grand-row{{background:linear-gradient(135deg,#081828,var(--navy));border-radius:10px;padding:11px 16px;display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;}}
.grand-label{{font-size:13px;font-weight:600;color:rgba(255,255,255,.65);}}
.grand-value{{font-size:21px;font-weight:900;color:var(--gold);font-variant-numeric:tabular-nums;}}
.clause-box{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 18px;margin-bottom:14px;}}
.clause-title{{font-size:11px;font-weight:800;color:var(--muted);letter-spacing:.1em;margin-bottom:7px;border-bottom:1px solid var(--border);padding-bottom:5px;}}
/* إقرار / توقيع */
.sig-section{{border:2px solid var(--navy);border-radius:12px;padding:18px 22px;margin-top:20px;}}
.sig-title{{font-size:13px;font-weight:900;color:var(--navy);text-align:center;margin-bottom:14px;padding-bottom:10px;border-bottom:1.5px solid var(--border);letter-spacing:.05em;}}
.sig-row{{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:4px;}}
.sig-box{{border:1px solid var(--border);border-radius:10px;padding:16px;text-align:center;}}
.sig-party{{font-size:13px;font-weight:800;margin-bottom:3px;}}
.sig-party-sub{{font-size:11px;color:var(--muted);margin-bottom:50px;}}
.sig-line{{border-top:1px solid #bbb;padding-top:6px;font-size:10px;color:#aaa;margin:0 10px;}}
.doc-footer{{background:linear-gradient(90deg,#081828,var(--navy));color:rgba(255,255,255,.55);padding:11px 28px;text-align:center;font-size:11px;}}
.action-bar{{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);display:flex;gap:8px;z-index:99;background:rgba(8,24,40,.85);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:8px 12px;}}
.action-bar button{{padding:8px 18px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer;border:none;font-family:'Cairo',sans-serif;}}
.btn-print{{background:var(--gold);color:#fff;}}
</style>
</head>
<body onload="setTimeout(()=>window.print(),500)">
<div class="page">

  <div class="doc-header">
    <div class="company-block">
      {f'<div style="background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px;">{logo_html}</div>' if logo_html else ""}
      <div>
        <div class="company-name">{co_name}</div>
        <div class="company-sub">{co_sub}</div>
        <div class="company-phone">{co_phone}</div>
      </div>
    </div>
    <div class="doc-meta">
      <div class="doc-type">عقد اتفاق</div>
      <div class="doc-badge"><span class="badge-label">رقم:</span><span class="badge-val">{code_v}</span></div>
      <div class="doc-badge" style="margin-top:5px;"><span class="badge-label">تاريخ:</span><span class="badge-val">{date_v}</span></div>
      {f'<div class="doc-badge" style="margin-top:5px;"><span class="badge-label">صالح حتى:</span><span class="badge-val">{valid_v}</span></div>'}
    </div>
  </div>
  <div class="gold-stripe"></div>

  <div class="body">

    <div class="parties">
      <div class="party-box">
        <div class="party-label">الطرف الأول — الشركة المورِّدة</div>
        <div class="party-name">{co_name}</div>
        {f'<div class="party-detail">{co_phone}</div>' if co_phone else ''}
        {f'<div class="party-detail">{co_addr}</div>' if co_addr else ''}
        {f'<div class="party-detail">{legal_str}</div>' if legal_str else ''}
      </div>
      <div class="party-box">
        <div class="party-label">الطرف الثاني — العميل</div>
        <div class="party-name">{cust_name}</div>
        {f'<div class="party-detail">{cust_phone}</div>' if cust_phone else ''}
        {f'<div class="party-detail">{cust_addr}</div>' if cust_addr else ''}
        {f'<div class="party-detail">الرقم الضريبي: {cust_tax}</div>' if cust_tax else ''}
      </div>
    </div>

    <div class="intro-box">
      اتفق الطرفان أدناه — الطرف الأول <b>{co_name}</b> والطرف الثاني <b>{cust_name}</b> — على تنفيذ توريد المعدات والمنتجات المبيّنة في الجدول أدناه وفق الأسعار والشروط المحددة، وقد وقّع الطرفان على هذا العقد إقراراً بالموافقة الكاملة على جميع بنوده.
    </div>

    <table class="items">
      <thead><tr>
        <th style="width:34px;text-align:center">#</th>
        <th>الصنف / المواصفات</th>
        <th style="width:110px;text-align:center">الكمية</th>
        <th style="width:115px;text-align:left">سعر الوحدة (ج.م)</th>
        <th style="width:115px;text-align:left">الإجمالي (ج.م)</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>

    <div class="totals-wrap">
      <div>
        <table class="totals"><tbody>{totals_rows}</tbody></table>
        <div class="grand-row" style="margin-top:8px;">
          <span class="grand-label">إجمالي قيمة العقد</span>
          <span class="grand-value">{tot_v:,.2f} ج.م</span>
        </div>
      </div>
    </div>

    {f'<div class="clause-box"><div class="clause-title">شروط الدفع والتسليم</div>{terms_html}</div>' if terms_v else ''}
    {f'<div class="clause-box"><div class="clause-title">ملاحظات</div>{notes_html}</div>' if notes_v else ''}

    <div class="sig-section">
      <div class="sig-title">إقرار بالموافقة وتوقيع الطرفين</div>
      <div class="sig-row">
        <div class="sig-box">
          <div class="sig-party">الطرف الأول — المورِّد</div>
          <div class="sig-party-sub">{co_name}</div>
          <div class="sig-line">الاسم والتوقيع والختم</div>
        </div>
        <div class="sig-box">
          <div class="sig-party">الطرف الثاني — العميل</div>
          <div class="sig-party-sub">{cust_name}</div>
          <div class="sig-line">الاسم والتوقيع والختم</div>
        </div>
      </div>
    </div>

  </div>

  <div class="doc-footer">{" | ".join(filter(None,[co_phone,co_addr,legal_str])) or co_name}</div>
</div>

<div class="action-bar no-print">
  <button class="btn-print" onclick="window.print()">🖨️ طباعة / PDF</button>
</div>
</body></html>"""
    return html
