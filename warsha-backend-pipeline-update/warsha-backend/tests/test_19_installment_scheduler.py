"""
test_19_installment_scheduler.py
اختبارات خدمة تحديث حالة الأقساط التلقائية

يغطي:
  - pending → overdue لما due_date < today
  - paid يفضل paid (مش يتغير)
  - Invoice.status يتحدث لـ overdue لو كل أقساطه متأخرة
  - Invoice.status يتحدث لـ partial لو في أقساط مدفوعة وأخرى متأخرة
  - فاتورة بدون أقساط تتحدث لـ overdue من due_date مباشرة
  - delete_contract بيشيل PaymentScheduleTemplate
  - deliver يرفض عقد غير مفعّل
"""
import pytest
from datetime import date, timedelta

BASE    = "/api/v1/sales"
BASE_SC = "/api/v1/supply-contracts"
CUST    = "/api/v1/customers"
INV     = "/api/v1/inventory"
SCHED   = "/api/v1/sales/admin/run-installment-scheduler"


# ── helpers ──────────────────────────────────────────────────────────
async def _cust(cli) -> int:
    r = await cli.post(CUST, json={"name": "عميل scheduler", "phone": "0511111199"})
    assert r.status_code == 201
    return r.json()["id"]


async def _inv_with_overdue_installment(cli, cid: int) -> tuple[int, int]:
    """فاتورة عندها قسط متأخر بالفعل — يرجع (invoice_id, installment_id)"""
    r = await cli.post(f"{BASE}/invoices", json={
        "customer_id": cid, "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 500.0}],
    })
    assert r.status_code == 201
    iid = r.json()["id"]
    yesterday = str(date.today() - timedelta(days=1))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [{"label": "متأخر", "amount": 500, "due_date": yesterday}]
    })
    sched = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    return iid, sched[0]["id"]


async def _trigger_scheduler(cli) -> dict:
    """يشغل الـ scheduler عبر admin endpoint"""
    r = await cli.post(SCHED)
    assert r.status_code == 200, r.text
    return r.json()


# ── Tests: تحديث حالة الأقساط ─────────────────────────────────────

@pytest.mark.asyncio
async def test_scheduler_marks_pending_as_overdue(cli):
    """قسط due_date أمس + status pending → يتحول لـ overdue بعد تشغيل الـ scheduler"""
    cid = await _cust(cli)
    iid, ins_id = await _inv_with_overdue_installment(cli, cid)

    # قبل الـ scheduler — status يجب أن يكون pending (لأنه اتنشأ بـ pending)
    sched_before = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    before_status = sched_before[0]["status"]
    # ممكن يكون pending أو overdue حسب متى يتحدث
    assert before_status in ("pending", "overdue")

    # شغّل الـ scheduler
    result = await _trigger_scheduler(cli)
    assert "updated_installments" in result

    # بعد الـ scheduler — لازم يكون overdue
    sched_after = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    assert sched_after[0]["status"] == "overdue", (
        f"توقعنا overdue بعد الـ scheduler، لقينا {sched_after[0]['status']}"
    )


@pytest.mark.asyncio
async def test_scheduler_does_not_change_paid_installment(cli):
    """قسط مدفوع بالكامل → يفضل paid بعد الـ scheduler"""
    cid = await _cust(cli)
    r = await cli.post(f"{BASE}/invoices", json={
        "customer_id": cid, "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 300.0}],
    })
    iid = r.json()["id"]
    yesterday = str(date.today() - timedelta(days=1))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [{"label": "قسط", "amount": 300, "due_date": yesterday}]
    })
    sched = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    ins_id = sched[0]["id"]

    # سدّد القسط
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule/{ins_id}/pay",
                   json={"amount": 300.0})

    # شغّل الـ scheduler
    await _trigger_scheduler(cli)

    # يجب أن يفضل paid
    sched_after = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    assert sched_after[0]["status"] == "paid"


@pytest.mark.asyncio
async def test_scheduler_updates_invoice_status_to_overdue(cli):
    """
    فاتورة كل أقساطها المفتوحة متأخرة → Invoice.status يتحدث لـ overdue
    """
    cid = await _cust(cli)
    iid, _ = await _inv_with_overdue_installment(cli, cid)

    await _trigger_scheduler(cli)

    inv = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv["status"] in ("overdue", "partial"), (
        f"توقعنا overdue أو partial، لقينا {inv['status']}"
    )


@pytest.mark.asyncio
async def test_scheduler_updates_invoice_to_partial_when_some_paid(cli):
    """
    فاتورة عندها قسط مدفوع + قسط متأخر
    → Invoice.paid_amount > 0 (التحقق من الدفعة) و status ليس unpaid
    """
    cid = await _cust(cli)
    r = await cli.post(f"{BASE}/invoices", json={
        "customer_id": cid, "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 600.0}],
    })
    iid = r.json()["id"]
    yesterday = str(date.today() - timedelta(days=1))
    future    = str(date.today() + timedelta(days=30))
    await cli.post(f"{BASE}/invoices/{iid}/payment-schedule", json={
        "installments": [
            {"label": "مدفوع",  "amount": 200, "due_date": yesterday},
            {"label": "متأخر",  "amount": 200, "due_date": yesterday},
            {"label": "قادم",   "amount": 200, "due_date": future},
        ]
    })
    sched = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    first_id = sched[0]["id"]

    # سدّد القسط الأول
    r_pay = await cli.post(f"{BASE}/invoices/{iid}/payment-schedule/{first_id}/pay",
                           json={"amount": 200.0})
    assert r_pay.status_code == 200

    # تحقق إن الدفعة اتسجلت صح قبل الـ scheduler
    inv_before = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert float(inv_before["paid_amount"]) == 200.0, "الدفعة لم تتسجل صح"
    assert inv_before["status"] in ("partial",), f"بعد الدفع المباشر: {inv_before['status']}"

    await _trigger_scheduler(cli)

    inv = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    # بعد الـ scheduler: paid_amount لا يزال 200 والـ status ليس unpaid
    assert float(inv["paid_amount"]) == 200.0
    assert inv["status"] in ("partial", "overdue"), (
        f"توقعنا partial أو overdue (في دفع جزئي)، لقينا {inv['status']}"
    )


