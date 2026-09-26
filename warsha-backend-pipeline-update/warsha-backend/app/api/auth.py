"""
Authentication API
POST /auth/login          → returns JWT token
GET  /auth/me             → returns current user info
POST /auth/change-password
"""
import os
import hashlib
import json
import base64
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from app.services.analytics import log_event, Events

from app.core.database import get_db, current_tenant_id, _is_sqlite as _DB_IS_SQLITE
from app.models.models import User, Tenant

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)

_DEFAULT_INSECURE = "heavy-erp-secret-key-change-in-production"


def _load_or_create_secret() -> str:
    """
    أولوية المفتاح:
      1) متغيّر البيئة JWT_SECRET إن كان مضبوطاً وغير القيمة الافتراضية.
      2) مفتاح عشوائي فريد لكل تثبيت يُحفَظ بجوار قاعدة البيانات (jwt_secret.key)
         ويبقى ثابتاً بين عمليات إعادة التشغيل.
    بهذا لا يعمل النظام أبداً بمفتاح افتراضي معروف.
    """
    env = os.getenv("JWT_SECRET")
    if env and env != _DEFAULT_INSECURE:
        return env

    base = os.getenv("ERP_DATA_DIR")
    if not base:
        try:
            from app.core.config import settings
            url = settings.DATABASE_URL
            if url.startswith("sqlite") and ":///" in url:
                base = os.path.dirname(url.split(":///", 1)[1])
        except Exception:
            base = None
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".warsherp")

    try:
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "jwt_secret.key")
        if os.path.exists(path):
            existing = open(path, encoding="utf-8").read().strip()
            if existing:
                return existing
        new_secret = secrets.token_hex(32)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_secret)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return new_secret
    except Exception:
        # ملاذ أخير: مفتاح عشوائي لكل جلسة تشغيل (يبطل الرموز عند إعادة التشغيل لكنه آمن)
        return secrets.token_hex(32)


SECRET_KEY = _load_or_create_secret()
TOKEN_EXPIRE_HOURS = 12

VALID_ROLES = {"admin", "accountant", "production", "viewer"}


# ── Simple JWT (no external library needed) ───────────────────

def _b64encode(data: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(data, separators=(',', ':')).encode()
    ).rstrip(b'=').decode()


def _b64decode(s: str) -> dict:
    padding = 4 - len(s) % 4
    s += '=' * (padding % 4)
    return json.loads(base64.urlsafe_b64decode(s))


def create_token(user_id: int, username: str, role: str, tenant_id: int) -> str:
    header  = _b64encode({"alg": "HS256", "typ": "JWT"})
    payload = _b64encode({
        "sub":       str(user_id),
        "username":  username,
        "role":      role,
        "tenant_id": tenant_id,
        "exp":       int((datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)).timestamp()),
        "iat":       int(datetime.now(timezone.utc).timestamp()),
    })
    sig_input = f"{header}.{payload}"
    sig = hmac.new(SECRET_KEY.encode(), sig_input.encode(), hashlib.sha256)
    signature = base64.urlsafe_b64encode(sig.digest()).rstrip(b'=').decode()
    return f"{header}.{payload}.{signature}"


def verify_token(token: str) -> Optional[dict]:
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        header, payload, signature = parts
        sig_input = f"{header}.{payload}"
        expected_sig = hmac.new(SECRET_KEY.encode(), sig_input.encode(), hashlib.sha256)
        expected = base64.urlsafe_b64encode(expected_sig.digest()).rstrip(b'=').decode()
        if not hmac.compare_digest(signature, expected):
            return None
        data = _b64decode(payload)
        if data.get('exp', 0) < datetime.now(timezone.utc).timestamp():
            return None
        return data
    except Exception:
        return None


_PBKDF2_ITERATIONS = 260_000

def hash_password(password: str) -> str:
    """Hash password with PBKDF2-HMAC-SHA256 + random salt (format: pbkdf2$salt$hash)."""
    salt = os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS).hex()
    return f"pbkdf2${salt}${h}"


def verify_password(plain: str, stored: str) -> bool:
    """Verify against pbkdf2 hashes and legacy plain-sha256 hashes."""
    if stored.startswith("pbkdf2$"):
        try:
            _, salt, expected = stored.split("$", 2)
            h = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), _PBKDF2_ITERATIONS).hex()
            return hmac.compare_digest(h, expected)
        except Exception:
            return False
    # Legacy: plain SHA-256 (will be re-hashed on next successful login)
    return hmac.compare_digest(hashlib.sha256(plain.encode()).hexdigest(), stored)


