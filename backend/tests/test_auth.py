"""
Tests for authentication: login, JWT creation/verify, RBAC enforcement.
"""
import pytest
from app.api.auth import (
    hash_password, create_token, verify_token,
    _b64encode, _b64decode
)
from test_helpers import auth


# ── Unit: password hashing ─────────────────────────────────────

def test_hash_password_deterministic():
    assert hash_password("secret") == hash_password("secret")

def test_hash_password_different_inputs():
    assert hash_password("abc") != hash_password("xyz")

def test_hash_password_length():
    h = hash_password("any")
    assert len(h) == 64  # SHA-256 hex


# ── Unit: JWT encode/decode ────────────────────────────────────

def test_b64_roundtrip():
    data = {"sub": "1", "role": "admin"}
    assert _b64decode(_b64encode(data)) == data

def test_create_token_returns_three_parts():
    token = create_token(1, "admin", "admin")
    assert token.count(".") == 2

def test_verify_token_valid():
    token = create_token(1, "admin", "admin")
    payload = verify_token(token)
    assert payload is not None
    assert payload["sub"] == "1"
    assert payload["username"] == "admin"
    assert payload["role"] == "admin"

def test_verify_token_wrong_secret(monkeypatch):
    token = create_token(1, "admin", "admin")
    monkeypatch.setattr("app.api.auth.SECRET_KEY", "wrong-key")
    assert verify_token(token) is None

def test_verify_token_tampered():
    token = create_token(1, "admin", "admin")
    parts = token.split(".")
    # tamper payload
    parts[1] = parts[1][:-2] + "ZZ"
    assert verify_token(".".join(parts)) is None

def test_verify_token_expired(monkeypatch):
    import app.api.auth as auth_mod
    monkeypatch.setattr(auth_mod, "TOKEN_EXPIRE_HOURS", -1)
    token = create_token(1, "admin", "admin")
    assert verify_token(token) is None


# ── Integration: POST /api/v1/auth/login ──────────────────────

@pytest.mark.asyncio
async def test_login_success(client, admin_user):
    r = await client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "admin123"
    })
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert data["user"]["role"] == "admin"
    assert data["user"]["username"] == "admin"

@pytest.mark.asyncio
async def test_login_wrong_password(client, admin_user):
    r = await client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "wrong"
    })
    assert r.status_code == 401

@pytest.mark.asyncio
async def test_login_nonexistent_user(client):
    r = await client.post("/api/v1/auth/login", json={
        "username": "nobody", "password": "x"
    })
    assert r.status_code == 401

@pytest.mark.asyncio
async def test_login_inactive_user(client, db):
    from app.models.models import User
    user = User(
        username="inactive",
        password_hash=hash_password("pw123"),
        role="viewer",
        is_active=False,
    )
    db.add(user)
    await db.commit()
    r = await client.post("/api/v1/auth/login", json={
        "username": "inactive", "password": "pw123"
    })
    assert r.status_code == 403


# ── Integration: GET /api/v1/auth/me ──────────────────────────

@pytest.mark.asyncio
async def test_me_authenticated(client, admin_user, admin_token):
    r = await client.get("/api/v1/auth/me", headers=auth(admin_token))
    assert r.status_code == 200
    assert r.json()["username"] == "admin"
    assert r.json()["role"] == "admin"

@pytest.mark.asyncio
async def test_me_no_token(client):
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401  # no token → 401

@pytest.mark.asyncio
async def test_me_bad_token(client):
    r = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage.token.here"})
    assert r.status_code == 401


# ── Integration: POST /api/v1/auth/change-password ────────────

@pytest.mark.asyncio
async def test_change_password_success(client, admin_user, admin_token):
    r = await client.post("/api/v1/auth/change-password",
        headers=auth(admin_token),
        json={"old_password": "admin123", "new_password": "newpass456"}
    )
    assert r.status_code == 200
    # verify new password works
    r2 = await client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "newpass456"
    })
    assert r2.status_code == 200

@pytest.mark.asyncio
async def test_change_password_wrong_old(client, admin_user, admin_token):
    r = await client.post("/api/v1/auth/change-password",
        headers=auth(admin_token),
        json={"old_password": "wrong", "new_password": "newpass456"}
    )
    assert r.status_code == 400

@pytest.mark.asyncio
async def test_change_password_too_short(client, admin_user, admin_token):
    r = await client.post("/api/v1/auth/change-password",
        headers=auth(admin_token),
        json={"old_password": "admin123", "new_password": "ab"}
    )
    assert r.status_code == 400


# ── RBAC role enforcement ─────────────────────────────────────

@pytest.mark.asyncio
async def test_rbac_admin_only_endpoint_as_viewer(client, viewer_user, viewer_token):
    """Viewer cannot access admin-only user management."""
    r = await client.get("/api/v1/users/", headers=auth(viewer_token))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_rbac_admin_only_endpoint_as_admin(client, admin_user, admin_token):
    """Admin can access user management."""
    r = await client.get("/api/v1/users/", headers=auth(admin_token))
    assert r.status_code == 200
