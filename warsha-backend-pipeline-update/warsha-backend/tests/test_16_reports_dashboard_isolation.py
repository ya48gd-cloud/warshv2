"""
test_16_reports_dashboard_isolation.py — اختبارات عزل المستأجرين للتقارير والداشبورد

CRITICAL REGRESSION TEST: قبل هذا الإصلاح كانت كل استعلامات app/api/reports.py
(~50 endpoint) و app/api/dashboard_new.py بدون أي فلتر tenant_id — يعني أي
مستخدم مسجّل دخول كان بيشوف أرقام/بيانات كل الورش (المستأجرين) مجمّعة مع بعض
بدل بيانات ورشته فقط. راجع تقرير الأمان لتفاصيل الإصلاح.

هذا الملف يتأكد إن التقارير والداشبورد بترجع بيانات tenant الحالي فقط.
"""
import pytest
from datetime import date

INV = "/api/v1/inventory"
REPORTS = "/api/v1/reports"
DASHBOARD = "/api/v1/dashboard"
PROD = "/api/v1/production"
ACCTS = "/api/v1/accounts"
EQ = "/api/v1/equipment"
CUST = "/api/v1/customers"
SALES = "/api/v1/sales"
SC = "/api/v1/supply-contracts"


async def _create_material(cli, name="مادة اختبار", unit_cost=10.0, reorder_level=0):
    r = await cli.post(f"{INV}/materials", json={
        "name_ar": name, "unit": "كجم", "unit_cost": unit_cost,
        "reorder_level": reorder_level,
    })
    assert r.status_code == 201, r.text
    return r.json()


# ════════════════ REPORTS — INVENTORY ════════════════════════════════
@pytest.mark.asyncio
async def test_reports_inventory_summary_isolation(cli, cli2):
    """t1 ينشئ مواد — تقرير ملخص المخزون بتاع t2 لازم يفضل صفر (ماشافش مواد t1)"""
    await _create_material(cli, "مادة T1-A")
    await _create_material(cli, "مادة T1-B")

    r2 = await cli2.get(f"{REPORTS}/inventory/summary")
    assert r2.status_code == 200
    assert r2.json()["total_items"] == 0

    r1 = await cli.get(f"{REPORTS}/inventory/summary")
    assert r1.status_code == 200
    assert r1.json()["total_items"] == 2


@pytest.mark.asyncio
async def test_reports_inventory_value_isolation(cli, cli2):
    """قيمة المخزون الإجمالية (/reports/inventory/value) لازم متتلوّتش ببيانات مستأجر تاني"""
    await _create_material(cli, "مادة قيمة T1", unit_cost=500.0)

    r2 = await cli2.get(f"{REPORTS}/inventory/value")
    assert r2.status_code == 200
    assert r2.json()["total_value"] == 0
    assert r2.json()["by_category"] == []


# ════════════════ REPORTS — EXECUTIVE DASHBOARD / EXPORTS ═══════════
@pytest.mark.asyncio
async def test_reports_executive_dashboard_isolation(cli, cli2):
    await _create_material(cli, "مادة تنفيذي T1", unit_cost=200.0)

    r2 = await cli2.get(f"{REPORTS}/dashboard")
    assert r2.status_code == 200
    assert r2.json()["inventory"]["total_value"] == 0


@pytest.mark.asyncio
async def test_reports_export_materials_isolation(cli, cli2):
    await _create_material(cli, "مادة تصدير T1")

    r2 = await cli2.get(f"{REPORTS}/export/materials")
    assert r2.status_code == 200
    # CSV بدون بيانات — سطر الهيدر فقط
    body = r2.content.decode("utf-8-sig")
    data_lines = [ln for ln in body.strip().splitlines()[1:] if ln.strip()]
    assert data_lines == []


# ════════════════ DASHBOARD KPIs ═════════════════════════════════════
@pytest.mark.asyncio
async def test_dashboard_kpis_isolation(cli, cli2):
    """قيمة المخزون ومواد إعادة الطلب في /dashboard/kpis خاصة بكل tenant"""
    await _create_material(cli, "مادة نقص T1", unit_cost=50.0, reorder_level=100)

    r2 = await cli2.get(f"{DASHBOARD}/kpis")
    assert r2.status_code == 200
    kpis2 = r2.json()
    assert kpis2["stock_value"]["value"] == 0
    assert kpis2["low_stock_items"]["value"] == 0

    r1 = await cli.get(f"{DASHBOARD}/kpis")
    kpis1 = r1.json()
    assert kpis1["low_stock_items"]["value"] == 1


