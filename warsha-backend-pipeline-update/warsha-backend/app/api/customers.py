from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional, List

from app.core.database import get_db
from app.api.auth import require_any, get_current_user
from app.models.models import Customer, CustomerOrder, CustomerOrderLine, CustomerPayment, CustomerPaymentAllocation, CustomerAsset, Equipment, ExtProject, AssetMovement
from app.services.invoice_due import get_invoice_due_status

router = APIRouter(prefix="/customers", tags=["customers"], dependencies=[Depends(require_any)])


async def _next_customer_code(db: AsyncSession) -> str:
    count = await db.scalar(select(func.count()).select_from(Customer))
    n = (count or 0) + 1
    while True:
        code = f"CUST-{n:04d}"
        exists = await db.scalar(select(Customer.id).where(Customer.code == code))
        if not exists:
            return code
        n += 1


async def _next_order_code(db: AsyncSession) -> str:
    count = await db.scalar(select(func.count()).select_from(CustomerOrder))
    n = (count or 0) + 1
    while True:
        code = f"ORD-{n:04d}"
        exists = await db.scalar(select(CustomerOrder.id).where(CustomerOrder.code == code))
        if not exists:
            return code
        n += 1


# ── Schemas ────────────────────────────────────────────────────
class CustomerIn(BaseModel):
    code: Optional[str] = None
    name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    tax_id: Optional[str] = None
    credit_limit: Decimal = Decimal("0")
    notes: Optional[str] = None
    payment_terms: Optional[str] = None

class OrderLineIn(BaseModel):
    equipment_id: Optional[int] = None
    description: str
    qty: Decimal = Decimal("1")
    unit: str = "قطعة"
    unit_price: Decimal = Decimal("0")
    notes: Optional[str] = None

class OrderIn(BaseModel):
    code: Optional[str] = None
    customer_id: int
    location_id: Optional[int] = None
    equipment_id: Optional[int] = None
    description: Optional[str] = None
    quantity: int = 1
    unit_price: Decimal = Decimal("0")
    status: str = "pending"
    order_date: Optional[date] = None
    delivery_date: Optional[date] = None
    notes: Optional[str] = None
    lines: Optional[List[OrderLineIn]] = None

class PaymentIn(BaseModel):
    customer_id: int
    location_id: Optional[int] = None
    order_id: Optional[int] = None
    amount: Decimal
    payment_type: str = "cash"
    payment_date: date
    reference: Optional[str] = None
    notes: Optional[str] = None


def cust_dict(c: Customer):
    return {"id": c.id, "code": c.code, "name": c.name,
            "phone": c.phone, "address": c.address,
            "tax_id": c.tax_id,
            "credit_limit": float(c.credit_limit),
            "balance": float(c.balance),
            "notes": c.notes,
            "payment_terms": getattr(c, "payment_terms", None),
            "created_at": c.created_at.isoformat() if c.created_at else None}


# ── Customers CRUD ─────────────────────────────────────────────
@router.get("")
async def list_customers(db: AsyncSession = Depends(get_db)):
    from app.models.models import Invoice, CustomerOrder, CustomerPayment
    from sqlalchemy import func as sqlfunc
    res = await db.execute(select(Customer).order_by(Customer.name))
    customers = res.scalars().all()

    # Calculate live balance for each customer
    result = []
    for cust in customers:
        # Sum invoices
        inv_res = await db.execute(
            select(sqlfunc.coalesce(sqlfunc.sum(Invoice.total), 0),
                   sqlfunc.coalesce(sqlfunc.sum(Invoice.paid_amount), 0))
            .where(Invoice.customer_id == cust.id)
        )
        inv_total, inv_paid = inv_res.one()

        # Sum orders
        ord_res = await db.execute(
            select(sqlfunc.coalesce(sqlfunc.sum(CustomerOrder.total_price), 0))
            .where(CustomerOrder.customer_id == cust.id)
        )
        ord_total = ord_res.scalar()

        # Sum customer payments
        pay_res = await db.execute(
            select(sqlfunc.coalesce(sqlfunc.sum(CustomerPayment.amount), 0))
            .where(CustomerPayment.customer_id == cust.id)
        )
        pay_total = pay_res.scalar()

        # Last order date — مطلوب لحساب churn risk في لوحة التحليلات
        last_order_res = await db.execute(
            select(sqlfunc.max(CustomerOrder.order_date))
            .where(CustomerOrder.customer_id == cust.id)
        )
        last_order_date = last_order_res.scalar()
        # fallback: آخر فاتورة لو مافيش orders
        if not last_order_date:
            last_inv_res = await db.execute(
                select(sqlfunc.max(Invoice.date))
                .where(Invoice.customer_id == cust.id)
            )
            last_order_date = last_inv_res.scalar()

        total_charged = float(inv_total) + float(ord_total)
        # نستخدم inv_paid فقط (مصدر موحّد) لتجنب double-counting مع CustomerPayments
        total_paid    = float(inv_paid)
        balance_due   = total_charged - total_paid  # موجب = عليه

        d = cust_dict(cust)
        d['balance'] = balance_due    # موجب = عليه (consistent مع الفرونت إند)
        d['total_charged'] = total_charged
        d['total_paid'] = total_paid
        d['last_order_date'] = str(last_order_date) if last_order_date else None
        result.append(d)

    return result

@router.get("/next-code")
async def get_next_customer_code(db: AsyncSession = Depends(get_db)):
    return {"code": await _next_customer_code(db)}


@router.get("/{cust_id}")
async def get_customer(cust_id: int, db: AsyncSession = Depends(get_db)):
    c = await db.get(Customer, cust_id)
    if not c:
        raise HTTPException(404)
    return cust_dict(c)


@router.post("", status_code=201)
async def create_customer(data: CustomerIn, db: AsyncSession = Depends(get_db)):
    if data.credit_limit is not None and data.credit_limit < 0:
        raise HTTPException(400, "حد الائتمان لا يمكن أن يكون سالباً")
    from app.core.database import current_tenant_id as _ctid_cust
    d = data.model_dump()
    d['code'] = d.get('code') or await _next_customer_code(db)
    d['tenant_id'] = _ctid_cust.get() or 1
    c = Customer(**d)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return cust_dict(c)

