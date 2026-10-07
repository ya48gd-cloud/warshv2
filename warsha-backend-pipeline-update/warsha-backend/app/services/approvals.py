"""
دورة الموافقات (Approval Workflow) — الخدمة العامة.

الاستخدام من أي endpoint حساس:

    await require_approval(
        db, current_user, "po_approve",
        entity_type="purchase_order", entity_id=po.id, entity_label=po.code,
        metrics={"amount": to_dec(po.total)},
        params={"total": to_dec(po.total), "supplier_id": po.supplier_id},
    )

السلوك:
  * مفيش قاعدة فعّالة للإجراء (أو الشرط مش متحقق / المستخدم معفى) => يرجّع None
    ويكمل الإجراء كالمعتاد (استعلام واحد على approval_rules فقط).
  * فيه موافقة معتمدة صالحة لنفس (المستخدم + الإجراء + البصمة) => تُستهلك ذرّيًا
    (UPDATE مشروط) داخل نفس transaction الإجراء، فلو الإجراء فشل بعدها تتراجع
    الاستهلاك وتبقى الموافقة صالحة. موافقة واحدة = تنفيذ واحد.
  * غير كده => يرفع ApprovalRequired (HTTP 428) وينشئ الطلب (أو يعيد الطلب
    المعلّق الموجود بدون إنشاء جديد).

قواعد صارمة لمن يستدعي require_approval (مهم):
  1. استدعِها بعد كل التحققات وفحوصات الحالة وقبل أول تعديل (mutation).
     مسار الرفض يعمل db.rollback() ثم يكتب الطلب ويعمل commit على نفس الجلسة،
     فأي تعديل سابق في نفس الجلسة هيتلغي (وده المقصود: الإجراء المرفوض ما يسيبش أثر).
  2. بعد db.rollback() كل كائنات ORM اللي اتحمّلت قبل كده بتبقى expired،
     ولمس أي attribute فيها في async يرفع MissingGreenlet. لذلك مرّر للدالة
     قيم Python عادية فقط (str/int/Decimal/dict) محسوبة قبل الاستدعاء،
     والخدمة نفسها ما بتقراش أي كائن ORM بعد الـ rollback.
  3. ApprovalRequired تابعة لـ HTTPException (428) عشان أي "except HTTPException: raise"
     موجود في الـ endpoints يمرّرها، ولا تتحول لـ 500.

التكامل مع سجل التدقيق لاحقًا: register_approval_listener(fn) — fn(db, event, request_dict, actor_dict)
بنفس جلسة قاعدة البيانات (best-effort، الأخطاء تُسجَّل وتُبلَع). الخدمة لا تستورد شيئًا من ميزة التدقيق
ولا تعتمد على أي ContextVar؛ كل شيء من قاموس get_current_user (tenant_id/sub/username/role).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import ApprovalRequest, ApprovalRule

logger = logging.getLogger("warsha.approvals")

_CENT = Decimal("0.01")

# ─────────────────────────────────────────────────────────────
# كتالوج الإجراءات القابلة للإعداد
# ─────────────────────────────────────────────────────────────
_COND_ALWAYS = {"type": "always", "label": "دائمًا (أي حالة)", "unit": None}


def _amt_cond(label: str) -> Dict[str, Any]:
    return {"type": "amount_gt", "label": label, "unit": "egp"}


ACTIONS: Dict[str, Dict[str, Any]] = {
    "po_approve": {
        "label": "اعتماد أمر شراء",
        "entity_type": "purchase_order",
        "conditions": [_COND_ALWAYS, _amt_cond("إجمالي أمر الشراء أكبر من")],
    },
    "invoice_discount": {
        "label": "خصم على فاتورة",
        "entity_type": "invoice",
        "conditions": [
            _COND_ALWAYS,
            _amt_cond("قيمة الخصم أكبر من"),
            {"type": "percent_gt", "label": "نسبة الخصم أكبر من", "unit": "percent"},
        ],
    },
    "invoice_delete": {
        "label": "حذف/إلغاء فاتورة صادرة",
        "entity_type": "invoice",
        "conditions": [_COND_ALWAYS, _amt_cond("إجمالي الفاتورة أكبر من")],
    },
    "worker_advance": {
        "label": "سلفة عامل",
        "entity_type": "worker",
        "conditions": [_COND_ALWAYS, _amt_cond("مبلغ السلفة أكبر من")],
    },
    "credit_note_issue": {
        "label": "إصدار إشعار دائن",
        "entity_type": "credit_note",
        "conditions": [_COND_ALWAYS, _amt_cond("إجمالي الإشعار أكبر من")],
    },
    "treasury_out": {
        "label": "صرف من الخزينة",
        "entity_type": "treasury_transaction",
        "conditions": [_COND_ALWAYS, _amt_cond("مبلغ الصرف أكبر من")],
    },
}

APPROVER_ROLES = ("admin", "accountant")
ROLE_LABELS = {"admin": "مدير", "accountant": "محاسب", "production": "إنتاج", "viewer": "مشاهد"}
EXEMPT_ROLE_OPTIONS = ["admin", "accountant", "production"]
STATUS_LABELS = {
    "pending": "معلّق",
    "approved": "تمت الموافقة",
    "rejected": "مرفوض",
    "consumed": "منفَّذ",
    "cancelled": "ملغي",
    "expired": "منتهي الصلاحية",
}
OPEN_STATUSES = ("pending", "approved")


def action_label(key: str) -> str:
    return (ACTIONS.get(key) or {}).get("label") or key


def role_label(role: Optional[str]) -> str:
    return ROLE_LABELS.get(role or "", role or "")


# ─────────────────────────────────────────────────────────────
# أدوات مساعدة
# ─────────────────────────────────────────────────────────────
def to_dec(v: Any, default: Optional[Decimal] = Decimal("0")) -> Optional[Decimal]:
    """تحويل آمن لـ Decimal مقرّب لقرشين (None/خطأ => default)."""
    if v is None:
        return default
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return default
    if not d.is_finite():
        return default
    return d.quantize(_CENT, rounding=ROUND_HALF_UP)


def _canon(v: Any) -> Any:
    if isinstance(v, bool) or v is None or isinstance(v, (int, str)):
        return v
    if isinstance(v, (Decimal, float)):
        d = to_dec(v, None)
        return str(d) if d is not None else None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _canon(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    return str(v)


def fingerprint(action_key: str, entity_type: str, entity_id: Optional[int],
                params: Optional[Dict[str, Any]]) -> str:
    """بصمة sha256 للإجراء: أي تغيير في المعاملات الأساسية بعد الموافقة يعني طلب جديد."""
    blob = json.dumps(
        {"a": action_key, "t": entity_type, "i": entity_id, "p": _canon(params or {})},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _fnum(v: Any) -> Optional[float]:
    return float(v) if v is not None else None


def split_roles(csv: Optional[str]) -> List[str]:
    return [r.strip() for r in (csv or "").split(",") if r.strip()]


def role_can_decide(role: Optional[str], required_role: str) -> bool:
    """admin يوافق على أي طلب، accountant بس على طلبات required_role == accountant."""
    if role == "admin":
        return True
    return role == "accountant" and required_role == "accountant"


def _ids(user: dict) -> tuple:
    return int(user["tenant_id"]), int(user["sub"])


def _actor(user: dict) -> dict:
    return {"user_id": int(user["sub"]), "username": user.get("username"), "role": user.get("role"),
            "tenant_id": int(user["tenant_id"])}


# ─────────────────────────────────────────────────────────────
# الاستثناء (428) والمعالج
# ─────────────────────────────────────────────────────────────
class ApprovalRequired(HTTPException):
    """تابعة لـ HTTPException عمدًا (راجع docstring الموديول)."""

    def __init__(self, message: str, approval: Dict[str, Any]):
        super().__init__(status_code=428, detail=message)
        self.approval = approval


async def approval_required_handler(request: Request, exc: ApprovalRequired):
    return JSONResponse(
        status_code=428,
        content={"detail": exc.detail, "code": "approval_required", "approval": exc.approval},
    )


# ─────────────────────────────────────────────────────────────
# Listeners (للتكامل لاحقًا مع سجل التدقيق) — بسيط وbest-effort
# ─────────────────────────────────────────────────────────────
_listeners: List[Any] = []


def register_approval_listener(fn) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def unregister_approval_listener(fn) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


async def _emit(db: AsyncSession, event: str, req: dict, actor: Optional[dict]) -> None:
    for fn in list(_listeners):
        try:
            res = fn(db, event, req, actor)
            if inspect.isawaitable(res):
                await res
        except Exception:  # noqa: BLE001 — لا نكسر العملية التجارية بسبب مستمع
            logger.warning("approval listener failed for event %s", event, exc_info=True)


# ─────────────────────────────────────────────────────────────
# تحويلات العرض
# ─────────────────────────────────────────────────────────────
def _rule_plain(r: ApprovalRule) -> Dict[str, Any]:
    return {
        "id": r.id, "name": r.name, "condition_type": r.condition_type,
        "threshold": r.threshold, "required_role": r.required_role or "admin",
        "valid_hours": int(r.valid_hours or 24),
    }


def _rule_matches(condition_type: str, threshold: Optional[Decimal],
                  amount: Optional[Decimal], percent: Optional[Decimal]) -> bool:
    if condition_type == "always":
        return True
    if threshold is None:
        return False
    if condition_type == "amount_gt":
        return amount is not None and amount > Decimal(str(threshold))
    if condition_type == "percent_gt":
        return percent is not None and percent > Decimal(str(threshold))
    return False


def _build_summary(key: str, entity_label: Optional[str], amount: Optional[Decimal],
                   percent: Optional[Decimal]) -> str:
    parts = [action_label(key)]
    if entity_label:
        parts.append(str(entity_label))
    if amount is not None:
        parts.append(f"المبلغ {float(amount):,.2f}")
    if percent is not None:
        parts.append(f"النسبة {float(percent):,.2f}%")
    return " — ".join(parts)


def _approval_payload(req: Dict[str, Any], previous_rejection: Optional[dict] = None) -> Dict[str, Any]:
    payload = {
        "request_id": req["id"],
        "status": req["status"],
        "action_key": req["action_key"],
        "action_label": action_label(req["action_key"]),
        "required_role": req["required_role"],
        "required_role_label": role_label(req["required_role"]),
        "amount": _fnum(req.get("amount")),
        "summary": req.get("summary") or action_label(req["action_key"]),
    }
    if previous_rejection:
        payload["previous_rejection"] = previous_rejection
    return payload


def _required_message(req: Dict[str, Any], fresh: bool) -> str:
    rl = role_label(req["required_role"])
    if req["status"] == "approved":
        return f"طلب الموافقة رقم {req['id']} تمت الموافقة عليه — أعد تنفيذ الإجراء مرة واحدة."
    if fresh:
        return (f"هذا الإجراء يتطلب موافقة ({rl}) - تم إنشاء طلب موافقة رقم {req['id']}. "
                f"أعد المحاولة بعد الموافقة.")
    return (f"هذا الإجراء يتطلب موافقة ({rl}) - طلب الموافقة رقم {req['id']} ما زال معلّقًا. "
            f"أعد المحاولة بعد الموافقة.")


def _row_plain(r: ApprovalRequest) -> Dict[str, Any]:
    snap = r.snapshot or {}
    return {
        "id": r.id, "action_key": r.action_key, "entity_type": r.entity_type,
        "entity_id": r.entity_id, "entity_label": r.entity_label,
        "amount": r.amount, "percent": r.percent, "status": r.status,
        "required_role": r.required_role, "summary": snap.get("summary"),
        "requested_by": r.requested_by,
    }


def request_to_dict(r: ApprovalRequest, uid: int, role: Optional[str], detail: bool = False) -> Dict[str, Any]:
    snap = r.snapshot or {}
    d = {
        "id": r.id,
        "action_key": r.action_key,
        "action_label": action_label(r.action_key),
        "entity_type": r.entity_type,
        "entity_id": r.entity_id,
        "entity_label": r.entity_label,
        "amount": _fnum(r.amount),
        "percent": _fnum(r.percent),
        "status": r.status,
        "required_role": r.required_role,
        "required_role_label": role_label(r.required_role),
        "requested_by": r.requested_by,
        "requested_by_name": r.requested_by_name,
        "requested_by_role": r.requested_by_role,
        "requested_at": r.requested_at.isoformat() if r.requested_at else None,
        "decided_by": r.decided_by,
        "decided_by_name": r.decided_by_name,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "decision_reason": r.decision_reason,
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        "consumed_at": r.consumed_at.isoformat() if r.consumed_at else None,
        "summary": snap.get("summary") or action_label(r.action_key),
        "is_mine": r.requested_by == uid,
        "can_decide": (r.status == "pending" and role_can_decide(role, r.required_role)
                       and r.requested_by != uid),
    }
    if detail:
        d["snapshot"] = snap
    return d


# ─────────────────────────────────────────────────────────────
# انتهاء الصلاحية (lazy — مفيش job في الخلفية)
# ─────────────────────────────────────────────────────────────
async def expire_stale_approvals(db: AsyncSession, tenant_id: int) -> int:
    """يحوّل الموافقات المعتمدة المنتهية (ولم تُستهلك) إلى expired. لا يعمل commit — المستدعي يلتزم."""
    now = datetime.utcnow()
    rows = (await db.execute(
        select(ApprovalRequest.id, ApprovalRequest.action_key).where(
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.status == "approved",
            ApprovalRequest.expires_at.is_not(None),
            ApprovalRequest.expires_at <= now,
        )
    )).all()
    n = 0
    for rid, _key in rows:
        res = await db.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == rid, ApprovalRequest.tenant_id == tenant_id,
                   ApprovalRequest.status == "approved")
            .values(status="expired", updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount == 1:
            n += 1
            await _emit(db, "expired", {"id": rid, "action_key": _key, "status": "expired"}, None)
    return n


# ─────────────────────────────────────────────────────────────
# require_approval — نقطة الاستدعاء الرئيسية
# ─────────────────────────────────────────────────────────────
async def _open_request(db: AsyncSession, tid: int, uid: int, fp: str) -> Optional[Dict[str, Any]]:
    r = (await db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.tenant_id == tid,
            ApprovalRequest.requested_by == uid,
            ApprovalRequest.fingerprint == fp,
            ApprovalRequest.status.in_(OPEN_STATUSES),
        ).order_by(ApprovalRequest.id.desc()).limit(1)
    )).scalar_one_or_none()
    return _row_plain(r) if r is not None else None


async def require_approval(
    db: AsyncSession,
    user: dict,
    action_key: str,
    *,
    entity_type: str,
    entity_id: Optional[int] = None,
    entity_label: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    summary: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """راجع docstring الموديول. يرجّع None (لا حاجة لموافقة) أو {"request_id", "consumed": True}.
    يرفع ApprovalRequired (428) لما الموافقة لسه مطلوبة."""
    if action_key not in ACTIONS:
        raise ValueError(f"approval action_key غير معروف: {action_key}")
    tid, uid = _ids(user)
    urole = user.get("role")
    metrics = metrics or {}
    amount = to_dec(metrics.get("amount"), None)
    percent = to_dec(metrics.get("percent"), None)

    # 1) القواعد الفعّالة (فلتر tenant صريح — مش معتمدين على ContextVar)
    rules = (await db.execute(
        select(ApprovalRule).where(
            ApprovalRule.tenant_id == tid,
            ApprovalRule.action_key == action_key,
            ApprovalRule.is_active.is_(True),
        ).order_by(ApprovalRule.id)
    )).scalars().all()
    if not rules:
        return None

    # قيم عادية فقط من هنا (قبل أي rollback)
    matched: List[Dict[str, Any]] = []
    for r in rules:
        if urole in split_roles(r.exempt_roles):
            continue
        if _rule_matches(r.condition_type, r.threshold, amount, percent):
            matched.append(_rule_plain(r))
    if not matched:
        return None

    strictest = next((m for m in matched if m["required_role"] == "admin"), matched[0])
    required_role = "admin" if strictest["required_role"] == "admin" else "accountant"
    valid_hours = strictest["valid_hours"]
    fp = fingerprint(action_key, entity_type, entity_id, params)
    now = datetime.utcnow()
    actor = _actor(user)

    # 2) محاولة استهلاك موافقة معتمدة صالحة (ذرّي، داخل transaction الإجراء نفسه)
    cand = (await db.execute(
        select(ApprovalRequest.id).where(
            ApprovalRequest.tenant_id == tid,
            ApprovalRequest.requested_by == uid,
            ApprovalRequest.fingerprint == fp,
            ApprovalRequest.status == "approved",
            ApprovalRequest.expires_at.is_not(None),
            ApprovalRequest.expires_at > now,
        ).order_by(ApprovalRequest.id.desc()).limit(1)
    )).scalar_one_or_none()
    if cand is not None:
        res = await db.execute(
            update(ApprovalRequest)
            .where(
                ApprovalRequest.id == cand, ApprovalRequest.tenant_id == tid,
                ApprovalRequest.requested_by == uid, ApprovalRequest.status == "approved",
                ApprovalRequest.expires_at.is_not(None), ApprovalRequest.expires_at > now,
            )
            .values(status="consumed", consumed_at=now, consumed_by=uid, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount == 1:
            await _emit(db, "consumed",
                        {"id": cand, "action_key": action_key, "entity_type": entity_type,
                         "entity_id": entity_id, "entity_label": entity_label, "status": "consumed"},
                        actor)
            return {"request_id": cand, "consumed": True}
        # خسرنا سباق (ضغطة مزدوجة/طلب موازي) — نكمل لمسار الرفض

    # 3) مسار الرفض: نلغي أي أثر للإجراء المرفوض ثم نكتب الطلب في معاملة مستقلة
    await db.rollback()
    await expire_stale_approvals(db, tid)

    existing = await _open_request(db, tid, uid, fp)
    if existing is not None:
        await db.commit()
        raise ApprovalRequired(_required_message(existing, fresh=False), _approval_payload(existing))

    prev = (await db.execute(
        select(ApprovalRequest.id, ApprovalRequest.status, ApprovalRequest.decision_reason).where(
            ApprovalRequest.tenant_id == tid,
            ApprovalRequest.requested_by == uid,
            ApprovalRequest.fingerprint == fp,
        ).order_by(ApprovalRequest.id.desc()).limit(1)
    )).first()
    previous_rejection = None
    if prev is not None and prev[1] == "rejected":
        previous_rejection = {"request_id": prev[0], "reason": prev[2] or ""}

    summary_txt = summary or _build_summary(action_key, entity_label, amount, percent)
    snapshot = _jsonable({
        "summary": summary_txt,
        "params": params or {},
        "metrics": {"amount": amount, "percent": percent},
        "rule": {"id": strictest["id"], "name": strictest["name"],
                 "condition_type": strictest["condition_type"],
                 "threshold": strictest["threshold"]},
    })
    new = ApprovalRequest(
        tenant_id=tid, rule_id=strictest["id"], action_key=action_key,
        entity_type=entity_type, entity_id=entity_id,
        entity_label=(str(entity_label)[:200] if entity_label else None),
        fingerprint=fp, amount=amount, percent=percent, snapshot=snapshot,
        status="pending", required_role=required_role, valid_hours=valid_hours,
        requested_by=uid, requested_by_name=(user.get("username") or None),
        requested_by_role=urole, requested_at=now, created_at=now,
    )
    db.add(new)
    try:
        await db.flush()
    except IntegrityError:
        # سباق: طلب موازي لنفس المستخدم/البصمة اتكتب قبلنا
        await db.rollback()
        existing = await _open_request(db, tid, uid, fp)
        if existing is None:
            raise
        raise ApprovalRequired(_required_message(existing, fresh=False), _approval_payload(existing))
    plain = {
        "id": new.id, "action_key": action_key, "entity_type": entity_type, "entity_id": entity_id,
        "entity_label": entity_label, "amount": amount, "percent": percent, "status": "pending",
        "required_role": required_role, "summary": summary_txt, "requested_by": uid,
    }
    await _emit(db, "requested", plain, actor)
    await db.commit()
    raise ApprovalRequired(_required_message(plain, fresh=True),
                           _approval_payload(plain, previous_rejection))


# ─────────────────────────────────────────────────────────────
# القرار / الإلغاء
# ─────────────────────────────────────────────────────────────
async def _load_request(db: AsyncSession, tid: int, rid: int) -> ApprovalRequest:
    r = (await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.id == rid, ApprovalRequest.tenant_id == tid)
    )).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "طلب الموافقة غير موجود")
    return r


async def decide_request(db: AsyncSession, user: dict, rid: int, decision: str,
                         reason: Optional[str]) -> ApprovalRequest:
    """decision: approve | reject. الحراس: الدور، منع الموافقة الذاتية، الحالة (UPDATE مشروط)."""
    tid, uid = _ids(user)
    role = user.get("role")
    r = await _load_request(db, tid, rid)
    plain = _row_plain(r)
    req_status, req_role, req_by = r.status, r.required_role, r.requested_by
    valid_hours = int(r.valid_hours or 24)
    if not role_can_decide(role, req_role):
        raise HTTPException(403, f"هذا الطلب يحتاج موافقة دور {role_label(req_role)}")
    if req_by == uid:
        raise HTTPException(403, "لا يمكنك الموافقة على طلبك أنت")
    reason = (reason or "").strip()
    if decision == "reject" and len(reason) < 3:
        raise HTTPException(400, "سبب الرفض مطلوب (3 أحرف على الأقل)")
    if req_status != "pending":
        raise HTTPException(409, "تم البت في هذا الطلب مسبقاً")
    now = datetime.utcnow()
    vals: Dict[str, Any] = dict(
        status="approved" if decision == "approve" else "rejected",
        decided_by=uid, decided_by_name=user.get("username"), decided_at=now,
        decision_reason=reason or None, updated_at=now,
    )
    if decision == "approve":
        vals["expires_at"] = now + timedelta(hours=valid_hours)
    res = await db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.id == rid, ApprovalRequest.tenant_id == tid,
               ApprovalRequest.status == "pending")
        .values(**vals).execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "تم البت في هذا الطلب مسبقاً")
    await _emit(db, "approved" if decision == "approve" else "rejected",
                {**plain, "status": vals["status"], "decision_reason": reason or None},
                _actor(user))
    await db.commit()
    db.expire_all()
    return await _load_request(db, tid, rid)


async def cancel_request(db: AsyncSession, user: dict, rid: int) -> ApprovalRequest:
    tid, uid = _ids(user)
    role = user.get("role")
    r = await _load_request(db, tid, rid)
    plain = _row_plain(r)
    if r.requested_by != uid and role != "admin":
        raise HTTPException(403, "لا يمكنك إلغاء طلب مستخدم آخر")
    if r.status not in OPEN_STATUSES:
        raise HTTPException(409, "لا يمكن إلغاء هذا الطلب في حالته الحالية")
    now = datetime.utcnow()
    res = await db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.id == rid, ApprovalRequest.tenant_id == tid,
               ApprovalRequest.status.in_(OPEN_STATUSES))
        .values(status="cancelled", updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "لا يمكن إلغاء هذا الطلب في حالته الحالية")
    await _emit(db, "cancelled", {**plain, "status": "cancelled"}, _actor(user))
    await db.commit()
    db.expire_all()
    return await _load_request(db, tid, rid)


async def count_requests(db: AsyncSession, tid: int, uid: int, role: Optional[str]) -> Dict[str, int]:
    now = datetime.utcnow()
    await expire_stale_approvals(db, tid)
    await db.commit()
    inbox = 0
    if role in APPROVER_ROLES:
        q = select(func.count()).select_from(ApprovalRequest).where(
            ApprovalRequest.tenant_id == tid, ApprovalRequest.status == "pending",
            ApprovalRequest.requested_by != uid,
        )
        if role != "admin":
            q = q.where(ApprovalRequest.required_role == "accountant")
        inbox = int(await db.scalar(q) or 0)
    mine_pending = int(await db.scalar(
        select(func.count()).select_from(ApprovalRequest).where(
            ApprovalRequest.tenant_id == tid, ApprovalRequest.requested_by == uid,
            ApprovalRequest.status == "pending")) or 0)
    mine_ready = int(await db.scalar(
        select(func.count()).select_from(ApprovalRequest).where(
            ApprovalRequest.tenant_id == tid, ApprovalRequest.requested_by == uid,
            ApprovalRequest.status == "approved", ApprovalRequest.expires_at > now)) or 0)
    return {"inbox_pending": inbox, "mine_pending": mine_pending, "mine_ready": mine_ready}
