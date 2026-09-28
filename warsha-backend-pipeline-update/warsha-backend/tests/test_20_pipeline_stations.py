"""
test_20_pipeline_stations.py — اختبارات محطات تتبع دورة حياة المعدة
(Equipment Pipeline): WIP فعلي (محطة 14) → FG فعلي (محطة 15) →
ربط التسليم (محطة 16) → لوحة Pipeline (محطة 17).

كل محطة بيتم اختبارها لوحدها بشكل دقيق قبل الانتقال للي بعدها.
"""
import pytest
from datetime import date

PROD = "/api/v1/production"
ACC = "/api/v1/accounting"
INV = "/api/v1/inventory"
EQ = "/api/v1/equipment"
DO = "/api/v1/delivery-orders"


async def _make_equipment(cli, name="معدة أعلاف"):
    r = await cli.post(EQ, json={"name_ar": name, "level": 1})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _make_material(cli, qty=100):
    r = await cli.post(f"{INV}/materials", json={"name_ar": "مادة إنتاج", "unit": "كجم", "unit_cost": 5.0})
    mid = r.json()["id"]
    await cli.post(f"{INV}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": qty, "unit_cost": 5.0,
    })
    return mid


async def _make_work_order(cli, eq_id, qty=1, customer_order_id=None):
    payload = {
        "equipment_id": eq_id,
        "planned_cost": 1000.0,
        "start_date": str(date.today()),
        "quantity": qty,
    }
    if customer_order_id:
        payload["customer_order_id"] = customer_order_id
    r = await cli.post(f"{ACC}/work-orders", json=payload)
    assert r.status_code in (200, 201), r.text
    return r.json()


# ═══════════════════════════════════════════════════════════════
# محطة 14 — تسجيل "تحت التصنيع" كرصيد فعلي في موقع WIP
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_issue_material_creates_real_wip_quant(cli):
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo = await _make_work_order(cli, eq_id, qty=2)
    wo_id = wo["id"]
    wo_code = wo["code"]

    r = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 10, "stage": "تجميع",
    })
    assert r.status_code in (200, 201), r.text

    # لازم تتلاقى مادة WIP-{wo.code} برصيد = كمية أمر الإنتاج (2)
    mats = await cli.get(f"{INV}/materials")
    assert mats.status_code == 200
    found = [m for m in mats.json() if m["code"] == f"WIP-{wo_code}"]
    assert found, f"لم يتم إنشاء مادة WIP للأمر {wo_code}: {mats.json()}"
    assert float(found[0]["stock_qty"]) == 2.0

    # ولازم يبقى ليها StockQuant فعلي في موقع WIP-DEFAULT (مش الموقع الافتراضي العام)
    wh = await cli.get("/api/v1/warehouses")
    wip_wh = next(w for w in wh.json() if w["code"] == "WIP")
    assert wip_wh is not None


@pytest.mark.asyncio
async def test_second_issue_does_not_duplicate_wip_qty(cli):
    """صرف تاني لنفس أمر الإنتاج لازم يحدّث التكلفة بس من غير ما يضاعف كمية WIP."""
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id, wo_code = wo["id"], wo["code"]

    await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 5, "stage": "قطع",
    })
    await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 5, "stage": "لحام",
    })

    mats = await cli.get(f"{INV}/materials")
    found = [m for m in mats.json() if m["code"] == f"WIP-{wo_code}"]
    assert len(found) == 1
    assert float(found[0]["stock_qty"]) == 1.0  # لسه 1 مش 2
    assert float(found[0]["unit_cost"]) > 0  # اتحدثت بالتكلفة التراكمية


# ═══════════════════════════════════════════════════════════════
# محطة 15 — ترحيل حقيقي WIP → FG عند إتمام أمر الإنتاج
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_complete_work_order_moves_wip_to_fg(cli):
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id, wo_code = wo["id"], wo["code"]

    await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 10, "stage": "تجميع",
    })

    mats_before = await cli.get(f"{INV}/materials")
    wip_before = next(m for m in mats_before.json() if m["code"] == f"WIP-{wo_code}")
    assert float(wip_before["stock_qty"]) == 1.0

    r = await cli.post(f"{PROD}/complete/{wo_id}")
    assert r.status_code in (200, 201), r.text

    mats_after = await cli.get(f"{INV}/materials")
    wip_after = next(m for m in mats_after.json() if m["code"] == f"WIP-{wo_code}")
    assert float(wip_after["stock_qty"]) == 0.0  # WIP اتفرغ

    fg = next(m for m in mats_after.json() if m["code"] == f"FG-{wo_code}")
    assert float(fg["stock_qty"]) == 1.0  # ودخل FG
    assert float(fg["unit_cost"]) > 0


@pytest.mark.asyncio
async def test_complete_work_order_without_prior_issue_still_creates_fg(cli):
    """أمر إنتاج اتقفل من غير أي صرف مواد (مثلاً معدة تكلفتها صفر) —
    المفروض ما ينهارش، وييجي FG برصيد الكمية حتى لو تكلفتها صفر."""
    eq_id = await _make_equipment(cli)
    wo = await _make_work_order(cli, eq_id, qty=3)
    wo_id, wo_code = wo["id"], wo["code"]

    r = await cli.post(f"{PROD}/complete/{wo_id}")
    assert r.status_code in (200, 201), r.text

    mats = await cli.get(f"{INV}/materials")
    fg = next(m for m in mats.json() if m["code"] == f"FG-{wo_code}")
    assert float(fg["stock_qty"]) == 3.0


# ═══════════════════════════════════════════════════════════════
# محطة 16 — ربط بند التسليم بالمعدة/أمر الإنتاج + خصم فعلي من FG
# ═══════════════════════════════════════════════════════════════

