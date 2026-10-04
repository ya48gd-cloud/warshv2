import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# تأكيد ترميز UTF-8 لمخارج الطرفية حتى لا تفشل عمليات print بنصوص عربية على Windows
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("warsha")

from app.api.inventory     import router as inventory_router
from app.api.warehouses    import router as warehouses_router
from app.api.equipment     import router as equipment_router, print_router as equipment_print_router
from app.api.accounting    import router as accounting_router, print_router as accounting_print_router
from app.api.cad           import router as cad_router
from app.api.analytics_reports import router as analytics_reports_router
from app.api.workers       import router as workers_router
from app.api.auth          import router as auth_router
from app.api.sales         import router as sales_router, print_router as sales_print_router
from app.api.production    import router as production_router
from app.api.dashboard_new import router as dashboard_new_router
from app.api.customers     import router as customers_router, orders_print_router
from app.api.attendance    import router as attendance_router
from app.api.users         import router as users_router
from app.api.purchases     import router as purchases_router, print_router as purchases_print_router
from app.api.field_service      import router as field_service_router
from app.api.reports            import router as reports_router, aging_print_router, export_router as reports_export_router
from app.api.workshop           import router as workshop_router
from app.api.external_projects  import router as ext_projects_router, print_router as ext_projects_print_router
from app.api.price_catalog      import router as price_catalog_router
from app.api.purchase_orders    import router as purchase_orders_router, print_router as po_print_router
from app.api.chart_of_accounts  import router as chart_of_accounts_router
from app.api.fixed_assets       import router as fixed_assets_router
from app.api.production_v2      import router as production_v2_router
from app.api.work_order_stages  import router as wo_stages_router
from app.api.stock_batches      import router as stock_batches_router
from app.api.treasury           import router as treasury_router, print_router as treasury_print_router
from app.api.supply_contracts   import router as supply_contracts_router, print_router as sc_print_router
from app.api.credit_notes       import router as credit_notes_router, print_router as cn_print_router
from app.api.work_completion_certs import router as wcc_router, print_router as wcc_print_router
from app.api.warranty_certs        import router as warranty_router, print_router as warranty_print_router
from app.api.delivery_orders       import router as delivery_orders_router
from app.api.catalog        import router as catalog_router
from app.api.super_admin    import router as super_admin_router

# ── Multi-tenancy: middleware + event listeners ─────────────
from app.core.tenant_middleware import TenantMiddleware
from app.core import tenant_events  # noqa: F401
from app.desktop import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # ── خدمة خلفية: تحديث حالة الأقساط المتأخرة كل ساعة ──
    from app.services.installment_scheduler import start_installment_scheduler
    scheduler_task = asyncio.create_task(start_installment_scheduler())
    try:
        yield
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Warsh ERP — SaaS",
    description="نظام ERP متكامل لورش تصنيع معدات الأعلاف",
    version="4.0.0",
    lifespan=lifespan,
)

# ── Middleware — الترتيب مهم: FastAPI يطبقهم عكسياً ──────────
# TenantMiddleware يُضاف أولاً هنا = يُنفَّذ آخراً (بعد CORS)
app.add_middleware(TenantMiddleware)

# CORSMiddleware يُضاف ثانياً هنا = يُنفَّذ أولاً (قبل أي middleware آخر)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


# ── معالج عام للأخطاء غير المتوقعة ────────────────────────────
# بدون هذا، أي exception غير معالج (500) قد يتسرب خارج سلسلة
# الـ ASGI middleware قبل أن تضاف رؤوس CORS، فيظهر الخطأ في
# المتصفح كـ "CORS policy" رغم أنه في الحقيقة 500 عادي.
# هذا الـ handler يحوّله دائماً إلى JSONResponse نظيفة تمر عبر
# CORSMiddleware بشكل طبيعي.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception on %s %s:\n%s", request.method, request.url.path, traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"detail": "خطأ داخلي في الخادم — تم تسجيله للمراجعة"},
    )


# ── تعارض قيود قاعدة البيانات (UNIQUE / FK / NOT NULL) ─────────────────
# أي IntegrityError مش ممسوك داخل الـ endpoint (كود مكرر، سباق على ترقيم،
# حذف سجل عليه سجلات مرتبطة...) كان بيطلع 500 "خطأ داخلي" غامض. نرجّعه 409
# برسالة عربية واضحة ومن غير ما نسرّب نص الخطأ الخام (أسماء جداول/قيود).
from sqlalchemy.exc import IntegrityError as _IntegrityError


