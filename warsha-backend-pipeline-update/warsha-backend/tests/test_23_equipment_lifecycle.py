"""
test_23_equipment_lifecycle.py — دورة المعدة الكاملة زي ما الواجهة بتنفذها:
حجز ← صرف (بالحجز) ← إنهاء من قائمة الحالة ← فاتورة من شاشة الأمر ← تسليم.

كل حالة هنا كانت ثغرة حقيقية ظهرت في تجربة الواجهة:
- صرف المواد بالحجز مكانش بيسجل قيد WIP/مخزون خام.
- "منتهي" من قائمة الحالة كان بيغيّر النص بس (مفيش FG ولا قيد إقفال).
- فاتورة الأمر من الشاشة (POST /sales/invoices) مكانتش بتخصم FG ولا COGS.
- فاتورة ثم تسليم (أو العكس) كان بيخصم FG مرتين أو يرفض التسليم.
"""
import pytest
from datetime import date

from tests.test_20_pipeline_stations import (
    PROD, ACC, INV, DO,
    _make_equipment, _make_material, _make_work_order, _make_customer, _make_delivery_order,
)

from tests.test_16_reports_dashboard_isolation import _seed_coa

SALES = "/api/v1/sales"


async def _fg_qty(cli, wo_code):
    mats = (await cli.get(f"{INV}/materials")).json()
    fg = next((m for m in mats if m["code"] == f"FG-{wo_code}"), None)
    return None if fg is None else float(fg["stock_qty"])


async def _account_balance(code):
    """رصيد حساب من دفتر الأستاذ (مدين − دائن) مباشرة من الداتابيز."""
    from sqlalchemy import select, func, case
    from tests.conftest import TestSessionLocal
    from app.models.models import AccountingEntryLine, ChartOfAccount
    async with TestSessionLocal() as s:
        val = await s.scalar(
            select(func.coalesce(func.sum(case(
                (AccountingEntryLine.side == "debit", AccountingEntryLine.amount),
                else_=-AccountingEntryLine.amount,
            )), 0))
            .join(ChartOfAccount, ChartOfAccount.id == AccountingEntryLine.account_id)
            .where(ChartOfAccount.code == code)
        )
    return float(val or 0)


async def _wo_issued_and_done(cli):
    await _seed_coa(cli)  # من غير دليل حسابات القيود بتتخطّى بصمت
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo = await _make_work_order(cli, eq_id, qty=1)
    r = await cli.post(f"{PROD}/reserve/{wo['id']}", json={"material_id": mid, "qty": 10})
    assert r.status_code in (200, 201), r.text
    res_list = (await cli.get(f"{PROD}/{wo['id']}/reservations")).json()
    res_id = (res_list[0] if isinstance(res_list, list) else res_list["reservations"][0])["id"]
    r = await cli.post(f"{PROD}/issue-by-reservation?reservation_id={res_id}")
    assert r.status_code == 200, r.text
    return eq_id, wo


@pytest.mark.asyncio
async def test_issue_by_reservation_posts_wip_journal(cli):
    await _wo_issued_and_done(cli)
    # 10 × 5 = 50 اتنقلت من مخزون الخامات لـ WIP في دفتر الأستاذ
    assert await _account_balance("1320") == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_status_done_runs_real_completion_and_cannot_reopen(cli):
    _, wo = await _wo_issued_and_done(cli)
    r = await cli.patch(f"{ACC}/work-orders/{wo['id']}/status?status=done")
    assert r.status_code == 200, r.text
    assert await _fg_qty(cli, wo["code"]) == 1.0
    # WIP اتقفل: المدين وقت الصرف = الدائن وقت الإقفال
    assert await _account_balance("1320") == pytest.approx(0.0, abs=0.01)

    r = await cli.patch(f"{ACC}/work-orders/{wo['id']}/status?status=in_progress")
    assert r.status_code == 400
    assert await _fg_qty(cli, wo["code"]) == 1.0


async def _invoice_from_wo_screen(cli, wo, cust_id):
    return await cli.post(f"{SALES}/invoices", json={
        "code": "", "customer_id": cust_id, "work_order_id": wo["id"], "date": str(date.today()),
        "tax_pct": 0, "discount_pct": 0,
        "lines": [{"description": "معدة", "qty": 1, "unit": "وحدة", "unit_price": 1500, "sort_order": 0}],
    })


@pytest.mark.asyncio
async def test_invoice_from_wo_screen_releases_fg_and_posts_cogs(cli):
    _, wo = await _wo_issued_and_done(cli)
    await cli.patch(f"{ACC}/work-orders/{wo['id']}/status?status=done")
    cust = await _make_customer(cli)
    r = await _invoice_from_wo_screen(cli, wo, cust)
    assert r.status_code == 201, r.text
    assert await _fg_qty(cli, wo["code"]) == 0.0
    assert await _account_balance("5100") > 0  # تكلفة بضاعة مباعة


@pytest.mark.asyncio
async def test_invoice_from_wo_screen_blocked_before_completion(cli):
    _, wo = await _wo_issued_and_done(cli)
    cust = await _make_customer(cli)
    r = await _invoice_from_wo_screen(cli, wo, cust)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_invoice_then_delivery_releases_fg_once(cli):
    eq_id, wo = await _wo_issued_and_done(cli)
    await cli.patch(f"{ACC}/work-orders/{wo['id']}/status?status=done")
    cust = await _make_customer(cli)
    assert (await _invoice_from_wo_screen(cli, wo, cust)).status_code == 201
    cogs_after_invoice = await _account_balance("5100")

    order = await _make_delivery_order(cli, cust, [{
        "description": "معدة", "qty": "1", "equipment_id": eq_id, "work_order_id": wo["id"],
    }])
    await cli.patch(f"{DO}/{order['id']}/status", json={"status": "confirmed"})
    r = await cli.patch(f"{DO}/{order['id']}/status", json={"status": "delivered"})
    assert r.status_code == 200, r.text  # كان بيترفض: "الرصيد غير كافٍ"
    assert await _fg_qty(cli, wo["code"]) == 0.0
    assert await _account_balance("5100") == pytest.approx(cogs_after_invoice)


@pytest.mark.asyncio
async def test_delivery_then_invoice_releases_fg_once(cli):
    eq_id, wo = await _wo_issued_and_done(cli)
    await cli.patch(f"{ACC}/work-orders/{wo['id']}/status?status=done")
    cust = await _make_customer(cli)
    order = await _make_delivery_order(cli, cust, [{
        "description": "معدة", "qty": "1", "equipment_id": eq_id, "work_order_id": wo["id"],
    }])
    await cli.patch(f"{DO}/{order['id']}/status", json={"status": "confirmed"})
    assert (await cli.patch(f"{DO}/{order['id']}/status", json={"status": "delivered"})).status_code == 200
    cogs_after_delivery = await _account_balance("5100")

    r = await _invoice_from_wo_screen(cli, wo, cust)
    assert r.status_code == 201, r.text
    assert await _fg_qty(cli, wo["code"]) == 0.0
    assert await _account_balance("5100") == pytest.approx(cogs_after_delivery)
