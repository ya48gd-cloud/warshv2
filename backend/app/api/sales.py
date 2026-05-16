"""
Sales API — عروض الأسعار والفواتير
"""
from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional, List

from app.core.database import get_db
from app.models.models import Quotation, QuotationLine, Invoice, InvoiceLine, Customer, CustomerOrder, CustomerPayment

router = APIRouter(prefix="/sales", tags=["sales"])


# ── Schemas ────────────────────────────────────────────────────
class LineIn(BaseModel):
    description: str
    qty: Decimal = Decimal("1")
    unit: str = "pcs"
    unit_price: Decimal
    notes: Optional[str] = None
    sort_order: int = 0


class QuotationIn(BaseModel):
    code: str
    customer_id: int
    date: date
    valid_until: Optional[date] = None
    tax_pct: Decimal = Decimal("0")
    discount_pct: Decimal = Decimal("0")
    notes: Optional[str] = None
    terms: Optional[str] = None
    lines: List[LineIn] = []


class InvoiceIn(BaseModel):
    code: str
    customer_id: int
    quotation_id: Optional[int] = None
    date: date
    due_date: Optional[date] = None
    tax_pct: Decimal = Decimal("0")
    discount_pct: Decimal = Decimal("0")
    notes: Optional[str] = None
    terms: Optional[str] = None
    lines: List[LineIn] = []


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
                   "sort_order": l.sort_order} for l in q.lines],
    }


def inv_dict(inv: Invoice):
    sm = {"unpaid": "غير مدفوعة", "partial": "مدفوعة جزئياً", "paid": "مدفوعة"}
    remaining = float(inv.total) - float(inv.paid_amount)
    return {
        "id": inv.id, "code": inv.code,
        "quotation_id": inv.quotation_id,
        "customer_id": inv.customer_id,
        "customer_name": inv.customer.name if inv.customer else "",
        "customer_phone": inv.customer.phone if inv.customer else "",
        "customer_address": inv.customer.address if inv.customer else "",
        "date": str(inv.date), "due_date": str(inv.due_date) if inv.due_date else None,
        "status": inv.status, "status_ar": sm.get(inv.status, inv.status),
        "subtotal": float(inv.subtotal), "tax_pct": float(inv.tax_pct),
        "tax_amount": float(inv.tax_amount), "discount_pct": float(inv.discount_pct),
        "discount_amt": float(inv.discount_amt), "total": float(inv.total),
        "paid_amount": float(inv.paid_amount), "remaining": remaining,
        "notes": inv.notes, "terms": inv.terms,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "lines": [{"id": l.id, "description": l.description, "qty": float(l.qty),
                   "unit": l.unit, "unit_price": float(l.unit_price),
                   "total_price": float(l.total_price), "notes": l.notes} for l in inv.lines],
    }


# ══ QUOTATIONS ═══════════════════════════════════════════════

@router.get("/quotations")
async def list_quotations(db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(Quotation).options(selectinload(Quotation.customer), selectinload(Quotation.lines))
        .order_by(Quotation.created_at.desc())
    )
    return [quot_dict(q) for q in r.scalars().all()]


@router.post("/quotations", status_code=201)
async def create_quotation(data: QuotationIn, db: AsyncSession = Depends(get_db)):
    subtotal, disc_amt, tax_amt, total = calc_totals(data.lines, data.tax_pct, data.discount_pct)
    q = Quotation(
        code=data.code, customer_id=data.customer_id, date=data.date,
        valid_until=data.valid_until, tax_pct=data.tax_pct, discount_pct=data.discount_pct,
        subtotal=subtotal, discount_amt=disc_amt, tax_amount=tax_amt, total=total,
        notes=data.notes, terms=data.terms,
    )
    db.add(q)
    await db.flush()
    for i, l in enumerate(data.lines):
        tp = Decimal(str(l.qty)) * Decimal(str(l.unit_price))
        db.add(QuotationLine(quotation_id=q.id, description=l.description,
            qty=l.qty, unit=l.unit, unit_price=l.unit_price,
            total_price=tp, notes=l.notes, sort_order=l.sort_order or i))
    await db.commit()
    await db.refresh(q)
    return {"id": q.id, "code": q.code, "total": float(q.total)}


