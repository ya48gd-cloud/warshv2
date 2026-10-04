"""
test_21_super_admin.py — اختبارات Super Admin (إدارة الورش على مستوى النظام)

يغطي بالأساس محطة الإصلاح: DELETE /super-admin/workshops/{id} كان بيعمل
db.delete(tenant) مباشرة من غير أي فحص لبيانات مرتبطة — وبما إن كل ورشة
جديدة بيتزرع لها فورًا chart_of_accounts + warehouses + admin user، فالحذف
الفعلي كان دايمًا هيصطدم بـ IntegrityError غير معالج (500) على أي قاعدة
بيانات بتفرض قيود FK فعليًا (PostgreSQL في production)، أو يسيب بيانات يتيمة
بصمت (SQLite بدون FK زي بيئة الاختبار). الإصلاح: نمنع الحذف النهائي طالما
فيه مستخدمين ونوجّه لاستخدام التعطيل (toggle) بدلاً منه.
"""
import pytest

SUPER = "/api/v1/super-admin"

_LOGIN = {"username": "superadmin", "password": "changeme123"}


async def _super_headers(client):
    r = await client.post(f"{SUPER}/login", json=_LOGIN)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_workshop(client, headers, slug="sa-test-shop"):
    r = await client.post(f"{SUPER}/workshops", json={
        "workshop_name": "ورشة اختبار سوبر أدمن",
        "workshop_slug": slug,
        "admin_username": "sa_admin",
        "admin_password": "secret123",
    }, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["tenant"]["id"]


@pytest.mark.asyncio
async def test_super_login_rejects_bad_credentials(client):
    r = await client.post(f"{SUPER}/login", json={"username": "superadmin", "password": "wrong"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_super_login_and_create_workshop(client):
    headers = await _super_headers(client)
    tid = await _create_workshop(client, headers, slug="sa-create-shop")
    assert tid > 0

    r = await client.get(f"{SUPER}/workshops", headers=headers)
    assert r.status_code == 200
    rows = r.json()
    match = next(w for w in rows if w["id"] == tid)
    assert match["user_count"] == 1


@pytest.mark.asyncio
async def test_delete_workshop_with_users_is_blocked(client):
    """كان قبل الإصلاح بيحاول يحذف مباشرة (خطر IntegrityError/بيانات يتيمة).
    دلوقتي المفروض يترفض بـ 409 وواضح إن في مستخدمين لسه مرتبطين."""
    headers = await _super_headers(client)
    tid = await _create_workshop(client, headers, slug="sa-blocked-delete")

    r = await client.delete(f"{SUPER}/workshops/{tid}", headers=headers)
    assert r.status_code == 409, r.text
    assert "مستخدم" in r.json()["detail"]

    # الورشة لازم تفضل موجودة فعلاً (مالحدفش)
    r2 = await client.get(f"{SUPER}/workshops", headers=headers)
    assert any(w["id"] == tid for w in r2.json())


@pytest.mark.asyncio
async def test_toggle_workshop_still_works_as_the_safe_alternative(client):
    headers = await _super_headers(client)
    tid = await _create_workshop(client, headers, slug="sa-toggle-shop")

    r = await client.put(f"{SUPER}/workshops/{tid}/toggle", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False

    r2 = await client.put(f"{SUPER}/workshops/{tid}/toggle", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["is_active"] is True


@pytest.mark.asyncio
async def test_delete_nonexistent_workshop_still_404(client):
    """الحماية الجديدة (فحص عدد المستخدمين) لازم تيجي بعد فحص وجود الورشة نفسها —
    مش قبله (رسالة 404 واضحة لورشة مش موجودة، مش 409 مضلِّل)."""
    headers = await _super_headers(client)
    r = await client.delete(f"{SUPER}/workshops/999999", headers=headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_workshop_validates_and_seeds_warehouses(client):
    headers = await _super_headers(client)
    r = await client.post(f"{SUPER}/workshops", json={
        "workshop_name": "ورشة", "workshop_slug": "sa-short-pass",
        "admin_username": "a", "admin_password": "123",
    }, headers=headers)
    assert r.status_code == 400
    r = await client.post(f"{SUPER}/workshops", json={
        "workshop_name": "ورشة", "workshop_slug": "sa-bad-plan",
        "admin_username": "a", "admin_password": "secret123", "plan": "x" * 40,
    }, headers=headers)
    assert r.status_code == 400

    tid = await _create_workshop(client, headers, slug="sa-seeded-shop")
    from sqlalchemy import select, func
    from app.models.models import Warehouse
    from tests.conftest import TestSessionLocal
    async with TestSessionLocal() as db:
        n = await db.scalar(select(func.count()).select_from(Warehouse).where(Warehouse.tenant_id == tid))
    assert n and n >= 8


@pytest.mark.asyncio
async def test_super_login_non_ascii_password_is_401_not_500(client):
    r = await client.post(f"{SUPER}/login", json={"username": "superadmin", "password": "كلمة"})
    assert r.status_code == 401