# ── Dependency: get current user (token → dict) ───────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="غير مسموح — يرجى تسجيل الدخول")
    token_data = verify_token(credentials.credentials)
    if not token_data:
        raise HTTPException(status_code=401, detail="الجلسة منتهية — يرجى إعادة تسجيل الدخول")
    return token_data


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Optional[dict]:
    if not credentials:
        return None
    return verify_token(credentials.credentials)


# ── RBAC: role-enforcement dependency factory ─────────────────

def require_role(*roles: str):
    """
    Usage:
        @router.post("/")
        async def create(db=Depends(get_db), u=Depends(require_role("admin","accountant"))):
            ...
    Raises 403 if authenticated user's role is not in *roles.
    """
    async def _check(
        credentials: HTTPAuthorizationCredentials = Depends(security),
        db: AsyncSession = Depends(get_db),
    ) -> dict:
        if not credentials:
            raise HTTPException(status_code=401, detail="غير مسموح — يرجى تسجيل الدخول")
        token_data = verify_token(credentials.credentials)
        if not token_data:
            raise HTTPException(status_code=401, detail="الجلسة منتهية — يرجى إعادة تسجيل الدخول")
        if token_data.get("role") not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"صلاحية '{token_data.get('role')}' غير كافية. مطلوب: {list(roles)}",
            )
        return token_data
    return _check


# Convenience shortcuts used across all API files
require_admin             = require_role("admin")
require_admin_accountant  = require_role("admin", "accountant")
require_admin_production  = require_role("admin", "production")
require_any               = require_role("admin", "accountant", "production", "viewer")


# ── Schemas ───────────────────────────────────────────────────

class LoginIn(BaseModel):
    username: str
    password: str
    tenant_slug: Optional[str] = None  # اختياري لو فيه ورشة واحدة فقط


class SignupIn(BaseModel):
    workshop_name: str        # اسم الورشة المعروض
    workshop_slug: str        # مُعرّف فريد (للـ subdomain لاحقاً)
    admin_username: str
    admin_password: str
    admin_full_name: Optional[str] = None


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


# ── Endpoints ─────────────────────────────────────────────────

@router.post("/signup", status_code=201)
async def signup_blocked():
    raise HTTPException(403, "التسجيل متاح فقط عبر Super Admin")

@router.post("/signup-internal", status_code=201)
async def signup(data: SignupIn, db: AsyncSession = Depends(get_db)):
    """
    إنشاء ورشة جديدة (tenant) + أول حساب admin لها.
    عملية public — لا تحتاج توثيق. صالحة فقط لتسجيل ورشة جديدة.
    """
    slug = data.workshop_slug.strip().lower()
    if not slug or not slug.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "معرّف الورشة (slug) يحتوي على أحرف غير مسموحة")
    if len(data.admin_password) < 6:
        raise HTTPException(400, "كلمة المرور قصيرة جداً (6 أحرف على الأقل)")

    # تحقّق من تفرّد الـ slug
    existing = await db.execute(select(Tenant).where(Tenant.slug == slug))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "هذا المُعرّف مستخدم بالفعل — اختر معرّفاً آخر")

    # ننشئ الـ tenant
    tenant = Tenant(name=data.workshop_name.strip(), slug=slug, plan="free")
    db.add(tenant)
    await db.flush()  # عشان نحصل على tenant.id

    # نضع متغير الجلسة عشان RLS يسمح بإدخال user جديد لـ tenant الجديد
    # (signup هو الحالة الوحيدة اللي نضع فيها session var بشكل صريح من داخل endpoint)
    if not _DB_IS_SQLITE:
        from sqlalchemy import text as _text
        await db.execute(_text("SELECT set_config('app.tenant_id', :t, false)").bindparams(t=str(tenant.id)))

    # ننشئ الـ admin user — نمرّر tenant_id صراحة لأن signup خارج سياق request authenticated
    user = User(
        tenant_id=tenant.id,
        username=data.admin_username.strip(),
        password_hash=hash_password(data.admin_password),
        full_name=data.admin_full_name or data.admin_username,
        role="admin",
        is_active=True,
    )
    db.add(user)
    await db.flush()  # نحتاج user.id قبل الـ commit، ونزرع دليل الحسابات في نفس الـ transaction

    # دليل الحسابات شرط أساسي لأي قيد محاسبي تلقائي (مبيعات/مشتريات/رواتب/صرف مواد) — بدونه auto_journal يتخطّى بصمت
    from app.api.chart_of_accounts import seed_default_accounts
    await seed_default_accounts(db, tenant.id)

    # محطة 10: المخازن الافتراضية الـ8 لكل ورشة جديدة
    from app.api.warehouses import seed_default_warehouses
    await seed_default_warehouses(db, tenant.id)

    await db.commit()
    await db.refresh(user)

    token = create_token(user.id, user.username, user.role, user.tenant_id)
    return {
        "token": token,
        "user": {
            "id":        user.id,
            "username":  user.username,
            "full_name": user.full_name,
            "role":      user.role,
            "tenant_id": user.tenant_id,
        },
        "tenant": {
            "id":   tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "plan": tenant.plan,
        },
        "expires_in_hours": TOKEN_EXPIRE_HOURS,
    }