@pytest.mark.asyncio
async def test_dashboard_charts_costs_isolation(cli, cli2):
    """رسم توزيع التكاليف — لازم يرجع فاضي لمستأجر ملوش أي CostLine"""
    r2 = await cli2.get(f"{DASHBOARD}/charts/costs")
    assert r2.status_code == 200
    assert r2.json()["values"] in ([], [0.0] * len(r2.json()["values"]))


# ════════════════ /reports/period-comparison — دقّة الربح (COGS الفعلي) ════
# قبل هذا الإصلاح: profit = sales_total - production_cost، وproduction_cost
# كانت مبنية فقط على WorkOrder.actual_cost لأوامر status='done' اللي created_at
# بتاعها يقع في نفس الفترة — يعني (أ) مبيعات عقود التوريد (supply_contracts,
# اللي مالهاش WorkOrder أصلاً) كانت بتدخل في الإيراد من غير أي تكلفة مقابلة
# إطلاقاً (ربح مبالغ فيه بالكامل)، و(ب) توقيت تكلفة أمر الإنتاج (تاريخ الإنشاء)
# ملهوش أي علاقة بتوقيت فاتورة البيع الفعلية. الإصلاح: cogs_actual بيتحسب من
# دفتر الأستاذ الحقيقي (حساب 5100) اللي بيتسجل الآن من كل مسارات البيع.

async def _seed_coa(cli):
    r = await cli.post(f"{ACCTS}/chart/seed")
    assert r.status_code == 200, r.text


async def _make_equipment_pc(cli, name="معدة تقرير الربح"):
    r = await cli.post(EQ, json={"name_ar": name, "level": 1})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _make_material_pc(cli, unit_cost=5.0, qty=100):
    r = await cli.post(f"{INV}/materials", json={
        "name_ar": "مادة تقرير ربح", "unit": "كجم", "unit_cost": unit_cost,
    })
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    r2 = await cli.post(f"{INV}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": qty, "unit_cost": unit_cost,
    })
    assert r2.status_code in (200, 201), r2.text
    return mid


async def _make_customer_pc(cli, name="عميل تقرير ربح"):
    r = await cli.post(CUST, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_period_comparison_cogs_matches_direct_wo_invoice(cli):
    """فوترة أمر إنتاج مباشرة (work-orders/{id}/to-invoice) — الربح المعروض
    لازم يعكس تكلفة البضاعة المباعة الحقيقية (COGS) مش يفترض إنها صفر."""
    await _seed_coa(cli)
    eq_id = await _make_equipment_pc(cli)
    mid = await _make_material_pc(cli, unit_cost=5.0, qty=100)
    cust_id = await _make_customer_pc(cli)

    order_r = await cli.post(f"{CUST}/orders", json={
        "customer_id": cust_id, "equipment_id": eq_id,
        "quantity": 1, "unit_price": 5000.0,
    })
    assert order_r.status_code == 201, order_r.text
    order_id = order_r.json()["id"]

    wo_r = await cli.post(f"{SALES}/orders/{order_id}/to-work-order", json={
        "equipment_id": eq_id, "quantity": 1,
    })
    assert wo_r.status_code == 201, wo_r.text
    wo_id = wo_r.json()["id"]

    issue_r = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 20, "stage": "تجميع",
    })
    assert issue_r.status_code in (200, 201), issue_r.text
    complete_r = await cli.post(f"{PROD}/complete/{wo_id}")
    assert complete_r.status_code in (200, 201), complete_r.text

    inv_r = await cli.post(f"{SALES}/work-orders/{wo_id}/to-invoice", json={})
    assert inv_r.status_code == 201, inv_r.text

    today = date.today().isoformat()
    rep = await cli.get(f"{REPORTS}/period-comparison",
                         params={"date_from": today, "date_to": today})
    assert rep.status_code == 200, rep.text
    cur = rep.json()["current"]

    assert cur["sales_total"] >= 5000.0
    # قبل الإصلاح: cogs_actual مكانتش موجودة أصلاً وكان profit = sales_total - production_cost
    # (production_cost هنا كان بيطابق فعليًا لأن WorkOrder واحد بس، لكن مع عقد
    # توريد بجانبه كان هيبقى صفر رغم وجود تكلفة حقيقية — راجع الاختبار التاني تحت)
    assert cur["cogs_actual"] > 0
    assert cur["profit"] == round(cur["sales_total"] - cur["cogs_actual"], 2)