@router.get("/quotations/{qid}")
async def get_quotation(qid: int, db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(Quotation).options(selectinload(Quotation.customer), selectinload(Quotation.lines))
        .where(Quotation.id == qid)
    )
    q = r.scalar_one_or_none()
    if not q: raise HTTPException(404)
    return quot_dict(q)


@router.put("/quotations/{qid}/status")
async def update_quotation_status(qid: int, status: str, db: AsyncSession = Depends(get_db)):
    q = await db.get(Quotation, qid)
    if not q: raise HTTPException(404)
    q.status = status
    await db.commit()
    return {"id": q.id, "status": q.status}


@router.delete("/quotations/{qid}")
async def delete_quotation(qid: int, db: AsyncSession = Depends(get_db)):
    q = await db.get(Quotation, qid)
    if not q: raise HTTPException(404)
    lines = (await db.execute(select(QuotationLine).where(QuotationLine.quotation_id==qid))).scalars().all()
    for l in lines: await db.delete(l)
    await db.delete(q)
    await db.commit()
    return {"message": "تم الحذف"}


@router.post("/quotations/{qid}/to-invoice")
async def quotation_to_invoice(qid: int, invoice_code: str, db: AsyncSession = Depends(get_db)):
    """تحويل عرض سعر إلى فاتورة"""
    r = await db.execute(
        select(Quotation).options(selectinload(Quotation.lines)).where(Quotation.id == qid)
    )
    q = r.scalar_one_or_none()
    if not q: raise HTTPException(404)
    inv = Invoice(
        code=invoice_code, customer_id=q.customer_id, quotation_id=qid,
        date=date.today(), tax_pct=q.tax_pct, discount_pct=q.discount_pct,
        subtotal=q.subtotal, discount_amt=q.discount_amt,
        tax_amount=q.tax_amount, total=q.total,
        notes=q.notes, terms=q.terms,
    )
    db.add(inv)
    await db.flush()
    for l in q.lines:
        db.add(InvoiceLine(invoice_id=inv.id, description=l.description,
            qty=l.qty, unit=l.unit, unit_price=l.unit_price,
            total_price=l.total_price, notes=l.notes, sort_order=l.sort_order))
    q.status = "accepted"
    await db.commit()
    return {"id": inv.id, "code": inv.code}


# ══ INVOICES ══════════════════════════════════════════════════

@router.get("/invoices")
async def list_invoices(db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(Invoice).options(selectinload(Invoice.customer), selectinload(Invoice.lines))
        .order_by(Invoice.created_at.desc())
    )
    return [inv_dict(inv) for inv in r.scalars().all()]


@router.post("/invoices", status_code=201)
async def create_invoice(data: InvoiceIn, db: AsyncSession = Depends(get_db)):
    subtotal, disc_amt, tax_amt, total = calc_totals(data.lines, data.tax_pct, data.discount_pct)
    inv = Invoice(
        code=data.code, customer_id=data.customer_id, quotation_id=data.quotation_id,
        date=data.date, due_date=data.due_date, tax_pct=data.tax_pct,
        discount_pct=data.discount_pct, subtotal=subtotal,
        discount_amt=disc_amt, tax_amount=tax_amt, total=total,
        notes=data.notes, terms=data.terms,
    )
    db.add(inv)
    await db.flush()
    for i, l in enumerate(data.lines):
        tp = Decimal(str(l.qty)) * Decimal(str(l.unit_price))
        db.add(InvoiceLine(invoice_id=inv.id, description=l.description,
            qty=l.qty, unit=l.unit, unit_price=l.unit_price,
            total_price=tp, notes=l.notes, sort_order=l.sort_order or i))
    # Update customer balance (سالب = عليه)
    cust = await db.get(Customer, data.customer_id)
    if cust:
        cust.balance = cust.balance - total
    await db.commit()
    await db.refresh(inv)
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