@app.exception_handler(_IntegrityError)
async def integrity_error_handler(request: Request, exc: _IntegrityError):
    logger.warning("IntegrityError on %s %s: %s", request.method, request.url.path, exc.orig)
    return JSONResponse(
        status_code=409,
        content={"detail": "تعارض في البيانات — القيمة مستخدمة بالفعل أو السجل مرتبط بسجلات أخرى"},
    )


# ── Core Modules ──────────────────────────────────────────────
app.include_router(inventory_router,      prefix="/api/v1")
app.include_router(warehouses_router,     prefix="/api/v1")
app.include_router(analytics_reports_router, prefix="/api/v1")
app.include_router(equipment_router,      prefix="/api/v1")
app.include_router(equipment_print_router, prefix="/api/v1")
app.include_router(accounting_router,     prefix="/api/v1")
app.include_router(accounting_print_router, prefix="/api/v1")
app.include_router(cad_router,           prefix="/api/v1")
app.include_router(workers_router,       prefix="/api/v1")
app.include_router(auth_router,          prefix="/api/v1")
app.include_router(sales_router,         prefix="/api/v1")
app.include_router(sales_print_router,   prefix="/api/v1")
app.include_router(production_router,    prefix="/api/v1")
app.include_router(dashboard_new_router, prefix="/api/v1")
app.include_router(customers_router,     prefix="/api/v1")
app.include_router(orders_print_router,  prefix="/api/v1")
app.include_router(attendance_router,    prefix="/api/v1")
app.include_router(users_router,         prefix="/api/v1")

# ── New SaaS Modules ──────────────────────────────────────────
app.include_router(purchases_router,     prefix="/api/v1")
app.include_router(purchases_print_router, prefix="/api/v1")
app.include_router(field_service_router, prefix="/api/v1")
app.include_router(reports_router,         prefix="/api/v1")
app.include_router(aging_print_router,     prefix="/api/v1")
app.include_router(reports_export_router,  prefix="/api/v1")
app.include_router(workshop_router,      prefix="/api/v1")
app.include_router(ext_projects_router,        prefix="/api/v1")
app.include_router(ext_projects_print_router,  prefix="/api/v1")
app.include_router(price_catalog_router,    prefix="/api/v1")
app.include_router(purchase_orders_router,  prefix="/api/v1")
app.include_router(po_print_router,         prefix="/api/v1")
app.include_router(chart_of_accounts_router, prefix="/api/v1")
app.include_router(fixed_assets_router,      prefix="/api/v1")
app.include_router(production_v2_router,    prefix="/api/v1")
app.include_router(wo_stages_router,        prefix="/api/v1")
app.include_router(stock_batches_router,    prefix="/api/v1")
app.include_router(treasury_router,         prefix="/api/v1")
app.include_router(treasury_print_router,   prefix="/api/v1")
app.include_router(supply_contracts_router, prefix="/api/v1")
app.include_router(sc_print_router,         prefix="/api/v1")
app.include_router(credit_notes_router,     prefix="/api/v1")
app.include_router(cn_print_router,         prefix="/api/v1")
app.include_router(wcc_router,          prefix="/api/v1")
app.include_router(wcc_print_router,    prefix="/api/v1")
app.include_router(warranty_router,        prefix="/api/v1")
app.include_router(warranty_print_router,  prefix="/api/v1")
app.include_router(catalog_router,         prefix="/api/v1")
app.include_router(super_admin_router,     prefix="/api/v1")
app.include_router(delivery_orders_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.0.0"}


static_dir = os.getenv("ERP_STATIC_DIR")
if static_dir and Path(static_dir).exists():
    static_path = Path(static_dir)
    assets_path = static_path / "assets"
    if assets_path.exists():
        app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

    @app.get("/")
    async def desktop_index():
        return FileResponse(static_path / "index.html")

    @app.get("/{full_path:path}")
    async def desktop_spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="API endpoint not found")
        candidate = static_path / full_path
        # SECURITY (fixed): full_path بعد فك الترميز ممكن يحتوي "../" (مثلاً
        # /..%2F..%2Fjwt_secret.key) فيخرج برّه مجلد الواجهة ويقرأ أي ملف
        # (مفتاح JWT، قاعدة البيانات...). نسمح فقط بملفات جوه static_path.
        try:
            candidate.resolve().relative_to(static_path.resolve())
            _inside = True
        except (ValueError, OSError):
            _inside = False
        if _inside and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(static_path / "index.html")
else:
    @app.get("/")
    async def root():
        return {
            "message": "Warsh ERP API v3.0 running",
            "docs": "/docs",
            "modules": [
                "inventory", "equipment", "accounting", "production",
                "sales", "customers", "workers", "attendance",
                "purchases", "field-service", "reports",
            ],
        }