@pytest.mark.asyncio
async def test_period_comparison_cogs_includes_supply_contract_sales(cli):
    """مبيعات عقد توريد (بدون أي WorkOrder إطلاقاً) — قبل الإصلاح كانت
    production_cost (ومن ثم الربح) بتتجاهلها بالكامل لأنها مش مبنية على
    WorkOrder خالص، فالربح المعروض كان = كامل الإيراد (هامش 100% وهمي)."""
    await _seed_coa(cli)
    cust_id = await _make_customer_pc(cli, "عميل عقد توريد")
    mid = await _make_material_pc(cli, unit_cost=10.0, qty=200)

    contract_r = await cli.post(SC, json={
        "customer_id": cust_id,
        "date": str(date.today()),
        "tax_pct": "0", "discount_amt": "0",
        "lines": [{
            "description": "توريد حديد", "qty": "5", "unit": "طن",
            "unit_price": "1000", "material_id": mid,
        }],
    })
    assert contract_r.status_code == 201, contract_r.text
    contract_id = contract_r.json()["id"]

    act_r = await cli.patch(f"{SC}/{contract_id}/activate")
    assert act_r.status_code == 200, act_r.text
    deliver_r = await cli.post(f"{SC}/{contract_id}/deliver", json={})
    assert deliver_r.status_code == 200, deliver_r.text

    today = date.today().isoformat()
    rep = await cli.get(f"{REPORTS}/period-comparison",
                         params={"date_from": today, "date_to": today})
    assert rep.status_code == 200, rep.text
    cur = rep.json()["current"]

    # قبل الإصلاح: production_cost (المستخدم وقتها للربح) كان بيفضل 0 هنا
    # (مفيش WorkOrder خالص)، فالربح المعروض كان = 5000 (الإيراد كامل) بدل
    # 5000 - 50 (تكلفة المادة الفعلية = 5 طن × 10 = 50).
    assert cur["sales_total"] >= 5000.0
    assert cur["cogs_actual"] == 50.0
    assert cur["profit"] == round(cur["sales_total"] - cur["cogs_actual"], 2)
    assert cur["profit"] < cur["sales_total"]


# ════════════════ Reports hardening — دقة الأرقام وأمان التصدير ═════════════
@pytest.mark.asyncio
async def test_aging_and_receivables_include_overdue_status(cli):
    """installment_scheduler بيكتب Invoice.status='overdue' فعليًا — قبل الإصلاح
    الفاتورة دي كانت بتختفي من الـ aging ومن إجمالي المستحقات."""
    cid = await _make_customer_pc(cli, "عميل متأخر")
    past = date(2020, 1, 1).isoformat()
    r = await cli.post(f"{SALES}/invoices", json={
        "customer_id": cid, "date": past, "due_date": "2020-01-15",
        "lines": [{"description": "بند", "qty": 1, "unit_price": 700}],
    })
    assert r.status_code == 201, r.text
    iid = r.json()["id"]

    s = await cli.post(f"{SALES}/admin/run-installment-scheduler")
    assert s.status_code == 200, s.text

    from app.core.database import AsyncSessionLocal
    from app.models.models import Invoice
    async with AsyncSessionLocal() as db:
        inv = await db.get(Invoice, iid)
        assert inv.status == "overdue"

    aging = (await cli.get(f"{REPORTS}/sales/aging")).json()
    codes = [e["id"] for b in aging["buckets"].values() for e in b]
    assert iid in codes
    assert aging["totals_by_bucket"]["90+"] >= 700

    exe = (await cli.get(f"{REPORTS}/dashboard")).json()
    assert exe["sales"]["total_receivable"] >= 700
    k = (await cli.get(f"{DASHBOARD}/kpis")).json()
    assert k["receivables"]["value"] >= 700

    pr = await cli.get(f"{REPORTS}/sales/aging/print")
    assert pr.status_code == 200
    assert r.json()["code"] in pr.text


@pytest.mark.asyncio
async def test_reports_reject_inverted_date_range(cli):
    for path in ("/sales/summary", "/cashflow", "/period-comparison",
                 "/production/scrap-analysis", "/payments/history"):
        r = await cli.get(f"{REPORTS}{path}",
                          params={"date_from": "2025-02-01", "date_to": "2025-01-01"})
        assert r.status_code == 400, (path, r.text)


@pytest.mark.asyncio
async def test_csv_export_neutralises_formula_injection(cli):
    r = await cli.post(CUST, json={"name": "=HYPERLINK(\"http://evil\",\"x\")"})
    assert r.status_code == 201, r.text
    body = (await cli.get(f"{REPORTS}/export/customers")).content.decode("utf-8-sig")
    lines = body.strip().splitlines()[1:]
    assert any(ln.startswith("\"'=HYPERLINK") or ln.startswith("'=HYPERLINK") for ln in lines), lines
    assert not any(ln.startswith("=") for ln in lines)