@router.post("/invoices/{iid}/payment")
async def record_invoice_payment(iid: int, amount: float, db: AsyncSession = Depends(get_db)):
    inv = await db.get(Invoice, iid)
    if not inv: raise HTTPException(404)
    inv.paid_amount = Decimal(str(inv.paid_amount)) + Decimal(str(amount))
    if inv.paid_amount >= inv.total:
        inv.status = "paid"
    elif inv.paid_amount > 0:
        inv.status = "partial"
    # Update customer balance (موجب = دفع)
    cust = await db.get(Customer, inv.customer_id)
    if cust:
        cust.balance = cust.balance + Decimal(str(amount))
    await db.commit()
    return {"paid_amount": float(inv.paid_amount), "remaining": float(inv.total - inv.paid_amount), "status": inv.status}


@router.delete("/invoices/{iid}")
async def delete_invoice(iid: int, db: AsyncSession = Depends(get_db)):
    inv = await db.get(Invoice, iid)
    if not inv: raise HTTPException(404)
    lines = (await db.execute(select(InvoiceLine).where(InvoiceLine.invoice_id==iid))).scalars().all()
    for l in lines: await db.delete(l)
    # Restore customer balance
    cust = await db.get(Customer, inv.customer_id)
    if cust:
        cust.balance = cust.balance + inv.total - inv.paid_amount
    await db.delete(inv)
    await db.commit()
    return {"message": "تم الحذف"}


# ══ PRINT ENDPOINTS ═══════════════════════════════════════════

def number_to_arabic_words(n: float) -> str:
    """تحويل الرقم لكلمات عربية مبسطة"""
    try:
        n = round(n, 2)
        integer = int(n)
        cents = round((n - integer) * 100)
        result = f"{integer:,} جنيه"
        if cents > 0:
            result += f" و{cents} قرش"
        return result
    except Exception:
        return str(n)


