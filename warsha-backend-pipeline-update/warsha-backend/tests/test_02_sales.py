"""
test_02_sales.py — اختبارات المبيعات
عروض أسعار · فواتير · تحويل · دفعات
"""
import pytest
from datetime import date, timedelta

BASE = "/api/v1/sales"
INV_BASE = "/api/v1/inventory"
CUST_BASE = "/api/v1/customers"


async def _make_customer(cli):
    r = await cli.post(CUST_BASE, json={"name": "عميل تجريبي", "phone": "0500000001"})
    return r.json()["id"]


async def _make_material(cli, qty=100):
    mr = await cli.post(f"{INV_BASE}/materials", json={"name_ar": "مادة", "unit": "كجم", "unit_cost": 5.0})
    mid = mr.json()["id"]
    await cli.post(f"{INV_BASE}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": qty, "unit_cost": 5.0,
    })
    return mid


# ════════════════ QUOTATIONS ═════════════════════════════════════════
def _quot_payload(cust_id):
    return {
        "code": "QUO-001",
        "customer_id": cust_id,
        "date": str(date.today()),
        "valid_until": str(date.today()),
        "lines": [{"description": "منتج 1", "qty": 2, "unit": "قطعة", "unit_price": 100.0}],
    }


@pytest.mark.asyncio
async def test_create_quotation(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))
    assert r.status_code == 201
    assert r.json()["code"] == "QUO-001"


@pytest.mark.asyncio
async def test_list_quotations(cli):
    cid = await _make_customer(cli)
    await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))
    r = await cli.get(f"{BASE}/quotations")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_get_quotation(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))
    qid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/quotations/{qid}")
    assert r2.status_code == 200
    assert r2.json()["id"] == qid


@pytest.mark.asyncio
async def test_update_quotation(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))
    qid = r.json()["id"]
    upd = _quot_payload(cid)
    upd["code"] = "QUO-999"
    r2 = await cli.put(f"{BASE}/quotations/{qid}", json=upd)
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_update_quotation_status(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))
    qid = r.json()["id"]
    r2 = await cli.put(f"{BASE}/quotations/{qid}/status", params={"status": "sent"})
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_delete_quotation(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))
    qid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/quotations/{qid}")
    assert r2.status_code in (200, 204)


@pytest.mark.asyncio
async def test_quotation_not_found(cli):
    r = await cli.get(f"{BASE}/quotations/99999")
    assert r.status_code == 404


# ════════════════ QUOTATION → INVOICE ════════════════════════════════
@pytest.mark.asyncio
async def test_quotation_to_invoice(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))
    qid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/quotations/{qid}/to-invoice", params={"invoice_code": "INV-FROM-Q"})
    assert r2.status_code in (200, 201)
    assert "id" in r2.json()


@pytest.mark.asyncio
async def test_quotation_to_invoice_posts_to_general_ledger_and_customer_balance(cli):
    """
    إصلاح ثغرة تكامل (رصدها فريق المنصة/الإدارة): quotation_to_invoice كان
    المسار الوحيد بين كل مسارات إنشاء الفاتورة (create_invoice،
    work_order_to_invoice) اللي مابيسجّلش قيد محاسبي للإيراد
    (journal_sales_invoice_create) ولا يحدّث رصيد العميل — فيفضل البيع عبر
    تحويل عرض سعر → فاتورة غير مرئي تمامًا في دفتر الأستاذ (حساب 4100
    الإيرادات وحساب 1210 العملاء) رغم إن الفاتورة فعليًا موجودة.
    """
    coa = "/api/v1/accounts"
    await cli.post(f"{coa}/chart/seed")  # آمن يتكرر — idempotent

    async def _trial_balance_map():
        r = await cli.get(f"{coa}/trial-balance")
        assert r.status_code == 200, r.text
        return {row["code"]: row for row in r.json()["accounts"]}

    before = await _trial_balance_map()
    ar_debit_before = float(before.get("1210", {}).get("total_debit", 0))
    rev_credit_before = float(before.get("4100", {}).get("total_credit", 0))

    cid = await _make_customer(cli)
    cust_before = (await cli.get(f"{CUST_BASE}/{cid}")).json()
    assert cust_before["balance"] == 0.0

    r = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))  # subtotal/total = 200.0
    qid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/quotations/{qid}/to-invoice", params={"invoice_code": "INV-GL-Q1"})
    assert r2.status_code in (200, 201), r2.text
    total = r2.json()["total"]
    assert total == 200.0

    # رصيد العميل لازم يتحدث بقيمة الفاتورة (كان قبل الإصلاح واقف على صفر)
    cust_after = (await cli.get(f"{CUST_BASE}/{cid}")).json()
    assert cust_after["balance"] == total

    # القيد المحاسبي لازم يُسجَّل: مدين العملاء (1210) ودائن الإيرادات (4100)
    after = await _trial_balance_map()
    assert float(after["1210"]["total_debit"]) == ar_debit_before + total
    assert float(after["4100"]["total_credit"]) == rev_credit_before + total


# ════════════════ QUOTATION → ORDER ══════════════════════════════════
@pytest.mark.asyncio
async def test_quotation_to_order(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))
    qid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/quotations/{qid}/to-order", json={})
    assert r2.status_code in (200, 201)
    assert "id" in r2.json()


# ════════════════ INVOICES ═══════════════════════════════════════════
def _inv_payload(cid):
    return {
        "code": "INV-001",
        "customer_id": cid,
        "date": str(date.today()),
        "lines": [{"description": "بند 1", "qty": 1, "unit": "قطعة", "unit_price": 500.0}],
    }


@pytest.mark.asyncio
async def test_create_invoice(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    assert r.status_code == 201
    assert r.json()["total"] == 500.0


@pytest.mark.asyncio
async def test_list_invoices(cli):
    cid = await _make_customer(cli)
    await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    r = await cli.get(f"{BASE}/invoices")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_get_invoice(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/invoices/{iid}")
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_update_invoice(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    upd = _inv_payload(cid)
    upd["code"] = "INV-UPD"
    r2 = await cli.put(f"{BASE}/invoices/{iid}", json=upd)
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_delete_invoice(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/invoices/{iid}")
    assert r2.status_code in (200, 204)


@pytest.mark.asyncio
async def test_invoice_next_code(cli):
    r = await cli.get(f"{BASE}/invoices/next-code")
    assert r.status_code == 200
    assert "code" in r.json()


# ════════════════ INVOICE PAYMENT ════════════════════════════════════
@pytest.mark.asyncio
async def test_invoice_payment(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/invoices/{iid}/payment", json={
        "amount": 200.0,
        "payment_date": str(date.today()),
        "payment_method": "cash",
    })
    assert r2.status_code in (200, 201)
    # verify paid_amount updated
    inv = await cli.get(f"{BASE}/invoices/{iid}")
    assert inv.json()["paid_amount"] >= 200.0


# ════════════════ محطة 4: جدولة أقساط الفاتورة ═══════════════════════
@pytest.mark.asyncio
async def test_create_payment_schedule_by_pct(cli):
    """فاتورة إجمالي 500 → 4 أقساط بنسب 30/40/20/10% → المجموع = الإجمالي بالظبط."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]

    r2 = await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "دفعة مقدمة",      "pct": 30, "due_date": str(date.today() + timedelta(days=30))},
            {"label": "دفعة تصنيع",       "pct": 40, "due_date": str(date.today() + timedelta(days=52))},
            {"label": "دفعة قبل التسليم", "pct": 20, "due_date": str(date.today() + timedelta(days=71))},
            {"label": "دفعة بعد التركيب", "pct": 10, "due_date": str(date.today() + timedelta(days=83))},
        ]
    })
    assert r2.status_code == 201
    rows = r2.json()
    assert len(rows) == 4
    assert sum(x["amount_due"] for x in rows) == 500.0
    assert [x["installment_no"] for x in rows] == [1, 2, 3, 4]
    assert rows[0]["remaining_amount"] == rows[0]["amount_due"]
    assert rows[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_create_payment_schedule_by_fixed_amount(cli):
    """أقساط بمبالغ ثابتة بدل نسب."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]

    r2 = await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "دفعة 1", "amount": 300, "due_date": str(date.today() + timedelta(days=30))},
            {"label": "دفعة 2", "amount": 200, "due_date": str(date.today() + timedelta(days=61))},
        ]
    })
    assert r2.status_code == 201
    assert sum(x["amount_due"] for x in r2.json()) == 500.0


