"""
Approvals API — دورة الموافقات
قواعد الموافقة (أدمن) + صندوق الطلبات + القرار (موافقة/رفض) + عدّاد المعلّق.
المنطق الفعلي في app/services/approvals.py.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import (
    block_viewer_writes, get_current_user, require_admin, require_admin_accountant, require_any,
)
from app.core.database import get_db
from app.models.models import ApprovalRequest, ApprovalRule, User
from app.services import approvals as svc

router = APIRouter(prefix="/approvals", tags=["approvals"],
                   dependencies=[Depends(require_any), Depends(block_viewer_writes)])

_MAX_AMOUNT = Decimal("999999999999.99")


# ═════════════════════════════════════════════════════════════
# Schemas (تحقق يدوي لإرجاع 400 برسائل عربية بدل 422)
# ═════════════════════════════════════════════════════════════
class RuleIn(BaseModel):
    name: Optional[str] = None
    action_key: Optional[str] = None
    condition_type: Optional[str] = None
    threshold: Optional[Any] = None
    required_role: Optional[str] = "admin"
    valid_hours: Optional[Any] = 24
    exempt_roles: Optional[List[str]] = None
    is_active: Optional[bool] = True


class DecisionIn(BaseModel):
    reason: Optional[str] = None


def _validate_rule(data: RuleIn) -> dict:
    name = (data.name or "").strip()
    if not name or len(name) > 100:
        raise HTTPException(400, "اسم القاعدة مطلوب (حتى 100 حرف)")
    key = (data.action_key or "").strip()
    action = svc.ACTIONS.get(key)
    if not action:
        raise HTTPException(400, "نوع الإجراء غير صالح")
    ctype = (data.condition_type or "").strip()
    allowed = [c["type"] for c in action["conditions"]]
    if ctype not in allowed:
        raise HTTPException(400, "نوع الشرط غير مسموح لهذا الإجراء")
    threshold = None
    if ctype != "always":
        if data.threshold is None or isinstance(data.threshold, bool):
            raise HTTPException(400, "حد الشرط مطلوب")
        try:
            threshold = Decimal(str(data.threshold))
        except (InvalidOperation, ValueError, TypeError):
            raise HTTPException(400, "حد الشرط غير صالح")
        if not threshold.is_finite() or threshold <= 0:
            raise HTTPException(400, "حد الشرط يجب أن يكون أكبر من صفر")
        if ctype == "percent_gt" and threshold > 100:
            raise HTTPException(400, "نسبة الحد لا يمكن أن تتجاوز 100")
        if threshold > _MAX_AMOUNT:
            raise HTTPException(400, "حد الشرط أكبر من الحد المسموح")
        threshold = threshold.quantize(Decimal("0.01"))
    role = (data.required_role or "admin").strip()
    if role not in svc.APPROVER_ROLES:
        raise HTTPException(400, "دور الموافقة يجب أن يكون admin أو accountant")
    vh = data.valid_hours if data.valid_hours is not None else 24
    if isinstance(vh, bool):
        raise HTTPException(400, "مدة الصلاحية غير صالحة")
    try:
        vh_dec = Decimal(str(vh).strip())
        if not vh_dec.is_finite() or vh_dec != vh_dec.to_integral_value():
            raise ValueError
        vh_int = int(vh_dec)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(400, "مدة الصلاحية غير صالحة")
    if vh_int < 1 or vh_int > 720:
        raise HTTPException(400, "مدة الصلاحية بالساعات يجب أن تكون بين 1 و720")
    exempt: List[str] = []
    for er in (data.exempt_roles or []):
        er = (er or "").strip()
        if er not in svc.EXEMPT_ROLE_OPTIONS:
            raise HTTPException(400, "دور الإعفاء غير صالح")
        if er not in exempt:
            exempt.append(er)
    return dict(
        name=name, action_key=key, condition_type=ctype, threshold=threshold,
        required_role=role, valid_hours=vh_int,
        exempt_roles=",".join(exempt) if exempt else None,
        is_active=True if data.is_active is None else bool(data.is_active),
    )


def _rule_dict(r: ApprovalRule, open_requests: int = 0) -> dict:
    return {
        "id": r.id, "name": r.name, "action_key": r.action_key,
        "action_label": svc.action_label(r.action_key),
        "condition_type": r.condition_type,
        "threshold": float(r.threshold) if r.threshold is not None else None,
        "required_role": r.required_role, "valid_hours": r.valid_hours,
        "exempt_roles": svc.split_roles(r.exempt_roles),
        "is_active": bool(r.is_active), "open_requests": open_requests,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


async def _rule_warnings(db: AsyncSession, tid: int, uid: int, vals: dict) -> List[str]:
    """تحذير لو مفيش مستخدم تاني نشط يقدر يوافق (منع الموافقة الذاتية => شلل لمستخدم وحيد)."""
    if not vals["is_active"]:
        return []
    exempt = svc.split_roles(vals["exempt_roles"])
    if "admin" in exempt:
        return []
    roles = ["admin"] if vals["required_role"] == "admin" else ["admin", "accountant"]
    others = await db.scalar(
        select(func.count()).select_from(User).where(
            User.tenant_id == tid, User.is_active.is_(True), User.role.in_(roles), User.id != uid,
        )
    )
    if not others:
        who = "مدير" if vals["required_role"] == "admin" else "مدير أو محاسب"
        return [f"لا يوجد مستخدم آخر نشط بدور ({who}) يمكنه الموافقة — لا يمكن الموافقة على طلبك بنفسك. "
                f"أضف مستخدمًا آخر أو أعفِ دورك من القاعدة."]
    return []


# ═════════════════════════════════════════════════════════════
# Meta
# ═════════════════════════════════════════════════════════════
@router.get("/meta")
async def approvals_meta():
    return {
        "actions": [
            {"key": k, "label": v["label"], "entity_type": v["entity_type"], "conditions": v["conditions"]}
            for k, v in svc.ACTIONS.items()
        ],
        "roles": [{"key": r, "label": svc.role_label(r)} for r in svc.APPROVER_ROLES],
        "exempt_role_options": svc.EXEMPT_ROLE_OPTIONS,
        "statuses": [{"key": k, "label": v} for k, v in svc.STATUS_LABELS.items()],
    }


# ═════════════════════════════════════════════════════════════
# Rules (أدمن فقط)
# ═════════════════════════════════════════════════════════════
@router.get("/rules")
async def list_rules(db: AsyncSession = Depends(get_db), user: dict = Depends(require_admin)):
    tid = int(user["tenant_id"])
    rules = (await db.execute(
        select(ApprovalRule).where(ApprovalRule.tenant_id == tid).order_by(ApprovalRule.id)
    )).scalars().all()
    counts = dict((await db.execute(
        select(ApprovalRequest.rule_id, func.count()).where(
            ApprovalRequest.tenant_id == tid, ApprovalRequest.status.in_(svc.OPEN_STATUSES),
            ApprovalRequest.rule_id.is_not(None),
        ).group_by(ApprovalRequest.rule_id)
    )).all())
    return {"items": [_rule_dict(r, int(counts.get(r.id, 0))) for r in rules]}


@router.post("/rules", status_code=201)
async def create_rule(data: RuleIn, db: AsyncSession = Depends(get_db),
                      user: dict = Depends(require_admin)):
    tid, uid = int(user["tenant_id"]), int(user["sub"])
    vals = _validate_rule(data)
    warnings = await _rule_warnings(db, tid, uid, vals)
    r = ApprovalRule(tenant_id=tid, created_by=uid, created_at=datetime.utcnow(), **vals)
    db.add(r)
    await db.flush()
    out = _rule_dict(r)
    await db.commit()
    return {**out, "warnings": warnings}


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: int, data: RuleIn, db: AsyncSession = Depends(get_db),
                      user: dict = Depends(require_admin)):
    tid, uid = int(user["tenant_id"]), int(user["sub"])
    r = (await db.execute(
        select(ApprovalRule).where(ApprovalRule.id == rule_id, ApprovalRule.tenant_id == tid)
    )).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "القاعدة غير موجودة")
    vals = _validate_rule(data)
    warnings = await _rule_warnings(db, tid, uid, vals)
    for k, v in vals.items():
        setattr(r, k, v)
    r.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(r)
    return {**_rule_dict(r), "warnings": warnings}


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db),
                      user: dict = Depends(require_admin)):
    tid = int(user["tenant_id"])
    r = (await db.execute(
        select(ApprovalRule).where(ApprovalRule.id == rule_id, ApprovalRule.tenant_id == tid)
    )).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "القاعدة غير موجودة")
    used = await db.scalar(
        select(func.count()).select_from(ApprovalRequest).where(
            ApprovalRequest.tenant_id == tid, ApprovalRequest.rule_id == rule_id)
    )
    if used:
        raise HTTPException(409, "استُخدمت هذه القاعدة في طلبات موافقة — عطّلها بدلاً من حذفها")
    await db.delete(r)
    await db.commit()
    return Response(status_code=204)


# ═════════════════════════════════════════════════════════════
# Requests
# ═════════════════════════════════════════════════════════════
@router.get("/pending-count")
async def pending_count(db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    return await svc.count_requests(db, int(user["tenant_id"]), int(user["sub"]), user.get("role"))


@router.get("/requests")
async def list_requests(
    scope: Optional[str] = None,
    status: Optional[str] = None,
    action_key: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    tid, uid = int(user["tenant_id"]), int(user["sub"])
    role = user.get("role")
    is_approver = role in svc.APPROVER_ROLES
    scope = (scope or ("inbox" if is_approver else "mine")).strip()
    if scope not in ("inbox", "mine", "all"):
        raise HTTPException(400, "نطاق غير صالح")
    if scope == "all" and role != "admin":
        raise HTTPException(403, "هذا النطاق للمدير فقط")
    if scope == "inbox" and not is_approver:
        scope = "mine"
    if status is None:
        status = "pending" if scope == "inbox" else "all"
    if status != "all" and status not in svc.STATUS_LABELS:
        raise HTTPException(400, "حالة غير صالحة")
    if action_key and action_key not in svc.ACTIONS:
        raise HTTPException(400, "نوع الإجراء غير صالح")
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))

    # انتهاء الصلاحية الكسول قبل القراءة
    await svc.expire_stale_approvals(db, tid)
    await db.commit()

    conds = [ApprovalRequest.tenant_id == tid]
    if scope == "mine":
        conds.append(ApprovalRequest.requested_by == uid)
    elif scope == "inbox":
        conds.append(ApprovalRequest.requested_by != uid)
        if role != "admin":
            conds.append(ApprovalRequest.required_role == "accountant")
    if status != "all":
        conds.append(ApprovalRequest.status == status)
    if action_key:
        conds.append(ApprovalRequest.action_key == action_key)

    total = int(await db.scalar(select(func.count()).select_from(ApprovalRequest).where(*conds)) or 0)
    rows = (await db.execute(
        select(ApprovalRequest).where(*conds)
        .order_by(ApprovalRequest.requested_at.desc(), ApprovalRequest.id.desc())
        .limit(limit).offset(offset)
    )).scalars().all()
    return {"items": [svc.request_to_dict(r, uid, role) for r in rows], "total": total}


@router.get("/requests/{rid}")
async def get_request(rid: int, db: AsyncSession = Depends(get_db),
                      user: dict = Depends(get_current_user)):
    tid, uid = int(user["tenant_id"]), int(user["sub"])
    role = user.get("role")
    r = (await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.id == rid, ApprovalRequest.tenant_id == tid)
    )).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "طلب الموافقة غير موجود")
    visible = (r.requested_by == uid or role == "admin"
               or (role == "accountant" and r.required_role == "accountant"))
    if not visible:
        raise HTTPException(404, "طلب الموافقة غير موجود")
    return svc.request_to_dict(r, uid, role, detail=True)


@router.post("/requests/{rid}/approve")
async def approve_request(rid: int, data: Optional[DecisionIn] = None,
                          db: AsyncSession = Depends(get_db),
                          user: dict = Depends(require_admin_accountant)):
    r = await svc.decide_request(db, user, rid, "approve", data.reason if data else None)
    return svc.request_to_dict(r, int(user["sub"]), user.get("role"), detail=True)


@router.post("/requests/{rid}/reject")
async def reject_request(rid: int, data: Optional[DecisionIn] = None,
                         db: AsyncSession = Depends(get_db),
                         user: dict = Depends(require_admin_accountant)):
    r = await svc.decide_request(db, user, rid, "reject", data.reason if data else None)
    return svc.request_to_dict(r, int(user["sub"]), user.get("role"), detail=True)


@router.post("/requests/{rid}/cancel")
async def cancel_request(rid: int, db: AsyncSession = Depends(get_db),
                         user: dict = Depends(get_current_user)):
    r = await svc.cancel_request(db, user, rid)
    return svc.request_to_dict(r, int(user["sub"]), user.get("role"), detail=True)
