"""
backups.py — النسخ الاحتياطي والاسترجاع (API)

  - sa_router  /super-admin/backups : للسوبر أدمن فقط (توكن in-memory عبر _verify_token).
        لا نستخدم Depends(get_db): الخدمة بتفتح جلسات قصيرة بنفسها (مهم للاسترجاع على SQLite).
  - router     /backups             : أدمن الورشة فقط (require_admin) — تصدير بيانات ورشته.
        الـ tenant_id من الـ JWT فقط، ودايماً بفلتر صريح.
"""
import asyncio
import logging
import os
import shutil
import tempfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.api.auth import require_admin
from app.api.super_admin import _verify_token
from app.core.database import get_db
from app.models.models import BackupRun
from app.services import backup_service as svc
from app.services import tenant_export as texp

logger = logging.getLogger("warsha.backup")

router = APIRouter(prefix="/backups", tags=["backups"])
sa_router = APIRouter(prefix="/super-admin/backups", tags=["backups-super-admin"])

_EXPOSE = "X-Checksum-SHA256, X-Export-Run-Id, X-Export-Checksum-SHA256, Content-Disposition"


class RestoreIn(BaseModel):
    confirm_token: str
    confirm_text: str


# ═════════════════════════════════════════════════════════════
# السوبر أدمن
# ═════════════════════════════════════════════════════════════

@sa_router.get("/status")
async def backup_status(_t: str = Depends(_verify_token)):
    return await svc.compute_status()


@sa_router.get("/restore-instructions")
async def restore_instructions(_t: str = Depends(_verify_token)):
    return svc.restore_instructions()


@sa_router.get("")
@sa_router.get("/", include_in_schema=False)
async def list_backups(
    limit: int = Query(50, ge=1, le=200),
    kind: Optional[str] = Query(None),
    _t: str = Depends(_verify_token),
):
    if kind is not None and kind not in svc.SYSTEM_KINDS:
        raise HTTPException(400, "نوع العملية غير صالح")
    return await svc.list_runs(limit=limit, kind=kind)


@sa_router.post("", status_code=201)
@sa_router.post("/", status_code=201, include_in_schema=False)
async def create_backup(_t: str = Depends(_verify_token)):
    run = await svc.create_full_backup("manual", "superadmin")
    return svc.run_to_item(run)


@sa_router.get("/{run_id}/download")
async def download_backup(run_id: int, _t: str = Depends(_verify_token)):
    run = await svc.get_system_run(run_id)
    path = svc.file_path_for_run(run)
    return FileResponse(
        str(path), media_type="application/octet-stream", filename=run.file_name,
        headers={"X-Checksum-SHA256": run.checksum_sha256 or "",
                 "Access-Control-Expose-Headers": _EXPOSE},
    )


@sa_router.delete("/{run_id}")
async def delete_backup(run_id: int, _t: str = Depends(_verify_token)):
    return await svc.delete_backup(run_id, "superadmin")


@sa_router.post("/{run_id}/verify")
async def verify_backup(run_id: int, _t: str = Depends(_verify_token)):
    return await svc.verify_backup(run_id)


@sa_router.post("/{run_id}/restore/prepare")
async def restore_prepare(run_id: int, token: str = Depends(_verify_token)):
    return await svc.prepare_restore(run_id, token)


@sa_router.post("/{run_id}/restore")
async def restore_backup(run_id: int, body: RestoreIn, token: str = Depends(_verify_token)):
    return await svc.restore_sqlite_backup(run_id, body.confirm_token, body.confirm_text, token)


# ═════════════════════════════════════════════════════════════
# أدمن الورشة — تصدير بيانات الورشة
# ═════════════════════════════════════════════════════════════

_export_running: set = set()  # tenant_ids اللي عليهم تصدير شغال (in-process)
_EXPORT_MEDIA = {"zip": "application/zip", "json": "application/json"}


def _cleanup_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


