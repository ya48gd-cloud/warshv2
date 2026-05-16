"""
Tests for user management API — CRUD, RBAC enforcement,
last-admin protection, and password reset.
"""
import pytest
from test_helpers import auth


# ── GET /users/ ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_users_as_admin(client, admin_user, admin_token):
    r = await client.get("/api/v1/users/", headers=auth(admin_token))
    assert r.status_code == 200
    usernames = [u["username"] for u in r.json()]
    assert "admin" in usernames

@pytest.mark.asyncio
async def test_list_users_as_viewer_forbidden(client, viewer_user, viewer_token):
    r = await client.get("/api/v1/users/", headers=auth(viewer_token))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_list_users_unauthenticated(client):
    r = await client.get("/api/v1/users/")
    assert r.status_code in (401, 403)  # HTTPBearer returns 401 for missing token


# ── POST /users/ ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_user_success(client, admin_user, admin_token):
    r = await client.post("/api/v1/users/",
        headers=auth(admin_token),
        json={
            "username": "new_accountant",
            "password": "pass123",
            "full_name": "محاسب جديد",
            "role": "accountant",
        }
    )
    assert r.status_code == 201
    data = r.json()
    assert data["username"] == "new_accountant"
    assert data["role"] == "accountant"
    assert data["is_active"] is True

@pytest.mark.asyncio
async def test_create_user_duplicate_username(client, admin_user, admin_token):
    await client.post("/api/v1/users/",
        headers=auth(admin_token),
        json={"username": "dupuser", "password": "pass123", "role": "viewer"}
    )
    r = await client.post("/api/v1/users/",
        headers=auth(admin_token),
        json={"username": "dupuser", "password": "pass456", "role": "viewer"}
    )
    assert r.status_code == 409

@pytest.mark.asyncio
async def test_create_user_short_password(client, admin_user, admin_token):
    r = await client.post("/api/v1/users/",
        headers=auth(admin_token),
        json={"username": "shortpw", "password": "abc", "role": "viewer"}
    )
    assert r.status_code == 400

@pytest.mark.asyncio
async def test_create_user_invalid_role(client, admin_user, admin_token):
    r = await client.post("/api/v1/users/",
        headers=auth(admin_token),
        json={"username": "badrole", "password": "pass123", "role": "superuser"}
    )
    assert r.status_code == 400

@pytest.mark.asyncio
async def test_create_user_as_viewer_forbidden(client, viewer_user, viewer_token):
    r = await client.post("/api/v1/users/",
        headers=auth(viewer_token),
        json={"username": "blocked", "password": "pass123", "role": "viewer"}
    )
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_create_user_all_roles(client, admin_user, admin_token):
    """All four valid roles must be accepted."""
    for role in ["admin", "accountant", "production", "viewer"]:
        r = await client.post("/api/v1/users/",
            headers=auth(admin_token),
            json={
                "username": f"testuser_{role}",
                "password": "pass1234",
                "role": role,
            }
        )
        assert r.status_code == 201, f"Failed for role: {role}"


