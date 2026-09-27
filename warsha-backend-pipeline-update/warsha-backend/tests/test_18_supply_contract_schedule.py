"""
test_18_supply_contract_schedule.py
اختبارات خطة أقساط عقد التوريد

يغطي:
  - حفظ قالب الأقساط على العقد (POST)
  - قراءة القالب (GET)
  - استبدال القالب (replace=true)
  - رفض مجموع نسب ≠ 100%
  - حذف القالب (DELETE)
  - تطبيق القالب تلقائياً عند تأكيد التسليم → InvoicePaymentSchedule
  - تأكيد التسليم بدون قالب → فاتورة عادية بدون أقساط
"""
import pytest
from datetime import date, timedelta

BASE_SC   = "/api/v1/supply-contracts"
BASE_INV  = "/api/v1/inventory"
BASE_CUST = "/api/v1/customers"
BASE_SALE = "/api/v1/sales"


# ── helpers ──────────────────────────────────────────────────────────

async def _make_customer(cli) -> int:
    r = await cli.post(BASE_CUST, json={"name": "عميل عقد", "phone": "0500000099"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_material(cli, qty: float = 200.0) -> int:
    mr = await cli.post(f"{BASE_INV}/materials", json={
        "name_ar": "حديد عقد", "unit": "كجم", "unit_cost": 10.0,
    })
    mid = mr.json()["id"]
    await cli.post(f"{BASE_INV}/movements", json={
        "material_id": mid, "movement_type": "in",
        "qty": qty, "unit_cost": 10.0,
    })
    return mid


async def _create_contract(cli, cust_id: int, material_id: int) -> dict:
    """ينشئ عقد بيع بسيط ويرجع dict الرد."""
    payload = {
        "customer_id": cust_id,
        "date": str(date.today()),
        "valid_until": str(date.today() + timedelta(days=90)),
        "payment_terms": "حسب جدول الأقساط",
        "tax_pct": "0",
        "discount_amt": "0",
        "lines": [{
            "description": "أعلاف محطة 1",
            "qty": "10",
            "unit": "طن",
            "unit_price": "1000",
            "material_id": material_id,
        }],
    }
    r = await cli.post(BASE_SC, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ── Tests: قالب الأقساط ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_schedule_template(cli):
    """يحفظ قالب أقساط ويرجع 3 صفوف بنسب صحيحة."""
    cid = await _make_customer(cli)
    mid = await _make_material(cli)
    c   = await _create_contract(cli, cid, mid)
    cid_contract = c["id"]

    r = await cli.post(f"{BASE_SC}/{cid_contract}/payment-schedule-template", json={
        "installments": [
            {"label": "دفعة مقدمة",   "pct": "30", "days_offset": "0"},
            {"label": "دفعة تصنيع",   "pct": "40", "days_offset": "30"},
            {"label": "دفعة تسليم",   "pct": "30", "days_offset": "60"},
        ]
    })
    assert r.status_code == 201, r.text
    rows = r.json()
    assert len(rows) == 3
    assert rows[0]["pct"] == 30.0
    assert rows[0]["label"] == "دفعة مقدمة"
    assert rows[2]["days_offset"] == 60


@pytest.mark.asyncio
async def test_get_schedule_template(cli):
    """يجلب القالب المحفوظ بـ total_pct = 100."""
    cid = await _make_customer(cli)
    mid = await _make_material(cli)
    c   = await _create_contract(cli, cid, mid)
    cid_contract = c["id"]

    await cli.post(f"{BASE_SC}/{cid_contract}/payment-schedule-template", json={
        "installments": [
            {"pct": "50", "days_offset": "0"},
            {"pct": "50", "days_offset": "45"},
        ]
    })
    r = await cli.get(f"{BASE_SC}/{cid_contract}/payment-schedule-template")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total_pct"] == 100.0
    assert len(data["installments"]) == 2


@pytest.mark.asyncio
async def test_replace_schedule_template(cli):
    """replace=true يستبدل القديم — العدد يتغير من 2 لـ 3."""
    cid = await _make_customer(cli)
    mid = await _make_material(cli)
    c   = await _create_contract(cli, cid, mid)
    cid_contract = c["id"]

    await cli.post(f"{BASE_SC}/{cid_contract}/payment-schedule-template", json={
        "installments": [
            {"pct": "50", "days_offset": "0"},
            {"pct": "50", "days_offset": "30"},
        ]
    })
    # استبدال بـ 3 أقساط
    r2 = await cli.post(
        f"{BASE_SC}/{cid_contract}/payment-schedule-template?replace=true",
        json={
            "installments": [
                {"label": "أولى",  "pct": "25", "days_offset": "0"},
                {"label": "ثانية", "pct": "50", "days_offset": "30"},
                {"label": "ثالثة", "pct": "25", "days_offset": "60"},
            ]
        }
    )
    assert r2.status_code == 201, r2.text
    assert len(r2.json()) == 3


@pytest.mark.asyncio
async def test_schedule_template_pct_not_100_rejected(cli):
    """مجموع النسب ≠ 100 → 400."""
    cid = await _make_customer(cli)
    mid = await _make_material(cli)
    c   = await _create_contract(cli, cid, mid)

    r = await cli.post(f"{BASE_SC}/{c['id']}/payment-schedule-template", json={
        "installments": [
            {"pct": "40", "days_offset": "0"},
            {"pct": "40", "days_offset": "30"},
        ]
    })
    assert r.status_code == 400, r.text
    assert "100" in r.json()["detail"]


@pytest.mark.asyncio
async def test_delete_schedule_template(cli):
    """حذف القالب → GET يرجع قائمة فارغة."""
    cid = await _make_customer(cli)
    mid = await _make_material(cli)
    c   = await _create_contract(cli, cid, mid)
    cid_contract = c["id"]

    await cli.post(f"{BASE_SC}/{cid_contract}/payment-schedule-template", json={
        "installments": [{"pct": "100", "days_offset": "0"}]
    })
    r_del = await cli.delete(f"{BASE_SC}/{cid_contract}/payment-schedule-template")
    assert r_del.status_code == 204

    r_get = await cli.get(f"{BASE_SC}/{cid_contract}/payment-schedule-template")
    assert r_get.json()["installments"] == []


@pytest.mark.asyncio
async def test_unknown_contract_returns_404(cli):
    """عقد غير موجود → 404."""
    r = await cli.get(f"{BASE_SC}/99999/payment-schedule-template")
    assert r.status_code == 404


# ── Tests: تطبيق القالب عند التسليم ─────────────────────────────────

@pytest.mark.asyncio
async def test_deliver_with_schedule_creates_installments(cli):
    """
    عقد عنده قالب 3 أقساط → عند تأكيد التسليم:
      - schedule_applied = true في الرد
      - الفاتورة تحتوي 3 InvoicePaymentSchedule
      - مجموع مبالغ الأقساط = إجمالي الفاتورة
    """
    cid = await _make_customer(cli)
    mid = await _make_material(cli, qty=500)
    c   = await _create_contract(cli, cid, mid)
    cid_contract = c["id"]

    # تفعيل العقد
    act = await cli.patch(f"{BASE_SC}/{cid_contract}/activate")
    assert act.status_code == 200, act.text

    # حفظ قالب 3 أقساط
    await cli.post(f"{BASE_SC}/{cid_contract}/payment-schedule-template", json={
        "installments": [
            {"label": "مقدم",    "pct": "30", "days_offset": "0"},
            {"label": "تصنيع",   "pct": "40", "days_offset": "30"},
            {"label": "استلام",  "pct": "30", "days_offset": "60"},
        ]
    })

    # تأكيد التسليم
    deliv = await cli.post(f"{BASE_SC}/{cid_contract}/deliver", json={
        "delivery_date":  str(date.today()),
        "received_by":    "محمد علي",
        "delivery_notes": "تم التسليم",
    })
    assert deliv.status_code == 200, deliv.text
    deliv_data = deliv.json()
    assert deliv_data["schedule_applied"] is True
    assert "invoice_id" in deliv_data

    inv_id = deliv_data["invoice_id"]

    # جلب الأقساط من الفاتورة
    sched_r = await cli.get(f"{BASE_SALE}/invoices/{inv_id}/payment-schedule")
    assert sched_r.status_code == 200, sched_r.text
    rows = sched_r.json()

    assert len(rows) == 3, f"توقعنا 3 أقساط، لقينا {len(rows)}"

    # تحقق المجموع = إجمالي الفاتورة
    inv_r = await cli.get(f"{BASE_SALE}/invoices/{inv_id}")
    inv_total = float(inv_r.json()["total"])
    schedule_total = sum(float(r["amount_due"]) for r in rows)
    assert abs(schedule_total - inv_total) < 0.02, (
        f"مجموع الأقساط {schedule_total} ≠ إجمالي الفاتورة {inv_total}"
    )

    # تحقق الأقساط بالترتيب الصحيح
    assert rows[0]["label"] == "مقدم"
    assert rows[1]["label"] == "تصنيع"
    assert rows[2]["label"] == "استلام"

    # تحقق days_offset صح
    base_date = date.today()
    assert rows[0]["due_date"] == str(base_date)
    assert rows[1]["due_date"] == str(base_date + timedelta(days=30))
    assert rows[2]["due_date"] == str(base_date + timedelta(days=60))


@pytest.mark.asyncio
async def test_deliver_without_schedule_no_installments(cli):
    """
    عقد بدون قالب أقساط → عند التسليم:
      - schedule_applied = false
      - الفاتورة لا تحتوي أقساط
    """
    cid = await _make_customer(cli)
    mid = await _make_material(cli, qty=200)
    c   = await _create_contract(cli, cid, mid)
    cid_contract = c["id"]

    await cli.patch(f"{BASE_SC}/{cid_contract}/activate")

    deliv = await cli.post(f"{BASE_SC}/{cid_contract}/deliver", json={
        "delivery_date": str(date.today()),
    })
    assert deliv.status_code == 200, deliv.text
    assert deliv.json()["schedule_applied"] is False

    inv_id = deliv.json()["invoice_id"]
    sched_r = await cli.get(f"{BASE_SALE}/invoices/{inv_id}/payment-schedule")
    assert sched_r.json() == []


@pytest.mark.asyncio
async def test_deliver_decrements_material_stock(cli):
    """
    تأكيد التسليم يجب أن يُنقص Material.stock_qty فعلياً (عبر inventory_ledger
    .post_issue) — قبل الإصلاح كان بيُنشئ StockMovement خام من غير ما يمر
    على post_issue، فـ stock_qty كان يفضل زي ما هو رغم "تسليم" الكمية.
    """
    cid = await _make_customer(cli)
    mid = await _make_material(cli, qty=200)
    stock_before = (await cli.get(f"{BASE_INV}/materials/{mid}")).json()["stock_qty"]

    c = await _create_contract(cli, cid, mid)
    cid_contract = c["id"]
    await cli.patch(f"{BASE_SC}/{cid_contract}/activate")

    deliv = await cli.post(f"{BASE_SC}/{cid_contract}/deliver", json={
        "delivery_date": str(date.today()),
    })
    assert deliv.status_code == 200, deliv.text

    stock_after = (await cli.get(f"{BASE_INV}/materials/{mid}")).json()["stock_qty"]
    # العقد فيه بند بكمية 10 (وحدة "طن" لكن الكمية المخزومة بتتعامل بالرقم
    # الخام في المادة نفسها — راجع _create_contract)
    assert float(stock_after) == float(stock_before) - 10.0, (
        f"المخزون قبل: {stock_before}، بعد: {stock_after} — لم يُنقص بالكمية المسلَّمة"
    )


@pytest.mark.asyncio
async def test_cancel_blocked_after_delivery_confirmed(cli):
    """لا يجوز إلغاء عقد توريد بعد تأكيد تسليمه (فاتورة ورصيد عميل وحركة مخزون تأثّروا فعلياً)."""
    cid = await _make_customer(cli)
    mid = await _make_material(cli, qty=200)
    c = await _create_contract(cli, cid, mid)
    cid_contract = c["id"]

    await cli.patch(f"{BASE_SC}/{cid_contract}/activate")
    deliv = await cli.post(f"{BASE_SC}/{cid_contract}/deliver", json={
        "delivery_date": str(date.today()),
    })
    assert deliv.status_code == 200, deliv.text

    r_cancel = await cli.patch(f"{BASE_SC}/{cid_contract}/cancel")
    assert r_cancel.status_code == 400, r_cancel.text


@pytest.mark.asyncio
async def test_deliver_rounding_correction(cli):
    """
    قالب 3×33.33% على إجمالي 1000 ج → مجموع الأقساط = 1000 بالظبط (آخر قسط يمتص الفرق).
    """
    cid = await _make_customer(cli)
    mid = await _make_material(cli, qty=1000)
    c   = await _create_contract(cli, cid, mid)
    cid_contract = c["id"]

    await cli.patch(f"{BASE_SC}/{cid_contract}/activate")

    await cli.post(f"{BASE_SC}/{cid_contract}/payment-schedule-template", json={
        "installments": [
            {"pct": "33.33", "days_offset": "0"},
            {"pct": "33.33", "days_offset": "30"},
            {"pct": "33.34", "days_offset": "60"},
        ]
    })

    deliv = await cli.post(f"{BASE_SC}/{cid_contract}/deliver", json={
        "delivery_date": str(date.today()),
    })
    assert deliv.status_code == 200

    inv_id = deliv.json()["invoice_id"]
    inv_r  = await cli.get(f"{BASE_SALE}/invoices/{inv_id}")
    inv_total = float(inv_r.json()["total"])

    sched_r = await cli.get(f"{BASE_SALE}/invoices/{inv_id}/payment-schedule")
    schedule_total = sum(float(r["amount_due"]) for r in sched_r.json())

    assert abs(schedule_total - inv_total) < 0.02, (
        f"مجموع {schedule_total} ≠ إجمالي {inv_total}"
    )