@router.put("/{cust_id}")
async def update_customer(cust_id: int, data: CustomerIn, db: AsyncSession = Depends(get_db)):
    c = await db.get(Customer, cust_id)
    if not c: raise HTTPException(404)
    if data.credit_limit is not None and data.credit_limit < 0:
        raise HTTPException(400, "حد الائتمان لا يمكن أن يكون سالباً")
    d = data.model_dump()
    if not d.get("code"):
        d["code"] = c.code  # حافظ على الكود الموجود لو لم يُرسَل
    for k, v in d.items():
        setattr(c, k, v)
    await db.commit()
    await db.refresh(c)
    return cust_dict(c)

@router.delete("/{cust_id}")
async def delete_customer(cust_id: int, db: AsyncSession = Depends(get_db)):
    from app.models.models import Invoice, CustomerOrder
    from sqlalchemy import select, func as sqlfunc
    c = await db.get(Customer, cust_id)
    if not c: raise HTTPException(404)
    # منع الحذف لو في فواتير أو طلبات مرتبطة
    inv_count = await db.scalar(select(sqlfunc.count()).select_from(Invoice).where(Invoice.customer_id == cust_id))
    ord_count  = await db.scalar(select(sqlfunc.count()).select_from(CustomerOrder).where(CustomerOrder.customer_id == cust_id))
    if (inv_count or 0) > 0 or (ord_count or 0) > 0:
        raise HTTPException(400, f"لا يمكن حذف العميل — لديه {inv_count} فاتورة و{ord_count} طلب. أرشف العميل بدلاً من الحذف.")
    await db.delete(c)
    await db.commit()
    return {"message": "تم الحذف"}

@router.get("/{cust_id}/statement")
async def customer_statement(cust_id: int, db: AsyncSession = Depends(get_db)):
    """كشف حساب العميل الكامل"""
    from app.models.models import Invoice, WorkOrder, FieldServiceOrder
    c = await db.get(Customer, cust_id)
    if not c: raise HTTPException(404)

    orders_res = await db.execute(
        select(CustomerOrder).where(CustomerOrder.customer_id == cust_id)
        .order_by(CustomerOrder.order_date)
    )
    orders = orders_res.scalars().all()

    payments_res = await db.execute(
        select(CustomerPayment).where(CustomerPayment.customer_id == cust_id)
        .order_by(CustomerPayment.payment_date)
    )
    payments = payments_res.scalars().all()

    invoices_res = await db.execute(
        select(Invoice).where(Invoice.customer_id == cust_id)
        .order_by(Invoice.date)
    )
    invoices = invoices_res.scalars().all()

    assets_res = await db.execute(
        select(CustomerAsset).where(CustomerAsset.customer_id == cust_id)
        .order_by(CustomerAsset.created_at.desc())
    )
    assets = assets_res.scalars().all()

    customer_order_ids = [o.id for o in orders]
    # جلب أوامر الإنتاج: إما مرتبطة بطلب عميل أو مرتبطة مباشرة بالعميل
    from sqlalchemy import or_
    wo_cond = WorkOrder.customer_id == cust_id
    if customer_order_ids:
        wo_cond = or_(wo_cond, WorkOrder.customer_order_id.in_(customer_order_ids))
    wo_res = await db.execute(
        select(WorkOrder).where(wo_cond)
        .order_by(WorkOrder.created_at.desc())
    )
    work_orders = wo_res.scalars().all()

    ext_projects_res = await db.execute(
        select(ExtProject).where(ExtProject.client_id == cust_id)
        .order_by(ExtProject.id.desc())
    )
    ext_projects = ext_projects_res.scalars().all()

    fs_orders_res = await db.execute(
        select(FieldServiceOrder).where(FieldServiceOrder.customer_id == cust_id)
        .order_by(FieldServiceOrder.id.desc())
    )
    field_service_orders = fs_orders_res.scalars().all()

    # الإجمالي = طلبات العملاء + الفواتير
    # لا نضيف طلب الإجمالي إلا إذا لم يتحوّل إلى فاتورة بعد (منع double-counting)
    invoice_order_ids = {inv.quotation_id for inv in invoices if inv.quotation_id}
    total_orders   = sum(o.total_price for o in orders if o.status != 'cancelled')
    total_invoices = sum(inv.total for inv in invoices)
    total_charged  = total_orders + total_invoices

    # المدفوع: نستخدم مجموع ما دُفع على الفواتير فعلاً (invoice.paid_amount هو المصدر الأدق)
    # + أي دفعات لم تُوزَّع على فواتير بعد (دفعات مقدمة على طلبات أو دفعات غير مخصصة)
    total_inv_paid = sum(inv.paid_amount for inv in invoices)

    allocated_payment_ids: set[int] = set()
    if payments:
        alloc_res = await db.execute(
            select(CustomerPaymentAllocation.payment_id).where(
                CustomerPaymentAllocation.payment_id.in_([p.id for p in payments])
            )
        )
        allocated_payment_ids = {row[0] for row in alloc_res.all()}

    total_unallocated_paid = sum(
        p.amount for p in payments if p.id not in allocated_payment_ids
    )
    total_paid = total_inv_paid + total_unallocated_paid

    balance_due = total_charged - total_paid

    # ── ملخص الأقساط للعميل (خفيف — من الدالة المركزية) ──
    from datetime import date as _today_date
    _today = _today_date.today()
    overdue_installments_count = 0
    overdue_installments_total = Decimal("0")
    next_installment_date = None
    unpaid_invs = [inv for inv in invoices if inv.status in ("unpaid", "partial")]
    for inv in unpaid_invs:
        due_st = await get_invoice_due_status(db, inv, _today)
        if due_st.is_overdue:
            overdue_installments_count += 1
            overdue_installments_total += due_st.overdue_amount
        if due_st.next_due_date:
            if next_installment_date is None or due_st.next_due_date < next_installment_date:
                next_installment_date = due_st.next_due_date

    # لا نكتب إلى DB هنا — كشف الحساب للقراءة فقط، والرصيد يُحدَّث عبر العمليات المباشرة

    return {
        "customer": cust_dict(c),
        "total_orders":   float(total_orders),
        "total_invoices": float(total_invoices),
        "total_charged":  float(total_charged),
        "total_paid":     float(total_paid),
        "balance_due":    float(balance_due),
        "total_owed":     float(balance_due),
        "total_ordered":  float(total_charged),
        "overdue_installments_count": overdue_installments_count,
        "overdue_installments_total": float(overdue_installments_total),
        "next_installment_date": str(next_installment_date) if next_installment_date else None,
        "orders": [{
            "id": o.id, "code": o.code, "description": o.description,
            "quantity": o.quantity, "unit_price": float(o.unit_price),
            "total_price": float(o.total_price), "status": o.status,
            "order_date": str(o.order_date) if o.order_date else None,
            "delivery_date": str(o.delivery_date) if o.delivery_date else None,
            "location_id": o.location_id,
        } for o in orders],
        "invoices": [{
            "id": inv.id, "code": inv.code,
            "total": float(inv.total),
            "paid_amount": float(inv.paid_amount),
            "remaining": float(inv.total - inv.paid_amount),
            "status": inv.status,
            "date": str(inv.date),
            "due_date": str(inv.due_date) if inv.due_date else None,
            "location_id": inv.location_id,
        } for inv in invoices],
        "payments": [{
            "id": p.id, "amount": float(p.amount),
            "payment_type": p.payment_type,
            "payment_date": str(p.payment_date),
            "reference": p.reference,
            "notes": p.notes,
            "location_id": p.location_id,
        } for p in payments],
        "assets": [{
            "id": a.id, "name": a.name, "model": a.model,
            "serial_number": a.serial_number,
            "delivery_date": str(a.delivery_date) if a.delivery_date else None,
            "warranty_end": str(a.warranty_end) if a.warranty_end else None,
            "status": a.status,
            "equipment_id": a.equipment_id,
            "current_location_id": a.current_location_id,
        } for a in assets],
        "work_orders": [{
            "id": wo.id, "code": wo.code, "status": wo.status,
            "planned_cost": float(wo.planned_cost or 0),
            "actual_cost": float(wo.actual_cost or 0),
            "start_date": str(wo.start_date) if wo.start_date else None,
            "end_date": str(wo.end_date) if wo.end_date else None,
            "customer_order_id": wo.customer_order_id,
        } for wo in work_orders],
        "ext_projects": [{
            "id": p.id, "code": p.code, "title": p.title,
            "project_type": p.project_type, "status": p.status,
            "location_id": p.location_id,
        } for p in ext_projects],
        "field_service_orders": [{
            "id": fso.id, "code": fso.code, "title": fso.title,
            "order_type": fso.order_type, "status": fso.status,
            "location_id": fso.location_id, "asset_id": fso.asset_id,
            "total_cost": float(fso.total_cost or 0),
            "planned_date": str(fso.planned_date) if fso.planned_date else None,
            "completion_date": str(fso.completion_date) if fso.completion_date else None,
        } for fso in field_service_orders],
    }


