"""
Super Admin API — يُستخدم فقط من لوحة إدارة النظام
"""
import os, secrets, hmac, time
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_db
from app.models.models import Tenant, User

router = APIRouter(prefix="/super-admin", tags=["super_admin"])

_DEFAULT_INSECURE_PASSWORD = "changeme123"
SUPER_ADMIN_USERNAME = os.getenv("SUPER_ADMIN_USERNAME", "superadmin")
SUPER_ADMIN_PASSWORD = os.getenv("SUPER_ADMIN_PASSWORD", _DEFAULT_INSECURE_PASSWORD)

# CRITICAL (fixed): كان النظام بيقبل تسجيل دخول Super Admin — اللي بيقدر يشوف/يعدّل/يحذف
# *كل* الورش (tenants) — بكلمة مرور افتراضية معروفة "changeme123" لو حد نسي يظبط
# SUPER_ADMIN_PASSWORD. ده باب خلفي حرج لأي تثبيت production ماحدش راجع الـ env vars بتاعته.
# دلوقتي: لو الباسورد لسه على القيمة الافتراضية وإحنا مش في ENVIRONMENT=development،
# اللوجن بيترفض تماماً (503) بدل ما يقبل باسورد معروف للجميع.
def _super_admin_password_is_safe() -> bool:
    if SUPER_ADMIN_PASSWORD != _DEFAULT_INSECURE_PASSWORD:
        return True
    try:
        from app.core.config import settings
        return settings.ENVIRONMENT == "development"
    except Exception:
        # لو معرفناش الـ environment، الأمان الأولى إننا نرفض
        return False


# CRITICAL (fixed): كانت الجلسات (_active_tokens) عبارة عن set بسيط من غير أي
# expiry — يعني توكن Super Admin بيفضل صالح للأبد (لحد ما السيرفر يعيد التشغيل)
# ومفيش endpoint لتسجيل الخروج/إبطال التوكن. دلوقتي كل توكن بيتخزن مع وقت
# انتهاء (نفس مدة صلاحية توكن المستخدم العادي)، وفيه endpoint /logout صريح.
SUPER_TOKEN_TTL_SECONDS = 12 * 3600  # 12 ساعة — نفس TOKEN_EXPIRE_HOURS في auth.py
_active_tokens: dict = {}  # token -> expires_at (epoch seconds)


def _prune_expired_tokens() -> None:
    now = time.time()
    expired = [t for t, exp in _active_tokens.items() if exp < now]
    for t in expired:
        _active_tokens.pop(t, None)


def _verify_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "غير مصرح")
    token = authorization.split(" ", 1)[1]
    _prune_expired_tokens()
    exp = _active_tokens.get(token)
    if exp is None or exp < time.time():
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
    if not _super_admin_password_is_safe():
        raise HTTPException(
            503,
            "Super Admin معطّل: يجب ضبط SUPER_ADMIN_PASSWORD (قيمة غير افتراضية) "
            "قبل تشغيل النظام خارج بيئة development.",
        )
    # مقارنة بزمن ثابت (constant-time) لتجنّب هجمات قياس التوقيت على كلمة المرور
    user_ok = hmac.compare_digest(data.username, SUPER_ADMIN_USERNAME)
    pass_ok = hmac.compare_digest(data.password, SUPER_ADMIN_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(401, "بيانات الدخول غير صحيحة")
    _prune_expired_tokens()
    token = secrets.token_hex(32)
    _active_tokens[token] = time.time() + SUPER_TOKEN_TTL_SECONDS
    return {"token": token, "message": "تم تسجيل دخول Super Admin", "expires_in_seconds": SUPER_TOKEN_TTL_SECONDS}


@router.post("/logout")
async def super_logout(_token: str = Depends(_verify_token)):
    _active_tokens.pop(_token, None)
    return {"message": "تم تسجيل الخروج"}


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


@router.post("/run-installment-scheduler")
async def run_installment_scheduler_now(
    _token: str = Depends(_verify_token),
):
    """
    تشغيل فوري لـ scheduler الأقساط — لـ admin أو testing.
    يحدث حالة كل الأقساط المتأخرة في كل الـ tenants فوراً.
    """
    from app.services.installment_scheduler import run_installment_status_update
    result = await run_installment_status_update()
    return {"message": "تم تحديث حالة الأقساط", **result}
