"""
Audit Log API — للأدمن فقط. قراءة فقط على السجلات (immutable) + إعداد مدة الاحتفاظ.

GET /audit/logs                           قائمة بفلاتر + pagination
GET /audit/logs/{id}                      تفاصيل سجل (changes + extra)
GET /audit/entities/{type}/{id}           تاريخ كيان محدد
GET /audit/meta                           بيانات الفلاتر
GET /audit/settings  /  PUT /audit/settings   مدة الاحتفاظ (الاستثناء الوحيد غير GET)

كل استعلام مقيّد صراحةً بـ tenant_id من التوكن (لا اعتماد على الفلتر التلقائي فقط).
لا يوجد أي route لتعديل أو حذف سجل، ولا route لتشغيل التنظيف.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.core.audit_events import audited_tables
from app.core.database import get_db
from app.models.models import AuditLog, AuditSetting, User
from app.services import audit_retention as ret
from app.services.audit import ACTIONS, ACTION_LABELS, entity_label

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_admin)])


def _tid(user: dict) -> int:
    return int(user["tenant_id"])


def _iso_z(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat() + "Z"


def _changed_fields(changes) -> list:
    if not isinstance(changes, dict):
        return []
    return [k for k in changes.keys() if not str(k).startswith("_")]


def _row(r: AuditLog, full: bool) -> dict:
    d = {
        "id": r.id,
        "created_at": _iso_z(r.created_at),
        "action": r.action,
        "action_label": ACTION_LABELS.get(r.action, r.action),
        "entity_type": r.entity_type,
        "entity_label": entity_label(r.entity_type),
        "entity_id": r.entity_id,
        "entity_ref": r.entity_ref,
        "user_id": r.user_id,
        "user_name": r.user_name,
        "ip": r.ip,
        "path": r.path,
        "request_id": r.request_id,
        "summary": r.summary,
        "changed_fields": _changed_fields(r.changes),
    }
    if full:
        d["changes"] = r.changes
        d["extra"] = r.extra
    return d


def _validate_entity_type(entity_type: str) -> None:
    if entity_type not in audited_tables():
        raise HTTPException(400, "نوع الكيان غير صالح")


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/logs")
async def list_logs(
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    tz_offset_minutes: int = Query(0, ge=-840, le=840),
    q: Optional[str] = None,
    include_changes: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tid = _tid(user)
    if action is not None and action not in ACTIONS:
        raise HTTPException(400, "إجراء غير صالح")
    if entity_type:
        _validate_entity_type(entity_type)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "تاريخ البداية يجب أن يكون قبل أو يساوي تاريخ النهاية")

    conds = [AuditLog.tenant_id == tid]
    if entity_type:
        conds.append(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        conds.append(AuditLog.entity_id == entity_id)
    if user_id is not None:
        conds.append(AuditLog.user_id == user_id)
    if action:
        conds.append(AuditLog.action == action)
    offset_td = timedelta(minutes=tz_offset_minutes)
    if date_from:
        conds.append(AuditLog.created_at >= datetime(date_from.year, date_from.month, date_from.day) - offset_td)
    if date_to:
        end = datetime(date_to.year, date_to.month, date_to.day) + timedelta(days=1)
        conds.append(AuditLog.created_at < end - offset_td)
    if q and q.strip():
        pat = f"%{_escape_like(q.strip())}%"
        conds.append((AuditLog.entity_ref.ilike(pat, escape="\\"))
                     | (AuditLog.user_name.ilike(pat, escape="\\")))

    total = (await db.execute(
        select(func.count()).select_from(AuditLog).where(*conds))).scalar_one()
    rows = (await db.execute(
        select(AuditLog).where(*conds)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit).offset(offset))).scalars().all()
    items = []
    for r in rows:
        d = _row(r, full=False)
        if include_changes:
            d["changes"] = r.changes
        items.append(d)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/logs/{log_id}")
async def get_log(
    log_id: int,
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tid = _tid(user)
    r = (await db.execute(
        select(AuditLog).where(AuditLog.id == log_id, AuditLog.tenant_id == tid)
    )).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "السجل غير موجود")
    return _row(r, full=True)


@router.get("/entities/{entity_type}/{entity_id}")
async def entity_history(
    entity_type: str,
    entity_id: int,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tid = _tid(user)
    _validate_entity_type(entity_type)
    conds = [AuditLog.tenant_id == tid, AuditLog.entity_type == entity_type,
             AuditLog.entity_id == entity_id]
    total = (await db.execute(
        select(func.count()).select_from(AuditLog).where(*conds))).scalar_one()
    rows = (await db.execute(
        select(AuditLog).where(*conds)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit).offset(offset))).scalars().all()
    return {"items": [_row(r, full=True) for r in rows],
            "total": total, "limit": limit, "offset": offset}


async def _override(db: AsyncSession, tid: int) -> Optional[int]:
    s = (await db.execute(
        select(AuditSetting).where(AuditSetting.tenant_id == tid))).scalar_one_or_none()
    return s.retention_days if s else None


@router.get("/meta")
async def meta(
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tid = _tid(user)
    users = (await db.execute(
        select(User.id, User.username, User.full_name)
        .where(User.tenant_id == tid).order_by(User.username))).all()
    p = ret.settings_payload(await _override(db, tid))
    return {
        "actions": [{"value": a, "label": ACTION_LABELS[a]} for a in ACTIONS],
        "entity_types": [{"value": t, "label": entity_label(t)} for t in audited_tables()],
        "users": [{"id": u.id, "username": u.username, "full_name": u.full_name} for u in users],
        "retention": {"days": p["days"], "custom": p["custom"],
                      "default_days": p["default_days"],
                      "min_days": p["min_days"], "max_days": p["max_days"]},
    }


@router.get("/settings")
async def get_settings(
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return ret.settings_payload(await _override(db, _tid(user)))


class AuditSettingsIn(BaseModel):
    retention_days: Optional[int]  # مطلوب (null = إزالة الـ override)


@router.put("/settings")
async def put_settings(
    body: AuditSettingsIn,
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tid = _tid(user)
    if not ret.valid_retention(body.retention_days):
        raise HTTPException(400, "مدة الاحتفاظ يجب أن تكون 0 أو بين 90 و3650 يوم")
    s = (await db.execute(
        select(AuditSetting).where(AuditSetting.tenant_id == tid))).scalar_one_or_none()
    if body.retention_days is None:
        if s is not None:
            await db.delete(s)
    else:
        if s is None:
            s = AuditSetting(tenant_id=tid)
            db.add(s)
        s.retention_days = body.retention_days
        s.updated_by = int(user["sub"])
        s.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    return ret.settings_payload(body.retention_days)