@pytest.mark.asyncio
async def test_export_customer_ledger_cross_tenant_blocked(cli, cli2):
    cid = await _make_customer_pc(cli, "عميل كشف T1")
    r_own = await cli.get(f"{REPORTS}/export/customer-ledger/{cid}")
    assert r_own.status_code == 200, r_own.text
    r_other = await cli2.get(f"{REPORTS}/export/customer-ledger/{cid}")
    assert r_other.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_production_chart_six_distinct_months(cli):
    r = await cli.get(f"{DASHBOARD}/charts/production")
    assert r.status_code == 200
    labels = r.json()["labels"]
    assert len(labels) == 6 and len(set(labels)) == 6


@pytest.mark.asyncio
async def test_all_report_and_export_endpoints_respond_with_data(cli):
    """smoke: كل endpoints التقارير/التصدير لازم ترجع 200 وفيه بيانات فعلية
    (export/customers كان بيرمي 500 لأن Customer مالوش email)."""
    await _seed_coa(cli)
    cid = await _make_customer_pc(cli, "عميل smoke")
    await _make_material_pc(cli, unit_cost=3.0, qty=10)
    r = await cli.post(f"{SALES}/invoices", json={
        "customer_id": cid, "date": date.today().isoformat(),
        "lines": [{"description": "بند", "qty": 2, "unit_price": 50}],
    })
    assert r.status_code == 201, r.text
    paths = [
        "/inventory/value", "/inventory/movement", "/inventory/slow-moving",
        "/inventory/summary", "/inventory/transactions", "/inventory/reconciliation",
        "/production/summary", "/production/scrap-analysis", "/profitability",
        "/sales/summary", "/sales/aging", "/sales/installments/upcoming",
        "/sales/installments/overdue", "/sales/installments/all",
        "/sales/installments/badge", "/sales/by-customer", "/purchases/summary",
        "/purchases/by-supplier", "/workers/summary", "/labor-performance",
        "/period-comparison", "/dashboard", "/cashflow", "/analytics/monthly-sales",
        "/analytics/production-by-type", "/payments/history", "/sales/aging/print",
        "/export/materials", "/export/invoices", "/export/customers",
        "/export/work-orders", "/export/quotations", "/export/profitability",
        "/export/cashflow", "/export/labor-performance", "/export/period-comparison",
        f"/export/customer-ledger/{cid}",
    ]
    for p in paths:
        resp = await cli.get(f"{REPORTS}{p}")
        assert resp.status_code == 200, (p, resp.status_code, resp.text[:300])
    for p in ("/full", "/kpis", "/alerts", "/charts/production", "/charts/cashflow", "/charts/costs"):
        resp = await cli.get(f"{DASHBOARD}{p}")
        assert resp.status_code == 200, (p, resp.text[:300])


@pytest.mark.asyncio
async def test_cashflow_excludes_bounced_cheques_and_matches_treasury_balance(cli):
    """الشيك المرتد بيتعكس أثره على رصيد الخزنة — تقرير التدفق النقدي لازم
    يستبعده عشان closing_balance يطابق رصيد الخزنة الفعلي."""
    T = "/api/v1/treasury"
    r = await cli.post(f"{T}/accounts", json={"name": "خزنة تقرير", "kind": "cash",
                                               "opening_balance": 1000})
    assert r.status_code == 201, r.text
    tr_id = r.json()["id"]
    today = date.today().isoformat()
    ok = await cli.post(f"{T}/transactions", json={
        "treasury_id": tr_id, "direction": "in", "amount": 200, "txn_date": today})
    assert ok.status_code == 201, ok.text
    chq = await cli.post(f"{T}/transactions", json={
        "treasury_id": tr_id, "direction": "in", "amount": 500, "txn_date": today,
        "payment_method": "cheque", "cheque_number": "123", "cheque_date": today})
    assert chq.status_code == 201, chq.text
    b = await cli.patch(f"{T}/cheques/{chq.json()['id']}/status", json={"cheque_status": "bounced"})
    assert b.status_code == 200, b.text

    rep = (await cli.get(f"{REPORTS}/cashflow",
                         params={"date_from": today, "date_to": today, "treasury_id": tr_id})).json()
    assert rep["total_in"] == 200.0
    assert rep["opening_balance"] == 1000.0
    assert rep["closing_balance"] == 1200.0
    bal = [t for t in rep["treasuries"] if t["id"] == tr_id][0]["balance"]
    assert rep["closing_balance"] == bal

    pc = (await cli.get(f"{REPORTS}/period-comparison",
                        params={"date_from": today, "date_to": today})).json()
    assert pc["current"]["cashflow_in"] == 200.0