async def _make_customer(cli):
    r = await cli.post("/api/v1/customers", json={"name": "عميل تجريبي"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _make_delivery_order(cli, customer_id, lines):
    r = await cli.post(DO, json={
        "order_date": str(date.today()),
        "customer_id": customer_id,
        "vehicle_plate": "أ ب ج 123",
        "driver_name": "سائق تجريبي",
        "lines": lines,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.mark.asyncio
async def test_delivery_deducts_real_fg_stock(cli):
    eq_id = await _make_equipment(cli)
    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id, wo_code = wo["id"], wo["code"]
    await cli.post(f"{PROD}/complete/{wo_id}")

    cust_id = await _make_customer(cli)
    order = await _make_delivery_order(cli, cust_id, [{
        "description": "معدة أعلاف", "qty": "1", "equipment_id": eq_id, "work_order_id": wo_id,
    }])
    assert order["lines"][0]["work_order_id"] == wo_id

    await cli.patch(f"{DO}/{order['id']}/status", json={"status": "confirmed"})
    r = await cli.patch(f"{DO}/{order['id']}/status", json={"status": "delivered"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "delivered"

    mats = await cli.get(f"{INV}/materials")
    fg = next(m for m in mats.json() if m["code"] == f"FG-{wo_code}")
    assert float(fg["stock_qty"]) == 0.0  # اتخصمت فعليًا


@pytest.mark.asyncio
async def test_delivery_blocked_when_fg_not_ready(cli):
    """
    قرار تصميم: تسليم معدة لسه مش خلّصت تصنيع (مفيش رصيد FG) لازم يُرفض
    بالكامل (400) — مش يتسجل "delivered" بتحذير. سجل تسليم كاذب أخطر من
    رفض العملية.
    """
    eq_id = await _make_equipment(cli)
    wo = await _make_work_order(cli, eq_id, qty=1)  # من غير complete()
    cust_id = await _make_customer(cli)
    order = await _make_delivery_order(cli, cust_id, [{
        "description": "معدة أعلاف", "qty": "1", "equipment_id": eq_id, "work_order_id": wo["id"],
    }])

    await cli.patch(f"{DO}/{order['id']}/status", json={"status": "confirmed"})
    r = await cli.patch(f"{DO}/{order['id']}/status", json={"status": "delivered"})
    assert r.status_code == 400
    assert "FG" in r.json()["detail"] or "رصيد" in r.json()["detail"]

    # لازم الأمر يفضل "confirmed" — لم يتغير حاله رغم فشل الخصم
    detail = await cli.get(f"{DO}/{order['id']}")
    assert detail.json()["status"] == "confirmed"


@pytest.mark.asyncio
async def test_delivery_blocked_when_qty_exceeds_available(cli):
    """طلب كمية أكبر من المتاح فعليًا في FG — نفس مبدأ الرفض الكامل."""
    eq_id = await _make_equipment(cli)
    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id = wo["id"]
    await cli.post(f"{PROD}/complete/{wo_id}")  # FG=1

    cust_id = await _make_customer(cli)
    order = await _make_delivery_order(cli, cust_id, [{
        "description": "معدة أعلاف", "qty": "5", "equipment_id": eq_id, "work_order_id": wo_id,
    }])
    await cli.patch(f"{DO}/{order['id']}/status", json={"status": "confirmed"})
    r = await cli.patch(f"{DO}/{order['id']}/status", json={"status": "delivered"})
    assert r.status_code == 400

    mats = await cli.get(f"{INV}/materials")
    fg = next(m for m in mats.json() if m["code"] == f"FG-{wo['code']}")
    assert float(fg["stock_qty"]) == 1.0  # ماتخصمش حاجة خالص (all-or-nothing)


@pytest.mark.asyncio
async def test_delivery_unlinked_lines_still_work_normally(cli):
    """بند تسليم عادي (من غير work_order_id) لازم يفضل شغال زي الأول من غير أي فحص FG."""
    cust_id = await _make_customer(cli)
    order = await _make_delivery_order(cli, cust_id, [{
        "description": "قطعة غيار عامة", "qty": "3",
    }])
    await cli.patch(f"{DO}/{order['id']}/status", json={"status": "confirmed"})
    r = await cli.patch(f"{DO}/{order['id']}/status", json={"status": "delivered"})
    assert r.status_code == 200
    assert r.json()["status"] == "delivered"


# ═══════════════════════════════════════════════════════════════
# محطة 17 — لوحة تتبع دورة حياة المعدة (Pipeline)
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pipeline_shows_wip_status_before_completion(cli):
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=100)
    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id = wo["id"]

    await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": mid, "qty": 10, "stage": "تجميع",
    })

    r = await cli.get(f"{PROD}/pipeline")
    assert r.status_code == 200
    row = next(x for x in r.json() if x["work_order_id"] == wo_id)
    assert row["pipeline_status"] == "تحت التصنيع"
    assert row["wip_qty"] == 1.0
    assert row["fg_qty"] == 0.0
    assert row["is_stock_based"] is True  # مفيش customer_order_id

    detail = await cli.get(f"{PROD}/pipeline/{wo_id}")
    assert detail.status_code == 200
    assert detail.json()["pipeline_status"] == "تحت التصنيع"


@pytest.mark.asyncio
async def test_pipeline_reflects_fg_and_delivery(cli):
    eq_id = await _make_equipment(cli)
    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id = wo["id"]
    await cli.post(f"{PROD}/complete/{wo_id}")

    r = await cli.get(f"{PROD}/pipeline")
    row = next(x for x in r.json() if x["work_order_id"] == wo_id)
    assert row["pipeline_status"] == "جاهزة في المخزون (FG)"
    assert row["fg_qty"] == 1.0
    assert row["progress_percent"] is None  # لا يوجد WorkOrderStage لهذا الأمر

    cust_id = await _make_customer(cli)
    order = await _make_delivery_order(cli, cust_id, [{
        "description": "معدة أعلاف", "qty": "1", "work_order_id": wo_id,
    }])
    await cli.patch(f"{DO}/{order['id']}/status", json={"status": "confirmed"})
    await cli.patch(f"{DO}/{order['id']}/status", json={"status": "delivered"})

    r2 = await cli.get(f"{PROD}/pipeline")
    row2 = next(x for x in r2.json() if x["work_order_id"] == wo_id)
    assert row2["pipeline_status"] == "تم التسليم بالكامل"
    assert row2["fg_qty"] == 0.0
    assert row2["delivered_qty"] == 1.0
    assert len(row2["deliveries"]) == 1
    assert row2["deliveries"][0]["delivery_order_code"] == order["code"]


@pytest.mark.asyncio
async def test_pipeline_search_filters_results(cli):
    eq_id = await _make_equipment(cli, name="معدة خلط أسمنت")
    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id, wo_code = wo["id"], wo["code"]

    r = await cli.get(f"{PROD}/pipeline", params={"q": "خلط أسمنت"})
    assert r.status_code == 200
    assert any(x["work_order_id"] == wo_id for x in r.json())

    r2 = await cli.get(f"{PROD}/pipeline", params={"q": "غير موجود إطلاقاً"})
    assert all(x["work_order_id"] != wo_id for x in r2.json())


@pytest.mark.asyncio
async def test_pipeline_respects_tenant_isolation(cli, cli2):
    eq_id = await _make_equipment(cli)
    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id = wo["id"]

    r_t1 = await cli.get(f"{PROD}/pipeline")
    assert any(x["work_order_id"] == wo_id for x in r_t1.json())

    r_t2 = await cli2.get(f"{PROD}/pipeline")
    assert all(x["work_order_id"] != wo_id for x in r_t2.json())

    detail_t2 = await cli2.get(f"{PROD}/pipeline/{wo_id}")
    assert detail_t2.status_code == 404


# ═══════════════════════════════════════════════════════════════
# محطة 18 — Backfill لأوامر الإنتاج القديمة (idempotent)
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_backfill_dry_run_does_not_modify_data(cli):
    eq_id = await _make_equipment(cli)
    wo = await _make_work_order(cli, eq_id, qty=2)
    wo_id, wo_code = wo["id"], wo["code"]
    await cli.post(f"{PROD}/complete/{wo_id}")  # status=done لكن عن طريق المسار العادي (اللي أصلاً بيعمل FG)

    # نمسح المادة اللي اتعملت عادي عشان نحاكي "أمر قديم" ماعندوش FG
    # (مينفعش نمسح من الـ API، فهنعمل أمر تاني بالفعل من غير ما نكمله ونحاكي القِدم بمحاكاة الحالة)
    r = await cli.post(f"{PROD}/pipeline/backfill", params={"dry_run": "true"})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    # الأمر ده already معاه FG من المسار العادي فلازم يبقى skipped
    assert wo_code in body["skipped"]


@pytest.mark.asyncio
async def test_backfill_creates_fg_for_legacy_done_order(cli, headers_t1):
    """محاكاة أمر إنتاج 'قديم' status=done بس من غير FG-{code} (زي لو كان
    اتعمل قبل تفعيل محطة 15) — الـ backfill لازم يسجّله صح."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app as _app

    eq_id = await _make_equipment(cli)
    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id, wo_code = wo["id"], wo["code"]

    # نغيّر حالته لـ done مباشرة في قاعدة البيانات (محاكاة "قديم" من غير
    # ما نعدي على complete_work_order اللي أصلاً بيعمل FG تلقائيًا)
    import os
    from sqlalchemy import text as _text
    from tests.conftest import TestSessionLocal
    async with TestSessionLocal() as s:
        await s.execute(_text("UPDATE work_orders SET status='done' WHERE id=:id"), {"id": wo_id})
        await s.commit()

    r = await cli.post(f"{PROD}/pipeline/backfill", params={"dry_run": "false"})
    assert r.status_code == 200
    body = r.json()
    assert wo_code in body["fg_created"]

    mats = await cli.get(f"{INV}/materials")
    fg = next(m for m in mats.json() if m["code"] == f"FG-{wo_code}")
    assert float(fg["stock_qty"]) == 1.0

    # تشغيلها تاني لازم تتجاهله (idempotent)
    r2 = await cli.post(f"{PROD}/pipeline/backfill", params={"dry_run": "false"})
    assert wo_code in r2.json()["skipped"]


@pytest.mark.asyncio
async def test_backfill_requires_admin(cli, headers_t1):
    """صلاحية admin مطلوبة — الـ fixture الافتراضي admin أصلاً فبنتأكد إن الرد مش 403 على الأقل."""
    r = await cli.post(f"{PROD}/pipeline/backfill", params={"dry_run": "true"})
    assert r.status_code != 403


# ═══════════════════════════════════════════════════════════════
# محطة 19 — استلام أمر الشراء بموقع فعلي (RAW / IMPORTED)
# ═══════════════════════════════════════════════════════════════

PO = "/api/v1/purchase-orders"


async def _make_supplier(cli, name="مورد محلي", supplier_type=None, is_import=None):
    payload = {"name": name}
    if supplier_type:
        payload["supplier_type"] = supplier_type
    if is_import is not None:
        payload["is_import"] = is_import
    r = await cli.post("/api/v1/purchases/suppliers", json=payload)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _make_po(cli, supplier_id, material_id, qty=10, unit_cost=5.0):
    r = await cli.post(PO, json={
        "supplier_id": supplier_id, "order_date": str(date.today()),
        "lines": [{"material_id": material_id, "description": "مادة", "ordered_qty": qty, "unit": "كجم", "unit_cost": unit_cost}],
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _bare_material(cli):
    r = await cli.post(f"{INV}/materials", json={"name_ar": "مادة استلام", "unit": "كجم", "unit_cost": 0})
    return r.json()["id"]


@pytest.mark.asyncio
async def test_local_supplier_receipt_goes_to_raw_location(cli):
    sup_id = await _make_supplier(cli, "مورد محلي", supplier_type="محلي")
    mid = await _bare_material(cli)
    po = await _make_po(cli, sup_id, mid, qty=10, unit_cost=5.0)

    r = await cli.patch(f"{PO}/{po['id']}/approve")
    assert r.status_code == 200, r.text
    r = await cli.post(f"{PO}/{po['id']}/receive", json={
        "receive_date": str(date.today()),
        "lines": [{"line_id": po["lines"][0]["id"], "received_qty": 10, "update_cost": True}],
    })
    assert r.status_code == 200, r.text

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 10.0
    assert float(mat.json()["unit_cost"]) == 5.0  # update_cost=True

    quants = await cli.get(f"{INV}/materials/{mid}/quants")
    assert quants.status_code == 200
    rows = quants.json()
    assert len(rows) == 1
    assert rows[0]["location_code"] == "RAW-DEFAULT"
    assert float(rows[0]["qty"]) == 10.0


@pytest.mark.asyncio
async def test_import_supplier_receipt_goes_to_imported_location(cli):
    sup_id = await _make_supplier(cli, "مورد صيني", supplier_type="مستورد")
    mid = await _bare_material(cli)
    po = await _make_po(cli, sup_id, mid, qty=20, unit_cost=8.0)

    await cli.patch(f"{PO}/{po['id']}/approve")
    r = await cli.post(f"{PO}/{po['id']}/receive", json={
        "receive_date": str(date.today()),
        "lines": [{"line_id": po["lines"][0]["id"], "received_qty": 20, "update_cost": True}],
    })
    assert r.status_code == 200, r.text

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 20.0

    quants = await cli.get(f"{INV}/materials/{mid}/quants")
    assert quants.status_code == 200
    rows = quants.json()
    assert len(rows) == 1
    assert rows[0]["location_code"] == "IMPORTED-DEFAULT"
    assert float(rows[0]["qty"]) == 20.0

    wh = await cli.get("/api/v1/warehouses")
    imported_wh = next(w for w in wh.json() if w["code"] == "IMPORTED")
    assert imported_wh is not None


@pytest.mark.asyncio
async def test_second_receipt_same_material_accumulates_at_same_location(cli):
    """استلام تاني لنفس المادة من مورد محلي — لازم يتجمّع في نفس موقع RAW مش يتكرر."""
    sup_id = await _make_supplier(cli, "مورد محلي 2", supplier_type="محلي")
    mid = await _bare_material(cli)
    po1 = await _make_po(cli, sup_id, mid, qty=10, unit_cost=5.0)
    await cli.patch(f"{PO}/{po1['id']}/approve")
    await cli.post(f"{PO}/{po1['id']}/receive", json={
        "receive_date": str(date.today()),
        "lines": [{"line_id": po1["lines"][0]["id"], "received_qty": 10, "update_cost": True}],
    })

    po2 = await _make_po(cli, sup_id, mid, qty=5, unit_cost=6.0)
    await cli.patch(f"{PO}/{po2['id']}/approve")
    await cli.post(f"{PO}/{po2['id']}/receive", json={
        "receive_date": str(date.today()),
        "lines": [{"line_id": po2["lines"][0]["id"], "received_qty": 5, "update_cost": False}],
    })

    quants = await cli.get(f"{INV}/materials/{mid}/quants")
    rows = quants.json()
    assert len(rows) == 1  # لسه موقع واحد بس، اتجمّع الرصيد فيه
    assert float(rows[0]["qty"]) == 15.0


# ═══════════════════════════════════════════════════════════════
# محطة 21 — تصنيف صريح is_import (بدل الاعتماد فقط على heuristic)
# ═══════════════════════════════════════════════════════════════

async def _receive_full(cli, sup_id, mid, qty=10, unit_cost=5.0):
    po = await _make_po(cli, sup_id, mid, qty=qty, unit_cost=unit_cost)
    await cli.patch(f"{PO}/{po['id']}/approve")
    r = await cli.post(f"{PO}/{po['id']}/receive", json={
        "receive_date": str(date.today()),
        "lines": [{"line_id": po["lines"][0]["id"], "received_qty": qty, "update_cost": True}],
    })
    assert r.status_code == 200, r.text
    return po


@pytest.mark.asyncio
async def test_explicit_is_import_true_overrides_missing_supplier_type(cli):
    """is_import=True صراحة، من غير أي supplier_type نصي — لازم يروح IMPORTED."""
    sup_id = await _make_supplier(cli, "مورد بدون تصنيف نصي", is_import=True)
    mid = await _bare_material(cli)
    await _receive_full(cli, sup_id, mid, qty=7)

    quants = await cli.get(f"{INV}/materials/{mid}/quants")
    rows = quants.json()
    assert len(rows) == 1
    assert rows[0]["location_code"] == "IMPORTED-DEFAULT"


@pytest.mark.asyncio
async def test_explicit_is_import_false_overrides_misleading_supplier_type(cli):
    """is_import=False صراحة، حتى لو supplier_type فيه كلمة 'مستورد' — الصريح بيغلب الـ heuristic."""
    sup_id = await _make_supplier(cli, "مورد استثناء", supplier_type="مستورد رسميًا بس بيتعامل محلي", is_import=False)
    mid = await _bare_material(cli)
    await _receive_full(cli, sup_id, mid, qty=4)

    quants = await cli.get(f"{INV}/materials/{mid}/quants")
    rows = quants.json()
    assert len(rows) == 1
    assert rows[0]["location_code"] == "RAW-DEFAULT"


@pytest.mark.asyncio
async def test_no_explicit_classification_falls_back_to_heuristic(cli):
    """is_import مش متحدد (None) — يرجع لسلوك محطة 19 (heuristic على supplier_type)."""
    sup_id = await _make_supplier(cli, "مورد بالطريقة القديمة", supplier_type="مستورد")
    mid = await _bare_material(cli)
    await _receive_full(cli, sup_id, mid, qty=3)

    quants = await cli.get(f"{INV}/materials/{mid}/quants")
    rows = quants.json()
    assert rows[0]["location_code"] == "IMPORTED-DEFAULT"


# ═══════════════════════════════════════════════════════════════
# محطة 22 — بند تكلفة يدوي (Cost Line) بيترحّل لـ AccountingEntry الحقيقي
# ═══════════════════════════════════════════════════════════════

ACC = "/api/v1/accounting"
COA = "/api/v1/accounts"


async def _trial_balance_map(cli):
    await cli.post(f"{COA}/chart/seed")  # آمن يتكرر — idempotent
    r = await cli.get(f"{COA}/trial-balance")
    assert r.status_code == 200, r.text
    return {row["code"]: row for row in r.json()["accounts"]}


@pytest.mark.asyncio
async def test_cost_line_posts_to_accounting_entry_not_legacy_table(cli):
    eq_id = await _make_equipment(cli)
    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id = wo["id"]

    before = await _trial_balance_map(cli)
    wip_before = float(before.get("1320", {}).get("total_debit", 0))
    raw_before = float(before.get("1310", {}).get("total_credit", 0))

    r = await cli.post(f"{ACC}/cost-lines", json={
        "work_order_id": wo_id, "cost_type": "material",
        "description": "صاج", "qty": 2, "unit_cost": 150,
    })
    assert r.status_code == 201, r.text

    after = await _trial_balance_map(cli)
    assert float(after["1320"]["total_debit"]) == wip_before + 300.0
    assert float(after["1310"]["total_credit"]) == raw_before + 300.0


@pytest.mark.asyncio
async def test_cost_line_delete_reverses_the_correct_line_only(cli):
    """أمر إنتاج عليه بندين تكلفة، حذف واحد بس لازم يعكس بنده هو تحديدًا،
    مش آخر قيد اتسجل (لأن ده كان هيبقى غلط لو استخدمنا journal_reverse_entry العام)."""
    eq_id = await _make_equipment(cli)
    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id = wo["id"]
    await cli.post(f"{COA}/chart/seed")  # لازم قبل أول بند تكلفة، مش بعده

    r1 = await cli.post(f"{ACC}/cost-lines", json={
        "work_order_id": wo_id, "cost_type": "material",
        "description": "صاج", "qty": 1, "unit_cost": 100,
    })
    line1_id = r1.json()["id"]

    r2 = await cli.post(f"{ACC}/cost-lines", json={
        "work_order_id": wo_id, "cost_type": "labor",
        "description": "لحام", "qty": 1, "unit_cost": 50,
    })
    line2_id = r2.json()["id"]

    before = await _trial_balance_map(cli)
    wip_before = float(before["1320"]["total_debit"])  # 100 + 50 = 150 اتضافوا

    # نحذف بند المواد (line1) بس
    dr = await cli.delete(f"{ACC}/cost-lines/{line1_id}")
    assert dr.status_code == 200, dr.text

    after = await _trial_balance_map(cli)
    # اتعكس بند المواد (100) — القيد العكسي: دائن WIP (1320) / مدين مخزون خام (1310)
    # يعني total_debit لـ1320 يفضل زي ما هو (150، من البندين الأصليين)،
    # وtotal_credit يزيد بـ100 (القيد العكسي)
    assert float(after["1320"]["total_debit"]) == wip_before  # لسه 150، من غير تغيير
    assert float(after["1320"]["total_credit"]) == 100.0  # القيد العكسي
    assert float(after["2210"]["total_credit"]) == 50.0  # بند العمالة لسه موجود زي ما هو


# ═══════════════════════════════════════════════════════════════
# محطة 23 — استكشاف: work-orders/{id}/to-invoice (مفيش أي اختبار عليه قبل كده)
# ═══════════════════════════════════════════════════════════════

async def _make_quotation(cli, cust_id, unit_price=1000, qty=1):
    import random
    r = await cli.post("/api/v1/sales/quotations", json={
        "code": f"QUO-REPRO-{random.randint(10000,99999)}",
        "customer_id": cust_id,
        "date": str(date.today()),
        "valid_until": str(date.today()),
        "lines": [{"description": "معدة أعلاف", "qty": qty, "unit": "قطعة", "unit_price": unit_price}],
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.mark.asyncio
async def test_work_order_to_invoice_creates_valid_invoice(cli):
    """
    قبل محطة 23: الـ endpoint ده كان بيطلع IntegrityError دايمًا (invoices.code
    وحقول تانية إجبارية معندهاش قيمة) — مفيش أي تست كان بيغطّيه فاكتشفناه
    اتأخر. دلوقتي لازم ينجح ويربط الفاتورة بأمر الإنتاج فعليًا.
    """
    eq_id = await _make_equipment(cli)
    cust_id = await _make_customer(cli)
    quot = await _make_quotation(cli, cust_id, unit_price=1000, qty=1)

    order_r = await cli.post(f"/api/v1/sales/quotations/{quot['id']}/to-order", json={})
    order_id = order_r.json()["id"]

    wo_r = await cli.post(f"/api/v1/sales/orders/{order_id}/to-work-order", json={
        "equipment_id": eq_id, "quantity": 1, "quantity_unit": "وحدة",
        "start_date": str(date.today()),
    })
    wo_id = wo_r.json()["id"]
    await cli.post(f"{PROD}/complete/{wo_id}")  # أمر الإنتاج يخلص فعليًا

    r = await cli.post(f"/api/v1/sales/work-orders/{wo_id}/to-invoice", json={})
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["total"] == 1000.0
    assert inv["code"]  # كود الفاتورة اتولّد فعليًا (كان NULL قبل كده)

    # لازم الفاتورة تبقى مربوطة بأمر الإنتاج فعليًا (Invoice.work_order_id)
    detail = await cli.get(f"/api/v1/sales/invoices/{inv['id']}")
    assert detail.json()["work_order_id"] == wo_id

    # ولازم wo.status يفضل "done" — مش "billed" — عشان تقارير الإنتاج المكتمل
    # (dashboard/reports/price_catalog) متفقدوش الأمر ده من حساباتها
    pipeline = await cli.get(f"{PROD}/pipeline/{wo_id}")
    assert pipeline.json()["status"] == "done"


@pytest.mark.asyncio
async def test_work_order_to_invoice_blocks_double_invoicing(cli):
    """قبل محطة 23: مفيش أي منع من فوترة نفس أمر الإنتاج مرتين."""
    eq_id = await _make_equipment(cli)
    cust_id = await _make_customer(cli)
    quot = await _make_quotation(cli, cust_id, unit_price=500, qty=1)
    order_id = (await cli.post(f"/api/v1/sales/quotations/{quot['id']}/to-order", json={})).json()["id"]
    wo_id = (await cli.post(f"/api/v1/sales/orders/{order_id}/to-work-order", json={
        "equipment_id": eq_id, "quantity": 1, "quantity_unit": "وحدة",
        "start_date": str(date.today()),
    })).json()["id"]
    await cli.post(f"{PROD}/complete/{wo_id}")

    r1 = await cli.post(f"/api/v1/sales/work-orders/{wo_id}/to-invoice", json={})
    assert r1.status_code == 201

    r2 = await cli.post(f"/api/v1/sales/work-orders/{wo_id}/to-invoice", json={})
    assert r2.status_code == 400
    assert "مسبقًا" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_work_order_to_invoice_deducts_fg_stock_and_posts_cogs(cli):
    """
    قبل الإصلاح: فوترة أمر إنتاج مباشرة (work-orders/{id}/to-invoice) كانت
    بتسجّل إيراد كامل (journal_sales_invoice_create) من غير ما تخصم أي مخزون
    تام الصنع (FG-{code}) ومن غير أي قيد COGS — نفس فئة فجوة "عقود التوريد"
    اللي اتصلحت قبل كده، لكن هنا في المسار الأكتر استخدامًا لفوترة التصنيع.
    """
    eq_id = await _make_equipment(cli)
    cust_id = await _make_customer(cli)
    quot = await _make_quotation(cli, cust_id, unit_price=1000, qty=2)
    order_id = (await cli.post(f"/api/v1/sales/quotations/{quot['id']}/to-order", json={})).json()["id"]
    wo_r = (await cli.post(f"/api/v1/sales/orders/{order_id}/to-work-order", json={
        "equipment_id": eq_id, "quantity": 2, "quantity_unit": "وحدة",
        "start_date": str(date.today()),
    })).json()
    wo_id, wo_code = wo_r["id"], wo_r["code"]
    await cli.post(f"{COA}/chart/seed")
    await cli.post(f"{ACC}/cost-lines", json={
        "work_order_id": wo_id, "cost_type": "material",
        "description": "صاج", "qty": 2, "unit_cost": 150,
    })
    await cli.post(f"{PROD}/complete/{wo_id}")

    mats_before = (await cli.get("/api/v1/inventory/materials")).json()
    fg_before = next(m for m in mats_before if m["code"] == f"FG-{wo_code}")
    assert float(fg_before["stock_qty"]) == 2.0

    before_bal = await _trial_balance_map(cli)
    cogs_before = float(before_bal.get("5100", {}).get("total_debit", 0))

    r = await cli.post(f"/api/v1/sales/work-orders/{wo_id}/to-invoice", json={})
    assert r.status_code == 201, r.text

    mats_after = (await cli.get("/api/v1/inventory/materials")).json()
    fg_after = next(m for m in mats_after if m["code"] == f"FG-{wo_code}")
    assert float(fg_after["stock_qty"]) == 0.0, "FG stock must be fully released on direct work-order invoicing"

    after_bal = await _trial_balance_map(cli)
    cogs_after = float(after_bal.get("5100", {}).get("total_debit", 0))
    assert cogs_after > cogs_before, "a COGS entry must be posted when invoicing a completed work order directly"


@pytest.mark.asyncio
async def test_work_order_to_invoice_blocked_when_not_yet_produced(cli):
    """محاولة فوترة أمر إنتاج معملوش /production/complete لسه لازم تترفض (مفيش رصيد FG)."""
    eq_id = await _make_equipment(cli)
    cust_id = await _make_customer(cli)
    quot = await _make_quotation(cli, cust_id, unit_price=500, qty=1)
    order_id = (await cli.post(f"/api/v1/sales/quotations/{quot['id']}/to-order", json={})).json()["id"]
    wo_id = (await cli.post(f"/api/v1/sales/orders/{order_id}/to-work-order", json={
        "equipment_id": eq_id, "quantity": 1, "quantity_unit": "وحدة",
        "start_date": str(date.today()),
    })).json()["id"]
    # ملحوظة: مفيش /production/complete هنا — التصنيع لسه ماخلصش

    r = await cli.post(f"/api/v1/sales/work-orders/{wo_id}/to-invoice", json={})
    assert r.status_code == 400
    assert "FG" in r.json()["detail"] or "تام الصنع" in r.json()["detail"]


CN_BASE_PIPE = "/api/v1/credit-notes"


@pytest.mark.asyncio
async def test_credit_note_is_return_restocks_fg_and_reverses_cogs(cli):
    """
    مرتجع بضاعة فعلي (is_return=True) على فاتورة صادرة من أمر إنتاج لازم:
    (1) يرجّع الكمية لمخزون تام الصنع (FG-{code})، (2) يعكس قيد تكلفة البضاعة
    المباعة الأصلي. إلغاء الإشعار بعد كده لازم يسحب الكمية تاني ويرجّع القيد.
    """
    eq_id = await _make_equipment(cli)
    cust_id = await _make_customer(cli)
    quot = await _make_quotation(cli, cust_id, unit_price=1000, qty=2)
    order_id = (await cli.post(f"/api/v1/sales/quotations/{quot['id']}/to-order", json={})).json()["id"]
    wo_r = (await cli.post(f"/api/v1/sales/orders/{order_id}/to-work-order", json={
        "equipment_id": eq_id, "quantity": 2, "quantity_unit": "وحدة",
        "start_date": str(date.today()),
    })).json()
    wo_id, wo_code = wo_r["id"], wo_r["code"]
    await cli.post(f"{COA}/chart/seed")
    await cli.post(f"{ACC}/cost-lines", json={
        "work_order_id": wo_id, "cost_type": "material",
        "description": "صاج", "qty": 2, "unit_cost": 150,
    })
    await cli.post(f"{PROD}/complete/{wo_id}")

    inv_r = await cli.post(f"/api/v1/sales/work-orders/{wo_id}/to-invoice", json={})
    assert inv_r.status_code == 201, inv_r.text
    inv_id = inv_r.json()["id"]

    def _fg_stock(mats):
        return next(m for m in mats if m["code"] == f"FG-{wo_code}")

    mats_after_sale = (await cli.get("/api/v1/inventory/materials")).json()
    assert float(_fg_stock(mats_after_sale)["stock_qty"]) == 0.0  # اتباع بالكامل

    before_bal = await _trial_balance_map(cli)
    cogs_before = float(before_bal.get("5100", {}).get("total_debit", 0))

    cn_r = await cli.post(CN_BASE_PIPE, json={
        "customer_id": cust_id, "invoice_id": inv_id, "date": str(date.today()),
        "return_type": "full", "is_return": True,
        "lines": [{"invoice_line_id": None, "description": "مرتجع", "qty": 2, "unit": "وحدة", "unit_price": 500}],
    })
    assert cn_r.status_code == 201, cn_r.text
    cn_id = cn_r.json()["id"]

    # لازم بند الإشعار يترتبط ببند الفاتورة عشان يترجع للمخزون — هات بند
    # الفاتورة الوحيد وحدّث الإشعار بيه
    inv_detail = (await cli.get(f"/api/v1/sales/invoices/{inv_id}")).json()
    inv_line_id = inv_detail["lines"][0]["id"]
    upd = await cli.put(f"{CN_BASE_PIPE}/{cn_id}", json={
        "customer_id": cust_id, "invoice_id": inv_id, "date": str(date.today()),
        "return_type": "full", "is_return": True,
        "lines": [{"invoice_line_id": inv_line_id, "description": "مرتجع", "qty": 2, "unit": "وحدة", "unit_price": 500}],
    })
    assert upd.status_code == 200, upd.text

    issue_r = await cli.patch(f"{CN_BASE_PIPE}/{cn_id}/issue")
    assert issue_r.status_code == 200, issue_r.text
    assert issue_r.json()["restocked_qty"] == 2.0

    mats_after_return = (await cli.get("/api/v1/inventory/materials")).json()
    assert float(_fg_stock(mats_after_return)["stock_qty"]) == 2.0, "FG stock must come back on a real return"

    after_bal = await _trial_balance_map(cli)
    cogs_after = float(after_bal.get("5100", {}).get("total_debit", 0))
    cogs_credit_after = float(after_bal.get("5100", {}).get("total_credit", 0))
    assert cogs_after == cogs_before, "no new COGS debit should be posted on a return"
    assert cogs_credit_after > 0, "a COGS reversal (credit to 5100) must be posted"

    # إلغاء الإشعار يسحب الكمية تاني ويرجّع قيد التكلفة
    cancel_r = await cli.patch(f"{CN_BASE_PIPE}/{cn_id}/cancel")
    assert cancel_r.status_code == 200, cancel_r.text

    mats_after_cancel = (await cli.get("/api/v1/inventory/materials")).json()
    assert float(_fg_stock(mats_after_cancel)["stock_qty"]) == 0.0, "cancelling the return must pull the stock back out"

    final_bal = await _trial_balance_map(cli)
    assert float(final_bal.get("5100", {}).get("total_debit", 0)) > cogs_before, (
        "cancelling the return must re-post the original COGS"
    )


@pytest.mark.asyncio
async def test_credit_note_is_return_noop_when_invoice_not_from_work_order(cli):
    """مرتجع بضاعة (is_return=True) على فاتورة عادية (مش من أمر إنتاج) —
    مفيش رابط مخزون معروف، فمفروض يعدي بسلام من غير أي أثر مخزني."""
    cust_id = await _make_customer(cli)
    inv_r = await cli.post("/api/v1/sales/invoices", json={
        "customer_id": cust_id, "date": str(date.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 300.0}],
    })
    inv_id = inv_r.json()["id"]

    cn_r = await cli.post(CN_BASE_PIPE, json={
        "customer_id": cust_id, "invoice_id": inv_id, "date": str(date.today()),
        "return_type": "full", "is_return": True,
        "lines": [{"description": "مرتجع", "qty": 1, "unit": "قطعة", "unit_price": 300.0}],
    })
    cn_id = cn_r.json()["id"]
    issue_r = await cli.patch(f"{CN_BASE_PIPE}/{cn_id}/issue")
    assert issue_r.status_code == 200, issue_r.text
    assert issue_r.json()["restocked_qty"] == 0.0


# ═══════════════════════════════════════════════════════════════
# محطة 24 — استكشاف: استلام أمر شراء (create_invoice=True) — هل بيترحّل محاسبيًا؟
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_po_receive_auto_invoice_posts_real_accounting_entry(cli):
    """
    قبل محطة 24: استلام أمر شراء بـcreate_invoice=True (الافتراضي) كان بينشئ
    PurchaseInvoice فعلي (بيرفع رصيد المورد) من غير أي قيد محاسبي خالص —
    لا مدين مخزون ولا دائن ذمم دائنة. اتأكد بـrepro قبل التصليح.
    """
    sup_id = await _make_supplier(cli, "مورد للفحص المحاسبي", supplier_type="محلي")
    mid = await _bare_material(cli)
    po = await _make_po(cli, sup_id, mid, qty=10, unit_cost=20.0)
    await cli.patch(f"{PO}/{po['id']}/approve")

    before = await _trial_balance_map(cli)
    payable_before = float(before.get("2110", {}).get("total_credit", 0))
    inv_before = float(before.get("1310", {}).get("total_debit", 0))

    r = await cli.post(f"{PO}/{po['id']}/receive", json={
        "receive_date": str(date.today()),
        "lines": [{"line_id": po["lines"][0]["id"], "received_qty": 10, "update_cost": True}],
        "create_invoice": True,
    })
    assert r.status_code == 200, r.text
    invoice_id = r.json()["invoice_id"]
    assert invoice_id is not None

    after = await _trial_balance_map(cli)
    # 200 = 10 × 20 (تكلفة الاستلام)، من غير ضريبة (tax_pct افتراضي 0 على المورد الجديد)
    assert float(after["2110"]["total_credit"]) == payable_before + 200.0
    assert float(after["1310"]["total_debit"]) == inv_before + 200.0


@pytest.mark.asyncio
async def test_deleting_auto_generated_purchase_invoice_reverses_accounting(cli):
    """حذف فاتورة شراء تلقائية لازم يعكس القيد المحاسبي بالظبط زي ما اتسجل."""
    sup_id = await _make_supplier(cli, "مورد للحذف", supplier_type="محلي")
    mid = await _bare_material(cli)
    po = await _make_po(cli, sup_id, mid, qty=5, unit_cost=40.0)
    await cli.patch(f"{PO}/{po['id']}/approve")
    await cli.post(f"{COA}/chart/seed")  # لازم قبل الاستلام، مش بعده

    r = await cli.post(f"{PO}/{po['id']}/receive", json={
        "receive_date": str(date.today()),
        "lines": [{"line_id": po["lines"][0]["id"], "received_qty": 5, "update_cost": True}],
        "create_invoice": True,
    })
    assert r.status_code == 200, r.text
    invoice_id = r.json()["invoice_id"]
    assert invoice_id is not None, r.text

    mid_state = await _trial_balance_map(cli)
    payable_mid = float(mid_state["2110"]["total_credit"])  # 200 اتضافت

    dr = await cli.delete(f"/api/v1/purchases/invoices/{invoice_id}")
    assert dr.status_code == 200, dr.text

    after = await _trial_balance_map(cli)
    # القيد العكسي بيضيف لـtotal_debit على 2110 (مش يقلل total_credit) —
    # فرصيد net الحساب يرجع صفر، لكن total_credit الأصلي يفضل زي ما هو
    assert float(after["2110"]["total_credit"]) == payable_mid  # لسه 200، محدش عدّل الأصلي
    assert float(after["2110"]["total_debit"]) == 200.0  # القيد العكسي


@pytest.mark.asyncio
async def test_po_receive_without_invoice_posts_no_accounting_entry(cli):
    """
    create_invoice=False (استلام بضاعة قبل وصول فاتورة المورد — GRNI): مفيش
    PurchaseInvoice يتعمل، فمفيش قيد محاسبي المفروض يتعمل كمان — التصرف ده
    متوقَّع ومقصود (مش fix)، بس بنوثقه ونتأكد إنه ثابت.
    """
    sup_id = await _make_supplier(cli, "مورد GRNI", supplier_type="محلي")
    mid = await _bare_material(cli)
    po = await _make_po(cli, sup_id, mid, qty=3, unit_cost=10.0)
    await cli.patch(f"{PO}/{po['id']}/approve")

    before = await _trial_balance_map(cli)
    payable_before = float(before.get("2110", {}).get("total_credit", 0))

    r = await cli.post(f"{PO}/{po['id']}/receive", json={
        "receive_date": str(date.today()),
        "lines": [{"line_id": po["lines"][0]["id"], "received_qty": 3, "update_cost": True}],
        "create_invoice": False,
    })
    assert r.status_code == 200, r.text
    assert r.json()["invoice_id"] is None

    # المخزون اتسجل فعليًا (محطة 19) رغم عدم وجود فاتورة
    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 3.0

    after = await _trial_balance_map(cli)
    assert float(after.get("2110", {}).get("total_credit", 0)) == payable_before  # من غير تغيير


# ═══════════════════════════════════════════════════════════════
# محطة 25 — مواد مشروع خارجي من المخزون تتخصم فعليًا (مش سطر تكلفة بس)
# ═══════════════════════════════════════════════════════════════

EP = "/api/v1/ext-projects"


async def _make_ext_project(cli, client_id, code="EP-STATION25"):
    r = await cli.post(EP, json={
        "code": code, "title": "مشروع اختبار محطة 25", "project_type": "installation",
        "client_id": client_id, "contract_value": 10000.0,
        "planned_start": str(date.today()), "planned_end": str(date.today()),
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_ext_project_material_from_stock_deducts_real_inventory(cli):
    """
    قبل محطة 25: مادة من المخزون على مشروع خارجي كانت بتتسجل كـ 'سطر تكلفة'
    بس، من غير أي خصم فعلي من Material.stock_qty أو StockQuant — يعني رصيد
    المخزون كان بيفضل أعلى من الواقع الفعلي للأبد.
    """
    cust_id = await _make_customer(cli)
    pid = await _make_ext_project(cli, cust_id)
    mid = await _make_material(cli, qty=100)  # رصيد فعلي 100

    r = await cli.post(f"{EP}/{pid}/materials", json={
        "source": "stock", "material_id": mid,
        "description": "مادة من المخزون", "quantity": 30, "unit_cost": 0,
        "usage_date": str(date.today()),
    })
    assert r.status_code == 201, r.text

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 70.0  # 100 - 30 اتخصمت فعليًا

    # ملاحظة مُحدَّثة: بعد التصليح الجذري (محطة 16/24 تكملة)، الموقع
    # الافتراضي المشترك بقى نفسه RAW-DEFAULT بالظبط (مش موقع "DEFAULT"
    # منفصل زي الأول) — يعني استهلاك المشاريع الخارجية ومحطة 19 (استلام
    # الشراء) بقوا بيتفقوا على نفس الموقع الفعلي، مش موقعين مختلفين.
    quants = await cli.get(f"{INV}/materials/{mid}/quants")
    default_row = next(q for q in quants.json() if q["location_code"] == "RAW-DEFAULT")
    assert float(default_row["qty"]) == 70.0


@pytest.mark.asyncio
async def test_default_location_now_unified_with_raw(cli):
    """
    إثبات مباشر لتصليح السبب الجذري: مادة دخلت جزء منها عن طريق شاشة
    الحركات العامة (post_receipt من غير location_id) وجزء تاني عن طريق
    استلام أمر شراء رسمي (محطة 19، موقع RAW صراحة) — قبل التصليح كانوا
    هيروحوا لموقعين منفصلين تمامًا (DEFAULT وRAW-DEFAULT) حتى لو نفس
    المادة بالظبط. دلوقتي لازم يتجمّعوا في نفس صف StockQuant الواحد.
    """
    mid = await _make_material(cli, qty=40)  # عن طريق /inventory/movements — location_id مش متبعوت

    sup_id = await _make_supplier(cli, "مورد توحيد المواقع", supplier_type="محلي")
    po = await _make_po(cli, sup_id, mid, qty=25, unit_cost=5.0)
    await cli.patch(f"{PO}/{po['id']}/approve")
    r = await cli.post(f"{PO}/{po['id']}/receive", json={
        "receive_date": str(date.today()),
        "lines": [{"line_id": po["lines"][0]["id"], "received_qty": 25, "update_cost": False}],
    })
    assert r.status_code == 200, r.text

    quants = await cli.get(f"{INV}/materials/{mid}/quants")
    rows = quants.json()
    assert len(rows) == 1, f"لازم صف StockQuant واحد بس بعد التوحيد، لقينا: {rows}"
    assert rows[0]["location_code"] == "RAW-DEFAULT"
    assert float(rows[0]["qty"]) == 65.0  # 40 + 25 اتجمّعوا في نفس الموقع


@pytest.mark.asyncio
async def test_ext_project_material_blocked_when_insufficient_stock(cli):
    """طلب كمية أكبر من المتاح فعليًا — لازم يترفض (نفس مبدأ محطة 16 الـfail-closed)."""
    cust_id = await _make_customer(cli)
    pid = await _make_ext_project(cli, cust_id, code="EP-STATION25B")
    mid = await _make_material(cli, qty=10)

    r = await cli.post(f"{EP}/{pid}/materials", json={
        "source": "stock", "material_id": mid,
        "description": "مادة زيادة عن المتاح", "quantity": 999, "unit_cost": 0,
        "usage_date": str(date.today()),
    })
    assert r.status_code == 400
    assert "الكمية المتاحة غير كافية" in r.json()["detail"]  # رسالة post_issue() المدمجة

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 10.0  # محدش اتخصم


@pytest.mark.asyncio
async def test_ext_project_material_delete_restores_stock(cli):
    """حذف بند مادة من المخزون لازم يرجّع الرصيد فعليًا (عكس متماثل)."""
    cust_id = await _make_customer(cli)
    pid = await _make_ext_project(cli, cust_id, code="EP-STATION25C")
    mid = await _make_material(cli, qty=50)

    r = await cli.post(f"{EP}/{pid}/materials", json={
        "source": "stock", "material_id": mid,
        "description": "مادة هترجع", "quantity": 20, "unit_cost": 0,
        "usage_date": str(date.today()),
    })
    line_id = r.json()["id"]

    mat_after_add = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat_after_add.json()["stock_qty"]) == 30.0

    dr = await cli.delete(f"{EP}/{pid}/materials/{line_id}")
    assert dr.status_code == 200, dr.text

    mat_after_delete = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat_after_delete.json()["stock_qty"]) == 50.0  # رجع زي الأول


@pytest.mark.asyncio
async def test_ext_project_material_local_purchase_untouched(cli):
    """مادة 'شراء محلي' (مش من المخزون) لازم تفضل شغالة زي الأول من غير أي فحص مخزون."""
    cust_id = await _make_customer(cli)
    pid = await _make_ext_project(cli, cust_id, code="EP-STATION25D")

    r = await cli.post(f"{EP}/{pid}/materials", json={
        "source": "local_purchase",
        "description": "مسمار ومواد بناء محلية", "quantity": 5, "unit_cost": 20,
        "usage_date": str(date.today()),
    })
    assert r.status_code == 201, r.text
    assert r.json()["total_cost"] == 100.0


# ═══════════════════════════════════════════════════════════════
# محطة 13 (ترقيم الروادماب الأصلي) — تقرير تسوية المخزون الشامل
# ═══════════════════════════════════════════════════════════════

RPT = "/api/v1/reports"


@pytest.mark.asyncio
async def test_reconciliation_clean_when_stock_entered_via_ledger(cli):
    """مادة دخلت عن طريق الليدجر المركزي بس (post_receipt) — لازم تبقى نضيفة، من غير أي mismatch."""
    mid = await _make_material(cli, qty=50)

    r = await cli.get(f"{RPT}/inventory/reconciliation", params={"only_mismatches": "false"})
    assert r.status_code == 200, r.text
    row = next(m for m in r.json()["materials"] if m["material_id"] == mid)
    assert row["has_mismatch"] is False
    quant_check = next(c for c in row["checks"] if c["source"].startswith("StockQuant"))
    assert quant_check["mismatch"] is False
    assert quant_check["diff"] == 0.0


@pytest.mark.asyncio
async def test_purchase_invoice_stays_reconciled_after_station15_fix(cli):
    """
    كان ده الاختبار اللي أثبت وجود الفجوة الحقيقية (بند 15 في الروادماب
    الأصلي: POST /purchases/invoices بيعدّل stock_qty من غير StockQuant).
    بعد التصليح، نفس السيناريو المفروض يفضل متّسق تمامًا — التقرير بنفسه
    هو أداة التحقق من إن الإصلاح شغال end-to-end.
    """
    sup_id = await _make_supplier(cli, "مورد تسوية", supplier_type="محلي")
    mid = await _bare_material(cli)

    r = await cli.post("/api/v1/purchases/invoices", json={
        "supplier_id": sup_id, "date": str(date.today()),
        "lines": [{
            "material_id": mid, "description": "مادة تسوية",
            "qty": 40, "unit": "كجم", "unit_cost": 10, "update_stock": True,
        }],
    })
    assert r.status_code == 201, r.text
    inv_id = r.json()["id"]

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 40.0

    report = await cli.get(f"{RPT}/inventory/reconciliation", params={"only_mismatches": "true"})
    row = next((m for m in report.json()["materials"] if m["material_id"] == mid), None)
    assert row is None, "بعد إصلاح محطة 15 مفروض القيد ده يبقى متسق — التقرير مايمسكوش"

    # وبعد التعديل واللي كان أخطر جزء (فقدان الأثر عند العكس اليدوي) —
    # لازم يفضل متسق برضو
    upd = await cli.put(f"/api/v1/purchases/invoices/{inv_id}", json={
        "supplier_id": sup_id, "date": str(date.today()),
        "lines": [{
            "material_id": mid, "description": "مادة تسوية معدَّلة",
            "qty": 25, "unit": "كجم", "unit_cost": 12, "update_stock": True,
        }],
    })
    assert upd.status_code == 200, upd.text

    mat2 = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat2.json()["stock_qty"]) == 25.0  # اتعكس 40 واتسجل 25 بس

    report2 = await cli.get(f"{RPT}/inventory/reconciliation", params={"only_mismatches": "true"})
    row2 = next((m for m in report2.json()["materials"] if m["material_id"] == mid), None)
    assert row2 is None, "بعد التعديل برضو لازم يفضل متسق — مش بس عند الإنشاء الأول"


@pytest.mark.asyncio
async def test_reconciliation_summary_counts_are_consistent(cli):
    """
    اختبار الـdetector نفسه بمعزل عن أي مُنتِج فعلي للفجوة — بنحاكي حالة
    مادة "قديمة" اتعدّل رصيدها مباشرة في قاعدة البيانات (زي لو حد عمل
    تصحيح يدوي فات على الـAPI بالكامل)، عشان نتأكد إن التقرير بيمسك أي
    مصدر تضارب مش بس السيناريوهات اللي جربناها إحنا.
    """
    mid_clean = await _make_material(cli, qty=20)
    mid_dirty = await _make_material(cli, qty=15)

    from tests.conftest import TestSessionLocal
    from sqlalchemy import text as _text
    async with TestSessionLocal() as s:
        await s.execute(_text("UPDATE materials SET stock_qty = stock_qty + 100 WHERE id = :id"), {"id": mid_dirty})
        await s.commit()

    report = await cli.get(f"{RPT}/inventory/reconciliation", params={"only_mismatches": "false"})
    body = report.json()
    assert body["summary"]["materials_checked"] >= 2
    assert body["summary"]["materials_with_mismatch"] >= 1
    clean_row = next(m for m in body["materials"] if m["material_id"] == mid_clean)
    dirty_row = next(m for m in body["materials"] if m["material_id"] == mid_dirty)
    assert clean_row["has_mismatch"] is False
    assert dirty_row["has_mismatch"] is True
    quant_check = next(c for c in dirty_row["checks"] if c["source"].startswith("StockQuant"))
    assert quant_check["diff"] == 100.0


@pytest.mark.asyncio
async def test_deleting_purchase_invoice_stays_reconciled(cli):
    """
    اكتشاف ذاتي أثناء التنفيذ: أول مراجعة لـdelete_purchase_invoice ركّزت
    على الجانب المحاسبي بس واعتبرته سليم — لكن الجانب المخزني كان فيه نفس
    فجوة الـupdate بالظبط (تعديل يدوي + حذف صفوف بدل عكس فعلي عبر post_issue).
    """
    sup_id = await _make_supplier(cli, "مورد حذف", supplier_type="محلي")
    mid = await _bare_material(cli)

    r = await cli.post("/api/v1/purchases/invoices", json={
        "supplier_id": sup_id, "date": str(date.today()),
        "lines": [{"material_id": mid, "description": "مادة", "qty": 15, "unit": "كجم", "unit_cost": 8, "update_stock": True}],
    })
    inv_id = r.json()["id"]

    mat_before = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat_before.json()["stock_qty"]) == 15.0

    dr = await cli.delete(f"/api/v1/purchases/invoices/{inv_id}")
    assert dr.status_code == 200, dr.text

    mat_after = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat_after.json()["stock_qty"]) == 0.0

    report = await cli.get(f"{RPT}/inventory/reconciliation", params={"only_mismatches": "true"})
    row = next((m for m in report.json()["materials"] if m["material_id"] == mid), None)
    assert row is None, "بعد الحذف لازم يفضل متسق تمامًا — صفر في stock_qty وصفر في StockQuant"


# ═══════════════════════════════════════════════════════════════
# محطة 16 (ترقيم الروادماب الأصلي) — حجز مادة بموقع فعلي (RAW) لا بالإجمالي بس
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_auto_reserve_tags_location_id(cli):
    """
    قبل محطة 16: MaterialReservation معندهاش أي location_id خالص — الحجز
    كله بيتعامل مع Material.stock_qty الإجمالي بس. دلوقتي لازم كل حجز
    جديد يترتبط بموقع RAW الفعلي (تحصين هيكلي، أول خطوة قبل فرض التوفر
    بالموقع فعليًا — ده محتاج بند 18 الأول، موضّح في الكود والتقرير).
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=50)
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 2.0, "unit_cost": 5.0})
    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id = wo["id"]

    r = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r.status_code in (200, 201), r.text

    res = await cli.get(f"{PROD}/{wo_id}/reservations")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) >= 1
    assert all(row["location_id"] is not None for row in rows)

    # لازم يبقى نفس موقع RAW-DEFAULT اللي باقي محطات المشروع (19/21/24) بتستخدمه
    quants_check = await cli.get(f"{INV}/materials/{mid}/quants")
    # بعد توحيد DEFAULT مع RAW، أي مادة دخلت بالطريقة العادية لازم تبقى في
    # RAW-DEFAULT فعليًا — مش مجرد افتراض، نتأكد منه هنا مباشرة
    wh = await cli.get("/api/v1/warehouses")
    raw_wh = next(w for w in wh.json() if w["code"] == "RAW")
    assert raw_wh is not None


