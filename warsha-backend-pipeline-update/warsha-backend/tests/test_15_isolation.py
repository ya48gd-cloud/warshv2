"""
test_15_isolation.py — اختبارات عزل المستأجرين (Multi-Tenant)
التأكد أن بيانات tenant 1 غير مرئية لـ tenant 2 والعكس
"""
import pytest

CUST = "/api/v1/customers"
INV = "/api/v1/inventory"
WORKERS = "/api/v1/workers"
TREAS = "/api/v1/treasury"
EQ = "/api/v1/equipment"
PURCH = "/api/v1/purchases"
SALES = "/api/v1/sales"


# ════════════════ CUSTOMERS ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_customer_tenant_isolation(cli, cli2):
    # t1 ينشئ عميل
    r1 = await cli.post(CUST, json={"name": "عميل T1"})
    assert r1.status_code == 201
    cid = r1.json()["id"]

    # t2 لا يرى العميل في القائمة
    r2 = await cli2.get(CUST)
    ids = [c["id"] for c in r2.json()]
    assert cid not in ids


@pytest.mark.asyncio
async def test_customer_cross_tenant_get(cli, cli2):
    r1 = await cli.post(CUST, json={"name": "عميل مخفي"})
    cid = r1.json()["id"]

    r2 = await cli2.get(f"{CUST}/{cid}")
    assert r2.status_code == 404


# ════════════════ INVENTORY ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_material_tenant_isolation(cli, cli2):
    r1 = await cli.post(f"{INV}/materials", json={"name_ar": "مادة T1", "unit": "كجم", "unit_cost": 5.0})
    assert r1.status_code == 201
    mid = r1.json()["id"]

    r2 = await cli2.get(f"{INV}/materials")
    ids = [m["id"] for m in r2.json()]
    assert mid not in ids


@pytest.mark.asyncio
async def test_material_cross_tenant_get(cli, cli2):
    r1 = await cli.post(f"{INV}/materials", json={"name_ar": "مادة مخفية", "unit": "كجم", "unit_cost": 5.0})
    mid = r1.json()["id"]
    r2 = await cli2.get(f"{INV}/materials/{mid}")
    assert r2.status_code == 404


# ════════════════ WORKERS ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_worker_tenant_isolation(cli, cli2):
    r1 = await cli.post(WORKERS, json={"name": "موظف T1", "daily_wage": 100.0})
    assert r1.status_code == 201
    wid = r1.json()["id"]

    r2 = await cli2.get(WORKERS)
    ids = [w["id"] for w in r2.json()]
    assert wid not in ids


# ════════════════ TREASURY ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_treasury_tenant_isolation(cli, cli2):
    r1 = await cli.post(f"{TREAS}/accounts", json={"name": "خزينة T1", "kind": "cash"})
    assert r1.status_code == 201
    tid = r1.json()["id"]

    r2 = await cli2.get(f"{TREAS}/accounts")
    ids = [a["id"] for a in r2.json()]
    assert tid not in ids


# ════════════════ EQUIPMENT ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_equipment_tenant_isolation(cli, cli2):
    r1 = await cli.post(EQ, json={"name_ar": "معدة T1", "level": 1})
    assert r1.status_code == 201
    eid = r1.json()["id"]

    r2 = await cli2.get(f"{EQ}/list-all")
    ids = [e["id"] for e in r2.json()]
    assert eid not in ids


# ════════════════ SUPPLIERS ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_supplier_tenant_isolation(cli, cli2):
    r1 = await cli.post(f"{PURCH}/suppliers", json={"name": "مورد T1", "supplier_type": "local"})
    assert r1.status_code == 201
    sid = r1.json()["id"]

    r2 = await cli2.get(f"{PURCH}/suppliers")
    ids = [s["id"] for s in r2.json()]
    assert sid not in ids


# ════════════════ SALES INVOICES ════════════════════════════════════
@pytest.mark.asyncio
async def test_invoice_tenant_isolation(cli, cli2):
    # t1 ينشئ عميل وفاتورة
    rc = await cli.post(CUST, json={"name": "عميل فاتورة T1"})
    cid = rc.json()["id"]
    from datetime import date
    ri = await cli.post(f"{SALES}/invoices", json={
        "code": "ISO-INV-001",
        "customer_id": cid,
        "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 100.0}],
    })
    assert ri.status_code == 201
    inv_id = ri.json()["id"]

    # t2 لا يرى الفاتورة
    r2 = await cli2.get(f"{SALES}/invoices")
    ids = [i["id"] for i in r2.json()]
    assert inv_id not in ids


# ════════════════ CROSS-TENANT DATA CANNOT INTERFERE ════════════════
@pytest.mark.asyncio
async def test_two_tenants_same_operation_no_clash(cli, cli2):
    """كلا المستأجرَين ينشئان عميلاً بنفس الاسم — لا تعارض"""
    r1 = await cli.post(CUST, json={"name": "مشترك"})
    r2 = await cli2.post(CUST, json={"name": "مشترك"})
    assert r1.status_code == 201
    assert r2.status_code == 201
    # IDs are different (even if names match)
    assert r1.json()["id"] != r2.json()["id"]
