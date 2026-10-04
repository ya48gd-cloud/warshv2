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


# ══ جولة 2 — إصلاح ثغرة تكامل: kind بلا تحقق enum كان بيسمح بقيمة عشوائية
# تكسر ترتيب _default_treasury (كاش قبل بنك) وتخلي create_treasury يربط
# الخزنة بحساب 1110 بصمت وكأنها "cash" حتى لو مقصود منها تكون حاجة تانية ═══
@pytest.mark.asyncio
async def test_create_treasury_rejects_invalid_kind(cli):
    r = await cli.post(f"{BASE}/accounts", json=_account("خزينة غريبة", kind="wallet"))
    assert r.status_code == 422


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


# ══ جولة 2 — إصلاح ثغرة تكامل: flow بلا تحقق enum كان بيسمح بقيمة عشوائية
# تُخزَّن كما هي بدون أي معنى محاسبي ═══════════════════════════════════
@pytest.mark.asyncio
async def test_create_category_rejects_invalid_flow(cli):
    r = await cli.post(f"{BASE}/categories", json={"name": "فئة غريبة", "flow": "sideways"})
    assert r.status_code == 422


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


# ════════════════ VALIDATION HARDENING ═══════════════════════════════
async def _balance(cli, tid):
    return {a["id"]: a for a in (await cli.get(f"{BASE}/accounts")).json()}[tid]["balance"]


@pytest.mark.asyncio
async def test_transaction_rejects_mismatched_category_flow(cli):
    tid = (await cli.post(f"{BASE}/accounts", json=_account("خزنة فئة", opening=500.0))).json()["id"]
    cid = (await cli.post(f"{BASE}/categories", json={"name": "إيراد فقط", "flow": "in"})).json()["id"]
    r = await cli.post(f"{BASE}/transactions", json={
        "treasury_id": tid, "direction": "out", "amount": 10, "category_id": cid,
    })
    assert r.status_code == 400
    assert await _balance(cli, tid) == 500.0


@pytest.mark.asyncio
async def test_transaction_rejects_unknown_treasury_and_category(cli):
    r = await cli.post(f"{BASE}/transactions", json={
        "treasury_id": 999999, "direction": "in", "amount": 10,
    })
    assert r.status_code == 404
    tid = (await cli.post(f"{BASE}/accounts", json=_account("خزنة فئة مجهولة"))).json()["id"]
    r2 = await cli.post(f"{BASE}/transactions", json={
        "treasury_id": tid, "direction": "in", "amount": 10, "category_id": 999999,
    })
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_transaction_amount_rounded_to_cents(cli):
    tid = (await cli.post(f"{BASE}/accounts", json=_account("خزنة تقريب"))).json()["id"]
    r = await cli.post(f"{BASE}/transactions", json={
        "treasury_id": tid, "direction": "in", "amount": "10.005",
    })
    assert r.status_code == 201
    assert r.json()["amount"] == 10.01
    assert await _balance(cli, tid) == 10.01


@pytest.mark.asyncio
async def test_inactive_treasury_rejects_new_transactions(cli):
    tid = (await cli.post(f"{BASE}/accounts", json=_account("خزنة معطلة", opening=100.0))).json()["id"]
    u = await cli.put(f"{BASE}/accounts/{tid}", json={"name": "خزنة معطلة", "is_active": False})
    assert u.status_code == 200 and u.json()["is_active"] is False
    r = await cli.post(f"{BASE}/transactions", json={"treasury_id": tid, "direction": "in", "amount": 5})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_treasury_keeps_kind_when_omitted(cli):
    tid = (await cli.post(f"{BASE}/accounts", json=_account("بنك ثابت", kind="bank"))).json()["id"]
    u = await cli.put(f"{BASE}/accounts/{tid}", json={"name": "بنك ثابت 2"})
    assert u.status_code == 200
    assert u.json()["kind"] == "bank"


