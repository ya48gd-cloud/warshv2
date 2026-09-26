"""
test_05_production.py — اختبارات الإنتاج
WorkOrder · حجز مواد · صرف · خردة · إتمام · جرد المخزون · bins
"""
import pytest
from datetime import date

PROD = "/api/v1/production"
ACC = "/api/v1/accounting"
INV = "/api/v1/inventory"
EQ = "/api/v1/equipment"


async def _make_equipment(cli):
    r = await cli.post(EQ, json={"name_ar": "معدة إنتاج", "level": 1})
    return r.json()["id"]


async def _make_material(cli, qty=100):
    r = await cli.post(f"{INV}/materials", json={"name_ar": "مادة إنتاج", "unit": "كجم", "unit_cost": 5.0})
    mid = r.json()["id"]
    await cli.post(f"{INV}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": qty, "unit_cost": 5.0,
    })
    return mid


async def _make_work_order(cli, eq_id, qty=10):
    r = await cli.post(f"{ACC}/work-orders", json={
        "equipment_id": eq_id,
        "planned_cost": 1000.0,
        "start_date": str(date.today()),
    })
    return r.json()["id"]


# ════════════════ ALERTS ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_production_alerts(cli):
    r = await cli.get(f"{PROD}/alerts")
    assert r.status_code == 200


# ════════════════ RESERVE MATERIALS ══════════════════════════════════
@pytest.mark.asyncio
async def test_reserve_materials(cli):
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=50)
    wo_id = await _make_work_order(cli, eq_id)

    # Add material to BOM so reservation works
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 5.0, "unit_cost": 5.0})

    r = await cli.post(f"{PROD}/reserve/{wo_id}", params={"quantity": 1})
    assert r.status_code in (200, 201)


@pytest.mark.asyncio
async def test_get_reservations(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_work_order(cli, eq_id)
    r = await cli.get(f"{PROD}/{wo_id}/reservations")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ════════════════ ISSUE MATERIAL ══════════════════════════════════════
@pytest.mark.asyncio
async def test_issue_material(cli):
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo_id = await _make_work_order(cli, eq_id)

    r = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id,
        "material_id": mid,
        "qty": 10,
        "stage": "تجميع",
    })
    assert r.status_code in (200, 201)

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert mat.json()["stock_qty"] <= 90


# ════════════════ SCRAP ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_record_scrap(cli):
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo_id = await _make_work_order(cli, eq_id)

    r = await cli.post(f"{PROD}/scrap", json={
        "work_order_id": wo_id,
        "material_id": mid,
        "qty": 2,
        "reason": "تالف",
        "stage": "لحام",
    })
    assert r.status_code in (200, 201)


@pytest.mark.asyncio
async def test_scrap_report(cli):
    r = await cli.get(f"{PROD}/scrap/report")
    assert r.status_code == 200


