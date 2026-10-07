"""
test_feat_audit.py — سجل التدقيق (Audit Log)

يغطي: الالتقاط المركزي (create/update/delete) · diff الحقول المتغيرة فقط ·
إخفاء الحقول الحساسة · تصنيف permission_change/password_change · أحداث login/logout ·
RBAC · عزل الـ tenant · الفلاتر والـ pagination · التشغيل خارج سياق الطلب ·
immutability · retention · احتواء الأخطاء.

كل التأكيدات مفلترة بـ entity_type/action لأن conftest بيزرع مخازن افتراضية (صفوف audit).
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import insert, select, func, event

from app.main import app
from app.api.auth import create_token, hash_password
from app.core import audit_context as actx
from app.core import audit_events
from app.models.models import AuditLog, AuditSetting, Customer, Material, User, ScrapSale, ScrapSaleLine
from app.models.models import AnalyticsEvent, StockQuant
from app.services import audit as svc
from app.services import audit_retention as ret
from tests.conftest import TestSessionLocal, test_engine

AUD = "/api/v1/audit"
CUST = "/api/v1/customers"
USERS = "/api/v1/users"


# ── helpers ──────────────────────────────────────────────────────────
async def _rows(**kw):
    """صفوف audit_logs مباشرة من الـ DB (بدون سياق tenant) مرتبة بالـ id."""
    async with TestSessionLocal() as s:
        q = select(AuditLog).order_by(AuditLog.id)
        for k, v in kw.items():
            q = q.where(getattr(AuditLog, k) == v)
        return (await s.execute(q)).scalars().all()


def _role_client(role, tid=1, uid=9):
    tok = create_token(user_id=uid, username=f"{role}_u", role=role, tenant_id=tid)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {tok}"})


async def _mk_user(username, password="Pass1234!", tenant_id=1, role="admin", active=True, legacy=False):
    import hashlib
    async with TestSessionLocal() as s:
        u = User(tenant_id=tenant_id, username=username, role=role, is_active=active,
                 full_name=username,
                 password_hash=(hashlib.sha256(password.encode()).hexdigest() if legacy
                                else hash_password(password)))
        s.add(u)
        await s.commit()
        return u.id


async def _insert_raw(**kw):
    base = dict(tenant_id=1, created_at=datetime(2026, 3, 10, 12, 0, 0), action="update",
                entity_type="customers", entity_id=1, entity_ref="x", user_id=1,
                user_name="admin", changes=None, extra=None, ip=None, path=None,
                request_id=None, summary=None)
    base.update(kw)
    async with TestSessionLocal() as s:
        await s.execute(insert(AuditLog.__table__), [base])
        await s.commit()


def _cust(name="عميل اختبار"):
    return {"name": name, "phone": "0500000099", "credit_limit": 5000.0}


# ════════════════ الالتقاط (create / update / delete) ════════════════
@pytest.mark.asyncio
async def test_create_update_delete_captured(cli):
    r = await cli.post(CUST, json=_cust("عميل أ"))
    assert r.status_code == 201
    cid = r.json()["id"]
    assert (await cli.put(f"{CUST}/{cid}", json=_cust("عميل ب"))).status_code == 200
    assert (await cli.delete(f"{CUST}/{cid}")).status_code in (200, 204)

    rows = await _rows(entity_type="customers", entity_id=cid)
    assert [x.action for x in rows] == ["create", "update", "delete"]
    for x in rows:
        assert x.tenant_id == 1
        assert x.user_id == 1 and x.user_name == "admin"
        assert x.ip  # httpx ASGITransport يمرر عنوان العميل
        assert x.path and x.path.startswith(("POST ", "PUT ", "DELETE ")) and "/customers" in x.path
        assert x.request_id and len(x.request_id) == 32
    # كل طلب له request_id مختلف
    assert len({x.request_id for x in rows}) == 3
    assert rows[0].changes["name"] == {"new": "عميل أ"}
    assert rows[1].changes["name"] == {"old": "عميل أ", "new": "عميل ب"}
    assert rows[2].changes["name"] == {"old": "عميل ب"}
    assert rows[0].entity_ref == "عميل أ" or rows[0].entity_ref  # مرجع بشري موجود
    # tenant_id و id لا يظهران كحقول في create/delete
    assert "tenant_id" not in rows[0].changes and "id" not in rows[0].changes


@pytest.mark.asyncio
async def test_update_diff_only_changed_fields(cli):
    cid = (await cli.post(CUST, json=_cust())).json()["id"]
    # نفس القيم بالضبط (حتى Decimal 5000.0 مقابل 5000.00) => لا صف
    assert (await cli.put(f"{CUST}/{cid}", json=_cust())).status_code == 200
    assert (await cli.put(f"{CUST}/{cid}", json=_cust())).status_code == 200
    assert [x for x in await _rows(entity_type="customers", entity_id=cid) if x.action == "update"] == []
    # تغيير حقل واحد
    p = _cust()
    p["phone"] = "0111111111"
    assert (await cli.put(f"{CUST}/{cid}", json=p)).status_code == 200
    ups = [x for x in await _rows(entity_type="customers", entity_id=cid) if x.action == "update"]
    assert len(ups) == 1
    assert list(ups[0].changes.keys()) == ["phone"]
    assert ups[0].changes["phone"]["new"] == "0111111111"
    assert "updated_at" not in ups[0].changes and "created_at" not in ups[0].changes


@pytest.mark.asyncio
async def test_users_last_login_alone_is_not_audited():
    uid = await _mk_user("ll_user")
    base = len(await _rows(entity_type="users", entity_id=uid))
    async with TestSessionLocal() as s:
        u = await s.get(User, uid)
        u.last_login = datetime(2026, 1, 1)
        await s.commit()
    assert len(await _rows(entity_type="users", entity_id=uid)) == base


@pytest.mark.asyncio
async def test_entity_history_endpoint(cli, cli2):
    cid = (await cli.post(CUST, json=_cust("هـ1"))).json()["id"]
    for n in ("هـ2", "هـ3"):
        assert (await cli.put(f"{CUST}/{cid}", json=_cust(n))).status_code == 200
    assert (await cli.delete(f"{CUST}/{cid}")).status_code in (200, 204)
    r = await cli.get(f"{AUD}/entities/customers/{cid}")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4
    assert [i["action"] for i in body["items"]] == ["delete", "update", "update", "create"]
    assert all("changes" in i and "extra" in i for i in body["items"])
    assert body["items"][0]["action_label"] == "حذف"
    assert body["items"][0]["entity_label"] == "عميل"
    # كيان بنوع غير مُدقَّق => 400
    assert (await cli.get(f"{AUD}/entities/not_a_table/1")).status_code == 400
    # tenant آخر لا يرى شيئًا
    r2 = await cli2.get(f"{AUD}/entities/customers/{cid}")
    assert r2.status_code == 200 and r2.json()["items"] == [] and r2.json()["total"] == 0


# ════════════════ الحقول الحساسة ═══════════════════════════════════════
@pytest.mark.asyncio
async def test_sensitive_masking(cli):
    r = await cli.post(f"{USERS}/", json={"username": "mask_u", "password": "Secret123!",
                                          "role": "accountant"})
    assert r.status_code == 201
    uid = r.json()["id"]
    assert (await cli.post(f"{USERS}/{uid}/reset-password",
                           json={"new_password": "NewSecret456!"})).status_code == 200
    tok = create_token(user_id=uid, username="mask_u", role="accountant", tenant_id=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {tok}"}) as c:
        r = await c.post("/api/v1/auth/change-password",
                         json={"old_password": "NewSecret456!", "new_password": "Third789!x"})
        assert r.status_code == 200, r.text

    rows = await _rows(entity_type="users", entity_id=uid)
    assert [x.action for x in rows] == ["create", "password_change", "password_change"]
    assert rows[0].changes["password_hash"] == {"new": "***"}
    assert rows[1].changes["password_hash"] == {"old": "***", "new": "***"}
    # أي صف في الجدول (أو أي رد API) لا يحتوي الهاش أو كلمات المرور
    async with TestSessionLocal() as s:
        allrows = (await s.execute(select(AuditLog))).scalars().all()
    blob = json.dumps([(x.changes, x.extra, x.summary, x.entity_ref) for x in allrows],
                      ensure_ascii=False)
    for needle in ("pbkdf2", "Secret123", "NewSecret456", "Third789"):
        assert needle not in blob
    api = (await cli.get(f"{AUD}/logs", params={"entity_type": "users", "include_changes": True})).text
    for needle in ("pbkdf2", "Secret123", "NewSecret456", "Third789"):
        assert needle not in api


@pytest.mark.asyncio
async def test_permission_change_classification(cli):
    await _mk_user("filler_admin")   # لتفادي تطابق id المستخدم مع sub التوكن (حماية تعطيل حسابك)
    uid = (await cli.post(f"{USERS}/", json={"username": "perm_u", "password": "Pass1234!",
                                             "role": "accountant"})).json()["id"]
    assert (await cli.put(f"{USERS}/{uid}", json={"role": "production"})).status_code == 200
    assert (await cli.put(f"{USERS}/{uid}", json={"is_active": False})).status_code == 200
    assert (await cli.put(f"{USERS}/{uid}", json={"full_name": "اسم جديد"})).status_code == 200
    rows = await _rows(entity_type="users", entity_id=uid)
    assert [x.action for x in rows] == ["create", "permission_change", "permission_change", "update"]
    assert rows[1].changes["role"] == {"old": "accountant", "new": "production"}
    assert rows[2].changes["is_active"] == {"old": True, "new": False}
    assert list(rows[3].changes.keys()) == ["full_name"]


@pytest.mark.asyncio
async def test_legacy_hash_upgrade_on_login_is_plain_update_without_actor(client):
    uid = await _mk_user("legacy_u", legacy=True)
    r = await client.post("/api/v1/auth/login", json={"username": "legacy_u", "password": "Pass1234!",
                                                      "tenant_slug": "tenant-1"})
    assert r.status_code == 200
    rows = await _rows(entity_type="users", entity_id=uid)
    ups = [x for x in rows if x.action == "update"]
    assert len(ups) == 1 and ups[0].user_id is None
    assert ups[0].changes["password_hash"] == {"old": "***", "new": "***"}
    assert not [x for x in rows if x.action == "password_change"]


# ════════════════ login / logout ═══════════════════════════════════════
@pytest.mark.asyncio
async def test_login_events(client, cli):
    uid = await _mk_user("bob1")
    ok = await client.post("/api/v1/auth/login", json={"username": "bob1", "password": "Pass1234!",
                                                       "tenant_slug": "tenant-1"})
    assert ok.status_code == 200
    rows = await _rows(action="login")
    assert len(rows) == 1
    assert rows[0].tenant_id == 1 and rows[0].user_id == uid and rows[0].user_name == "bob1"
    assert rows[0].entity_type == "users" and rows[0].entity_id == uid
    assert rows[0].ip and rows[0].path == "POST /api/v1/auth/login"

    bad = await client.post("/api/v1/auth/login", json={"username": "bob1", "password": "WRONG-pw-1",
                                                        "tenant_slug": "tenant-1"})
    assert bad.status_code == 401
    f = await _rows(action="login_failed")
    assert len(f) == 1
    assert f[0].tenant_id == 1 and f[0].user_id == uid and f[0].user_name == "bob1"
    assert f[0].extra == {"reason": "bad_password", "username_attempted": "bob1"}
    assert "WRONG-pw-1" not in json.dumps([f[0].extra, f[0].changes, f[0].summary])

    # slug معروف + اسم مستخدم غير موجود => يُسجَّل (الورشة معروفة) بدون user_id
    bad2 = await client.post("/api/v1/auth/login", json={"username": "ghost", "password": "x12345",
                                                         "tenant_slug": "tenant-2"})
    assert bad2.status_code == 401
    f = await _rows(action="login_failed", tenant_id=2)
    assert len(f) == 1 and f[0].user_id is None and f[0].user_name == "ghost"

    # slug مجهول => لا صف ولا انهيار
    n_before = len(await _rows(action="login_failed"))
    r = await client.post("/api/v1/auth/login", json={"username": "x", "password": "y",
                                                      "tenant_slug": "no-such-tenant"})
    assert r.status_code == 401
    assert len(await _rows(action="login_failed")) == n_before

    # logout
    lo = await cli.post("/api/v1/auth/logout")
    assert lo.status_code == 200 and lo.json() == {"message": "تم تسجيل الخروج"}
    rows = await _rows(action="logout")
    assert len(rows) == 1 and rows[0].tenant_id == 1 and rows[0].user_id == 1
    assert rows[0].user_name == "admin"


@pytest.mark.asyncio
async def test_logout_requires_token(client):
    r = await client.post("/api/v1/auth/logout")
    assert r.status_code == 401
    assert await _rows(action="logout") == []


@pytest.mark.asyncio
async def test_login_failed_inactive_and_disabled_tenant(client):
    await _mk_user("inact", active=False)
    r = await client.post("/api/v1/auth/login", json={"username": "inact", "password": "Pass1234!",
                                                      "tenant_slug": "tenant-1"})
    assert r.status_code == 403
    f = await _rows(action="login_failed")
    assert len(f) == 1 and f[0].extra["reason"] == "inactive"

    from sqlalchemy import text
    async with TestSessionLocal() as s:
        await s.execute(text("UPDATE tenants SET is_active = 0 WHERE id = 2"))
        await s.commit()
    try:
        r = await client.post("/api/v1/auth/login", json={"username": "x", "password": "y",
                                                          "tenant_slug": "tenant-2"})
        assert r.status_code == 403
        f = await _rows(action="login_failed", tenant_id=2)
        assert len(f) == 1 and f[0].extra["reason"] == "tenant_disabled"
    finally:
        async with TestSessionLocal() as s:
            await s.execute(text("UPDATE tenants SET is_active = 1 WHERE id = 2"))
            await s.commit()


@pytest.mark.asyncio
async def test_login_failed_ambiguous_username_writes_nothing(client):
    await _mk_user("dupname", tenant_id=1)
    await _mk_user("dupname", tenant_id=2)
    r = await client.post("/api/v1/auth/login", json={"username": "dupname", "password": "wrongpw1"})
    assert r.status_code == 401
    assert await _rows(action="login_failed") == []
    # اسم فريد في ورشة واحدة بدون slug => يُسجَّل لتلك الورشة
    uid = await _mk_user("solo_u", tenant_id=2)
    r = await client.post("/api/v1/auth/login", json={"username": "solo_u", "password": "wrongpw1"})
    assert r.status_code == 401
    f = await _rows(action="login_failed")
    assert len(f) == 1 and f[0].tenant_id == 2 and f[0].user_id == uid
    # اسم غير موجود نهائيًا => لا شيء
    r = await client.post("/api/v1/auth/login", json={"username": "nobody_x", "password": "wrongpw1"})
    assert r.status_code == 401
    assert len(await _rows(action="login_failed")) == 1


# ════════════════ RBAC ════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["viewer", "accountant", "production"])
async def test_rbac_non_admin_forbidden(role):
    async with _role_client(role) as c:
        for path in ("/logs", "/logs/1", "/entities/customers/1", "/meta", "/settings"):
            assert (await c.get(AUD + path)).status_code == 403, (role, path)
        assert (await c.put(AUD + "/settings", json={"retention_days": 100})).status_code == 403


@pytest.mark.asyncio
async def test_rbac_no_token_and_admin_ok(client, cli):
    for path in ("/logs", "/meta", "/settings", "/entities/customers/1"):
        assert (await client.get(AUD + path)).status_code == 401
    assert (await client.put(AUD + "/settings", json={"retention_days": 100})).status_code == 401
    for path in ("/logs", "/meta", "/settings"):
        assert (await cli.get(AUD + path)).status_code == 200


@pytest.mark.asyncio
async def test_only_put_settings_is_non_get_route(cli):
    non_get = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/v1/audit"):
            for m in (getattr(route, "methods", None) or set()):
                if m not in ("GET", "HEAD"):
                    non_get.append((m, path))
    assert non_get == [("PUT", "/api/v1/audit/settings")]
    await cli.post(CUST, json=_cust())
    lid = (await _rows(entity_type="customers"))[0].id
    for method in ("delete", "patch", "post", "put"):
        r = await getattr(cli, method)(f"{AUD}/logs/{lid}")
        assert r.status_code in (404, 405)
    for method in ("delete", "post", "put", "patch"):
        r = await getattr(cli, method)(f"{AUD}/logs")
        assert r.status_code in (404, 405)
    assert (await cli.post(f"{AUD}/purge")).status_code in (404, 405)
    assert await _rows(entity_type="customers")  # لم يُمسح شيء


# ════════════════ عزل الـ tenant ══════════════════════════════════════
@pytest.mark.asyncio
async def test_tenant_isolation(cli, cli2):
    c1 = (await cli.post(CUST, json=_cust("ورشة1"))).json()["id"]
    c2 = (await cli2.post(CUST, json=_cust("ورشة2"))).json()["id"]
    await _mk_user("t1_user", tenant_id=1)
    await _mk_user("t2_user", tenant_id=2)

    r1 = (await cli.get(f"{AUD}/logs", params={"entity_type": "customers"})).json()
    r2 = (await cli2.get(f"{AUD}/logs", params={"entity_type": "customers"})).json()
    assert all(i["entity_id"] == c1 for i in r1["items"]) and r1["total"] == 1
    assert all(i["entity_id"] == c2 for i in r2["items"]) and r2["total"] == 1

    # تفاصيل سجل ورشة أخرى => 404
    t2_row = (await _rows(entity_type="customers", tenant_id=2))[0]
    assert (await cli.get(f"{AUD}/logs/{t2_row.id}")).status_code == 404
    assert (await cli2.get(f"{AUD}/logs/{t2_row.id}")).status_code == 200
    # فلتر user_id لا يعبر الورش
    r = (await cli.get(f"{AUD}/logs", params={"user_id": 2})).json()
    assert all(i["user_id"] == 2 for i in r["items"])
    assert not any(x["entity_id"] == c2 and x["entity_type"] == "customers" for x in r["items"])
    # meta
    m1 = (await cli.get(f"{AUD}/meta")).json()
    assert {u["username"] for u in m1["users"]} == {"t1_user"}
    m2 = (await cli2.get(f"{AUD}/meta")).json()
    assert {u["username"] for u in m2["users"]} == {"t2_user"}
    # الإعدادات لا تتسرب
    assert (await cli.put(f"{AUD}/settings", json={"retention_days": 120})).status_code == 200
    assert (await cli2.get(f"{AUD}/settings")).json()["custom"] is False
    assert (await cli.get(f"{AUD}/settings")).json()["retention_days"] == 120
    assert (await cli.get(f"{AUD}/logs", params={"entity_type": "audit_settings"})).json()["total"] >= 1
    assert (await cli2.get(f"{AUD}/logs", params={"entity_type": "audit_settings"})).json()["total"] == 0


# ════════════════ الفلاتر والـ pagination ══════════════════════════════
@pytest.mark.asyncio
async def test_filters_and_pagination(cli):
    base = datetime(2026, 3, 10, 12, 0, 0)
    for i in range(5):
        await _insert_raw(created_at=base + timedelta(minutes=i), entity_id=100 + i,
                          entity_ref=f"ref_{i}", user_id=7, user_name="سامي", action="update",
                          entity_type="invoices")
    await _insert_raw(created_at=base, entity_type="customers", entity_id=5, entity_ref="100%_real",
                      user_id=8, user_name="منى", action="create")
    p = {"entity_type": "invoices"}
    r = (await cli.get(f"{AUD}/logs", params={**p, "limit": 2})).json()
    assert r["total"] == 5 and len(r["items"]) == 2 and r["limit"] == 2 and r["offset"] == 0
    assert [i["entity_id"] for i in r["items"]] == [104, 103]  # الأحدث أولًا
    r = (await cli.get(f"{AUD}/logs", params={**p, "limit": 2, "offset": 4})).json()
    assert len(r["items"]) == 1 and r["items"][0]["entity_id"] == 100
    assert (await cli.get(f"{AUD}/logs", params={"user_id": 8})).json()["total"] == 1
    assert (await cli.get(f"{AUD}/logs", params={"action": "create", "entity_type": "customers"})).json()["total"] == 1
    assert (await cli.get(f"{AUD}/logs", params={"entity_type": "invoices", "entity_id": 102})).json()["total"] == 1
    # q: ilike على المرجع واسم المستخدم + تهريب % و _
    assert (await cli.get(f"{AUD}/logs", params={"q": "ref_3"})).json()["total"] == 1
    assert (await cli.get(f"{AUD}/logs", params={"q": "سامي"})).json()["total"] == 5
    assert (await cli.get(f"{AUD}/logs", params={"q": "%"})).json()["total"] == 1   # فقط "100%_real"
    assert (await cli.get(f"{AUD}/logs", params={"q": "_"})).json()["total"] >= 1
    assert (await cli.get(f"{AUD}/logs", params={"q": "ref_"})).json()["total"] == 5
    assert (await cli.get(f"{AUD}/logs", params={"q": "refX"})).json()["total"] == 0
    # include_changes
    it = (await cli.get(f"{AUD}/logs", params={**p, "include_changes": True, "limit": 1})).json()["items"][0]
    assert "changes" in it
    it = (await cli.get(f"{AUD}/logs", params={**p, "limit": 1})).json()["items"][0]
    assert "changes" not in it and "extra" not in it and it["changed_fields"] == []
    assert it["created_at"].endswith("Z")
    # أخطاء
    assert (await cli.get(f"{AUD}/logs", params={"action": "insert"})).status_code == 400
    assert (await cli.get(f"{AUD}/logs", params={"entity_type": "zzz"})).status_code == 400
    assert (await cli.get(f"{AUD}/logs", params={"date_from": "2026-03-12", "date_to": "2026-03-10"})).status_code == 400
    assert (await cli.get(f"{AUD}/logs", params={"limit": 1000})).status_code == 422
    assert (await cli.get(f"{AUD}/logs", params={"limit": 0})).status_code == 422
    assert (await cli.get(f"{AUD}/logs", params={"offset": -1})).status_code == 422
    assert (await cli.get(f"{AUD}/logs", params={"date_from": "garbage"})).status_code == 422
    assert (await cli.get(f"{AUD}/logs", params={"tz_offset_minutes": 900})).status_code == 422


@pytest.mark.asyncio
async def test_date_filters_and_tz_offset(cli):
    # 2026-03-10 22:30 UTC  =  2026-03-11 00:30 بتوقيت القاهرة (+120)
    await _insert_raw(created_at=datetime(2026, 3, 10, 22, 30, 0), entity_type="invoices",
                      entity_id=1, entity_ref="edge")
    def q(**kw):
        return {"entity_type": "invoices", **kw}
    g = lambda **kw: cli.get(f"{AUD}/logs", params=q(**kw))
    assert (await g(date_from="2026-03-10", date_to="2026-03-10")).json()["total"] == 1
    assert (await g(date_from="2026-03-11", date_to="2026-03-11")).json()["total"] == 0
    assert (await g(date_from="2026-03-11", date_to="2026-03-11", tz_offset_minutes=120)).json()["total"] == 1
    assert (await g(date_from="2026-03-10", date_to="2026-03-10", tz_offset_minutes=120)).json()["total"] == 0
    # حدود شاملة لنهاية اليوم
    await _insert_raw(created_at=datetime(2026, 3, 10, 23, 59, 59), entity_type="invoices",
                      entity_id=2, entity_ref="end")
    assert (await g(date_to="2026-03-10")).json()["total"] == 2
    assert (await g(date_from="2026-03-11")).json()["total"] == 0
    assert (await g(date_from="2026-03-10")).json()["total"] == 2


@pytest.mark.asyncio
async def test_meta_shape(cli):
    m = (await cli.get(f"{AUD}/meta")).json()
    assert [a["value"] for a in m["actions"]] == list(svc.ACTIONS)
    assert all(a["label"] for a in m["actions"])
    ets = {e["value"]: e["label"] for e in m["entity_types"]}
    assert ets["customers"] == "عميل" and ets["invoices"] == "فاتورة" and ets["users"] == "مستخدم"
    assert "audit_logs" not in ets and "analytics_events" not in ets and "stock_quants" not in ets
    assert m["retention"] == {"days": 730, "custom": False, "default_days": 730,
                              "min_days": 90, "max_days": 3650}


# ════════════════ خارج سياق الطلب (jobs) ═══════════════════════════════
@pytest.mark.asyncio
async def test_outside_request_context():
    assert actx.audit_user_id.get() is None
    async with TestSessionLocal() as s:
        m = Material(tenant_id=1, code="JOB-1", name_ar="خامة", name_en="m", unit="kg",
                     unit_cost=10, category_id=1)
        s.add(m)
        await s.commit()
        mid = m.id
        m.unit_cost = Decimal("12.50")
        await s.commit()
    rows = await _rows(entity_type="materials", entity_id=mid)
    assert [x.action for x in rows] == ["create", "update"]
    assert all(x.user_id is None and x.user_name is None and x.tenant_id == 1 for x in rows)
    assert all(x.request_id is None and x.ip is None and x.path is None for x in rows)
    assert rows[1].changes["unit_cost"] == {"old": "10", "new": "12.50"} or \
        rows[1].changes["unit_cost"]["new"] == "12.50"

    # كائن tenant 2 يتعدّل في job بدون ContextVar: الصف يتبع tenant الكائن
    async with TestSessionLocal() as s:
        m2 = Material(tenant_id=2, code="JOB-2", name_ar="خامة2", name_en="m2", unit="kg",
                      unit_cost=1, category_id=2)
        s.add(m2)
        await s.commit()
        m2id = m2.id
        m2.name_ar = "اسم جديد"
        await s.commit()
    t2 = await _rows(entity_type="materials", entity_id=m2id)
    assert [x.action for x in t2] == ["create", "update"] and all(x.tenant_id == 2 for x in t2)
    assert not [x for x in await _rows(tenant_id=1, entity_type="materials") if x.entity_id == m2id]


@pytest.mark.asyncio
async def test_installment_scheduler_job_is_audited_with_null_actor(cli):
    from datetime import date as _d
    cid = (await cli.post(CUST, json={"name": "عميل جدولة"})).json()["id"]
    r = await cli.post("/api/v1/sales/invoices", json={
        "customer_id": cid, "date": str(_d.today()),
        "lines": [{"description": "بند", "qty": 1, "unit": "قطعة", "unit_price": 500.0}]})
    assert r.status_code == 201
    iid = r.json()["id"]
    await cli.post(f"/api/v1/sales/invoices/{iid}/payment-schedule", json={
        "installments": [{"label": "متأخر", "amount": 500,
                          "due_date": str(_d.today() - timedelta(days=1))}]})
    from app.services.installment_scheduler import _run_once
    async with TestSessionLocal() as s:
        await _run_once(s)
    ups = [x for x in await _rows(entity_type="invoices", entity_id=iid) if x.action == "update"]
    job = [x for x in ups if x.user_id is None and "status" in (x.changes or {})]
    assert job and job[0].tenant_id == 1 and job[0].request_id is None
    assert job[0].changes["status"]["new"] == "overdue"


@pytest.mark.asyncio
async def test_excluded_models_and_no_recursion():
    async with TestSessionLocal() as s:
        s.add(AnalyticsEvent(tenant_id=1, event_type="x"))
        s.add(StockQuant(tenant_id=1, material_id=1, location_id=1, qty=1))
        await s.commit()
    assert await _rows(entity_type="analytics_events") == []
    assert await _rows(entity_type="stock_quants") == []
    assert await _rows(entity_type="audit_logs") == []
    n = len(await _rows())
    async with TestSessionLocal() as s:
        # قراءة صفوف التدقيق ثم flush فارغ لا يولّد صفوفًا جديدة
        (await s.execute(select(AuditLog))).scalars().all()
        await s.flush()
        await s.commit()
    assert len(await _rows()) == n


@pytest.mark.asyncio
async def test_audit_disabled_context_and_env_kill_switch(monkeypatch):
    with actx.audit_disabled():
        async with TestSessionLocal() as s:
            s.add(Customer(tenant_id=1, name="مخفي", code="HID-1"))
            await s.commit()
    assert await _rows(entity_type="customers") == []
    monkeypatch.setenv("AUDIT_ENABLED", "false")
    async with TestSessionLocal() as s:
        s.add(Customer(tenant_id=1, name="مخفي2", code="HID-2"))
        await s.commit()
    assert await _rows(entity_type="customers") == []
    monkeypatch.delenv("AUDIT_ENABLED")
    async with TestSessionLocal() as s:
        s.add(Customer(tenant_id=1, name="ظاهر", code="HID-3"))
        await s.commit()
    assert len(await _rows(entity_type="customers")) == 1


# ════════════════ rollback / احتواء الأخطاء / immutability ═════════════
@pytest.mark.asyncio
async def test_rollback_discards_audit_rows(cli):
    async with TestSessionLocal() as s:
        s.add(Customer(tenant_id=1, name="لن يُحفظ", code="RB-1"))
        await s.flush()          # after_flush كتب صف التدقيق في نفس الـ transaction
        await s.rollback()
    assert await _rows(entity_type="customers") == []
    # طلب فاشل (تكرار كود) لا يترك صفوفًا زائدة
    await cli.post(CUST, json={**_cust("أول"), "code": "DUP-1"})
    n = len(await _rows(entity_type="customers"))
    r = await cli.post(CUST, json={**_cust("تاني"), "code": "DUP-1"})
    assert r.status_code >= 400
    assert len(await _rows(entity_type="customers")) == n


@pytest.mark.asyncio
async def test_capture_failure_is_contained(cli, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(audit_events, "_build_rows", boom)
    r = await cli.post(CUST, json=_cust("رغم الفشل"))
    assert r.status_code == 201
    assert (await cli.get(f"{CUST}/{r.json()['id']}")).status_code == 200
    assert await _rows(entity_type="customers") == []


@pytest.mark.asyncio
async def test_orm_immutability_guard(cli):
    await cli.post(CUST, json=_cust())
    async with TestSessionLocal() as s:
        row = (await s.execute(select(AuditLog).where(AuditLog.entity_type == "customers"))).scalars().first()
        row.summary = "tampered"
        with pytest.raises(RuntimeError):
            await s.flush()
        await s.rollback()
    async with TestSessionLocal() as s:
        row = (await s.execute(select(AuditLog).where(AuditLog.entity_type == "customers"))).scalars().first()
        await s.delete(row)
        with pytest.raises(RuntimeError):
            await s.flush()
        await s.rollback()
    assert len(await _rows(entity_type="customers")) == 1
    assert (await _rows(entity_type="customers"))[0].summary is None


@pytest.mark.asyncio
async def test_cascade_delete_and_orphans_logged():
    async with TestSessionLocal() as s:
        sale = ScrapSale(tenant_id=1, code="SS-1", sale_date=date(2026, 1, 1), treasury_id=1,
                         total_amount=10)
        sale.lines.append(ScrapSaleLine(tenant_id=1, material_id=1, qty=1, unit_price=5, total_price=5))
        sale.lines.append(ScrapSaleLine(tenant_id=1, material_id=1, qty=2, unit_price=5, total_price=10))
        s.add(sale)
        await s.commit()
        sid = sale.id
        line_ids = [l.id for l in sale.lines]
    assert len(await _rows(entity_type="scrap_sale_lines", action="create")) == 2
    # orphan: إزالة بند من المجموعة (delete-orphan) ثم حذف الأب بالكامل (cascade)
    async with TestSessionLocal() as s:
        from sqlalchemy.orm import selectinload
        sale = (await s.execute(select(ScrapSale).options(selectinload(ScrapSale.lines))
                                .where(ScrapSale.id == sid))).scalar_one()
        sale.lines.remove(sale.lines[0])
        await s.commit()
    assert len(await _rows(entity_type="scrap_sale_lines", action="delete")) == 1
    async with TestSessionLocal() as s:
        from sqlalchemy.orm import selectinload
        sale = (await s.execute(select(ScrapSale).options(selectinload(ScrapSale.lines))
                                .where(ScrapSale.id == sid))).scalar_one()
        await s.delete(sale)
        await s.commit()
    assert len(await _rows(entity_type="scrap_sale_lines", action="delete")) == 2
    assert {x.entity_id for x in await _rows(entity_type="scrap_sale_lines", action="delete")} == set(line_ids)
    assert len(await _rows(entity_type="scrap_sales", action="delete")) == 1


@pytest.mark.asyncio
async def test_performance_one_audit_statement_per_flush():
    stmts = []

    def _hook(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO AUDIT_LOGS"):
            stmts.append(executemany)

    event.listen(test_engine.sync_engine, "before_cursor_execute", _hook)
    try:
        async with TestSessionLocal() as s:
            s.add(Customer(tenant_id=1, name="واحد", code="P-1"))
            await s.commit()
        assert stmts == [False]
        stmts.clear()
        async with TestSessionLocal() as s:
            for i in range(200):
                s.add(Customer(tenant_id=1, name=f"c{i}", code=f"PC-{i}"))
            await s.commit()
        assert stmts == [True]     # executemany واحد لكل 200 كائن
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _hook)
    assert len(await _rows(entity_type="customers")) == 201


# ════════════════ Unit: التحويل والإخفاء ═══════════════════════════════
def test_json_safe_and_masking_units():
    from datetime import timezone
    assert svc.json_safe(Decimal("10.50")) == "10.50"
    assert svc.json_safe(Decimal("1E+3")) == "1000"
    assert svc.json_safe(date(2026, 1, 2)) == "2026-01-02"
    assert svc.json_safe(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05"
    assert svc.json_safe(b"abc").startswith("[بيانات ثنائية")
    assert svc.json_safe("a\x00b") == "ab"
    assert svc.json_safe(float("nan")) is None and svc.json_safe(float("inf")) is None
    assert svc.json_safe(Decimal("NaN")) is None
    big = "x" * 5000
    assert svc.json_safe(big) == "[نص/بيانات كبيرة: 5000 حرف]"
    assert svc.json_safe("data:image/png;base64,AAAA") == "[نص/بيانات كبيرة: 26 حرف]"
    assert svc.json_safe(True) is True and svc.json_safe(5) == 5 and svc.json_safe(None) is None
    # الإخفاء
    assert svc.mask_or_safe("password_hash", "pbkdf2$x$y") == "***"
    assert svc.mask_or_safe("api_key", "k") == "***"
    assert svc.mask_or_safe("access_token", "t") == "***"
    assert svc.mask_or_safe("password_hash", None) is None
    assert svc.mask_or_safe("keyword", "legit") == "legit"       # لا نمط 'key' عام
    assert svc.mask_or_safe("monkey_count", 3) == 3
    # المقارنة الرقمية
    assert svc.values_equal(10, Decimal("10.00")) and svc.values_equal(Decimal("1.5"), 1.5)
    assert not svc.values_equal(10, Decimal("10.01"))
    assert svc.values_equal(None, None) and not svc.values_equal(None, 0) and not svc.values_equal("", None)
    # cap_changes
    ch = {f"f{i}": {"old": "y" * 900, "new": "z" * 900} for i in range(40)}
    capped = svc.cap_changes(ch)
    assert capped["_truncated"] > 0
    assert len(json.dumps(capped, ensure_ascii=False)) <= 16 * 1024
    assert svc.cap_changes({"a": {"new": 1}}) == {"a": {"new": 1}}
    # pick_ref
    assert svc.pick_ref({"code": "C1", "name": "n"}) == "C1"
    assert svc.pick_ref({"invoice_number": "INV-1", "code": "C1"}) == "INV-1"
    assert svc.pick_ref({"name": "x" * 500}) == "x" * 200
    assert svc.pick_ref({"foo": 1}) is None
    # classify
    assert svc.classify_action("users", "update", ["role"], True) == "permission_change"
    assert svc.classify_action("users", "update", ["password_hash"], True) == "password_change"
    assert svc.classify_action("users", "update", ["password_hash"], False) == "update"
    assert svc.classify_action("users", "update", ["role", "password_hash"], True) == "permission_change"
    assert svc.classify_action("customers", "update", ["role"], True) == "update"
    assert svc.classify_action("users", "create", ["role"], True) == "create"


@pytest.mark.asyncio
async def test_hostile_values_roundtrip_and_logo_data_placeholder(cli):
    from app.models.models import WorkshopProfile
    async with TestSessionLocal() as s:
        wp = WorkshopProfile(tenant_id=1, name="ورشة\x00نول", logo_data=None)
        s.add(wp)
        await s.commit()
        wid = wp.id
        wp.logo_data = "data:image/png;base64," + "A" * 50000
        wp.address = "ع" * 3000
        await s.commit()
    rows = await _rows(entity_type="workshop_profile", entity_id=wid)
    assert [x.action for x in rows] == ["create", "update"]
    assert rows[0].changes["name"]["new"] == "ورشةنول"
    assert rows[1].changes["logo_data"]["new"].startswith("[نص/بيانات كبيرة:")
    assert rows[1].changes["address"]["new"].startswith("[نص/بيانات كبيرة:")
    assert "AAAA" not in json.dumps(rows[1].changes)
    # الـ API يرجّعها كنص
    api = (await cli.get(f"{AUD}/entities/workshop_profile/{wid}")).json()
    assert api["total"] == 2


# ════════════════ Retention ═══════════════════════════════════════════
@pytest.mark.asyncio
async def test_retention_settings_validation(cli):
    g = (await cli.get(f"{AUD}/settings")).json()
    assert g == {"retention_days": None, "custom": False, "days": 730, "default_days": 730,
                 "min_days": 90, "max_days": 3650}
    for bad in (89, 3651, -1, 1):
        r = await cli.put(f"{AUD}/settings", json={"retention_days": bad})
        assert r.status_code == 400, bad
        assert r.json()["detail"] == "مدة الاحتفاظ يجب أن تكون 0 أو بين 90 و3650 يوم"
    assert (await cli.put(f"{AUD}/settings", json={"retention_days": "abc"})).status_code == 422
    assert (await cli.put(f"{AUD}/settings", json={})).status_code == 422
    r = await cli.put(f"{AUD}/settings", json={"retention_days": 90})
    assert r.status_code == 200 and r.json()["retention_days"] == 90 and r.json()["custom"] is True
    assert (await cli.get(f"{AUD}/settings")).json()["days"] == 90
    assert (await cli.get(f"{AUD}/meta")).json()["retention"]["days"] == 90
    r = await cli.put(f"{AUD}/settings", json={"retention_days": 0})
    assert r.status_code == 200 and r.json()["days"] == 0
    r = await cli.put(f"{AUD}/settings", json={"retention_days": None})
    assert r.status_code == 200 and r.json()["custom"] is False and r.json()["days"] == 730
    # تغيير الإعداد نفسه مُدقَّق
    acts = [x.action for x in await _rows(entity_type="audit_settings", tenant_id=1)]
    assert acts == ["create", "update", "delete"]   # 90 -> 0 -> null


@pytest.mark.asyncio
async def test_retention_purge_isolation(cli, cli2):
    now = datetime.utcnow()
    old = now - timedelta(days=400)
    for _ in range(3):
        await _insert_raw(tenant_id=1, created_at=old, entity_type="invoices", entity_id=1)
    for _ in range(2):
        await _insert_raw(tenant_id=2, created_at=old, entity_type="invoices", entity_id=1)
    await _insert_raw(tenant_id=1, created_at=now - timedelta(days=5), entity_type="invoices", entity_id=2)
    await _insert_raw(tenant_id=1, created_at=now - timedelta(days=200), entity_type="invoices", entity_id=3)
    assert (await cli.put(f"{AUD}/settings", json={"retention_days": 90})).status_code == 200
    res = await ret.purge_expired()
    assert res == {1: 4}   # 3 بعمر 400 يوم + 1 بعمر 200 يوم > 90
    t1 = [x for x in await _rows(tenant_id=1, entity_type="invoices")]
    assert [x.entity_id for x in t1] == [2]
    t2 = await _rows(tenant_id=2, entity_type="invoices")
    assert len(t2) == 2                                  # الافتراضي 730 يوم => باقية
    purge = await _rows(action="purge")
    assert len(purge) == 1 and purge[0].tenant_id == 1
    assert purge[0].extra["deleted"] == 4 and purge[0].extra["retention_days"] == 90
    assert purge[0].user_id is None
    # تانية: لا شيء جديد للمسح => لا صف purge جديد
    assert await ret.purge_expired() == {}
    assert len(await _rows(action="purge")) == 1
    # tenant 2 = احتفاظ دائم (0): تُتخطى حتى لو الصف عمره 800 يوم
    await _insert_raw(tenant_id=2, created_at=now - timedelta(days=800), entity_type="invoices", entity_id=9)
    assert (await cli2.put(f"{AUD}/settings", json={"retention_days": 0})).status_code == 200
    assert await ret.purge_expired() == {}
    assert len(await _rows(tenant_id=2, entity_type="invoices")) == 3
    # بدون override و default صغير عبر البيئة
    assert (await cli2.put(f"{AUD}/settings", json={"retention_days": None})).status_code == 200
    res = await ret.purge_expired()
    assert res == {2: 1}   # الصف بعمر 800 يوم فقط يتجاوز الافتراضي 730


@pytest.mark.asyncio
async def test_retention_env_default(monkeypatch):
    monkeypatch.setenv("AUDIT_DEFAULT_RETENTION_DAYS", "0")
    assert ret.default_days() == 0 and ret.effective_days(None) == 0
    monkeypatch.setenv("AUDIT_DEFAULT_RETENTION_DAYS", "garbage")
    assert ret.default_days() == 730
    monkeypatch.setenv("AUDIT_DEFAULT_RETENTION_DAYS", "0")
    await _insert_raw(tenant_id=1, created_at=datetime.utcnow() - timedelta(days=4000))
    assert await ret.purge_expired() == {}
    assert len(await _rows(tenant_id=1, entity_type="customers")) == 1


# ════════════════ Middleware context ══════════════════════════════════
@pytest.mark.asyncio
async def test_context_does_not_leak_between_requests(cli):
    await cli.post(CUST, json=_cust("سياق"))
    assert actx.audit_user_id.get() is None
    assert actx.audit_request_id.get() is None


@pytest.mark.asyncio
async def test_super_admin_label_and_trust_proxy(cli, monkeypatch):
    monkeypatch.setenv("AUDIT_TRUST_PROXY", "1")
    r = await cli.post(CUST, json=_cust("وراء بروكسي"), headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})
    assert r.status_code == 201
    rows = await _rows(entity_type="customers")
    assert rows[-1].ip == "203.0.113.7"
    monkeypatch.delenv("AUDIT_TRUST_PROXY")
    r = await cli.post(CUST, json=_cust("بدون ثقة"), headers={"X-Forwarded-For": "203.0.113.7"})
    rows = await _rows(entity_type="customers")
    assert rows[-1].ip != "203.0.113.7"


@pytest.mark.asyncio
async def test_unloaded_old_value_flagged_old_unknown():
    async with TestSessionLocal() as s:
        c = Customer(tenant_id=1, name="قديم", code="OU-1")
        s.add(c)
        await s.commit()
        cid = c.id
        s.expire(c)                 # القيم القديمة غير محمّلة => old مجهول
        c.name = "جديد"
        await s.commit()
    ups = [x for x in await _rows(entity_type="customers", entity_id=cid) if x.action == "update"]
    assert len(ups) == 1
    assert ups[0].changes["name"] == {"old": None, "old_unknown": True, "new": "جديد"}