@router.post("/login")
async def login(data: LoginIn, db: AsyncSession = Depends(get_db)):
    # signup مفتوح، فاللوجن لازم يكون بدون قيد RLS — نستخدم استعلام صريح بـ tenant
    # لو slug موجود، نقيّد البحث به؛ لو لا، نسمح بـ login لأول ورشة فيها هذا الاسم (للأنظمة أحادية الورشة)
    if data.tenant_slug:
        slug = data.tenant_slug.strip().lower()
        tenant_q = await db.execute(select(Tenant).where(Tenant.slug == slug))
        tenant = tenant_q.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=401, detail="ورشة غير معروفة")
        if not tenant.is_active:
            raise HTTPException(status_code=403, detail="هذه الورشة معطّلة")
        result = await db.execute(
            select(User).where(
                User.username == data.username.strip(),
                User.tenant_id == tenant.id,
            )
        )
        user = result.scalar_one_or_none()
    else:
        # بدون tenant_slug: ممكن يكون فيه أكتر من ورشة بنفس اسم المستخدم
        # (مثلاً "admin" في كل ورشة) — ناخد أول واحد بكلمة مرور صحيحة
        result = await db.execute(
            select(User).where(User.username == data.username.strip())
        )
        candidates = result.scalars().all()
        user = next(
            (u for u in candidates if verify_password(data.password, u.password_hash)),
            None,
        )
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور غير صحيحة")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="الحساب معطل")
    # ترقية تلقائية للهاش القديم (SHA-256 بدون salt) إلى PBKDF2
    if not user.password_hash.startswith("pbkdf2$"):
        user.password_hash = hash_password(data.password)
        await db.commit()
    token = create_token(user.id, user.username, user.role, user.tenant_id)
    await log_event(Events.USER_LOGIN, tenant_id=user.tenant_id, user_id=user.id,
                     entity_type="user", entity_id=user.id, source_path="/api/v1/auth/login")
    return {
        "token": token,
        "user": {
            "id":        user.id,
            "username":  user.username,
            "full_name": user.full_name,
            "role":      user.role,
            "tenant_id": user.tenant_id,
        },
        "expires_in_hours": TOKEN_EXPIRE_HOURS,
    }


@router.get("/me")
async def get_me(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(
            User.id == int(current_user['sub']),
            User.tenant_id == int(current_user.get('tenant_id', 0)),
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "المستخدم غير موجود")
    return {
        "id":        user.id,
        "username":  user.username,
        "full_name": user.full_name,
        "role":      user.role,
        "is_active": user.is_active,
        "tenant_id": user.tenant_id,
    }


@router.post("/change-password")
async def change_password(
    data: ChangePasswordIn,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(
            User.id == int(current_user['sub']),
            User.tenant_id == int(current_user.get('tenant_id', 0)),
        )
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.old_password, user.password_hash):
        raise HTTPException(400, "كلمة المرور القديمة غير صحيحة")
    if len(data.new_password) < 6:
        raise HTTPException(400, "كلمة المرور الجديدة قصيرة جداً (6 أحرف على الأقل)")
    user.password_hash = hash_password(data.new_password)
    await db.commit()
    return {"message": "تم تغيير كلمة المرور بنجاح"}
