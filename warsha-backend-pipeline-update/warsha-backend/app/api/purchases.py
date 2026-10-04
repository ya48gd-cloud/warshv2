"""
Purchases API — الموردون وفواتير الشراء
"""
import os
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional, List

import re
import html as _html_mod

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, field_validator

from app.core.database import get_db
from app.api.auth import require_any, get_current_user
from app.services.auto_journal import (
    journal_purchase_invoice_create, journal_purchase_invoice_payment,
    journal_purchase_invoice_delete,
)
from app.models.models import (
    Supplier, PurchaseInvoice, PurchaseInvoiceLine,
    FileAttachment, Material, WorkshopProfile, BarStockItem,
)

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


async def _block_viewer_writes(request: Request, user: dict = Depends(require_any)) -> dict:
    """إصلاح ثغرة صلاحيات: مستخدم "مشاهد" (viewer) كان يقدر ينشئ/يعدّل/يحذف
    موردين وفواتير شراء ويسجّل دفعات ويرفع/يحذف مرفقات بطلب API مباشر رغم إن
    الواجهة مخبية الأزرار (ROLE_WRITE['viewer'] = []). نفس نمط customers/sales."""
    if request.method.upper() not in _READ_METHODS and user.get("role") == "viewer":
        raise HTTPException(403, "صلاحية 'viewer' (مشاهد) لا تسمح بالتعديل")
    return user


router = APIRouter(prefix="/purchases", tags=["purchases"],
                   dependencies=[Depends(require_any), Depends(_block_viewer_writes)])
print_router = APIRouter(prefix="/purchases", tags=["purchases_print"], dependencies=[Depends(require_any)])


async def _next_supplier_code(db: AsyncSession, tid: Optional[int] = None) -> str:
    """يولّد كود مورد تلقائي — مُقيّد بنفس الـ tenant (uq_supplier_tenant_code
    فريد على (tenant_id, code) مش على code وحده، فلازم العدّ والفحص يبقوا
    بنفس النطاق بدل ما يتخطوا أكواد متاحة فعلاً بسبب مورد لـ tenant تاني)."""
    q_count = select(func.count()).select_from(Supplier)
    if tid is not None:
        q_count = q_count.where(Supplier.tenant_id == tid)
    count = await db.scalar(q_count)
    n = (count or 0) + 1
    while True:
        code = f"SUP-{n:04d}"
        q_exists = select(Supplier.id).where(Supplier.code == code)
        if tid is not None:
            q_exists = q_exists.where(Supplier.tenant_id == tid)
        exists = await db.scalar(q_exists)
        if not exists:
            return code
        n += 1


async def _next_internal_code(db: AsyncSession, tid: int) -> str:
    """يولّد كود داخلي تلقائي للفاتورة: PI-YYYY-XXXX"""
    from datetime import date as _d
    year = _d.today().year
    count = await db.scalar(
        select(func.count()).select_from(PurchaseInvoice)
        .where(PurchaseInvoice.tenant_id == tid)
    )
    n = (count or 0) + 1
    # العدّ وحده بيولّد كود مكرر بعد حذف أي فاتورة (مثلاً فاتورتين، اتحذفت
    # الأولى → العدد 1 → PI-YYYY-0002 تاني) فيقع الإنشاء بـ IntegrityError
    # (500) على uq_purchaseinvoice_tenant_code لو الكود اتستخدم كـ code.
    # نتخطى أي كود مستخدم فعلاً (كـ code أو internal_code) لنفس الـ tenant.
    while True:
        candidate = f"PI-{year}-{n:04d}"
        taken = await db.scalar(
            select(PurchaseInvoice.id).where(
                PurchaseInvoice.tenant_id == tid,
                (PurchaseInvoice.code == candidate) | (PurchaseInvoice.internal_code == candidate),
            ).limit(1)
        )
        if not taken:
            return candidate
        n += 1


async def _invoice_code_taken(db: AsyncSession, tid: int, code: str, exclude_id: Optional[int] = None) -> bool:
    """هل كود فاتورة الشراء مستخدم لنفس الـ tenant؟ (uq_purchaseinvoice_tenant_code)
    — بدل ما التكرار يوصل للـ DB ويرجع 500 IntegrityError."""
    q = select(PurchaseInvoice.id).where(
        PurchaseInvoice.tenant_id == tid, PurchaseInvoice.code == code,
    )
    if exclude_id is not None:
        q = q.where(PurchaseInvoice.id != exclude_id)
    return bool(await db.scalar(q.limit(1)))


def _validate_invoice_header(data) -> None:
    """تحقق من رأس فاتورة الشراء: الضريبة والخصم والتواريخ.
    كان ممكن تمرير tax_pct سالب أو خصم أكبر من الإجمالي، فيطلع total سالب
    ويتسجل قيد محاسبي معكوس ويقل رصيد المورد بدل ما يزيد."""
    if data.tax_pct is not None and (data.tax_pct < 0 or data.tax_pct > 100):
        raise HTTPException(400, "نسبة الضريبة يجب أن تكون بين 0 و 100")
    if data.discount_amt is not None and data.discount_amt < 0:
        raise HTTPException(400, "الخصم لا يمكن أن يكون سالباً")
    if data.due_date and data.date and data.due_date < data.date:
        raise HTTPException(400, "تاريخ الاستحقاق يجب أن يكون بعد تاريخ الفاتورة")
    subtotal = sum(Decimal(str(l.qty)) * Decimal(str(l.unit_cost)) for l in data.lines)
    if Decimal(str(data.discount_amt or 0)) > subtotal:
        raise HTTPException(400, "الخصم لا يمكن أن يتجاوز إجمالي البنود")

UPLOAD_DIR = Path(os.getenv("ERP_UPLOAD_DIR", "/app/uploads")) / "attachments"

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # 15MB — حد أقصى لحجم المرفق (منع استنزاف القرص/الذاكرة)
# entity_type بيدخل في اسم الملف على القرص — لازم يكون معرّف بسيط (حروف/أرقام/_)
# عشان مايبقاش فيه path traversal (مثلاً entity_type=../../x كان بيكتب ملف برا مجلد المرفقات)
_ENTITY_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,49}$")


def _check_attachment_size(content: bytes) -> None:
    if not content:
        raise HTTPException(400, "الملف فارغ")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(400, "حجم الملف أكبر من الحد المسموح (15 ميجابايت)")


