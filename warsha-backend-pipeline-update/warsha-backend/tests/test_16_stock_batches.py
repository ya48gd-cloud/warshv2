"""
test_16_stock_batches.py — محطة 8: دمج StockBatch مع الرصيد العام والحركات
"""
import pytest
from datetime import date

INV = "/api/v1/inventory"
BATCH = "/api/v1/inventory/batches"
EQ = "/api/v1/equipment"
PROD = "/api/v1/production"
ACC = "/api/v1/accounting"


async def _make_material(cli, qty=0):
    r = await cli.post(f"{INV}/materials", json={
        "name_ar": "مادة دفعات", "unit": "كجم", "unit_cost": 10.0,
    })
    mid = r.json()["id"]
    if qty:
        await cli.post(f"{INV}/movements", json={
            "material_id": mid, "movement_type": "in", "qty": qty, "unit_cost": 10.0,
        })
    return mid


def _batch_payload(mid, lot="LOT-001", qty=50, cost=10.0):
    return {
        "material_id": mid, "lot_number": lot,
        "received_date": str(date.today()),
        "qty_received": qty, "unit_cost": cost,
    }


@pytest.mark.asyncio
async def test_create_batch_updates_stock_and_creates_movement(cli):
    """استلام دُفعة جديدة → رصيد المادة العام بيزيد بنفس الكمية، وفيه حركة 'in' مربوطة بالدُفعة كمصدر."""
    mid = await _make_material(cli, qty=0)
    r = await cli.post(BATCH, json=_batch_payload(mid, qty=50))
    assert r.status_code == 201
    batch_id = r.json()["id"]

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert mat.json()["stock_qty"] == 50.0

    movements = (await cli.get(f"{INV}/movements/{mid}")).json()
    assert len(movements) == 1
    assert movements[0]["source_doc_type"] == "stock_batch"
    assert movements[0]["source_doc_id"] == batch_id
    assert movements[0]["qty"] == 50.0


@pytest.mark.asyncio
async def test_issue_from_batch_updates_stock_and_creates_movement(cli):
    """صرف من دُفعة → رصيد المادة العام ينقص، qty_remaining في الدُفعة ينقص، وفيه حركة 'out' مربوطة."""
    mid = await _make_material(cli, qty=0)
    batch_id = (await cli.post(BATCH, json=_batch_payload(mid, qty=50))).json()["id"]

    r = await cli.post(f"{BATCH}/{batch_id}/issue", json={"qty": 20})
    assert r.status_code == 200
    assert r.json()["remaining_qty"] == 30.0

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert mat.json()["stock_qty"] == 30.0

    movements = (await cli.get(f"{INV}/movements/{mid}")).json()
    out_mv = next(m for m in movements if m["movement_type"] == "out")
    assert out_mv["source_doc_type"] == "stock_batch_issue"
    assert out_mv["source_doc_id"] == batch_id


@pytest.mark.asyncio
async def test_issue_from_batch_marks_consumed_when_fully_issued(cli):
    """صرف كل الكمية المتبقية من دُفعة → حالتها تبقى consumed."""
    mid = await _make_material(cli, qty=0)
    batch_id = (await cli.post(BATCH, json=_batch_payload(mid, qty=20))).json()["id"]
    r = await cli.post(f"{BATCH}/{batch_id}/issue", json={"qty": 20})
    assert r.json()["status"] == "consumed"
    assert r.json()["remaining_qty"] == 0.0

    b = await cli.get(f"{BATCH}/{batch_id}")
    assert b.json()["status"] == "consumed"


@pytest.mark.asyncio
async def test_issue_from_batch_respects_reservation_guard(cli):
    """
    نفس فحص محطة 1: صرف من دُفعة مايتجاوزش المتاح الحقيقي (الرصيد - المحجوز
    لأمر إنتاج آخر)، حتى لو الدُفعة نفسها فيها كمية كافية.
    """
    mid = await _make_material(cli, qty=0)
    await cli.post(BATCH, json=_batch_payload(mid, qty=100))  # الرصيد العام بقى 100

    eq_id = (await cli.post(EQ, json={"name_ar": "معدة", "level": 1})).json()["id"]
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 40.0, "unit_cost": 10.0})
    wo_id = (await cli.post(f"{ACC}/work-orders", json={
        "equipment_id": eq_id, "planned_cost": 1000.0, "start_date": str(date.today()),
    })).json()["id"]
    await cli.post(f"{PROD}/auto-reserve/{wo_id}")   # يحجز 40 من الـ 100

    batches = (await cli.get(f"{BATCH}?material_id={mid}")).json()
    batch_id = batches[0]["id"]

    # المتاح الحر = 100 - 40 = 60، فمحاولة صرف 70 من الدُفعة لازم تترفض
    r = await cli.post(f"{BATCH}/{batch_id}/issue", json={"qty": 70})
    assert r.status_code == 400
    assert "محجوز" in r.json()["detail"]

    # صرف ضمن المتاح الحر (60) ينجح عادي
    r2 = await cli.post(f"{BATCH}/{batch_id}/issue", json={"qty": 60})
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_traceability_reconciles_system_stock_with_batches(cli):
    """
    بعد الإصلاح، system_stock (Material.stock_qty) لازم يساوي مجموع الدُفعات
    النشطة بالظبط — قبل محطة 8 كان الاتنين مستقلين تمامًا عن بعض.
    """
    mid = await _make_material(cli, qty=0)
    await cli.post(BATCH, json=_batch_payload(mid, lot="LOT-A", qty=30))
    await cli.post(BATCH, json=_batch_payload(mid, lot="LOT-B", qty=20))

    trace = (await cli.get(f"{BATCH}/material/{mid}/traceability")).json()
    assert trace["system_stock"] == 50.0
    assert trace["total_remaining_batches"] == 50.0
    assert trace["system_stock"] == trace["total_remaining_batches"]


@pytest.mark.asyncio
async def test_duplicate_lot_number_still_rejected(cli):
    """توافق خلفي: فحص تكرار رقم الدُفعة لسه شغال زي الأول."""
    mid = await _make_material(cli, qty=0)
    await cli.post(BATCH, json=_batch_payload(mid, lot="DUP-1", qty=10))
    r = await cli.post(BATCH, json=_batch_payload(mid, lot="DUP-1", qty=5))
    assert r.status_code == 400


# ════════════ Round-2 gap/validation fixes ═══════════════════════════

@pytest.mark.asyncio
async def test_create_batch_rejects_non_positive_qty(cli):
    """كمية استلام صفر أو سالبة كانت بتعدي — post_receipt بيرفضها لكن لازم
    تتقفل من بوابة الـ API كمان برسالة واضحة، مش بعد ما batch اتعمل فعلاً."""
    mid = await _make_material(cli, qty=0)
    r = await cli.post(BATCH, json=_batch_payload(mid, qty=0))
    assert r.status_code == 400

    r2 = await cli.post(BATCH, json=_batch_payload(mid, qty=-5))
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_create_batch_rejects_supplier_from_another_tenant(cli, cli2):
    """مورد صحيح بس تابع لتنانت تاني كان بيتقبل من غير فحص — الدُفعة كانت
    هتتربط بمورد مش بتاعها."""
    sup = await cli2.post("/api/v1/purchases/suppliers", json={"name": "مورد تنانت2"})
    assert sup.status_code == 201
    foreign_supplier_id = sup.json()["id"]

    mid = await _make_material(cli, qty=0)
    payload = _batch_payload(mid, qty=10)
    payload["supplier_id"] = foreign_supplier_id
    r = await cli.post(BATCH, json=payload)
    assert r.status_code == 400
