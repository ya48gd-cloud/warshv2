"""
TenantMiddleware — يقرأ JWT من رأس الطلب، يستخرج tenant_id، ويضعه في ContextVar.
يُشغَّل قبل أي endpoint، فيصبح tenant متاحاً تلقائياً لـ get_db و ORM events.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.database import current_tenant_id
from app.core import audit_context as _audit_ctx
from app.api.auth import verify_token


# مسارات معفاة من تحديد الـ tenant
PUBLIC_PATHS = {
    "/health",
    "/api/v1/auth/login",
    "/api/v1/auth/signup",
    "/docs", "/redoc", "/openapi.json",
}


def _is_public(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in PUBLIC_PATHS)


def _bind_audit(request: Request, token_data):
    import os
    import uuid
    uid = None
    uname = None
    if token_data:
        try:
            uid = int(token_data.get("sub"))
        except (TypeError, ValueError):
            uid = None
        uname = token_data.get("username")
    path = request.url.path
    if path.startswith("/api/v1/super-admin"):
        uname = "super-admin"  # توكنات السوبر أدمن ليست JWT
    ip = request.client.host if request.client else None
    if os.getenv("AUDIT_TRUST_PROXY", "").strip() == "1":
        xff = request.headers.get("x-forwarded-for")
        if xff:
            ip = xff.split(",")[0].strip() or ip
    return _audit_ctx.bind_audit_context(
        uid, uname, ip, f"{request.method} {path}"[:200], uuid.uuid4().hex)


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # وضع الصيانة أثناء استرجاع نسخة احتياطية (backup): كل شيء 503 ما عدا السوبر أدمن و/health
        from app.services.backup_service import is_restoring
        if is_restoring():
            _p = request.url.path
            if not (_p == "/health" or _p.startswith("/api/v1/super-admin")):
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=503,
                    content={"detail": "النظام قيد الاسترجاع من نسخة احتياطية — حاول بعد قليل"})
        token_data = None

        # استخرج التوكن من الرأس
        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            _parts = auth.split(None, 1)
            if len(_parts) == 2 and _parts[1].strip():
                token_data = verify_token(_parts[1].strip())

        tid = None
        if token_data:
            tid = token_data.get("tenant_id")

        # ضع القيمة في ContextVar للطلب الحالي
        token = current_tenant_id.set(tid) if tid is not None else None
        # سياق التدقيق (سجل التدقيق): المستخدم/IP/المسار/معرّف الطلب — لا يكسر الطلب أبداً
        audit_tokens = None
        try:
            audit_tokens = _bind_audit(request, token_data)
        except Exception:
            audit_tokens = None
        try:
            response = await call_next(request)
        finally:
            # رجّع ContextVar لحالته السابقة (مهم لا يتسرب بين الطلبات)
            if token is not None:
                current_tenant_id.reset(token)
            _audit_ctx.reset_audit_context(audit_tokens)

        return response
