"""
test_11_equipment.py — اختبارات المعدات
CRUD · شجرة · BOM · خطوط التكلفة · الأبعاد
"""
import pytest

BASE = "/api/v1/equipment"
INV = "/api/v1/inventory"


def _eq(name="معدة اختبار", level=1, parent=None):
    d = {"name_ar": name, "level": level}
    if parent:
        d["parent_id"] = parent
    return d


async def _make_material(cli):
    r = await cli.post(f"{INV}/materials", json={"name_ar": "مادة BOM", "unit": "كجم", "unit_cost": 8.0})
    return r.json()["id"]


# ════════════════ EQUIPMENT CRUD ════════════════════════════════════
@pytest.mark.asyncio
async def test_create_equipment(cli):
    r = await cli.post(BASE, json=_eq())
    assert r.status_code == 201
    d = r.json()
    assert d["name_ar"] == "معدة اختبار"
    assert d["level"] == 0  # الجذر مستواه 0 (يُحسب من الشجرة)


@pytest.mark.asyncio
async def test_list_root_equipment(cli):
    await cli.post(BASE, json=_eq())
    r = await cli.get(BASE)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_list_all_equipment(cli):
    await cli.post(BASE, json=_eq())
    r = await cli.get(f"{BASE}/list-all")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_get_equipment(cli):
    r = await cli.post(BASE, json=_eq())
    eid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/{eid}")
    assert r2.status_code == 200
    assert r2.json()["id"] == eid


@pytest.mark.asyncio
async def test_get_equipment_not_found(cli):
    r = await cli.get(f"{BASE}/99999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_equipment(cli):
    r = await cli.post(BASE, json=_eq())
    eid = r.json()["id"]
    r2 = await cli.put(f"{BASE}/{eid}", json=_eq("معدة محدّثة"))
    assert r2.status_code == 200
    assert r2.json()["name_ar"] == "معدة محدّثة"


@pytest.mark.asyncio
async def test_delete_equipment(cli):
    r = await cli.post(BASE, json=_eq())
    eid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/{eid}")
    assert r2.status_code in (200, 204)


# ════════════════ HIERARCHY / TREE ══════════════════════════════════
@pytest.mark.asyncio
async def test_create_child_equipment(cli):
    rp = await cli.post(BASE, json=_eq("أب", level=1))
    parent_id = rp.json()["id"]
    rc = await cli.post(BASE, json=_eq("ابن", level=2, parent=parent_id))
    assert rc.status_code == 201
    assert rc.json()["parent_id"] == parent_id


@pytest.mark.asyncio
async def test_get_equipment_tree(cli):
    r = await cli.post(BASE, json=_eq("شجرة"))
    eid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/{eid}/tree")
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_get_all_equipment_trees(cli):
    r = await cli.get(f"{BASE}/tree/all")
    assert r.status_code == 200


# ════════════════ BOM ════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_add_bom_line(cli):
    r = await cli.post(BASE, json=_eq())
    eid = r.json()["id"]
    mid = await _make_material(cli)
    r2 = await cli.post(f"{BASE}/bom", json={
        "equipment_id": eid, "material_id": mid, "qty": 3.0, "unit_cost": 8.0,
    })
    assert r2.status_code == 201
    assert r2.json()["equipment_id"] == eid


@pytest.mark.asyncio
async def test_get_bom(cli):
    r = await cli.post(BASE, json=_eq())
    eid = r.json()["id"]
    mid = await _make_material(cli)
    await cli.post(f"{BASE}/bom", json={"equipment_id": eid, "material_id": mid, "qty": 1.0, "unit_cost": 8.0})
    r2 = await cli.get(f"{BASE}/{eid}/bom")
    assert r2.status_code == 200
    assert len(r2.json()) >= 1


@pytest.mark.asyncio
async def test_bom_cost_breakdown(cli):
    r = await cli.post(BASE, json=_eq())
    eid = r.json()["id"]
    mid = await _make_material(cli)
    await cli.post(f"{BASE}/bom", json={"equipment_id": eid, "material_id": mid, "qty": 2.0, "unit_cost": 8.0})
    r2 = await cli.get(f"{BASE}/{eid}/bom/cost")
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_bom_parametric(cli):
    r = await cli.post(BASE, json=_eq())
    eid = r.json()["id"]
    mid = await _make_material(cli)
    await cli.post(f"{BASE}/bom", json={"equipment_id": eid, "material_id": mid, "qty": 1.0, "unit_cost": 8.0})
    r2 = await cli.get(f"{BASE}/{eid}/bom/parametric", params={"quantity": 5.0})
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_delete_bom_line(cli):
    r = await cli.post(BASE, json=_eq())
    eid = r.json()["id"]
    mid = await _make_material(cli)
    r2 = await cli.post(f"{BASE}/bom", json={"equipment_id": eid, "material_id": mid, "qty": 1.0, "unit_cost": 8.0})
    line_id = r2.json()["id"]
    r3 = await cli.delete(f"{BASE}/bom/{line_id}")
    assert r3.status_code in (200, 204)


# ════════════════ COST LINES ════════════════════════════════════════
@pytest.mark.asyncio
async def test_add_cost_line(cli):
    r = await cli.post(BASE, json=_eq())
    eid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/{eid}/cost-lines", json={
        "cost_type": "material", "description": "مواد خام",
        "qty": 5.0, "unit_cost": 100.0,
    })
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_list_cost_lines(cli):
    r = await cli.post(BASE, json=_eq())
    eid = r.json()["id"]
    await cli.post(f"{BASE}/{eid}/cost-lines", json={
        "cost_type": "labor", "description": "عمالة", "qty": 2.0, "unit_cost": 200.0,
    })
    r2 = await cli.get(f"{BASE}/{eid}/cost-lines")
    assert r2.status_code == 200
    assert len(r2.json()) >= 1


@pytest.mark.asyncio
async def test_delete_cost_line(cli):
    r = await cli.post(BASE, json=_eq())
    eid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/{eid}/cost-lines", json={
        "cost_type": "other", "description": "متنوع", "qty": 1.0, "unit_cost": 50.0,
    })
    line_id = r2.json()["id"]
    r3 = await cli.delete(f"{BASE}/cost-lines/{line_id}")
    assert r3.status_code in (200, 204)


# ════════════════ DIMENSIONS ════════════════════════════════════════
@pytest.mark.asyncio
async def test_add_dimension(cli):
    r = await cli.post(BASE, json=_eq())
    eid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/{eid}/dimensions",
                        params={"dim_key": "length", "dim_value": "150", "unit": "cm"})
    assert r2.status_code in (200, 201)


@pytest.mark.asyncio
async def test_delete_dimension(cli):
    r = await cli.post(BASE, json=_eq())
    eid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/{eid}/dimensions",
                        params={"dim_key": "width", "dim_value": "80", "unit": "cm"})
    dim_id = r2.json()["id"]
    r3 = await cli.delete(f"{BASE}/dimension/{dim_id}")
    assert r3.status_code in (200, 204)
