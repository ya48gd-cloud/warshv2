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


# ════════════════ TENANT OWNERSHIP ════════════════════════════════════
@pytest.mark.asyncio
async def test_record_attendance_rejects_foreign_worker(cli, cli2):
    """Regression: record_attendance never checked that worker_id belongs to
    the caller's own tenant — a worker id from another tenant was silently
    accepted and an attendance row created for it."""
    wid_other = await _make_worker(cli2, "عامل مستأجر آخر")
    r = await cli.post(BASE, json={
        "worker_id": wid_other,
        "attendance_date": str(date.today()),
        "status": "present",
    })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_bulk_attendance_rejects_foreign_worker(cli, cli2):
    """Same tenant-ownership gap as above, for the bulk endpoint."""
    wid_other = await _make_worker(cli2, "عامل مستأجر آخر 2")
    r = await cli.post(f"{BASE}/bulk", json={
        "attendance_date": str(date.today()),
        "records": [{"worker_id": wid_other, "status": "present"}],
    })
    assert r.status_code == 404


# ════════════════ HR HARDENING: VALIDATION / PAID-PAYROLL LOCK ═════════
@pytest.mark.asyncio
async def test_attendance_rejects_invalid_status_and_overtime(cli):
    wid = await _make_worker(cli, "عامل تحقق")
    d = str(date.today())
    r = await cli.post(BASE, json={"worker_id": wid, "attendance_date": d, "status": "banana"})
    assert r.status_code == 400
    r = await cli.post(BASE, json={"worker_id": wid, "attendance_date": d,
                                   "status": "present", "overtime_hours": 30})
    assert r.status_code == 400
    r = await cli.post(f"{BASE}/bulk", json={"work_date": d,
                                             "records": [{"worker_id": wid, "status": "xx"}]})
    assert r.status_code == 400
    r = await cli.post(f"{BASE}/bulk", json={"work_date": d, "records": [
        {"worker_id": wid, "status": "present"}, {"worker_id": wid, "status": "absent"}]})
    assert r.status_code == 400
    # worker_id كنص رقمي يتقبل
    r = await cli.post(f"{BASE}/bulk", json={"work_date": d,
                                             "records": [{"worker_id": str(wid), "status": "half"}]})
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_worker_monthly_rejects_bad_month(cli):
    wid = await _make_worker(cli, "عامل شهر")
    r = await cli.get(f"{BASE}/worker/{wid}", params={"month": 13, "year": 2025})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_attendance_locked_inside_paid_payroll(cli):
    wid = await _make_worker(cli, "عامل كشف مصروف")
    ws = date(2020, 1, 4)
    we = ws + timedelta(days=6)
    r = await cli.post(BASE, json={"worker_id": wid, "attendance_date": str(ws), "status": "present"})
    assert r.status_code == 201
    att_id = r.json()["id"]
    rp = await cli.post(WORKERS + "/payroll", json={"week_start": str(ws), "week_end": str(we)})
    assert rp.status_code == 201, rp.text
    run_id = rp.json()["id"]
    assert (await cli.post(f"{WORKERS}/payroll/{run_id}/generate")).status_code == 200
    rpay = await cli.post(f"{WORKERS}/payroll/{run_id}/pay")
    assert rpay.status_code == 200, rpay.text

    r = await cli.post(BASE, json={"worker_id": wid, "attendance_date": str(ws), "status": "absent"})
    assert r.status_code == 409
    r = await cli.post(f"{BASE}/bulk", json={"work_date": str(ws + timedelta(days=1)),
                                             "records": [{"worker_id": wid, "status": "present"}]})
    assert r.status_code == 409
    assert (await cli.delete(f"{BASE}/{att_id}")).status_code == 409
    # يوم خارج الكشف مازال مسموح
    r = await cli.post(BASE, json={"worker_id": wid, "attendance_date": str(we + timedelta(days=1)),
                                   "status": "present"})
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_viewer_cannot_write_attendance(cli):
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.api.auth import create_token
    wid = await _make_worker(cli, "عامل مشاهد")
    tok = create_token(user_id=992, username="viewer_att", role="viewer", tenant_id=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {tok}"}) as vcli:
        r = await vcli.post(BASE, json={"worker_id": wid, "attendance_date": str(date.today()),
                                        "status": "present"})
        assert r.status_code == 403
        assert (await vcli.get(BASE, params={"date": str(date.today())})).status_code == 200