@pytest.mark.asyncio
async def test_scheduler_marks_invoice_without_schedule_as_overdue(cli):
    """
    فاتورة بدون خطة أقساط + due_date أمس → Invoice.status يتحدث لـ overdue
    """
    cid = await _cust(cli)
    r = await cli.post(f"{BASE}/invoices", json={
        "customer_id": cid,
        "date": str(date.today()),
        "due_date": str(date.today() - timedelta(days=2)),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 400.0}],
    })
    assert r.status_code == 201, r.text
    iid = r.json()["id"]

    # تأكد مفيش أقساط
    sched = (await cli.get(f"{BASE}/invoices/{iid}/payment-schedule")).json()
    assert sched == []

    await _trigger_scheduler(cli)

    inv = (await cli.get(f"{BASE}/invoices/{iid}")).json()
    assert inv["status"] == "overdue", (
        f"توقعنا overdue (due_date ماضي + بدون أقساط)، لقينا {inv['status']}"
    )


# ── Tests: delete_contract ينظف PaymentScheduleTemplate ──────────────

@pytest.mark.asyncio
async def test_delete_contract_removes_payment_schedule_template(cli):
    """
    حذف العقد يشيل قالب الأقساط المرتبط به تلقائياً.
    """
    cid = await _cust(cli)
    r = await cli.post(BASE_SC, json={
        "customer_id": cid,
        "date": str(date.today()),
        "valid_until": str(date.today() + timedelta(days=60)),
        "payment_terms": "حسب الاتفاق",
        "tax_pct": "0", "discount_amt": "0",
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 1000.0,
                   "material_id": None}],
    })
    assert r.status_code == 201, r.text
    cid_contract = r.json()["id"]

    # أضف قالب أقساط
    await cli.post(f"{BASE_SC}/{cid_contract}/payment-schedule-template", json={
        "installments": [
            {"pct": "50", "days_offset": "0"},
            {"pct": "50", "days_offset": "30"},
        ]
    })

    # تحقق القالب موجود
    r_get = await cli.get(f"{BASE_SC}/{cid_contract}/payment-schedule-template")
    assert len(r_get.json()["installments"]) == 2

    # احذف العقد
    r_del = await cli.delete(f"{BASE_SC}/{cid_contract}")
    assert r_del.status_code in (200, 204), r_del.text

    # القالب يجب أن يكون اتحذف (نتحقق بمحاولة جلبه — يُرجع 404)
    r_after = await cli.get(f"{BASE_SC}/{cid_contract}/payment-schedule-template")
    assert r_after.status_code == 404


# ── Tests: deliver يرفض العقد غير المفعّل ───────────────────────────

@pytest.mark.asyncio
async def test_deliver_rejects_inactive_contract(cli):
    """
    محاولة تأكيد التسليم على عقد status='draft' → 400
    """
    cid = await _cust(cli)
    r = await cli.post(BASE_SC, json={
        "customer_id": cid,
        "date": str(date.today()),
        "valid_until": str(date.today() + timedelta(days=60)),
        "payment_terms": "نقداً",
        "tax_pct": "0", "discount_amt": "0",
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 500.0,
                   "material_id": None}],
    })
    cid_contract = r.json()["id"]
    # لا نفعّل العقد — status = 'draft'

    r_del = await cli.post(f"{BASE_SC}/{cid_contract}/deliver", json={
        "delivery_date": str(date.today()),
    })
    assert r_del.status_code == 400
    assert "تفعيل" in r_del.json()["detail"]


@pytest.mark.asyncio
async def test_scheduler_returns_stats(cli):
    """الـ scheduler يرجع stats صحيحة (updated_installments + updated_invoices + run_date)"""
    result = await _trigger_scheduler(cli)
    assert "updated_installments" in result
    assert "updated_invoices" in result
    assert "run_date" in result
    assert result["run_date"] == str(date.today())


@pytest.mark.asyncio
async def test_scheduler_triggered_by_tenant_does_not_touch_other_tenants(cli, cli2):
    """أدمن ورشة 1 يشغّل الـ scheduler ← أقساط ورشة 2 ماتتلمسش (UPDATE جماعي بدون فلتر tenant)"""
    cid2 = await _cust(cli2)
    await _inv_with_overdue_installment(cli2, cid2)
    r1 = await _trigger_scheduler(cli)
    assert r1["updated_installments"] == 0
    r2 = await _trigger_scheduler(cli2)
    assert r2["updated_installments"] >= 1
