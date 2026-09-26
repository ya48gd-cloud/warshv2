"""
test_14_flows.py — اختبارات End-to-End
1. دورة المبيعات الكاملة: عرض → فاتورة → دفع → خزينة
2. دورة الشراء: PO → استلام → مخزون + فاتورة
3. دورة الإنتاج: أمر عمل → حجز مواد → صرف → إتمام
4. مسير الرواتب: حضور → مسير → توليد البنود
5. تحويل خزينة: من حساب لحساب مع تحقق الأرصدة
"""
import pytest
from datetime import date, timedelta

SALES = "/api/v1/sales"
PURCH = "/api/v1/purchases"
PO = "/api/v1/purchase-orders"
PROD = "/api/v1/production"
ACC = "/api/v1/accounting"
TREAS = "/api/v1/treasury"
CUST = "/api/v1/customers"
WORKERS = "/api/v1/workers"
ATT = "/api/v1/attendance"
INV = "/api/v1/inventory"
EQ = "/api/v1/equipment"


# ════════════════ FLOW 1: دورة المبيعات الكاملة ══════════════════════
@pytest.mark.asyncio
async def test_full_sales_cycle(cli):
    # 1. عميل
    rc = await cli.post(CUST, json={"name": "عميل تدفق"})
    cid = rc.json()["id"]

    # 2. عرض سعر
    rq = await cli.post(f"{SALES}/quotations", json={
        "code": "FLOW-Q1",
        "customer_id": cid,
        "date": str(date.today()),
        "valid_until": str(date.today()),
        "lines": [{"description": "منتج", "qty": 5, "unit": "قطعة", "unit_price": 200.0}],
    })
    assert rq.status_code == 201
    qid = rq.json()["id"]

    # 3. تحويل لفاتورة
    ri = await cli.post(f"{SALES}/quotations/{qid}/to-invoice", params={"invoice_code": "FLOW-INV1"})
    assert ri.status_code in (200, 201)
    inv_id = ri.json()["id"]
    assert ri.json()["total"] == 1000.0

    # 4. دفع جزئي
    rp = await cli.post(f"{SALES}/invoices/{inv_id}/payment", json={
        "amount": 600.0,
        "payment_date": str(date.today()),
        "payment_method": "cash",
    })
    assert rp.status_code in (200, 201)

    # 5. تحقق أن paid_amount تحدّث
    inv = await cli.get(f"{SALES}/invoices/{inv_id}")
    assert inv.json()["paid_amount"] >= 600.0


# ════════════════ FLOW 2: دورة الشراء الكاملة ═══════════════════════
@pytest.mark.asyncio
async def test_full_purchase_cycle(cli):
    # 1. مورد + مادة
    rs = await cli.post(f"{PURCH}/suppliers", json={"name": "مورد تدفق", "supplier_type": "local"})
    sid = rs.json()["id"]
    rm = await cli.post(f"{INV}/materials", json={"name_ar": "حديد تدفق", "unit": "طن", "unit_cost": 500.0})
    mid = rm.json()["id"]

    # 2. أمر شراء
    rpo = await cli.post(PO, json={
        "order_date": str(date.today()),
        "supplier_id": sid,
        "lines": [{"description": "حديد", "ordered_qty": 10, "unit": "طن",
                   "unit_cost": 500.0, "material_id": mid}],
    })
    assert rpo.status_code == 201
    po_id = rpo.json()["id"]

    # 3. موافقة
    await cli.patch(f"{PO}/{po_id}/approve")

    # 4. استلام
    pr = await cli.get(f"{PO}/{po_id}")
    line_id = pr.json()["lines"][0]["id"]
    stock_before = (await cli.get(f"{INV}/materials/{mid}")).json()["stock_qty"]

    await cli.post(f"{PO}/{po_id}/receive", json={
        "receive_date": str(date.today()),
        "lines": [{"line_id": line_id, "received_qty": 10}],
        "create_invoice": False,
    })

    # 5. تحقق المخزون
    stock_after = (await cli.get(f"{INV}/materials/{mid}")).json()["stock_qty"]
    assert stock_after >= stock_before + 10