@pytest.mark.asyncio
async def test_delete_category_in_use_blocked(cli):
    tid = (await cli.post(f"{BASE}/accounts", json=_account("خزنة فئة مستخدمة"))).json()["id"]
    cid = (await cli.post(f"{BASE}/categories", json={"name": "فئة مستخدمة", "flow": "both"})).json()["id"]
    await cli.post(f"{BASE}/transactions", json={
        "treasury_id": tid, "direction": "in", "amount": 5, "category_id": cid,
    })
    r = await cli.delete(f"{BASE}/categories/{cid}")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_delete_transfer_leg_removes_both_legs(cli):
    a = (await cli.post(f"{BASE}/accounts", json=_account("مصدر حذف", opening=1000.0))).json()["id"]
    b = (await cli.post(f"{BASE}/accounts", json=_account("وجهة حذف", opening=0.0))).json()["id"]
    r = await cli.post(f"{BASE}/transfer", json={"from_treasury_id": a, "to_treasury_id": b, "amount": 300})
    assert r.status_code == 201
    grp_txns = [t for t in (await cli.get(f"{BASE}/transactions")).json()
                if t["source"] == "transfer" and t["treasury_id"] in (a, b)]
    assert len(grp_txns) == 2
    d = await cli.delete(f"{BASE}/transactions/{grp_txns[0]['id']}")
    assert d.status_code == 204
    left = [t for t in (await cli.get(f"{BASE}/transactions")).json()
            if t["source"] == "transfer" and t["treasury_id"] in (a, b)]
    assert left == []
    assert await _balance(cli, a) == 1000.0
    assert await _balance(cli, b) == 0.0


@pytest.mark.asyncio
async def test_transfer_to_unknown_treasury_rejected(cli):
    a = (await cli.post(f"{BASE}/accounts", json=_account("مصدر مجهول", opening=100.0))).json()["id"]
    r = await cli.post(f"{BASE}/transfer", json={"from_treasury_id": a, "to_treasury_id": 999999, "amount": 10})
    assert r.status_code == 404
    assert await _balance(cli, a) == 100.0


@pytest.mark.asyncio
async def test_edit_cancelled_cheque_does_not_inflate_balance(cli):
    tid = (await cli.post(f"{BASE}/accounts", json=_account("خزنة شيك", opening=0.0))).json()["id"]
    r = await cli.post(f"{BASE}/transactions", json={
        "treasury_id": tid, "direction": "in", "amount": 200, "payment_method": "cheque",
        "cheque_number": "CH-1", "cheque_date": str(date.today()),
    })
    assert r.status_code == 201
    xid = r.json()["id"]
    c = await cli.patch(f"{BASE}/cheques/{xid}/status", json={"cheque_status": "cancelled"})
    assert c.status_code == 200
    assert await _balance(cli, tid) == 0.0
    u = await cli.put(f"{BASE}/transactions/{xid}", json={
        "treasury_id": tid, "direction": "in", "amount": 300,
    })
    assert u.status_code == 200
    assert await _balance(cli, tid) == 0.0
    s = (await cli.get(f"{BASE}/summary")).json()
    assert all(t["balance"] >= 0 for t in s["treasuries"])


# ════════════════ HARDENING ROUND 3 ══════════════════════════════════
@pytest.mark.asyncio
async def test_delete_in_txn_blocked_when_balance_would_go_negative(cli):
    tid = (await cli.post(f"{BASE}/accounts", json=_account("خزنة سالب"))).json()["id"]
    rin = await cli.post(f"{BASE}/transactions", json={"treasury_id": tid, "direction": "in", "amount": 100})
    assert rin.status_code == 201
    rout = await cli.post(f"{BASE}/transactions", json={"treasury_id": tid, "direction": "out", "amount": 80})
    assert rout.status_code == 201
    d = await cli.delete(f"{BASE}/transactions/{rin.json()['id']}")
    assert d.status_code == 409
    assert await _balance(cli, tid) == 20.0
    # حذف الصرف ثم القبض مسموح
    assert (await cli.delete(f"{BASE}/transactions/{rout.json()['id']}")).status_code == 204
    assert (await cli.delete(f"{BASE}/transactions/{rin.json()['id']}")).status_code == 204
    assert await _balance(cli, tid) == 0.0