# ── Orders ─────────────────────────────────────────────────────


# ── مواقع العميل ───────────────────────────────────────────────
class LocationIn(BaseModel):
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None
    contact_person: Optional[str] = None
    notes: Optional[str] = None


def _loc_dict(l):
    return {"id": l.id, "customer_id": l.customer_id, "name": l.name,
            "address": l.address, "phone": l.phone,
            "contact_person": l.contact_person, "notes": l.notes,
            "is_active": l.is_active}


@router.get("/{cust_id}/locations")
async def list_locations(cust_id: int, db: AsyncSession = Depends(get_db)):
    from app.models.models import CustomerLocation
    rows = (await db.execute(
        select(CustomerLocation).where(CustomerLocation.customer_id == cust_id)
        .order_by(CustomerLocation.name)
    )).scalars().all()
    return [_loc_dict(l) for l in rows]


@router.post("/{cust_id}/locations", status_code=201)
async def create_location(cust_id: int, data: LocationIn, db: AsyncSession = Depends(get_db)):
    from app.models.models import CustomerLocation
    cust = await db.get(Customer, cust_id)
    if not cust: raise HTTPException(404, "العميل غير موجود")
    if not (data.name or "").strip(): raise HTTPException(400, "اسم الموقع مطلوب")
    from app.core.database import current_tenant_id as _ctid_loc
    loc = CustomerLocation(tenant_id=_ctid_loc.get() or 1,
                           customer_id=cust_id, name=data.name.strip(),
                           address=data.address, phone=data.phone,
                           contact_person=data.contact_person, notes=data.notes)
    db.add(loc); await db.commit(); await db.refresh(loc)
    return _loc_dict(loc)


@router.put("/locations/{loc_id}")
async def update_location(loc_id: int, data: LocationIn, db: AsyncSession = Depends(get_db)):
    from app.models.models import CustomerLocation
    loc = await db.get(CustomerLocation, loc_id)
    if not loc: raise HTTPException(404, "الموقع غير موجود")
    loc.name = (data.name or loc.name).strip()
    loc.address = data.address; loc.phone = data.phone
    loc.contact_person = data.contact_person; loc.notes = data.notes
    await db.commit(); await db.refresh(loc)
    return _loc_dict(loc)


@router.delete("/locations/{loc_id}", status_code=204)
async def delete_location(loc_id: int, db: AsyncSession = Depends(get_db)):
    from app.models.models import CustomerLocation, Invoice
    from sqlalchemy import func as sqlfunc
    loc = await db.get(CustomerLocation, loc_id)
    if not loc: raise HTTPException(404, "الموقع غير موجود")
    # نمنع الحذف لو عليه فواتير
    cnt = (await db.execute(
        select(sqlfunc.count()).select_from(Invoice).where(Invoice.location_id == loc_id)
    )).scalar() or 0
    if cnt > 0:
        raise HTTPException(409, "لا يمكن حذف موقع عليه فواتير")
    await db.delete(loc); await db.commit()


