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


# ════════════════ GL SYNC (محطة تدقيق محاسبي) ═════════════════════════
# إصلاح: تعديل/حذف حركة خزينة يدوية مرتبطة بقيد محاسبي (COA) ما كانش
# بيعكس/يحدّث القيد المرتبط — فيفضل قيد قديم أو يتيم في دليل الحسابات
# وميزان المراجعة يبقى غير متوافق مع رصيد الخزينة الفعلي.
@pytest.mark.asyncio
async def test_transaction_edit_and_delete_keep_gl_in_sync(cli):
    ACC_BASE = "/api/v1/accounts"

    await cli.post(f"{ACC_BASE}/chart/seed")
    accounts = (await cli.get(f"{ACC_BASE}/chart")).json()
    cash_acc = next(a for a in accounts if a["code"] == "1110")
    expense_acc = next(a for a in accounts if a["code"] == "5500")

    tr = await cli.post(f"{BASE}/accounts", json={
        "name": "خزينة GL", "kind": "cash", "opening_balance": 1000.0,
        "coa_account_id": cash_acc["id"],
    })
    assert tr.status_code == 201
    treasury_id = tr.json()["id"]

    cat = await cli.post(f"{BASE}/categories", json={
        "name": "فئة GL", "flow": "out", "coa_account_id": expense_acc["id"],
    })
    assert cat.status_code == 201
    category_id = cat.json()["id"]

    txn = await cli.post(f"{BASE}/transactions", json={
        "treasury_id": treasury_id, "direction": "out", "amount": 100.0,
        "category_id": category_id, "txn_date": str(date.today()),
        "description": "مصروف تجريبي",
    })
    assert txn.status_code == 201
    txn_id = txn.json()["id"]

    tb = (await cli.get(f"{ACC_BASE}/trial-balance")).json()
    assert tb["totals"]["balanced"] is True
    expense_row = next(r for r in tb["accounts"] if r["code"] == "5500")
    assert expense_row["balance"] == 100.0

    # عدّل المبلغ لـ 250 — القيد المحاسبي القديم لازم يتعكس والجديد يتسجل بالمبلغ الجديد فقط
    upd = await cli.put(f"{BASE}/transactions/{txn_id}", json={
        "treasury_id": treasury_id, "direction": "out", "amount": 250.0,
        "category_id": category_id, "txn_date": str(date.today()),
        "description": "مصروف معدّل",
    })
    assert upd.status_code == 200

    tb2 = (await cli.get(f"{ACC_BASE}/trial-balance")).json()
    assert tb2["totals"]["balanced"] is True
    expense_row2 = next(r for r in tb2["accounts"] if r["code"] == "5500")
    assert expense_row2["balance"] == 250.0, (
        "رصيد حساب المصروف لازم يعكس 250 فقط بعد التعديل، مش 100 القديمة + 250 الجديدة"
    )

    # احذف الحركة — القيد بتاعها لازم ينعكس تماماً فيرجع صافي حساب المصروف صفر
    dele = await cli.delete(f"{BASE}/transactions/{txn_id}")
    assert dele.status_code == 204

    tb3 = (await cli.get(f"{ACC_BASE}/trial-balance")).json()
    assert tb3["totals"]["balanced"] is True
    expense_rows3 = [r for r in tb3["accounts"] if r["code"] == "5500"]
    assert not expense_rows3 or expense_rows3[0]["balance"] == 0.0, (
        "بعد حذف الحركة، صافي حساب المصروف لازم يرجع صفر (مايفضلش قيد يتيم)"
    )
