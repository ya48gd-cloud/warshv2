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
async def test_generate_payroll_includes_overtime_pay(cli):
    """Regression: overtime_hours recorded on attendance must be paid out in the
    generated payroll line (daily_wage/8 per hour) — it used to be silently
    dropped by generate_from_attendance even though the weekly summary showed it."""
    rw = await cli.post(BASE, json=_worker("عامل إضافي"))
    wid = rw.json()["id"]
    daily_wage = rw.json()["daily_wage"]

    week_start = date.today() - timedelta(days=date.today().weekday())
    week_end = week_start + timedelta(days=6)

    await cli.post("/api/v1/attendance", json={
        "worker_id": wid,
        "attendance_date": str(week_start),
        "status": "present",
        "overtime_hours": 4,
    })

    rp = await cli.post(f"{BASE}/payroll", json={
        "week_start": str(week_start), "week_end": str(week_end),
    })
    run_id = rp.json()["id"]

    r = await cli.post(f"{BASE}/payroll/{run_id}/generate")
    assert r.status_code in (200, 201)
    lines = r.json()["lines"]
    line = next(l for l in lines if l["worker"] == "عامل إضافي")
    expected_overtime_pay = daily_wage * 4 / 8
    assert line["overtime_hours"] == 4
    assert abs(line["overtime_pay"] - expected_overtime_pay) < 0.01
    expected_gross = daily_wage * 1 + expected_overtime_pay  # 1 present day + OT
    assert abs(line["total_gross"] - expected_gross) < 0.01


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


# ════════════════ MONEY-CORRECTNESS REGRESSIONS ═══════════════════════
# سلف/مكافآت العمال وكشف الرواتب: تتبع الحلقة الكاملة (سلفة → خصم في
# الراتب → استرداد فعلي) من غير ازدواج أو ضياع مبلغ.

TREASURY_BASE = "/api/v1/treasury"


