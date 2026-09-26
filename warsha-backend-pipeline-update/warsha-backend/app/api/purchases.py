"""
Purchases API — الموردون وفواتير الشراء
"""
import os
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

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

router = APIRouter(prefix="/purchases", tags=["purchases"], dependencies=[Depends(require_any)])
print_router = APIRouter(prefix="/purchases", tags=["purchases_print"], dependencies=[Depends(require_any)])


async def _next_supplier_code(db: AsyncSession) -> str:
    count = await db.scalar(select(func.count()).select_from(Supplier))
    n = (count or 0) + 1
    while True:
        code = f"SUP-{n:04d}"
        exists = await db.scalar(select(Supplier.id).where(Supplier.code == code))
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
    return f"PI-{year}-{n:04d}"

UPLOAD_DIR = Path(os.getenv("ERP_UPLOAD_DIR", "/app/uploads")) / "attachments"

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


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


class PaymentIn(BaseModel):
    amount: float
    payment_date: Optional[date] = None
    payment_method: Optional[str] = None   # cash | transfer | cheque | bill
    reference: Optional[str] = None        # رقم الشيك / رقم التحويل
    notes: Optional[str] = None
    wht_amount: float = 0                  # ضريبة الخصم تحت الحساب المستقطعة من الدفعة


class SupplierPaymentIn(BaseModel):
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
    res = await db.execute(
        select(Supplier).where(Supplier.is_active == True).order_by(Supplier.name)
    )
    return [supplier_dict(s) for s in res.scalars().all()]


@router.get("/suppliers/next-code")
async def get_next_supplier_code(db: AsyncSession = Depends(get_db)):
    return {"code": await _next_supplier_code(db)}


@router.post("/suppliers", status_code=201)
async def create_supplier(data: SupplierIn, db: AsyncSession = Depends(get_db)):
    from app.core.database import current_tenant_id as _ctid_sup
    d = data.model_dump()
    d['code'] = d.get('code') or await _next_supplier_code(db)
    d['tenant_id'] = _ctid_sup.get() or 1
    s = Supplier(**d)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return supplier_dict(s)


@router.get("/suppliers/{sid}")
async def get_supplier(sid: int, db: AsyncSession = Depends(get_db)):
    s = await db.get(Supplier, sid)
    if not s: raise HTTPException(404)
    return supplier_dict(s)


@router.put("/suppliers/{sid}")
async def update_supplier(sid: int, data: SupplierIn, db: AsyncSession = Depends(get_db)):
    s = await db.get(Supplier, sid)
    if not s: raise HTTPException(404)
    d = data.model_dump()
    if not d.get("code"):
        d["code"] = s.code  # حافظ على الكود الموجود لو لم يُرسَل
    for k, v in d.items():
        setattr(s, k, v)
    await db.commit()
    await db.refresh(s)
    return supplier_dict(s)


@router.delete("/suppliers/{sid}")
async def delete_supplier(sid: int, db: AsyncSession = Depends(get_db)):
    s = await db.get(Supplier, sid)
    if not s: raise HTTPException(404)
    s.is_active = False
    await db.commit()
    return {"message": "تم الأرشفة", "id": sid}


@router.get("/suppliers/{sid}/statement")
async def supplier_statement(sid: int, db: AsyncSession = Depends(get_db)):
    """كشف حساب مورد"""
    s = await db.get(Supplier, sid)
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
    s = await db.get(Supplier, sid)
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
    q = select(PurchaseInvoice).options(
        selectinload(PurchaseInvoice.supplier),
        selectinload(PurchaseInvoice.lines),
        selectinload(PurchaseInvoice.attachments),
    )
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

    from app.core.database import current_tenant_id as _ctid_pi
    _tid_pi = _ctid_pi.get() or 1
    internal_code = await _next_internal_code(db, _tid_pi)

    # محطة 15 (الروادماب الأصلي): تحديد الموقع الفعلي (RAW/IMPORTED) مرة واحدة
    # لكل الفاتورة، بدل ما نعدّل stock_qty يدويًا من غير أي أثر في StockQuant
    from app.services.inventory_ledger import is_import_supplier, get_or_create_warehouse_bin, post_receipt
    supplier_for_loc = await db.get(Supplier, data.supplier_id)
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