# ════════════════ COMPLETE WO ════════════════════════════════════════
@pytest.mark.asyncio
async def test_complete_work_order(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_work_order(cli, eq_id)
    r = await cli.post(f"{PROD}/complete/{wo_id}")
    assert r.status_code in (200, 201)


@pytest.mark.asyncio
async def test_wo_cost_breakdown(cli):
    eq_id = await _make_equipment(cli)
    wo_id = await _make_work_order(cli, eq_id)
    r = await cli.get(f"{PROD}/{wo_id}/cost-breakdown")
    assert r.status_code == 200


# ════════════════ BIN LOCATIONS ══════════════════════════════════════
@pytest.mark.asyncio
async def test_create_bin(cli):
    r = await cli.post(f"{PROD}/bins", json={
        "code": "BIN-001", "name_ar": "رف أ", "zone": "A",
    })
    assert r.status_code == 201
    assert r.json()["code"] == "BIN-001"


@pytest.mark.asyncio
async def test_list_bins(cli):
    await cli.post(f"{PROD}/bins", json={"code": "BIN-002", "name_ar": "رف ب"})
    r = await cli.get(f"{PROD}/bins")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_bin_stock(cli):
    r = await cli.post(f"{PROD}/bins", json={"code": "BIN-003", "name_ar": "رف ج"})
    bin_id = r.json()["id"]
    r2 = await cli.get(f"{PROD}/bins/{bin_id}/stock")
    assert r2.status_code == 200


# ════════════════ STOCK COUNT ════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_stock_count(cli):
    r = await cli.post(f"{PROD}/stock-count")
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_list_stock_counts(cli):
    r = await cli.get(f"{PROD}/stock-count")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_stock_count_scan_and_close(cli):
    # Create a fresh material
    r_mat = await cli.post(f"{INV}/materials", json={"name_ar": "مادة جرد", "unit": "كجم", "unit_cost": 1.0})
    mid = r_mat.json()["id"]

    r_sc = await cli.post(f"{PROD}/stock-count")
    count_id = r_sc.json()["id"]

    # Scan a material in this count
    r_scan = await cli.post(f"{PROD}/stock-count/{count_id}/scan", json={
        "material_id": mid, "actual_qty": 15,
    })
    assert r_scan.status_code in (200, 201)

    # Close count
    r_close = await cli.post(f"{PROD}/stock-count/{count_id}/close")
    assert r_close.status_code == 200


# ════════════════ MRP ════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mrp_suggestions(cli):
    r = await cli.get(f"{PROD}/mrp/suggestions")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_run_mrp(cli):
    r = await cli.post(f"{PROD}/mrp/run")
    assert r.status_code in (200, 201)


# ════════════════ AUTO RESERVE ═══════════════════════════════════════
@pytest.mark.asyncio
async def test_auto_reserve(cli):
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=50)
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 2.0, "unit_cost": 5.0})
    wo_id = await _make_work_order(cli, eq_id)
    r = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r.status_code in (200, 201)


# ════════════════ محطة 1: منع الصرف اليدوي من الرصيد المحجوز ═══════════
@pytest.mark.asyncio
async def test_manual_issue_blocked_by_reservation(cli):
    """رصيد 100، محجوز 5 لأمر إنتاج → محاولة صرف يدوي 98 (أكبر من المتاح الحر 95) يجب أن تُرفض."""
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 5.0, "unit_cost": 5.0})
    wo_id = await _make_work_order(cli, eq_id)

    r = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r.status_code in (200, 201)

    r2 = await cli.post(f"{INV}/movements", json={
        "material_id": mid, "movement_type": "out", "qty": 98, "unit_cost": 5.0,
    })
    assert r2.status_code == 400
    body = r2.json()["detail"]
    assert "محجوز" in body

    # الرصيد ما اتأثرش لما الصرف اتمنع
    mat = await cli.get(f"{INV}/materials/{mid}")
    assert mat.json()["stock_qty"] == 100


@pytest.mark.asyncio
async def test_manual_issue_allowed_within_free_stock(cli):
    """رصيد 100، محجوز 5 لأمر إنتاج → صرف يدوي 95 (بالظبط = المتاح الحر) يجب أن ينجح."""
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 5.0, "unit_cost": 5.0})
    wo_id = await _make_work_order(cli, eq_id)

    r = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r.status_code in (200, 201)

    r2 = await cli.post(f"{INV}/movements", json={
        "material_id": mid, "movement_type": "out", "qty": 95, "unit_cost": 5.0,
    })
    assert r2.status_code == 201

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert mat.json()["stock_qty"] == 5


@pytest.mark.asyncio
async def test_manual_issue_without_any_reservation_unaffected(cli):
    """مفيش حجوزات خالص على المادة → الصرف اليدوي يفضل شغال زي الأول بدون أي تغيير في السلوك."""
    mid = await _make_material(cli, qty=40)
    r = await cli.post(f"{INV}/movements", json={
        "material_id": mid, "movement_type": "out", "qty": 40, "unit_cost": 5.0,
    })
    assert r.status_code == 201
    mat = await cli.get(f"{INV}/materials/{mid}")
    assert mat.json()["stock_qty"] == 0
