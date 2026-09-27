"""
test_13_field_service.py — اختبارات خدمة ما بعد البيع
أصول · طلبات خدمة · تعيين · زيارات · عمالة · لوحة · تقويم
"""
import pytest
from datetime import date

BASE = "/api/v1/field-service"
CUST = "/api/v1/customers"
WORKERS = "/api/v1/workers"
EQ = "/api/v1/equipment"
INV = "/api/v1/inventory"


async def _make_customer(cli):
    r = await cli.post(CUST, json={"name": "عميل خدمة"})
    return r.json()["id"]


async def _make_worker(cli):
    r = await cli.post(WORKERS, json={"name": "فني خدمة", "daily_wage": 130.0})
    return r.json()["id"]


async def _make_equipment(cli):
    r = await cli.post(EQ, json={"name_ar": "معدة خدمة", "level": 1})
    return r.json()["id"]


async def _make_material(cli):
    r = await cli.post(f"{INV}/materials", json={"name_ar": "أداة خدمة", "unit": "قطعة", "unit_cost": 10.0})
    return r.json()["id"]


async def _make_asset(cli, cid, eq_id=None):
    payload = {"customer_id": cid, "name": "أصل عميل", "model": "موديل 1"}
    if eq_id:
        payload["equipment_id"] = eq_id
    r = await cli.post(f"{BASE}/assets", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_order(cli, cid, asset_id=None):
    payload = {
        "code": "FSO-001",
        "customer_id": cid,
        "order_type": "maintenance",
        "title": "صيانة دورية",
        "priority": "normal",
        "planned_date": str(date.today()),
    }
    if asset_id:
        payload["asset_id"] = asset_id
    r = await cli.post(f"{BASE}/orders", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ════════════════ ASSETS ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_asset(cli):
    cid = await _make_customer(cli)
    r = await cli.post(f"{BASE}/assets", json={"customer_id": cid, "name": "أصل 1"})
    assert r.status_code == 201
    assert r.json()["customer_id"] == cid


@pytest.mark.asyncio
async def test_list_assets(cli):
    cid = await _make_customer(cli)
    await _make_asset(cli, cid)
    r = await cli.get(f"{BASE}/assets")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_list_assets_by_customer(cli):
    cid = await _make_customer(cli)
    await _make_asset(cli, cid)
    r = await cli.get(f"{BASE}/assets", params={"customer_id": cid})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_get_asset(cli):
    cid = await _make_customer(cli)
    aid = await _make_asset(cli, cid)
    r = await cli.get(f"{BASE}/assets/{aid}")
    assert r.status_code == 200
    assert r.json()["id"] == aid


@pytest.mark.asyncio
async def test_update_asset(cli):
    cid = await _make_customer(cli)
    aid = await _make_asset(cli, cid)
    r = await cli.put(f"{BASE}/assets/{aid}", json={
        "customer_id": cid, "name": "أصل محدّث", "model": "موديل 2",
    })
    assert r.status_code == 200
    assert r.json()["name"] == "أصل محدّث"


@pytest.mark.asyncio
async def test_delete_asset(cli):
    cid = await _make_customer(cli)
    aid = await _make_asset(cli, cid)
    r = await cli.delete(f"{BASE}/assets/{aid}")
    assert r.status_code in (200, 204)


# ════════════════ SERVICE ORDERS ══════════════════════════════════════
@pytest.mark.asyncio
async def test_create_service_order(cli):
    cid = await _make_customer(cli)
    oid = await _make_order(cli, cid)
    assert oid > 0


@pytest.mark.asyncio
async def test_list_service_orders(cli):
    cid = await _make_customer(cli)
    await _make_order(cli, cid)
    r = await cli.get(f"{BASE}/orders")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_get_service_order(cli):
    cid = await _make_customer(cli)
    oid = await _make_order(cli, cid)
    r = await cli.get(f"{BASE}/orders/{oid}")
    assert r.status_code == 200
    assert r.json()["id"] == oid


@pytest.mark.asyncio
async def test_update_service_order(cli):
    cid = await _make_customer(cli)
    oid = await _make_order(cli, cid)
    r = await cli.put(f"{BASE}/orders/{oid}", json={
        "code": "FSO-001", "customer_id": cid,
        "order_type": "repair", "title": "إصلاح طارئ", "priority": "urgent",
    })
    assert r.status_code == 200
    assert r.json()["order_type"] == "repair"


@pytest.mark.asyncio
async def test_update_order_status(cli):
    cid = await _make_customer(cli)
    oid = await _make_order(cli, cid)
    r = await cli.patch(f"{BASE}/orders/{oid}/status", json={"status": "in_progress"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_delete_service_order(cli):
    cid = await _make_customer(cli)
    oid = await _make_order(cli, cid)
    r = await cli.delete(f"{BASE}/orders/{oid}")
    assert r.status_code in (200, 204)


# ════════════════ ASSIGNMENTS ════════════════════════════════════════
@pytest.mark.asyncio
async def test_assign_workers(cli):
    cid = await _make_customer(cli)
    oid = await _make_order(cli, cid)
    wid = await _make_worker(cli)
    r = await cli.post(f"{BASE}/orders/{oid}/assign", json={
        "worker_ids": [wid], "role": "technician",
    })
    assert r.status_code in (200, 201)


@pytest.mark.asyncio
async def test_remove_assignment(cli):
    cid = await _make_customer(cli)
    oid = await _make_order(cli, cid)
    wid = await _make_worker(cli)
    r = await cli.post(f"{BASE}/orders/{oid}/assign", json={"worker_ids": [wid], "role": "helper"})
    assignments = r.json() if isinstance(r.json(), list) else [r.json()]
    if assignments and "id" in assignments[0]:
        asgn_id = assignments[0]["id"]
        r2 = await cli.delete(f"{BASE}/orders/{oid}/assign/{asgn_id}")
        assert r2.status_code in (200, 204)


# ════════════════ VISITS ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_record_visit(cli):
    cid = await _make_customer(cli)
    oid = await _make_order(cli, cid)
    r = await cli.post(f"{BASE}/orders/{oid}/visit", json={
        "visit_date": str(date.today()),
        "labor_hours": 3.5,
        "work_done": "تم الصيانة",
        "status": "completed",
    })
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_list_visits(cli):
    cid = await _make_customer(cli)
    oid = await _make_order(cli, cid)
    await cli.post(f"{BASE}/orders/{oid}/visit", json={
        "visit_date": str(date.today()), "status": "completed",
    })
    r = await cli.get(f"{BASE}/orders/{oid}/visits")
    assert r.status_code == 200
    assert len(r.json()) >= 1


# ════════════════ LABOR ═════════════════════════════════════════════
@pytest.mark.asyncio
async def test_add_labor(cli):
    cid = await _make_customer(cli)
    oid = await _make_order(cli, cid)
    wid = await _make_worker(cli)
    r = await cli.post(f"{BASE}/orders/{oid}/labor", json={
        "worker_id": wid,
        "entry_date": str(date.today()),
        "days": 1,
        "daily_wage": 200.0,
        "allowance": 0,
    })
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_list_labor(cli):
    cid = await _make_customer(cli)
    oid = await _make_order(cli, cid)
    r = await cli.get(f"{BASE}/orders/{oid}/labor")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ════════════════ TOOL WITHDRAWALS ═══════════════════════════════════
@pytest.mark.asyncio
async def test_withdraw_and_return_tool(cli):
    cid = await _make_customer(cli)
    oid = await _make_order(cli, cid)
    mid = await _make_material(cli)
    r = await cli.post(f"{BASE}/orders/{oid}/tools", json={
        "material_id": mid, "qty": 2, "withdrawn_date": str(date.today()),
    })
    assert r.status_code == 201
    wid = r.json()["id"]

    r2 = await cli.patch(f"{BASE}/orders/{oid}/tools/{wid}/return", json={
        "returned_date": str(date.today()), "returned_qty": 2, "damaged_qty": 0,
    })
    assert r2.status_code == 200
    assert r2.json()["status"] == "returned"


@pytest.mark.asyncio
async def test_return_tool_rejects_qty_over_withdrawn(cli):
    """Regression: return_tool never validated returned_qty (+ damaged_qty)
    against the originally withdrawn qty, so more tooling than was ever
    checked out could be recorded as returned."""
    cid = await _make_customer(cli)
    oid = await _make_order(cli, cid)
    mid = await _make_material(cli)
    r = await cli.post(f"{BASE}/orders/{oid}/tools", json={
        "material_id": mid, "qty": 1, "withdrawn_date": str(date.today()),
    })
    wid = r.json()["id"]

    r2 = await cli.patch(f"{BASE}/orders/{oid}/tools/{wid}/return", json={
        "returned_date": str(date.today()), "returned_qty": 5, "damaged_qty": 0,
    })
    assert r2.status_code == 400


# ════════════════ DASHBOARD + CALENDAR ═══════════════════════════════
@pytest.mark.asyncio
async def test_field_service_dashboard(cli):
    r = await cli.get(f"{BASE}/dashboard")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_service_calendar(cli):
    r = await cli.get(f"{BASE}/calendar")
    assert r.status_code == 200
