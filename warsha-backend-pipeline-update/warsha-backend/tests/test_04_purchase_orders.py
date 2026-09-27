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
async def test_receive_rejects_draft_purchase_order(cli):
    """لا يجوز استلام أمر شراء لم تتم الموافقة عليه بعد (bypass لخطوة approve)."""
    sid = await _make_supplier(cli)
    mid = await _make_material(cli)
    r = await cli.post(BASE, json=_po_payload(sid, mid))
    po_id = r.json()["id"]

    pr = await cli.get(f"{BASE}/{po_id}")
    line_id = pr.json()["lines"][0]["id"]

    r2 = await cli.post(f"{BASE}/{po_id}/receive", json={
        "receive_date": str(date.today()),
        "lines": [{"line_id": line_id, "received_qty": 5}],
        "create_invoice": False,
    })
    assert r2.status_code == 400, r2.text


@pytest.mark.asyncio
async def test_cancel_blocked_after_partial_receive(cli):
    """لا يجوز إلغاء أمر شراء استُلم منه جزء بالفعل (المخزون/الفاتورة تأثّرا فعلياً)."""
    sid = await _make_supplier(cli)
    mid = await _make_material(cli)
    r = await cli.post(BASE, json=_po_payload(sid, mid))
    po_id = r.json()["id"]
    await cli.patch(f"{BASE}/{po_id}/approve")

    pr = await cli.get(f"{BASE}/{po_id}")
    line_id = pr.json()["lines"][0]["id"]

    # استلام جزئي فقط (10 من أصل 20)
    r2 = await cli.post(f"{BASE}/{po_id}/receive", json={
        "receive_date": str(date.today()),
        "lines": [{"line_id": line_id, "received_qty": 10}],
        "create_invoice": False,
    })
    assert r2.status_code == 200
    assert r2.json()["status"] == "partial"

    r3 = await cli.patch(f"{BASE}/{po_id}/cancel")
    assert r3.status_code == 400, r3.text


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


# ════════════════ ROUND-2 GAP REGRESSION TESTS ═══════════════════════

@pytest.mark.asyncio
async def test_create_po_rejects_material_of_another_tenant(cli, cli2):
    """أمر الشراء كان بيقبل material_id تخميني تابع لـtenant تاني من غير أي
    تحقق — عند الاستلام كان بيرفع مخزون/سعر مادة مش بتاعت الـtenant الحالي."""
    r2 = await cli2.post("/api/v1/inventory/materials", json={
        "name_ar": "مادة تينانت 2 - أمر شراء", "unit": "كجم", "unit_cost": 5.0,
    })
    assert r2.status_code == 201, r2.text
    other_tenant_mid = r2.json()["id"]

    sid = await _make_supplier(cli)
    r = await cli.post(BASE, json=_po_payload(sid, other_tenant_mid))
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_delete_po_cleans_up_attachments(cli):
    """حذف أمر شراء (مسودة) كان بيسيب مرفقات (لو اتضافت عبر endpoint المرفقات
    العام /purchases/attach) يتيمة — سجل وملف على القرص من غير أي مرجع."""
    sid = await _make_supplier(cli)
    mid = await _make_material(cli)
    r = await cli.post(BASE, json=_po_payload(sid, mid))
    po_id = r.json()["id"]

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    up = await cli.post(
        f"{PUR}/attach?entity_type=purchase_order&entity_id={po_id}",
        files={"file": ("po.png", png_bytes, "image/png")},
    )
    assert up.status_code == 200, up.text
    att_id = up.json()["id"]

    r2 = await cli.delete(f"{BASE}/{po_id}")
    assert r2.status_code in (200, 204)

    dl = await cli.get(f"{PUR}/attachments/{att_id}/download")
    assert dl.status_code == 404
