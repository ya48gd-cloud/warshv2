"""
test_08_customers.py — اختبارات العملاء
عملاء · مواقع · طلبات · مدفوعات · كشف حساب
"""
import pytest
from datetime import date

BASE = "/api/v1/customers"
SALES_BASE = "/api/v1/sales"


def _cust(name="عميل اختبار"):
    return {"name": name, "phone": "0500000099", "credit_limit": 5000.0}


# ════════════════ CUSTOMERS ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_customer(cli):
    r = await cli.post(BASE, json=_cust())
    assert r.status_code == 201
    assert r.json()["name"] == "عميل اختبار"


@pytest.mark.asyncio
async def test_list_customers(cli):
    await cli.post(BASE, json=_cust("عميل للقائمة"))
    r = await cli.get(BASE)
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_get_customer_not_found(cli):
    r = await cli.get(f"{BASE}/99999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_customer(cli):
    r = await cli.post(BASE, json=_cust())
    cid = r.json()["id"]
    r2 = await cli.put(f"{BASE}/{cid}", json=_cust("عميل محدّث"))
    assert r2.status_code == 200
    assert r2.json()["name"] == "عميل محدّث"


@pytest.mark.asyncio
async def test_delete_customer(cli):
    r = await cli.post(BASE, json=_cust("للحذف"))
    cid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/{cid}")
    assert r2.status_code in (200, 204)


@pytest.mark.asyncio
async def test_customer_next_code(cli):
    r = await cli.get(f"{BASE}/next-code")
    assert r.status_code == 200
    assert "code" in r.json()


# ════════════════ STATEMENT / LEDGER ════════════════════════════════
@pytest.mark.asyncio
async def test_customer_statement(cli):
    r = await cli.post(BASE, json=_cust())
    cid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/{cid}/statement")
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_customer_ledger(cli):
    r = await cli.post(BASE, json=_cust())
    cid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/{cid}/ledger")
    assert r2.status_code == 200


# ════════════════ LOCATIONS ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_location(cli):
    r = await cli.post(BASE, json=_cust())
    cid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/{cid}/locations", json={
        "name": "موقع المنصورة", "city": "المنصورة",
        "address": "شارع الجيش", "governorate": "الدقهلية",
    })
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_list_locations(cli):
    r = await cli.post(BASE, json=_cust())
    cid = r.json()["id"]
    await cli.post(f"{BASE}/{cid}/locations", json={"name": "موقع 1", "city": "القاهرة"})
    r2 = await cli.get(f"{BASE}/{cid}/locations")
    assert r2.status_code == 200
    assert len(r2.json()) >= 1


@pytest.mark.asyncio
async def test_update_location(cli):
    r = await cli.post(BASE, json=_cust())
    cid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/{cid}/locations", json={"name": "موقع قديم", "city": "القاهرة"})
    lid = r2.json()["id"]
    r3 = await cli.put(f"{BASE}/locations/{lid}", json={"name": "موقع جديد", "city": "الإسكندرية"})
    assert r3.status_code == 200


@pytest.mark.asyncio
async def test_delete_location(cli):
    r = await cli.post(BASE, json=_cust())
    cid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/{cid}/locations", json={"name": "موقع للحذف", "city": "طنطا"})
    lid = r2.json()["id"]
    r3 = await cli.delete(f"{BASE}/locations/{lid}")
    assert r3.status_code == 204


# ════════════════ ORDERS ═════════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_order(cli):
    r = await cli.post(BASE, json=_cust())
    cid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/orders", json={
        "customer_id": cid,
        "order_date": str(date.today()),
        "status": "pending",
        "lines": [{"description": "منتج 1", "qty": 2, "unit": "قطعة", "unit_price": 300.0}],
    })
    assert r2.status_code == 201
    assert r2.json()["customer_id"] == cid


@pytest.mark.asyncio
async def test_list_customer_orders(cli):
    r = await cli.post(BASE, json=_cust())
    cid = r.json()["id"]
    await cli.post(f"{BASE}/orders", json={
        "customer_id": cid, "status": "pending",
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 100.0}],
    })
    r2 = await cli.get(f"{BASE}/{cid}/orders")
    assert r2.status_code == 200
    assert len(r2.json()) >= 1


@pytest.mark.asyncio
async def test_list_all_orders(cli):
    r = await cli.get(f"{BASE}/orders/all")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_order_next_code(cli):
    r = await cli.get(f"{BASE}/orders/next-code")
    assert r.status_code == 200
    assert "code" in r.json()


@pytest.mark.asyncio
async def test_update_order_status(cli):
    r = await cli.post(BASE, json=_cust())
    cid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/orders", json={
        "customer_id": cid, "status": "pending",
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 100.0}],
    })
    oid = r2.json()["id"]
    r3 = await cli.put(f"{BASE}/orders/{oid}", params={"status": "in_production"})
    assert r3.status_code == 200


# ════════════════ PAYMENTS ═══════════════════════════════════════════
@pytest.mark.asyncio
async def test_record_customer_payment(cli):
    r = await cli.post(BASE, json=_cust())
    cid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/payments", json={
        "customer_id": cid,
        "amount": 250.0,
        "payment_type": "cash",
        "payment_date": str(date.today()),
    })
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_list_all_payments(cli):
    r = await cli.get(f"{BASE}/payments/all")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ══ إصلاح ثغرة تكامل (بعد تحليل المحطات 1-7): دفعة عامة من شاشة العملاء
# لازم توزّع على الأقساط برضه، مش بس على إجمالي الفاتورة ══════════════
@pytest.mark.asyncio
async def test_general_customer_payment_also_updates_installments(cli):
    """
    دفعة مسجّلة من POST /customers/payments (مش من شاشة الفاتورة) على فاتورة
    عندها خطة أقساط — لازم الأقساط تتحدّث تلقائيًا برضه، مش بس Invoice.paid_amount.
    """
    r = await cli.post(BASE, json=_cust())
    cid = r.json()["id"]
    inv = await cli.post(f"{SALES_BASE}/invoices", json={
        "customer_id": cid, "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 500.0}],
    })
    iid = inv.json()["id"]
    await cli.post(f"{SALES_BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "قسط 1", "amount": 200, "due_date": str(date.today())},
            {"label": "قسط 2", "amount": 300, "due_date": str(date.today())},
        ]
    })

    r2 = await cli.post(f"{BASE}/payments", json={
        "customer_id": cid, "amount": 200.0,
        "payment_type": "cash", "payment_date": str(date.today()),
    })
    assert r2.status_code == 201

    rows = (await cli.get(f"{SALES_BASE}/invoices/{iid}/payment-schedule")).json()
    assert rows[0]["status"] == "paid"     # كان المفروض يفضل pending قبل الإصلاح
    assert rows[1]["status"] == "pending"