async def _make_treasury(cli, opening=1000.0):
    r = await cli.post(f"{TREASURY_BASE}/accounts", json={
        "name": f"خزينة اختبار {opening}", "kind": "cash", "opening_balance": opening,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _treasury_balance(cli, treasury_id):
    r = await cli.get(f"{TREASURY_BASE}/accounts")
    row = next(t for t in r.json() if t["id"] == treasury_id)
    return row["balance"]


@pytest.mark.asyncio
async def test_advance_validation_rejects_bad_input(cli, cli2):
    """Regression: add_advance never validated amount>0 / advance_type, and
    never checked that worker_id actually belongs to the caller's tenant."""
    rw = await cli.post(BASE, json=_worker("للتحقق"))
    wid = rw.json()["id"]

    bad_amount = await cli.post(f"{BASE}/advances", json={
        "worker_id": wid, "amount": 0, "advance_type": "advance", "date": str(date.today()),
    })
    assert bad_amount.status_code == 400

    bad_type = await cli.post(f"{BASE}/advances", json={
        "worker_id": wid, "amount": 100, "advance_type": "not_a_type", "date": str(date.today()),
    })
    assert bad_type.status_code == 400

    rw2 = await cli2.post(BASE, json=_worker("عامل مستأجر آخر"))
    wid2 = rw2.json()["id"]
    cross_tenant = await cli.post(f"{BASE}/advances", json={
        "worker_id": wid2, "amount": 100, "advance_type": "advance", "date": str(date.today()),
    })
    assert cross_tenant.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_worker_code_rejected_cleanly(cli):
    """Regression: an explicit duplicate worker code hit the DB's unique
    constraint directly (unhandled IntegrityError -> 500) instead of a clean
    400 validation error."""
    r1 = await cli.post(BASE, json={**_worker("الأول"), "code": "EMP-DUP-1"})
    assert r1.status_code == 201
    r2 = await cli.post(BASE, json={**_worker("الثاني"), "code": "EMP-DUP-1"})
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_bonus_is_not_paid_twice(cli):
    """Regression: a 'bonus' WorkerAdvance triggered an immediate treasury
    cash-out at creation time AND was paid again through the payroll net
    (its amount is added into gross+bonus) — the same bonus left the
    treasury twice. It must leave the treasury exactly once, at payroll pay
    time, and must never be re-included in a later payroll run."""
    treasury_id = await _make_treasury(cli, opening=1000.0)
    balance_before_bonus = await _treasury_balance(cli, treasury_id)

    rw = await cli.post(BASE, json=_worker("عامل مكافأة"))
    wid = rw.json()["id"]

    week_start = date.today() - timedelta(days=date.today().weekday())
    week_end = week_start + timedelta(days=6)
    await cli.post("/api/v1/attendance", json={
        "worker_id": wid, "attendance_date": str(week_start), "status": "present",
    })

    adv = await cli.post(f"{BASE}/advances", json={
        "worker_id": wid, "amount": 50.0, "advance_type": "bonus", "date": str(week_start),
    })
    assert adv.status_code == 201

    # لسه محصلتش المكافأة نقدًا وقت التسجيل — الخزنة لازم تفضل زي ما هي
    balance_after_bonus = await _treasury_balance(cli, treasury_id)
    assert balance_after_bonus == balance_before_bonus

    rp1 = await cli.post(f"{BASE}/payroll", json={"week_start": str(week_start), "week_end": str(week_end)})
    run1_id = rp1.json()["id"]
    gen1 = await cli.post(f"{BASE}/payroll/{run1_id}/generate")
    line1 = next(l for l in gen1.json()["lines"] if l["worker"] == "عامل مكافأة")
    assert abs(line1["net"] - (150.0 + 50.0)) < 0.01  # يوم حضور واحد + المكافأة

    # أسبوع تاني — نفس المكافأة القديمة لازم متترجعش تتحسب/تتدفع تاني
    week2_start = week_start + timedelta(days=7)
    week2_end = week2_start + timedelta(days=6)
    await cli.post("/api/v1/attendance", json={
        "worker_id": wid, "attendance_date": str(week2_start), "status": "present",
    })
    rp2 = await cli.post(f"{BASE}/payroll", json={"week_start": str(week2_start), "week_end": str(week2_end)})
    run2_id = rp2.json()["id"]
    gen2 = await cli.post(f"{BASE}/payroll/{run2_id}/generate")
    line2 = next(l for l in gen2.json()["lines"] if l["worker"] == "عامل مكافأة")
    assert abs(line2["net"] - 150.0) < 0.01

    profile = await cli.get(f"{BASE}/{wid}/profile")
    assert profile.json()["pending_bonus"] == 0.0


@pytest.mark.asyncio
async def test_advance_partial_recovery_not_written_off(cli):
    """Regression: generate_from_attendance capped the *deduction* so net pay
    never goes negative, but then marked the ENTIRE advance as settled even
    when only part of it was actually recoverable that week — silently
    forgiving the unrecovered remainder forever instead of carrying it to the
    next payroll run."""
    rw = await cli.post(BASE, json=_worker("عامل سلفة كبيرة"))
    wid = rw.json()["id"]

    week1_start = date.today() - timedelta(days=date.today().weekday())
    week1_end = week1_start + timedelta(days=6)
    await cli.post("/api/v1/attendance", json={
        "worker_id": wid, "attendance_date": str(week1_start), "status": "present",
    })  # gross = 150 فقط هذا الأسبوع

    adv = await cli.post(f"{BASE}/advances", json={
        "worker_id": wid, "amount": 300.0, "advance_type": "advance", "date": str(week1_start),
    })
    assert adv.status_code == 201

    rp1 = await cli.post(f"{BASE}/payroll", json={"week_start": str(week1_start), "week_end": str(week1_end)})
    run1_id = rp1.json()["id"]
    gen1 = await cli.post(f"{BASE}/payroll/{run1_id}/generate")
    line1 = next(l for l in gen1.json()["lines"] if l["worker"] == "عامل سلفة كبيرة")
    # سعة الأسبوع (150) مش كافية لتغطية السلفة (300) بالكامل — لازم تتأجل
    # بالكامل (net = gross كامل)، مش تتخصم جزئيًا وتُشطب باقيها.
    assert line1["net"] == 150.0

    profile1 = await cli.get(f"{BASE}/{wid}/profile")
    assert profile1.json()["pending_advances"] == 300.0

    week2_start = week1_start + timedelta(days=7)
    week2_end = week2_start + timedelta(days=6)
    for d in range(4):
        await cli.post("/api/v1/attendance", json={
            "worker_id": wid, "attendance_date": str(week2_start + timedelta(days=d)), "status": "present",
        })  # gross = 600 — سعة كافية دلوقتي

    rp2 = await cli.post(f"{BASE}/payroll", json={"week_start": str(week2_start), "week_end": str(week2_end)})
    run2_id = rp2.json()["id"]
    gen2 = await cli.post(f"{BASE}/payroll/{run2_id}/generate")
    line2 = next(l for l in gen2.json()["lines"] if l["worker"] == "عامل سلفة كبيرة")
    assert line2["net"] == 600.0 - 300.0

    profile2 = await cli.get(f"{BASE}/{wid}/profile")
    assert profile2.json()["pending_advances"] == 0.0


@pytest.mark.asyncio
async def test_cannot_delete_settled_advance_directly(cli):
    """Regression: deleting an advance already settled into a payroll run only
    reversed its own treasury/GL entry, leaving the payroll line's deduction
    (and the worker's already-reduced net pay) with no advance record to
    justify it — effectively pocketing that amount with no trace."""
    rw = await cli.post(BASE, json=_worker("عامل حذف سلفة"))
    wid = rw.json()["id"]
    week_start = date.today() - timedelta(days=date.today().weekday())
    week_end = week_start + timedelta(days=6)
    await cli.post("/api/v1/attendance", json={
        "worker_id": wid, "attendance_date": str(week_start), "status": "present",
    })
    adv = await cli.post(f"{BASE}/advances", json={
        "worker_id": wid, "amount": 50.0, "advance_type": "advance", "date": str(week_start),
    })
    aid = adv.json()["id"]
    rp = await cli.post(f"{BASE}/payroll", json={"week_start": str(week_start), "week_end": str(week_end)})
    run_id = rp.json()["id"]
    await cli.post(f"{BASE}/payroll/{run_id}/generate")

    r = await cli.delete(f"{BASE}/advances/{aid}")
    assert r.status_code == 400

    # لسه ممكن تتحذف بعد ما تتلغى تسويتها (عن طريق حذف الكشف نفسه)
    await cli.delete(f"{BASE}/payroll/{run_id}")
    r2 = await cli.delete(f"{BASE}/advances/{aid}")
    assert r2.status_code in (200, 204)



# ════════════════ HR HARDENING: ROLES / VALIDATION ═══════════════════
@pytest.mark.asyncio
async def test_non_writer_roles_cannot_modify_workers_or_payroll(cli):
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.api.auth import create_token
    wid = (await cli.post(BASE, json=_worker("عامل صلاحيات"))).json()["id"]
    ra = await cli.post(f"{BASE}/advances", json={
        "worker_id": wid, "amount": 50, "advance_type": "bonus", "date": str(date.today())})
    assert ra.status_code == 201
    aid = ra.json()["id"]
    for role in ("viewer", "production"):
        tok = create_token(user_id=993, username=f"{role}_hr", role=role, tenant_id=1)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {tok}"}) as rc:
            assert (await rc.delete(f"{BASE}/advances/{aid}")).status_code == 403
            assert (await rc.post(f"{BASE}/advances", json={
                "worker_id": wid, "amount": 50, "advance_type": "advance",
                "date": str(date.today())})).status_code == 403
            assert (await rc.post(BASE, json=_worker("x"))).status_code == 403
            assert (await rc.put(f"{BASE}/{wid}", json=_worker("y"))).status_code == 403
            assert (await rc.post(f"{BASE}/payroll", json={
                "week_start": "2019-01-05", "week_end": "2019-01-11"})).status_code == 403
            # القراءة مسموحة
            assert (await rc.get(BASE)).status_code == 200
            assert (await rc.get(f"{BASE}/payroll")).status_code == 200
    # accountant يقدر
    tok = create_token(user_id=994, username="acc_hr", role="accountant", tenant_id=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {tok}"}) as ac:
        assert (await ac.delete(f"{BASE}/advances/{aid}")).status_code == 200


@pytest.mark.asyncio
async def test_payroll_rejects_bad_ranges_and_overlap(cli):
    r = await cli.post(f"{BASE}/payroll", json={"week_start": "2019-02-10", "week_end": "2019-02-01"})
    assert r.status_code == 400
    r = await cli.post(f"{BASE}/payroll", json={"week_start": "2019-02-01", "week_end": "2019-04-01"})
    assert r.status_code == 400
    r = await cli.post(f"{BASE}/payroll", json={"week_start": "2019-02-02", "week_end": "2019-02-08"})
    assert r.status_code == 201
    r = await cli.post(f"{BASE}/payroll", json={"week_start": "2019-02-05", "week_end": "2019-02-11"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_payroll_cannot_be_paid_twice(cli):
    wid = (await cli.post(BASE, json=_worker("عامل صرف مزدوج"))).json()["id"]
    ws = date(2019, 3, 2)
    await cli.post("/api/v1/attendance", json={"worker_id": wid, "attendance_date": str(ws), "status": "present"})
    run_id = (await cli.post(f"{BASE}/payroll", json={
        "week_start": str(ws), "week_end": str(ws + timedelta(days=6))})).json()["id"]
    assert (await cli.post(f"{BASE}/payroll/{run_id}/generate")).status_code == 200
    assert (await cli.post(f"{BASE}/payroll/{run_id}/pay")).status_code == 200
    assert (await cli.post(f"{BASE}/payroll/{run_id}/pay")).status_code == 400
    assert (await cli.post(f"{BASE}/payroll/{run_id}/generate")).status_code == 400
    assert (await cli.delete(f"{BASE}/payroll/{run_id}")).status_code == 400


@pytest.mark.asyncio
async def test_worker_and_advance_amounts_are_validated(cli):
    assert (await cli.post(BASE, json={"name": "   ", "daily_wage": 100})).status_code in (400, 422)
    assert (await cli.post(BASE, json={"name": "x" * 201, "daily_wage": 100})).status_code in (400, 422)
    r = await cli.post(BASE, json={"name": "أجر كسري", "daily_wage": "150.006"})
    assert r.status_code == 201
    assert r.json()["daily_wage"] == 150.01
    wid = r.json()["id"]
    r = await cli.post(f"{BASE}/advances", json={
        "worker_id": wid, "amount": "NaN", "advance_type": "bonus", "date": str(date.today())})
    assert r.status_code in (400, 422)
    # عامل معطَّل لا يأخذ سلفة
    assert (await cli.delete(f"{BASE}/{wid}")).status_code == 200
    r = await cli.post(f"{BASE}/advances", json={
        "worker_id": wid, "amount": 10, "advance_type": "bonus", "date": str(date.today())})
    assert r.status_code == 400
