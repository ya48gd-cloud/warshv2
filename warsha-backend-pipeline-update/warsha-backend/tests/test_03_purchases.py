"""
test_03_purchases.py — اختبارات المشتريات
موردون · فواتير شراء · دفعات · مرفقات
"""
import pytest
from datetime import date

BASE = "/api/v1/purchases"


def _supplier():
    return {"name": "مورد تجريبي", "phone": "0500000002", "supplier_type": "local"}


def _invoice(supplier_id):
    return {
        "code": f"PI-{supplier_id}-001",
        "supplier_id": supplier_id,
        "date": str(date.today()),
        "lines": [{"description": "مادة خام", "qty": 10, "unit": "كجم", "unit_cost": 20.0, "update_stock": False}],
    }


# ════════════════ SUPPLIERS ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_supplier(cli):
    r = await cli.post(f"{BASE}/suppliers", json=_supplier())
    assert r.status_code == 201
    assert r.json()["name"] == "مورد تجريبي"


@pytest.mark.asyncio
async def test_list_suppliers(cli):
    await cli.post(f"{BASE}/suppliers", json=_supplier())
    r = await cli.get(f"{BASE}/suppliers")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_get_supplier(cli):
    r = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/suppliers/{sid}")
    assert r2.status_code == 200
    assert r2.json()["id"] == sid


@pytest.mark.asyncio
async def test_update_supplier(cli):
    r = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = r.json()["id"]
    upd = _supplier()
    upd["name"] = "مورد محدَّث"
    r2 = await cli.put(f"{BASE}/suppliers/{sid}", json=upd)
    assert r2.status_code == 200
    assert r2.json()["name"] == "مورد محدَّث"


@pytest.mark.asyncio
async def test_delete_supplier(cli):
    r = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/suppliers/{sid}")
    assert r2.status_code in (200, 204)


@pytest.mark.asyncio
async def test_supplier_not_found(cli):
    r = await cli.get(f"{BASE}/suppliers/99999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_supplier_next_code(cli):
    r = await cli.get(f"{BASE}/suppliers/next-code")
    assert r.status_code == 200
    assert "code" in r.json()


@pytest.mark.asyncio
async def test_supplier_statement(cli):
    r = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/suppliers/{sid}/statement")
    assert r2.status_code == 200


# ════════════════ PURCHASE INVOICES ══════════════════════════════════
@pytest.mark.asyncio
async def test_create_purchase_invoice(cli):
    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    r = await cli.post(f"{BASE}/invoices", json=_invoice(sid))
    assert r.status_code == 201
    d = r.json()
    assert d["supplier_id"] == sid
    assert d["subtotal"] == 200.0


@pytest.mark.asyncio
async def test_list_purchase_invoices(cli):
    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    await cli.post(f"{BASE}/invoices", json=_invoice(sid))
    r = await cli.get(f"{BASE}/invoices")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_get_purchase_invoice(cli):
    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    r = await cli.post(f"{BASE}/invoices", json=_invoice(sid))
    iid = r.json()["id"]
    r2 = await cli.get(f"{BASE}/invoices/{iid}")
    assert r2.status_code == 200
    assert r2.json()["id"] == iid


@pytest.mark.asyncio
async def test_delete_purchase_invoice(cli):
    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    r = await cli.post(f"{BASE}/invoices", json=_invoice(sid))
    iid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/invoices/{iid}")
    assert r2.status_code in (200, 204)


# ════════════════ INVOICE PAYMENT ════════════════════════════════════
@pytest.mark.asyncio
async def test_purchase_invoice_payment(cli):
    sr = await cli.post(f"{BASE}/suppliers", json=_supplier())
    sid = sr.json()["id"]
    ir = await cli.post(f"{BASE}/invoices", json=_invoice(sid))
    iid = ir.json()["id"]
    r = await cli.post(f"{BASE}/invoices/{iid}/payment", json={
        "amount": 100.0,
        "payment_date": str(date.today()),
        "payment_method": "cash",
    })
    assert r.status_code in (200, 201)
    inv = await cli.get(f"{BASE}/invoices/{iid}")
    assert inv.json()["paid_amount"] >= 100.0


# ════════════════ DASHBOARD ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_purchases_dashboard(cli):
    r = await cli.get(f"{BASE}/dashboard")
    assert r.status_code == 200
    d = r.json()
    assert "total_suppliers" in d
    assert "total_invoices" in d