@router.post("/tenant-export")
async def tenant_export(
    fmt: str = Query("zip", alias="format"),
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tid = user.get("tenant_id")
    if tid is None:
        raise HTTPException(403, "غير مسموح")
    try:
        tid = int(tid)
    except (TypeError, ValueError):
        raise HTTPException(403, "غير مسموح")
    if fmt not in _EXPORT_MEDIA:
        raise HTTPException(400, "صيغة التصدير غير مدعومة — المتاح: zip أو json")
    if tid in _export_running:
        raise HTTPException(409, "عملية تصدير جارية بالفعل")
    _export_running.add(tid)
    tmp_dir: Optional[str] = None
    run_id: Optional[int] = None
    try:
        try:
            uid = int(user.get("sub"))
        except (TypeError, ValueError):
            uid = None
        run = BackupRun(
            tenant_id=tid, kind=svc.KIND_TENANT_EXPORT, trigger="manual", status="running",
            db_engine=svc.db_engine_kind(), created_by=user.get("username"),
            created_by_user_id=uid, started_at=svc.utcnow(), details={"format": fmt},
        )
        db.add(run)
        await db.commit()
        run_id = run.id

        tmp_dir = tempfile.mkdtemp(prefix="warsha_export_")
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        fname = f"warsha-export-{tid}-{stamp}.{fmt}"
        dest = os.path.join(tmp_dir, fname)
        res = await texp.export_tenant_to_file(db, tid, fmt, dest)

        run = (await db.execute(
            select(BackupRun).where(BackupRun.id == run_id, BackupRun.tenant_id == tid)
        )).scalars().first()
        run.status = "success"
        run.finished_at = svc.utcnow()
        run.size_bytes = res["size"]
        run.checksum_sha256 = res["sha256"]
        run.integrity_ok = True
        run.details = {"format": fmt, "tables": res["tables"], "redacted_columns": res["redacted"]}
        await db.commit()
        return FileResponse(
            dest, media_type=_EXPORT_MEDIA[fmt], filename=fname,
            headers={"X-Export-Run-Id": str(run_id), "X-Export-Checksum-SHA256": res["sha256"],
                     "Access-Control-Expose-Headers": _EXPOSE},
            background=BackgroundTask(_cleanup_dir, tmp_dir),
        )
    except BaseException as e:  # noqa: BLE001
        if tmp_dir:
            _cleanup_dir(tmp_dir)
        if run_id is not None:
            try:
                await db.rollback()
                r = (await db.execute(
                    select(BackupRun).where(BackupRun.id == run_id, BackupRun.tenant_id == tid)
                )).scalars().first()
                if r is not None:
                    r.status = "failed"
                    r.finished_at = svc.utcnow()
                    r.error = svc.sanitize_error(f"{type(e).__name__}: {e}")
                    await db.commit()
            except Exception:  # noqa: BLE001
                logger.warning("could not record failed export", exc_info=True)
        if isinstance(e, HTTPException) or isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
            raise
        logger.error("tenant export failed", exc_info=True)
        raise HTTPException(500, "فشل تصدير بيانات الورشة — تم تسجيل الخطأ")
    finally:
        _export_running.discard(tid)


@router.get("/exports")
async def list_exports(
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tid = user.get("tenant_id")
    if tid is None:
        raise HTTPException(403, "غير مسموح")
    rows = (await db.execute(
        select(BackupRun).where(
            BackupRun.tenant_id == int(tid), BackupRun.kind == svc.KIND_TENANT_EXPORT
        ).order_by(BackupRun.id.desc()).limit(limit)
    )).scalars().all()
    out = []
    for r in rows:
        d = r.details or {}
        out.append({
            "id": r.id, "status": r.status,
            "started_at": svc.iso_z(r.started_at), "finished_at": svc.iso_z(r.finished_at),
            "size_bytes": r.size_bytes, "checksum_sha256": r.checksum_sha256,
            "created_by": r.created_by, "error": r.error,
            "format": d.get("format"), "tables": d.get("tables") or {},
        })
    return out