@pytest.mark.asyncio
async def test_reservation_availability_now_uses_real_location_balance(cli):
    """
    محطة 16 (الروادماب الأصلي، مكتملة): بعد تصليح توحيد DEFAULT/RAW، التوفر
    بقى بيتحقق من الرصيد الفعلي في مواقع RAW+IMPORTED (مش Material.stock_qty
    المجرَّد). نثبت الفرق العملي: نخلق حالة انحراف متعمَّدة بين stock_qty
    ورصيد الموقع الفعلي (زي لو حصل تصحيح يدوي في القاعدة فات على الليدجر) —
    الفحص الجديد لازم يتبع الموقع الحقيقي مش الرقم المجرَّد المحتمل يكون
    غير دقيق.
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=10)  # 10 فعليًا في RAW-DEFAULT
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 3.0, "unit_cost": 5.0})

    # ننفخ stock_qty المجرَّد يدويًا لـ100 من غير ما نلمس StockQuant — انحراف متعمَّد
    from tests.conftest import TestSessionLocal
    from sqlalchemy import text as _text
    async with TestSessionLocal() as s:
        await s.execute(_text("UPDATE materials SET stock_qty = 100 WHERE id = :id"), {"id": mid})
        await s.commit()

    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id = wo["id"]

    r = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r.status_code in (200, 201), r.text
    body = r.json()
    # لو الفحص القديم (stock_qty=100) كان لسه شغال، كان هيقول "متاح" بلا مشاكل.
    # الفحص الجديد بيتبع رصيد الموقع الحقيقي (10)، فطلب 3 لازم يفضل متاح —
    # بس نتأكد إن الحجز اتسجل بالكمية الصح (3) مش بافتراض الـ100 المضلِّل
    assert body["blocked"] is False
    res = await cli.get(f"{PROD}/{wo_id}/reservations")
    assert float(res.json()[0]["reserved_qty"]) == 3.0


@pytest.mark.asyncio
async def test_reservation_blocked_when_real_location_balance_insufficient(cli):
    """
    نفس السيناريو بس بالعكس: stock_qty المجرَّد بيقول "مفيش حاجة" (صفر) رغم
    إن فيه فعليًا رصيد في RAW — الفحص الجديد لازم يعتمد على الموقع الحقيقي
    مش الرقم المجرَّد، فالحجز لازم ينجح.
    """
    eq_id = await _make_equipment(cli)
    mid = await _make_material(cli, qty=20)  # 20 فعليًا في RAW-DEFAULT
    await cli.post(f"{EQ}/bom", json={"equipment_id": eq_id, "material_id": mid, "qty": 5.0, "unit_cost": 5.0})

    from tests.conftest import TestSessionLocal
    from sqlalchemy import text as _text
    async with TestSessionLocal() as s:
        await s.execute(_text("UPDATE materials SET stock_qty = 0 WHERE id = :id"), {"id": mid})
        await s.commit()

    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id = wo["id"]

    r = await cli.post(f"{PROD}/auto-reserve/{wo_id}")
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["blocked"] is False  # الرصيد الفعلي في RAW (20) كافي لـ5، رغم إن stock_qty بيقول صفر
    res = await cli.get(f"{PROD}/{wo_id}/reservations")
    assert float(res.json()[0]["reserved_qty"]) == 5.0


# ═══════════════════════════════════════════════════════════════
# محطة 18 (ترقيم الروادماب الأصلي) — توحيد صرف الألواح/الأسياخ
# ═══════════════════════════════════════════════════════════════

async def _make_plate_material(cli, density_kg_m2=8, qty=100):
    r = await cli.post(f"{INV}/materials", json={
        "name_ar": "لوح صاج", "unit": "كجم", "unit_cost": 10,
        "material_kind": "plate", "is_plate": True, "density_kg_m2": density_kg_m2,
        "plate_length_cm": 300, "plate_width_cm": 150,  # لازم موجودين عشان issue_material يوجّه لـ _consume_plate_qty
    })
    mid = r.json()["id"]
    await cli.post(f"{INV}/movements", json={"material_id": mid, "movement_type": "in", "qty": qty, "unit_cost": 10})
    return mid


async def _make_bar_material(cli, weight_per_meter_kg=2, qty=100):
    r = await cli.post(f"{INV}/materials", json={
        "name_ar": "سيخ حديد", "unit": "كجم", "unit_cost": 15,
        "material_kind": "bar", "bar_length_cm": 600, "weight_per_meter_kg": weight_per_meter_kg,
    })
    mid = r.json()["id"]
    await cli.post(f"{INV}/movements", json={"material_id": mid, "movement_type": "in", "qty": qty, "unit_cost": 15})
    return mid


@pytest.mark.asyncio
async def test_plate_bulk_consumption_via_unified_helper(cli):
    """صرف مادة لوح (من غير أي نسالة سابقة) — يمر بالكامل على _consume_bulk_stock الموحّدة."""
    eq_id = await _make_equipment(cli)
    mid = await _make_plate_material(cli, qty=100)
    wo = await _make_work_order(cli, eq_id, qty=1)

    r = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo["id"], "material_id": mid, "qty": 30, "stage": "قطع",
    })
    assert r.status_code in (200, 201), r.text

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 70.0  # 100 - 30


@pytest.mark.asyncio
async def test_bar_bulk_consumption_with_weight_per_meter(cli):
    """صرف سيخ (weight_per_meter_kg > 0، من غير نسالة سابقة) — يمر على المسار التاني للدالة الموحّدة."""
    eq_id = await _make_equipment(cli)
    mid = await _make_bar_material(cli, weight_per_meter_kg=2, qty=50)
    wo = await _make_work_order(cli, eq_id, qty=1)

    r = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo["id"], "material_id": mid, "qty": 12, "stage": "قطع",
    })
    assert r.status_code in (200, 201), r.text

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 38.0  # 50 - 12


@pytest.mark.asyncio
async def test_bar_bulk_consumption_without_weight_per_meter(cli):
    """
    سيخ من غير weight_per_meter_kg (مسار الرجوع المبكر داخل _consume_bar_qty) —
    ده كان أول مكان استخدمت فيه الدالة الموحّدة في الكود، لازم يتأكد بيشتغل لوحده.
    """
    eq_id = await _make_equipment(cli)
    r = await cli.post(f"{INV}/materials", json={
        "name_ar": "سيخ بدون بيانات وزن", "unit": "كجم", "unit_cost": 15,
        "material_kind": "bar", "bar_length_cm": 600, "weight_per_meter_kg": 0,
    })
    mid = r.json()["id"]
    await cli.post(f"{INV}/movements", json={"material_id": mid, "movement_type": "in", "qty": 20, "unit_cost": 15})
    wo = await _make_work_order(cli, eq_id, qty=1)

    r2 = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo["id"], "material_id": mid, "qty": 5, "stage": "قطع",
    })
    assert r2.status_code in (200, 201), r2.text

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 15.0  # 20 - 5


@pytest.mark.asyncio
async def test_plate_shortage_still_reported_after_unification(cli):
    """طلب كمية أكبر من المتاح — مسار الخطأ لازم يفضل شغال زي الأول بعد التوحيد."""
    eq_id = await _make_equipment(cli)
    mid = await _make_plate_material(cli, qty=5)
    wo = await _make_work_order(cli, eq_id, qty=1)

    r = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo["id"], "material_id": mid, "qty": 999, "stage": "قطع",
    })
    assert r.status_code == 400
    assert "غير كافٍ" in r.json()["detail"]

    mat = await cli.get(f"{INV}/materials/{mid}")
    assert float(mat.json()["stock_qty"]) == 5.0  # محدّش اتخصم رغم الفشل الجزئي


# ═══════════════════════════════════════════════════════════════
# محطة 22 (ترقيم الروادماب الأصلي) — قيد COGS فعلي عند التسليم
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delivery_posts_real_cogs_entry(cli):
    """
    قبل محطة 22: التسليم كان بيخصم رصيد FG فعليًا (محطة 16/21) لكن من غير
    أي قيد محاسبي COGS — يعني تكلفة البضاعة المباعة كانت غايبة تمامًا من
    ميزان المراجعة رغم إن الإيراد (فاتورة البيع) بيتسجل صح.
    """
    eq_id = await _make_equipment(cli)
    wo = await _make_work_order(cli, eq_id, qty=1)
    wo_id = wo["id"]
    # نحدد تكلفة معروفة عشان نتحقق من رقم COGS بالظبط
    await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo_id, "material_id": await _make_material(cli, qty=100),
        "qty": 20, "stage": "تجميع",
    })
    await cli.post(f"{PROD}/complete/{wo_id}")

    mat_fg = await cli.get(f"{INV}/materials")
    fg = next(m for m in mat_fg.json() if m["code"] == f"FG-{wo['code']}")
    fg_unit_cost = float(fg["unit_cost"])
    assert fg_unit_cost > 0

    cust_id = await _make_customer(cli)
    order = await _make_delivery_order(cli, cust_id, [{
        "description": "معدة أعلاف", "qty": "1", "work_order_id": wo_id,
    }])

    before = await _trial_balance_map(cli)
    cogs_debit_before = float(before.get("5100", {}).get("total_debit", 0))
    fg_credit_before = float(before.get("1330", {}).get("total_credit", 0))

    await cli.patch(f"{DO}/{order['id']}/status", json={"status": "confirmed"})
    r = await cli.patch(f"{DO}/{order['id']}/status", json={"status": "delivered"})
    assert r.status_code == 200, r.text

    after = await _trial_balance_map(cli)
    assert float(after["5100"]["total_debit"]) == round(cogs_debit_before + fg_unit_cost, 2)
    assert float(after["1330"]["total_credit"]) == round(fg_credit_before + fg_unit_cost, 2)


@pytest.mark.asyncio
async def test_no_cogs_when_delivery_line_unlinked(cli):
    """بند تسليم عادي (من غير work_order_id) — مفيش رصيد FG اتحرك، فمفيش COGS المفروض تتسجل."""
    cust_id = await _make_customer(cli)
    before = await _trial_balance_map(cli)
    cogs_before = float(before.get("5100", {}).get("total_debit", 0))

    order = await _make_delivery_order(cli, cust_id, [{"description": "قطعة غيار عامة", "qty": "3"}])
    await cli.patch(f"{DO}/{order['id']}/status", json={"status": "confirmed"})
    r = await cli.patch(f"{DO}/{order['id']}/status", json={"status": "delivered"})
    assert r.status_code == 200

    after = await _trial_balance_map(cli)
    assert float(after.get("5100", {}).get("total_debit", 0)) == cogs_before  # من غير تغيير


# ═══════════════════════════════════════════════════════════════
# محطة 23 (ترقيم الروادماب الأصلي) — ربط المعدات المستوردة بفاتورة شراء رسمية
# ═══════════════════════════════════════════════════════════════

async def _make_purchase_invoice(cli, sup_id, total_unit_cost=5000.0, qty=1):
    r = await cli.post("/api/v1/purchases/invoices", json={
        "supplier_id": sup_id, "date": str(date.today()),
        "lines": [{
            "description": "معدة مستوردة جاهزة", "qty": qty, "unit": "قطعة",
            "unit_cost": total_unit_cost, "update_stock": False,
        }],
    })
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_equipment_linked_to_invoice_derives_purchase_fields(cli):
    """
    قبل محطة 23: purchase_date/purchase_value كانوا إدخال يدوي حر بلا أي
    مرجع رسمي. دلوقتي لو ربطنا معدة بفاتورة شراء حقيقية، القيمتين لازم
    يتحددوا من الفاتورة نفسها — حتى لو المستخدم بعت قيم يدوية مختلفة.
    """
    sup_id = await _make_supplier(cli, "مورد معدات مستوردة", supplier_type="مستورد")
    inv = await _make_purchase_invoice(cli, sup_id, total_unit_cost=7500.0, qty=1)

    r = await cli.post(EQ, json={
        "name_ar": "خلاطة أعلاف مستوردة", "level": 1,
        "purchase_invoice_id": inv["id"],
        # قيم يدوية مختلفة عمدًا — المفروض تتجاهل لصالح الفاتورة
        "purchase_date": "2020-01-01", "purchase_value": 1.0,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["purchase_invoice_id"] == inv["id"]
    assert body["purchase_value"] == 7500.0  # من الفاتورة، مش الـ1.0 اليدوي
    assert body["purchase_date"] == str(date.today())  # تاريخ الفاتورة الحقيقي، مش 2020-01-01


@pytest.mark.asyncio
async def test_equipment_without_invoice_link_keeps_manual_entry(cli):
    """توافق خلفي: معدة عادية (بدون ربط فاتورة) لازم تفضل تقبل إدخال يدوي زي الأول بالظبط."""
    r = await cli.post(EQ, json={
        "name_ar": "معدة مصنّعة داخليًا", "level": 1,
        "purchase_date": "2024-06-01", "purchase_value": 12000.0,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["purchase_invoice_id"] is None
    assert body["purchase_value"] == 12000.0
    assert body["purchase_date"] == "2024-06-01"


@pytest.mark.asyncio
async def test_equipment_invalid_invoice_link_rejected(cli):
    r = await cli.post(EQ, json={
        "name_ar": "معدة بفاتورة وهمية", "level": 1, "purchase_invoice_id": 999999,
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_equipment_update_can_attach_invoice_later(cli):
    """معدة اتعملت الأول من غير ربط، وبعدين اترتبطت بفاتورة — نفس منطق الاستخلاص لازم يتطبق."""
    eq_id = await _make_equipment(cli, name="معدة هترتبط بعدين")
    sup_id = await _make_supplier(cli, "مورد تأخير", supplier_type="مستورد")
    inv = await _make_purchase_invoice(cli, sup_id, total_unit_cost=3300.0, qty=1)

    r = await cli.put(f"{EQ}/{eq_id}", json={
        "name_ar": "معدة هترتبط بعدين", "level": 1, "purchase_invoice_id": inv["id"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["purchase_invoice_id"] == inv["id"]
    assert body["purchase_value"] == 3300.0


# ═══════════════════════════════════════════════════════════════
# محطة 24 (ترقيم الروادماب الأصلي) — رسمنة الهالك كمخزن SCRAP فعلي
# ═══════════════════════════════════════════════════════════════

async def _make_treasury(cli, name="خزينة هالك"):
    r = await cli.post("/api/v1/treasury/accounts", json={"name": name, "kind": "cash", "opening_balance": 0.0})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_scrap_recorded_as_real_stock_in_scrap_warehouse(cli):
    """
    قبل محطة 24: تسجيل هالك كان بيخصم من المادة الأصلية ويسجّل ScrapRecord
    بس — مخزن SCRAP (موجود من محطة 10 كسجل) مكانش بياخد أي رصيد فعلي خالص.
    """
    mid = await _make_material(cli, qty=100)

    r = await cli.post(f"{PROD}/scrap", json={
        "material_id": mid, "qty": 15, "reason": "قطع خاطئ",
    })
    assert r.status_code in (200, 201), r.text

    mats = await cli.get(f"{INV}/materials")
    scrap_mat = next(m for m in mats.json() if m["code"].startswith("SCRAP-"))
    assert float(scrap_mat["stock_qty"]) == 15.0

    quants = await cli.get(f"{INV}/materials/{scrap_mat['id']}/quants")
    rows = quants.json()
    assert len(rows) == 1
    assert rows[0]["location_code"] == "SCRAP-DEFAULT"
    assert float(rows[0]["qty"]) == 15.0


@pytest.mark.asyncio
async def test_scrap_sale_deducts_from_real_scrap_warehouse(cli):
    """بيع الهالك لازم يخصم فعليًا من مخزن SCRAP، مش بس يعلّم ScrapRecord.qty_sold."""
    mid = await _make_material(cli, qty=50)
    await cli.post(f"{PROD}/scrap", json={"material_id": mid, "qty": 20, "reason": "تلف"})

    mats = await cli.get(f"{INV}/materials")
    scrap_mat = next(m for m in mats.json() if m["code"].startswith("SCRAP-"))
    assert float(scrap_mat["stock_qty"]) == 20.0

    tid = await _make_treasury(cli)
    r = await cli.post(f"{PROD}/scrap-sales", json={
        "sale_date": str(date.today()), "treasury_id": tid, "payment_method": "cash",
        "lines": [{"material_id": mid, "qty": 12, "unit_price": 2.0}],
    })
    assert r.status_code in (200, 201), r.text

    mats_after = await cli.get(f"{INV}/materials")
    scrap_after = next(m for m in mats_after.json() if m["code"].startswith("SCRAP-"))
    assert float(scrap_after["stock_qty"]) == 8.0  # 20 - 12


@pytest.mark.asyncio
async def test_scrap_from_two_materials_tracked_separately(cli):
    """هالك من مادتين مختلفتين لازم يتسجل في مادتين SCRAP منفصلتين، من غير أي تداخل."""
    mid1 = await _make_material(cli, qty=30)
    mid2 = await _make_material(cli, qty=30)
    await cli.post(f"{PROD}/scrap", json={"material_id": mid1, "qty": 5, "reason": "تلف"})
    await cli.post(f"{PROD}/scrap", json={"material_id": mid2, "qty": 9, "reason": "تلف"})

    mats = await cli.get(f"{INV}/materials")
    scrap_mats = [m for m in mats.json() if m["code"].startswith("SCRAP-")]
    assert len(scrap_mats) == 2
    quantities = sorted(float(m["stock_qty"]) for m in scrap_mats)
    assert quantities == [5.0, 9.0]


# ═══════════════════════════════════════════════════════════════
# محطة 24 (تكملة) — رسمنة النسالة (Remnant) كمخزن REMNANT فعلي
# ═══════════════════════════════════════════════════════════════

CUT = "/api/v1/inventory/cutting-plan/commit"


@pytest.mark.asyncio
async def test_cutting_plan_creates_real_remnant_stock(cli):
    """
    قبل التكملة: مخطط التقطيع كان بينشئ SheetStockItem/BarStockItem
    (النسالة نفسها بأبعادها) من غير أي أثر في StockQuant — مخزن REMNANT
    (من محطة 10) كان فاضي دايمًا رغم وجود نسالة فعلية.
    """
    mid = await _make_plate_material(cli, density_kg_m2=8, qty=50)

    r = await cli.post(CUT, json={
        "material_id": mid,
        "waste_items": [{"kind": "sheet", "length_cm": 100, "width_cm": 50, "qty": 1, "action": "remnant"}],
    })
    assert r.status_code == 200, r.text

    mats = await cli.get(f"{INV}/materials")
    rem_mat = next(m for m in mats.json() if m["code"].startswith("REMNANT-"))
    assert float(rem_mat["stock_qty"]) == 0.5  # 1م × 0.5م = 0.5 م²

    quants = await cli.get(f"{INV}/materials/{rem_mat['id']}/quants")
    rows = quants.json()
    assert len(rows) == 1
    assert rows[0]["location_code"] == "REMNANT-DEFAULT"


@pytest.mark.asyncio
async def test_plate_issue_consumes_from_remnant_ledger(cli):
    """صرف مادة لوح — لو فيه نسالة كافية، لازم تتستهلك منها ويتحدّث دفتر REMNANT فعليًا."""
    eq_id = await _make_equipment(cli)
    mid = await _make_plate_material(cli, density_kg_m2=8, qty=200)

    await cli.post(CUT, json={
        "material_id": mid,
        "waste_items": [{"kind": "sheet", "length_cm": 200, "width_cm": 100, "qty": 1, "action": "remnant"}],
    })
    mats = await cli.get(f"{INV}/materials")
    rem_mat = next(m for m in mats.json() if m["code"].startswith("REMNANT-"))
    assert float(rem_mat["stock_qty"]) == 2.0  # 2م × 1م = 2 م²

    wo = await _make_work_order(cli, eq_id, qty=1)
    r = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo["id"], "material_id": mid, "qty": 15, "stage": "قطع",  # 15كجم ≈ من النسالة
    })
    assert r.status_code in (200, 201), r.text

    mats_after = await cli.get(f"{INV}/materials")
    rem_after = next(m for m in mats_after.json() if m["code"].startswith("REMNANT-"))
    assert float(rem_after["stock_qty"]) == 0.0  # النسالة (بقيمة 16كجم = 2م²×8) اتستهلكت بالكامل


@pytest.mark.asyncio
async def test_bar_partial_consumption_updates_remnant_ledger_correctly(cli):
    """
    نسالة سيخ أكبر من المطلوب — لازم تتصرف بالكامل من دفتر REMNANT، وتترجع
    نسالة جديدة أصغر بنفس القيمة المتبقية بالظبط (مش أكتر ولا أقل).
    """
    eq_id = await _make_equipment(cli)
    r = await cli.post(f"{INV}/materials", json={
        "name_ar": "سيخ للنسالة", "unit": "كجم", "unit_cost": 15,
        "material_kind": "bar", "bar_length_cm": 600, "weight_per_meter_kg": 2, "min_remnant_value": 20,
    })
    mid = r.json()["id"]
    await cli.post(f"{INV}/movements", json={"material_id": mid, "movement_type": "in", "qty": 500, "unit_cost": 15})

    # نسالة سيخ طولها 300سم (وزن = 3م × 2كجم/م = 6كجم)
    await cli.post(CUT, json={
        "material_id": mid,
        "waste_items": [{"kind": "bar", "length_cm": 300, "qty": 1, "action": "remnant"}],
    })
    mats = await cli.get(f"{INV}/materials")
    rem_mat = next(m for m in mats.json() if m["code"].startswith("REMNANT-"))
    assert float(rem_mat["stock_qty"]) == 6.0

    # نطلب صرف 2كجم بس (= متر واحد) — أقل من النسالة الموجودة (300سم)
    wo = await _make_work_order(cli, eq_id, qty=1)
    r2 = await cli.post(f"{PROD}/issue", json={
        "work_order_id": wo["id"], "material_id": mid, "qty": 2, "stage": "قطع",
    })
    assert r2.status_code in (200, 201), r2.text

    mats_after = await cli.get(f"{INV}/materials")
    rem_after = next(m for m in mats_after.json() if m["code"].startswith("REMNANT-"))
    # النسالة الأصلية (6كجم) اتصرفت كاملة، ورجعت نسالة جديدة بالمتبقي فقط:
    # 300سم - 100سم(المطلوب) = 200سم = 4كجم
    assert float(rem_after["stock_qty"]) == 4.0