@pytest.mark.asyncio
async def test_delete_transfer_blocked_when_destination_spent(cli):
    a = (await cli.post(f"{BASE}/accounts", json=_account("مصدر سالب", opening=500.0))).json()["id"]
    b = (await cli.post(f"{BASE}/accounts", json=_account("وجهة سالب"))).json()["id"]
    assert (await cli.post(f"{BASE}/transfer", json={
        "from_treasury_id": a, "to_treasury_id": b, "amount": 300})).status_code == 201
    assert (await cli.post(f"{BASE}/transactions", json={
        "treasury_id": b, "direction": "out", "amount": 250})).status_code == 201
    leg = [t for t in (await cli.get(f"{BASE}/transactions")).json()
           if t["source"] == "transfer" and t["treasury_id"] == b][0]
    d = await cli.delete(f"{BASE}/transactions/{leg['id']}")
    assert d.status_code == 409
    assert await _balance(cli, a) == 200.0
    assert await _balance(cli, b) == 50.0


@pytest.mark.asyncio
async def test_edit_in_txn_blocked_when_balance_would_go_negative(cli):
    tid = (await cli.post(f"{BASE}/accounts", json=_account("خزنة تعديل سالب"))).json()["id"]
    rin = await cli.post(f"{BASE}/transactions", json={"treasury_id": tid, "direction": "in", "amount": 100})
    await cli.post(f"{BASE}/transactions", json={"treasury_id": tid, "direction": "out", "amount": 90})
    u = await cli.put(f"{BASE}/transactions/{rin.json()['id']}", json={
        "treasury_id": tid, "direction": "in", "amount": 50})
    assert u.status_code == 409
    assert await _balance(cli, tid) == 10.0


@pytest.mark.asyncio
async def test_category_flow_change_blocked_with_opposite_txns(cli):
    tid = (await cli.post(f"{BASE}/accounts", json=_account("خزنة فئة مرنة"))).json()["id"]
    cid = (await cli.post(f"{BASE}/categories", json={"name": "فئة مرنة", "flow": "both"})).json()["id"]
    await cli.post(f"{BASE}/transactions", json={
        "treasury_id": tid, "direction": "in", "amount": 5, "category_id": cid})
    r = await cli.put(f"{BASE}/categories/{cid}", json={"name": "فئة مرنة", "flow": "out"})
    assert r.status_code == 409
    r2 = await cli.put(f"{BASE}/categories/{cid}", json={"name": "فئة مرنة", "flow": "in"})
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_treasury_tenant_isolation(cli, cli2):
    tid = (await cli.post(f"{BASE}/accounts", json=_account("خزنة t1", opening=100.0))).json()["id"]
    x = (await cli.post(f"{BASE}/transactions", json={
        "treasury_id": tid, "direction": "in", "amount": 10})).json()["id"]
    assert all(a["id"] != tid for a in (await cli2.get(f"{BASE}/accounts")).json())
    assert all(t["id"] != x for t in (await cli2.get(f"{BASE}/transactions")).json())
    assert (await cli2.put(f"{BASE}/transactions/{x}", json={
        "treasury_id": tid, "direction": "in", "amount": 1})).status_code == 404
    assert (await cli2.delete(f"{BASE}/transactions/{x}")).status_code == 404
    assert (await cli2.get(f"{BASE}/transactions/{x}/print")).status_code == 404
    assert await _balance(cli, tid) == 110.0


@pytest.mark.asyncio
async def test_viewer_cannot_write_treasury(cli):
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.api.auth import create_token
    tok = create_token(user_id=987654, username="viewer_tr", role="viewer", tenant_id=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {tok}"}) as v:
        assert (await v.get(f"{BASE}/accounts")).status_code == 200
        r = await v.post(f"{BASE}/accounts", json=_account("خزنة مشاهد"))
        assert r.status_code == 403
