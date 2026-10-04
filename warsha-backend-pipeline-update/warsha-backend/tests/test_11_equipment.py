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


# ════════════════ HARDENING: RBAC · TENANT · VALIDATION ═════════════
@pytest.mark.asyncio
async def test_viewer_cannot_write_equipment_but_can_read(client, cli):
    from app.api.auth import create_token
    eid = (await cli.post(BASE, json=_eq())).json()["id"]
    tok = create_token(user_id=990011, username="eq_viewer", role="viewer", tenant_id=1)
    h = {"Authorization": f"Bearer {tok}"}
    assert (await client.get(BASE, headers=h)).status_code == 200
    assert (await client.get(f"{BASE}/{eid}", headers=h)).status_code == 200
    assert (await client.post(BASE, json=_eq(), headers=h)).status_code == 403
    assert (await client.delete(f"{BASE}/{eid}", headers=h)).status_code == 403
    assert (await client.put(f"{BASE}/{eid}", json=_eq("x"), headers=h)).status_code == 403


@pytest.mark.asyncio
async def test_bom_line_cross_tenant_patch_delete_blocked(cli, cli2):
    eid = (await cli.post(BASE, json=_eq())).json()["id"]
    mid = await _make_material(cli)
    lid = (await cli.post(f"{BASE}/bom", json={"equipment_id": eid, "material_id": mid,
                                               "qty": 2, "unit_cost": 8})).json()["id"]
    assert (await cli2.patch(f"{BASE}/bom/{lid}", json={"qty": 999})).status_code == 404
    assert (await cli2.delete(f"{BASE}/bom/{lid}")).status_code == 404
    assert (await cli2.get(f"{BASE}/{eid}/bom")).status_code == 404
    assert (await cli2.get(f"{BASE}/{eid}/bom/cost")).status_code == 404
    assert (await cli2.get(f"{BASE}/{eid}/cost-lines")).status_code == 404
    assert (await cli2.get(f"{BASE}/{eid}/bom/print")).status_code == 404
    r = await cli.get(f"{BASE}/{eid}/bom")
    assert r.status_code == 200 and float(r.json()[0]["qty"]) == 2.0


@pytest.mark.asyncio
async def test_bom_material_from_other_tenant_rejected(cli, cli2):
    eid = (await cli.post(BASE, json=_eq())).json()["id"]
    mid_t2 = await _make_material(cli2)
    r = await cli.post(f"{BASE}/bom", json={"equipment_id": eid, "material_id": mid_t2, "qty": 1, "unit_cost": 8})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bom_negative_values_rejected(cli):
    eid = (await cli.post(BASE, json=_eq())).json()["id"]
    mid = await _make_material(cli)
    r = await cli.post(f"{BASE}/bom", json={"equipment_id": eid, "material_id": mid, "qty": -1, "unit_cost": 8})
    assert r.status_code == 400
    r = await cli.post(f"{BASE}/bom", json={"equipment_id": eid, "material_id": mid, "qty": 1, "unit_cost": -8})
    assert r.status_code == 400
    lid = (await cli.post(f"{BASE}/bom", json={"equipment_id": eid, "material_id": mid,
                                               "qty": 1, "unit_cost": 8})).json()["id"]
    assert (await cli.patch(f"{BASE}/bom/{lid}", json={"qty": 0})).status_code == 400
    assert (await cli.patch(f"{BASE}/bom/{lid}", json={"unit_cost": -1})).status_code == 400
    r = await cli.patch(f"{BASE}/bom/{lid}", json={"qty": 3, "unit_cost": 10})
    assert r.status_code == 200 and r.json()["total_cost"] == 30.0


@pytest.mark.asyncio
async def test_dimension_cross_tenant_blocked(cli, cli2):
    eid = (await cli.post(BASE, json=_eq())).json()["id"]
    r = await cli2.post(f"{BASE}/{eid}/dimensions", params={"dim_key": "l", "dim_value": "1", "unit": "cm"})
    assert r.status_code == 404
    did = (await cli.post(f"{BASE}/{eid}/dimensions",
                          params={"dim_key": "l", "dim_value": "1", "unit": "cm"})).json()["id"]
    assert (await cli2.delete(f"{BASE}/dimension/{did}")).status_code == 404


@pytest.mark.asyncio
async def test_parent_validation_cycle_and_cross_tenant(cli, cli2):
    a = (await cli.post(BASE, json=_eq("A"))).json()["id"]
    b = (await cli.post(BASE, json=_eq("B", parent=a))).json()["id"]
    # A تحت B (ابنها) → حلقة
    assert (await cli.put(f"{BASE}/{a}", json={"name_ar": "A", "parent_id": b})).status_code == 400
    assert (await cli.put(f"{BASE}/{a}", json={"name_ar": "A", "parent_id": a})).status_code == 400
    other = (await cli2.post(BASE, json=_eq("tenant2"))).json()["id"]
    assert (await cli.post(BASE, json=_eq("x", parent=other))).status_code == 400
    assert (await cli.put(f"{BASE}/{b}", json={"name_ar": "B", "parent_id": other})).status_code == 400


