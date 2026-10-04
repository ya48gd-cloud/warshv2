"""
test_24_stock_count_warehouse.py — الجرد لكل مخزن:
ورقة الجرد بأصناف المخزن ده بس، التسوية على موقعه، قيد تسوية (زيادة/عجز)،
منع جردين مفتوحين على نفس المخزن، إضافة صنف لقيناه مش متسجل، الإلغاء.
"""
import pytest

from tests.test_17_warehouses import INV, _wh_id, _new_material, _move, _by_wh
from tests.test_23_equipment_lifecycle import _account_balance
from tests.test_16_reports_dashboard_isolation import _seed_coa

PROD = "/api/v1/production"


async def _start(cli, wh):
    r = await cli.post(f"{PROD}/stock-count", json={"warehouse_id": wh})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_count_sheet_only_has_that_warehouse_items(cli):
    raw, imp = await _wh_id(cli, "RAW"), await _wh_id(cli, "IMPORTED")
    a = await _new_material(cli, name="صنف خامات")
    b = await _new_material(cli, name="صنف مستورد")
    await _move(cli, a, "in", 50, wh=raw)
    await _move(cli, b, "in", 20, wh=imp)
    cid = await _start(cli, imp)
    sheet = (await cli.get(f"{PROD}/stock-count/{cid}")).json()
    assert sheet["warehouse_id"] == imp and sheet["warehouse_name"]
    assert [l["material_id"] for l in sheet["lines"]] == [b]
    assert sheet["lines"][0]["book_qty"] == 20


@pytest.mark.asyncio
async def test_close_adjusts_only_that_warehouse_and_posts_journal(cli):
    await _seed_coa(cli)
    raw, imp = await _wh_id(cli, "RAW"), await _wh_id(cli, "IMPORTED")
    mid = await _new_material(cli, name="صنف في مخزنين", cost=10.0)
    await _move(cli, mid, "in", 50, wh=raw)
    await _move(cli, mid, "in", 20, wh=imp)
    inv_before = await _account_balance("1310")

    cid = await _start(cli, imp)
    r = await cli.post(f"{PROD}/stock-count/{cid}/scan", json={"material_id": mid, "actual_qty": 17})
    assert r.status_code == 200, r.text
    r = await cli.post(f"{PROD}/stock-count/{cid}/close")
    assert r.status_code == 200, r.text
    assert r.json()["shortage_value"] == pytest.approx(30.0)

    quants = (await cli.get(f"{INV}/materials/{mid}/quants")).json()
    assert _by_wh(quants) == {"RAW": 50, "IMPORTED": 17}      # الخامات ما اتلمسش
    mat = (await cli.get(f"{INV}/materials/{mid}")).json()
    assert float(mat["stock_qty"]) == 67
    assert await _account_balance("1310") == pytest.approx(inv_before - 30)
    assert await _account_balance("5600") == pytest.approx(30)

    # حركة التسوية ظاهرة في حركات المخزن ده
    rows = (await cli.get(f"{INV}/movements/{mid}?warehouse_id={imp}")).json()
    assert rows[0]["source_warehouse_id"] == imp and rows[0]["qty"] == 3


@pytest.mark.asyncio
async def test_surplus_and_found_item_not_on_sheet(cli):
    raw = await _wh_id(cli, "RAW")
    a = await _new_material(cli, name="صنف موجود")
    extra = await _new_material(cli, name="صنف لقيناه")
    await _move(cli, a, "in", 10, wh=raw)
    cid = await _start(cli, raw)
    assert (await cli.post(f"{PROD}/stock-count/{cid}/scan", json={"material_id": extra, "actual_qty": 4})).status_code == 200
    sheet = (await cli.get(f"{PROD}/stock-count/{cid}")).json()
    assert {l["material_id"] for l in sheet["lines"]} >= {a, extra}
    r = await cli.post(f"{PROD}/stock-count/{cid}/close")
    assert r.status_code == 200 and r.json()["uncounted"] >= 1   # الصنف "أ" ما اتعدش → ما يتسوّاش
    quants = (await cli.get(f"{INV}/materials/{extra}/quants")).json()
    assert _by_wh(quants) == {"RAW": 4}
    quants_a = (await cli.get(f"{INV}/materials/{a}/quants")).json()
    assert _by_wh(quants_a) == {"RAW": 10}


@pytest.mark.asyncio
async def test_two_open_counts_same_warehouse_blocked(cli):
    raw, imp = await _wh_id(cli, "RAW"), await _wh_id(cli, "IMPORTED")
    await _start(cli, raw)
    assert (await cli.post(f"{PROD}/stock-count", json={"warehouse_id": raw})).status_code == 409
    assert (await cli.post(f"{PROD}/stock-count", json={})).status_code == 409   # "كل المخازن" بيتداخل
    await _start(cli, imp)                                                       # مخزن تاني عادي


@pytest.mark.asyncio
async def test_cancel_and_unscan(cli):
    raw = await _wh_id(cli, "RAW")
    mid = await _new_material(cli, name="صنف إلغاء")
    await _move(cli, mid, "in", 5, wh=raw)
    cid = await _start(cli, raw)
    await cli.post(f"{PROD}/stock-count/{cid}/scan", json={"material_id": mid, "actual_qty": 1})
    assert (await cli.delete(f"{PROD}/stock-count/{cid}/scan/{mid}")).status_code == 200
    line = next(l for l in (await cli.get(f"{PROD}/stock-count/{cid}")).json()["lines"] if l["material_id"] == mid)
    assert line["actual_qty"] is None
    assert (await cli.post(f"{PROD}/stock-count/{cid}/cancel")).status_code == 200
    assert (await cli.post(f"{PROD}/stock-count/{cid}/close")).status_code == 400
    mat = (await cli.get(f"{INV}/materials/{mid}")).json()
    assert float(mat["stock_qty"]) == 5
    await _start(cli, raw)   # بعد الإلغاء ينفع جرد جديد


@pytest.mark.asyncio
async def test_count_isolation(cli, cli2):
    raw = await _wh_id(cli, "RAW")
    cid = await _start(cli, raw)
    assert (await cli2.get(f"{PROD}/stock-count/{cid}")).status_code == 404
    assert (await cli2.post(f"{PROD}/stock-count/{cid}/close")).status_code == 404
    assert (await cli2.post(f"{PROD}/stock-count", json={"warehouse_id": raw})).status_code == 404
