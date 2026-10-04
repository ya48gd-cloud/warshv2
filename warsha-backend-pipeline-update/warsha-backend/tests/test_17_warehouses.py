"""
test_17_warehouses.py — محطة 10: المخازن المنطقية (Warehouse)
"""
import pytest

BASE = "/api/v1/warehouses"

DEFAULT_CODES = {"RAW", "SPARE", "IMPORTED", "WIP", "FG", "REMNANT", "SCRAP", "RETURNS"}


@pytest.mark.asyncio
async def test_default_warehouses_seeded(cli):
    """Tenant جديد → الـ8 مخازن الافتراضية موجودة تلقائيًا من غير أي إجراء يدوي."""
    r = await cli.get(BASE)
    assert r.status_code == 200
    codes = {w["code"] for w in r.json()}
    assert codes == DEFAULT_CODES
    assert all(w["is_default"] for w in r.json())
    assert all(w["is_active"] for w in r.json())


@pytest.mark.asyncio
async def test_seeding_is_idempotent(cli):
    """استدعاء seed_default_warehouses تاني على نفس الـ tenant (زي إعادة تشغيل UPDATE.bat) ما بيكررش المخازن."""
    from app.api.warehouses import seed_default_warehouses
    from tests.conftest import TestSessionLocal
    async with TestSessionLocal() as session:
        added = await seed_default_warehouses(session, 1)
        await session.commit()
    assert added == 0   # كلهم موجودين بالفعل من الـ clean_db fixture

    r = await cli.get(BASE)
    assert len(r.json()) == 8   # مفيش تكرار


@pytest.mark.asyncio
async def test_create_custom_warehouse(cli):
    r = await cli.post(BASE, json={"code": "SITE1", "name_ar": "مخزن الموقع 1", "warehouse_type": "general"})
    assert r.status_code == 201
    w = r.json()
    assert w["code"] == "SITE1"
    assert w["is_default"] is False

    all_wh = (await cli.get(BASE)).json()
    assert len(all_wh) == 9