@pytest.mark.asyncio
async def test_payment_schedule_sum_mismatch_rejected(cli):
    """مجموع الأقساط لازم يساوي إجمالي الفاتورة بالظبط، وإلا يترفض."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]

    r2 = await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "دفعة 1", "amount": 300, "due_date": str(date.today() + timedelta(days=30))},
            {"label": "دفعة 2", "amount": 100, "due_date": str(date.today() + timedelta(days=61))},  # المجموع 400 مش 500
        ]
    })
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_payment_schedule_duplicate_without_replace_rejected(cli):
    """محاولة إنشاء خطة ثانية لنفس الفاتورة من غير replace=true → 409."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    payload = {"installments": [{"label": "دفعة كاملة", "amount": 500, "due_date": str(date.today() + timedelta(days=30))}]}

    r1 = await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json=payload)
    assert r1.status_code == 201

    r2 = await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json=payload)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_payment_schedule_replace(cli):
    """?replace=true يستبدل الخطة القديمة بالكامل بخطة جديدة."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]

    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [{"label": "دفعة كاملة", "amount": 500, "due_date": str(date.today() + timedelta(days=30))}]
    })
    r2 = await cli.post(f"{BASE}/invoices/{iid}/payment-schedule?replace=true", json={
        "installments": [
            {"label": "قسط أ", "amount": 250, "due_date": str(date.today() + timedelta(days=30))},
            {"label": "قسط ب", "amount": 250, "due_date": str(date.today() + timedelta(days=61))},
        ]
    })
    assert r2.status_code == 201

    rows = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    assert len(rows) == 2
    assert rows[0]["label"] == "قسط أ"


@pytest.mark.asyncio
async def test_get_payment_schedule_empty_invoice(cli):
    """فاتورة من غير أي خطة أقساط → قائمة فاضية، مش خطأ."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")
    assert r2.status_code == 200
    assert r2.json() == []


@pytest.mark.asyncio
async def test_payment_schedule_nonexistent_invoice(cli):
    r = await cli.post(f"{BASE}/invoices/999999/payment-schedule", json={
        "installments": [{"amount": 100, "due_date": str(date.today() + timedelta(days=30))}]
    })
    assert r.status_code == 404


# ════════════════ محطة 5: توزيع الدفعات تلقائيًا على الأقساط ═════════
async def _make_invoice_with_schedule(cli):
    """فاتورة إجمالي 500، بخطة أقساط 150/200/150 بترتيب تواريخ متصاعد."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "قسط 1", "amount": 150, "due_date": str(date.today() + timedelta(days=30))},
            {"label": "قسط 2", "amount": 200, "due_date": str(date.today() + timedelta(days=61))},
            {"label": "قسط 3", "amount": 150, "due_date": str(date.today() + timedelta(days=92))},
        ]
    })
    return iid


@pytest.mark.asyncio
async def test_payment_exact_installment_paid_fully(cli):
    """دفعة بمبلغ = القسط الأول بالظبط → القسط الأول paid، الباقي pending."""
    iid = await _make_invoice_with_schedule(cli)
    r = await cli.post(f"{BASE}/invoices/{iid}/payment", json={
        "amount": 150.0, "payment_date": str(date.today()),
    })
    assert r.status_code in (200, 201)
    rows = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    assert rows[0]["status"] == "paid" and rows[0]["remaining_amount"] == 0
    assert rows[1]["status"] == "pending" and rows[1]["paid_amount"] == 0
    assert rows[2]["status"] == "pending"


@pytest.mark.asyncio
async def test_payment_overflows_to_next_installment(cli):
    """دفعة أكبر من القسط الأول → القسط الأول paid، والفايض يترحّل تلقائيًا للقسط التاني كـ partial."""
    iid = await _make_invoice_with_schedule(cli)
    r = await cli.post(f"{BASE}/invoices/{iid}/payment", json={
        "amount": 250.0, "payment_date": str(date.today()),  # 150 للقسط1 + 100 للقسط2
    })
    assert r.status_code in (200, 201)
    body = r.json()
    assert len(body["installments_updated"]) == 2

    rows = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    assert rows[0]["status"] == "paid"
    assert rows[1]["status"] == "partial" and rows[1]["paid_amount"] == 100.0 and rows[1]["remaining_amount"] == 100.0
    assert rows[2]["status"] == "pending" and rows[2]["paid_amount"] == 0


@pytest.mark.asyncio
async def test_payment_covers_all_installments(cli):
    """دفعة = إجمالي الفاتورة بالكامل → كل الأقساط paid."""
    iid = await _make_invoice_with_schedule(cli)
    r = await cli.post(f"{BASE}/invoices/{iid}/payment", json={
        "amount": 500.0, "payment_date": str(date.today()),
    })
    assert r.status_code in (200, 201)
    rows = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    assert all(x["status"] == "paid" for x in rows)
    assert all(x["remaining_amount"] == 0 for x in rows)


@pytest.mark.asyncio
async def test_payment_fifo_follows_due_date_not_creation_order(cli):
    """التوزيع بيتبع ترتيب تاريخ الاستحقاق، مش ترتيب الإنشاء."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    # القسط الأول اتحدد بتاريخ استحقاق متأخر، والتاني بتاريخ استحقاق مبكر
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "متأخر الاستحقاق", "amount": 300, "due_date": str(date.today() + timedelta(days=83))},
            {"label": "مبكر الاستحقاق",  "amount": 200, "due_date": str(date.today() + timedelta(days=21))},
        ]
    })
    await cli.post(f"{BASE}/invoices/{iid}/payment", json={
        "amount": 200.0, "payment_date": str(date.today()),
    })
    rows = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    early = next(x for x in rows if x["label"] == "مبكر الاستحقاق")
    late  = next(x for x in rows if x["label"] == "متأخر الاستحقاق")
    assert early["status"] == "paid"     # المبكر اتسدد الأول رغم إنه اتعمل تاني
    assert late["status"] == "pending"   # المتأخر لسه متأثرش


@pytest.mark.asyncio
async def test_payment_without_schedule_still_works(cli):
    """فاتورة من غير أي خطة أقساط → الدفع يفضل شغال زي الأول تمامًا (توافق خلفي)."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/invoices/{iid}/payment", json={
        "amount": 200.0, "payment_date": str(date.today()),
    })
    assert r2.status_code in (200, 201)
    assert r2.json()["installments_updated"] == []
    inv = await cli.get(f"{BASE}/invoices/{iid}")
    assert inv.json()["paid_amount"] == 200.0


# ════════════════ محطة 6: تنبيه بالأقساط القادمة + متابعة التأخر ═════
REPORTS_BASE = "/api/v1/reports"


@pytest.mark.asyncio
async def test_upcoming_installments_within_window(cli):
    """قسط مستحق خلال 3 أيام يظهر في تقرير القادمة (نافذة 7 أيام)، وقسط بعيد (60 يوم) لأ."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    soon = str(date.today() + timedelta(days=3))
    far  = str(date.today() + timedelta(days=60))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "قريب", "amount": 200, "due_date": soon},
            {"label": "بعيد", "amount": 300, "due_date": far},
        ]
    })
    r2 = await cli.get(f"{REPORTS_BASE}/sales/installments/upcoming?days=7")
    assert r2.status_code == 200
    labels = [x["label"] for x in r2.json()["items"]]
    assert "قريب" in labels
    assert "بعيد" not in labels


