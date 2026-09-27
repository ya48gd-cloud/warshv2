"""
test_16_reports_dashboard_isolation.py — اختبارات عزل المستأجرين للتقارير والداشبورد

CRITICAL REGRESSION TEST: قبل هذا الإصلاح كانت كل استعلامات app/api/reports.py
(~50 endpoint) و app/api/dashboard_new.py بدون أي فلتر tenant_id — يعني أي
مستخدم مسجّل دخول كان بيشوف أرقام/بيانات كل الورش (المستأجرين) مجمّعة مع بعض
بدل بيانات ورشته فقط. راجع تقرير الأمان لتفاصيل الإصلاح.

هذا الملف يتأكد إن التقارير والداشبورد بترجع بيانات tenant الحالي فقط.
"""
import pytest

INV = "/api/v1/inventory"
REPORTS = "/api/v1/reports"
DASHBOARD = "/api/v1/dashboard"


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
