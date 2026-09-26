"""
test_12_external_projects.py — اختبارات المشاريع الخارجية
مشاريع · فريق · حضور · أدوات · مواد · رحلات · مصاريف · تقدم · دفعات
"""
import pytest
from datetime import date

BASE = "/api/v1/ext-projects"
CUST = "/api/v1/customers"
WORKERS = "/api/v1/workers"
INV = "/api/v1/inventory"


async def _make_client(cli):
    r = await cli.post(CUST, json={"name": "عميل مشروع خارجي"})
    return r.json()["id"]


async def _make_worker(cli):
    r = await cli.post(WORKERS, json={"name": "عامل مشروع", "daily_wage": 120.0})
    return r.json()["id"]


async def _make_material(cli):
    r = await cli.post(f"{INV}/materials", json={"name_ar": "مادة مشروع", "unit": "كجم", "unit_cost": 5.0})
    return r.json()["id"]


async def _create_project(cli, client_id):
    r = await cli.post(BASE, json={
        "code": "EP-001",
        "title": "مشروع تركيب",
        "project_type": "installation",
        "client_id": client_id,
        "contract_value": 50000.0,
        "planned_start": str(date.today()),
        "planned_end": str(date.today()),
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ════════════════ PROJECT CRUD ════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_project(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    assert pid > 0


@pytest.mark.asyncio
async def test_list_projects(cli):
    cid = await _make_client(cli)
    await _create_project(cli, cid)
    r = await cli.get(BASE)
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_get_project(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    r = await cli.get(f"{BASE}/{pid}")
    assert r.status_code == 200
    assert r.json()["id"] == pid


@pytest.mark.asyncio
async def test_update_project(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    r = await cli.put(f"{BASE}/{pid}", json={
        "code": "EP-001", "title": "مشروع محدَّث",
        "project_type": "installation", "client_id": cid,
        "contract_value": 60000.0,
    })
    assert r.status_code == 200
    g = await cli.get(f"{BASE}/{pid}")
    assert g.json()["title"] == "مشروع محدَّث"


@pytest.mark.asyncio
async def test_update_project_status(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    r = await cli.patch(f"{BASE}/{pid}/status", params={"status": "in_progress"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_delete_project(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    r = await cli.delete(f"{BASE}/{pid}")
    assert r.status_code in (200, 204)


@pytest.mark.asyncio
async def test_project_dashboard(cli):
    r = await cli.get(f"{BASE}/dashboard")
    assert r.status_code == 200


# ════════════════ TEAM ════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_add_team_member(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    wid = await _make_worker(cli)
    r = await cli.post(f"{BASE}/{pid}/team", json={
        "worker_id": wid, "role": "technician", "daily_rate": 150.0,
    })
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_remove_team_member(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    wid = await _make_worker(cli)
    r = await cli.post(f"{BASE}/{pid}/team", json={"worker_id": wid, "role": "helper", "daily_rate": 100.0})
    mid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/{pid}/team/{mid}")
    assert r2.status_code in (200, 204)


# ════════════════ ATTENDANCE ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_project_attendance(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    wid = await _make_worker(cli)
    await cli.post(f"{BASE}/{pid}/team", json={"worker_id": wid, "role": "technician", "daily_rate": 120.0})
    r = await cli.post(f"{BASE}/{pid}/attendance", json={
        "work_date": str(date.today()),
        "records": [{"worker_id": wid, "status": "present"}],
    })
    assert r.status_code in (200, 201)


# ════════════════ TOOLS ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_add_tool(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    r = await cli.post(f"{BASE}/{pid}/tools", json={
        "tool_name": "حفارة", "qty": 2.0, "unit": "قطعة",
        "issue_date": str(date.today()),
    })
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_delete_tool(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    r = await cli.post(f"{BASE}/{pid}/tools", json={
        "tool_name": "مطرقة", "qty": 1.0, "unit": "قطعة",
        "issue_date": str(date.today()),
    })
    tid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/{pid}/tools/{tid}")
    assert r2.status_code in (200, 204)


# ════════════════ MATERIALS ═══════════════════════════════════════════
@pytest.mark.asyncio
async def test_add_material(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    mat_id = await _make_material(cli)
    r = await cli.post(f"{BASE}/{pid}/materials", json={
        "description": "مادة مشروع", "quantity": 50.0, "unit_cost": 10.0,
        "usage_date": str(date.today()),
    })
    assert r.status_code == 201


# ════════════════ TRIPS ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_add_trip(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    wid = await _make_worker(cli)
    r = await cli.post(f"{BASE}/{pid}/trips", json={
        "trip_date": str(date.today()),
        "worker_id": wid,
        "distance_km": 120.0,
        "cost": 200.0,
        "purpose": "زيارة ميدانية",
    })
    assert r.status_code == 201


# ════════════════ EXPENSES ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_add_expense(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    r = await cli.post(f"{BASE}/{pid}/expenses", json={
        "expense_date": str(date.today()),
        "category": "مصاريف عامة",
        "amount": 500.0,
        "description": "وجبات الفريق",
    })
    assert r.status_code == 201


# ════════════════ PROGRESS ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_add_progress(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    r = await cli.post(f"{BASE}/{pid}/progress", json={
        "report_date": str(date.today()),
        "completion_pct": 30,
        "work_done": "اكتملت الأساسات",
    })
    assert r.status_code == 201


# ════════════════ PAYMENTS ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_add_payment_milestone(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    r = await cli.post(f"{BASE}/{pid}/payments", json={
        "milestone": "دفعة أولى",
        "due_amount": 10000.0,
        "due_date": str(date.today()),
    })
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_receive_payment(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    r = await cli.post(f"{BASE}/{pid}/payments", json={
        "milestone": "دفعة", "due_amount": 5000.0, "due_date": str(date.today()),
    })
    payid = r.json()["id"]
    r2 = await cli.patch(f"{BASE}/{pid}/payments/{payid}/receive", json={
        "paid_date": str(date.today()),
        "paid_amount": 5000.0,
    })
    assert r2.status_code == 200


# ════════════════ COST SUMMARY ════════════════════════════════════════
@pytest.mark.asyncio
async def test_cost_summary(cli):
    cid = await _make_client(cli)
    pid = await _create_project(cli, cid)
    r = await cli.get(f"{BASE}/{pid}/cost-summary")
    assert r.status_code == 200
