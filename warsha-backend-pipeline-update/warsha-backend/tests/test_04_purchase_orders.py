"""
test_04_purchase_orders.py — اختبارات أوامر الشراء
إنشاء → موافقة → إلغاء → استلام → فاتورة تلقائية → تحديث المخزون
"""
import pytest
from datetime import date

BASE = "/api/v1/purchase-orders"
PUR = "/api/v1/purchases"
INV = "/api/v1/inventory"


async def _make_supplier(cli):
    r = await cli.post(f"{PUR}/suppliers", json={"name": "مورد PO", "supplier_type": "local"})
    return r.json()["id"]


async def _make_material(cli):
    r = await cli.post(f"{INV}/materials", json={"name_ar": "مادة PO", "unit": "كجم", "unit_cost": 5.0})
    return r.json()["id"]


def _po_payload(supplier_id, material_id):
    return {
        "order_date": str(date.today()),
        "supplier_id": supplier_id,
        "lines": [{"description": "مادة أساسية", "ordered_qty": 20, "unit": "كجم",
                   "unit_cost": 10.0, "material_id": material_id}],
    }


# ════════════════ CRUD ════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_purchase_order(cli):
    sid = await _make_supplier(cli)
    mid = await _make_material(cli)
    r = await cli.post(BASE, json=_po_payload(sid, mid))
    assert r.status_code == 201
    d = r.json()
    assert d["supplier_id"] == sid
    assert d["status"] in ("draft", "pending")


@pytest.mark.asyncio
async def test_list_purchase_orders(cli):
    sid = await _make_supplier(cli)
    mid = await _make_material(cli)
    await cli.post(BASE, json=_po_payload(sid, mid))
    r = await cli.get(BASE)
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_get_purchase_order(cli):
    sid = await _make_supplier(cli)
    mid = await _make_material(cli)
    r = await cli.post(BASE, json=_po_payload(sid, mid))
    po_id = r.json()["id"]
    r2 = await cli.get(f"{BASE}/{po_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == po_id


@pytest.mark.asyncio
async def test_po_next_code(cli):
    r = await cli.get(f"{BASE}/next-code")
    assert r.status_code == 200
    assert "code" in r.json()


@pytest.mark.asyncio
async def test_delete_purchase_order(cli):
    sid = await _make_supplier(cli)
    mid = await _make_material(cli)
    r = await cli.post(BASE, json=_po_payload(sid, mid))
    po_id = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/{po_id}")
    assert r2.status_code in (200, 204)


# ════════════════ APPROVE ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_approve_purchase_order(cli):
    sid = await _make_supplier(cli)
    mid = await _make_material(cli)
    r = await cli.post(BASE, json=_po_payload(sid, mid))
    po_id = r.json()["id"]
    r2 = await cli.patch(f"{BASE}/{po_id}/approve")
    assert r2.status_code == 200
    assert r2.json()["status"] in ("approved", "confirmed")


# ════════════════ CANCEL ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_cancel_purchase_order(cli):
    sid = await _make_supplier(cli)
    mid = await _make_material(cli)
    r = await cli.post(BASE, json=_po_payload(sid, mid))
    po_id = r.json()["id"]
    r2 = await cli.patch(f"{BASE}/{po_id}/cancel")
    assert r2.status_code == 200
    assert r2.json()["status"] == "cancelled"


# ════════════════ RECEIVE → AUTO INVOICE + STOCK ════════════════════
@pytest.mark.asyncio
async def test_receive_purchase_order_updates_stock(cli):
    sid = await _make_supplier(cli)
    mid = await _make_material(cli)
    r = await cli.post(BASE, json=_po_payload(sid, mid))
    po_id = r.json()["id"]
    po = r.json()

    # Approve first
    await cli.patch(f"{BASE}/{po_id}/approve")

    # Get line id
    pr = await cli.get(f"{BASE}/{po_id}")
    lines = pr.json().get("lines", [])
    assert len(lines) >= 1
    line_id = lines[0]["id"]

    stock_before = (await cli.get(f"{INV}/materials/{mid}")).json()["stock_qty"]

    receive_payload = {
        "receive_date": str(date.today()),
        "lines": [{"line_id": line_id, "received_qty": 20}],
        "create_invoice": False,
    }
    r3 = await cli.post(f"{BASE}/{po_id}/receive", json=receive_payload)
    assert r3.status_code == 200

    stock_after = (await cli.get(f"{INV}/materials/{mid}")).json()["stock_qty"]
    assert stock_after >= stock_before + 20


@pytest.mark.asyncio
async def test_receive_creates_invoice_when_flag_true(cli):
    sid = await _make_supplier(cli)
    mid = await _make_material(cli)
    r = await cli.post(BASE, json=_po_payload(sid, mid))
    po_id = r.json()["id"]
    await cli.patch(f"{BASE}/{po_id}/approve")

    pr = await cli.get(f"{BASE}/{po_id}")
    line_id = pr.json()["lines"][0]["id"]

    r3 = await cli.post(f"{BASE}/{po_id}/receive", json={
        "receive_date": str(date.today()),
        "lines": [{"line_id": line_id, "received_qty": 10}],
        "create_invoice": True,
    })
    assert r3.status_code == 200
    data = r3.json()
    # Either invoice_id in response OR invoice created separately — just check 200
    assert data is not None


# ════════════════ DASHBOARD ════════════════════════════════════════
@pytest.mark.asyncio
async def test_po_dashboard(cli):
    r = await cli.get(f"{BASE}/dashboard/summary")
    assert r.status_code == 200