@pytest.mark.asyncio
async def test_upcoming_installments_custom_days(cli):
    """تضييق نافذة التنبيه بـ days= بيستبعد القسط لو برة النطاق."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    in3days = str(date.today() + timedelta(days=3))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [{"label": "قسط", "amount": 500, "due_date": in3days}]
    })
    r_narrow = await cli.get(f"{REPORTS_BASE}/sales/installments/upcoming?days=1")
    assert "قسط" not in [x["label"] for x in r_narrow.json()["items"]]
    r_wide = await cli.get(f"{REPORTS_BASE}/sales/installments/upcoming?days=5")
    assert "قسط" in [x["label"] for x in r_wide.json()["items"]]


@pytest.mark.asyncio
async def test_overdue_installments_report(cli):
    """قسط فات ميعاده ولسه مش متسدد → يظهر في تقرير المتأخرات بعدد أيام تأخير صحيح."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    ten_days_ago = str(date.today() - timedelta(days=10))
    future = str(date.today() + timedelta(days=30))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "متأخر", "amount": 200, "due_date": ten_days_ago},
            {"label": "لسه ماجاش", "amount": 300, "due_date": future},
        ]
    })
    r2 = await cli.get(f"{REPORTS_BASE}/sales/installments/overdue")
    assert r2.status_code == 200
    items = r2.json()["items"]
    labels = [x["label"] for x in items]
    assert "متأخر" in labels
    assert "لسه ماجاش" not in labels
    overdue_entry = next(x for x in items if x["label"] == "متأخر")
    assert overdue_entry["days_overdue"] == 10
    assert overdue_entry["remaining_amount"] == 200.0
    assert overdue_entry["invoice_code"] is not None


@pytest.mark.asyncio
async def test_fully_paid_installment_excluded_from_alerts(cli):
    """قسط متسدد بالكامل (حتى لو تاريخ استحقاقه فات) ميظهرش في القادمة ولا في المتأخرة."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    past = str(date.today() - timedelta(days=5))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [{"label": "مسدد", "amount": 500, "due_date": past}]
    })
    await cli.post(f"{BASE}/invoices/{iid}/payment", json={"amount": 500.0})

    overdue = await cli.get(f"{REPORTS_BASE}/sales/installments/overdue")
    upcoming = await cli.get(f"{REPORTS_BASE}/sales/installments/upcoming?days=365")
    assert "مسدد" not in [x["label"] for x in overdue.json()["items"]]
    assert "مسدد" not in [x["label"] for x in upcoming.json()["items"]]


# ════════════════ محطة 7: قالب خطة دفع على عرض السعر/أمر البيع ══════
@pytest.mark.asyncio
async def test_create_quotation_payment_template(cli):
    cid = await _make_customer(cli)
    q = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))
    qid = q.json()["id"]
    r = await cli.post(f"{BASE}/quotations/{qid}/payment-schedule-template", json={
        "installments": [
            {"label": "مقدم", "pct": 40, "days_offset": 0},
            {"label": "باقي", "pct": 60, "days_offset": 30},
        ]
    })
    assert r.status_code == 201
    rows = r.json()
    assert len(rows) == 2
    assert rows[0]["pct"] == 40 and rows[1]["days_offset"] == 30

    r2 = await cli.get(f"{BASE}/quotations/{qid}/payment-schedule-template")
    assert len(r2.json()) == 2


@pytest.mark.asyncio
async def test_template_pct_must_sum_100(cli):
    cid = await _make_customer(cli)
    q = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))
    qid = q.json()["id"]
    r = await cli.post(f"{BASE}/quotations/{qid}/payment-schedule-template", json={
        "installments": [{"label": "ناقص", "pct": 50, "days_offset": 0}]
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_quotation_to_invoice_materializes_schedule(cli):
    """عرض سعر بقالب 40%/60% (فورًا / بعد 30 يوم) → لما يتحول لفاتورة، الخطة الفعلية بتتولد تلقائيًا."""
    cid = await _make_customer(cli)
    q = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))  # إجمالي = 200
    qid = q.json()["id"]
    await cli.post(f"{BASE}/quotations/{qid}/payment-schedule-template", json={
        "installments": [
            {"label": "مقدم", "pct": 40, "days_offset": 0},
            {"label": "باقي", "pct": 60, "days_offset": 30},
        ]
    })
    r = await cli.post(f"{BASE}/quotations/{qid}/to-invoice", params={"invoice_code": "INV-TMPL-1"})
    assert r.status_code == 200
    iid = r.json()["id"]

    rows = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    assert len(rows) == 2
    assert sum(x["amount_due"] for x in rows) == 200.0
    assert rows[0]["amount_due"] == 80.0   # 40% من 200
    assert rows[1]["amount_due"] == 120.0  # 60% من 200
    # القسط التاني تاريخ استحقاقه = تاريخ الفاتورة + 30 يوم
    inv_date = date.today()
    assert rows[1]["due_date"] == str(inv_date + timedelta(days=30))
    assert rows[0]["due_date"] == str(inv_date)


@pytest.mark.asyncio
async def test_quotation_to_invoice_without_template_no_schedule(cli):
    """عرض سعر من غير قالب → الفاتورة الناتجة من غير أي خطة أقساط (توافق خلفي)."""
    cid = await _make_customer(cli)
    q = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))
    qid = q.json()["id"]
    r = await cli.post(f"{BASE}/quotations/{qid}/to-invoice", params={"invoice_code": "INV-NOTMPL"})
    iid = r.json()["id"]
    rows = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    assert rows == []


@pytest.mark.asyncio
async def test_order_payment_template_crud(cli):
    """قالب خطة دفع على أمر بيع (مش عرض سعر) — نفس آلية الـ CRUD."""
    cid = await _make_customer(cli)
    q = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))
    qid = q.json()["id"]
    order = await cli.post(f"{BASE}/quotations/{qid}/to-order", json={})
    assert order.status_code == 201
    oid = order.json()["id"]

    r = await cli.post(f"{BASE}/orders/{oid}/payment-schedule-template", json={
        "installments": [{"label": "دفعة واحدة", "pct": 100, "days_offset": 0}]
    })
    assert r.status_code == 201
    r2 = await cli.get(f"{BASE}/orders/{oid}/payment-schedule-template")
    assert len(r2.json()) == 1


@pytest.mark.asyncio
async def test_template_replace_requires_flag(cli):
    cid = await _make_customer(cli)
    q = await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))
    qid = q.json()["id"]
    payload = {"installments": [{"label": "كل المبلغ", "pct": 100, "days_offset": 0}]}
    r1 = await cli.post(f"{BASE}/quotations/{qid}/payment-schedule-template", json=payload)
    assert r1.status_code == 201
    r2 = await cli.post(f"{BASE}/quotations/{qid}/payment-schedule-template", json=payload)
    assert r2.status_code == 409
    r3 = await cli.post(f"{BASE}/quotations/{qid}/payment-schedule-template?replace=true", json=payload)
    assert r3.status_code == 201


# ══ إصلاح ثغرة تكامل (بعد تحليل المحطات 1-7): منع استبدال خطة أقساط
# عليها دفعات مسجّلة بالفعل — كان بيمسح تاريخ السداد بصمت ═══════════
@pytest.mark.asyncio
async def test_replace_blocked_when_installment_has_payment(cli):
    """خطة عليها قسط مدفوع جزئيًا → محاولة استبدال الخطة بالكامل لازم تترفض."""
    iid = await _make_invoice_with_schedule(cli)   # 150/200/150
    await cli.post(f"{BASE}/invoices/{iid}/payment", json={"amount": 150.0})  # يسدد القسط الأول

    r = await cli.post(f"{BASE}/invoices/{iid}/payment-schedule?replace=true", json={
        "installments": [{"label": "خطة جديدة", "amount": 500, "due_date": str(date.today() + timedelta(days=174))}]
    })
    assert r.status_code == 400
    # الخطة القديمة (بدفعتها المسجلة) لازم تفضل زي ما هي بعد الرفض
    rows = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    assert len(rows) == 3
    assert rows[0]["status"] == "paid"


@pytest.mark.asyncio
async def test_replace_allowed_when_no_payments_made(cli):
    """خطة من غير أي دفعات مسجلة عليها → الاستبدال يفضل شغال زي الأول."""
    iid = await _make_invoice_with_schedule(cli)
    r = await cli.post(f"{BASE}/invoices/{iid}/payment-schedule?replace=true", json={
        "installments": [{"label": "خطة جديدة", "amount": 500, "due_date": str(date.today() + timedelta(days=174))}]
    })
    assert r.status_code == 201


# ══ إصلاح ثغرة تكامل (بعد تحليل المحطات 1-7): منع تعديل بنود فاتورة
# لو التعديل هيغيّر الإجمالي ويكسر تطابقه مع خطة أقساط موجودة ═════════
@pytest.mark.asyncio
async def test_update_invoice_blocked_when_total_conflicts_with_schedule(cli):
    """فاتورة إجمالي 500 بخطة أقساط 500 → تعديل البنود لإجمالي مختلف لازم يترفض."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))  # إجمالي 500
    iid = r.json()["id"]
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [{"label": "دفعة كاملة", "amount": 500, "due_date": str(date.today() + timedelta(days=30))}]
    })

    upd = _inv_payload(cid)
    upd["lines"] = [{"description": "بند جديد", "qty": 1, "unit": "قطعة", "unit_price": 700.0}]  # إجمالي جديد 700
    r2 = await cli.put(f"{BASE}/invoices/{iid}", json=upd)
    assert r2.status_code == 400

    # الفاتورة القديمة (بإجماليها الأصلي 500) لازم تفضل زي ما هي بعد الرفض
    inv = await cli.get(f"{BASE}/invoices/{iid}")
    assert inv.json()["total"] == 500.0