def _validate_image_attachment(file: UploadFile) -> str:
    """يتحقق أن المرفق صورة فقط بالامتداد (الأكثر موثوقية)، ويرجع الامتداد بحروف صغيرة.
    لا نعتمد على content_type لأن المتصفحات/العملاء قد ترسل image/jpg أو application/octet-stream لصور صحيحة."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(400, "يُسمح برفع صور فقط (jpg, jpeg, png, gif, webp)")
    return ext


# ── Schemas ────────────────────────────────────────────────────

class SupplierIn(BaseModel):
    code: Optional[str] = None
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    contact_person: Optional[str] = None
    address: Optional[str] = None
    tax_id: Optional[str] = None
    payment_terms: Optional[str] = None
    bank_account: Optional[str] = None
    iban: Optional[str] = None
    supplier_type: Optional[str] = None
    is_import: Optional[bool] = None  # محطة 21: تصنيف صريح، له الأولوية على heuristic كلمات supplier_type
    notes: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("اسم المورد مطلوب")
        if len(v) > 200:
            raise ValueError("اسم المورد طويل جداً (الحد 200 حرف)")
        return v

    @field_validator("code")
    @classmethod
    def _code_len(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if len(v) > 30:
            raise ValueError("كود المورد طويل جداً (الحد 30 حرف)")
        return v or None

    @field_validator("phone", "email", "contact_person", "tax_id", "payment_terms",
                     "bank_account", "iban", "supplier_type")
    @classmethod
    def _max_lengths(cls, v: Optional[str], info) -> Optional[str]:
        # نفس أطوال أعمدة جدول suppliers — على Postgres القيمة الأطول كانت
        # بترجع 500 (StringDataRightTruncation) بدل رسالة واضحة.
        limits = {"phone": 30, "email": 200, "contact_person": 200, "tax_id": 50,
                  "payment_terms": 100, "bank_account": 200, "iban": 50, "supplier_type": 30}
        if v is not None and len(v) > limits[info.field_name]:
            raise ValueError(f"الحقل {info.field_name} أطول من المسموح ({limits[info.field_name]} حرف)")
        return v


class PurchaseLineIn(BaseModel):
    material_id: Optional[int] = None
    description: str
    qty: Decimal
    unit: str = "قطعة"
    unit_cost: Decimal
    update_stock: bool = True
    update_cost: bool = True


class PurchaseInvoiceIn(BaseModel):
    code: Optional[str] = None
    supplier_id: Optional[int] = None
    supplier_name: Optional[str] = None
    date: date
    due_date: Optional[date] = None
    tax_pct: Decimal = Decimal("0")
    discount_amt: Decimal = Decimal("0")
    notes: Optional[str] = None
    lines: List[PurchaseLineIn] = []


def _check_finite(v):
    """json.loads بيقبل NaN/Infinity — Decimal('NaN') كان بيوقع المقارنة بـ 500."""
    import math
    if v is not None and not math.isfinite(float(v)):
        raise ValueError("قيمة رقمية غير صالحة")
    return v


class _PaymentFieldsMixin:
    @field_validator("amount", "wht_amount", check_fields=False)
    @classmethod
    def _finite(cls, v):
        return _check_finite(v)

    @field_validator("payment_method", check_fields=False)
    @classmethod
    def _pm_len(cls, v):
        if v is not None and len(v) > 30:
            raise ValueError("طريقة الدفع غير صالحة")
        return v

    @field_validator("reference", check_fields=False)
    @classmethod
    def _ref_len(cls, v):
        if v is not None and len(v) > 100:
            raise ValueError("المرجع طويل جداً (الحد 100 حرف)")
        return v


class PaymentIn(_PaymentFieldsMixin, BaseModel):
    amount: float
    payment_date: Optional[date] = None
    payment_method: Optional[str] = None   # cash | transfer | cheque | bill
    reference: Optional[str] = None        # رقم الشيك / رقم التحويل
    notes: Optional[str] = None
    wht_amount: float = 0                  # ضريبة الخصم تحت الحساب المستقطعة من الدفعة


class SupplierPaymentIn(_PaymentFieldsMixin, BaseModel):
    supplier_id: int
    invoice_id: Optional[int] = None
    amount: float
    payment_date: Optional[date] = None
    payment_method: Optional[str] = None   # cash | transfer | cheque | bill
    reference: Optional[str] = None        # رقم الشيك / رقم التحويل
    notes: Optional[str] = None
    wht_amount: float = 0                  # ضريبة الخصم تحت الحساب المستقطعة من الدفعة


# ── Helpers ────────────────────────────────────────────────────

def supplier_dict(s: Supplier) -> dict:
    return {
        "id": s.id, "code": s.code, "name": s.name,
        "phone": s.phone, "email": s.email,
        "contact_person": s.contact_person,
        "address": s.address,
        "tax_id": s.tax_id, "payment_terms": s.payment_terms,
        "bank_account": getattr(s, "bank_account", None),
        "iban":         getattr(s, "iban", None),
        "supplier_type": getattr(s, "supplier_type", None),
        "is_import": getattr(s, "is_import", None),
        "balance": float(s.balance), "notes": s.notes,
        "is_active": s.is_active,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def inv_dict(inv: PurchaseInvoice) -> dict:
    sm = {
        "draft": "مسودة", "received": "مستلمة",
        "paid": "مدفوعة", "partial": "مدفوعة جزئياً",
    }
    return {
        "id": inv.id, "code": inv.code,
        "internal_code": getattr(inv, "internal_code", None),
        "supplier_id": inv.supplier_id,
        "supplier_name": inv.supplier_name or (inv.supplier.name if inv.supplier else ""),
        "date": str(inv.date),
        "due_date": str(inv.due_date) if inv.due_date else None,
        "status": inv.status, "status_ar": sm.get(inv.status, inv.status),
        "subtotal": float(inv.subtotal),
        "tax_pct": float(inv.tax_pct), "tax_amount": float(inv.tax_amount),
        "discount_amt": float(inv.discount_amt),
        "total": float(inv.total),
        "paid_amount": float(inv.paid_amount),
        "remaining": float(inv.total - inv.paid_amount),
        "notes": inv.notes,
        "attachment_count": len(inv.attachments) if "attachments" in inv.__dict__ else 0,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "lines": [{
            "id": l.id, "material_id": l.material_id,
            "description": l.description,
            "qty": float(l.qty), "unit": l.unit,
            "unit_cost": float(l.unit_cost), "total_cost": float(l.total_cost),
            "update_stock": l.update_stock,
        } for l in inv.lines],
    }


# ══════════════════════════════════════════════════════════════
# SUPPLIERS
# ══════════════════════════════════════════════════════════════

@router.get("/suppliers")
async def list_suppliers(db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_ls
    tid = _ctid_ls.get()
    q = select(Supplier).where(Supplier.is_active == True).order_by(Supplier.name)
    if tid is not None:
        q = q.where(Supplier.tenant_id == tid)
    res = await db.execute(q)
    return [supplier_dict(s) for s in res.scalars().all()]


@router.get("/suppliers/next-code")
async def get_next_supplier_code(db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_nc
    return {"code": await _next_supplier_code(db, _ctid_nc.get())}


@router.post("/suppliers", status_code=201)
async def create_supplier(data: SupplierIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_sup
    d = data.model_dump()
    d['code'] = d.get('code') or await _next_supplier_code(db, _ctid_sup.get())
    d['tenant_id'] = _ctid_sup.get() or 1
    # كود مكرر لنفس الـ tenant كان بيوصل للـ DB ويرجع 500 IntegrityError
    if await db.scalar(select(Supplier.id).where(
            Supplier.tenant_id == d['tenant_id'], Supplier.code == d['code']).limit(1)):
        raise HTTPException(400, f"كود المورد {d['code']} مستخدم من قبل")
    s = Supplier(**d)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return supplier_dict(s)


async def _get_tenant_supplier(db: AsyncSession, sid: int) -> Optional[Supplier]:
    """جلب مورد مقيّد بالـ tenant الحالي — يمنع أي tenant من قراءة/تعديل
    مورد تابع لـ tenant تاني عن طريق تخمين الـ id (كانت db.get(Supplier, sid)
    بترجع أي مورد بغض النظر عن الـ tenant، وده تسريب بيانات بين المستأجرين)."""
    from app.core.database import current_tenant_id as _ctid_gs
    tid = _ctid_gs.get()
    q = select(Supplier).where(Supplier.id == sid)
    if tid is not None:
        q = q.where(Supplier.tenant_id == tid)
    return (await db.execute(q)).scalar_one_or_none()


@router.get("/suppliers/{sid}")
async def get_supplier(sid: int, db: AsyncSession = Depends(get_db)):
    s = await _get_tenant_supplier(db, sid)
    if not s: raise HTTPException(404)
    return supplier_dict(s)


@router.put("/suppliers/{sid}")
async def update_supplier(sid: int, data: SupplierIn, db: AsyncSession = Depends(get_db)):
    s = await _get_tenant_supplier(db, sid)
    if not s: raise HTTPException(404)
    d = data.model_dump()
    if not d.get("code"):
        d["code"] = s.code  # حافظ على الكود الموجود لو لم يُرسَل
    elif d["code"] != s.code and await db.scalar(select(Supplier.id).where(
            Supplier.tenant_id == s.tenant_id, Supplier.code == d["code"],
            Supplier.id != s.id).limit(1)):
        raise HTTPException(400, f"كود المورد {d['code']} مستخدم من قبل")
    for k, v in d.items():
        setattr(s, k, v)
    await db.commit()
    await db.refresh(s)
    return supplier_dict(s)


@router.delete("/suppliers/{sid}")
async def delete_supplier(sid: int, db: AsyncSession = Depends(get_db)):
    s = await _get_tenant_supplier(db, sid)
    if not s: raise HTTPException(404)
    s.is_active = False
    await db.commit()
    return {"message": "تم الأرشفة", "id": sid}


@router.get("/suppliers/{sid}/statement")
async def supplier_statement(sid: int, db: AsyncSession = Depends(get_db)):
    """كشف حساب مورد"""
    s = await _get_tenant_supplier(db, sid)
    if not s: raise HTTPException(404)
    res = await db.execute(
        select(PurchaseInvoice)
        .options(selectinload(PurchaseInvoice.lines))
        .where(PurchaseInvoice.supplier_id == sid)
        .order_by(PurchaseInvoice.date)
    )
    invoices = res.scalars().all()
    total = sum(float(inv.total) for inv in invoices)
    paid  = sum(float(inv.paid_amount) for inv in invoices)

    from app.models.models import SupplierPayment
    onaccount_res = await db.execute(
        select(SupplierPayment).where(
            SupplierPayment.supplier_id == sid,
            SupplierPayment.invoice_id.is_(None),
        )
    )
    onaccount_paid = sum(float(p.amount) for p in onaccount_res.scalars().all())

    payments_res = await db.execute(
        select(SupplierPayment)
        .where(SupplierPayment.supplier_id == sid)
        .order_by(SupplierPayment.payment_date, SupplierPayment.id)
    )
    invoice_codes = {inv.id: inv.code for inv in invoices}
    payments = [{
        "id": p.id,
        "invoice_id": p.invoice_id,
        "invoice_code": invoice_codes.get(p.invoice_id),
        "amount": float(p.amount),
        "payment_date": p.payment_date.isoformat() if p.payment_date else None,
        "payment_method": p.payment_method,
        "reference": p.reference,
        "notes": p.notes,
    } for p in payments_res.scalars().all()]

    return {
        "supplier": supplier_dict(s),
        "total_invoices": total,
        "total_paid": paid + onaccount_paid,
        "onaccount_paid": onaccount_paid,
        "balance_due": total - paid - onaccount_paid,
        "invoices": [inv_dict(inv) for inv in invoices],
        "payments": payments,
    }


# ══════════════════════════════════════════════════════════════
# PURCHASE INVOICES
# ══════════════════════════════════════════════════════════════



@router.get("/suppliers/{sid}/ledger")
async def supplier_ledger(sid: int, date_from: Optional[date] = None,
                          date_to: Optional[date] = None, db: AsyncSession = Depends(get_db)):
    """كشف حساب تفصيلي للمورد من تاريخ لتاريخ — فواتير + مدفوعات مرتّبة زمنياً"""
    from app.models.models import SupplierPayment
    s = await _get_tenant_supplier(db, sid)
    if not s: raise HTTPException(404, "المورد غير موجود")

    # الرصيد الافتتاحي = صافي كل الحركات قبل date_from
    opening_balance = 0.0
    if date_from:
        op_inv_q = select(func.coalesce(func.sum(PurchaseInvoice.total), 0)).where(
            PurchaseInvoice.supplier_id == sid, PurchaseInvoice.date < date_from)
        op_pay_q = select(func.coalesce(func.sum(SupplierPayment.amount), 0)).where(
            SupplierPayment.supplier_id == sid, SupplierPayment.payment_date < date_from)
        op_inv_total = float((await db.execute(op_inv_q)).scalar() or 0)
        op_pay_total = float((await db.execute(op_pay_q)).scalar() or 0)
        opening_balance = round(op_inv_total - op_pay_total, 2)

    # الفواتير
    inv_q = select(PurchaseInvoice).where(PurchaseInvoice.supplier_id == sid)
    if date_from: inv_q = inv_q.where(PurchaseInvoice.date >= date_from)
    if date_to:   inv_q = inv_q.where(PurchaseInvoice.date <= date_to)
    invoices = (await db.execute(inv_q)).scalars().all()

    # المدفوعات
    pay_q = select(SupplierPayment).where(SupplierPayment.supplier_id == sid)
    if date_from: pay_q = pay_q.where(SupplierPayment.payment_date >= date_from)
    if date_to:   pay_q = pay_q.where(SupplierPayment.payment_date <= date_to)
    payments = (await db.execute(pay_q)).scalars().all()

    rows = []
    for inv in invoices:
        rows.append({"date": str(inv.date), "type": "invoice", "ref": inv.code,
                     "description": f"فاتورة شراء {inv.code}",
                     "debit": float(inv.total), "credit": 0.0})
    for p in payments:
        rows.append({"date": str(p.payment_date), "type": "payment", "ref": f"دفعة #{p.id}",
                     "description": p.notes or "دفعة", "debit": 0.0, "credit": float(p.amount)})

    rows.sort(key=lambda r: (r["date"], 0 if r["type"] == "invoice" else 1))
    # الرصيد الجاري
    bal = opening_balance
    for r in rows:
        bal += r["debit"] - r["credit"]
        r["balance"] = round(bal, 2)

    total_debit  = round(sum(r["debit"] for r in rows), 2)
    total_credit = round(sum(r["credit"] for r in rows), 2)
    return {
        "supplier": {"id": s.id, "name": s.name, "code": s.code},
        "date_from": str(date_from) if date_from else None,
        "date_to": str(date_to) if date_to else None,
        "opening_balance": opening_balance,
        "rows": rows,
        "total_debit": total_debit, "total_credit": total_credit,
        "balance": round(opening_balance + total_debit - total_credit, 2),
        "current_balance": float(s.balance),
    }


@router.get("/invoices")
async def list_purchase_invoices(
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    from app.core.database import current_tenant_id as _ctid_lpi
    tid = _ctid_lpi.get()
    q = select(PurchaseInvoice).options(
        selectinload(PurchaseInvoice.supplier),
        selectinload(PurchaseInvoice.lines),
        selectinload(PurchaseInvoice.attachments),
    )
    if tid is not None:
        q = q.where(PurchaseInvoice.tenant_id == tid)
    if status:
        q = q.where(PurchaseInvoice.status == status)
    if supplier_id:
        q = q.where(PurchaseInvoice.supplier_id == supplier_id)
    res = await db.execute(q.order_by(PurchaseInvoice.date.desc()))
    return [inv_dict(inv) for inv in res.scalars().all()]


@router.post("/invoices", status_code=201)
async def create_purchase_invoice(data: PurchaseInvoiceIn, db: AsyncSession = Depends(get_db)):
    # المورد مطلوب
    if not data.supplier_id:
        raise HTTPException(400, "المورد مطلوب — يرجى اختيار مورد")
    if not data.lines:
        raise HTTPException(400, "أضف بنداً واحداً على الأقل")
    for l in data.lines:
        if l.qty is None or l.qty <= 0:
            raise HTTPException(400, "كمية كل بند يجب أن تكون أكبر من صفر")
        if l.unit_cost is not None and l.unit_cost < 0:
            raise HTTPException(400, "سعر الوحدة لا يمكن أن يكون سالباً")
    _validate_invoice_header(data)

    from app.core.database import current_tenant_id as _ctid_pi
    _tid_pi = _ctid_pi.get() or 1
    internal_code = await _next_internal_code(db, _tid_pi)
    if data.code:
        data.code = data.code.strip()
        if len(data.code) > 50:
            raise HTTPException(400, "رقم الفاتورة طويل جداً (الحد 50 حرف)")
    if data.code and await _invoice_code_taken(db, _tid_pi, data.code):
        raise HTTPException(400, f"رقم فاتورة الشراء {data.code} مستخدم من قبل")

    # محطة 15 (الروادماب الأصلي): تحديد الموقع الفعلي (RAW/IMPORTED) مرة واحدة
    # لكل الفاتورة، بدل ما نعدّل stock_qty يدويًا من غير أي أثر في StockQuant
    from app.services.inventory_ledger import is_import_supplier, get_or_create_warehouse_bin, post_receipt
    supplier_for_loc = await db.get(Supplier, data.supplier_id)
    # تحقق إن المورد فعلاً تابع لنفس الـ tenant — منع إسناد فاتورة لمورد تابع
    # لـ tenant تاني عن طريق تمرير supplier_id تخميني (تسريب/عبث بين المستأجرين).
    # ملحوظة: الفلتر التلقائي على مستوى الـORM (tenant_events._apply_tenant_filter)
    # بيخلي db.get يرجع None مباشرة لمورد تابع لـ tenant تاني — فالشرط القديم
    # "supplier_for_loc and supplier_for_loc.tenant_id != _tid_pi" كان كوداً
    # ميت فعلياً (supplier_for_loc بيبقى None أصلاً في الحالة دي، فالشرط
    # مايتحققش أبداً)، وده كان بيسيب الفاتورة تتنشئ بنجاح بـ supplier_id
    # يتيم (تابع لـ tenant تاني) من غير أي رسالة خطأ ومن غير تحديث رصيد أي
    # مورد حقيقي. الإصلاح: نتحقق من عدم وجود المورد أصلاً بدل مقارنة tenant_id.
    if not supplier_for_loc:
        raise HTTPException(400, "المورد غير موجود")
    # أي بند مربوط بمادة (حتى لو update_stock=False) لازم المادة تكون تابعة لنفس
    # الـ tenant — كان ممكن يتسجل بند بـ material_id "يتيم" تابع لـ tenant تاني.
    for l in data.lines:
        if l.material_id and not await db.get(Material, l.material_id):
            raise HTTPException(400, "المادة غير موجودة")
    wh_code = "IMPORTED" if is_import_supplier(supplier_for_loc) else "RAW"

    subtotal = sum(Decimal(str(l.qty)) * Decimal(str(l.unit_cost)) for l in data.lines)
    tax_amount = subtotal * Decimal(str(data.tax_pct)) / 100
    total = subtotal - Decimal(str(data.discount_amt)) + tax_amount

    inv = PurchaseInvoice(
        tenant_id=_tid_pi,
        code=data.code or internal_code,
        internal_code=internal_code,
        supplier_id=data.supplier_id,
        supplier_name=data.supplier_name,
        date=data.date,
        due_date=data.due_date,
        tax_pct=data.tax_pct,
        tax_amount=tax_amount,
        discount_amt=data.discount_amt,
        subtotal=subtotal,
        total=total,
        notes=data.notes,
        status="received",
    )
    db.add(inv)
    await db.flush()

    for line_data in data.lines:
        total_cost = Decimal(str(line_data.qty)) * Decimal(str(line_data.unit_cost))
        line = PurchaseInvoiceLine(
            tenant_id=_tid_pi,
            invoice_id=inv.id,
            material_id=line_data.material_id,
            description=line_data.description,
            qty=line_data.qty,
            unit=line_data.unit,
            unit_cost=line_data.unit_cost,
            total_cost=total_cost,
            update_stock=line_data.update_stock,
        )
        db.add(line)

        if line_data.update_stock and line_data.material_id:
            mat = await db.get(Material, line_data.material_id)
            # الفلتر التلقائي على مستوى الـORM (tenant_events._apply_tenant_filter)
            # بيخلي db.get يرجع None لأي مادة تابعة لـ tenant تاني — فمنعاً لتسريب
            # مخزون/سعر مادة تانية كان أصلاً متغطّى. لكن قبل الإصلاح ده، تمرير
            # material_id تخميني (مش موجود لنفس الـ tenant) كان بيسيب سكوت تام:
            # يتسجل بند الفاتورة بمرجع مادة "يتيم" من غير أي تحديث مخزون/سعر
            # ومن غير أي رسالة خطأ للمستخدم رغم إنه طلب update_stock=True صراحة.
            if not mat:
                raise HTTPException(400, "المادة غير موجودة")
            if mat:
                bin_loc = await get_or_create_warehouse_bin(db, _tid_pi, wh_code)
                await post_receipt(
                    db, material=mat, qty=Decimal(str(line_data.qty)),
                    unit_cost=Decimal(str(line_data.unit_cost)),
                    source_doc_type="purchase_invoice", source_doc_id=inv.id,
                    destination_location_id=bin_loc.id,
                    reference=data.code or internal_code, notes=f"فاتورة شراء: {data.code or internal_code}",
                    movement_date=data.date,
                )
                if line_data.update_cost:
                    mat.unit_cost = Decimal(str(line_data.unit_cost))
                mat.last_purchase_price = Decimal(str(line_data.unit_cost))
                # إنشاء سجل أسياخ تلقائياً للمواد ذات bar_length_cm
                if mat.weight_per_meter_kg and mat.bar_length_cm and float(mat.weight_per_meter_kg) > 0 and float(mat.bar_length_cm) > 0:
                    bar_weight = float(mat.bar_length_cm) / 100.0 * float(mat.weight_per_meter_kg)
                    n_bars = max(1, round(float(line_data.qty) / bar_weight))
                    db.add(BarStockItem(
                        tenant_id=_tid_pi,
                        material_id=mat.id,
                        length_cm=mat.bar_length_cm,
                        qty=n_bars,
                        source="purchased",
                        notes=f"فاتورة شراء: {data.code}",
                    ))

    if data.supplier_id:
        sup = await db.get(Supplier, data.supplier_id)
        if sup:
            sup.balance += total

    # قيد محاسبي تلقائي
    from app.core.database import current_tenant_id as _ctid
    _tid = _ctid.get() or 1
    await journal_purchase_invoice_create(db, _tid, inv.id, inv.code, tax_amount, total, data.date)
    await db.commit()
    await db.refresh(inv)
    return {"id": inv.id, "code": inv.code, "supplier_id": inv.supplier_id,
            "subtotal": float(inv.subtotal), "total": float(inv.total)}


async def _tenant_invoice_query(iid: int):
    from app.core.database import current_tenant_id as _ctid_tiq
    tid = _ctid_tiq.get()
    q = select(PurchaseInvoice).where(PurchaseInvoice.id == iid)
    if tid is not None:
        q = q.where(PurchaseInvoice.tenant_id == tid)
    return q


@router.get("/invoices/{iid}")
async def get_purchase_invoice(iid: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        (await _tenant_invoice_query(iid)).options(
            selectinload(PurchaseInvoice.supplier),
            selectinload(PurchaseInvoice.lines).selectinload(PurchaseInvoiceLine.material),
        )
    )
    inv = res.scalar_one_or_none()
    if not inv: raise HTTPException(404)
    d = inv_dict(inv)
    d["lines"] = [{
        "id": l.id, "material_id": l.material_id,
        "material_code": l.material.code if l.material else None,
        "material_name": l.material.name_ar if l.material else None,
        "description": l.description,
        "qty": float(l.qty), "unit": l.unit,
        "unit_cost": float(l.unit_cost), "total_cost": float(l.total_cost),
        "update_stock": l.update_stock,
    } for l in inv.lines]
    atts = await db.execute(
        select(FileAttachment).where(
            FileAttachment.entity_type == "purchase_invoice",
            FileAttachment.entity_id == iid,
        )
    )
    d["attachments"] = [{
        "id": a.id, "original_name": a.original_name,
        "stored_name": a.stored_name, "file_size": a.file_size,
        "mime_type": a.mime_type, "notes": a.notes,
        "filename": a.original_name,
        "url": f"/api/v1/purchases/attachments/{a.id}/download",
        "created_at": a.created_at.isoformat() if a.created_at else None,
    } for a in atts.scalars().all()]
    return d


@router.put("/invoices/{iid}")
async def update_purchase_invoice(iid: int, data: PurchaseInvoiceIn, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        (await _tenant_invoice_query(iid)).options(selectinload(PurchaseInvoice.lines))
    )
    inv = res.scalar_one_or_none()
    if not inv: raise HTTPException(404)
    if inv.paid_amount > 0:
        raise HTTPException(400, "لا يمكن تعديل فاتورة تم تسجيل دفعات عليها")
    if not data.lines:
        raise HTTPException(400, "أضف بنداً واحداً على الأقل")
    for l in data.lines:
        if l.qty is None or l.qty <= 0:
            raise HTTPException(400, "كمية كل بند يجب أن تكون أكبر من صفر")
        if l.unit_cost is not None and l.unit_cost < 0:
            raise HTTPException(400, "سعر الوحدة لا يمكن أن يكون سالباً")
    _validate_invoice_header(data)

    from app.core.database import current_tenant_id as _ctid_upd
    _tid_upd = _ctid_upd.get() or 1
    if data.code:
        data.code = data.code.strip()
        if len(data.code) > 50:
            raise HTTPException(400, "رقم الفاتورة طويل جداً (الحد 50 حرف)")
    if data.code and data.code != inv.code and await _invoice_code_taken(db, _tid_upd, data.code, exclude_id=inv.id):
        raise HTTPException(400, f"رقم فاتورة الشراء {data.code} مستخدم من قبل")
    # فاتورة كان ليها مورد — مايتشالش المورد منها في التعديل (نفس شرط الإنشاء:
    # "المورد مطلوب")؛ من غير كده الفاتورة بتبقى بدين في 2110 من غير أي مورد.
    if inv.supplier_id and not data.supplier_id:
        raise HTTPException(400, "المورد مطلوب — يرجى اختيار مورد")
    for l in data.lines:
        if l.material_id and not await db.get(Material, l.material_id):
            raise HTTPException(400, "المادة غير موجودة")

    # محطة 15 (الروادماب الأصلي): عكس تأثير البنود القديمة عبر post_issue()
    # فعليًا (بيحدّث StockQuant وMaterial.stock_qty مع بعض) — بدل التعديل
    # اليدوي المباشر + حذف صفوف StockMovement القديمة (كان بيكسر أثر
    # التدقيق/audit trail، وبيسيب StockQuant من غير أي تصحيح خالص).
    #
    # نفس منطق تحديد الموقع اللي استُخدم وقت الإنشاء (RAW/IMPORTED حسب
    # تصنيف المورد الحالي على الفاتورة قبل أي تغيير) — عشان العكس يحصل من
    # نفس الموقع اللي الرصيد الأصلي اتسجل فيه بالظبط.
    from app.services.inventory_ledger import is_import_supplier, get_or_create_warehouse_bin, post_issue, post_receipt
    old_supplier_for_loc = await db.get(Supplier, inv.supplier_id) if inv.supplier_id else None
    old_wh_code = "IMPORTED" if is_import_supplier(old_supplier_for_loc) else "RAW"
    old_bin = await get_or_create_warehouse_bin(db, _tid_upd, old_wh_code)

    for l in inv.lines:
        if l.update_stock and l.material_id:
            mat = await db.get(Material, l.material_id)
            if mat and mat.stock_qty >= Decimal(str(l.qty)):
                await post_issue(
                    db, material=mat, qty=Decimal(str(l.qty)), unit_cost=mat.unit_cost or Decimal("0"),
                    source_doc_type="purchase_invoice_edit", source_doc_id=inv.id,
                    source_location_id=old_bin.id,
                    reference=inv.code, notes=f"تعديل فاتورة شراء — عكس البند القديم: {inv.code}",
                )
            elif mat:
                # الرصيد الحالي أقل من الكمية المفروض تتعكس (اتصرفت بالفعل في
                # حاجة تانية) — منعدلش الرصيد بالسالب، بس نوثق الحالة بدل
                # ما نفشل العملية كلها بصمت
                mat.stock_qty = Decimal("0")
        await db.delete(l)

    # عكس رصيد المورد والقيد المحاسبي القديمين
    if inv.supplier_id:
        old_sup = await db.get(Supplier, inv.supplier_id)
        if old_sup:
            old_sup.balance -= inv.total
    await journal_purchase_invoice_delete(db, _tid_upd, inv.id, inv.code, inv.tax_amount, inv.total, inv.date)

    # إعادة حساب الإجماليات الجديدة
    subtotal = sum(Decimal(str(l.qty)) * Decimal(str(l.unit_cost)) for l in data.lines)
    tax_amount = subtotal * Decimal(str(data.tax_pct)) / 100
    total = subtotal - Decimal(str(data.discount_amt)) + tax_amount

    inv.code = data.code or inv.code
    inv.supplier_id = data.supplier_id
    inv.supplier_name = data.supplier_name
    inv.date = data.date
    inv.due_date = data.due_date
    inv.tax_pct = data.tax_pct
    inv.tax_amount = tax_amount
    inv.discount_amt = data.discount_amt
    inv.subtotal = subtotal
    inv.total = total
    inv.notes = data.notes

    await db.flush()

    # الموقع الجديد بيتحدد حسب المورد الجديد على الفاتورة (ممكن يكون اتغيّر)
    new_supplier = await db.get(Supplier, data.supplier_id) if data.supplier_id else None
    # نفس ملاحظة create_purchase_invoice: db.get بيرجع None تلقائياً لمورد
    # تابع لـ tenant تاني، فالتحقق لازم يبقى "مطلوب ومش موجود" مش "موجود
    # وبتنانت مختلف" (اللي كان كوداً ميتاً بيسيب الفاتورة تتحدّث بمورد يتيم).
    if data.supplier_id and not new_supplier:
        raise HTTPException(400, "المورد غير موجود")
    new_wh_code = "IMPORTED" if is_import_supplier(new_supplier) else "RAW"
    new_bin = await get_or_create_warehouse_bin(db, _tid_upd, new_wh_code)

    for line_data in data.lines:
        total_cost = Decimal(str(line_data.qty)) * Decimal(str(line_data.unit_cost))
        db.add(PurchaseInvoiceLine(
            tenant_id=_tid_upd, invoice_id=inv.id,
            material_id=line_data.material_id,
            description=line_data.description,
            qty=line_data.qty, unit=line_data.unit,
            unit_cost=line_data.unit_cost, total_cost=total_cost,
            update_stock=line_data.update_stock,
        ))

        if line_data.update_stock and line_data.material_id:
            mat = await db.get(Material, line_data.material_id)
            if not mat:
                raise HTTPException(400, "المادة غير موجودة")
            if mat:
                await post_receipt(
                    db, material=mat, qty=Decimal(str(line_data.qty)),
                    unit_cost=Decimal(str(line_data.unit_cost)),
                    source_doc_type="purchase_invoice_edit", source_doc_id=inv.id,
                    destination_location_id=new_bin.id,
                    reference=inv.code, notes=f"تعديل فاتورة شراء: {inv.code}",
                    movement_date=data.date,
                )
                if line_data.update_cost:
                    mat.unit_cost = Decimal(str(line_data.unit_cost))
                mat.last_purchase_price = Decimal(str(line_data.unit_cost))

    if data.supplier_id:
        new_sup = await db.get(Supplier, data.supplier_id)
        if new_sup:
            new_sup.balance += total

    await journal_purchase_invoice_create(db, _tid_upd, inv.id, inv.code, tax_amount, total, data.date)

    await db.commit()
    await db.refresh(inv)
    return {"id": inv.id, "code": inv.code, "total": float(inv.total)}


@router.post("/payments")
async def record_supplier_payment(data: SupplierPaymentIn, db: AsyncSession = Depends(get_db),
                                   current_user: dict = Depends(get_current_user)):
    """
    دفعة عامة لمورد — مرتبطة بفاتورة (invoice_id) أو "تحت الحساب" بدون فاتورة.
    لو invoice_id محدد، يتم تطبيقها على الفاتورة بنفس منطق /invoices/{iid}/payment.
    """
    sup = await _get_tenant_supplier(db, data.supplier_id)
    if not sup:
        raise HTTPException(404, "المورد غير موجود")

    amount = Decimal(str(data.amount))
    pay_date = data.payment_date or date.today()
    if amount <= 0:
        raise HTTPException(400, "المبلغ يجب أن يكون أكبر من صفر")
    wht_amt = Decimal(str(data.wht_amount or 0))
    if wht_amt < 0 or wht_amt > amount:
        raise HTTPException(400, "ضريبة الخصم تحت الحساب غير صحيحة")

    inv = None
    invoice_amount = Decimal("0")
    excess = amount
    if data.invoice_id:
        # قفل صف الفاتورة (Postgres) — دفعتين متزامنتين كانوا ممكن يقروا نفس
        # paid_amount ويتخطّوا المتبقي (سداد زائد). SQLite بيتجاهل FOR UPDATE.
        inv = (await db.execute((await _tenant_invoice_query(data.invoice_id)).with_for_update())).scalar_one_or_none()
        if not inv:
            raise HTTPException(404, "فاتورة الشراء غير موجودة")
        # تحقق إن الفاتورة فعلاً تابعة لنفس المورد المحدد — من غيره كان ممكن
        # نمرر supplier_id لمورد وinvoice_id لفاتورة مورد تاني (نفس الـ tenant)،
        # فتتسدد الفاتورة (paid_amount/status) وتتخصم القيمة من رصيد مورد مالوش
        # أي علاقة بالفاتورة دي، ويفضل رصيد المورد الحقيقي صاحب الفاتورة زي
        # ما هو — خلط فعلي بين حسابات موردين مختلفين.
        if inv.supplier_id != sup.id:
            raise HTTPException(400, "الفاتورة لا تخص هذا المورد")
        remaining = inv.total - inv.paid_amount
        invoice_amount = min(amount, remaining) if remaining > 0 else Decimal("0")
        excess = amount - invoice_amount
        inv.paid_amount = inv.paid_amount + invoice_amount
        if inv.paid_amount >= inv.total:
            inv.status = "paid"
        elif inv.paid_amount > 0:
            inv.status = "partial"

    # تحقق إن ضريبة الخصم لا تتجاوز الجزء اللي فعلاً هتتخصم منه محاسبياً.
    # الفحص الأول فوق كان بيقارن wht_amt بإجمالي amount بس — لكن لو جزء من
    # الدفعة راح على الفاتورة (invoice_amount) والباقي "تحت الحساب" (excess)،
    # القيد المحاسبي (journal_purchase_invoice_payment) بيحمّل wht_amt كامل
    # على invoice_amount لوحده. لو wht_amt > invoice_amount، سطر الصندوق
    # (1110) بيتحسب بقيمة سالبة (amount - wht_amount)، يعني قيد معكوس فعلياً
    # (الصندوق "يزيد" بدل ما "يقل") رغم إن مجموع القيد لسه متوازن ظاهرياً —
    # فساد محاسبي حقيقي كان بيمر من غير أي تحقق.
    bucket = invoice_amount if invoice_amount > 0 else excess
    if wht_amt > bucket:
        raise HTTPException(400, "ضريبة الخصم تحت الحساب أكبر من المبلغ المطبَّق على الفاتورة/تحت الحساب")

    sup.balance -= amount

    from app.models.models import SupplierPayment
    from app.core.database import current_tenant_id as _ctid4
    _tid4 = _ctid4.get() or 1

    sup_pay = None
    if invoice_amount > 0:
        sup_pay = SupplierPayment(
            supplier_id=sup.id, invoice_id=inv.id if inv else None,
            amount=invoice_amount, payment_date=pay_date,
            payment_method=data.payment_method,
            reference=data.reference,
            notes=data.notes,
            created_by=int(current_user.get('sub', 0)) or None,
        )
        db.add(sup_pay)
        await db.flush()
        from app.models.models import SupplierPaymentAllocation
        db.add(SupplierPaymentAllocation(
            tenant_id=_tid4, payment_id=sup_pay.id, invoice_id=inv.id, amount=invoice_amount,
        ))
        await journal_purchase_invoice_payment(db, _tid4, inv.id, inv.code, invoice_amount, pay_date, wht_amount=wht_amt)

    excess_pay = None
    if excess > 0:
        note = data.notes
        if inv:
            extra_note = f"دفعة زائدة عن فاتورة {inv.code} — مُرحّلة تحت الحساب"
            note = f"{note} - {extra_note}" if note else extra_note
        excess_pay = SupplierPayment(
            supplier_id=sup.id, invoice_id=None,
            amount=excess, payment_date=pay_date,
            payment_method=data.payment_method,
            reference=data.reference,
            notes=note,
            created_by=int(current_user.get('sub', 0)) or None,
        )
        db.add(excess_pay)
        await db.flush()
        excess_wht = wht_amt if invoice_amount == 0 else Decimal("0")
        await journal_purchase_invoice_payment(db, _tid4, excess_pay.id, f"دفعة تحت الحساب - {sup.name}", excess, pay_date, wht_amount=excess_wht)

    # ربط تلقائي بالخزنة — خروج فلوس
    try:
        from app.services.treasury_link import record_payment_in_treasury
        net_amount = amount - wht_amt
        if net_amount > 0:
            await record_payment_in_treasury(
                db, amount=net_amount, direction="out", source="supplier_payment",
                linked_type=None, linked_id=None,
                category_name="دفع لموردين",
                description=(f"دفعة لفاتورة شراء {inv.code}" if inv else f"دفعة تحت الحساب لمورد {sup.name}"),
                txn_date=pay_date,
            )
    except Exception:
        pass  # الخزنة اختيارية — مايكسرش الدفع

    await db.commit()
    return {
        "supplier_payment_id": (sup_pay or excess_pay).id,
        "supplier_balance": float(sup.balance),
        "invoice_id": inv.id if inv else None,
        "paid_amount": float(inv.paid_amount) if inv else None,
        "remaining": float(inv.total - inv.paid_amount) if inv else None,
        "status": inv.status if inv else None,
        "excess_onaccount": float(excess) if excess > 0 else 0,
    }


@router.post("/invoices/{iid}/payment")
async def record_purchase_payment(iid: int, data: PaymentIn, db: AsyncSession = Depends(get_db),
                                  current_user: dict = Depends(get_current_user)):
    # قفل صف الفاتورة لمنع سداد زائد من دفعتين متزامنتين (Postgres)
    inv = (await db.execute((await _tenant_invoice_query(iid)).with_for_update())).scalar_one_or_none()
    if not inv: raise HTTPException(404)
    amount = Decimal(str(data.amount))
    pay_date = data.payment_date or date.today()
    remaining = inv.total - inv.paid_amount
    if amount <= 0:
        raise HTTPException(400, "المبلغ يجب أن يكون أكبر من صفر")
    if amount > remaining:
        raise HTTPException(400, f"المبلغ ({float(amount):,.2f}) أكبر من المتبقي ({float(remaining):,.2f})")
    wht_amt = Decimal(str(data.wht_amount or 0))
    if wht_amt < 0 or wht_amt > amount:
        raise HTTPException(400, "ضريبة الخصم تحت الحساب غير صحيحة")
    inv.paid_amount = inv.paid_amount + amount
    if inv.paid_amount >= inv.total:
        inv.status = "paid"
    elif inv.paid_amount > 0:
        inv.status = "partial"
    if inv.supplier_id:
        sup = await db.get(Supplier, inv.supplier_id)
        if sup:
            sup.balance -= amount

    # سجّل الدفعة في كشف الحساب
    from app.models.models import SupplierPayment
    sup_pay = SupplierPayment(
        supplier_id=inv.supplier_id, invoice_id=inv.id,
        amount=amount, payment_date=pay_date,
        payment_method=data.payment_method,
        reference=data.reference,
        notes=data.notes,
        created_by=int(current_user.get('sub', 0)) or None,
    )
    db.add(sup_pay)
    await db.flush()  # لاحتياج sup_pay.id

    from app.core.database import current_tenant_id as _ctid3
    _tid3 = _ctid3.get() or 1
    from app.models.models import SupplierPaymentAllocation
    db.add(SupplierPaymentAllocation(
        tenant_id=_tid3, payment_id=sup_pay.id, invoice_id=inv.id, amount=amount,
    ))
    await journal_purchase_invoice_payment(db, _tid3, inv.id, inv.code, amount, pay_date, wht_amount=wht_amt)

    # ربط تلقائي بالخزنة — خروج فلوس
    try:
        from app.services.treasury_link import record_payment_in_treasury
        net_amount = amount - wht_amt
        if net_amount > 0:
            await record_payment_in_treasury(
                db, amount=net_amount, direction="out", source="supplier_payment",
                linked_type=None, linked_id=None,  # دفعات جزئية متعددة مسموحة
                category_name="دفع لموردين",
                description=f"دفعة لفاتورة شراء {inv.code}",
                txn_date=pay_date,
            )
    except Exception:
        pass  # الخزنة اختيارية — مايكسرش الدفع

    await db.commit()
    return {
        "paid_amount": float(inv.paid_amount),
        "remaining": float(inv.total - inv.paid_amount),
        "status": inv.status,
        "supplier_payment_id": sup_pay.id,
    }


@router.delete("/invoices/{iid}")
async def delete_purchase_invoice(iid: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        (await _tenant_invoice_query(iid)).options(selectinload(PurchaseInvoice.lines))
    )
    inv = res.scalar_one_or_none()
    if not inv: raise HTTPException(404)
    # كان الشرط بيمنع حذف الفاتورة المدفوعة بالكامل فقط ("paid") ويسمح بحذف
    # فاتورة "partial" (مدفوعة جزئياً) بحرية. المشكلة: عكس القيد المحاسبي
    # تحت بيعكس دائماً كامل قيمة الفاتورة (total) من حساب الموردين (2110)،
    # بينما سداد الدفعة الجزئية كان بالفعل عكس جزء منها فعلياً (2110 مدين
    # بقيمة المبلغ المسدد) — فالعكس المزدوج ده بيسيب حساب الموردين (2110)
    # بفارق سالب يساوي المبلغ المسدد فعلاً (خطأ محاسبي حقيقي وقائم، مش مجرد
    # عرض). نفس منطق update_purchase_invoice بالظبط (بيمنع التعديل لو
    # paid_amount > 0)، فالحذف لازم يتبع نفس القاعدة.
    if inv.paid_amount > 0:
        raise HTTPException(400, "لا يمكن حذف فاتورة تم تسجيل دفعات عليها")
    # منع حذف فاتورة عليها سجلات تابعة (دفعات/تخصيصات/دفعات مخزون/معدات مربوطة)
    # — كان الحذف بيكسر الـ FK (500 على Postgres) أو يسيب سجلات يتيمة على SQLite.
    from app.models.models import SupplierPayment as _SP_d, SupplierPaymentAllocation as _SPA_d, StockBatch as _SB_d, Equipment as _EQ_d
    for _model, _col, _label in (
        (_SP_d, _SP_d.invoice_id, "دفعات مورد"),
        (_SPA_d, _SPA_d.invoice_id, "تخصيصات دفعات"),
        (_SB_d, _SB_d.purchase_invoice_id, "دفعات مخزون (Stock Batches)"),
        (_EQ_d, _EQ_d.purchase_invoice_id, "معدات"),
    ):
        if await db.scalar(select(_model.id).where(_col == iid).limit(1)):
            raise HTTPException(400, f"لا يمكن حذف الفاتورة — مرتبط بها {_label}")

    from app.core.database import current_tenant_id as _ctid_del
    _tid_del = _ctid_del.get() or 1

    # محطة 15 (الروادماب الأصلي): عكس فعلي عبر post_issue() — نفس التصحيح
    # اللي اتعمل في update_purchase_invoice. ملحوظة صراحة: أول مراجعة لي
    # لدالة الحذف دي ركّزت على الجانب المحاسبي بس (journal_purchase_invoice_delete)
    # واعتبرتها "سليمة" — ده كان غلط، الجانب المخزني هنا كان فيه نفس فجوة
    # الـupdate بالظبط ومكنش اتصلح غير دلوقتي.
    from app.services.inventory_ledger import is_import_supplier, get_or_create_warehouse_bin, post_issue
    supplier_for_loc = await db.get(Supplier, inv.supplier_id) if inv.supplier_id else None
    wh_code = "IMPORTED" if is_import_supplier(supplier_for_loc) else "RAW"
    bin_loc = await get_or_create_warehouse_bin(db, _tid_del, wh_code)

    for l in inv.lines:
        # عكس حركات المخزون لو كانت الفاتورة أثّرت على المخزون
        if l.update_stock and l.material_id:
            mat = await db.get(Material, l.material_id)
            if mat and mat.stock_qty >= Decimal(str(l.qty)):
                await post_issue(
                    db, material=mat, qty=Decimal(str(l.qty)), unit_cost=mat.unit_cost or Decimal("0"),
                    source_doc_type="purchase_invoice_delete", source_doc_id=inv.id,
                    source_location_id=bin_loc.id,
                    reference=inv.code, notes=f"حذف فاتورة شراء: {inv.code}",
                )
            elif mat:
                mat.stock_qty = Decimal("0")
        await db.delete(l)
    # عكس الجزء غير المدفوع فقط من رصيد المورد
    # (الجزء المدفوع انعكس بالفعل عند تسجيل كل دفعة)
    unpaid = inv.total - inv.paid_amount
    if inv.supplier_id and unpaid > 0:
        sup = await db.get(Supplier, inv.supplier_id)
        if sup:
            sup.balance -= unpaid
    # قيد عكسي في دفتر الأستاذ
    await journal_purchase_invoice_delete(db, _tid_del, inv.id, inv.code, inv.tax_amount, inv.total, inv.date)

    # حذف مرفقات الفاتورة (ملفات + سجلات) — كان الحذف السابق بيمسح الفاتورة
    # وبنودها بس، ويسيب سجلات FileAttachment يتيمة تشاور على invoice_id بقى
    # مش موجود، والملفات الفعلية على القرص من غير أي مرجع ليها تاني (تسريب
    # مساحة تخزين + بيانات معلّقة للأبد).
    atts_res = await db.execute(
        select(FileAttachment).where(
            FileAttachment.entity_type == "purchase_invoice",
            FileAttachment.entity_id == iid,
        )
    )
    for att in atts_res.scalars().all():
        att_path = UPLOAD_DIR / att.stored_name
        if att_path.exists():
            att_path.unlink()
        await db.delete(att)

    await db.delete(inv)
    await db.commit()
    return {"message": "تم الحذف", "id": iid}


# ── File Attachments ───────────────────────────────────────────

@router.post("/invoices/{iid}/attachment")
async def upload_attachment(
    iid: int,
    file: UploadFile = File(...),
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    inv = (await db.execute(await _tenant_invoice_query(iid))).scalar_one_or_none()
    if not inv: raise HTTPException(404)

    ext = _validate_image_attachment(file)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"pi_{iid}_{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / stored

    content = await file.read()
    _check_attachment_size(content)
    dest.write_bytes(content)

    from app.core.database import current_tenant_id as _ctid_att1
    att = FileAttachment(
        tenant_id=_ctid_att1.get() or 1,
        entity_type="purchase_invoice",
        entity_id=iid,
        original_name=(file.filename or "")[:300],
        stored_name=stored,
        file_size=len(content),
        mime_type=(file.content_type or "")[:100] or None,
        notes=notes,
    )
    db.add(att)
    await db.commit()
    await db.refresh(att)
    return {
        "id": att.id, "original_name": att.original_name,
        "stored_name": att.stored_name, "file_size": att.file_size,
    }


async def _get_tenant_attachment(db: AsyncSession, att_id: int) -> Optional[FileAttachment]:
    from app.core.database import current_tenant_id as _ctid_ga
    tid = _ctid_ga.get()
    q = select(FileAttachment).where(FileAttachment.id == att_id)
    if tid is not None:
        q = q.where(FileAttachment.tenant_id == tid)
    return (await db.execute(q)).scalar_one_or_none()


@router.get("/attachments/{att_id}/download")
async def download_attachment(att_id: int, db: AsyncSession = Depends(get_db)):
    att = await _get_tenant_attachment(db, att_id)
    if not att: raise HTTPException(404)
    path = UPLOAD_DIR / att.stored_name
    if not path.exists(): raise HTTPException(404, "الملف غير موجود على القرص")
    return FileResponse(path, filename=att.original_name, media_type=att.mime_type or "application/octet-stream")


@router.delete("/attachments/{att_id}")
async def delete_attachment(att_id: int, db: AsyncSession = Depends(get_db)):
    att = await _get_tenant_attachment(db, att_id)
    if not att: raise HTTPException(404)
    path = UPLOAD_DIR / att.stored_name
    if path.exists():
        path.unlink()
    await db.delete(att)
    await db.commit()
    return {"message": "تم الحذف", "id": att_id}


# ── Generic Attachment Upload (for any entity) ─────────────────

@router.post("/attach")
async def upload_generic_attachment(
    entity_type: str,
    entity_id: int,
    file: UploadFile = File(...),
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """رفع مرفق لأي كيان في النظام (فاتورة بيع، حركة مخزون، الخ) — صور فقط"""
    # entity_type بيدخل في اسم الملف على القرص — منع path traversal
    # (entity_type=../../x كان بيكتب الملف برا مجلد المرفقات)
    if not _ENTITY_TYPE_RE.match(entity_type or ""):
        raise HTTPException(400, "نوع الكيان غير صالح")
    if entity_id <= 0:
        raise HTTPException(400, "معرّف الكيان غير صالح")
    ext = _validate_image_attachment(file)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{entity_type}_{entity_id}_{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / stored
    content = await file.read()
    _check_attachment_size(content)
    dest.write_bytes(content)

    from app.core.database import current_tenant_id as _ctid_att2
    att = FileAttachment(
        tenant_id=_ctid_att2.get() or 1,
        entity_type=entity_type,
        entity_id=entity_id,
        original_name=(file.filename or "")[:300],
        stored_name=stored,
        file_size=len(content),
        mime_type=(file.content_type or "")[:100] or None,
        notes=notes,
    )
    db.add(att)
    await db.commit()
    await db.refresh(att)
    return {
        "id": att.id, "entity_type": entity_type, "entity_id": entity_id,
        "original_name": att.original_name, "file_size": att.file_size,
    }


@router.get("/attachments")
async def list_attachments(entity_type: str, entity_id: int, db: AsyncSession = Depends(get_db)):
    """جلب كل مرفقات كيان معين"""
    from app.core.database import current_tenant_id as _ctid_la
    tid = _ctid_la.get()
    q = select(FileAttachment).where(
        FileAttachment.entity_type == entity_type,
        FileAttachment.entity_id == entity_id,
    )
    if tid is not None:
        q = q.where(FileAttachment.tenant_id == tid)
    res = await db.execute(q.order_by(FileAttachment.created_at))
    return [{
        "id": a.id, "original_name": a.original_name,
        "stored_name": a.stored_name, "file_size": a.file_size,
        "mime_type": a.mime_type, "notes": a.notes,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    } for a in res.scalars().all()]


# ── Print / HTML View ──────────────────────────────────────────

@print_router.get("/invoices/{iid}/print", response_class=HTMLResponse)
async def print_purchase_invoice(iid: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        (await _tenant_invoice_query(iid)).options(
            selectinload(PurchaseInvoice.supplier),
            selectinload(PurchaseInvoice.lines).selectinload(PurchaseInvoiceLine.material),
        )
    )
    inv = res.scalar_one_or_none()
    if not inv: raise HTTPException(404)

    lines_html = ""
    for i, l in enumerate(inv.lines):
        bg = "#f9f9f7" if i % 2 == 0 else "#fff"
        # كل النصوص اللي بيدخلها المستخدم لازم تتعمل escape (منع XSS مخزّن في صفحة الطباعة)
        mat_info = _html_mod.escape(f"{l.material.code} — {l.material.name_ar}" if l.material else str(l.description or ""))
        lines_html += f"""
        <tr style="background:{bg}">
          <td style="padding:9px 12px;text-align:center;color:#6b6a64;font-size:12px">{i+1}</td>
          <td style="padding:9px 12px">{mat_info}</td>
          <td style="padding:9px 12px;text-align:center">{float(l.qty):.3f} {_html_mod.escape(str(l.unit or ""))}</td>
          <td style="padding:9px 12px;text-align:left">{float(l.unit_cost):,.2f}</td>
          <td style="padding:9px 12px;text-align:left;font-weight:600">{float(l.total_cost):,.2f}</td>
        </tr>"""

    import html as _html
    def _e(s): return _html.escape(str(s or ""))

    supplier_name  = _e(inv.supplier_name or (inv.supplier.name if inv.supplier else "غير محدد"))
    supplier_phone = _e(inv.supplier.phone if inv.supplier else "")
    supplier_tax   = _e(inv.supplier.tax_id if inv.supplier else "")

    # ── بيانات الورشة مع فلتر tenant صريح ──
    from sqlalchemy import select as _sel
    from app.core.database import current_tenant_id as _ctid
    _tid = _ctid.get()
    _q = _sel(WorkshopProfile)
    if _tid is not None:
        _q = _q.where(WorkshopProfile.tenant_id == _tid)
    _pr = await db.execute(_q.limit(1))
    prof = _pr.scalar_one_or_none()
    company_name    = _e((prof.name if prof else None) or "ورشة معدات الأعلاف")
    company_sub     = _e((prof.description if prof else None) or "تصنيع وتوريد معدات الأعلاف")
    company_phone   = _e(((prof.mobile or prof.phone) if prof else None) or "")
    company_address = _e((prof.address if prof else None) or "")
    company_tax     = _e((prof.tax_id if prof else None) or "")
    company_creg    = _e((prof.commercial_reg if prof else None) or "")
    company_footer  = _e((prof.report_footer if prof else None) or "شكراً لتعاملكم معنا")
    logo_html = (
        f'<img src="{_html_mod.escape(str(prof.logo_data), quote=True)}" alt="logo" '
        f'style="max-height:70px;max-width:180px;object-fit:contain;margin-bottom:10px"/>'
        if prof and prof.logo_data else ""
    )
    legal_html = ""
    if company_tax:
        legal_html += f'<div style="font-size:12px;color:#6b6a64">ب.ض: {company_tax}</div>'
    if company_creg:
        legal_html += f'<div style="font-size:12px;color:#6b6a64">س.ت: {company_creg}</div>'

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head><meta charset="UTF-8"/><title>فاتورة شراء — {_html_mod.escape(str(inv.code or ""))}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',Tahoma,Arial,sans-serif;font-size:13px;color:#1a1a18;background:#fff;direction:rtl}}
  @media print{{.no-print{{display:none!important}}}}
  .page{{max-width:800px;margin:0 auto;padding:32px}}
  .header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:28px;border-bottom:3px solid #0F6E56;padding-bottom:20px}}
  table{{width:100%;border-collapse:collapse;margin-bottom:20px}}
  thead th{{background:#0F6E56;color:#fff;padding:10px 12px;font-size:12px}}
  .btn{{position:fixed;bottom:24px;background:#0F6E56;color:#fff;border:none;padding:12px 24px;border-radius:8px;font-size:14px;cursor:pointer}}
  .footer{{margin-top:32px;text-align:center;font-size:11px;color:#6b6a64;border-top:1px solid #e2e0d8;padding-top:16px}}
</style></head>
<body onload="setTimeout(()=>window.print(),400)"><div class="page">
  <div class="header">
    <div>
      {logo_html}
      <div style="font-size:22px;font-weight:700;color:#0F6E56">{company_name}</div>
      <div style="font-size:12px;color:#6b6a64;margin-top:4px">{company_sub}</div>
      <div style="font-size:12px;color:#6b6a64;margin-top:4px">{company_phone}</div>
      <div style="font-size:12px;color:#6b6a64">{company_address}</div>
      {legal_html}
    </div>
    <div style="text-align:left">
      <div style="font-size:20px;font-weight:700">فاتورة شراء</div>
      <div style="color:#6b6a64">رقم: {_html_mod.escape(str(inv.code or ""))}</div>
      <div style="color:#6b6a64">التاريخ: {inv.date}</div>
    </div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px">
    <div style="background:#f9f9f7;border-radius:8px;padding:14px">
      <div style="font-size:10px;color:#6b6a64;font-weight:600;margin-bottom:6px">المورد</div>
      <div style="font-size:14px;font-weight:600">{supplier_name}</div>
      <div style="font-size:12px;color:#6b6a64">{supplier_phone}</div>
      {f'<div style="font-size:12px;color:#6b6a64">الرقم الضريبي: {supplier_tax}</div>' if supplier_tax else ''}
    </div>
    <div style="background:#f9f9f7;border-radius:8px;padding:14px">
      <div style="font-size:10px;color:#6b6a64;font-weight:600;margin-bottom:6px">تفاصيل الفاتورة</div>
      <div>الإجمالي: <b>{float(inv.total):,.2f} ج</b></div>
      <div>المدفوع: {float(inv.paid_amount):,.2f} ج</div>
      <div style="color:#854F0B">المتبقي: {float(inv.total - inv.paid_amount):,.2f} ج</div>
    </div>
  </div>
  <table>
    <thead><tr>
      <th style="width:40px;text-align:center">#</th>
      <th>المادة / الوصف</th>
      <th style="width:100px;text-align:center">الكمية / الوحدة</th>
      <th style="width:110px;text-align:left">سعر الوحدة (ج)</th>
      <th style="width:110px;text-align:left">الإجمالي (ج)</th>
    </tr></thead>
    <tbody>{lines_html}</tbody>
  </table>
  {f'<div style="background:#f9f9f7;border-radius:8px;padding:14px;font-size:12px;color:#6b6a64"><b>ملاحظات:</b> {_html_mod.escape(str(inv.notes))}</div>' if inv.notes else ''}
  <div class="footer">{company_footer} — {company_name}</div>
</div>
<button class="btn no-print" style="left:24px" onclick="window.print()">🖨️ طباعة / تنزيل PDF</button>
</body></html>"""