@pytest.mark.asyncio
async def test_duplicate_warehouse_code_rejected(cli):
    r = await cli.post(BASE, json={"code": "RAW", "name_ar": "تكرار", "warehouse_type": "general"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_default_warehouse_blocked(cli):
    """مخزن أساسي (من الـ8) ميتحذفش خالص، حتى لو مفيهوش أي رصيد."""
    warehouses = (await cli.get(BASE)).json()
    raw_wh = next(w for w in warehouses if w["code"] == "RAW")
    r = await cli.delete(f"{BASE}/{raw_wh['id']}")
    assert r.status_code == 400
    # لسه موجود بعد محاولة الحذف
    still_there = (await cli.get(BASE)).json()
    assert any(w["code"] == "RAW" for w in still_there)


@pytest.mark.asyncio
async def test_delete_custom_warehouse_allowed(cli):
    """مخزن مخصّص (مش من الأساسيين) ينفع يتحذف عادي."""
    created = (await cli.post(BASE, json={"code": "TEMP1", "name_ar": "مؤقت", "warehouse_type": "general"})).json()
    r = await cli.delete(f"{BASE}/{created['id']}")
    assert r.status_code == 200
    remaining = (await cli.get(BASE)).json()
    assert not any(w["code"] == "TEMP1" for w in remaining)


@pytest.mark.asyncio
async def test_toggle_warehouse_active(cli):
    created = (await cli.post(BASE, json={"code": "TEMP2", "name_ar": "مؤقت 2"})).json()
    r = await cli.post(f"{BASE}/{created['id']}/toggle-active")
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    # اتخفى من القائمة الافتراضية (النشطة بس)
    active_only = (await cli.get(BASE)).json()
    assert not any(w["code"] == "TEMP2" for w in active_only)

    # لسه ظاهر لو طلبت include_inactive
    with_inactive = (await cli.get(f"{BASE}?include_inactive=true")).json()
    assert any(w["code"] == "TEMP2" for w in with_inactive)


@pytest.mark.asyncio
async def test_update_warehouse(cli):
    created = (await cli.post(BASE, json={"code": "TEMP3", "name_ar": "اسم قديم"})).json()
    r = await cli.put(f"{BASE}/{created['id']}", json={"code": "TEMP3", "name_ar": "اسم جديد", "warehouse_type": "general"})
    assert r.status_code == 200
    assert r.json()["name_ar"] == "اسم جديد"


@pytest.mark.asyncio
async def test_update_default_warehouse_name_allowed_but_code_locked(cli):
    """اسم مخزن أساسي ينفع يتعدّل، لكن كوده (RAW/WIP/...) لازم يفضل ثابت —
    كود تاني في النظام (get_or_create_warehouse_bin وغيره) بيدوّر عليه بالاسم
    الحرفي، فتغييره هيسيب مواقع تخزين يتيمة من غير حد يلاحظ."""
    warehouses = (await cli.get(BASE)).json()
    raw_wh = next(w for w in warehouses if w["code"] == "RAW")

    # تغيير الاسم بس (نفس الكود) لازم يعدي عادي
    r_name = await cli.put(f"{BASE}/{raw_wh['id']}", json={
        "code": "RAW", "name_ar": "اسم جديد للخامات", "warehouse_type": raw_wh["warehouse_type"],
    })
    assert r_name.status_code == 200
    assert r_name.json()["name_ar"] == "اسم جديد للخامات"

    # محاولة تغيير الكود نفسه لازم تترفض
    r_code = await cli.put(f"{BASE}/{raw_wh['id']}", json={
        "code": "RAWX", "name_ar": "اسم جديد للخامات", "warehouse_type": raw_wh["warehouse_type"],
    })
    assert r_code.status_code == 400
    still_there = (await cli.get(BASE)).json()
    assert any(w["code"] == "RAW" for w in still_there)


@pytest.mark.asyncio
async def test_warehouses_isolated_per_tenant(cli, cli2):
    """مخزن مخصّص اتعمل لـ tenant1 مايظهرش عند tenant2."""
    await cli.post(BASE, json={"code": "ONLY_T1", "name_ar": "خاص بالمستأجر 1"})
    t2_warehouses = (await cli2.get(BASE)).json()
    assert not any(w["code"] == "ONLY_T1" for w in t2_warehouses)
    # لكن الـ8 الأساسيين موجودين لـ tenant2 برضه (مستقلين)
    assert len(t2_warehouses) == 8


# ════════════════ رصيد كل مخزن (StockQuant لكل مخزن) ════════════════
INV = "/api/v1/inventory"


async def _wh_id(c, code):
    return next(w["id"] for w in (await c.get(BASE)).json() if w["code"] == code)


async def _new_material(c, name="صاج تجريبي", cost=10.0):
    r = await c.post(f"{INV}/materials", json={
        "name_ar": name, "unit": "كجم", "unit_cost": cost, "category_id": 1,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _move(c, mid, mtype, qty, wh=None, cost=10.0):
    body = {"material_id": mid, "movement_type": mtype, "qty": qty, "unit_cost": cost}
    if wh is not None:
        body["warehouse_id"] = wh
    return await c.post(f"{INV}/movements", json=body)


def _by_wh(quants):
    out = {}
    for q in quants:
        out[q["warehouse_code"]] = out.get(q["warehouse_code"], 0) + q["qty"]
    return out


@pytest.mark.asyncio
async def test_receipt_into_chosen_warehouse(cli):
    mid = await _new_material(cli)
    spare = await _wh_id(cli, "SPARE")
    r = await _move(cli, mid, "in", 50, wh=spare)
    assert r.status_code == 201, r.text
    # بدون مخزن → RAW زي السلوك القديم
    assert (await _move(cli, mid, "in", 20)).status_code == 201

    quants = (await cli.get(f"{INV}/materials/{mid}/quants")).json()
    assert all("warehouse_id" in q and "warehouse_name" in q for q in quants)
    assert _by_wh(quants) == {"SPARE": 50, "RAW": 20}
    mat = (await cli.get(f"{INV}/materials/{mid}")).json()
    assert float(mat["stock_qty"]) == 70

    dist = (await cli.get(f"{INV}/warehouse-distribution")).json()
    assert {d["warehouse_code"]: d["qty"] for d in dist[str(mid)]} == {"SPARE": 50, "RAW": 20}


@pytest.mark.asyncio
async def test_issue_from_warehouse_insufficient_qty(cli):
    mid = await _new_material(cli)
    raw, spare = await _wh_id(cli, "RAW"), await _wh_id(cli, "SPARE")
    assert (await _move(cli, mid, "in", 100, wh=raw)).status_code == 201
    # الرصيد الكلي 100 لكن مخزن قطع الغيار فاضي → رفض
    r = await _move(cli, mid, "out", 10, wh=spare)
    assert r.status_code == 400
    assert "غير كاف" in r.json()["detail"]
    # الصرف من RAW يعدي
    r2 = await _move(cli, mid, "out", 30, wh=raw)
    assert r2.status_code == 201, r2.text
    quants = (await cli.get(f"{INV}/materials/{mid}/quants")).json()
    assert _by_wh(quants) == {"RAW": 70}


@pytest.mark.asyncio
async def test_movement_with_foreign_warehouse_404(cli, cli2):
    mid = await _new_material(cli)
    t2_raw = await _wh_id(cli2, "RAW")
    r = await _move(cli, mid, "in", 5, wh=t2_raw)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_transfer_between_warehouses(cli):
    mid = await _new_material(cli)
    raw, imp = await _wh_id(cli, "RAW"), await _wh_id(cli, "IMPORTED")
    assert (await _move(cli, mid, "in", 80, wh=raw)).status_code == 201

    r = await cli.post(f"{INV}/movements/transfer", json={
        "material_id": mid, "source_warehouse_id": raw, "destination_warehouse_id": imp, "qty": 30,
    })
    assert r.status_code == 201, r.text
    quants = (await cli.get(f"{INV}/materials/{mid}/quants")).json()
    assert _by_wh(quants) == {"RAW": 50, "IMPORTED": 30}
    mat = (await cli.get(f"{INV}/materials/{mid}")).json()
    assert float(mat["stock_qty"]) == 80   # الإجمالي ثابت

    # نفس المخزن / كمية صفر / أكتر من المتاح → 400
    same = await cli.post(f"{INV}/movements/transfer", json={
        "material_id": mid, "source_warehouse_id": raw, "destination_warehouse_id": raw, "qty": 1})
    assert same.status_code == 400
    zero = await cli.post(f"{INV}/movements/transfer", json={
        "material_id": mid, "source_warehouse_id": raw, "destination_warehouse_id": imp, "qty": 0})
    assert zero.status_code == 400
    too_much = await cli.post(f"{INV}/movements/transfer", json={
        "material_id": mid, "source_warehouse_id": imp, "destination_warehouse_id": raw, "qty": 31})
    assert too_much.status_code == 400
    missing = await cli.post(f"{INV}/movements/transfer", json={"material_id": mid, "qty": 1})
    assert missing.status_code == 400


@pytest.mark.asyncio
async def test_stock_summary_and_warehouse_stock(cli):
    m1 = await _new_material(cli, "خامة 1", cost=10)
    m2 = await _new_material(cli, "خامة 2", cost=4)
    raw, fg = await _wh_id(cli, "RAW"), await _wh_id(cli, "FG")
    await _move(cli, m1, "in", 10, wh=raw, cost=10)
    await _move(cli, m2, "in", 5, wh=raw, cost=4)
    await _move(cli, m2, "in", 3, wh=fg, cost=4)

    summary = {w["code"]: w for w in (await cli.get(f"{BASE}/stock-summary")).json()}
    assert summary["RAW"]["items_count"] == 2
    assert summary["RAW"]["total_value"] == 120.0   # 10*10 + 5*4
    assert summary["FG"]["items_count"] == 1
    assert summary["FG"]["total_value"] == 12.0
    assert summary["SPARE"]["items_count"] == 0

    st = (await cli.get(f"{BASE}/{raw}/stock")).json()
    assert st["items_count"] == 2
    assert st["total_value"] == 120.0
    row = next(i for i in st["items"] if i["material_id"] == m2)
    assert row["qty"] == 5 and row["unit"] == "كجم" and row["value"] == 20.0


@pytest.mark.asyncio
async def test_warehouse_stock_cross_tenant_404(cli, cli2):
    mid = await _new_material(cli)
    raw = await _wh_id(cli, "RAW")
    await _move(cli, mid, "in", 10, wh=raw)
    assert (await cli2.get(f"{BASE}/{raw}/stock")).status_code == 404
    assert (await cli2.get(f"{INV}/materials/{mid}/quants")).status_code == 404
    t2 = {w["code"]: w for w in (await cli2.get(f"{BASE}/stock-summary")).json()}
    assert t2["RAW"]["items_count"] == 0
    assert str(mid) not in (await cli2.get(f"{INV}/warehouse-distribution")).json()


@pytest.mark.asyncio
async def test_reconcile_unlocated_stock_idempotent(cli):
    from decimal import Decimal
    from sqlalchemy import select
    from app.models.models import Material
    from app.services.inventory_ledger import reconcile_unlocated_stock
    from tests.conftest import TestSessionLocal

    mid = await _new_material(cli)
    # رصيد قديم من غير أي StockQuant (زي بيانات مرحّلة)
    async with TestSessionLocal() as s:
        m = await s.scalar(select(Material).where(Material.id == mid))
        m.stock_qty = Decimal("40")
        await s.commit()
    async with TestSessionLocal() as s:
        first = await reconcile_unlocated_stock(s)
        await s.commit()
    async with TestSessionLocal() as s:
        second = await reconcile_unlocated_stock(s)
        await s.commit()
    assert first >= 1
    assert second == 0
    quants = (await cli.get(f"{INV}/materials/{mid}/quants")).json()
    assert _by_wh(quants) == {"RAW": 40}


# ── حركات المخزن: فلتر بالمخزن + اسم مخزن المصدر/الوجهة ────────────────

@pytest.mark.asyncio
async def test_material_movements_filtered_by_warehouse(cli):
    mid = await _new_material(cli, name="مادة حركات المخازن")
    raw, imp = await _wh_id(cli, "RAW"), await _wh_id(cli, "IMPORTED")
    assert (await _move(cli, mid, "in", 100, wh=raw)).status_code == 201
    assert (await cli.post(f"{INV}/movements/transfer", json={
        "material_id": mid, "source_warehouse_id": raw, "destination_warehouse_id": imp, "qty": 40,
    })).status_code == 201
    assert (await _move(cli, mid, "out", 10, wh=imp)).status_code == 201

    allm = (await cli.get(f"{INV}/movements/{mid}")).json()
    assert len(allm) == 3
    tr = next(m for m in allm if m["movement_type"] == "transfer")
    assert tr["source_warehouse_id"] == raw and tr["destination_warehouse_id"] == imp
    assert tr["source_warehouse_name"] and tr["destination_warehouse_name"]

    # الرصيد التراكمي في مخزن المستوردة: +40 (تحويل) −10 (صرف) = 30
    impm = (await cli.get(f"{INV}/movements/{mid}?warehouse_id={imp}")).json()
    assert len(impm) == 2
    assert impm[0]["balance_after"] == 30 and impm[0]["warehouse_direction"] == "out"
    assert impm[1]["warehouse_direction"] == "in"
    # في مخزن الخامات: +100 −40 = 60، والصرف من المستوردة مالوش دعوة بيه
    rawm = (await cli.get(f"{INV}/movements/{mid}?warehouse_id={raw}")).json()
    assert len(rawm) == 2 and rawm[0]["balance_after"] == 60


@pytest.mark.asyncio
async def test_legacy_movement_without_location_counts_as_raw(cli):
    from sqlalchemy import update
    from tests.conftest import TestSessionLocal
    from app.models.models import StockMovement
    mid = await _new_material(cli, name="مادة قديمة")
    assert (await _move(cli, mid, "in", 5)).status_code == 201
    async with TestSessionLocal() as s:   # محاكاة بيانات قبل تفعيل المواقع
        await s.execute(update(StockMovement).where(StockMovement.material_id == mid)
                        .values(destination_location_id=None))
        await s.commit()
    raw = await _wh_id(cli, "RAW")
    rows = (await cli.get(f"{INV}/movements/{mid}?warehouse_id={raw}")).json()
    assert len(rows) == 1 and rows[0]["destination_warehouse_id"] == raw


@pytest.mark.asyncio
async def test_warehouse_movements_all_materials(cli, cli2):
    raw, imp = await _wh_id(cli, "RAW"), await _wh_id(cli, "IMPORTED")
    a = await _new_material(cli, name="مادة أ")
    b = await _new_material(cli, name="مادة ب")
    await _move(cli, a, "in", 10, wh=raw)
    await _move(cli, b, "in", 20, wh=imp)
    rows = (await cli.get(f"{INV}/warehouse-movements/{imp}")).json()
    assert [r["material_id"] for r in rows] == [b]
    assert rows[0]["material_name"] == "مادة ب" and rows[0]["warehouse_direction"] == "in"
    assert (await cli.get(f"{INV}/warehouse-movements/{imp}?date_from=2030-01-02&date_to=2030-01-01")).status_code == 400
    # مخزن ورشة تانية → 404
    assert (await cli2.get(f"{INV}/warehouse-movements/{imp}")).status_code == 404
