import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.inventory     import router as inventory_router
from app.api.equipment     import router as equipment_router
from app.api.accounting    import router as accounting_router
from app.api.cad           import router as cad_router
from app.api.workers       import router as workers_router
from app.api.auth          import router as auth_router
from app.api.sales         import router as sales_router
from app.api.production    import router as production_router
from app.api.dashboard_new import router as dashboard_new_router
from app.api.customers     import router as customers_router
from app.api.attendance    import router as attendance_router
from app.api.users         import router as users_router          # ← RBAC Phase 2

app = FastAPI(
    title="Heavy Equipment Workshop ERP",
    description="نظام ERP لورشة تصنيع المعدات الثقيلة",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inventory_router,     prefix="/api/v1")
app.include_router(equipment_router,     prefix="/api/v1")
app.include_router(accounting_router,    prefix="/api/v1")
app.include_router(cad_router,           prefix="/api/v1")
app.include_router(workers_router,       prefix="/api/v1")
app.include_router(auth_router,          prefix="/api/v1")
app.include_router(sales_router,         prefix="/api/v1")
app.include_router(production_router,    prefix="/api/v1")
app.include_router(dashboard_new_router, prefix="/api/v1")
app.include_router(customers_router,     prefix="/api/v1")
app.include_router(attendance_router,    prefix="/api/v1")
app.include_router(users_router,         prefix="/api/v1")        # ← RBAC Phase 2


@app.get("/health")
async def health():
    return {"status": "ok"}


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
        candidate = static_path / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(static_path / "index.html")
else:
    @app.get("/")
    async def root():
        return {"message": "Heavy ERP API running", "docs": "/docs"}
