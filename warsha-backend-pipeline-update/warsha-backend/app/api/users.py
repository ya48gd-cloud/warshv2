"""
Users API — إدارة المستخدمين (admin only)
GET    /users/        → list all users
POST   /users/        → create user
PUT    /users/{id}    → update role / full_name / is_active
POST   /users/{id}/reset-password
DELETE /users/{id}
GET    /users/permissions → permissions matrix
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_db
from app.models.models import User
from app.core.database import current_tenant_id


def _tid() -> int:
    """يجلب tenant_id الحالي من ContextVar أو يرفع 401."""
    tid = current_tenant_id.get()
    if tid is None:
        from fastapi import HTTPException
        raise HTTPException(401, "غير مسموح")
    return tid
from app.api.auth import require_admin, hash_password

router = APIRouter(prefix="/users", tags=["users"])

VALID_ROLES = {"admin", "accountant", "production", "viewer"}

ROLE_LABELS = {
    "admin":      "مدير النظام",
    "accountant": "محاسب",
    "production": "إنتاج",
    "viewer":     "مشاهد",
}

ROLE_NAV = {
    "admin":      ["dashboard","materials","movements","workorders","costing",
                   "equipment","bom","workers","payroll","attendance",
                   "customers","orders","quotations","invoices","production",
                   "mrp","scrap","stockcount","users","paymenthistory",
                   "supplycontracts","deliverynotes"],
    "accountant": ["dashboard","materials","movements","workorders",
                   "workers","payroll","attendance",
                   "customers","orders","quotations","invoices","scrap","paymenthistory",
                   "supplycontracts","deliverynotes"],
    "production": ["dashboard","materials","movements","workorders","costing",
                   "equipment","bom","production","mrp","scrap","stockcount"],
    "viewer":     ["dashboard","materials","workorders","equipment","bom",
                   "customers","quotations","invoices","supplycontracts","deliverynotes"],
}

ROLE_WRITE = {
    "admin":      ["inventory","production","sales","workers","customers","users"],
    "accountant": ["sales","workers","customers"],
    "production": ["inventory","production"],
    "viewer":     [],
}


# ── Schemas ────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = None
    role: str = "viewer"


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class ResetPasswordIn(BaseModel):
    new_password: str


# ── Helpers ────────────────────────────────────────────────────

def _serialize(u: User) -> dict:
    return {
        "id":         u.id,
        "username":   u.username,
        "full_name":  u.full_name,
        "role":       u.role,
        "role_label": ROLE_LABELS.get(u.role, u.role),
        "is_active":  u.is_active,
    }


async def _count_active_admins(db: AsyncSession) -> int:
    res = await db.execute(
        select(User).where(
            User.tenant_id == _tid(),
            User.role == "admin",
            User.is_active == True,
        )
    )
    return len(res.scalars().all())


# ── Routes ─────────────────────────────────────────────────────

@router.get("/permissions")
async def get_permissions(_: dict = Depends(require_admin)):
    return {
        "roles":       list(VALID_ROLES),
        "labels":      ROLE_LABELS,
        "nav":         ROLE_NAV,
        "write":       ROLE_WRITE,
    }


@router.get("/")
async def list_users(
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.tenant_id == _tid()).order_by(User.id)
    )
    return [_serialize(u) for u in result.scalars().all()]


@router.post("/", status_code=201)
async def create_user(
    body: UserCreate,
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"دور غير صالح. الخيارات: {list(VALID_ROLES)}")
    if len(body.password) < 6:
        raise HTTPException(400, "كلمة المرور قصيرة (6 أحرف على الأقل)")
    if len(body.password) > 128:
        raise HTTPException(400, "كلمة المرور طويلة جداً (128 حرف كحد أقصى)")
    # الـ login بيعمل strip لاسم المستخدم — لو خزّناه بمسافات عمره ما هيعرف يدخل
    username = (body.username or "").strip()
    if not username:
        raise HTTPException(400, "اسم المستخدم مطلوب")
    if len(username) > 50:
        raise HTTPException(400, "اسم المستخدم طويل جداً (50 حرف كحد أقصى)")
    full_name = (body.full_name or "").strip() or username
    if len(full_name) > 200:
        raise HTTPException(400, "الاسم الكامل طويل جداً (200 حرف كحد أقصى)")

    existing = await db.execute(
        select(User).where(User.username == username, User.tenant_id == _tid())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "اسم المستخدم موجود بالفعل")

    user = User(
        tenant_id=_tid(),
        username=username,
        password_hash=hash_password(body.password),
        full_name=full_name,
        role=body.role,
        is_active=True,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "اسم المستخدم موجود بالفعل")
    await db.refresh(user)
    return _serialize(user)


@router.put("/{user_id}")
async def update_user(
    user_id: int,
    body: UserUpdate,
    current_admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == _tid())
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "المستخدم غير موجود")

    if body.role is not None:
        if body.role not in VALID_ROLES:
            raise HTTPException(400, f"دور غير صالح: {body.role}")
        # Prevent demoting last admin
        if user.role == "admin" and body.role != "admin" and user.is_active:
            if await _count_active_admins(db) <= 1:
                raise HTTPException(400, "لا يمكن تغيير دور آخر مدير متبقي")
        user.role = body.role

    if body.full_name is not None:
        if len(body.full_name) > 200:
            raise HTTPException(400, "الاسم الكامل طويل جداً (200 حرف كحد أقصى)")
        user.full_name = body.full_name.strip() or user.username

    if body.is_active is not None:
        if user.id == int(current_admin["sub"]) and not body.is_active:
            raise HTTPException(400, "لا يمكنك تعطيل حسابك الخاص")
        if user.role == "admin" and not body.is_active and user.is_active:
            if await _count_active_admins(db) <= 1:
                raise HTTPException(400, "لا يمكن تعطيل آخر مدير متبقي")
        user.is_active = body.is_active

    await db.commit()
    return _serialize(user)


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    body: ResetPasswordIn,
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == _tid())
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "المستخدم غير موجود")
    if len(body.new_password) < 6:
        raise HTTPException(400, "كلمة المرور قصيرة (6 أحرف على الأقل)")
    if len(body.new_password) > 128:
        raise HTTPException(400, "كلمة المرور طويلة جداً (128 حرف كحد أقصى)")
    user.password_hash = hash_password(body.new_password)
    await db.commit()
    return {"message": f"تم إعادة تعيين كلمة مرور {user.username}"}


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    current_admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if user_id == int(current_admin["sub"]):
        raise HTTPException(400, "لا يمكنك حذف حسابك الخاص")

    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == _tid())
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "المستخدم غير موجود")

    if user.role == "admin" and user.is_active and await _count_active_admins(db) <= 1:
        raise HTTPException(400, "لا يمكن حذف آخر مدير متبقي")

    # GAP (fixed): المستخدم مربوط بـ FK (created_by/approved_by) في دفعات العملاء
    # والموردين والقيود والخزينة وأوامر الشراء وحركات الأصول. الحذف المباشر كان
    # بيرمي IntegrityError (500) على Postgres أو يسيب مراجع يتيمة ويضيّع أثر
    # المراجعة (audit trail). في الحالة دي نمنع الحذف ونوجّه للتعطيل بدله.
    from sqlalchemy import func as _func
    from app.models.models import (
        CustomerPayment, SupplierPayment, AssetMovement, PurchaseOrder,
        AccountingEntry, TreasuryTransaction,
    )
    for _model, _col in (
        (CustomerPayment, "created_by"), (SupplierPayment, "created_by"),
        (AssetMovement, "created_by"), (PurchaseOrder, "approved_by"),
        (AccountingEntry, "created_by"), (TreasuryTransaction, "created_by"),
    ):
        _n = await db.scalar(
            select(_func.count()).select_from(_model).where(getattr(_model, _col) == user.id)
        )
        if _n:
            raise HTTPException(
                409,
                "لا يمكن حذف المستخدم لأنه مرتبط بعمليات مسجّلة (دفعات/قيود/خزينة...). "
                "قم بتعطيل الحساب بدلاً من حذفه.",
            )

    await db.delete(user)
    await db.commit()
    return {"message": f"تم حذف المستخدم {user.username}"}
