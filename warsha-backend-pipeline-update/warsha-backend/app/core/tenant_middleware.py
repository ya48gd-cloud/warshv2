"""
TenantMiddleware — يقرأ JWT من رأس الطلب، يستخرج tenant_id، ويضعه في ContextVar.
يُشغَّل قبل أي endpoint، فيصبح tenant متاحاً تلقائياً لـ get_db و ORM events.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.database import current_tenant_id
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


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        token_data = None

        # استخرج التوكن من الرأس
        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(None, 1)[1].strip()
            token_data = verify_token(token)

        tid = None
        if token_data:
            tid = token_data.get("tenant_id")

        # ضع القيمة في ContextVar للطلب الحالي
        token = current_tenant_id.set(tid) if tid is not None else None
        try:
            response = await call_next(request)
        finally:
            # رجّع ContextVar لحالته السابقة (مهم لا يتسرب بين الطلبات)
            if token is not None:
                current_tenant_id.reset(token)

        return response