def build_print_html(doc_type: str, doc: dict, company: dict) -> str:
    is_quotation = doc_type == "quotation"
    title = "عرض سعر" if is_quotation else "فاتورة مبيعات"
    status_colors = {
        "draft": "#6b6a64", "sent": "#185FA5", "accepted": "#3B6D11",
        "rejected": "#A32D2D", "unpaid": "#854F0B", "partial": "#185FA5", "paid": "#3B6D11"
    }
    status_color = status_colors.get(doc.get("status", ""), "#6b6a64")

    lines_html = ""
    for i, line in enumerate(doc.get("lines", [])):
        bg = "#f9f9f7" if i % 2 == 0 else "#fff"
        lines_html += f"""
        <tr style="background:{bg}">
          <td style="padding:9px 12px;text-align:center;color:#6b6a64;font-size:12px">{i+1}</td>
          <td style="padding:9px 12px">{line['description']}</td>
          <td style="padding:9px 12px;text-align:center">{line['qty']} {line['unit']}</td>
          <td style="padding:9px 12px;text-align:left">{line['unit_price']:,.2f}</td>
          <td style="padding:9px 12px;text-align:left;font-weight:600">{line['total_price']:,.2f}</td>
        </tr>"""

    subtotal = doc.get("subtotal", 0)
    disc_pct = doc.get("discount_pct", 0)
    disc_amt = doc.get("discount_amt", 0)
    tax_pct  = doc.get("tax_pct", 0)
    tax_amt  = doc.get("tax_amount", 0)
    total    = doc.get("total", 0)
    paid     = doc.get("paid_amount", 0)
    remaining = doc.get("remaining", total)

    totals_html = f"""
      <tr><td style="padding:6px 0;color:#6b6a64">الإجمالي قبل الخصم</td>
          <td style="padding:6px 0;text-align:left;font-weight:500">{subtotal:,.2f} ج</td></tr>"""
    if disc_pct > 0:
        totals_html += f"""
      <tr><td style="padding:6px 0;color:#6b6a64">الخصم ({disc_pct}%)</td>
          <td style="padding:6px 0;text-align:left;color:#A32D2D">({disc_amt:,.2f} ج)</td></tr>"""
    if tax_pct > 0:
        totals_html += f"""
      <tr><td style="padding:6px 0;color:#6b6a64">ضريبة القيمة المضافة ({tax_pct}%)</td>
          <td style="padding:6px 0;text-align:left">{tax_amt:,.2f} ج</td></tr>"""
    totals_html += f"""
      <tr style="border-top:2px solid #0F6E56">
        <td style="padding:10px 0;font-weight:700;font-size:15px;color:#0F6E56">الإجمالي النهائي</td>
        <td style="padding:10px 0;text-align:left;font-weight:700;font-size:15px;color:#0F6E56">{total:,.2f} ج</td>
      </tr>"""
    if not is_quotation and paid > 0:
        totals_html += f"""
      <tr><td style="padding:6px 0;color:#3B6D11">المدفوع</td>
          <td style="padding:6px 0;text-align:left;color:#3B6D11">{paid:,.2f} ج</td></tr>
      <tr><td style="padding:6px 0;color:#854F0B;font-weight:600">المتبقي</td>
          <td style="padding:6px 0;text-align:left;color:#854F0B;font-weight:600">{remaining:,.2f} ج</td></tr>"""

    valid_line = ""
    if is_quotation and doc.get("valid_until"):
        valid_line = f'<div style="margin-top:4px;font-size:12px;color:#6b6a64">صالح حتى: {doc["valid_until"]}</div>'

    due_line = ""
    if not is_quotation and doc.get("due_date"):
        due_line = f'<div style="margin-top:4px;font-size:12px;color:#854F0B">تاريخ الاستحقاق: {doc["due_date"]}</div>'

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title} — {doc['code']}</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'Segoe UI',Tahoma,Arial,sans-serif; font-size:13px; color:#1a1a18; background:#fff; direction:rtl; }}
  @media print {{
    .no-print {{ display:none !important; }}
    body {{ margin:0; }}
    .page {{ box-shadow:none; margin:0; padding:16px; }}
  }}
  .page {{ max-width:800px; margin:0 auto; padding:32px; }}
  .header {{ display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:28px; border-bottom:3px solid #0F6E56; padding-bottom:20px; }}
  .company-name {{ font-size:22px; font-weight:700; color:#0F6E56; }}
  .company-sub {{ font-size:12px; color:#6b6a64; margin-top:4px; }}
  .doc-title {{ text-align:left; }}
  .doc-type {{ font-size:20px; font-weight:700; color:#1a1a18; }}
  .doc-code {{ font-size:13px; color:#6b6a64; margin-top:4px; }}
  .doc-date {{ font-size:12px; color:#6b6a64; margin-top:2px; }}
  .status-badge {{ display:inline-block; padding:3px 10px; border-radius:12px; font-size:11px; font-weight:600; color:{status_color}; background:{status_color}22; margin-top:6px; }}
  .parties {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-bottom:24px; }}
  .party-box {{ background:#f9f9f7; border-radius:8px; padding:14px; }}
  .party-label {{ font-size:10px; text-transform:uppercase; color:#6b6a64; font-weight:600; letter-spacing:.05em; margin-bottom:6px; }}
  .party-name {{ font-size:14px; font-weight:600; }}
  .party-detail {{ font-size:12px; color:#6b6a64; margin-top:3px; }}
  table {{ width:100%; border-collapse:collapse; margin-bottom:20px; }}
  thead th {{ background:#0F6E56; color:#fff; padding:10px 12px; font-size:12px; font-weight:500; }}
  .totals-table {{ width:280px; margin-right:auto; border-collapse:collapse; }}
  .totals-table td {{ padding:6px 0; font-size:13px; }}
  .notes-box {{ background:#f9f9f7; border-radius:8px; padding:14px; margin-top:20px; font-size:12px; color:#6b6a64; }}
  .amount-words {{ background:#E1F5EE; border-radius:8px; padding:12px 16px; margin-top:16px; font-size:13px; color:#0F6E56; font-weight:500; }}
  .footer {{ margin-top:32px; text-align:center; font-size:11px; color:#6b6a64; border-top:1px solid #e2e0d8; padding-top:16px; }}
  .print-btn {{ position:fixed; bottom:24px; left:24px; background:#0F6E56; color:#fff; border:none; padding:12px 24px; border-radius:8px; font-size:14px; cursor:pointer; box-shadow:0 4px 12px rgba(0,0,0,.2); }}
  .pdf-btn {{ position:fixed; bottom:24px; left:160px; background:#185FA5; color:#fff; border:none; padding:12px 24px; border-radius:8px; font-size:14px; cursor:pointer; box-shadow:0 4px 12px rgba(0,0,0,.2); }}
</style>
</head>
<body>
<div class="page" id="printable">

  <div class="header">
    <div>
      <div class="company-name">{company.get('name','ورشة معدات الأعلاف')}</div>
      <div class="company-sub">{company.get('sub','تصنيع وتوريد معدات الأعلاف')}</div>
      <div class="company-sub" style="margin-top:4px">{company.get('phone','')}</div>
      <div class="company-sub">{company.get('address','')}</div>
    </div>
    <div class="doc-title">
      <div class="doc-type">{title}</div>
      <div class="doc-code">رقم: {doc['code']}</div>
      <div class="doc-date">التاريخ: {doc['date']}</div>
      {valid_line}{due_line}
      <div class="status-badge">{doc.get('status_ar','')}</div>
    </div>
  </div>

  <div class="parties">
    <div class="party-box">
      <div class="party-label">مُقدَّم من</div>
      <div class="party-name">{company.get('name','ورشة معدات الأعلاف')}</div>
      <div class="party-detail">{company.get('phone','')}</div>
      <div class="party-detail">{company.get('address','')}</div>
    </div>
    <div class="party-box">
      <div class="party-label">مُقدَّم إلى</div>
      <div class="party-name">{doc.get('customer_name','')}</div>
      <div class="party-detail">{doc.get('customer_phone','') or ''}</div>
      <div class="party-detail">{doc.get('customer_address','') or ''}</div>
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th style="width:40px;text-align:center">#</th>
        <th>الوصف</th>
        <th style="width:100px;text-align:center">الكمية / الوحدة</th>
        <th style="width:110px;text-align:left">سعر الوحدة (ج)</th>
        <th style="width:110px;text-align:left">الإجمالي (ج)</th>
      </tr>
    </thead>
    <tbody>{lines_html}</tbody>
  </table>

  <div style="display:flex;justify-content:space-between;align-items:flex-start">
    <div style="flex:1">
      {f'<div class="amount-words">فقط: {number_to_arabic_words(total)}</div>' if total > 0 else ''}
      {f'<div class="notes-box"><b>ملاحظات:</b><br>{doc["notes"]}</div>' if doc.get("notes") else ''}
      {f'<div class="notes-box" style="margin-top:8px"><b>شروط الدفع:</b><br>{doc["terms"]}</div>' if doc.get("terms") else ''}
    </div>
    <div style="margin-right:32px">
      <table class="totals-table">
        {totals_html}
      </table>
    </div>
  </div>

  <div class="footer">
    شكراً لتعاملكم معنا — {company.get('name','ورشة معدات الأعلاف')}
    {f'<br>هاتف: {company.get("phone","")}' if company.get("phone") else ''}
  </div>
</div>

<button class="print-btn no-print" onclick="window.print()">🖨️ طباعة</button>
<button class="pdf-btn no-print" onclick="downloadPDF()">📥 تحميل PDF</button>

<script>
function downloadPDF() {{
  window.print(); // Browser print dialog handles PDF save
}}
</script>
</body></html>"""


COMPANY_INFO = {
    "name": "ورشة معدات الأعلاف",
    "sub": "تصنيع وتوريد معدات الأعلاف والتصنيع الغذائي",
    "phone": "01001234567",
    "address": "القاهرة، مصر",
}


@router.get("/quotations/{qid}/print", response_class=HTMLResponse)
async def print_quotation(qid: int, db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(Quotation).options(selectinload(Quotation.customer), selectinload(Quotation.lines))
        .where(Quotation.id == qid)
    )
    q = r.scalar_one_or_none()
    if not q: raise HTTPException(404)
    return build_print_html("quotation", quot_dict(q), COMPANY_INFO)


@router.get("/invoices/{iid}/print", response_class=HTMLResponse)
async def print_invoice(iid: int, db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(Invoice).options(selectinload(Invoice.customer), selectinload(Invoice.lines))
        .where(Invoice.id == iid)
    )
    inv = r.scalar_one_or_none()
    if not inv: raise HTTPException(404)
    return build_print_html("invoice", inv_dict(inv), COMPANY_INFO)