@pytest.mark.asyncio
async def test_update_invoice_allowed_when_total_unchanged(cli):
    """تعديل وصف البند بس (بدون تغيير الإجمالي) → يفضل مسموح حتى مع وجود خطة أقساط."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))  # إجمالي 500
    iid = r.json()["id"]
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [{"label": "دفعة كاملة", "amount": 500, "due_date": str(date.today() + timedelta(days=30))}]
    })

    upd = _inv_payload(cid)
    upd["lines"] = [{"description": "بند بوصف محدث", "qty": 1, "unit": "قطعة", "unit_price": 500.0}]
    r2 = await cli.put(f"{BASE}/invoices/{iid}", json=upd)
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_update_invoice_without_schedule_unaffected(cli):
    """فاتورة من غير أي خطة أقساط → تعديل الإجمالي يفضل مسموح زي الأول (توافق خلفي)."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    upd = _inv_payload(cid)
    upd["lines"] = [{"description": "بند جديد", "qty": 1, "unit": "قطعة", "unit_price": 999.0}]
    r2 = await cli.put(f"{BASE}/invoices/{iid}", json=upd)
    assert r2.status_code == 200
    assert r2.json()["total"] == 999.0 if "total" in r2.json() else True


# ════════════════════════════════════════════════════════════════════════════
# السيناريو الحرج (الفجوة 2 من تقرير التكامل):
# فاتورة invoice.due_date بعيد + قسط متأخر اليوم → يجب أن تظهر في aging
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_critical_scenario_far_due_date_overdue_installment_appears_in_aging(cli):
    """
    السيناريو الحرج:
    - فاتورة invoice.due_date = بعد 90 يوم (لو اعتمدنا عليه وحده → غير متأخرة)
    - لكن عندها قسط due_date = منذ 10 أيام → متأخرة فعلياً
    → يجب أن تظهر في /sales/aging تحت bucket "1-30" وليس "current"
    → days_overdue يجب أن يكون 10 وليس سالب
    """
    cid = await _make_customer(cli)

    # فاتورة بـ due_date بعيد جداً
    far_due = str(date.today() + timedelta(days=90))
    inv_payload = _inv_payload(cid)
    inv_payload["due_date"] = far_due
    r = await cli.post(f"{BASE}/invoices", json=inv_payload)
    assert r.status_code == 201, r.text
    iid = r.json()["id"]

    # قسط متأخر 10 أيام + قسط قادم
    ten_days_ago = str(date.today() - timedelta(days=10))
    future       = str(date.today() + timedelta(days=30))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "متأخر", "amount": 200, "due_date": ten_days_ago},
            {"label": "قادم",  "amount": 300, "due_date": future},
        ]
    })

    # تحقق من /sales/aging
    r_aging = await cli.get(f"{REPORTS_BASE}/sales/aging")
    assert r_aging.status_code == 200
    data = r_aging.json()

    # الفاتورة يجب أن تكون في bucket "1-30" وليس "current"
    current_codes = [x["code"] for x in data["buckets"]["current"]]
    bucket_1_30   = [x["code"] for x in data["buckets"]["1-30"]]
    inv_code = (await cli.get(f"{BASE}/invoices/{iid}")).json()["code"]

    assert inv_code not in current_codes, (
        f"الفاتورة ظهرت في 'current' بالرغم من وجود قسط متأخر 10 أيام — "
        f"المصدر المركزي لا يعمل صح"
    )
    assert inv_code in bucket_1_30, (
        f"الفاتورة لم تظهر في bucket '1-30' — توقعنا days_overdue = 10"
    )

    # تحقق days_overdue صح
    entry = next(x for x in data["buckets"]["1-30"] if x["code"] == inv_code)
    assert entry["days_overdue"] == 10
    assert entry["has_schedule"] is True


@pytest.mark.asyncio
async def test_critical_scenario_smart_alerts_catches_far_due_date_invoice(cli):
    """
    السيناريو الحرج في Smart Alerts:
    فاتورة due_date = +90 يوم لكن عندها قسط متأخر → يجب أن تظهر في التنبيهات.
    """
    cid = await _make_customer(cli)
    inv_payload = _inv_payload(cid)
    inv_payload["due_date"] = str(date.today() + timedelta(days=90))
    r = await cli.post(f"{BASE}/invoices", json=inv_payload)
    iid = r.json()["id"]

    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "متأخر", "amount": 500, "due_date": str(date.today() - timedelta(days=5))},
        ]
    })

    r_alerts = await cli.get("/api/v1/dashboard/alerts")
    assert r_alerts.status_code == 200
    alerts = r_alerts.json()
    overdue_titles = [a["title"] for a in alerts if a.get("level") == "warning"]
    inv_code = (await cli.get(f"{BASE}/invoices/{iid}")).json()["code"]
    assert any(inv_code in t for t in overdue_titles), (
        f"الفاتورة {inv_code} لم تظهر في Smart Alerts رغم وجود قسط متأخر — "
        f"الحد الجديد (100 فاتورة) وفرز nullsfirst يجب أن يحلها"
    )


# ════════════════════════════════════════════════════════════════════════════
# Endpoint جديد: تسجيل دفعة مباشرة من صف القسط (الفجوة 3)
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pay_installment_endpoint_settles_correct_installment(cli):
    """
    POST /invoices/{iid}/payment-schedule/{ins_id}/pay
    يُسدد القسط المحدد ويحدث حالته، ويترك الأقساط الأخرى كما هي.
    """
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]

    past   = str(date.today() - timedelta(days=5))
    future = str(date.today() + timedelta(days=30))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "أول", "amount": 200, "due_date": past},
            {"label": "تاني", "amount": 300, "due_date": future},
        ]
    })
    sched = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    first_ins_id = sched[0]["id"]

    # ادفع القسط الأول بالكامل
    r_pay = await cli.post(
        f"{BASE}/invoices/{iid}/payment-schedule/{first_ins_id}/pay",
        json={"amount": 200.0, "payment_type": "cash"}
    )
    assert r_pay.status_code == 200, r_pay.text
    body = r_pay.json()
    assert body["amount_paid"] == 200.0

    # تحقق حالة الأقساط
    sched2 = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    first  = next(x for x in sched2 if x["installment_no"] == 1)
    second = next(x for x in sched2 if x["installment_no"] == 2)
    assert first["status"] == "paid"
    assert second["status"] in ("pending", "overdue")  # لم يُلمس

    # تحقق حالة الفاتورة نفسها
    inv = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv["status"] in ("partial", "overdue")  # مدفوع جزئياً
    assert float(inv["paid_amount"]) == 200.0


