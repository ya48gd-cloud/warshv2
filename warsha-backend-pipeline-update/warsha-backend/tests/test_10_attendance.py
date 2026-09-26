"""
test_10_attendance.py — اختبارات الحضور
تسجيل فردي · جماعي · ملخص أسبوعي · عرض أسبوعي · حذف
"""
import pytest
from datetime import date, timedelta

BASE = "/api/v1/attendance"
WORKERS = "/api/v1/workers"


async def _make_worker(cli, name="عامل حضور"):
    r = await cli.post(WORKERS, json={"name": name, "daily_wage": 100.0})
    return r.json()["id"]


# ════════════════ SINGLE ATTENDANCE ══════════════════════════════════
@pytest.mark.asyncio
async def test_record_attendance_present(cli):
    wid = await _make_worker(cli)
    r = await cli.post(BASE, json={
        "worker_id": wid,
        "attendance_date": str(date.today()),
        "status": "present",
    })
    assert r.status_code == 201
    d = r.json()
    assert d["worker_id"] == wid
    assert d["status"] == "present"


@pytest.mark.asyncio
async def test_record_attendance_absent(cli):
    wid = await _make_worker(cli)
    r = await cli.post(BASE, json={
        "worker_id": wid,
        "attendance_date": str(date.today() - timedelta(days=1)),
        "status": "absent",
    })
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_record_attendance_with_overtime(cli):
    wid = await _make_worker(cli)
    r = await cli.post(BASE, json={
        "worker_id": wid,
        "attendance_date": str(date.today() - timedelta(days=2)),
        "status": "present",
        "overtime_hours": 2.5,
    })
    assert r.status_code == 201
    assert float(r.json()["overtime_hours"]) == 2.5


# ════════════════ LIST ATTENDANCE BY DATE ════════════════════════════
@pytest.mark.asyncio
async def test_list_attendance_by_date(cli):
    wid = await _make_worker(cli)
    target_date = str(date.today() - timedelta(days=3))
    await cli.post(BASE, json={"worker_id": wid, "attendance_date": target_date, "status": "present"})
    r = await cli.get(BASE, params={"date": target_date})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_list_all_attendance(cli):
    r = await cli.get(BASE, params={"date": str(date.today())})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ════════════════ BULK ATTENDANCE ════════════════════════════════════
@pytest.mark.asyncio
async def test_bulk_attendance(cli):
    w1 = await _make_worker(cli, "عامل جماعي 1")
    w2 = await _make_worker(cli, "عامل جماعي 2")
    r = await cli.post(f"{BASE}/bulk", json={
        "attendance_date": str(date.today() - timedelta(days=4)),
        "records": [
            {"worker_id": w1, "status": "present"},
            {"worker_id": w2, "status": "absent"},
        ],
    })
    assert r.status_code == 201
    d = r.json()
    assert isinstance(d, list) or isinstance(d, dict)


# ════════════════ SUMMARY ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_attendance_summary(cli):
    week_start = date.today() - timedelta(days=date.today().weekday())
    week_str = str(week_start)
    r = await cli.get(f"{BASE}/summary", params={"week": week_str})
    assert r.status_code == 200


# ════════════════ WEEK VIEW ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_week_attendance(cli):
    week_start = date.today() - timedelta(days=date.today().weekday())
    r = await cli.get(f"{BASE}/week", params={"week_start": str(week_start)})
    assert r.status_code == 200


# ════════════════ WORKER ATTENDANCE ══════════════════════════════════
@pytest.mark.asyncio
async def test_worker_attendance_monthly(cli):
    wid = await _make_worker(cli)
    r = await cli.get(f"{BASE}/worker/{wid}",
                      params={"month": date.today().month, "year": date.today().year})
    assert r.status_code == 200


# ════════════════ DELETE ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_delete_attendance(cli):
    wid = await _make_worker(cli)
    r = await cli.post(BASE, json={
        "worker_id": wid,
        "attendance_date": str(date.today() - timedelta(days=5)),
        "status": "present",
    })
    att_id = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/{att_id}")
    assert r2.status_code == 204