# ── Dashboard Summary ──────────────────────────────────────────

@router.get("/dashboard")
async def purchases_dashboard(db: AsyncSession = Depends(get_db)):
    """ملخص المشتريات"""
    from sqlalchemy import extract
    from datetime import date as dt
    from app.core.database import current_tenant_id as _ctid_dash
    today = dt.today()
    tid = _ctid_dash.get()

    sup_q = select(func.count()).select_from(Supplier).where(Supplier.is_active == True)
    inv_count_q = select(func.count()).select_from(PurchaseInvoice)
    inv_total_q = select(func.coalesce(func.sum(PurchaseInvoice.total), 0))
    unpaid_q = select(func.coalesce(func.sum(PurchaseInvoice.total - PurchaseInvoice.paid_amount), 0)) \
        .where(PurchaseInvoice.status.in_(["received", "partial"]))
    mtd_q = select(func.coalesce(func.sum(PurchaseInvoice.total), 0)).where(
        extract('month', PurchaseInvoice.date) == today.month,
        extract('year', PurchaseInvoice.date) == today.year,
    )
    if tid is not None:
        # بدون فلترة الـ tenant هنا كانت اللوحة بترجّع أرقام مجمّعة من كل
        # المستأجرين مع بعض (تسريب بيانات تجاري بين tenants مختلفين)
        sup_q       = sup_q.where(Supplier.tenant_id == tid)
        inv_count_q = inv_count_q.where(PurchaseInvoice.tenant_id == tid)
        inv_total_q = inv_total_q.where(PurchaseInvoice.tenant_id == tid)
        unpaid_q    = unpaid_q.where(PurchaseInvoice.tenant_id == tid)
        mtd_q       = mtd_q.where(PurchaseInvoice.tenant_id == tid)

    total_suppliers = await db.scalar(sup_q)
    total_invoices  = await db.scalar(inv_count_q)
    total_value     = await db.scalar(inv_total_q)
    unpaid_value    = await db.scalar(unpaid_q)
    mtd_value       = await db.scalar(mtd_q)

    return {
        "total_suppliers": total_suppliers,
        "total_invoices":  total_invoices,
        "total_value":     float(total_value or 0),
        "unpaid_value":    float(unpaid_value or 0),
        "mtd_value":       float(mtd_value or 0),
    }