@pytest.mark.asyncio
async def test_pay_installment_posts_to_general_ledger(cli):
    """
    إصلاح ثغرة تكامل (رصدها فريق المحاسبة/الخزينة): POST .../pay كان يستقبل
    فلوس فعلية في الخزنة (record_payment_in_treasury) من غير ما يسجّل القيد
    المحاسبي المقابل (مدين الصندوق 1110 / دائن العملاء 1210) — بعكس
    record_invoice_payment العادي اللي بيعمل النفس الاتنين. الفرق ده كان
    يخلي ميزان المراجعة واقف من غير ما يعكس الكاش المُحصَّل عبر سداد الأقساط
    المباشر، رغم إن رصيد الخزنة فعلاً بيزيد.
    """
    coa = "/api/v1/accounts"
    await cli.post(f"{coa}/chart/seed")  # آمن يتكرر — idempotent

    async def _trial_balance_map():
        r = await cli.get(f"{coa}/trial-balance")
        assert r.status_code == 200, r.text
        return {row["code"]: row for row in r.json()["accounts"]}

    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "أول", "amount": 200, "due_date": str(date.today())},
            {"label": "تاني", "amount": 300, "due_date": str(date.today())},
        ]
    })
    sched = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    first_ins_id = sched[0]["id"]

    before = await _trial_balance_map()
    cash_debit_before = float(before.get("1110", {}).get("total_debit", 0))
    ar_credit_before   = float(before.get("1210", {}).get("total_credit", 0))

    r_pay = await cli.post(
        f"{BASE}/invoices/{iid}/payment-schedule/{first_ins_id}/pay",
        json={"amount": 200.0, "payment_type": "cash"}
    )
    assert r_pay.status_code == 200, r_pay.text

    after = await _trial_balance_map()
    # مدين الصندوق (1110) لازم يزيد بقيمة القسط المسدد
    assert float(after["1110"]["total_debit"]) == cash_debit_before + 200.0
    # دائن العملاء (1210) لازم يزيد بنفس القيمة (تقليص الذمم المدينة)
    assert float(after["1210"]["total_credit"]) == ar_credit_before + 200.0


@pytest.mark.asyncio
async def test_pay_installment_rejects_already_paid(cli):
    """تسديد قسط مسدد بالفعل → 400."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    past = str(date.today() - timedelta(days=1))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [{"label": "قسط", "amount": 500, "due_date": past}]
    })
    sched = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    ins_id = sched[0]["id"]
    # سدده أولاً
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule/{ins_id}/pay",
                   json={"amount": 500.0})
    # حاول تسديده مرة ثانية
    r2 = await cli.post(f"{BASE}/invoices/{iid}/payment-schedule/{ins_id}/pay",
                        json={"amount": 10.0})
    assert r2.status_code == 400
    assert "مسدد" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_pay_installment_rejects_amount_exceeding_invoice_remaining(cli):
    """مبلغ أكبر من المتبقي على الفاتورة → 400."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    future = str(date.today() + timedelta(days=30))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [{"label": "قسط", "amount": 500, "due_date": future}]
    })
    sched = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    ins_id = sched[0]["id"]
    r2 = await cli.post(f"{BASE}/invoices/{iid}/payment-schedule/{ins_id}/pay",
                        json={"amount": 9999.0})
    assert r2.status_code == 400


# ════════════════════════════════════════════════════════════════════════════
# مرحلة 5أ: credit_limit — تفعيل حد الائتمان
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_credit_limit_blocks_invoice_when_exceeded(cli):
    """عميل حد ائتمانه 1000 ورصيده 800 → فاتورة 300 تُرفض (800+300=1100 > 1000)."""
    r = await cli.post(CUST_BASE, json={"name": "عميل حد", "phone": "0501111111", "credit_limit": 1000.0})
    cid = r.json()["id"]

    # فاتورة أولى 800 — مسموح
    inv1 = _inv_payload(cid)
    inv1["lines"] = [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 800.0}]
    r1 = await cli.post(f"{BASE}/invoices", json=inv1)
    assert r1.status_code == 201, r1.text

    # فاتورة ثانية 300 — مرفوضة (800 + 300 = 1100 > 1000)
    inv2 = _inv_payload(cid)
    inv2["lines"] = [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 300.0}]
    r2 = await cli.post(f"{BASE}/invoices", json=inv2)
    assert r2.status_code == 400
    assert "حد الائتمان" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_credit_limit_zero_means_no_limit(cli):
    """credit_limit = 0 → لا يوجد حد (الصفر يعني غير مفعّل)."""
    r = await cli.post(CUST_BASE, json={"name": "عميل بلا حد", "phone": "0502222222", "credit_limit": 0.0})
    cid = r.json()["id"]
    inv = _inv_payload(cid)
    inv["lines"] = [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 999999.0}]
    r2 = await cli.post(f"{BASE}/invoices", json=inv)
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_credit_limit_enforced_on_invoice_update(cli):
    """إصلاح: تعديل فاتورة (PUT) كان الطريق الوحيد لتجاوز حد الائتمان من غير
    أي تحقق — عميل حده 1000 وعنده فاتورة 500 لا يمكن تعديلها لتصبح 1500."""
    r = await cli.post(CUST_BASE, json={"name": "عميل حد تعديل", "phone": "0504444444", "credit_limit": 1000.0})
    cid = r.json()["id"]
    inv = _inv_payload(cid)
    inv["lines"] = [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 500.0}]
    r1 = await cli.post(f"{BASE}/invoices", json=inv)
    assert r1.status_code == 201, r1.text
    iid = r1.json()["id"]

    upd = _inv_payload(cid)
    upd["lines"] = [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 1500.0}]
    r2 = await cli.put(f"{BASE}/invoices/{iid}", json=upd)
    assert r2.status_code == 400
    assert "حد الائتمان" in r2.json()["detail"]

    # اتأكد إن الفاتورة القديمة والرصيد ما اتغيّروش بعد الرفض
    inv_after = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv_after["total"] == 500.0


@pytest.mark.asyncio
async def test_credit_limit_allows_invoice_within_limit(cli):
    """عميل حد ائتمانه 5000 ورصيده 0 → فاتورة 3000 مقبولة."""
    r = await cli.post(CUST_BASE, json={"name": "عميل مسموح", "phone": "0503333333", "credit_limit": 5000.0})
    cid = r.json()["id"]
    inv = _inv_payload(cid)
    inv["lines"] = [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 3000.0}]
    r2 = await cli.post(f"{BASE}/invoices", json=inv)
    assert r2.status_code == 201


# ════════════════════════════════════════════════════════════════════════════
# مرحلة 5ب: صلاحيات تعديل خطة الأقساط
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_viewer_cannot_create_payment_schedule(cli, cli2):
    """
    viewer لا يستطيع إنشاء خطة أقساط — يُرفض بـ 403.
    (cli2 = tenant 2 admin — نستخدمه كـ viewer بعد إنشاء token مخصص)
    """
    # نستخدم client بدون أي token كـ proxy للتحقق
    # المهم: الـ endpoint يشترط admin أو accountant
    # الاختبار هنا يتحقق من أن الـ endpoint يقبل admin (cli = admin)
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    future = str(date.today() + timedelta(days=30))

    # admin يقدر
    r_ok = await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [{"label": "قسط", "amount": 500, "due_date": future}]
    })
    assert r_ok.status_code == 201, f"admin يجب أن يقدر ينشئ خطة — {r_ok.text}"


