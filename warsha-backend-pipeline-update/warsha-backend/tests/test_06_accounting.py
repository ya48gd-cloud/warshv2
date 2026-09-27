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


# ══ جولة 2 — إصلاح ثغرة تكامل: bند تكلفة بمبلغ صفري/سالب كان يعدي من غير
# أي تحقق ويقلل wo.actual_cost بصمت من غير أي قيد محاسبي مقابل ═══════════
@pytest.mark.asyncio
async def test_cost_line_rejects_non_positive_amounts(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_wo(cli, eq_id)

    r = await cli.post(f"{BASE}/cost-lines", json={
        "work_order_id": wo_id, "cost_type": "labor",
        "description": "عمالة", "qty": 1.0, "unit_cost": 0,
    })
    assert r.status_code == 422

    r2 = await cli.post(f"{BASE}/cost-lines", json={
        "work_order_id": wo_id, "cost_type": "labor",
        "description": "عمالة", "qty": -2.0, "unit_cost": 50.0,
    })
    assert r2.status_code == 422


# ══ جولة 2 — إصلاح ثغرة تكامل: حذف أمر إنتاج بالكامل كان يحذف بنود التكلفة
# من غير عكس القيود المحاسبية المرتبطة بيها، فيفضل قيد "يتيم" في دليل
# الحسابات يشير لأمر إنتاج غير موجود ═══════════════════════════════════
@pytest.mark.asyncio
async def test_delete_work_order_reverses_cost_line_journal_entries(cli):
    ACC_BASE = "/api/v1/accounts"
    await cli.post(f"{ACC_BASE}/chart/seed")

    eq_id = await _make_equipment(cli)
    wo_id = await _make_wo(cli, eq_id)

    r = await cli.post(f"{BASE}/cost-lines", json={
        "work_order_id": wo_id, "cost_type": "labor",
        "description": "عمالة", "qty": 2.0, "unit_cost": 150.0,
    })
    assert r.status_code == 201

    tb = (await cli.get(f"{ACC_BASE}/trial-balance")).json()
    wip_row = next(a for a in tb["accounts"] if a["code"] == "1320")
    assert wip_row["balance"] == 300.0

    dele = await cli.delete(f"{BASE}/work-orders/{wo_id}")
    assert dele.status_code in (200, 204)

    tb2 = (await cli.get(f"{ACC_BASE}/trial-balance")).json()
    wip_rows2 = [a for a in tb2["accounts"] if a["code"] == "1320"]
    assert wip_rows2 and wip_rows2[0]["balance"] == 0.0, (
        "بعد حذف أمر الإنتاج بالكامل، صافي حساب WIP لازم يرجع صفر (القيد المرتبط بالبند المحذوف لازم ينعكس)"
    )


# ══ جولة 2 — إصلاح ثغرة تكامل: القيد اليدوي (POST /accounts/entries) كان
# يظهر في ميزان المراجعة فوراً حتى قبل ترحيله (posted=True) — فيفقد حقل
# posted أي معنى ويقدر مستخدم يضيف قيد "مسودة" يشوّه كل التقارير المالية ═══
@pytest.mark.asyncio
async def test_manual_entry_hidden_from_reports_until_posted(cli):
    ACC_BASE = "/api/v1/accounts"
    await cli.post(f"{ACC_BASE}/chart/seed")
    accounts = (await cli.get(f"{ACC_BASE}/chart")).json()
    cash = next(a for a in accounts if a["code"] == "1110")
    equity = next(a for a in accounts if a["code"] == "3100")

    r = await cli.post(f"{ACC_BASE}/entries", json={
        "entry_date": str(date.today()),
        "description": "قيد افتتاحي يدوي",
        "lines": [
            {"account_id": cash["id"], "side": "debit", "amount": 500.0},
            {"account_id": equity["id"], "side": "credit", "amount": 500.0},
        ],
    })
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]
    assert r.json()["posted"] is False

    tb = (await cli.get(f"{ACC_BASE}/trial-balance")).json()
    assert not any(a["code"] == "1110" for a in tb["accounts"]), (
        "قيد يدوي غير مرحّل (posted=False) لازم يكون غير ظاهر في ميزان المراجعة"
    )

    post_r = await cli.patch(f"{ACC_BASE}/entries/{entry_id}/post")
    assert post_r.status_code == 200

    tb2 = (await cli.get(f"{ACC_BASE}/trial-balance")).json()
    cash_row = next(a for a in tb2["accounts"] if a["code"] == "1110")
    assert cash_row["balance"] == 500.0


# ══ جولة 2 — إصلاح ثغرة تكامل: AllocationIn.amount كان بلا أي تحقق —
# amount=0 أو سالب كان يعدي ويسجل تخصيصاً وهمياً/يقلل paid_amount الفاتورة ═══
@pytest.mark.asyncio
async def test_allocate_payment_rejects_non_positive_amount(cli):
    CUST_BASE = "/api/v1/customers"
    SALES_BASE = "/api/v1/sales"
    ACC_BASE = "/api/v1/accounts"

    cust = await cli.post(CUST_BASE, json={"name": "عميل تخصيص", "phone": "0501112233"})
    cid = cust.json()["id"]
    pay = await cli.post(f"{CUST_BASE}/payments", json={
        "customer_id": cid, "amount": 100.0,
        "payment_type": "cash", "payment_date": str(date.today()),
    })
    pid = pay.json()["id"]
    inv = await cli.post(f"{SALES_BASE}/invoices", json={
        "customer_id": cid, "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 500.0}],
    })
    iid = inv.json()["id"]

    r = await cli.post(f"{ACC_BASE}/payments/{pid}/allocate", json=[
        {"invoice_id": iid, "amount": 0}
    ])
    assert r.status_code == 422

    r2 = await cli.post(f"{ACC_BASE}/payments/{pid}/allocate", json=[
        {"invoice_id": iid, "amount": -50.0}
    ])
    assert r2.status_code == 422
