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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from app.services.analytics import log_event, Events
from app.services.audit import record_event as _audit_record_event
import logging as _logging

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


# ── Live account checks ───────────────────────────────────────
# SECURITY (fixed): الـ JWT كان بيفضل صالح 12 ساعة حتى لو:
#   - الأدمن عطّل المستخدم (is_active=False)
#   - Super Admin عطّل الورشة كلها (toggle)
#   - الأدمن نزّل دور المستخدم (مثلاً admin → viewer) — التوكن القديم كان
#     بيحتفظ بصلاحيات admin ويقدر يرجّع نفسه admin تاني.
# دلوقتي كل طلب موثّق بيتأكد من حالة الورشة والمستخدم الفعلية في الداتابيز،
# وبيعتمد الدور الحالي المخزّن بدل الدور المختوم في التوكن.
async def _enforce_live_account(db: AsyncSession, token_data: dict) -> dict:
    try:
        tid = int(token_data.get("tenant_id"))
        uid = int(token_data.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="الجلسة غير صالحة — يرجى إعادة تسجيل الدخول")

    tenant = await db.get(Tenant, tid)
    if tenant is not None and tenant.is_active is False:
        raise HTTPException(status_code=403, detail="هذه الورشة معطّلة")

    res = await db.execute(
        select(User).where(User.id == uid, User.tenant_id == tid)
    )
    user = res.scalar_one_or_none()
    # نطابق اسم المستخدم كمان: الـ id لوحده ممكن يتعاد استخدامه بعد الحذف
    # (SQLite بدون AUTOINCREMENT) فيتطبّق على صف مش بتاع صاحب التوكن.
    if user is not None and user.username == token_data.get("username"):
        if not user.is_active:
            raise HTTPException(status_code=401, detail="الحساب معطل — تواصل مع مدير النظام")
        if user.role and user.role != token_data.get("role"):
            token_data = {**token_data, "role": user.role}
    return token_data


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
    return await _enforce_live_account(db, token_data)


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
        token_data = await _enforce_live_account(db, token_data)
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

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def block_viewer_writes(request: Request, user: dict = Depends(require_any)) -> dict:
    """Dependency مشتركة على مستوى الراوتر: "مشاهد" (viewer) قراءة فقط.
    ROLE_WRITE['viewer'] = [] في الواجهة، لكن راوترات كتير كانت require_any بس
    فالمشاهد يقدر ينشئ/يعدّل/يحذف بطلب API مباشر. (الدور هنا هو الدور الحي من الداتابيز)."""
    if request.method.upper() not in _SAFE_METHODS and user.get("role") == "viewer":
        raise HTTPException(403, "صلاحية 'viewer' (مشاهد) لا تسمح بالتعديل")
    return user


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


def validate_new_workshop(workshop_name: str, slug: str, admin_username: str,
                          admin_password: str, admin_full_name: Optional[str] = None) -> None:
    """تحقّق مشترك لإنشاء ورشة جديدة (signup-internal + Super Admin).
    الحدود مطابقة لأطوال الأعمدة (Tenant.name=200, Tenant.slug=50,
    User.username=50, User.full_name=200) — بدونها Postgres بيرمي 500."""
    if not (workshop_name or "").strip():
        raise HTTPException(400, "اسم الورشة مطلوب")
    if len(workshop_name.strip()) > 200:
        raise HTTPException(400, "اسم الورشة طويل جداً (200 حرف كحد أقصى)")
    if not slug or not slug.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "معرّف الورشة (slug) يحتوي على أحرف غير مسموحة")
    if not slug.isascii():
        raise HTTPException(400, "معرّف الورشة (slug) يجب أن يكون بأحرف إنجليزية وأرقام فقط")
    if len(slug) > 50:
        raise HTTPException(400, "معرّف الورشة طويل جداً (50 حرف كحد أقصى)")
    uname = (admin_username or "").strip()
    if not uname:
        raise HTTPException(400, "اسم المستخدم مطلوب")
    if len(uname) > 50:
        raise HTTPException(400, "اسم المستخدم طويل جداً (50 حرف كحد أقصى)")
    if len(admin_password or "") < 6:
        raise HTTPException(400, "كلمة المرور قصيرة جداً (6 أحرف على الأقل)")
    if len(admin_password) > 128:
        raise HTTPException(400, "كلمة المرور طويلة جداً (128 حرف كحد أقصى)")
    if admin_full_name and len(admin_full_name) > 200:
        raise HTTPException(400, "الاسم الكامل طويل جداً (200 حرف كحد أقصى)")


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
    validate_new_workshop(data.workshop_name, slug, data.admin_username,
                          data.admin_password, data.admin_full_name)

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
        full_name=(data.admin_full_name or "").strip() or data.admin_username.strip(),
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

    try:
        await db.commit()
    except IntegrityError:
        # سباق: طلبين بنفس الـ slug في نفس اللحظة → القيد الفريد يرفض الثاني
        await db.rollback()
        raise HTTPException(409, "هذا المُعرّف مستخدم بالفعل — اختر معرّفاً آخر")
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


# SECURITY (fixed): مفيش أي حد لمحاولات الدخول الفاشلة → brute-force مفتوح على
# كلمات المرور. دلوقتي: 10 محاولات فاشلة خلال 15 دقيقة لنفس (IP + ورشة + مستخدم)
# بتقفل المحاولات مؤقتاً (429). الذاكرة محلية للعملية (كافية للنسخة الـ desktop/الفردية).
_LOGIN_MAX_FAILS = 10
_LOGIN_WINDOW_SECONDS = 15 * 60
_login_failures: dict = {}  # key -> [timestamps]


