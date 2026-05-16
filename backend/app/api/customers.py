from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_db
from app.models.models import Customer, CustomerOrder, CustomerPayment

router = APIRouter(prefix="/customers", tags=["customers"])


# ── Schemas ────────────────────────────────────────────────────
class CustomerIn(BaseModel):
    code: str
    name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    tax_id: Optional[str] = None
    credit_limit: Decimal = Decimal("0")
    notes: Optional[str] = None

class OrderIn(BaseModel):
    code: str
    customer_id: int
    equipment_id: Optional[int] = None
    description: Optional[str] = None
    quantity: int = 1
    unit_price: Decimal = Decimal("0")
    status: str = "pending"
    order_date: Optional[date] = None
    delivery_date: Optional[date] = None
    notes: Optional[str] = None

class PaymentIn(BaseModel):
    customer_id: int
    order_id: Optional[int] = None
    amount: Decimal
    payment_type: str = "cash"
    payment_date: date
    reference: Optional[str] = None
    notes: Optional[str] = None


def cust_dict(c: Customer):
    return {"id": c.id, "code": c.code, "name": c.name,
            "phone": c.phone, "address": c.address,
            "credit_limit": float(c.credit_limit),
            "balance": float(c.balance),
            "notes": c.notes,
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

        total_charged = float(inv_total) + float(ord_total)
        total_paid    = float(inv_paid) + float(pay_total)
        balance_due   = total_charged - total_paid  # موجب = عليه

        d = cust_dict(cust)
        d['balance'] = -balance_due   # سالب = عليه (convention في الكود)
        d['total_charged'] = total_charged
        d['total_paid'] = total_paid
        result.append(d)

    return result

@router.post("", status_code=201)
async def create_customer(data: CustomerIn, db: AsyncSession = Depends(get_db)):
    c = Customer(**data.model_dump())
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return cust_dict(c)

@router.put("/{cust_id}")
async def update_customer(cust_id: int, data: CustomerIn, db: AsyncSession = Depends(get_db)):
    c = await db.get(Customer, cust_id)
    if not c: raise HTTPException(404)
    for k, v in data.model_dump().items():
        setattr(c, k, v)
    await db.commit()
    await db.refresh(c)
    return cust_dict(c)

@router.delete("/{cust_id}")
async def delete_customer(cust_id: int, db: AsyncSession = Depends(get_db)):
    c = await db.get(Customer, cust_id)
    if not c: raise HTTPException(404)
    await db.delete(c)
    await db.commit()
    return {"message": "تم الحذف"}

@router.get("/{cust_id}/statement")
async def customer_statement(cust_id: int, db: AsyncSession = Depends(get_db)):
    """كشف حساب العميل الكامل"""
    from app.models.models import Invoice
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

    # الإجمالي = طلبات العملاء + الفواتير
    total_orders   = sum(o.total_price for o in orders)
    total_invoices = sum(inv.total for inv in invoices)
    total_charged  = total_orders + total_invoices

    # المدفوع = مدفوعات العملاء + مدفوعات الفواتير
    total_cust_paid = sum(p.amount for p in payments)
    total_inv_paid  = sum(inv.paid_amount for inv in invoices)
    total_paid      = total_cust_paid + total_inv_paid

    balance_due = total_charged - total_paid

    # Sync stored balance to calculated value
    if c.balance != -balance_due:
        c.balance = -balance_due
        await db.commit()

    return {
        "customer": cust_dict(c),
        "total_orders":   float(total_orders),
        "total_invoices": float(total_invoices),
        "total_charged":  float(total_charged),
        "total_paid":     float(total_paid),
        "balance_due":    float(balance_due),
        "orders": [{
            "id": o.id, "code": o.code, "description": o.description,
            "quantity": o.quantity, "unit_price": float(o.unit_price),
            "total_price": float(o.total_price), "status": o.status,
            "order_date": str(o.order_date) if o.order_date else None,
            "delivery_date": str(o.delivery_date) if o.delivery_date else None,
        } for o in orders],
        "invoices": [{
            "id": inv.id, "code": inv.code,
            "total": float(inv.total),
            "paid_amount": float(inv.paid_amount),
            "remaining": float(inv.total - inv.paid_amount),
            "status": inv.status,
            "date": str(inv.date),
        } for inv in invoices],
        "payments": [{
            "id": p.id, "amount": float(p.amount),
            "payment_type": p.payment_type,
            "payment_date": str(p.payment_date),
            "reference": p.reference,
        } for p in payments],
    }


# ── Orders ─────────────────────────────────────────────────────
@router.get("/orders/all")
async def list_all_orders(db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.customer))
        .order_by(CustomerOrder.created_at.desc())
    )
    orders = res.scalars().all()
    sm = {"pending": "معلق", "in_progress": "جاري", "delivered": "مُسلَّم", "cancelled": "ملغي"}
    return [{
        "id": o.id, "code": o.code,
        "customer_name": o.customer.name if o.customer else "",
        "customer_id": o.customer_id,
        "description": o.description,
        "total_price": float(o.total_price),
        "status": o.status, "status_ar": sm.get(o.status, o.status),
        "order_date": str(o.order_date) if o.order_date else None,
    } for o in orders]

@router.post("/orders", status_code=201)
async def create_order(data: OrderIn, db: AsyncSession = Depends(get_db)):
    total = data.unit_price * data.quantity
    order = CustomerOrder(**data.model_dump(), total_price=total)
    db.add(order)
    # تحديث رصيد العميل (إضافة المديونية)
    cust = await db.get(Customer, data.customer_id)
    if cust:
        cust.balance -= total   # سالب = عليه
    await db.commit()
    await db.refresh(order)
    return {"id": order.id, "code": order.code, "total_price": float(order.total_price)}

@router.put("/orders/{order_id}")
async def update_order_status(order_id: int, status: str, db: AsyncSession = Depends(get_db)):
    o = await db.get(CustomerOrder, order_id)
    if not o: raise HTTPException(404)
    o.status = status
    await db.commit()
    return {"id": o.id, "status": o.status}


# ── Payments ───────────────────────────────────────────────────
@router.post("/payments", status_code=201)
async def record_payment(data: PaymentIn, db: AsyncSession = Depends(get_db)):
    payment = CustomerPayment(**data.model_dump())
    db.add(payment)
    # تحديث رصيد العميل
    cust = await db.get(Customer, data.customer_id)
    if cust:
        cust.balance += data.amount   # موجب = دفع
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
