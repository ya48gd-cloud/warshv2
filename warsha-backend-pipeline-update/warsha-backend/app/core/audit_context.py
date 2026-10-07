"""
سياق التدقيق (Audit context) — ContextVars تُضبط من TenantMiddleware لكل طلب.

لا يستورد أي موديل (لتجنّب الاستيراد الدائري). خارج سياق الطلب (jobs / scripts)
كل القيم None => الفاعل يُعتبر "النظام".
"""
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional, List

audit_user_id: ContextVar[Optional[int]] = ContextVar("audit_user_id", default=None)
audit_user_name: ContextVar[Optional[str]] = ContextVar("audit_user_name", default=None)
audit_ip: ContextVar[Optional[str]] = ContextVar("audit_ip", default=None)
audit_path: ContextVar[Optional[str]] = ContextVar("audit_path", default=None)
audit_request_id: ContextVar[Optional[str]] = ContextVar("audit_request_id", default=None)

# تعليق مؤقت للالتقاط (seeders / bulk import / backup restore)
_audit_disabled: ContextVar[bool] = ContextVar("audit_disabled", default=False)

_VARS = (audit_user_id, audit_user_name, audit_ip, audit_path, audit_request_id)


def audit_enabled() -> bool:
    """مفتاح إيقاف عام (AUDIT_ENABLED=false) + تعليق السياق الحالي."""
    if os.getenv("AUDIT_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
        return False
    return not _audit_disabled.get()


@contextmanager
def audit_disabled():
    """تعليق التقاط التدقيق داخل هذا السياق (للـ seeders / الاستيراد الجماعي / الاستعادة)."""
    token = _audit_disabled.set(True)
    try:
        yield
    finally:
        _audit_disabled.reset(token)


def bind_audit_context(user_id: Optional[int], user_name: Optional[str], ip: Optional[str],
                       path: Optional[str], request_id: Optional[str]) -> List:
    """يضبط كل الـ ContextVars ويرجّع tokens لإعادتها لاحقًا."""
    values = (user_id, user_name, ip, path, request_id)
    return [var.set(val) for var, val in zip(_VARS, values)]


def reset_audit_context(tokens: Optional[List]) -> None:
    if not tokens:
        return
    for var, tok in zip(_VARS, tokens):
        try:
            var.reset(tok)
        except Exception:
            pass