@pytest.mark.asyncio
async def test_move_equipment_updates_descendant_levels(cli):
    a = (await cli.post(BASE, json=_eq("A"))).json()["id"]
    b = (await cli.post(BASE, json=_eq("B"))).json()["id"]
    c = (await cli.post(BASE, json=_eq("C", parent=b))).json()["id"]
    r = await cli.put(f"{BASE}/{b}", json={"name_ar": "B", "parent_id": a})
    assert r.status_code == 200 and r.json()["level"] == 1
    assert (await cli.get(f"{BASE}/{c}")).json()["level"] == 2


@pytest.mark.asyncio
async def test_update_partial_keeps_unsent_fields(cli):
    p = (await cli.post(BASE, json=_eq("P"))).json()["id"]
    r = await cli.post(BASE, json={"name_ar": "K", "parent_id": p, "serial_number": "SN-1", "weight_kg": 12.5})
    eid = r.json()["id"]
    r2 = await cli.put(f"{BASE}/{eid}", json={"name_ar": "K2"})
    assert r2.status_code == 200
    d = r2.json()
    assert d["parent_id"] == p and d["serial_number"] == "SN-1" and d["weight_kg"] == 12.5


@pytest.mark.asyncio
async def test_equipment_numeric_validation(cli):
    assert (await cli.post(BASE, json={"name_ar": "x", "weight_kg": -1})).status_code == 400
    assert (await cli.post(BASE, json={"name_ar": "x", "base_qty": 0})).status_code == 400
    assert (await cli.post(BASE, json={"name_ar": "x", "calc_unit": "meter",
                                       "length_min": 5, "length_max": 2})).status_code == 400
    assert (await cli.post(BASE, json={"name_ar": "x", "calc_unit": "bogus"})).status_code == 400
    assert (await cli.post(BASE, json={"name_ar": "x", "equip_status": "bogus"})).status_code == 400
    assert (await cli.post(BASE, json={"name_ar": "   "})).status_code == 400


@pytest.mark.asyncio
async def test_delete_equipment_with_children_409(cli):
    p = (await cli.post(BASE, json=_eq("P"))).json()["id"]
    c = (await cli.post(BASE, json=_eq("C", parent=p))).json()["id"]
    assert (await cli.delete(f"{BASE}/{p}")).status_code == 409
    assert (await cli.delete(f"{BASE}/{c}")).status_code in (200, 204)
    assert (await cli.delete(f"{BASE}/{p}")).status_code in (200, 204)


@pytest.mark.asyncio
async def test_duplicate_copies_cost_lines(cli):
    eid = (await cli.post(BASE, json=_eq())).json()["id"]
    await cli.post(f"{BASE}/{eid}/cost-lines", json={"cost_type": "labor", "description": "L", "cost_per_unit": 150})
    r = await cli.post(f"{BASE}/{eid}/duplicate", json={"code": "DUP-1"})
    assert r.status_code == 201
    new_id = r.json()["id"]
    lines = (await cli.get(f"{BASE}/{new_id}/cost-lines")).json()
    assert len(lines) == 1 and lines[0]["cost_per_unit"] == 150.0


@pytest.mark.asyncio
async def test_cost_line_cross_tenant_and_negative(cli, cli2):
    eid = (await cli.post(BASE, json=_eq())).json()["id"]
    r = await cli.post(f"{BASE}/{eid}/cost-lines", json={"cost_type": "labor", "description": "x", "cost_per_unit": -5})
    assert r.status_code == 400
    lid = (await cli.post(f"{BASE}/{eid}/cost-lines",
                          json={"cost_type": "labor", "description": "x", "cost_per_unit": 5})).json()["id"]
    assert (await cli2.delete(f"{BASE}/cost-lines/{lid}")).status_code == 404
    assert (await cli2.post(f"{BASE}/{eid}/cost-lines",
                            json={"cost_type": "labor", "description": "x", "cost_per_unit": 5})).status_code == 400


@pytest.mark.asyncio
async def test_print_bom_escapes_html(cli):
    eid = (await cli.post(BASE, json={"name_ar": "<script>alert(1)</script>"})).json()["id"]
    r = await cli.get(f"{BASE}/{eid}/bom/print")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


@pytest.mark.asyncio
async def test_category_count_is_tenant_scoped(cli):
    r = await cli.post(f"{BASE}/categories", json={"name_ar": "CAT"})
    assert r.status_code == 201
    cid = r.json()["id"]
    await cli.post(BASE, json={"name_ar": "x", "category_id": cid})
    cats = (await cli.get(f"{BASE}/categories/list")).json()
    assert [c for c in cats if c["id"] == cid][0]["equipment_count"] == 1
    assert (await cli.delete(f"{BASE}/categories/{cid}")).status_code == 409