@router.get("/{cust_id}/ledger")
async def customer_ledger(cust_id: int, date_from: date = None, date_to: date = None,
                          location_id: Optional[int] = None,
                          db: AsyncSession = Depends(get_db)):
    """كشف حساب تفصيلي للعميل من تاريخ لتاريخ — فواتير + مدفوعات مرتّبة زمنياً"""
    from app.models.models import Invoice
    cust = await db.get(Customer, cust_id)
    if not cust: raise HTTPException(404, "العميل غير موجود")

    # الرصيد الافتتاحي = صافي كل الحركات قبل date_from
    opening_balance = 0.0
    if date_from:
        op_inv_q = select(func.coalesce(func.sum(Invoice.total), 0)).where(
            Invoice.customer_id == cust_id, Invoice.date < date_from)
        if location_id:
            op_inv_q = op_inv_q.where(Invoice.location_id == location_id)
        op_pay_q = select(func.coalesce(func.sum(CustomerPayment.amount), 0)).where(
            CustomerPayment.customer_id == cust_id, CustomerPayment.payment_date < date_from)
        if location_id:
            op_pay_q = op_pay_q.where(CustomerPayment.location_id == location_id)
        op_inv_total = float((await db.execute(op_inv_q)).scalar() or 0)
        op_pay_total = float((await db.execute(op_pay_q)).scalar() or 0)
        opening_balance = round(op_inv_total - op_pay_total, 2)

    inv_q = select(Invoice).where(Invoice.customer_id == cust_id)
    if location_id:
        inv_q = inv_q.where(Invoice.location_id == location_id)
    if date_from: inv_q = inv_q.where(Invoice.date >= date_from)
    if date_to:   inv_q = inv_q.where(Invoice.date <= date_to)
    invoices = (await db.execute(inv_q)).scalars().all()

    pay_q = select(CustomerPayment).where(CustomerPayment.customer_id == cust_id)
    if location_id: pay_q = pay_q.where(CustomerPayment.location_id == location_id)
    if date_from: pay_q = pay_q.where(CustomerPayment.payment_date >= date_from)
    if date_to:   pay_q = pay_q.where(CustomerPayment.payment_date <= date_to)
    payments = (await db.execute(pay_q)).scalars().all()

    rows = []
    for inv in invoices:
        rows.append({"date": str(inv.date), "type": "invoice", "ref": inv.code,
                     "description": f"فاتورة {inv.code}",
                     "debit": float(inv.total), "credit": 0.0})
    for p in payments:
        rows.append({"date": str(p.payment_date), "type": "payment", "ref": f"دفعة #{p.id}",
                     "description": p.notes or "دفعة", "debit": 0.0, "credit": float(p.amount)})

    rows.sort(key=lambda r: (r["date"], 0 if r["type"] == "invoice" else 1))
    bal = opening_balance
    for r in rows:
        bal += r["debit"] - r["credit"]
        r["balance"] = round(bal, 2)

    total_debit  = round(sum(r["debit"] for r in rows), 2)
    total_credit = round(sum(r["credit"] for r in rows), 2)
    return {
        "customer": {"id": cust.id, "name": cust.name, "code": getattr(cust, "code", None)},
        "date_from": str(date_from) if date_from else None,
        "date_to": str(date_to) if date_to else None,
        "opening_balance": opening_balance,
        "rows": rows,
        "total_debit": total_debit, "total_credit": total_credit,
        "balance": round(opening_balance + total_debit - total_credit, 2),
        "current_balance": float(cust.balance),
    }


