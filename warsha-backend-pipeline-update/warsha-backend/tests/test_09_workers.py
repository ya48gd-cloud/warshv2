"""
test_09_workers.py — اختبارات الموظفين
موظفون · سلف · مسير الرواتب · توليد بنود من الحضور
"""
import pytest
from datetime import date, timedelta

BASE = "/api/v1/workers"


def _worker(name="أحمد محمد"):
    return {"name": name, "daily_wage": 150.0, "job_title": "فني", "phone": "0501234567"}


# ════════════════ WORKERS ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_worker(cli):
    r = await cli.post(BASE, json=_worker())
    assert r.status_code == 201
    d = r.json()
    assert d["name"] == "أحمد محمد"
    assert d["daily_wage"] == 150.0


@pytest.mark.asyncio
async def test_list_workers(cli):
    await cli.post(BASE, json=_worker("محمد علي"))
    r = await cli.get(BASE)
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_update_worker(cli):
    r = await cli.post(BASE, json=_worker())
    wid = r.json()["id"]
    r2 = await cli.put(f"{BASE}/{wid}", json=_worker("خالد إبراهيم"))
    assert r2.status_code == 200
    assert r2.json()["name"] == "خالد إبراهيم"


@pytest.mark.asyncio
async def test_deactivate_worker(cli):
    r = await cli.post(BASE, json=_worker("للتعطيل"))
    wid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/{wid}")
    assert r2.status_code in (200, 204)


@pytest.mark.asyncio
async def test_worker_next_code(cli):
    r = await cli.get(f"{BASE}/next-code")
    assert r.status_code == 200
    assert "code" in r.json()


@pytest.mark.asyncio
async def test_worker_profile(cli):
    r = await cli.post(BASE, json=_worker())
    wid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/{wid}/profile")
    assert r2.status_code == 200


# ════════════════ ADVANCES ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_add_advance(cli):
    r = await cli.post(BASE, json=_worker())
    wid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/advances", json={
        "worker_id": wid,
        "amount": 300.0,
        "advance_type": "advance",
        "date": str(date.today()),
        "notes": "سلفة عاجلة",
    })
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_delete_advance(cli):
    r = await cli.post(BASE, json=_worker())
    wid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/advances", json={
        "worker_id": wid, "amount": 100.0,
        "advance_type": "advance", "date": str(date.today()),
    })
    aid = r2.json()["id"]
    r3 = await cli.delete(f"{BASE}/advances/{aid}")
    assert r3.status_code in (200, 204)


# ════════════════ PAYROLL ═══════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_payroll_run(cli):
    week_start = date.today() - timedelta(days=date.today().weekday())
    week_end = week_start + timedelta(days=6)
    r = await cli.post(f"{BASE}/payroll", json={
        "week_start": str(week_start),
        "week_end": str(week_end),
        "notes": "أسبوع اختبار",
    })
    assert r.status_code == 201
    d = r.json()
    assert "id" in d


@pytest.mark.asyncio
async def test_list_payroll(cli):
    r = await cli.get(f"{BASE}/payroll")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_generate_payroll_from_attendance(cli):
    # Create worker + attendance + payroll run
    rw = await cli.post(BASE, json=_worker("عامل مسير"))
    wid = rw.json()["id"]

    week_start = date.today() - timedelta(days=date.today().weekday())
    week_end = week_start + timedelta(days=6)

    # Record attendance for this worker
    await cli.post("/api/v1/attendance", json={
        "worker_id": wid,
        "attendance_date": str(week_start),
        "status": "present",
    })

    # Create payroll run
    rp = await cli.post(f"{BASE}/payroll", json={
        "week_start": str(week_start), "week_end": str(week_end),
    })
    run_id = rp.json()["id"]

    # Generate lines from attendance
    r = await cli.post(f"{BASE}/payroll/{run_id}/generate")
    assert r.status_code in (200, 201)


@pytest.mark.asyncio
async def test_get_payroll_detail(cli):
    week_start = date.today() - timedelta(days=date.today().weekday() + 7)
    week_end = week_start + timedelta(days=6)
    rp = await cli.post(f"{BASE}/payroll", json={
        "week_start": str(week_start), "week_end": str(week_end),
    })
    run_id = rp.json()["id"]
    r = await cli.get(f"{BASE}/payroll/{run_id}")
    assert r.status_code == 200
    assert r.json()["id"] == run_id


@pytest.mark.asyncio
async def test_delete_payroll_run(cli):
    week_start = date(2025, 1, 6)
    week_end = date(2025, 1, 12)
    rp = await cli.post(f"{BASE}/payroll", json={
        "week_start": str(week_start), "week_end": str(week_end),
    })
    run_id = rp.json()["id"]
    r = await cli.delete(f"{BASE}/payroll/{run_id}")
    assert r.status_code in (200, 204)