@router.get("/invoices/{iid}")
async def get_purchase_invoice(iid: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(PurchaseInvoice)
        .options(selectinload(PurchaseInvoice.supplier),
                 selectinload(PurchaseInvoice.lines).selectinload(PurchaseInvoiceLine.material))
        .where(PurchaseInvoice.id == iid)
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
        select(PurchaseInvoice).options(selectinload(PurchaseInvoice.lines))
        .where(PurchaseInvoice.id == iid)
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

    from app.core.database import current_tenant_id as _ctid_upd
    _tid_upd = _ctid_upd.get() or 1

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
    new_wh_code = "IMPORTED" if is_import_supplier(await db.get(Supplier, data.supplier_id) if data.supplier_id else None) else "RAW"
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
    sup = await db.get(Supplier, data.supplier_id)
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
        inv = await db.get(PurchaseInvoice, data.invoice_id)
        if not inv:
            raise HTTPException(404, "فاتورة الشراء غير موجودة")
        remaining = inv.total - inv.paid_amount
        invoice_amount = min(amount, remaining) if remaining > 0 else Decimal("0")
        excess = amount - invoice_amount
        inv.paid_amount = inv.paid_amount + invoice_amount
        if inv.paid_amount >= inv.total:
            inv.status = "paid"
        elif inv.paid_amount > 0:
            inv.status = "partial"

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
    inv = await db.get(PurchaseInvoice, iid)
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
        select(PurchaseInvoice)
        .options(selectinload(PurchaseInvoice.lines))
        .where(PurchaseInvoice.id == iid)
    )
    inv = res.scalar_one_or_none()
    if not inv: raise HTTPException(404)
    if inv.status == "paid":
        raise HTTPException(400, "لا يمكن حذف فاتورة مدفوعة بالكامل")

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
    inv = await db.get(PurchaseInvoice, iid)
    if not inv: raise HTTPException(404)

    ext = _validate_image_attachment(file)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"pi_{iid}_{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / stored

    content = await file.read()
    dest.write_bytes(content)

    from app.core.database import current_tenant_id as _ctid_att1
    att = FileAttachment(
        tenant_id=_ctid_att1.get() or 1,
        entity_type="purchase_invoice",
        entity_id=iid,
        original_name=file.filename,
        stored_name=stored,
        file_size=len(content),
        mime_type=file.content_type,
        notes=notes,
    )
    db.add(att)
    await db.commit()
    await db.refresh(att)
    return {
        "id": att.id, "original_name": att.original_name,
        "stored_name": att.stored_name, "file_size": att.file_size,
    }


@router.get("/attachments/{att_id}/download")
async def download_attachment(att_id: int, db: AsyncSession = Depends(get_db)):
    att = await db.get(FileAttachment, att_id)
    if not att: raise HTTPException(404)
    path = UPLOAD_DIR / att.stored_name
    if not path.exists(): raise HTTPException(404, "الملف غير موجود على القرص")
    return FileResponse(path, filename=att.original_name, media_type=att.mime_type or "application/octet-stream")


@router.delete("/attachments/{att_id}")
async def delete_attachment(att_id: int, db: AsyncSession = Depends(get_db)):
    att = await db.get(FileAttachment, att_id)
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
    ext = _validate_image_attachment(file)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{entity_type}_{entity_id}_{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / stored
    content = await file.read()
    dest.write_bytes(content)

    from app.core.database import current_tenant_id as _ctid_att2
    att = FileAttachment(
        tenant_id=_ctid_att2.get() or 1,
        entity_type=entity_type,
        entity_id=entity_id,
        original_name=file.filename,
        stored_name=stored,
        file_size=len(content),
        mime_type=file.content_type,
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
    res = await db.execute(
        select(FileAttachment).where(
            FileAttachment.entity_type == entity_type,
            FileAttachment.entity_id == entity_id,
        ).order_by(FileAttachment.created_at)
    )
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
        select(PurchaseInvoice)
        .options(selectinload(PurchaseInvoice.supplier),
                 selectinload(PurchaseInvoice.lines).selectinload(PurchaseInvoiceLine.material))
        .where(PurchaseInvoice.id == iid)
    )
    inv = res.scalar_one_or_none()
    if not inv: raise HTTPException(404)

    lines_html = ""
    for i, l in enumerate(inv.lines):
        bg = "#f9f9f7" if i % 2 == 0 else "#fff"
        mat_info = f"{l.material.code} — {l.material.name_ar}" if l.material else l.description
        lines_html += f"""
        <tr style="background:{bg}">
          <td style="padding:9px 12px;text-align:center;color:#6b6a64;font-size:12px">{i+1}</td>
          <td style="padding:9px 12px">{mat_info}</td>
          <td style="padding:9px 12px;text-align:center">{float(l.qty):.3f} {l.unit}</td>
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
        f'<img src="{prof.logo_data}" alt="logo" '
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
<head><meta charset="UTF-8"/><title>فاتورة شراء — {inv.code}</title>
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
      <div style="color:#6b6a64">رقم: {inv.code}</div>
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
  {f'<div style="background:#f9f9f7;border-radius:8px;padding:14px;font-size:12px;color:#6b6a64"><b>ملاحظات:</b> {inv.notes}</div>' if inv.notes else ''}
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
    today = dt.today()

    total_suppliers = await db.scalar(select(func.count()).select_from(Supplier).where(Supplier.is_active == True))
    total_invoices  = await db.scalar(select(func.count()).select_from(PurchaseInvoice))
    total_value     = await db.scalar(select(func.coalesce(func.sum(PurchaseInvoice.total), 0)))
    unpaid_value    = await db.scalar(
        select(func.coalesce(func.sum(PurchaseInvoice.total - PurchaseInvoice.paid_amount), 0))
        .where(PurchaseInvoice.status.in_(["received", "partial"]))
    )
    mtd_value = await db.scalar(
        select(func.coalesce(func.sum(PurchaseInvoice.total), 0))
        .where(
            extract('month', PurchaseInvoice.date) == today.month,
            extract('year', PurchaseInvoice.date) == today.year,
        )
    )

    return {
        "total_suppliers": total_suppliers,
        "total_invoices":  total_invoices,
        "total_value":     float(total_value or 0),
        "unpaid_value":    float(unpaid_value or 0),
        "mtd_value":       float(mtd_value or 0),
    }
