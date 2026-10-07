"""
التقاط سجل التدقيق مركزيًا عبر SQLAlchemy Session events (يُسجَّل بمجرد الاستيراد،
مثل tenant_events). لا يلمس أي endpoint.

الآلية (كل شيء في after_flush):
  - after_flush يشتغل قبل finalize_flush_changes، فتاريخ الخصائص (attribute history)
    و state.dict لسه سليمين: نقدر نحسب diff التعديل ونلقط صور الحذف.
  - الصفوف تُكتب بـ Core executemany على نفس الاتصال/الـ transaction (session.connection())
    => rollback للعملية بيلغي صفوف التدقيق معاها، ومفيش recursion (Core لا يشغّل ORM events)
    ولا فلتر tenant.
  - tenant_id الصف دايمًا من الكائن نفسه (مش من الـ ContextVar) => آمن في jobs.
  - لا نقرأ إلا state.dict و attrs[key].history (لا lazy-load => لا MissingGreenlet).
  - أي خطأ داخل الالتقاط يُسجَّل كتحذير ولا يكسر العملية التجارية.

before_flush: حارس immutability — أي تعديل/حذف ORM لكائن AuditLog يرفع RuntimeError.
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import Integer, event, insert, inspect as sa_inspect
from sqlalchemy.orm import Session

from app.core import audit_context as ctx
from app.models.models import AuditLog
from app.services import audit as svc

logger = logging.getLogger("warsha.audit")

_CHUNK = 500
_MISSING = object()
_SKIP_COLS = frozenset({"tenant_id"})


class _Info:
    __slots__ = ("table", "pk_key", "cols", "ignored")

    def __init__(self, table: str, pk_key: Optional[str], cols: List[str], ignored: frozenset):
        self.table = table
        self.pk_key = pk_key
        self.cols = cols
        self.ignored = ignored


_INFO: Dict[Any, Optional[_Info]] = {}


def _info(mapper) -> Optional[_Info]:
    """معلومات مُخزَّنة لكل mapper (None = غير مُدقَّق)."""
    try:
        return _INFO[mapper]
    except KeyError:
        pass
    info = None
    try:
        cls = mapper.class_
        table = mapper.local_table.name
        if hasattr(cls, "tenant_id") and table not in svc.IGNORED_TABLES:
            pk_key = None
            if len(mapper.primary_key) == 1 and isinstance(mapper.primary_key[0].type, Integer):
                pk_key = mapper.get_property_by_column(mapper.primary_key[0]).key
            cols = [p.key for p in mapper.column_attrs]
            ignored = svc.GLOBAL_IGNORED_COLUMNS | svc.TABLE_IGNORED_COLUMNS.get(table, frozenset())
            info = _Info(table, pk_key, cols, ignored)
    except Exception:
        info = None
    _INFO[mapper] = info
    return info


def audited_tables() -> List[str]:
    """أسماء كل الجداول المُدقَّقة (للـ API / meta)."""
    from app.core.database import Base
    out = set()
    for mapper in Base.registry.mappers:
        info = _info(mapper)
        if info is not None:
            out.add(info.table)
    return sorted(out)


# ── حارس immutability ───────────────────────────────────────────
@event.listens_for(Session, "before_flush")
def _audit_immutability_guard(session, flush_context, instances):
    if not session.dirty and not session.deleted:
        return
    for coll in (session.dirty, session.deleted):
        for obj in coll:
            if isinstance(obj, AuditLog):
                raise RuntimeError("سجل التدقيق غير قابل للتعديل")


# ── الالتقاط ─────────────────────────────────────────────────────
def _snapshot(values: Dict[str, Any], info: _Info, key_filter) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for key in info.cols:
        if key in info.ignored or key in _SKIP_COLS or key == info.pk_key:
            continue
        v = values.get(key, _MISSING)
        if v is _MISSING or v is None:
            continue
        out[key] = {key_filter: svc.mask_or_safe(key, v)}
    return out


def _entity_id(values: Dict[str, Any], info: _Info) -> Optional[int]:
    if not info.pk_key:
        return None
    v = values.get(info.pk_key)
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _build_rows(session, flush_context) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    actor_known = (ctx.audit_user_id.get() is not None) or (ctx.audit_user_name.get() is not None)
    for state, (isdelete, listonly) in list(flush_context.states.items()):
        if listonly:
            continue
        info = _info(state.mapper)
        if info is None:
            continue
        values = state.dict
        tid = values.get("tenant_id")
        if not isinstance(tid, int) or isinstance(tid, bool):
            continue  # بدون tenant لا نكتب (لا نخمّن)

        if isdelete:
            op = "delete"
            changes = _snapshot(values, info, "old")
        elif not state.has_identity:
            op = "create"
            changes = _snapshot(values, info, "new")
        else:
            op = "update"
            changes = {}
            for key in list(state.committed_state.keys()):
                if key not in info.cols or key in info.ignored:
                    continue
                if key not in values:
                    continue
                hist = state.attrs[key].history
                if not hist.has_changes():
                    continue
                new = values[key]
                if hist.deleted:
                    old = hist.deleted[0]
                    if svc.values_equal(old, new):
                        continue
                    changes[key] = {"old": svc.mask_or_safe(key, old),
                                    "new": svc.mask_or_safe(key, new)}
                else:
                    # القيمة القديمة غير محمّلة (attribute كان expired)
                    changes[key] = {"old": None, "old_unknown": True,
                                    "new": svc.mask_or_safe(key, new)}
            if not changes:
                continue

        action = svc.classify_action(info.table, op, changes.keys(), actor_known)
        rows.append(svc.build_row(
            tenant_id=tid,
            action=action,
            entity_type=info.table,
            entity_id=_entity_id(values, info),
            entity_ref=svc.pick_ref(values),
            changes=svc.cap_changes(changes) if changes else None,
        ))
    return rows


@event.listens_for(Session, "after_flush")
def _audit_after_flush(session, flush_context):
    try:
        if not ctx.audit_enabled():
            return
        if not flush_context.states:
            return
        rows = _build_rows(session, flush_context)
        if not rows:
            return
        conn = session.connection()
        table = AuditLog.__table__
        for i in range(0, len(rows), _CHUNK):
            conn.execute(insert(table), rows[i:i + _CHUNK])
    except Exception:
        # التدقيق لا يكسر عملية تجارية أبدًا
        logger.warning("audit capture failed", exc_info=True)