def _login_key(request: Optional[Request], username: str, slug: Optional[str]) -> str:
    ip = ""
    try:
        ip = request.client.host if request is not None and request.client else ""
    except Exception:
        ip = ""
    return f"{ip}|{(slug or '').strip().lower()}|{(username or '').strip().lower()}"


def _login_recent_failures(key: str) -> list:
    import time as _time
    now = _time.time()
    fails = [t for t in _login_failures.get(key, []) if now - t < _LOGIN_WINDOW_SECONDS]
    if fails:
        _login_failures[key] = fails
    else:
        _login_failures.pop(key, None)
    return fails


def _login_register_failure(key: str) -> None:
    import time as _time
    _login_failures.setdefault(key, []).append(_time.time())
    # حماية الذاكرة من التضخم
    if len(_login_failures) > 10000:
        for k in list(_login_failures.keys())[:5000]:
            _login_failures.pop(k, None)


_audit_logger = _logging.getLogger("warsha.audit")


async def _audit_auth_event(db: AsyncSession, action: str, tenant_id: int, user_id, username,
                            reason: Optional[str] = None) -> None:
    """يسجّل حدث دخول/خروج في سجل التدقيق داخل جلسة الطلب ثم commit.
    لا يكسر الطلب أبداً (أي فشل يُسجَّل كتحذير فقط). لا يُخزَّن أي كلمة مرور."""
    try:
        uname = (username or "").strip()[:100] or "?"
        extra = {"reason": reason, "username_attempted": uname} if reason else None
        _audit_record_event(
            db, action, int(tenant_id), user_id=user_id, user_name=uname,
            entity_type="users", entity_id=user_id, entity_ref=uname, extra=extra)
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        _audit_logger.warning("audit auth event '%s' failed", action, exc_info=True)


@router.post("/login")
async def login(data: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    _key = _login_key(request, data.username, data.tenant_slug)
    if len(_login_recent_failures(_key)) >= _LOGIN_MAX_FAILS:
        raise HTTPException(status_code=429, detail="محاولات دخول فاشلة كثيرة — حاول مرة أخرى بعد 15 دقيقة")
    # signup مفتوح، فاللوجن لازم يكون بدون قيد RLS — نستخدم استعلام صريح بـ tenant
    # لو slug موجود، نقيّد البحث به؛ لو لا، نسمح بـ login لأول ورشة فيها هذا الاسم (للأنظمة أحادية الورشة)
    if data.tenant_slug:
        slug = data.tenant_slug.strip().lower()
        tenant_q = await db.execute(select(Tenant).where(Tenant.slug == slug))
        tenant = tenant_q.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=401, detail="ورشة غير معروفة")
        if not tenant.is_active:
            await _audit_auth_event(db, "login_failed", tenant.id, None, data.username, "tenant_disabled")
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
        _login_register_failure(_key)
        # تدقيق: نسجّل فقط لو الورشة معروفة بلا لبس (slug صريح، أو اسم مستخدم موجود في ورشة واحدة فقط)
        _t_id, _u_id = None, None
        if data.tenant_slug:
            _t_id = tenant.id
            _u_id = user.id if user else None
        elif len(candidates) == 1:
            _t_id, _u_id = candidates[0].tenant_id, candidates[0].id
        if _t_id is not None:
            await _audit_auth_event(db, "login_failed", _t_id, _u_id, data.username, "bad_password")
        raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور غير صحيحة")
    if not user.is_active:
        await _audit_auth_event(db, "login_failed", user.tenant_id, user.id, data.username, "inactive")
        raise HTTPException(status_code=403, detail="الحساب معطل")
    # SECURITY (fixed): الدخول بدون tenant_slug كان بيتخطى فحص "الورشة معطّلة"
    _user_tenant = await db.get(Tenant, user.tenant_id)
    if _user_tenant is not None and _user_tenant.is_active is False:
        await _audit_auth_event(db, "login_failed", user.tenant_id, user.id, data.username, "tenant_disabled")
        raise HTTPException(status_code=403, detail="هذه الورشة معطّلة")
    _login_failures.pop(_key, None)
    user.last_login = datetime.now(timezone.utc).replace(tzinfo=None)
    # ترقية تلقائية للهاش القديم (SHA-256 بدون salt) إلى PBKDF2
    if not user.password_hash.startswith("pbkdf2$"):
        user.password_hash = hash_password(data.password)
    await db.commit()
    await _audit_auth_event(db, "login", user.tenant_id, user.id, user.username)
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


@router.post("/logout")
async def logout(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """تسجيل خروج في سجل التدقيق فقط. الـ JWT عديم الحالة (stateless) ولا يُلغى هنا —
    الواجهة هي التي تمسح التوكن."""
    uid = int(current_user["sub"])
    await _audit_auth_event(db, "logout", int(current_user["tenant_id"]), uid,
                            current_user.get("username"))
    return {"message": "تم تسجيل الخروج"}


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
    if len(data.new_password) > 128:
        raise HTTPException(400, "كلمة المرور الجديدة طويلة جداً (128 حرف كحد أقصى)")
    user.password_hash = hash_password(data.new_password)
    await db.commit()
    return {"message": "تم تغيير كلمة المرور بنجاح"}
