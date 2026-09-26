"""
Super Admin API — يُستخدم فقط من لوحة إدارة النظام
"""
import os, secrets
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_db
from app.models.models import Tenant, User

router = APIRouter(prefix="/super-admin", tags=["super_admin"])

SUPER_ADMIN_USERNAME = os.getenv("SUPER_ADMIN_USERNAME", "superadmin")
SUPER_ADMIN_PASSWORD = os.getenv("SUPER_ADMIN_PASSWORD", "changeme123")
_active_tokens: set = set()


def _verify_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "غير مصرح")
    token = authorization.split(" ", 1)[1]
    if token not in _active_tokens:
        raise HTTPException(401, "جلسة منتهية أو غير صالحة")
    return token


class SuperLoginIn(BaseModel):
    username: str
    password: str


class CreateWorkshopIn(BaseModel):
    workshop_name: str
    workshop_slug: str
    admin_username: str
    admin_password: str
    admin_full_name: Optional[str] = None
    plan: Optional[str] = "trial"


@router.post("/login")
async def super_login(data: SuperLoginIn):
    if data.username != SUPER_ADMIN_USERNAME or data.password != SUPER_ADMIN_PASSWORD:
        raise HTTPException(401, "بيانات الدخول غير صحيحة")
    token = secrets.token_hex(32)
    _active_tokens.add(token)
    return {"token": token, "message": "تم تسجيل دخول Super Admin"}


@router.get("/workshops")
async def list_workshops(
    db: AsyncSession = Depends(get_db),
    _token: str = Depends(_verify_token),
):
    result = await db.execute(select(Tenant).order_by(Tenant.id))
    tenants = result.scalars().all()
    out = []
    for t in tenants:
        count_q = await db.execute(select(func.count()).where(User.tenant_id == t.id))
        user_count = count_q.scalar() or 0
        out.append({
            "id": t.id,
            "name": t.name,
            "slug": t.slug,
            "plan": getattr(t, "plan", "trial"),
            "is_active": getattr(t, "is_active", True),
            "user_count": user_count,
        })
    return out


@router.post("/workshops", status_code=201)
async def create_workshop(
    data: CreateWorkshopIn,
    db: AsyncSession = Depends(get_db),
    _token: str = Depends(_verify_token),
):
    from app.api.auth import hash_password

    slug = data.workshop_slug.strip().lower()
    if not slug or not slug.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "معرّف الورشة غير صالح")

    existing = await db.execute(select(Tenant).where(Tenant.slug == slug))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "هذا المعرّف مستخدم بالفعل")

    tenant = Tenant(name=data.workshop_name.strip(), slug=slug)
    if hasattr(tenant, "plan"):
        tenant.plan = data.plan or "trial"
    db.add(tenant)
    await db.flush()

    user = User(
        tenant_id=tenant.id,
        username=data.admin_username.strip(),
        password_hash=hash_password(data.admin_password),
        full_name=data.admin_full_name or data.admin_username,
        role="admin",
        is_active=True,
    )
    db.add(user)

    try:
        from app.api.chart_of_accounts import seed_default_accounts
        await seed_default_accounts(db, tenant.id)
    except Exception:
        pass

    await db.commit()
    return {
        "message": "تم إنشاء الورشة بنجاح",
        "tenant": {"id": tenant.id, "name": tenant.name, "slug": slug},
        "admin": {"username": user.username},
    }


@router.put("/workshops/{tenant_id}/toggle")
async def toggle_workshop(
    tenant_id: int,
    db: AsyncSession = Depends(get_db),
    _token: str = Depends(_verify_token),
):
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(404, "الورشة غير موجودة")
    if hasattr(tenant, "is_active"):
        tenant.is_active = not tenant.is_active
    await db.commit()
    return {"message": "تم تحديث حالة الورشة", "is_active": getattr(tenant, "is_active", True)}


@router.delete("/workshops/{tenant_id}")
async def delete_workshop(
    tenant_id: int,
    db: AsyncSession = Depends(get_db),
    _token: str = Depends(_verify_token),
):
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(404, "الورشة غير موجودة")
    await db.delete(tenant)
    await db.commit()
    return {"message": "تم حذف الورشة"}
