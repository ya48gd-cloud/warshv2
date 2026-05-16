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
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.models.models import User

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)

SECRET_KEY = os.getenv("JWT_SECRET", "heavy-erp-secret-key-change-in-production")
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


def create_token(user_id: int, username: str, role: str) -> str:
    header  = _b64encode({"alg": "HS256", "typ": "JWT"})
    payload = _b64encode({
        "sub":      str(user_id),
        "username": username,
        "role":     role,
        "exp":      int((datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)).timestamp()),
        "iat":      int(datetime.now(timezone.utc).timestamp()),
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


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


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


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


# ── Endpoints ─────────────────────────────────────────────────

@router.post("/login")
async def login(data: LoginIn, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(User.username == data.username.strip())
    )
    user = result.scalar_one_or_none()
    if not user or user.password_hash != hash_password(data.password):
        raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور غير صحيحة")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="الحساب معطل")
    token = create_token(user.id, user.username, user.role)
    return {
        "token": token,
        "user": {
            "id":        user.id,
            "username":  user.username,
            "full_name": user.full_name,
            "role":      user.role,
        },
        "expires_in_hours": TOKEN_EXPIRE_HOURS,
    }


@router.get("/me")
async def get_me(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == int(current_user['sub'])))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "المستخدم غير موجود")
    return {
        "id":        user.id,
        "username":  user.username,
        "full_name": user.full_name,
        "role":      user.role,
        "is_active": user.is_active,
    }


@router.post("/change-password")
async def change_password(
    data: ChangePasswordIn,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == int(current_user['sub'])))
    user = result.scalar_one_or_none()
    if not user or user.password_hash != hash_password(data.old_password):
        raise HTTPException(400, "كلمة المرور القديمة غير صحيحة")
    if len(data.new_password) < 6:
        raise HTTPException(400, "كلمة المرور الجديدة قصيرة جداً (6 أحرف على الأقل)")
    user.password_hash = hash_password(data.new_password)
    await db.commit()
    return {"message": "تم تغيير كلمة المرور بنجاح"}