# ════════════════ FLOW 3: دورة الإنتاج ══════════════════════════════
@pytest.mark.asyncio
async def test_full_production_cycle(cli):
    # 1. معدة + مادة بمخزون كافٍ
    req = await cli.post(EQ, json={"name_ar": "معدة تدفق", "level": 1})
    eq_id = req.json()["id"]

    rm = await cli.post(f"{INV}/materials", json={"name_ar": "مادة إنتاج", "unit": "كجم", "unit_cost": 10.0})
    mid = rm.json()["id"]
    await cli.post(f"{INV}/movements", json={"material_id": mid, "movement_type": "in", "qty": 200, "unit_cost": 10.0})

    # 2. BOM
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 10.0})

    # 3. أمر عمل
    rwo = await cli.post(f"{ACC}/work-orders", json={
        "equipment_id": eq_id, "planned_cost": 5000.0,
        "start_date": str(date.today()),
    })
    wo_id = rwo.json()["id"]

    # 4. حجز مواد
    await cli.post(f"{PROD}/reserve/{wo_id}", params={"quantity": 1})

    # 5. صرف
    rissue = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 10.0, "stage": "تجميع",
    })
    assert rissue.status_code in (200, 201)

    # 6. إتمام أمر العمل
    rc = await cli.post(f"{PROD}/complete/{wo_id}")
    assert rc.status_code in (200, 201)

    # 7. تحقق تحقق تغيّر الحالة
    wo = await cli.get(f"{ACC}/work-orders/{wo_id}")
    assert wo.json()["status"] in ("completed", "done", "closed")


# ════════════════ FLOW 4: مسير الرواتب ══════════════════════════════
@pytest.mark.asyncio
async def test_payroll_flow(cli):
    # 1. موظف
    rw = await cli.post(WORKERS, json={"name": "عامل مسير تدفق", "daily_wage": 200.0})
    wid = rw.json()["id"]

    # 2. تسجيل حضور يوم الأحد
    week_start = date.today() - timedelta(days=date.today().weekday())
    for d in range(5):
        work_day = week_start + timedelta(days=d)
        await cli.post(ATT, json={
            "worker_id": wid,
            "attendance_date": str(work_day),
            "status": "present",
        })

    # 3. مسير رواتب
    rp = await cli.post(f"{WORKERS}/payroll", json={
        "week_start": str(week_start),
        "week_end": str(week_start + timedelta(days=6)),
    })
    assert rp.status_code == 201
    run_id = rp.json()["id"]

    # 4. توليد بنود من الحضور
    rg = await cli.post(f"{WORKERS}/payroll/{run_id}/generate")
    assert rg.status_code in (200, 201)

    # 5. تحقق من تفاصيل المسير
    rd = await cli.get(f"{WORKERS}/payroll/{run_id}")
    assert rd.status_code == 200
    assert rd.json()["id"] == run_id


# ════════════════ FLOW 5: تحويل خزينة ═══════════════════════════════
@pytest.mark.asyncio
async def test_treasury_transfer_flow(cli):
    # 1. حسابان
    r1 = await cli.post(f"{TREAS}/accounts", json={"name": "صندوق مصدر", "kind": "cash", "opening_balance": 2000.0})
    r2 = await cli.post(f"{TREAS}/accounts", json={"name": "بنك وجهة", "kind": "bank", "opening_balance": 0.0})
    from_id = r1.json()["id"]
    to_id = r2.json()["id"]

    bal_from_before = r1.json()["balance"]
    bal_to_before = r2.json()["balance"]

    # 2. تحويل
    rt = await cli.post(f"{TREAS}/transfer", json={
        "from_treasury_id": from_id,
        "to_treasury_id": to_id,
        "amount": 800.0,
        "txn_date": str(date.today()),
        "description": "تحويل نقدي",
    })
    assert rt.status_code in (200, 201)

    # 3. تحقق الأرصدة
    accounts = {a["id"]: a for a in (await cli.get(f"{TREAS}/accounts")).json()}
    assert accounts[from_id]["balance"] <= bal_from_before - 800.0 + 0.01
    assert accounts[to_id]["balance"] >= bal_to_before + 800.0 - 0.01