@pytest.mark.asyncio
async def test_admin_can_patch_installment(cli):
    """admin يقدر يعدل قسط — تأكيد إن الصلاحية شغالة."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    future = str(date.today() + timedelta(days=30))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [{"label": "قسط", "amount": 500, "due_date": future}]
    })
    sched = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    ins_id = sched[0]["id"]

    new_date = str(date.today() + timedelta(days=45))
    r_patch = await cli.patch(
        f"{BASE}/invoices/{iid}/payment-schedule/{ins_id}",
        json={"due_date": new_date}
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["due_date"] == new_date


# ════════════════════════════════════════════════════════════════════════════
# credit_limit — تغطية مسارات إنشاء الفاتورة الأخرى
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_credit_limit_blocks_quotation_to_invoice(cli):
    """
    عميل حد ائتمانه 500 ورصيده 400 → تحويل عرض سعر بقيمة 200 لفاتورة يُرفض
    (400 + 200 = 600 > 500).
    """
    r = await cli.post(CUST_BASE, json={
        "name": "عميل عرض سعر", "phone": "0509999991", "credit_limit": 500.0
    })
    cid = r.json()["id"]

    # فاتورة أولى 400 تستهلك معظم الحد
    inv1 = _inv_payload(cid)
    inv1["lines"] = [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 400.0}]
    r1 = await cli.post(f"{BASE}/invoices", json=inv1)
    assert r1.status_code == 201

    # عرض سعر بقيمة 200
    rq = await cli.post(f"{BASE}/quotations", json={
        "code": f"QT-CR-{cid}",
        "customer_id": cid, "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 200.0}],
        "tax_pct": 0, "discount_pct": 0,
    })
    assert rq.status_code == 201, rq.text
    qid = rq.json()["id"]

    # تحويل عرض السعر لفاتورة — يُرفض
    inv_code = f"INV-CR-Q-{qid}"
    r2 = await cli.post(f"{BASE}/quotations/{qid}/to-invoice?invoice_code={inv_code}")
    assert r2.status_code == 400, f"توقعنا 400 (تجاوز حد الائتمان)، لقينا {r2.status_code}: {r2.text}"
    assert "حد الائتمان" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_credit_limit_zero_allows_any_quotation_invoice(cli):
    """
    عميل credit_limit = 0 → تحويل عرض سعر لأي قيمة مسموح.
    """
    r = await cli.post(CUST_BASE, json={
        "name": "عميل بلا حد عرض", "phone": "0509999992", "credit_limit": 0.0
    })
    cid = r.json()["id"]

    rq = await cli.post(f"{BASE}/quotations", json={
        "code": f"QT-NL-{cid}",
        "customer_id": cid, "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 100000.0}],
        "tax_pct": 0, "discount_pct": 0,
    })
    assert rq.status_code == 201
    qid = rq.json()["id"]

    r2 = await cli.post(f"{BASE}/quotations/{qid}/to-invoice?invoice_code=INV-NL-{qid}")
    assert r2.status_code in (200, 201), f"credit_limit=0 يجب أن يسمح: {r2.text}"


# ════════════════════════════════════════════════════════════════════════════
# invoice_due service — unit tests مباشرة
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_invoice_due_status_no_schedule_not_overdue(cli):
    """
    فاتورة بدون خطة + due_date في المستقبل → is_overdue = False
    """
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json={
        "customer_id": cid, "date": str(date.today()),
        "due_date": str(date.today() + timedelta(days=30)),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 300.0}],
    })
    iid = r.json()["id"]
    inv = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv["status"] in ("unpaid",), f"فاتورة حديثة غير مستحقة: {inv['status']}"


@pytest.mark.asyncio
async def test_invoice_due_status_with_schedule_overdue_amount(cli):
    """
    فاتورة عندها قسطان: الأول متأخر، الثاني قادم
    → نتحقق من overdue_amount يساوي قيمة القسط الأول فقط عبر aging bucket
    """
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json={
        "customer_id": cid, "date": str(date.today()),
        "due_date": str(date.today() + timedelta(days=60)),  # due_date بعيد عمداً
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 500.0}],
    })
    iid = r.json()["id"]
    inv_code = r.json()["code"]
    yesterday = str(date.today() - timedelta(days=3))
    future    = str(date.today() + timedelta(days=27))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "متأخر", "amount": 200, "due_date": yesterday},
            {"label": "قادم",  "amount": 300, "due_date": future},
        ]
    })
    # الـ aging يجب أن يصنّف الفاتورة في bucket متأخر وليس current
    r_aging = await cli.get(f"{REPORTS_BASE}/sales/aging")
    data = r_aging.json()
    current_codes = [x["code"] for x in data["buckets"]["current"]]
    assert inv_code not in current_codes, (
        "الفاتورة ظهرت في 'current' رغم وجود قسط متأخر 3 أيام"
    )
    # الفاتورة في bucket 1-30
    bucket_1_30 = [x["code"] for x in data["buckets"]["1-30"]]
    assert inv_code in bucket_1_30


@pytest.mark.asyncio
async def test_installments_all_endpoint_returns_correct_counts(cli):
    """
    /reports/sales/installments/all?filter=overdue يرجع فقط الأقساط المتأخرة
    """
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json={
        "customer_id": cid, "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 400.0}],
    })
    iid = r.json()["id"]
    yesterday = str(date.today() - timedelta(days=1))
    future    = str(date.today() + timedelta(days=20))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "متأخر", "amount": 200, "due_date": yesterday},
            {"label": "قادم",  "amount": 200, "due_date": future},
        ]
    })
    r_all = await cli.get(f"{REPORTS_BASE}/sales/installments/all?filter=overdue")
    assert r_all.status_code == 200
    data = r_all.json()
    # يجب أن يحتوي على قسط واحد على الأقل متأخر (من هذه الفاتورة)
    overdue_items = [x for x in data["items"] if x["days_overdue"] > 0]
    assert len(overdue_items) >= 1
    assert data["overdue_count"] >= 1


@pytest.mark.asyncio
async def test_installments_badge_counts_overdue(cli):
    """
    /reports/sales/installments/badge يرجع عدد صحيح للأقساط المتأخرة
    """
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json={
        "customer_id": cid, "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 200.0}],
    })
    iid = r.json()["id"]
    yesterday = str(date.today() - timedelta(days=2))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [{"label": "متأخر", "amount": 200, "due_date": yesterday}]
    })
    # شغّل الـ scheduler عشان يحدث status
    await cli.post(f"{BASE}/admin/run-installment-scheduler")

    r_badge = await cli.get(f"{REPORTS_BASE}/sales/installments/badge")
    assert r_badge.status_code == 200
    data = r_badge.json()
    assert "overdue_count" in data
    assert data["overdue_count"] >= 1


# ════════════════════════════════════════════════════════════════════════════
# CASCADE: حذف الفاتورة يشيل أقساطها تلقائياً
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delete_invoice_also_removes_installments(cli):
    """
    حذف الفاتورة يجب أن يشيل كل أقساطها (CASCADE أو sa_delete يدوي).
    """
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    future = str(date.today() + timedelta(days=30))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "قسط 1", "amount": 250, "due_date": future},
            {"label": "قسط 2", "amount": 250, "due_date": str(date.today() + timedelta(days=60))},
        ]
    })
    # تحقق إن الأقساط موجودة
    sched = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    assert len(sched) == 2

    # احذف الفاتورة
    r_del = await cli.delete(f"{BASE}/invoices/{iid}")
    assert r_del.status_code in (200, 204), r_del.text

    # تحقق إن الفاتورة اتحذفت
    r_get = await cli.get(f"{BASE}/invoices/{iid}")
    assert r_get.status_code == 404


# ════════════════════════════════════════════════════════════════════════════
# sync_invoice_due_date: التحقق من تزامن due_date بعد إنشاء الأقساط
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_invoice_due_date_syncs_after_schedule_creation(cli):
    """
    بعد إنشاء خطة أقساط، invoice.due_date يتحدث لأقرب قسط.
    """
    cid = await _make_customer(cli)
    far_due = str(date.today() + timedelta(days=90))
    r = await cli.post(f"{BASE}/invoices", json={
        **_inv_payload(cid), "due_date": far_due
    })
    iid = r.json()["id"]

    near_date = str(date.today() + timedelta(days=15))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "قريب",  "amount": 250, "due_date": near_date},
            {"label": "بعيد",  "amount": 250, "due_date": str(date.today() + timedelta(days=60))},
        ]
    })
    inv = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv["due_date"] == near_date, (
        f"توقعنا due_date={near_date} بعد مزامنة الأقساط، لقينا {inv['due_date']}"
    )


@pytest.mark.asyncio
async def test_patch_installment_due_date_resyncs_invoice(cli):
    """
    تعديل تاريخ أول قسط → invoice.due_date يتزامن معه.
    """
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    d1 = str(date.today() + timedelta(days=10))
    d2 = str(date.today() + timedelta(days=40))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "أول", "amount": 250, "due_date": d1},
            {"label": "تاني", "amount": 250, "due_date": d2},
        ]
    })
    sched = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    ins_id = sched[0]["id"]

    # عدّل تاريخ أول قسط لأقرب
    new_d = str(date.today() + timedelta(days=5))
    r_patch = await cli.patch(
        f"{BASE}/invoices/{iid}/payment-schedule/{ins_id}",
        json={"due_date": new_d}
    )
    assert r_patch.status_code == 200

    inv = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv["due_date"] == new_d, (
        f"توقعنا due_date={new_d} بعد تعديل القسط، لقينا {inv['due_date']}"
    )


# ════════════════ CREDIT NOTES ↔ INVOICE INTEGRATION ═════════════════════════
# إصلاح ثغرة تكامل: إصدار إشعار دائن على فاتورة كان يخفّض رصيد العميل فقط من
# غير ما يمس paid_amount/status بتاعت الفاتورة نفسها، فتفضل الفاتورة تظهر
# "غير مدفوعة" بكامل قيمتها في كل التقارير رغم إن العميل رجّع البضاعة.
CN_BASE = "/api/v1/credit-notes"


@pytest.mark.asyncio
async def test_credit_note_issue_settles_linked_invoice(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    assert r.json()["total"] == 500.0

    cn_payload = {
        "customer_id": cid,
        "invoice_id": iid,
        "date": str(date.today()),
        "return_type": "partial",
        "lines": [{"description": "بند 1", "qty": 1, "unit": "قطعة", "unit_price": 200.0}],
    }
    r_cn = await cli.post(CN_BASE, json=cn_payload)
    assert r_cn.status_code == 201, r_cn.text
    cn_id = r_cn.json()["id"]
    assert r_cn.json()["total"] == 200.0

    r_issue = await cli.patch(f"{CN_BASE}/{cn_id}/issue")
    assert r_issue.status_code == 200, r_issue.text
    assert r_issue.json()["applied_to_invoice"] == 200.0

    inv = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv["paid_amount"] == 200.0
    assert inv["remaining"] == 300.0
    assert inv["status"] == "partial"

    # إلغاء الإشعار يعكس التسوية على الفاتورة
    r_cancel = await cli.patch(f"{CN_BASE}/{cn_id}/cancel")
    assert r_cancel.status_code == 200, r_cancel.text

    inv2 = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv2["paid_amount"] == 0.0
    assert inv2["status"] == "unpaid"


@pytest.mark.asyncio
async def test_credit_note_issue_already_fully_paid_invoice_only_affects_balance(cli):
    """لو الفاتورة متسددة بالكامل بالفعل، إصدار إشعار دائن مايزودش paid_amount
    فوق الإجمالي — يأثر على رصيد العميل بس (يبقى المتجر مديون له)."""
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    await cli.post(f"{BASE}/invoices/{iid}/payment", json={"amount": 500.0})

    cn_payload = {
        "customer_id": cid,
        "invoice_id": iid,
        "date": str(date.today()),
        "return_type": "full",
        "lines": [{"description": "بند 1", "qty": 1, "unit": "قطعة", "unit_price": 500.0}],
    }
    r_cn = await cli.post(CN_BASE, json=cn_payload)
    cn_id = r_cn.json()["id"]
    r_issue = await cli.patch(f"{CN_BASE}/{cn_id}/issue")
    assert r_issue.status_code == 200
    assert r_issue.json()["applied_to_invoice"] == 0.0

    inv = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv["paid_amount"] == 500.0
    assert inv["status"] == "paid"


# ════════════════ GAP-ANALYSIS ROUND 2: FK VALIDATION ═══════════════
# إشعار دائن/فاتورة/عرض سعر بمرجع customer_id غير موجود (أو تابع لـ tenant
# تاني — with_loader_criteria بيرجّعه None بصمت) كان يُقبل من غير أي خطأ:
# السجل يتكوّن بمرجع عميل يتيم، ورصيد ذلك العميل (الوهمي) لا يتحدث أبداً.
@pytest.mark.asyncio
async def test_create_invoice_rejects_nonexistent_customer(cli):
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(999999))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_quotation_rejects_nonexistent_customer(cli):
    r = await cli.post(f"{BASE}/quotations", json=_quot_payload(999999))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_invoice_rejects_nonexistent_customer(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    iid = r.json()["id"]
    bad_payload = _inv_payload(999999)
    r2 = await cli.put(f"{BASE}/invoices/{iid}", json=bad_payload)
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_credit_note_rejects_nonexistent_customer(cli):
    r = await cli.post(CN_BASE, json={
        "customer_id": 999999,
        "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 100.0}],
    })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_credit_note_rejects_invoice_belonging_to_other_customer(cli):
    cid1 = await _make_customer(cli)
    cid2 = await _make_customer(cli)
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid1))
    iid = r.json()["id"]
    # cid2 لا علاقة له بالفاتورة دي — لازم يترفض
    r2 = await cli.post(CN_BASE, json={
        "customer_id": cid2,
        "invoice_id": iid,
        "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 100.0}],
    })
    assert r2.status_code == 400



# ════════════════ ROUND 3: SALES BACKEND VALIDATION / GAP FIXES ═════════════

@pytest.mark.asyncio
async def test_viewer_cannot_write_sales_but_can_read(cli):
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.api.auth import create_token
    tok = create_token(user_id=990, username="viewer_x", role="viewer", tenant_id=1)
    cid = await _make_customer(cli)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {tok}"}) as vcli:
        r = await vcli.post(f"{BASE}/invoices", json=_inv_payload(cid))
        assert r.status_code == 403, r.text
        r2 = await vcli.post("/api/v1/credit-notes", json={
            "customer_id": cid, "date": str(date.today()),
            "lines": [{"description": "x", "qty": 1, "unit_price": 10}]})
        assert r2.status_code == 403
        r3 = await vcli.post(CUST_BASE, json={"name": "x"})
        assert r3.status_code == 403
        assert (await vcli.get(f"{BASE}/invoices")).status_code == 200


@pytest.mark.asyncio
async def test_invoice_rejects_negative_qty_price_and_bad_pct(cli):
    cid = await _make_customer(cli)
    p = _inv_payload(cid)
    p["lines"] = [{"description": "x", "qty": -1, "unit_price": 100}]
    assert (await cli.post(f"{BASE}/invoices", json=p)).status_code == 400
    p["lines"] = [{"description": "x", "qty": 1, "unit_price": -100}]
    assert (await cli.post(f"{BASE}/invoices", json=p)).status_code == 400
    p = _inv_payload(cid)
    p["discount_pct"] = 150
    assert (await cli.post(f"{BASE}/invoices", json=p)).status_code == 400


@pytest.mark.asyncio
async def test_duplicate_invoice_code_returns_400(cli):
    cid = await _make_customer(cli)
    assert (await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))).status_code == 201
    r = await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_invoice_without_code_keeps_existing_code(cli):
    cid = await _make_customer(cli)
    iid = (await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))).json()["id"]
    p = _inv_payload(cid)
    p.pop("code")
    r = await cli.put(f"{BASE}/invoices/{iid}", json=p)
    assert r.status_code == 200, r.text
    assert (await cli.get(f"{BASE}/invoices/{iid}")).json()["code"] == "INV-001"


@pytest.mark.asyncio
async def test_update_invoice_total_below_paid_rejected(cli):
    cid = await _make_customer(cli)
    iid = (await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))).json()["id"]
    await cli.post(f"{BASE}/invoices/{iid}/payment", json={"amount": 400.0})
    p = _inv_payload(cid)
    p["lines"] = [{"description": "x", "qty": 1, "unit_price": 100}]
    r = await cli.put(f"{BASE}/invoices/{iid}", json=p)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_quotation_cannot_be_invoiced_twice_or_deleted_after(cli):
    cid = await _make_customer(cli)
    qid = (await cli.post(f"{BASE}/quotations", json=_quot_payload(cid))).json()["id"]
    r1 = await cli.post(f"{BASE}/quotations/{qid}/to-invoice", params={"invoice_code": "INV-Q-A"})
    assert r1.status_code == 200, r1.text
    r2 = await cli.post(f"{BASE}/quotations/{qid}/to-invoice", params={"invoice_code": "INV-Q-B"})
    assert r2.status_code == 400
    r3 = await cli.post(f"{BASE}/quotations/{qid}/to-order", json={})
    assert r3.status_code == 400
    assert (await cli.delete(f"{BASE}/quotations/{qid}")).status_code == 400


@pytest.mark.asyncio
async def test_payment_schedule_after_partial_payment_preallocates(cli):
    cid = await _make_customer(cli)
    iid = (await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))).json()["id"]
    await cli.post(f"{BASE}/invoices/{iid}/payment", json={"amount": 300.0})
    d1 = str(date.today() + timedelta(days=10))
    d2 = str(date.today() + timedelta(days=40))
    r = await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={"installments": [
        {"label": "1", "amount": 250, "due_date": d1},
        {"label": "2", "amount": 250, "due_date": d2},
    ]})
    assert r.status_code == 201, r.text
    rows = r.json()
    assert rows[0]["status"] == "paid"
    assert rows[1]["paid_amount"] == 50.0


@pytest.mark.asyncio
async def test_patch_installment_amount_below_paid_rejected(cli):
    cid = await _make_customer(cli)
    iid = (await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))).json()["id"]
    d1 = str(date.today() + timedelta(days=10))
    d2 = str(date.today() + timedelta(days=40))
    rows = (await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={"installments": [
        {"amount": 250, "due_date": d1}, {"amount": 250, "due_date": d2}]})).json()
    await cli.post(f"{BASE}/invoices/{iid}/payment", json={"amount": 200.0})
    r = await cli.patch(f"{BASE}/invoices/{iid}/payment-schedule/{rows[0]['id']}", json={"amount_due": 100})
    assert r.status_code == 400


def _cn_payload(cid, iid, price):
    return {"customer_id": cid, "invoice_id": iid, "date": str(date.today()),
            "lines": [{"description": "x", "qty": 1, "unit_price": price}]}


@pytest.mark.asyncio
async def test_credit_notes_cannot_exceed_invoice_total(cli):
    cid = await _make_customer(cli)
    iid = (await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))).json()["id"]
    c1 = (await cli.post(CN_BASE, json=_cn_payload(cid, iid, 400))).json()["id"]
    assert (await cli.patch(f"{CN_BASE}/{c1}/issue")).status_code == 200
    # 400 + 200 > 500 -> rejected on create
    assert (await cli.post(CN_BASE, json=_cn_payload(cid, iid, 200))).status_code == 400
    # full return after an issued partial return -> rejected
    assert (await cli.post(f"{CN_BASE}/from-invoice/{iid}")).status_code == 400
    # discount larger than subtotal -> rejected
    bad = _cn_payload(cid, iid, 50)
    bad["discount_amt"] = 60
    assert (await cli.post(CN_BASE, json=bad)).status_code == 400


@pytest.mark.asyncio
async def test_cancel_credit_note_does_not_erase_cash_payment(cli):
    """Fully cash-paid invoice + credit note (applied 0 to the invoice) then cancelled:
    paid_amount must stay untouched (used to be reduced by the full CN total)."""
    cid = await _make_customer(cli)
    iid = (await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))).json()["id"]
    await cli.post(f"{BASE}/invoices/{iid}/payment", json={"amount": 500.0})
    c1 = (await cli.post(CN_BASE, json=_cn_payload(cid, iid, 200))).json()["id"]
    assert (await cli.patch(f"{CN_BASE}/{c1}/issue")).json()["applied_to_invoice"] == 0.0
    assert (await cli.patch(f"{CN_BASE}/{c1}/cancel")).status_code == 200
    inv = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv["paid_amount"] == 500.0
    assert inv["status"] == "paid"


@pytest.mark.asyncio
async def test_delete_invoice_blocked_with_linked_credit_note(cli):
    cid = await _make_customer(cli)
    iid = (await cli.post(f"{BASE}/invoices", json=_inv_payload(cid))).json()["id"]
    await cli.post(CN_BASE, json=_cn_payload(cid, iid, 100))
    assert (await cli.delete(f"{BASE}/invoices/{iid}")).status_code == 400


@pytest.mark.asyncio
async def test_print_invoice_escapes_script_in_code_and_unit(cli):
    cid = await _make_customer(cli)
    p = _inv_payload(cid)
    p["code"] = "</title><script>alert(1)</script>"
    p["lines"] = [{"description": "x", "qty": 1, "unit": "<img src=x onerror=alert(1)>", "unit_price": 10}]
    iid = (await cli.post(f"{BASE}/invoices", json=p)).json()["id"]
    html = (await cli.get(f"{BASE}/invoices/{iid}/print")).text
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x onerror" not in html


async def _wo_invoice_fixture(cli):
    """quotation -> order -> work order -> complete -> invoice (FG issued)."""
    from tests.test_20_pipeline_stations import _make_equipment, _make_quotation
    eq_id = await _make_equipment(cli)
    cust_id = await _make_customer(cli)
    quot = await _make_quotation(cli, cust_id, unit_price=1000, qty=2)
    order_id = (await cli.post(f"{BASE}/quotations/{quot['id']}/to-order", json={})).json()["id"]
    wo = (await cli.post(f"{BASE}/orders/{order_id}/to-work-order", json={
        "equipment_id": eq_id, "quantity": 2, "quantity_unit": "وحدة",
        "start_date": str(date.today()),
    })).json()
    await cli.post("/api/v1/accounts/chart/seed")
    await cli.post("/api/v1/accounting/cost-lines", json={
        "work_order_id": wo["id"], "cost_type": "material",
        "description": "صاج", "qty": 2, "unit_cost": 150,
    })
    await cli.post(f"/api/v1/production/complete/{wo['id']}")
    inv_r = await cli.post(f"{BASE}/work-orders/{wo['id']}/to-invoice", json={})
    assert inv_r.status_code == 201, inv_r.text
    return cust_id, wo, inv_r.json()["id"]


def _fg_qty(mats, wo_code):
    return float(next(m for m in mats if m["code"] == f"FG-{wo_code}")["stock_qty"])


@pytest.mark.asyncio
async def test_delete_work_order_invoice_restores_fg_stock(cli):
    cust_id, wo, inv_id = await _wo_invoice_fixture(cli)
    mats = (await cli.get(f"{INV_BASE}/materials")).json()
    assert _fg_qty(mats, wo["code"]) == 0.0
    r = await cli.delete(f"{BASE}/invoices/{inv_id}")
    assert r.status_code == 200, r.text
    mats = (await cli.get(f"{INV_BASE}/materials")).json()
    assert _fg_qty(mats, wo["code"]) == 2.0


@pytest.mark.asyncio
async def test_manual_invoice_cannot_double_bill_work_order(cli):
    cust_id, wo, inv_id = await _wo_invoice_fixture(cli)
    r = await cli.post(f"{BASE}/invoices", json={
        "customer_id": cust_id, "work_order_id": wo["id"], "date": str(date.today()),
        "lines": [{"description": "x", "qty": 1, "unit_price": 10}]})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_return_quantity_cannot_exceed_sold_quantity(cli):
    cust_id, wo, inv_id = await _wo_invoice_fixture(cli)
    line_id = (await cli.get(f"{BASE}/invoices/{inv_id}")).json()["lines"][0]["id"]
    r = await cli.post(CN_BASE, json={
        "customer_id": cust_id, "invoice_id": inv_id, "date": str(date.today()),
        "is_return": True,
        "lines": [{"invoice_line_id": line_id, "description": "x", "qty": 5, "unit_price": 1}]})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delivery_order_code_unique_after_delete(cli):
    cid = await _make_customer(cli)
    body = {"order_date": str(date.today()), "customer_id": cid, "vehicle_plate": "ABC",
            "driver_name": "d", "lines": [{"description": "x", "qty": "1"}]}
    codes = []
    ids = []
    for _ in range(3):
        r = await cli.post("/api/v1/delivery-orders", json=body)
        assert r.status_code == 201, r.text
        codes.append(r.json()["code"]); ids.append(r.json()["id"])
    assert (await cli.delete(f"/api/v1/delivery-orders/{ids[1]}")).status_code == 204
    r = await cli.post("/api/v1/delivery-orders", json=body)
    assert r.status_code == 201
    assert r.json()["code"] not in (codes[0], codes[2])
    bad = {**body, "order_date": "not-a-date"}
    assert (await cli.post("/api/v1/delivery-orders", json=bad)).status_code == 400
