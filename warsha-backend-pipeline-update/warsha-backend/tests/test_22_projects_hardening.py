"""
test_22_projects_hardening.py — تحصين وحدة المشاريع الخارجية / الخدمة الميدانية /
عقود التوريد / شهادات الإتمام والضمان (validation + tenant isolation + integrity).
"""
import pytest
from datetime import date, timedelta

EP = "/api/v1/ext-projects"
FS = "/api/v1/field-service"
SC = "/api/v1/supply-contracts"
WCC = "/api/v1/work-completion-certs"
WR = "/api/v1/warranty-certs"
CUST = "/api/v1/customers"
WORKERS = "/api/v1/workers"
INV = "/api/v1/inventory"


async def _cust(cli, name="عميل تحصين"):
    r = await cli.post(CUST, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _project(cli, cid, code="EPH-1", value=10000.0):
    r = await cli.post(EP, json={
        "code": code, "title": "مشروع", "client_id": cid, "contract_value": value,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _material(cli, qty=100):
    r = await cli.post(f"{INV}/materials", json={"name_ar": "مادة تحصين", "unit": "كجم", "unit_cost": 10.0})
    mid = r.json()["id"]
    r2 = await cli.post(f"{INV}/movements", json={
        "material_id": mid, "movement_type": "in", "qty": qty, "unit_cost": 10.0,
    })
    assert r2.status_code == 201, r2.text
    return mid


async def _contract(cli, cid, mid=None, code=None):
    line = {"description": "بند", "qty": "2", "unit": "قطعة", "unit_price": "100"}
    if mid:
        line["material_id"] = mid
    payload = {"customer_id": cid, "date": str(date.today()), "lines": [line]}
    if code:
        payload["code"] = code
    return await cli.post(SC, json=payload)


# ════════════════ EXTERNAL PROJECTS ═══════════════════════════════════
@pytest.mark.asyncio
async def test_ext_project_duplicate_code_returns_409(cli):
    cid = await _cust(cli)
    await _project(cli, cid, code="DUP-1")
    r = await cli.post(EP, json={"code": "DUP-1", "title": "x", "client_id": cid})
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_ext_project_print_with_materials_does_not_crash(cli):
    """كانت الطباعة بتستخدم m.qty (عمود مش موجود) → 500 لأي مشروع فيه مواد."""
    cid = await _cust(cli)
    pid = await _project(cli, cid)
    r = await cli.post(f"{EP}/{pid}/materials", json={
        "source": "local_purchase", "description": "<script>alert(1)</script>",
        "quantity": 2, "unit_cost": 5, "usage_date": str(date.today()),
    })
    assert r.status_code == 201, r.text
    r = await cli.get(f"{EP}/{pid}/print")
    assert r.status_code == 200, r.text
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


@pytest.mark.asyncio
async def test_ext_project_print_cross_tenant_404(cli, cli2):
    cid = await _cust(cli)
    pid = await _project(cli, cid)
    r = await cli2.get(f"{EP}/{pid}/print")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ext_project_payment_overpayment_rejected(cli):
    cid = await _cust(cli)
    pid = await _project(cli, cid)
    r = await cli.post(f"{EP}/{pid}/payments", json={"milestone": "م", "due_amount": 1000})
    payid = r.json()["id"]
    r = await cli.patch(f"{EP}/{pid}/payments/{payid}/receive", json={"paid_amount": 1500})
    assert r.status_code == 400
    r = await cli.patch(f"{EP}/{pid}/payments/{payid}/receive", json={"paid_amount": 1000})
    assert r.status_code == 200 and r.json()["status"] == "paid"


@pytest.mark.asyncio
async def test_ext_project_invalid_inputs_rejected(cli):
    cid = await _cust(cli)
    pid = await _project(cli, cid)
    r = await cli.post(f"{EP}/{pid}/progress", json={"report_date": str(date.today()), "completion_pct": 150})
    assert r.status_code == 400
    r = await cli.post(f"{EP}/{pid}/expenses", json={
        "expense_date": str(date.today()), "description": "x", "amount": -5,
    })
    assert r.status_code == 400
    r = await cli.post(f"{EP}/{pid}/materials", json={
        "source": "local_purchase", "description": "x", "quantity": 0, "usage_date": str(date.today()),
    })
    assert r.status_code == 400
    r = await cli.post(f"{EP}/{pid}/trips", json={"trip_date": str(date.today()), "fuel_cost": -1})
    assert r.status_code == 400
    r = await cli.post(f"{EP}/{pid}/attendance", json={"work_date": str(date.today()), "records": [{"status": "present"}]})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_ext_project_delete_blocked_with_collected_payment(cli):
    cid = await _cust(cli)
    pid = await _project(cli, cid)
    r = await cli.post(f"{EP}/{pid}/payments", json={"milestone": "م", "due_amount": 1000})
    payid = r.json()["id"]
    await cli.patch(f"{EP}/{pid}/payments/{payid}/receive", json={"paid_amount": 500})
    r = await cli.delete(f"{EP}/{pid}")
    assert r.status_code == 409
    # بعد حذف المرحلة المحصَّلة يسمح بالحذف
    assert (await cli.delete(f"{EP}/{pid}/payments/{payid}")).status_code == 200
    assert (await cli.delete(f"{EP}/{pid}")).status_code == 200


@pytest.mark.asyncio
async def test_ext_project_delete_blocked_with_stock_material(cli):
    cid = await _cust(cli)
    pid = await _project(cli, cid)
    mid = await _material(cli)
    r = await cli.post(f"{EP}/{pid}/materials", json={
        "source": "stock", "material_id": mid, "description": "x",
        "quantity": 3, "usage_date": str(date.today()),
    })
    assert r.status_code == 201, r.text
    assert (await cli.delete(f"{EP}/{pid}")).status_code == 409


# ════════════════ FIELD SERVICE ═══════════════════════════════════════
async def _fs_order(cli, cid, code="FSH-1"):
    r = await cli.post(f"{FS}/orders", json={"code": code, "customer_id": cid, "title": "صيانة"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_fs_duplicate_code_and_bad_enum(cli):
    cid = await _cust(cli)
    await _fs_order(cli, cid, code="FSD-1")
    r = await cli.post(f"{FS}/orders", json={"code": "FSD-1", "customer_id": cid, "title": "x"})
    assert r.status_code == 409
    r = await cli.post(f"{FS}/orders", json={"code": "FSD-2", "customer_id": cid, "title": "x", "priority": "mega"})
    assert r.status_code == 400
    r = await cli.post(f"{FS}/orders", json={"code": "FSD-3", "customer_id": 999999, "title": "x"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_fs_status_assigned_allowed(cli):
    cid = await _cust(cli)
    oid = await _fs_order(cli, cid)
    r = await cli.patch(f"{FS}/orders/{oid}/status", json={"status": "assigned"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_fs_delete_order_with_labor_blocked(cli):
    cid = await _cust(cli)
    oid = await _fs_order(cli, cid)
    w = await cli.post(WORKERS, json={"name": "فني", "daily_wage": 100.0})
    r = await cli.post(f"{FS}/orders/{oid}/labor", json={
        "worker_id": w.json()["id"], "entry_date": str(date.today()), "days": 1, "daily_wage": 100,
    })
    assert r.status_code == 201, r.text
    assert (await cli.delete(f"{FS}/orders/{oid}")).status_code == 409


@pytest.mark.asyncio
async def test_fs_subresources_cross_tenant_404(cli, cli2):
    cid = await _cust(cli)
    oid = await _fs_order(cli, cid)
    for path in ("labor", "tools", "visits"):
        r = await cli2.get(f"{FS}/orders/{oid}/{path}")
        assert r.status_code == 404, (path, r.status_code)


@pytest.mark.asyncio
async def test_fs_delete_asset_with_orders_blocked(cli):
    cid = await _cust(cli)
    r = await cli.post(f"{FS}/assets", json={"customer_id": cid, "name": "معدة"})
    aid = r.json()["id"]
    r = await cli.post(f"{FS}/orders", json={"code": "FSA-1", "customer_id": cid, "title": "x", "asset_id": aid})
    assert r.status_code == 201, r.text
    assert (await cli.delete(f"{FS}/assets/{aid}")).status_code == 409


# ════════════════ SUPPLY CONTRACTS ════════════════════════════════════
@pytest.mark.asyncio
async def test_sc_second_tenant_autocode_no_collision(cli, cli2):
    """code عليه UNIQUE عالمي — tenant تاني كان بياخد SC-0001 مكرر → 500."""
    r1 = await _contract(cli, await _cust(cli))
    assert r1.status_code == 201, r1.text
    r2 = await _contract(cli2, await _cust(cli2, "عميل ت2"))
    assert r2.status_code == 201, r2.text
    assert r1.json()["code"] != r2.json()["code"]
    # كود صريح مستخدم عند tenant تاني → 400 مش 500
    r3 = await _contract(cli2, await _cust(cli2, "عميل ت2ب"), code=r1.json()["code"])
    assert r3.status_code == 400


@pytest.mark.asyncio
async def test_sc_invalid_discount_and_tax(cli):
    cid = await _cust(cli)
    base = {"customer_id": cid, "date": str(date.today()),
            "lines": [{"description": "ب", "qty": "1", "unit_price": "100"}]}
    r = await cli.post(SC, json={**base, "discount_amt": "-5"})
    assert r.status_code == 400
    r = await cli.post(SC, json={**base, "discount_amt": "500"})
    assert r.status_code == 400
    r = await cli.post(SC, json={**base, "tax_pct": "150"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_sc_template_locked_after_delivery_and_double_delivery(cli):
    cid = await _cust(cli)
    mid = await _material(cli)
    c = (await _contract(cli, cid, mid)).json()
    assert (await cli.patch(f"{SC}/{c['id']}/activate")).status_code == 200
    r = await cli.post(f"{SC}/{c['id']}/deliver", json={"delivery_date": str(date.today())})
    assert r.status_code == 200, r.text
    r = await cli.post(f"{SC}/{c['id']}/deliver", json={})
    assert r.status_code == 400
    r = await cli.post(f"{SC}/{c['id']}/payment-schedule-template", json={
        "installments": [{"label": "كامل", "pct": "100", "days_offset": 0}],
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_sc_template_rejects_negative_pct(cli):
    cid = await _cust(cli)
    c = (await _contract(cli, cid)).json()
    r = await cli.post(f"{SC}/{c['id']}/payment-schedule-template", json={
        "installments": [{"pct": "150"}, {"pct": "-50", "days_offset": 10}],
    })
    assert r.status_code == 400


# ════════════════ CERTIFICATES ════════════════════════════════════════
@pytest.mark.asyncio
async def test_wcc_and_warranty_second_tenant_and_isolation(cli, cli2):
    c1 = await _cust(cli)
    r = await cli.post(WCC, json={"customer_id": c1, "date": str(date.today()), "project_name": "م1"})
    assert r.status_code == 201, r.text
    wcc_id = r.json()["id"]
    c2 = await _cust(cli2, "عميل ت2")
    r2 = await cli2.post(WCC, json={"customer_id": c2, "date": str(date.today()), "project_name": "م2"})
    assert r2.status_code == 201, r2.text
    assert (await cli2.get(f"{WCC}/{wcc_id}")).status_code == 404
    # ربط شهادة ضمان بشهادة إتمام لعميل مختلف → 400
    r = await cli.post(WR, json={
        "customer_id": await _cust(cli, "عميل آخر"), "wcc_id": wcc_id, "date": str(date.today()),
        "product_name": "منتج", "warranty_start": str(date.today()),
    })
    assert r.status_code == 400
    r = await cli.post(WR, json={
        "customer_id": c1, "wcc_id": wcc_id, "date": str(date.today()),
        "product_name": "منتج", "warranty_start": str(date.today()),
    })
    assert r.status_code == 201, r.text
    wr_id = r.json()["id"]
    assert (await cli2.get(f"{WR}/{wr_id}")).status_code == 404
    # حذف شهادة إتمام مرتبطة بشهادة ضمان → 409
    assert (await cli.delete(f"{WCC}/{wcc_id}")).status_code == 409
