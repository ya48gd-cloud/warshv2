"""
test_06_accounting.py — اختبارات أوامر العمل المحاسبية
WO CRUD · خطوط التكلفة · القيد اليومي · التباين · لوحة التحكم
"""
import pytest
from datetime import date

BASE = "/api/v1/accounting"
EQ = "/api/v1/equipment"
INV = "/api/v1/inventory"


async def _make_equipment(cli):
    r = await cli.post(EQ, json={"name_ar": "معدة محاسبة", "level": 1})
    return r.json()["id"]


async def _make_wo(cli, eq_id, planned_cost=500.0):
    r = await cli.post(f"{BASE}/work-orders", json={
        "equipment_id": eq_id,
        "planned_cost": planned_cost,
        "start_date": str(date.today()),
        "priority": "normal",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ════════════════ WORK ORDERS ════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_work_order(cli):
    eq_id = await _make_equipment(cli)
    r = await cli.post(f"{BASE}/work-orders", json={
        "equipment_id": eq_id,
        "planned_cost": 1000.0,
        "start_date": str(date.today()),
    })
    assert r.status_code == 201
    d = r.json()
    assert d["equipment_id"] == eq_id
    assert d["planned_cost"] == 1000.0


@pytest.mark.asyncio
async def test_list_work_orders(cli):
    eq_id = await _make_equipment(cli)
    await _make_wo(cli, eq_id)
    r = await cli.get(f"{BASE}/work-orders")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_get_work_order(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_wo(cli, eq_id)
    r = await cli.get(f"{BASE}/work-orders/{wo_id}")
    assert r.status_code == 200
    assert r.json()["id"] == wo_id


@pytest.mark.asyncio
async def test_update_work_order(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_wo(cli, eq_id)
    r = await cli.put(f"{BASE}/work-orders/{wo_id}", json={
        "equipment_id": eq_id,
        "status": "draft",
        "planned_cost": 2000.0,
    })
    assert r.status_code == 200
    assert r.json()["planned_cost"] == 2000.0


@pytest.mark.asyncio
async def test_change_wo_status(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_wo(cli, eq_id)
    r = await cli.patch(f"{BASE}/work-orders/{wo_id}/status", params={"status": "in_progress"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_delete_work_order(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_wo(cli, eq_id)
    r = await cli.delete(f"{BASE}/work-orders/{wo_id}")
    assert r.status_code in (200, 204)


@pytest.mark.asyncio
async def test_wo_next_code(cli):
    r = await cli.get(f"{BASE}/work-orders/next-code")
    assert r.status_code == 200
    assert "code" in r.json()


# ════════════════ COST LINES ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_add_cost_line(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_wo(cli, eq_id)
    r = await cli.post(f"{BASE}/cost-lines", json={
        "work_order_id": wo_id,
        "cost_type": "labor",
        "description": "عمالة يومية",
        "qty": 2.0,
        "unit_cost": 150.0,
    })
    assert r.status_code == 201
    d = r.json()
    assert d["work_order_id"] == wo_id


@pytest.mark.asyncio
async def test_get_cost_lines(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_wo(cli, eq_id)
    await cli.post(f"{BASE}/cost-lines", json={
        "work_order_id": wo_id, "cost_type": "material",
        "description": "مواد", "qty": 1.0, "unit_cost": 200.0,
    })
    r = await cli.get(f"{BASE}/work-orders/{wo_id}/cost-lines")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_delete_cost_line(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_wo(cli, eq_id)
    r = await cli.post(f"{BASE}/cost-lines", json={
        "work_order_id": wo_id, "cost_type": "other",
        "description": "متنوع", "qty": 1.0, "unit_cost": 50.0,
    })
    line_id = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/cost-lines/{line_id}")
    assert r2.status_code in (200, 204)


# ════════════════ JOURNAL ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_get_journal(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_wo(cli, eq_id)
    r = await cli.get(f"{BASE}/work-orders/{wo_id}/journal")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ════════════════ VARIANCE ═══════════════════════════════════════════
@pytest.mark.asyncio
async def test_cost_variance(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_wo(cli, eq_id, planned_cost=1000.0)
    # Add a cost line
    await cli.post(f"{BASE}/cost-lines", json={
        "work_order_id": wo_id, "cost_type": "labor",
        "description": "عمالة", "qty": 1.0, "unit_cost": 800.0,
    })
    r = await cli.get(f"{BASE}/work-orders/{wo_id}/variance")
    assert r.status_code == 200
    d = r.json()
    assert "planned_cost" in d
    assert "actual_cost" in d
    assert "variance" in d


# ════════════════ DASHBOARD ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_accounting_dashboard(cli):
    r = await cli.get(f"{BASE}/dashboard")
    assert r.status_code == 200


# ══ إصلاح ثغرة تكامل (بعد تحليل المحطات 1-7): التوزيع اليدوي لدفعة على
# فاتورة من شاشة الحسابات لازم يحدّث الأقساط برضه ═════════════════════
@pytest.mark.asyncio
async def test_manual_allocation_also_updates_installments(cli):
    CUST_BASE = "/api/v1/customers"
    SALES_BASE = "/api/v1/sales"
    ACC_BASE = "/api/v1/accounts"

    cust = await cli.post(CUST_BASE, json={"name": "عميل حسابات", "phone": "0501112222"})
    cid = cust.json()["id"]

    # دفعة غير مخصصة الأول (قبل ما يكون فيه أي فاتورة أصلاً، فمفيش حاجة تتوزع عليها تلقائيًا)
    pay = await cli.post(f"{CUST_BASE}/payments", json={
        "customer_id": cid, "amount": 150.0,
        "payment_type": "cash", "payment_date": str(date.today()),
    })
    pid = pay.json()["id"]

    inv = await cli.post(f"{SALES_BASE}/invoices", json={
        "customer_id": cid, "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 500.0}],
    })
    iid = inv.json()["id"]
    await cli.post(f"{SALES_BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "قسط 1", "amount": 150, "due_date": str(date.today())},
            {"label": "قسط 2", "amount": 350, "due_date": str(date.today())},
        ]
    })

    r = await cli.post(f"{ACC_BASE}/payments/{pid}/allocate", json=[
        {"invoice_id": iid, "amount": 150.0}
    ])
    assert r.status_code == 200

    rows = (await cli.get(f"{SALES_BASE}/invoices/{iid}/payment-schedule")).json()
    assert rows[0]["status"] == "paid"
    assert rows[1]["status"] == "pending"
