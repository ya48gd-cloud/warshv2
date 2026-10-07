"""
test_feat_approvals.py — دورة الموافقات (Approval Workflow)

يغطي: القواعد (CRUD/RBAC/تحقق/عزل) · 428 approval_required · الدورة الكاملة
(طلب → موافقة → إعادة الإجراء → consumed) · منع الموافقة الذاتية والتحايل (بصمة/مستخدم/انتهاء)
· الخطافات الستة (أمر شراء/خصم فاتورة/حذف فاتورة/سلفة/إشعار دائن/صرف خزينة)
· تماثل الانعكاس والاتزان المحاسبي · عزل الـ tenant · listeners.

الـ tokens بتستخدم ids مختلفة (الـ DB في الاختبارات مفيهاش users فعلية — الدور من التوكن).
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, update, func

from app.main import app
from app.api.auth import create_token
from app.models.models import (
    ApprovalRequest, ApprovalRule, User, Customer, Invoice, TreasuryTransaction, WorkerAdvance,
    CreditNote, PurchaseOrder,
)
from app.services import approvals as svc
from tests.conftest import TestSessionLocal

AP = "/api/v1/approvals"
SALES = "/api/v1/sales"
CUST = "/api/v1/customers"
PUR = "/api/v1/purchases"
PO = "/api/v1/purchase-orders"
INV = "/api/v1/inventory"
TRS = "/api/v1/treasury"
WRK = "/api/v1/workers"
CN = "/api/v1/credit-notes"
COA = "/api/v1/accounts"

# ids ثابتة للمستخدمين التجريبيين
REQ_ID = 13      # محاسب (الطالب)
ADM2_ID = 12     # أدمن تاني (المُوافِق)
ADM1_ID = 1      # الأدمن الافتراضي في conftest


@pytest_asyncio.fixture
async def mk():
    made = []

    def _mk(uid, name, role, tid=1):
        tok = create_token(user_id=uid, username=name, role=role, tenant_id=tid)
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                        headers={"Authorization": f"Bearer {tok}"})
        made.append(c)
        return c

    yield _mk
    for c in made:
        await c.aclose()


@pytest_asyncio.fixture
async def adm(mk):
    return mk(ADM1_ID, "admin", "admin")


@pytest_asyncio.fixture
async def adm2(mk):
    return mk(ADM2_ID, "admin_b", "admin")


@pytest_asyncio.fixture
async def acct(mk):
    return mk(REQ_ID, "acct_req", "accountant")


@pytest_asyncio.fixture
async def acct2(mk):
    return mk(14, "acct_two", "accountant")


@pytest_asyncio.fixture
async def prod(mk):
    return mk(15, "prod_u", "production")


@pytest_asyncio.fixture
async def viewer(mk):
    return mk(16, "view_u", "viewer")


@pytest_asyncio.fixture
async def adm_t2(mk):
    return mk(21, "admin_t2", "admin", tid=2)


@pytest_asyncio.fixture
async def adm2_t2(mk):
    return mk(22, "admin_t2b", "admin", tid=2)


# ── helpers ──────────────────────────────────────────────────────────
async def add_rule(c, action_key, ctype="amount_gt", threshold=100, **kw):
    body = {"name": f"قاعدة {action_key}", "action_key": action_key, "condition_type": ctype,
            "threshold": None if ctype == "always" else threshold,
            "required_role": "admin", "valid_hours": 24, "exempt_roles": [], "is_active": True}
    body.update(kw)
    r = await c.post(f"{AP}/rules", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def rows(model=ApprovalRequest, **kw):
    async with TestSessionLocal() as s:
        q = select(model).order_by(model.id)
        for k, v in kw.items():
            q = q.where(getattr(model, k) == v)
        return (await s.execute(q)).scalars().all()


async def set_req(rid, **vals):
    async with TestSessionLocal() as s:
        await s.execute(update(ApprovalRequest).where(ApprovalRequest.id == rid).values(**vals))
        await s.commit()


async def mk_customer(c, name="عميل موافقات"):
    r = await c.post(CUST, json={"name": name, "phone": "0500000001"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def inv_payload(cid, discount_pct=0, price=1000, qty=1, code=None):
    p = {"customer_id": cid, "date": str(date.today()), "tax_pct": 0, "discount_pct": discount_pct,
         "lines": [{"description": "منتج", "qty": qty, "unit": "قطعة", "unit_price": price}]}
    if code:
        p["code"] = code
    return p


async def tb(c):
    await c.post(f"{COA}/chart/seed")
    r = await c.get(f"{COA}/trial-balance")
    assert r.status_code == 200, r.text
    return {row["code"]: row for row in r.json()["accounts"]}


def approval_of(resp):
    assert resp.status_code == 428, resp.text
    j = resp.json()
    assert j["code"] == "approval_required"
    assert isinstance(j["detail"], str)
    return j["approval"]


async def make_po(c, qty=20, cost=10.0, tid=1):
    tok = create_token(user_id=ADM1_ID if tid == 1 else 21, username="setup_admin", role="admin",
                       tenant_id=tid)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {tok}"}) as ad:
        s = await ad.post(f"{PUR}/suppliers", json={"name": "مورد موافقات", "supplier_type": "local"})
        m = await ad.post(f"{INV}/materials", json={"name_ar": "مادة PO", "unit": "كجم", "unit_cost": 5.0})
    assert s.status_code in (200, 201), s.text
    assert m.status_code in (200, 201), m.text
    r = await c.post(PO, json={
        "order_date": str(date.today()), "supplier_id": s.json()["id"],
        "lines": [{"description": "مادة", "ordered_qty": qty, "unit": "كجم", "unit_cost": cost,
                   "material_id": m.json()["id"]}],
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def make_treasury(c, opening=100000.0):
    r = await c.post(f"{TRS}/accounts", json={"name": "خزينة موافقات", "kind": "cash",
                                              "opening_balance": opening})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def make_worker(c):
    r = await c.post(WRK, json={"name": "عامل موافقات", "daily_wage": 150.0, "job_title": "فني"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def adv_payload(wid, amount=500, typ="advance"):
    return {"worker_id": wid, "amount": amount, "advance_type": typ, "date": str(date.today())}


# ════════════════ بدون قواعد = سلوك الأصل ════════════════════════════
@pytest.mark.asyncio
async def test_no_rules_no_behavior_change(acct, adm):
    cid = await mk_customer(adm)
    r = await acct.post(f"{SALES}/invoices", json=inv_payload(cid, discount_pct=50))
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    po_id = await make_po(acct)
    assert (await acct.patch(f"{PO}/{po_id}/approve")).status_code == 200
    tid = await make_treasury(adm)
    assert (await acct.post(f"{TRS}/transactions", json={
        "treasury_id": tid, "direction": "out", "amount": 50000})).status_code == 201
    wid = await make_worker(adm)
    assert (await acct.post(f"{WRK}/advances", json=adv_payload(wid, 9000))).status_code == 201
    assert (await acct.delete(f"{SALES}/invoices/{iid}")).status_code == 200
    assert await rows() == []


@pytest.mark.asyncio
async def test_inactive_rule_does_not_gate(acct, adm):
    await add_rule(adm, "invoice_discount", "always", is_active=False)
    cid = await mk_customer(adm)
    assert (await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 10))).status_code == 201
    assert await rows() == []


# ════════════════ القواعد: CRUD + RBAC + تحقق ═════════════════════════
@pytest.mark.asyncio
async def test_meta_and_rules_crud(adm, acct, prod, viewer):
    m = await acct.get(f"{AP}/meta")
    assert m.status_code == 200
    keys = {a["key"] for a in m.json()["actions"]}
    assert keys == {"po_approve", "invoice_discount", "invoice_delete", "worker_advance",
                    "credit_note_issue", "treasury_out"}
    assert {r["key"] for r in m.json()["roles"]} == {"admin", "accountant"}

    created = await add_rule(adm, "po_approve", threshold=5000, name="أوامر كبيرة")
    assert created["id"] and created["threshold"] == 5000 and created["action_label"]
    assert created["exempt_roles"] == [] and isinstance(created["warnings"], list)
    rid = created["id"]

    lst = await adm.get(f"{AP}/rules")
    assert lst.status_code == 200
    assert [x["id"] for x in lst.json()["items"]] == [rid]

    up = await adm.put(f"{AP}/rules/{rid}", json={
        "name": "معدّلة", "action_key": "po_approve", "condition_type": "always",
        "required_role": "accountant", "valid_hours": 48, "exempt_roles": ["production"],
        "is_active": True})
    assert up.status_code == 200, up.text
    assert up.json()["threshold"] is None and up.json()["required_role"] == "accountant"
    assert up.json()["exempt_roles"] == ["production"] and up.json()["valid_hours"] == 48

    assert (await adm.delete(f"{AP}/rules/{rid}")).status_code == 204
    assert (await adm.get(f"{AP}/rules")).json()["items"] == []


@pytest.mark.asyncio
async def test_rules_rbac_non_admin_forbidden(adm, acct, prod, viewer):
    rule = await add_rule(adm, "po_approve", threshold=1)
    body = {"name": "x", "action_key": "po_approve", "condition_type": "always"}
    for c in (acct, prod, viewer):
        assert (await c.get(f"{AP}/rules")).status_code == 403
        assert (await c.post(f"{AP}/rules", json=body)).status_code == 403
        assert (await c.put(f"{AP}/rules/{rule['id']}", json=body)).status_code == 403
        assert (await c.delete(f"{AP}/rules/{rule['id']}")).status_code == 403
    assert len((await adm.get(f"{AP}/rules")).json()["items"]) == 1


@pytest.mark.asyncio
async def test_rule_validation(adm):
    base = {"name": "ok", "action_key": "po_approve", "condition_type": "amount_gt", "threshold": 10}

    async def bad(**over):
        r = await adm.post(f"{AP}/rules", json={**base, **over})
        assert r.status_code == 400, (over, r.text)
        assert isinstance(r.json()["detail"], str)

    await bad(action_key="nope")
    await bad(condition_type="percent_gt", threshold=10)      # percent_gt مسموح لخصم الفاتورة فقط
    await bad(condition_type="weird")
    await bad(threshold=0)
    await bad(threshold=-5)
    await bad(threshold=None)
    await bad(threshold="abc")
    await bad(required_role="production")
    await bad(required_role="viewer")
    await bad(valid_hours=0)
    await bad(valid_hours=721)
    await bad(valid_hours=2.5)
    await bad(exempt_roles=["viewer"])
    await bad(exempt_roles=["root"])
    await bad(name="   ")
    await bad(name="x" * 101)
    r = await adm.post(f"{AP}/rules", json={**base, "action_key": "invoice_discount",
                                            "condition_type": "percent_gt", "threshold": 101})
    assert r.status_code == 400
    r = await adm.post(f"{AP}/rules", json={**base, "action_key": "invoice_discount",
                                            "condition_type": "percent_gt", "threshold": 100})
    assert r.status_code == 201
    assert await rows(ApprovalRule, tenant_id=1) and len(await rows(ApprovalRule)) == 1


@pytest.mark.asyncio
async def test_rule_warning_when_no_other_approver(adm):
    r = await add_rule(adm, "po_approve", "always")
    assert r["warnings"], "لا يوجد أدمن آخر => تحذير"
    async with TestSessionLocal() as s:
        s.add(User(id=ADM2_ID, tenant_id=1, username="admin_b", password_hash="x", role="admin",
                   is_active=True))
        await s.commit()
    r2 = await add_rule(adm, "po_approve", "always", name="تانية")
    assert r2["warnings"] == []
    r3 = await add_rule(adm, "po_approve", "always", name="معفى", exempt_roles=["admin"])
    assert r3["warnings"] == []


@pytest.mark.asyncio
async def test_rules_tenant_isolation(adm, adm_t2):
    r = await add_rule(adm, "po_approve", threshold=10)
    assert (await adm_t2.get(f"{AP}/rules")).json()["items"] == []
    body = {"name": "x", "action_key": "po_approve", "condition_type": "always"}
    assert (await adm_t2.put(f"{AP}/rules/{r['id']}", json=body)).status_code == 404
    assert (await adm_t2.delete(f"{AP}/rules/{r['id']}")).status_code == 404
    assert len((await adm.get(f"{AP}/rules")).json()["items"]) == 1


@pytest.mark.asyncio
async def test_rule_delete_blocked_when_used(adm, adm2, acct):
    rule = await add_rule(adm, "po_approve", threshold=10)
    po_id = await make_po(acct)
    approval_of(await acct.patch(f"{PO}/{po_id}/approve"))
    r = await adm.delete(f"{AP}/rules/{rule['id']}")
    assert r.status_code == 409 and "عطّلها" in r.json()["detail"]
    listing = (await adm.get(f"{AP}/rules")).json()["items"][0]
    assert listing["open_requests"] == 1


# ════════════════ دورة أمر الشراء ════════════════════════════════════
@pytest.mark.asyncio
async def test_po_approval_full_flow(adm, adm2, acct):
    await add_rule(adm, "po_approve", "amount_gt", 100)       # الأمر = 20*10 = 200
    po_id = await make_po(acct)

    r1 = await acct.patch(f"{PO}/{po_id}/approve")
    a1 = approval_of(r1)
    assert a1["status"] == "pending" and a1["required_role"] == "admin"
    assert a1["action_key"] == "po_approve" and a1["amount"] == 200.0 and a1["summary"]
    rid = a1["request_id"]
    assert (await acct.get(f"{PO}/{po_id}")).json()["status"] == "draft"

    # تكرار الطلب = نفس الـ request_id (بدون صف جديد)
    a2 = approval_of(await acct.patch(f"{PO}/{po_id}/approve"))
    assert a2["request_id"] == rid
    assert len(await rows()) == 1

    # الطالب لا يوافق على طلبه
    assert (await acct.post(f"{AP}/requests/{rid}/approve", json={})).status_code == 403
    # محاسب مش مؤهل لطلب required_role=admin
    # (الطالب نفسه محاسب — 403 على الدور قبل أي حاجة)
    ok = await adm2.post(f"{AP}/requests/{rid}/approve", json={"reason": "تمام"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved" and ok.json()["expires_at"]

    # إعادة الإجراء تعدي مرة واحدة
    r3 = await acct.patch(f"{PO}/{po_id}/approve")
    assert r3.status_code == 200, r3.text
    assert (await acct.get(f"{PO}/{po_id}")).json()["status"] == "approved"
    req = (await rows())[0]
    assert req.status == "consumed" and req.consumed_by == REQ_ID and req.consumed_at
    assert req.decided_by == ADM2_ID

    # أمر تاني بنفس الإجمالي يحتاج طلب جديد (الموافقة اتستهلكت)
    po2 = await make_po(acct)
    b = approval_of(await acct.patch(f"{PO}/{po2}/approve"))
    assert b["request_id"] != rid
    assert len(await rows()) == 2


@pytest.mark.asyncio
async def test_po_below_threshold_passes_and_equal_does_not_trigger(adm, acct):
    await add_rule(adm, "po_approve", "amount_gt", 200)       # > 200 فقط، والأمر = 200 بالظبط
    po_id = await make_po(acct)
    assert (await acct.patch(f"{PO}/{po_id}/approve")).status_code == 200
    assert await rows() == []


@pytest.mark.asyncio
async def test_428_not_500_and_state_checks_come_first(adm, acct):
    await add_rule(adm, "po_approve", "always")
    po_id = await make_po(acct)
    r = await acct.patch(f"{PO}/{po_id}/approve")
    assert r.status_code == 428
    # أمر بدون مورد: فحص الحالة بيفشل 400 قبل ما نعمل أي طلب
    r2 = await acct.patch(f"{PO}/999999/approve")
    assert r2.status_code == 404
    assert len(await rows()) == 1


@pytest.mark.asyncio
async def test_po_hook_with_loaded_orm_object_regression(adm, acct):
    """po اتحمّل قبل الاستدعاء (with_for_update) ثم rollback داخل require_approval — لا MissingGreenlet."""
    await add_rule(adm, "po_approve", "always")
    po_id = await make_po(acct)
    for _ in range(2):
        r = await acct.patch(f"{PO}/{po_id}/approve")
        assert r.status_code == 428, r.text
    assert len(await rows()) == 1


# ════════════════ القرار: RBAC / حالات ══════════════════════════════
@pytest.mark.asyncio
async def test_decision_rbac_and_states(adm, adm2, acct, acct2, prod, viewer):
    await add_rule(adm, "po_approve", "always", required_role="admin")
    po_id = await make_po(acct)
    rid = approval_of(await acct.patch(f"{PO}/{po_id}/approve"))["request_id"]

    for c in (prod, viewer):
        assert (await c.post(f"{AP}/requests/{rid}/approve", json={})).status_code == 403
        assert (await c.post(f"{AP}/requests/{rid}/reject", json={"reason": "لا"})).status_code == 403
    # محاسب آخر على طلب يحتاج admin
    r = await acct2.post(f"{AP}/requests/{rid}/approve", json={})
    assert r.status_code == 403 and "مدير" in r.json()["detail"]
    # رفض بدون سبب
    assert (await adm2.post(f"{AP}/requests/{rid}/reject", json={})).status_code == 400
    assert (await adm2.post(f"{AP}/requests/{rid}/reject", json={"reason": "ab"})).status_code == 400
    ok = await adm2.post(f"{AP}/requests/{rid}/reject", json={"reason": "سعر مبالغ فيه"})
    assert ok.status_code == 200 and ok.json()["status"] == "rejected"
    assert ok.json()["decision_reason"] == "سعر مبالغ فيه"
    # قرار تاني
    assert (await adm2.post(f"{AP}/requests/{rid}/approve", json={})).status_code == 409
    assert (await adm2.post(f"{AP}/requests/{rid}/reject", json={"reason": "تاني"})).status_code == 409

    # الإجراء بعد الرفض => طلب جديد مع previous_rejection
    a = approval_of(await acct.patch(f"{PO}/{po_id}/approve"))
    assert a["request_id"] != rid
    assert a["previous_rejection"] == {"request_id": rid, "reason": "سعر مبالغ فيه"}
    assert (await acct.get(f"{PO}/{po_id}")).json()["status"] == "draft"


@pytest.mark.asyncio
async def test_accountant_can_decide_accountant_required_and_admin_can_too(adm, adm2, acct, acct2):
    await add_rule(adm, "po_approve", "always", required_role="accountant")
    po1 = await make_po(acct)
    rid = approval_of(await acct.patch(f"{PO}/{po1}/approve"))["request_id"]
    ok = await acct2.post(f"{AP}/requests/{rid}/approve", json={})
    assert ok.status_code == 200, ok.text
    assert (await acct.patch(f"{PO}/{po1}/approve")).status_code == 200
    po2 = await make_po(acct)
    rid2 = approval_of(await acct.patch(f"{PO}/{po2}/approve"))["request_id"]
    assert (await adm2.post(f"{AP}/requests/{rid2}/approve", json={})).status_code == 200


@pytest.mark.asyncio
async def test_strictest_rule_wins(adm, adm2, acct):
    await add_rule(adm, "po_approve", "amount_gt", 100, required_role="accountant", name="a")
    await add_rule(adm, "po_approve", "amount_gt", 150, required_role="admin", name="b")
    po_id = await make_po(acct)
    a = approval_of(await acct.patch(f"{PO}/{po_id}/approve"))
    assert a["required_role"] == "admin"


@pytest.mark.asyncio
async def test_cancel_and_cancel_rbac(adm, adm2, acct, acct2, viewer):
    await add_rule(adm, "po_approve", "always")
    po_id = await make_po(acct)
    rid = approval_of(await acct.patch(f"{PO}/{po_id}/approve"))["request_id"]
    assert (await viewer.post(f"{AP}/requests/{rid}/cancel")).status_code == 403
    assert (await acct2.post(f"{AP}/requests/{rid}/cancel")).status_code == 403   # غير الطالب وغير أدمن
    c = await acct.post(f"{AP}/requests/{rid}/cancel")
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert (await adm2.post(f"{AP}/requests/{rid}/approve", json={})).status_code == 409
    assert (await acct.post(f"{AP}/requests/{rid}/cancel")).status_code == 409
    # الطلب الملغي مبقاش يحجز الفهرس: إعادة المحاولة تعمل طلب جديد
    a = approval_of(await acct.patch(f"{PO}/{po_id}/approve"))
    assert a["request_id"] != rid
    # أدمن يلغي طلب غيره
    assert (await adm2.post(f"{AP}/requests/{a['request_id']}/cancel")).status_code == 200


@pytest.mark.asyncio
async def test_approved_request_can_be_cancelled_then_not_usable(adm, adm2, acct):
    await add_rule(adm, "po_approve", "always")
    po_id = await make_po(acct)
    rid = approval_of(await acct.patch(f"{PO}/{po_id}/approve"))["request_id"]
    await adm2.post(f"{AP}/requests/{rid}/approve", json={})
    assert (await acct.post(f"{AP}/requests/{rid}/cancel")).status_code == 200
    assert (await acct.patch(f"{PO}/{po_id}/approve")).status_code == 428


@pytest.mark.asyncio
async def test_expired_approval_not_usable_and_replacement_has_no_integrity_error(adm, adm2, acct):
    await add_rule(adm, "po_approve", "always")
    po_id = await make_po(acct)
    rid = approval_of(await acct.patch(f"{PO}/{po_id}/approve"))["request_id"]
    await adm2.post(f"{AP}/requests/{rid}/approve", json={})
    # نخلي الموافقة منتهية من غير ما نمرّر على sweep
    await set_req(rid, expires_at=datetime.utcnow() - timedelta(minutes=1))
    r = await acct.patch(f"{PO}/{po_id}/approve")
    a = approval_of(r)                       # 428 مش 409/500 (IntegrityError)
    assert a["request_id"] != rid and a["status"] == "pending"
    old = [x for x in await rows() if x.id == rid][0]
    assert old.status == "expired"
    assert (await acct.get(f"{PO}/{po_id}")).json()["status"] == "draft"


# ════════════════ منع التحايل ═══════════════════════════════════════
@pytest.mark.asyncio
async def test_fingerprint_tamper_resistance(adm, adm2, acct):
    await add_rule(adm, "invoice_discount", "percent_gt", 10)
    cid = await mk_customer(adm)
    a = approval_of(await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 20)))
    await adm2.post(f"{AP}/requests/{a['request_id']}/approve", json={})
    # تغيير النسبة / السعر / العميل بعد الموافقة => طلب جديد
    b = approval_of(await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 25)))
    assert b["request_id"] != a["request_id"]
    c = approval_of(await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 20, price=2000)))
    assert c["request_id"] not in (a["request_id"], b["request_id"])
    cid2 = await mk_customer(adm, "عميل آخر")
    d = approval_of(await acct.post(f"{SALES}/invoices", json=inv_payload(cid2, 20)))
    assert d["request_id"] not in (a["request_id"], b["request_id"], c["request_id"])
    # الموافقة الأصلية لسه سليمة وتُستهلك بنفس المعاملات بالضبط
    ok = await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 20))
    assert ok.status_code == 201, ok.text
    req = [x for x in await rows() if x.id == a["request_id"]][0]
    assert req.status == "consumed"


@pytest.mark.asyncio
async def test_approval_bound_to_entity_and_to_requester(adm, adm2, acct, acct2):
    await add_rule(adm, "po_approve", "always")
    po_a = await make_po(acct)
    po_b = await make_po(acct)
    ra = approval_of(await acct.patch(f"{PO}/{po_a}/approve"))["request_id"]
    await adm2.post(f"{AP}/requests/{ra}/approve", json={})
    # موافقة PO A مش بتفتح PO B
    rb = approval_of(await acct.patch(f"{PO}/{po_b}/approve"))
    assert rb["request_id"] != ra
    # مستخدم تاني مش بيستهلك موافقة غيره
    other = approval_of(await acct2.patch(f"{PO}/{po_a}/approve"))
    assert other["request_id"] not in (ra, rb["request_id"])
    reqs = {x.id: x for x in await rows()}
    assert reqs[ra].status == "approved" and reqs[other["request_id"]].requested_by == 14
    assert (await acct.patch(f"{PO}/{po_a}/approve")).status_code == 200


@pytest.mark.asyncio
async def test_exempt_roles_skip_rule(adm, acct):
    await add_rule(adm, "po_approve", "always", exempt_roles=["accountant"])
    po_id = await make_po(acct)
    assert (await acct.patch(f"{PO}/{po_id}/approve")).status_code == 200
    assert await rows() == []


# ════════════════ خصم الفاتورة (4 مسارات) ══════════════════════════
@pytest.mark.asyncio
async def test_invoice_create_discount_conditions(adm, adm2, acct):
    await add_rule(adm, "invoice_discount", "amount_gt", 150, name="قيمة")
    cid = await mk_customer(adm)
    # خصم 10% من 1000 = 100 (<=150) يعدي
    assert (await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 10))).status_code == 201
    # خصم صفر يعدي
    assert (await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 0))).status_code == 201
    # 20% = 200 > 150 => 428، ولا فاتورة جديدة ولا تأثير على رصيد العميل
    bal_before = (await adm.get(f"{CUST}/{cid}")).json()["balance"]
    n_before = len((await adm.get(f"{SALES}/invoices")).json())
    approval_of(await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 20)))
    assert len((await adm.get(f"{SALES}/invoices")).json()) == n_before
    assert (await adm.get(f"{CUST}/{cid}")).json()["balance"] == bal_before


@pytest.mark.asyncio
async def test_invoice_update_gate_only_when_discount_changes(adm, adm2, acct):
    cid = await mk_customer(adm)
    r = await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 20))   # قبل القاعدة
    assert r.status_code == 201
    iid = r.json()["id"]
    await add_rule(adm, "invoice_discount", "percent_gt", 10)
    # تعديل بدون تغيير الخصم (ملاحظات) => يعدي
    p = inv_payload(cid, 20)
    p["notes"] = "ملاحظة"
    assert (await acct.put(f"{SALES}/invoices/{iid}", json=p)).status_code == 200
    # رفع الخصم => 428
    a = approval_of(await acct.put(f"{SALES}/invoices/{iid}", json=inv_payload(cid, 30)))
    assert (await adm.get(f"{SALES}/invoices/{iid}")).json()["discount_pct"] == 20
    await adm2.post(f"{AP}/requests/{a['request_id']}/approve", json={})
    assert (await acct.put(f"{SALES}/invoices/{iid}", json=inv_payload(cid, 30))).status_code == 200
    assert (await adm.get(f"{SALES}/invoices/{iid}")).json()["discount_pct"] == 30
    # تخفيض الخصم لقيمة أقل من العتبة => يعدي
    assert (await acct.put(f"{SALES}/invoices/{iid}", json=inv_payload(cid, 5))).status_code == 200


@pytest.mark.asyncio
async def test_quotation_to_invoice_gated(adm, adm2, acct):
    await add_rule(adm, "invoice_discount", "percent_gt", 10)
    cid = await mk_customer(adm)
    q = await acct.post(f"{SALES}/quotations", json={
        "code": "QUO-AP-1", "customer_id": cid, "date": str(date.today()),
        "valid_until": str(date.today()), "discount_pct": 15,
        "lines": [{"description": "منتج", "qty": 1, "unit": "قطعة", "unit_price": 1000}]})
    assert q.status_code == 201, q.text
    qid = q.json()["id"]
    a = approval_of(await acct.post(f"{SALES}/quotations/{qid}/to-invoice",
                                    params={"invoice_code": "INV-AP-Q1"}))
    assert a["action_key"] == "invoice_discount"
    assert (await adm.get(f"{SALES}/invoices")).json() == []
    await adm2.post(f"{AP}/requests/{a['request_id']}/approve", json={})
    ok = await acct.post(f"{SALES}/quotations/{qid}/to-invoice", params={"invoice_code": "INV-AP-Q1"})
    assert ok.status_code in (200, 201), ok.text
    assert len((await adm.get(f"{SALES}/invoices")).json()) == 1


# ════════════════ حذف فاتورة: تماثل الانعكاس والاتزان ══════════════
@pytest.mark.asyncio
async def test_invoice_delete_symmetry_and_trial_balance(adm, adm2, acct):
    await tb(adm)   # seed COA قبل الفاتورة عشان القيد يتسجل
    cid = await mk_customer(adm)
    inv = await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 0, price=1000))
    assert inv.status_code == 201
    iid = inv.json()["id"]
    await add_rule(adm, "invoice_delete", "always")
    bal_after_create = (await adm.get(f"{CUST}/{cid}")).json()["balance"]
    assert bal_after_create == 1000.0
    t_before = await tb(adm)

    a = approval_of(await acct.delete(f"{SALES}/invoices/{iid}"))
    # لا أثر جزئي: الفاتورة موجودة، الرصيد والقيود كما هي
    assert (await adm.get(f"{SALES}/invoices/{iid}")).status_code == 200
    assert (await adm.get(f"{CUST}/{cid}")).json()["balance"] == bal_after_create
    t_mid = await tb(adm)
    assert t_mid["1210"]["total_credit"] == t_before["1210"]["total_credit"]
    assert t_mid["4100"]["total_debit"] == t_before["4100"]["total_debit"]

    await adm2.post(f"{AP}/requests/{a['request_id']}/approve", json={})
    ok = await acct.delete(f"{SALES}/invoices/{iid}")
    assert ok.status_code == 200, ok.text
    assert (await adm.get(f"{SALES}/invoices/{iid}")).status_code == 404
    assert (await adm.get(f"{CUST}/{cid}")).json()["balance"] == 0.0
    t_after = await tb(adm)
    # الانعكاس تم مرة واحدة: دائن العملاء 1210 و مدين الإيرادات 4100 زادوا بقيمة الفاتورة
    assert float(t_after["1210"]["total_credit"]) - float(t_before["1210"]["total_credit"]) == 1000.0
    assert float(t_after["4100"]["total_debit"]) - float(t_before["4100"]["total_debit"]) == 1000.0
    # الميزان متزن
    total_dr = sum(float(r["total_debit"]) for r in t_after.values())
    total_cr = sum(float(r["total_credit"]) for r in t_after.values())
    assert abs(total_dr - total_cr) < 0.01
    # الموافقة اتستهلكت؛ إعادة الحذف => 404 (حالة) مش طلب جديد
    assert (await acct.delete(f"{SALES}/invoices/{iid}")).status_code == 404
    assert len(await rows()) == 1


@pytest.mark.asyncio
async def test_invoice_delete_paid_fails_before_approval_request(adm, acct):
    cid = await mk_customer(adm)
    iid = (await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 0))).json()["id"]
    assert (await acct.post(f"{SALES}/invoices/{iid}/payment",
                            json={"amount": 100, "payment_type": "cash"})).status_code in (200, 201)
    await add_rule(adm, "invoice_delete", "always")
    r = await acct.delete(f"{SALES}/invoices/{iid}")
    assert r.status_code == 400 and await rows() == []


# ════════════════ سلفة عامل ═════════════════════════════════════════
@pytest.mark.asyncio
async def test_worker_advance_gate(adm, adm2, acct):
    await tb(adm)
    tid = await make_treasury(adm, 10000.0)
    wid = await make_worker(adm)
    await add_rule(adm, "worker_advance", "amount_gt", 300)
    # أقل من الحد وbonus يعدوا
    assert (await acct.post(f"{WRK}/advances", json=adv_payload(wid, 200))).status_code == 201
    assert (await acct.post(f"{WRK}/advances", json=adv_payload(wid, 5000, "bonus"))).status_code == 201

    bal0 = [t for t in (await adm.get(f"{TRS}/accounts")).json() if t["id"] == tid][0]["balance"]
    advs0 = len(await rows(WorkerAdvance))
    a = approval_of(await acct.post(f"{WRK}/advances", json=adv_payload(wid, 500)))
    assert len(await rows(WorkerAdvance)) == advs0
    assert [t for t in (await adm.get(f"{TRS}/accounts")).json() if t["id"] == tid][0]["balance"] == bal0

    await adm2.post(f"{AP}/requests/{a['request_id']}/approve", json={})
    ok = await acct.post(f"{WRK}/advances", json=adv_payload(wid, 500))
    assert ok.status_code == 201, ok.text
    assert len(await rows(WorkerAdvance)) == advs0 + 1
    bal1 = [t for t in (await adm.get(f"{TRS}/accounts")).json() if t["id"] == tid][0]["balance"]
    assert bal1 == bal0 - 500
    # replay فوري: لازم موافقة جديدة (مفيش صرف مزدوج)
    again = approval_of(await acct.post(f"{WRK}/advances", json=adv_payload(wid, 500)))
    assert again["request_id"] != a["request_id"]
    assert len(await rows(WorkerAdvance)) == advs0 + 1
    t = await tb(adm)
    total_dr = sum(float(r["total_debit"]) for r in t.values())
    total_cr = sum(float(r["total_credit"]) for r in t.values())
    assert abs(total_dr - total_cr) < 0.01


@pytest.mark.asyncio
async def test_worker_advance_rbac_viewer_and_production_forbidden(adm, prod, viewer):
    wid = await make_worker(adm)
    await add_rule(adm, "worker_advance", "always")
    assert (await viewer.post(f"{WRK}/advances", json=adv_payload(wid, 5))).status_code == 403
    assert (await prod.post(f"{WRK}/advances", json=adv_payload(wid, 5))).status_code == 403
    assert await rows() == []


# ════════════════ إشعار دائن ════════════════════════════════════════
@pytest.mark.asyncio
async def test_credit_note_issue_gate(adm, adm2, acct):
    cid = await mk_customer(adm)
    r = await acct.post(CN, json={"customer_id": cid, "date": str(date.today()), "return_type": "partial",
                                   "lines": [{"description": "تسوية", "qty": 1, "unit": "قطعة",
                                              "unit_price": 400}]})
    assert r.status_code == 201, r.text
    cn_id = r.json()["id"]
    await add_rule(adm, "credit_note_issue", "amount_gt", 100)
    bal0 = (await adm.get(f"{CUST}/{cid}")).json()["balance"]
    a = approval_of(await acct.patch(f"{CN}/{cn_id}/issue"))
    assert (await adm.get(f"{CN}/{cn_id}")).json()["status"] == "draft"
    assert (await adm.get(f"{CUST}/{cid}")).json()["balance"] == bal0
    await adm2.post(f"{AP}/requests/{a['request_id']}/approve", json={})
    ok = await acct.patch(f"{CN}/{cn_id}/issue")
    assert ok.status_code == 200, ok.text
    assert (await adm.get(f"{CUST}/{cid}")).json()["balance"] == bal0 - 400
    # ثاني مرة => فشل حالة (400) مش طلب جديد
    assert (await acct.patch(f"{CN}/{cn_id}/issue")).status_code == 400
    assert len(await rows()) == 1


# ════════════════ صرف الخزينة ═══════════════════════════════════════
@pytest.mark.asyncio
async def test_treasury_out_gate_create_and_update(adm, adm2, acct):
    tid = await make_treasury(adm, 100000.0)
    await add_rule(adm, "treasury_out", "amount_gt", 1000)
    # الإيداع مش متأثر مهما كان الحجم
    assert (await acct.post(f"{TRS}/transactions", json={
        "treasury_id": tid, "direction": "in", "amount": 99999})).status_code == 201
    small = await acct.post(f"{TRS}/transactions", json={
        "treasury_id": tid, "direction": "out", "amount": 800, "description": "صغير"})
    assert small.status_code == 201
    sid = small.json()["id"]
    body = {"treasury_id": tid, "direction": "out", "amount": 5000, "description": "كبير"}
    a = approval_of(await acct.post(f"{TRS}/transactions", json=body))
    assert a["amount"] == 5000.0
    accs = (await adm.get(f"{TRS}/accounts")).json()
    before = [t for t in accs if t["id"] == tid][0]["balance"]
    await adm2.post(f"{AP}/requests/{a['request_id']}/approve", json={})
    assert (await acct.post(f"{TRS}/transactions", json=body)).status_code == 201
    after = [t for t in (await adm.get(f"{TRS}/accounts")).json() if t["id"] == tid][0]["balance"]
    assert after == before - 5000

    # تعديل يرفع المصروف فوق الحد => 428، تخفيضه لا
    up_big = {"treasury_id": tid, "direction": "out", "amount": 3000, "description": "صغير"}
    a2 = approval_of(await acct.put(f"{TRS}/transactions/{sid}", json=up_big))
    assert a2["request_id"] != a["request_id"]
    up_ok = {"treasury_id": tid, "direction": "out", "amount": 500, "description": "صغير"}
    assert (await acct.put(f"{TRS}/transactions/{sid}", json=up_ok)).status_code == 200


@pytest.mark.asyncio
async def test_treasury_insufficient_balance_checked_before_approval(adm, acct):
    tid = await make_treasury(adm, 100.0)
    await add_rule(adm, "treasury_out", "always")
    r = await acct.post(f"{TRS}/transactions", json={"treasury_id": tid, "direction": "out", "amount": 500})
    assert r.status_code == 400 and await rows() == []


# ════════════════ atomic consume ════════════════════════════════════
@pytest.mark.asyncio
async def test_consume_rolls_back_when_action_fails(adm, adm2, acct, monkeypatch):
    await add_rule(adm, "invoice_discount", "percent_gt", 10)
    cid = await mk_customer(adm)
    a = approval_of(await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 20)))
    await adm2.post(f"{AP}/requests/{a['request_id']}/approve", json={})

    import app.api.sales as sales_mod
    from fastapi import HTTPException

    async def boom(*args, **kwargs):
        raise HTTPException(400, "فشل مفتعل بعد الاستهلاك")

    monkeypatch.setattr(sales_mod, "journal_sales_invoice_create", boom)
    r = await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 20))
    assert r.status_code == 400
    req = [x for x in await rows() if x.id == a["request_id"]][0]
    assert req.status == "approved", "الاستهلاك اتراجع مع فشل الإجراء"
    monkeypatch.undo()
    ok = await acct.post(f"{SALES}/invoices", json=inv_payload(cid, 20))
    assert ok.status_code == 201, ok.text
    assert [x for x in await rows() if x.id == a["request_id"]][0].status == "consumed"


@pytest.mark.asyncio
async def test_double_submit_one_open_request(adm, acct):
    await add_rule(adm, "worker_advance", "always")
    wid = await make_worker(adm)
    await tb(adm)
    await make_treasury(adm)
    for _ in range(3):
        assert (await acct.post(f"{WRK}/advances", json=adv_payload(wid, 100))).status_code == 428
    assert len(await rows()) == 1


# ════════════════ القائمة / الصندوق / العدّاد ════════════════════════
@pytest.mark.asyncio
async def test_inbox_mine_all_scopes_and_pending_count(adm, adm2, acct, acct2, prod, viewer):
    await add_rule(adm, "po_approve", "always", required_role="admin", name="a")
    po1, po2 = await make_po(acct), await make_po(acct)
    r1 = approval_of(await acct.patch(f"{PO}/{po1}/approve"))["request_id"]
    r2 = approval_of(await acct.patch(f"{PO}/{po2}/approve"))["request_id"]

    assert (await adm2.get(f"{AP}/pending-count")).json() == {
        "inbox_pending": 2, "mine_pending": 0, "mine_ready": 0}
    assert (await acct.get(f"{AP}/pending-count")).json() == {
        "inbox_pending": 0, "mine_pending": 2, "mine_ready": 0}   # محاسب مش مؤهل لطلبات admin
    assert (await prod.get(f"{AP}/pending-count")).json()["inbox_pending"] == 0
    assert (await viewer.get(f"{AP}/pending-count")).status_code == 200

    inbox = (await adm2.get(f"{AP}/requests", params={"scope": "inbox"})).json()
    assert inbox["total"] == 2 and {x["id"] for x in inbox["items"]} == {r1, r2}
    assert all(x["can_decide"] and not x["is_mine"] for x in inbox["items"])
    assert (await acct.get(f"{AP}/requests", params={"scope": "inbox"})).json()["total"] == 0
    mine = (await acct.get(f"{AP}/requests", params={"scope": "mine"})).json()
    assert mine["total"] == 2 and all(x["is_mine"] and not x["can_decide"] for x in mine["items"])
    # الأحدث أولاً
    assert [x["id"] for x in mine["items"]] == [r2, r1]
    # scope=all للأدمن فقط
    assert (await adm2.get(f"{AP}/requests", params={"scope": "all"})).json()["total"] == 2
    assert (await acct.get(f"{AP}/requests", params={"scope": "all"})).status_code == 403
    assert (await prod.get(f"{AP}/requests", params={"scope": "all"})).status_code == 403
    # production بيتجبر على mine حتى لو طلب inbox
    assert (await prod.get(f"{AP}/requests", params={"scope": "inbox"})).json()["total"] == 0

    await adm2.post(f"{AP}/requests/{r1}/approve", json={})
    assert (await acct.get(f"{AP}/pending-count")).json() == {
        "inbox_pending": 0, "mine_pending": 1, "mine_ready": 1}
    assert (await adm2.get(f"{AP}/pending-count")).json()["inbox_pending"] == 1
    # inbox الافتراضي = pending فقط، و status=all بيرجّع الكل
    assert (await adm.get(f"{AP}/requests", params={"scope": "inbox"})).json()["total"] == 1
    assert (await adm.get(f"{AP}/requests", params={"scope": "inbox", "status": "all"})).json()["total"] == 2
    assert (await adm.get(f"{AP}/requests", params={"scope": "mine", "status": "bogus"})).status_code == 400
    assert (await adm.get(f"{AP}/requests", params={"scope": "weird"})).status_code == 400
    lim = (await adm.get(f"{AP}/requests", params={"scope": "all", "limit": 1})).json()
    assert len(lim["items"]) == 1 and lim["total"] == 2


@pytest.mark.asyncio
async def test_request_detail_visibility(adm, adm2, acct, acct2, prod):
    await add_rule(adm, "po_approve", "always", required_role="admin")
    po_id = await make_po(acct)
    rid = approval_of(await acct.patch(f"{PO}/{po_id}/approve"))["request_id"]
    d = await acct.get(f"{AP}/requests/{rid}")
    assert d.status_code == 200 and d.json()["snapshot"]["params"]["supplier_id"]
    assert d.json()["snapshot"]["rule"]["condition_type"] == "always"
    assert (await adm2.get(f"{AP}/requests/{rid}")).json()["can_decide"] is True
    # محاسب آخر / production مش طالبين الطلب والـ required_role=admin => 404
    assert (await acct2.get(f"{AP}/requests/{rid}")).status_code == 404
    assert (await prod.get(f"{AP}/requests/{rid}")).status_code == 404
    assert (await adm.get(f"{AP}/requests/999999")).status_code == 404


@pytest.mark.asyncio
async def test_expired_requests_dropped_from_ready_count(adm, adm2, acct):
    await add_rule(adm, "po_approve", "always")
    po_id = await make_po(acct)
    rid = approval_of(await acct.patch(f"{PO}/{po_id}/approve"))["request_id"]
    await adm2.post(f"{AP}/requests/{rid}/approve", json={})
    assert (await acct.get(f"{AP}/pending-count")).json()["mine_ready"] == 1
    await set_req(rid, expires_at=datetime.utcnow() - timedelta(seconds=5))
    assert (await acct.get(f"{AP}/pending-count")).json()["mine_ready"] == 0
    assert [x for x in await rows() if x.id == rid][0].status == "expired"


# ════════════════ عزل الـ tenant ════════════════════════════════════
@pytest.mark.asyncio
async def test_tenant_isolation_of_requests_and_rules(adm, adm2, acct, adm_t2, adm2_t2, mk):
    await add_rule(adm, "po_approve", "always")
    po_id = await make_po(acct)
    rid = approval_of(await acct.patch(f"{PO}/{po_id}/approve"))["request_id"]
    # tenant 2: لا يرى ولا يقرر
    assert (await adm_t2.get(f"{AP}/requests", params={"scope": "all"})).json()["total"] == 0
    assert (await adm_t2.get(f"{AP}/requests/{rid}")).status_code == 404
    assert (await adm_t2.post(f"{AP}/requests/{rid}/approve", json={})).status_code == 404
    assert (await adm_t2.post(f"{AP}/requests/{rid}/reject", json={"reason": "xyz"})).status_code == 404
    assert (await adm_t2.post(f"{AP}/requests/{rid}/cancel")).status_code == 404
    assert (await adm_t2.get(f"{AP}/pending-count")).json()["inbox_pending"] == 0
    # قاعدة tenant 1 مش بتحكم tenant 2
    acct_t2 = mk(REQ_ID, "acct_req", "accountant", tid=2)
    po_t2 = await make_po(acct_t2, tid=2)
    assert (await acct_t2.patch(f"{PO}/{po_t2}/approve")).status_code == 200
    # نفس البصمة/نفس user id في tenant 2 بقاعدته الخاصة => طلب مستقل
    await add_rule(adm_t2, "po_approve", "always")
    po_t2b = await make_po(acct_t2, tid=2)
    b = approval_of(await acct_t2.patch(f"{PO}/{po_t2b}/approve"))
    assert b["request_id"] != rid
    assert (await adm2.get(f"{AP}/requests", params={"scope": "all"})).json()["total"] == 1
    assert {x.tenant_id for x in await rows()} == {1, 2}


# ════════════════ listeners ═════════════════════════════════════════
@pytest.mark.asyncio
async def test_listener_events_and_failure_isolation(adm, adm2, acct):
    seen = []

    def good(db, event, req, actor):
        seen.append((event, req.get("id"), (actor or {}).get("username")))

    async def bad(db, event, req, actor):
        raise RuntimeError("boom")

    svc.register_approval_listener(bad)
    svc.register_approval_listener(good)
    try:
        await add_rule(adm, "po_approve", "always")
        po_id = await make_po(acct)
        rid = approval_of(await acct.patch(f"{PO}/{po_id}/approve"))["request_id"]
        await adm2.post(f"{AP}/requests/{rid}/approve", json={})
        assert (await acct.patch(f"{PO}/{po_id}/approve")).status_code == 200
    finally:
        svc.unregister_approval_listener(bad)
        svc.unregister_approval_listener(good)
    assert [e[0] for e in seen] == ["requested", "approved", "consumed"]
    assert seen[1][2] == "admin_b" and seen[0][2] == "acct_req"


# ════════════════ ميتا: سلسلة الـ migrations ════════════════════════
def test_migration_chain_is_linear_to_0037():
    import re
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent / "migrations" / "versions"
    chain = {}
    for f in base.glob("0*.py"):
        txt = f.read_text(encoding="utf-8")
        rev = re.search(r"^revision\s*(?::[^=]+)?=\s*['\"](\d+)['\"]", txt, re.M)
        down = re.search(r"^down_revision\s*(?::[^=]+)?=\s*['\"](\d+)['\"]", txt, re.M)
        if rev:
            chain[rev.group(1)] = down.group(1) if down else None
    assert chain["0037"] == "0036"
    # كل رقم من 0001..0037 موجود مرة واحدة وكل down_revision هو السابق مباشرة
    for n in range(2, 38):
        assert chain[f"{n:04d}"] == f"{n - 1:04d}", n


# ════════════════ work-order → invoice (خصم) ════════════════════════
@pytest.mark.asyncio
async def test_work_order_to_invoice_discount_gated(adm, adm2, acct):
    from tests.test_20_pipeline_stations import _make_equipment, _make_customer, _make_quotation, PROD
    eq_id = await _make_equipment(adm)
    cust_id = await _make_customer(adm)
    quot = await _make_quotation(adm, cust_id, unit_price=1000, qty=1)
    order_id = (await adm.post(f"{SALES}/quotations/{quot['id']}/to-order", json={})).json()["id"]
    wo_id = (await adm.post(f"{SALES}/orders/{order_id}/to-work-order", json={
        "equipment_id": eq_id, "quantity": 1, "quantity_unit": "وحدة",
        "start_date": str(date.today())})).json()["id"]
    await adm.post(f"{PROD}/complete/{wo_id}")
    await add_rule(adm, "invoice_discount", "percent_gt", 5)

    a = approval_of(await acct.post(f"{SALES}/work-orders/{wo_id}/to-invoice", json={"discount_pct": 10}))
    assert (await adm.get(f"{SALES}/invoices")).json() == []
    await adm2.post(f"{AP}/requests/{a['request_id']}/approve", json={})
    ok = await acct.post(f"{SALES}/work-orders/{wo_id}/to-invoice", json={"discount_pct": 10})
    assert ok.status_code == 201, ok.text
    assert ok.json()["total"] == 900.0
    assert [x for x in await rows() if x.id == a["request_id"]][0].status == "consumed"