# ── PUT /users/{id} ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_user_role(client, admin_user, admin_token, db):
    from app.models.models import User
    from app.api.auth import hash_password
    user = User(username="toupdate", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    r = await client.put(f"/api/v1/users/{user.id}",
        headers=auth(admin_token),
        json={"role": "production"}
    )
    assert r.status_code == 200
    assert r.json()["role"] == "production"

@pytest.mark.asyncio
async def test_update_user_full_name(client, admin_user, admin_token, db):
    from app.models.models import User
    from app.api.auth import hash_password
    user = User(username="nameuser", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    r = await client.put(f"/api/v1/users/{user.id}",
        headers=auth(admin_token),
        json={"full_name": "الاسم الجديد"}
    )
    assert r.status_code == 200
    assert r.json()["full_name"] == "الاسم الجديد"

@pytest.mark.asyncio
async def test_update_user_deactivate(client, admin_user, admin_token, db):
    from app.models.models import User
    from app.api.auth import hash_password
    user = User(username="deactivate_me", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    r = await client.put(f"/api/v1/users/{user.id}",
        headers=auth(admin_token),
        json={"is_active": False}
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False

@pytest.mark.asyncio
async def test_cannot_deactivate_self(client, admin_user, admin_token):
    r = await client.put(f"/api/v1/users/{admin_user.id}",
        headers=auth(admin_token),
        json={"is_active": False}
    )
    assert r.status_code == 400

@pytest.mark.asyncio
async def test_cannot_demote_last_admin(client, admin_user, admin_token):
    """If admin is the only admin, cannot change their role."""
    r = await client.put(f"/api/v1/users/{admin_user.id}",
        headers=auth(admin_token),
        json={"role": "viewer"}
    )
    assert r.status_code == 400

@pytest.mark.asyncio
async def test_update_user_not_found(client, admin_user, admin_token):
    r = await client.put("/api/v1/users/99999",
        headers=auth(admin_token),
        json={"role": "viewer"}
    )
    assert r.status_code == 404

@pytest.mark.asyncio
async def test_update_user_as_viewer_forbidden(client, viewer_user, viewer_token, admin_user):
    r = await client.put(f"/api/v1/users/{admin_user.id}",
        headers=auth(viewer_token),
        json={"full_name": "hacked"}
    )
    assert r.status_code == 403


# ── POST /users/{id}/reset-password ───────────────────────────

@pytest.mark.asyncio
async def test_reset_password_success(client, admin_user, admin_token, db):
    from app.models.models import User
    from app.api.auth import hash_password
    user = User(username="resetme", password_hash=hash_password("old"), role="viewer", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    r = await client.post(f"/api/v1/users/{user.id}/reset-password",
        headers=auth(admin_token),
        json={"new_password": "newpass123"}
    )
    assert r.status_code == 200
    # verify new password works for login
    r2 = await client.post("/api/v1/auth/login", json={"username": "resetme", "password": "newpass123"})
    assert r2.status_code == 200

@pytest.mark.asyncio
async def test_reset_password_too_short(client, admin_user, admin_token, viewer_user):
    r = await client.post(f"/api/v1/users/{viewer_user.id}/reset-password",
        headers=auth(admin_token),
        json={"new_password": "ab"}
    )
    assert r.status_code == 400

@pytest.mark.asyncio
async def test_reset_password_viewer_forbidden(client, viewer_user, viewer_token, admin_user):
    r = await client.post(f"/api/v1/users/{admin_user.id}/reset-password",
        headers=auth(viewer_token),
        json={"new_password": "newpass123"}
    )
    assert r.status_code == 403


# ── DELETE /users/{id} ────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_user_success(client, admin_user, admin_token, db):
    from app.models.models import User
    from app.api.auth import hash_password
    user = User(username="deleteme", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    r = await client.delete(f"/api/v1/users/{user.id}", headers=auth(admin_token))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_cannot_delete_self(client, admin_user, admin_token):
    r = await client.delete(f"/api/v1/users/{admin_user.id}", headers=auth(admin_token))
    assert r.status_code == 400

@pytest.mark.asyncio
async def test_cannot_delete_last_admin(client, admin_user, admin_token):
    r = await client.delete(f"/api/v1/users/{admin_user.id}", headers=auth(admin_token))
    assert r.status_code == 400

@pytest.mark.asyncio
async def test_delete_user_not_found(client, admin_user, admin_token):
    r = await client.delete("/api/v1/users/99999", headers=auth(admin_token))
    assert r.status_code == 404

@pytest.mark.asyncio
async def test_delete_user_as_viewer_forbidden(client, viewer_user, viewer_token, admin_user):
    r = await client.delete(f"/api/v1/users/{admin_user.id}", headers=auth(viewer_token))
    assert r.status_code == 403
