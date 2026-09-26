"""
test_00_auth.py — اختبارات المصادقة
signup (workshop_name+slug) · login · /me · invalid/expired token
"""
import pytest
from app.api.auth import create_token, hash_password
from app.models.models import User, Tenant
from tests.conftest import TestSessionLocal


def _signup_payload(slug="warsh-test-001"):
    return {
        "workshop_name": "ورشة اختبار",
        "workshop_slug": slug,
        "admin_username": f"admin_{slug.replace('-', '_')}",
        "admin_password": "Secure123!",
    }


async def _create_tenant_and_user(username="testadmin", password="Pass1234!", tenant_id=None):
    """Helper: create Tenant + User directly in the test DB."""
    async with TestSessionLocal() as db:
        tenant = Tenant(name="ورشة اختبار", slug=f"slug-{username}", plan="free")
        db.add(tenant)
        await db.flush()

        tid = tenant_id or tenant.id
        u = User(
            username=username,
            password_hash=hash_password(password),
            role="admin",
            tenant_id=tenant.id,
            is_active=True,
            full_name=username,
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        await db.refresh(tenant)
        return u, tenant


# ── Signup ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_signup_creates_user(client):
    r = await client.post("/api/v1/auth/signup-internal", json=_signup_payload("test-slug-001"))
    assert r.status_code == 201
    data = r.json()
    assert "token" in data
    assert "user" in data
    assert "tenant" in data


@pytest.mark.asyncio
async def test_signup_duplicate_slug(client):
    slug = "dup-slug-999"
    await client.post("/api/v1/auth/signup-internal", json=_signup_payload(slug))
    r = await client.post("/api/v1/auth/signup-internal", json=_signup_payload(slug))
    assert r.status_code in (400, 409, 422)


@pytest.mark.asyncio
async def test_signup_invalid_slug(client):
    r = await client.post("/api/v1/auth/signup-internal", json={
        "workshop_name": "ورشة",
        "workshop_slug": "bad slug!@#",
        "admin_username": "user",
        "admin_password": "Pass1234!",
    })
    assert r.status_code in (400, 422)


# ── Login ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_login_success(client):
    u, tenant = await _create_tenant_and_user("loginuser", "MyPass99!")
    r = await client.post("/api/v1/auth/login", json={
        "username": "loginuser", "password": "MyPass99!",
        "tenant_slug": tenant.slug,
    })
    assert r.status_code == 200
    assert "token" in r.json()


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    u, tenant = await _create_tenant_and_user("loginuser2", "CorrectPass1!")
    r = await client.post("/api/v1/auth/login", json={
        "username": "loginuser2", "password": "WrongPass!",
        "tenant_slug": tenant.slug,
    })
    assert r.status_code in (400, 401)


@pytest.mark.asyncio
async def test_login_nonexistent_user(client):
    r = await client.post("/api/v1/auth/login", json={
        "username": "nobody_xyz", "password": "anything",
    })
    assert r.status_code in (400, 401, 404)


@pytest.mark.asyncio
async def test_login_nonexistent_tenant_slug(client):
    r = await client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "Pass1234!",
        "tenant_slug": "no-such-tenant-9999",
    })
    assert r.status_code in (400, 401, 404)


# ── /me ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_me_with_valid_token(client):
    # Signup → real user/tenant → use returned token
    r = await client.post("/api/v1/auth/signup-internal", json=_signup_payload("me-test-slug"))
    token = r.json()["token"]
    r2 = await client.get("/api/v1/auth/me",
                          headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    data = r2.json()
    assert "username" in data or "role" in data


@pytest.mark.asyncio
async def test_me_without_token(client):
    r = await client.get("/api/v1/auth/me")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_me_with_invalid_token(client):
    r = await client.get("/api/v1/auth/me",
                         headers={"Authorization": "Bearer bad.token.here"})
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_me_with_expired_token(client):
    import base64, json, hmac as _hmac, hashlib, time
    from app.api.auth import SECRET_KEY
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b'=').decode()
    payload_data = {
        "sub": "1", "username": "x", "role": "admin",
        "tenant_id": 1, "exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200,
    }
    payload = base64.urlsafe_b64encode(
        json.dumps(payload_data, separators=(',', ':')).encode()
    ).rstrip(b'=').decode()
    sig_input = f"{header}.{payload}"
    sig = _hmac.new(SECRET_KEY.encode(), sig_input.encode(), hashlib.sha256)
    signature = base64.urlsafe_b64encode(sig.digest()).rstrip(b'=').decode()
    expired_token = f"{header}.{payload}.{signature}"
    r = await client.get("/api/v1/auth/me",
                         headers={"Authorization": f"Bearer {expired_token}"})
    assert r.status_code in (401, 403)