@router.get("/{cust_id}/print-statement", response_class=HTMLResponse)
async def print_customer_statement(
    cust_id: int,
    mode: str = "summary",        # summary | detailed
    db: AsyncSession = Depends(get_db),
):
    """طباعة كشف حساب العميل — إجمالي أو مفصّل"""
    from app.models.models import Invoice, InvoiceLine, CustomerPayment, CustomerLocation
    cust = await db.get(Customer, cust_id)
    if not cust: raise HTTPException(404)

    # جلب الفواتير مع بنودها (للمفصّل)
    inv_q = (
        select(Invoice)
        .options(selectinload(Invoice.lines))
        .where(Invoice.customer_id == cust_id)
        .order_by(Invoice.date)
    )
    invoices = (await db.execute(inv_q)).scalars().all()

    pay_q = select(CustomerPayment).where(CustomerPayment.customer_id == cust_id).order_by(CustomerPayment.payment_date)
    payments = (await db.execute(pay_q)).scalars().all()

    # ترتيب الحركات + الرصيد المتراكم
    rows = []
    for inv in invoices:
        rows.append({"date": str(inv.date), "type": "invoice", "ref": inv.code,
                     "desc": f"فاتورة {inv.code}", "notes": inv.notes or "",
                     "debit": float(inv.total), "credit": 0.0,
                     "lines": [{"desc": l.description, "qty": float(l.qty), "unit": l.unit,
                                 "price": float(l.unit_price), "total": float(l.total_price)} for l in inv.lines]})
    for p in payments:
        rows.append({"date": str(p.payment_date), "type": "payment", "ref": f"#{p.id}",
                     "desc": p.notes or "دفعة", "notes": "", "debit": 0.0, "credit": float(p.amount), "lines": []})
    rows.sort(key=lambda r: r["date"])
    bal = 0.0
    for r in rows:
        bal += r["debit"] - r["credit"]
        r["balance"] = round(bal, 2)

    total_inv   = sum(float(i.total) for i in invoices)
    total_paid  = sum(float(p.amount) for p in payments)
    balance_due = round(total_inv - total_paid, 2)

    def _egp(v): return f"{float(v):,.2f} ج.م"
    def _cls(v): return "red" if v > 0 else "green"

    # ── صفوف HTML ──────────────────────────────────────────────────
    rows_html = ""
    for r in rows:
        bal_color = "#dc2626" if r["balance"] > 0 else "#059669"
        if r["type"] == "invoice":
            rows_html += f"""
            <tr class="inv-row" style="font-weight:600;background:#f8fafc;cursor:pointer" onclick="toggle('{r['ref']}')">
              <td>{r['date']}</td>
              <td><span style="color:#1e3a5f">📄 {r['ref']}</span></td>
              <td>{r['desc']}</td>
              <td style="color:#dc2626;font-weight:700">{_egp(r['debit'])}</td>
              <td>—</td>
              <td style="color:{bal_color};font-weight:700">{_egp(r['balance'])}</td>
              <td style="font-size:10px;color:#94a3b8">{'▼' if mode=='detailed' and r['lines'] else ''}</td>
            </tr>"""
            if mode == "detailed" and r["lines"]:
                rows_html += f'<tr id="lines-{r["ref"]}" class="lines-row" style="display:none"><td colspan="7" style="padding:0"><table style="width:100%;font-size:11px;background:#f1f5f9"><thead><tr style="background:#e2e8f0"><th style="padding:4px 8px;text-align:right">البيان</th><th>الكمية</th><th>الوحدة</th><th>السعر</th><th>الإجمالي</th></tr></thead><tbody>'
                for l in r["lines"]:
                    rows_html += f'<tr><td style="padding:4px 8px">{l["desc"]}</td><td style="text-align:center">{l["qty"]}</td><td style="text-align:center">{l["unit"]}</td><td>{_egp(l["price"])}</td><td style="font-weight:600">{_egp(l["total"])}</td></tr>'
                rows_html += "</tbody></table></td></tr>"
        else:
            rows_html += f"""
            <tr style="background:#f0fdf4">
              <td>{r['date']}</td>
              <td><span style="color:#059669">💰 {r['ref']}</span></td>
              <td>{r['desc']}</td>
              <td>—</td>
              <td style="color:#059669;font-weight:700">{_egp(r['credit'])}</td>
              <td style="color:{bal_color};font-weight:700">{_egp(r['balance'])}</td>
              <td></td>
            </tr>"""

    # ── ملخص الأقساط للطباعة ──
    from app.services.invoice_due import get_invoice_due_status
    from datetime import date as _today_date
    _today_ps = _today_date.today()
    ps_overdue_total = 0.0
    ps_next_date     = None
    unpaid_invs_ps   = [i for i in invoices if i.status in ("unpaid", "partial", "overdue")]
    for inv_ps in unpaid_invs_ps:
        ds = await get_invoice_due_status(db, inv_ps, _today_ps)
        if ds.is_overdue:
            ps_overdue_total += float(ds.overdue_amount)
        if ds.next_due_date:
            if ps_next_date is None or ds.next_due_date < ps_next_date:
                ps_next_date = ds.next_due_date

    installment_kpis = ""
    if unpaid_invs_ps:
        installment_kpis = f"""
  <div class="kpi"><div class="val" style="color:#dc2626">{_egp(ps_overdue_total)}</div><div class="lbl">أقساط متأخرة</div></div>
  <div class="kpi"><div class="val" style="color:#1D4ED8">{str(ps_next_date) if ps_next_date else '—'}</div><div class="lbl">أقرب قسط قادم</div></div>"""

    mode_label = "مفصّل" if mode == "detailed" else "إجمالي"
    html = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<title>كشف حساب — {cust.name}</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  @page {{ size: A4; margin: 12mm }}
  * {{ box-sizing: border-box; margin: 0; padding: 0 }}
  body {{ font-family: 'IBM Plex Sans Arabic', Arial, sans-serif; font-size: 12px; color: #1e293b }}
  .header {{ background: #1e3a5f; color: #fff; padding: 16px 20px; border-radius: 8px 8px 0 0; display: flex; justify-content: space-between; align-items: center }}
  .header h1 {{ font-size: 18px; font-weight: 700 }}
  .header .meta {{ font-size: 11px; opacity: .85; line-height: 1.9 }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; margin: 12px 0 }}
  .kpi {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px; text-align: center }}
  .kpi .val {{ font-size: 15px; font-weight: 700 }}
  .kpi .lbl {{ font-size: 10px; color: #6b7280; margin-top: 3px }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 4px }}
  th {{ background: #1e3a5f; color: #fff; padding: 7px 8px; text-align: right; font-size: 11px }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #f1f5f9; font-size: 11px }}
  .lines-row td {{ padding: 0 }}
  tfoot td {{ background: #1e3a5f22; font-weight: 700; border-top: 2px solid #1e3a5f }}
  @media print {{ .no-print {{ display:none }} * {{ -webkit-print-color-adjust:exact!important; print-color-adjust:exact!important }} }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>كشف حساب — {cust.name}</h1>
    <div class="meta">{getattr(cust,'code','') or ''} &nbsp;|&nbsp; كشف {mode_label} &nbsp;|&nbsp; تاريخ الطباعة: {date.today().strftime('%Y/%m/%d')}</div>
  </div>
  <div style="text-align:left;font-size:20px">🏭</div>
</div>

<div class="kpi-grid">
  <div class="kpi"><div class="val" style="color:#dc2626">{_egp(total_inv)}</div><div class="lbl">إجمالي الفواتير</div></div>
  <div class="kpi"><div class="val" style="color:#059669">{_egp(total_paid)}</div><div class="lbl">إجمالي المدفوعات</div></div>
  <div class="kpi"><div class="val" style="color:{'#dc2626' if balance_due>0 else '#059669'}">{_egp(balance_due)}</div><div class="lbl">الرصيد المستحق</div></div>
  {installment_kpis}
</div>

<table>
  <thead>
    <tr>
      <th>التاريخ</th><th>المرجع</th><th>البيان</th>
      <th>مدين</th><th>دائن</th><th>الرصيد</th>
      <th style="width:20px"></th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
  <tfoot>
    <tr>
      <td colspan="3">الإجمالي</td>
      <td style="color:#dc2626">{_egp(total_inv)}</td>
      <td style="color:#059669">{_egp(total_paid)}</td>
      <td style="color:{'#dc2626' if balance_due>0 else '#059669'}">{_egp(balance_due)}</td>
      <td></td>
    </tr>
  </tfoot>
</table>

<script>
function toggle(ref) {{
  const el = document.getElementById('lines-' + ref);
  if (el) el.style.display = el.style.display === 'none' ? '' : 'none';
}}
window.onload = () => window.print();
</script>
</body></html>"""
    return HTMLResponse(content=html)


@router.get("/{cust_id}/orders")
async def list_customer_orders(cust_id: int, db: AsyncSession = Depends(get_db)):
    """أوامر عميل محدد"""
    c = await db.get(Customer, cust_id)
    if not c:
        raise HTTPException(404)
    res = await db.execute(
        select(CustomerOrder)
        .where(CustomerOrder.customer_id == cust_id)
        .options(selectinload(CustomerOrder.location))
        .order_by(CustomerOrder.created_at.desc())
    )
    orders = res.scalars().all()
    sm = {"pending": "معلق", "in_progress": "جاري", "delivered": "مُسلَّم", "cancelled": "ملغي"}
    return [{
        "id": o.id, "code": o.code,
        "customer_id": o.customer_id,
        "location_id": o.location_id,
        "location_name": o.location.name if o.location else None,
        "description": o.description,
        "quantity": o.quantity,
        "unit_price": float(o.unit_price),
        "total_price": float(o.total_price),
        "status": o.status, "status_ar": sm.get(o.status, o.status),
        "order_date": str(o.order_date) if o.order_date else None,
        "delivery_date": str(o.delivery_date) if o.delivery_date else None,
    } for o in orders]


@router.get("/orders/all")
async def list_all_orders(db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.customer), selectinload(CustomerOrder.lines), selectinload(CustomerOrder.location))
        .order_by(CustomerOrder.created_at.desc())
    )
    return [_order_dict(o) for o in res.scalars().all()]

@router.get("/orders/next-code")
async def get_next_order_code(db: AsyncSession = Depends(get_db)):
    return {"code": await _next_order_code(db)}


def _order_dict(o: CustomerOrder) -> dict:
    lines = []
    for l in (o.lines or []):
        lines.append({
            "id": l.id, "equipment_id": l.equipment_id,
            "description": l.description,
            "qty": float(l.qty), "unit": l.unit,
            "unit_price": float(l.unit_price),
            "total_price": float(l.total_price),
            "notes": l.notes, "sort_order": l.sort_order,
        })
    return {
        "id": o.id, "code": o.code,
        "customer_id": o.customer_id,
        "customer_name": o.customer.name if o.customer else "",
        "location_id": o.location_id,
        "location_name": o.location.name if getattr(o, "location", None) else None,
        "description": o.description,
        "quantity": o.quantity,
        "unit_price": float(o.unit_price),
        "total_price": float(o.total_price),
        "total_amount": float(o.total_price),
        "status": o.status,
        "order_date": str(o.order_date) if o.order_date else None,
        "delivery_date": str(o.delivery_date) if o.delivery_date else None,
        "notes": o.notes,
        "lines": lines,
    }


@router.post("/orders", status_code=201)
async def create_order(data: OrderIn, db: AsyncSession = Depends(get_db)):
    if data.order_date and data.delivery_date and data.delivery_date < data.order_date:
        raise HTTPException(400, "تاريخ التسليم يجب أن يكون بعد تاريخ الطلب")
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get() or 1

    code = data.code or await _next_order_code(db)

    # حساب الإجمالي: من البنود لو موجودة، وإلا من الحقول التقليدية
    if data.lines:
        total = sum(Decimal(str(l.qty)) * Decimal(str(l.unit_price)) for l in data.lines)
    else:
        total = Decimal(str(data.unit_price)) * data.quantity

    order = CustomerOrder(
        tenant_id=tid,
        code=code, customer_id=data.customer_id,
        location_id=data.location_id,
        equipment_id=data.equipment_id,
        description=data.description,
        quantity=data.quantity, unit_price=data.unit_price,
        total_price=total, status=data.status,
        order_date=data.order_date, delivery_date=data.delivery_date,
        notes=data.notes,
    )
    db.add(order)
    await db.flush()

    if data.lines:
        for i, l in enumerate(data.lines):
            line_total = Decimal(str(l.qty)) * Decimal(str(l.unit_price))
            db.add(CustomerOrderLine(
                tenant_id=tid, order_id=order.id,
                equipment_id=l.equipment_id,
                description=l.description,
                qty=l.qty, unit=l.unit,
                unit_price=l.unit_price, total_price=line_total,
                notes=l.notes, sort_order=i,
            ))

    # تحديث رصيد العميل
    cust = await db.get(Customer, data.customer_id)
    if cust:
        cust.balance += total

    await db.commit()

    # إعادة تحميل مع العلاقات
    res = await db.execute(
        select(CustomerOrder)
        .where(CustomerOrder.id == order.id)
        .options(selectinload(CustomerOrder.customer), selectinload(CustomerOrder.lines), selectinload(CustomerOrder.location))
    )
    return _order_dict(res.scalar_one())

_VALID_ORDER_STATUSES = {"pending", "in_production", "delivered", "cancelled"}

@router.put("/orders/{order_id}")
async def update_order_status(order_id: int, status: str, db: AsyncSession = Depends(get_db)):
    if status not in _VALID_ORDER_STATUSES:
        raise HTTPException(400, f"حالة غير صالحة. القيم المسموحة: {sorted(_VALID_ORDER_STATUSES)}")
    o = await db.get(CustomerOrder, order_id)
    if not o: raise HTTPException(404)
    # إلغاء الطلب → إرجاع الرصيد الذي خُصم عند إنشائه
    if status == "cancelled" and o.status != "cancelled":
        cust = await db.get(Customer, o.customer_id)
        if cust:
            cust.balance -= o.total_price

    created_asset_id = None
    if status == "delivered" and o.status != "delivered":
        equipment_id = o.equipment_id
        source_work_order_id = None
        if not equipment_id:
            # الطلبات المُحوَّلة من عرض سعر غالباً لا تحمل equipment_id مباشرة —
            # نبحث عن أمر الإنتاج المرتبط بهذا الطلب لمعرفة المعدة المُصنَّعة
            from app.models.models import WorkOrder
            wo_res = await db.execute(
                select(WorkOrder.id, WorkOrder.equipment_id).where(WorkOrder.customer_order_id == o.id)
                .order_by(WorkOrder.id.desc())
            )
            wo_row = wo_res.first()
            if wo_row:
                source_work_order_id, equipment_id = wo_row

        eq = await db.get(Equipment, equipment_id) if equipment_id else None
        if eq:
            # محاولة إيجاد مدة ضمان من مشروع تركيب مرتبط بنفس العميل والمعدة
            warranty_months = 12
            proj_res = await db.execute(
                select(ExtProject)
                .where(ExtProject.client_id == o.customer_id, ExtProject.equipment_id == equipment_id)
                .order_by(ExtProject.id.desc())
            )
            proj = proj_res.scalars().first()
            if proj and proj.warranty_months:
                warranty_months = proj.warranty_months

            delivery_date = o.delivery_date or date.today()
            # إصلاح: كان بيثبّت تاريخ الانتهاء على أول الشهر دايمًا (يفقد يوم
            # التسليم)، فلو التسليم في آخر الشهر كان الضمان بينتهي قبل موعده
            # الصحيح بيومين لغاية ٣٠ يوم. نحافظ على نفس يوم الشهر (مع تقريب
            # آمن لآخر يوم في الشهر لو الشهر الهدف أقصر).
            import calendar as _cal
            _total_months = delivery_date.month - 1 + warranty_months
            _end_year = delivery_date.year + _total_months // 12
            _end_month = _total_months % 12 + 1
            _end_day = min(delivery_date.day, _cal.monthrange(_end_year, _end_month)[1])
            warranty_end = date(_end_year, _end_month, _end_day)

            asset = CustomerAsset(
                tenant_id=o.tenant_id,
                customer_id=o.customer_id,
                equipment_id=eq.id,
                serial_number=eq.serial_number,
                name=eq.name_ar,
                model=eq.code,
                delivery_date=delivery_date,
                warranty_end=warranty_end,
                status="active",
                notes=f"تم إنشاؤه تلقائيًا عند تسليم الطلب {o.code}",
                source_work_order_id=source_work_order_id,
                current_location_id=o.location_id,
            )
            db.add(asset)
            await db.flush()
            created_asset_id = asset.id

            if source_work_order_id:
                db.add(AssetMovement(
                    tenant_id=o.tenant_id, asset_id=asset.id, event_type="manufactured",
                    ref_type="work_order", ref_id=source_work_order_id,
                    to_location_id=None, event_date=delivery_date,
                    notes=f"تصنيع عبر أمر إنتاج #{source_work_order_id}",
                ))
            db.add(AssetMovement(
                tenant_id=o.tenant_id, asset_id=asset.id, event_type="delivered",
                ref_type="customer_order", ref_id=o.id,
                from_location_id=None, to_location_id=o.location_id,
                event_date=delivery_date,
                notes=f"تسليم للعميل عبر الطلب {o.code}",
            ))

    o.status = status
    await db.commit()
    result = {"id": o.id, "status": o.status}
    if created_asset_id:
        result["asset_created"] = True
        result["asset_id"] = created_asset_id
    return result


@router.patch("/orders/{order_id}/edit")
async def edit_order_fields(order_id: int, data: OrderIn, db: AsyncSession = Depends(get_db)):
    """تعديل بيانات الطلب — مسموح فقط في حالة pending"""
    from app.core.database import current_tenant_id
    res = await db.execute(
        select(CustomerOrder).options(selectinload(CustomerOrder.lines))
        .where(CustomerOrder.id == order_id)
    )
    o = res.scalar_one_or_none()
    if not o:
        raise HTTPException(404)
    if o.status != "pending":
        raise HTTPException(400, "لا يمكن تعديل الطلب بعد بدء الإنتاج أو التسليم")

    old_total = Decimal(str(o.total_price))

    if data.lines:
        new_total = sum(Decimal(str(l.qty)) * Decimal(str(l.unit_price)) for l in data.lines)
    else:
        new_total = Decimal(str(data.unit_price)) * data.quantity

    # تحديث رصيد العميل بالفرق
    cust = await db.get(Customer, o.customer_id)
    if cust:
        cust.balance = cust.balance - old_total + new_total

    o.customer_id = data.customer_id
    o.location_id = data.location_id
    o.equipment_id = data.equipment_id
    o.description = data.description
    o.quantity = data.quantity
    o.unit_price = data.unit_price
    o.total_price = new_total
    o.order_date = data.order_date
    o.delivery_date = data.delivery_date
    o.notes = data.notes

    if data.lines is not None:
        for old_line in o.lines:
            await db.delete(old_line)
        await db.flush()

        tid = current_tenant_id.get() or 1
        for i, l in enumerate(data.lines):
            line_total = Decimal(str(l.qty)) * Decimal(str(l.unit_price))
            db.add(CustomerOrderLine(
                tenant_id=tid, order_id=o.id,
                equipment_id=l.equipment_id,
                description=l.description,
                qty=l.qty, unit=l.unit,
                unit_price=l.unit_price, total_price=line_total,
                notes=l.notes, sort_order=i,
            ))

    await db.commit()
    res2 = await db.execute(
        select(CustomerOrder).where(CustomerOrder.id == o.id)
        .options(selectinload(CustomerOrder.customer), selectinload(CustomerOrder.lines), selectinload(CustomerOrder.location))
    )
    return _order_dict(res2.scalar_one())


# ── Payments ───────────────────────────────────────────────────
@router.post("/payments", status_code=201)
async def record_payment(data: PaymentIn, db: AsyncSession = Depends(get_db),
                         current_user: dict = Depends(get_current_user)):
    from decimal import Decimal as _Dec
    from app.models.models import Invoice, CustomerPaymentAllocation
    from sqlalchemy import select as _sel

    from app.core.database import current_tenant_id as _ctid_cpay
    payment_data = data.model_dump()
    payment_data['created_by'] = int(current_user.get('sub', 0)) or None
    payment_data['tenant_id'] = _ctid_cpay.get() or 1
    payment = CustomerPayment(**payment_data)
    db.add(payment)
    await db.flush()

    # تحديث رصيد العميل
    cust = await db.get(Customer, data.customer_id)
    if cust:
        cust.balance -= data.amount   # دفعة تُقلّص ما يستحق

    # توزيع تلقائي على أقدم فواتير غير مدفوعة (FIFO allocation)
    remaining_pay = _Dec(str(data.amount))
    if remaining_pay > 0:
        unpaid_invs = (await db.execute(
            _sel(Invoice).where(
                Invoice.customer_id == data.customer_id,
                Invoice.status.in_(["unpaid", "partial"]),
                Invoice.total > Invoice.paid_amount,
            ).order_by(Invoice.date)
        )).scalars().all()

        from app.core.database import current_tenant_id as _ctid_p
        _tid_p = _ctid_p.get() or 1

        for inv in unpaid_invs:
            if remaining_pay <= 0:
                break
            inv_remaining = inv.total - inv.paid_amount
            alloc_amt = min(remaining_pay, inv_remaining)
            inv.paid_amount += alloc_amt
            inv.status = "paid" if inv.paid_amount >= inv.total else "partial"
            db.add(CustomerPaymentAllocation(
                tenant_id=_tid_p, payment_id=payment.id,
                invoice_id=inv.id, amount=alloc_amt,
            ))
            # محطة 5 (إصلاح ثغرة تكامل): نفس المسار اللي بيوزّع دفعة على الأقساط
            # في شاشة الفاتورة، لازم يتطبّق هنا كمان — وإلا الفاتورة تتقفل
            # مدفوعة والأقساط تفضل "pending" زي ما هي.
            from app.api.sales import _allocate_payment_to_installments
            await _allocate_payment_to_installments(db, inv.id, alloc_amt)
            remaining_pay -= alloc_amt

        # قيد محاسبي تلقائي
        from app.services.auto_journal import journal_sales_invoice_payment
        await journal_sales_invoice_payment(
            db, _tid_p, payment.id, f"PAY-{payment.id}",
            _Dec(str(data.amount)), data.payment_date,
        )

    # ربط تلقائي بالخزنة — دخول فلوس
    try:
        from app.services.treasury_link import record_payment_in_treasury
        await record_payment_in_treasury(
            db, amount=data.amount, direction="in", source="customer_payment",
            linked_type="customer_payment", linked_id=payment.id,
            category_name="تحصيل من عملاء",
            description=f"دفعة من عميل #{data.customer_id}",
            reference=getattr(data, "reference", None),
            txn_date=data.payment_date,
        )
    except Exception:
        pass  # الخزنة اختيارية — مايكسرش الدفع

    await db.commit()
    await db.refresh(payment)
    return {"id": payment.id, "amount": float(payment.amount),
            "customer_balance": float(cust.balance) if cust else 0}

@router.get("/payments/all")
async def list_payments(db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(CustomerPayment)
        .options(selectinload(CustomerPayment.customer))
        .order_by(CustomerPayment.payment_date.desc()).limit(50)
    )
    return [{
        "id": p.id, "customer_name": p.customer.name if p.customer else "",
        "amount": float(p.amount), "payment_type": p.payment_type,
        "payment_date": str(p.payment_date), "reference": p.reference,
    } for p in res.scalars().all()]


# ── Print Order ────────────────────────────────────────────────────────────────
from fastapi import APIRouter as _AR

orders_print_router = _AR(prefix="/customers/orders", tags=["customers-print"], dependencies=[Depends(require_any)])

@orders_print_router.get("/{order_id}/print", response_class=HTMLResponse)
async def print_order(order_id: int, db: AsyncSession = Depends(get_db)):
    from app.models.models import WorkshopProfile
    from app.core.database import current_tenant_id
    tid = current_tenant_id.get()

    res = await db.execute(
        select(CustomerOrder)
        .where(CustomerOrder.id == order_id)
        .options(selectinload(CustomerOrder.customer), selectinload(CustomerOrder.lines), selectinload(CustomerOrder.location))
    )
    o = res.scalar_one_or_none()
    if not o:
        raise HTTPException(404, "الطلب غير موجود")

    prof_res = await db.execute(select(WorkshopProfile).where(WorkshopProfile.tenant_id == tid))
    prof = prof_res.scalar_one_or_none()
    ws_name    = prof.name    if prof else "الورشة"
    ws_address = prof.address if prof else ""
    ws_phone   = prof.phone   if prof else ""
    logo_html  = f'<img src="{prof.logo_data}" style="max-height:70px;max-width:200px">' if (prof and prof.logo_data) else ""

    STATUS_MAP = {"pending": "معلق", "in_progress": "جاري التنفيذ", "delivered": "مُسلَّم", "cancelled": "ملغي"}
    status_ar = STATUS_MAP.get(o.status, o.status)

    # بنود الطلب
    if o.lines:
        rows_html = ""
        for i, l in enumerate(o.lines, 1):
            rows_html += f"""
            <tr>
                <td style="text-align:center">{i}</td>
                <td>{l.description}</td>
                <td style="text-align:center">{float(l.qty):g} {l.unit}</td>
                <td style="text-align:center">{float(l.unit_price):,.2f}</td>
                <td style="text-align:center">{float(l.total_price):,.2f}</td>
            </tr>"""
        total_val = float(o.total_price)
    else:
        total_val = float(o.unit_price) * o.quantity
        rows_html = f"""
        <tr>
            <td style="text-align:center">1</td>
            <td>{o.description or ""}</td>
            <td style="text-align:center">{o.quantity} قطعة</td>
            <td style="text-align:center">{float(o.unit_price):,.2f}</td>
            <td style="text-align:center">{total_val:,.2f}</td>
        </tr>"""

    delivery_row = f"<tr><td>تاريخ التسليم المتوقع</td><td>{o.delivery_date}</td></tr>" if o.delivery_date else ""

    html = f"""<!DOCTYPE html><html dir="rtl" lang="ar">
<head><meta charset="UTF-8"><title>طلب {o.code}</title>
<style>
  body{{font-family:Arial,sans-serif;padding:20px;color:#222;direction:rtl}}
  .header{{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #1a56a0;margin-bottom:16px;padding-bottom:12px}}
  .ws-info h2{{margin:0;color:#1a56a0;font-size:18px}}
  .ws-info p{{margin:2px 0;font-size:12px;color:#555}}
  .doc-title{{text-align:center;background:#1a56a0;color:#fff;padding:8px 24px;border-radius:4px;font-size:15px}}
  .meta-table td{{padding:4px 8px;font-size:13px;border-bottom:1px solid #eee}}
  table.items{{width:100%;border-collapse:collapse;margin-top:16px;font-size:13px}}
  table.items th{{background:#1a56a0;color:#fff;padding:7px;text-align:center}}
  table.items td{{padding:6px 8px;border:1px solid #ddd}}
  table.items tr:nth-child(even){{background:#f7f9ff}}
  .total-row{{font-weight:bold;background:#e8f0fe!important}}
  .notes{{margin-top:16px;font-size:12px;color:#555;border-top:1px solid #eee;padding-top:8px}}
</style></head>
<body>
<div class="header">
  <div class="ws-info">
    <h2>{ws_name}</h2>
    <p>{ws_address}</p>
    <p>{ws_phone}</p>
  </div>
  {logo_html}
  <div class="doc-title">طـلـب عـمـيـل<br><span style="font-size:20px;font-weight:bold">{o.code}</span></div>
</div>
<table class="meta-table" style="width:100%">
  <tr><td><b>العميل</b></td><td>{o.customer.name if o.customer else ""}</td>
      <td><b>تاريخ الطلب</b></td><td>{o.order_date}</td></tr>
  {delivery_row}
  <tr><td><b>الحالة</b></td><td>{status_ar}</td>
      <td><b>ملاحظات</b></td><td>{o.notes or ""}</td></tr>
</table>
<table class="items">
  <thead><tr><th>#</th><th>الوصف</th><th>الكمية</th><th>سعر الوحدة</th><th>الإجمالي</th></tr></thead>
  <tbody>
    {rows_html}
    <tr class="total-row"><td colspan="4" style="text-align:left;padding:8px">الإجمالي</td>
        <td style="text-align:center">{total_val:,.2f} ج.م</td></tr>
  </tbody>
</table>
<div class="notes">النظام: {ws_name} — تم طباعة هذه الوثيقة من نظام Warsh ERP</div>
<script>window.onload=()=>window.print()</script>
</body></html>"""
    return HTMLResponse(html)

