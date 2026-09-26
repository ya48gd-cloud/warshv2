"""
test_07_treasury.py — اختبارات الخزينة
حسابات · فئات · حركات · تحويل (رصيد)
"""
import pytest
from datetime import date
from decimal import Decimal

BASE = "/api/v1/treasury"


def _account(name="خزينة رئيسية", kind="cash", opening=0.0):
    return {"name": name, "kind": kind, "opening_balance": opening}


# ════════════════ TREASURY ACCOUNTS ══════════════════════════════════
@pytest.mark.asyncio
async def test_create_treasury(cli):
    r = await cli.post(f"{BASE}/accounts", json=_account())
    assert r.status_code == 201
    d = r.json()
    assert d["name"] == "خزينة رئيسية"
    assert d["kind"] == "cash"


@pytest.mark.asyncio
async def test_list_treasuries(cli):
    await cli.post(f"{BASE}/accounts", json=_account("خزينة 1"))
    r = await cli.get(f"{BASE}/accounts")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_update_treasury(cli):
    r = await cli.post(f"{BASE}/accounts", json=_account("قديمة"))
    tid = r.json()["id"]
    r2 = await cli.put(f"{BASE}/accounts/{tid}", json=_account("محدّثة"))
    assert r2.status_code == 200
    assert r2.json()["name"] == "محدّثة"


@pytest.mark.asyncio
async def test_delete_treasury(cli):
    r = await cli.post(f"{BASE}/accounts", json=_account("للحذف"))
    tid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/accounts/{tid}")
    assert r2.status_code == 204


# ════════════════ CATEGORIES ════════════════════════════════════════
@pytest.mark.asyncio
async def test_create_category(cli):
    r = await cli.post(f"{BASE}/categories", json={"name": "مصاريف إدارية", "flow": "out"})
    assert r.status_code == 201
    assert r.json()["name"] == "مصاريف إدارية"


@pytest.mark.asyncio
async def test_list_categories(cli):
    await cli.post(f"{BASE}/categories", json={"name": "إيرادات", "flow": "in"})
    r = await cli.get(f"{BASE}/categories")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_delete_category(cli):
    r = await cli.post(f"{BASE}/categories", json={"name": "للحذف", "flow": "both"})
    cid = r.json()["id"]
    r2 = await cli.delete(f"{BASE}/categories/{cid}")
    assert r2.status_code == 204


# ════════════════ TRANSACTIONS ═══════════════════════════════════════
@pytest.mark.asyncio
async def test_create_transaction_in(cli):
    r = await cli.post(f"{BASE}/accounts", json=_account("خزينة حركة", opening=0.0))
    tid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/transactions", json={
        "treasury_id": tid,
        "direction": "in",
        "amount": 500.0,
        "txn_date": str(date.today()),
        "description": "إيراد بيع",
    })
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_transaction_increases_balance(cli):
    r = await cli.post(f"{BASE}/accounts", json=_account("خزينة رصيد", opening=100.0))
    tid = r.json()["id"]
    bal_before = r.json()["balance"]

    await cli.post(f"{BASE}/transactions", json={
        "treasury_id": tid, "direction": "in", "amount": 200.0,
        "txn_date": str(date.today()),
    })

    accounts = await cli.get(f"{BASE}/accounts")
    acc = next(a for a in accounts.json() if a["id"] == tid)
    assert acc["balance"] >= bal_before + 200.0


@pytest.mark.asyncio
async def test_list_transactions(cli):
    r = await cli.post(f"{BASE}/accounts", json=_account("خزينة قائمة"))
    tid = r.json()["id"]
    await cli.post(f"{BASE}/transactions", json={
        "treasury_id": tid, "direction": "in",
        "amount": 100.0, "txn_date": str(date.today()),
    })
    r2 = await cli.get(f"{BASE}/transactions", params={"treasury_id": tid})
    assert r2.status_code == 200
    assert len(r2.json()) >= 1


@pytest.mark.asyncio
async def test_delete_transaction(cli):
    r = await cli.post(f"{BASE}/accounts", json=_account("خزينة حذف"))
    tid = r.json()["id"]
    r2 = await cli.post(f"{BASE}/transactions", json={
        "treasury_id": tid, "direction": "in",
        "amount": 50.0, "txn_date": str(date.today()),
    })
    txn_id = r2.json()["id"]
    r3 = await cli.delete(f"{BASE}/transactions/{txn_id}")
    assert r3.status_code == 204


# ════════════════ TRANSFER ═══════════════════════════════════════════
@pytest.mark.asyncio
async def test_transfer_between_accounts(cli):
    r1 = await cli.post(f"{BASE}/accounts", json=_account("مصدر", opening=1000.0))
    r2 = await cli.post(f"{BASE}/accounts", json=_account("وجهة", opening=0.0))
    from_id = r1.json()["id"]
    to_id = r2.json()["id"]

    r3 = await cli.post(f"{BASE}/transfer", json={
        "from_treasury_id": from_id,
        "to_treasury_id": to_id,
        "amount": 300.0,
        "txn_date": str(date.today()),
    })
    assert r3.status_code in (200, 201)

    # Verify balances shifted
    accounts = {a["id"]: a for a in (await cli.get(f"{BASE}/accounts")).json()}
    assert accounts[from_id]["balance"] <= 700.0
    assert accounts[to_id]["balance"] >= 300.0


@pytest.mark.asyncio
async def test_transfer_insufficient_balance(cli):
    r1 = await cli.post(f"{BASE}/accounts", json=_account("فارغة", opening=10.0))
    r2 = await cli.post(f"{BASE}/accounts", json=_account("وجهة 2", opening=0.0))
    from_id = r1.json()["id"]
    to_id = r2.json()["id"]
    r3 = await cli.post(f"{BASE}/transfer", json={
        "from_treasury_id": from_id,
        "to_treasury_id": to_id,
        "amount": 9999.0,
        "txn_date": str(date.today()),
    })
    assert r3.status_code in (400, 422)


# ════════════════ SUMMARY ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_treasury_summary(cli):
    r = await cli.get(f"{BASE}/summary")
    assert r.status_code == 200
    d = r.json()
    assert "total_balance" in d or "total_cash" in d or "total" in d or isinstance(d, list)
