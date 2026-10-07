"""
backup_scheduler.py — جدولة النسخ الاحتياطي التلقائي (يومياً افتراضياً).

مبني على نمط installment_scheduler: مهمة asyncio تبدأ من lifespan وتفحص كل ساعة.
  - ERP_BACKUP_AUTO=0 يوقف الجدولة بالكامل.
  - لو فيه نسخة ناجحة خلال ERP_BACKUP_INTERVAL_HOURS (افتراضي 24) يتخطى.
  - فشل محاولة مجدولة بيحسب كمحاولة: ما نعيدش قبل ERP_BACKUP_RETRY_HOURS (افتراضي 6)
    وبالتالي ما يتكتبش أكتر من صف فاشل واحد لكل نافذة (مفيش spam لو pg_dump مش موجود).
  - الـ job ملوش ContextVar للـ tenant: كل الاستعلامات بـ tenant_id IS NULL صريح.
  - run_scheduled_backup_once() ما بترميش exception أبداً.
"""
import asyncio
import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import select

from app.core import database
from app.models.models import BackupRun
from app.services import backup_service as svc

logger = logging.getLogger("warsha.backup")


async def run_scheduled_backup_once(session_factory=None) -> dict:
    """محاولة واحدة. يرجّع {skipped, reason} أو {ran: True, run_id} أو {ran: False, failed: True, error}."""
    try:
        if not svc.auto_enabled():
            return {"skipped": True, "reason": "disabled"}
        if svc.is_busy():
            return {"skipped": True, "reason": "busy"}
        factory = session_factory or database.AsyncSessionLocal
        # تنظيف وصيانة
        await asyncio.to_thread(svc.cleanup_part_files)
        await svc.reap_stale_runs(session_factory)
        await svc.reindex_backup_dir(session_factory)

        now = svc.utcnow()
        async with factory() as s:
            last = await svc._last_success(s)
            if last and last.finished_at and \
                    now - last.finished_at < timedelta(hours=svc.get_interval_hours()):
                return {"skipped": True, "reason": "recent_backup"}
            recent_fail = (await s.execute(
                select(BackupRun).where(
                    BackupRun.tenant_id.is_(None), BackupRun.kind == svc.KIND_FULL,
                    BackupRun.trigger == "scheduled", BackupRun.status == "failed",
                ).order_by(BackupRun.id.desc()).limit(1)
            )).scalars().first()
            if recent_fail and recent_fail.finished_at and \
                    now - recent_fail.finished_at < timedelta(hours=svc.get_retry_hours()):
                return {"skipped": True, "reason": "backoff"}
        try:
            run = await svc.create_full_backup("scheduled", "scheduler", session_factory)
        except svc.BackupError as e:
            if e.status_code == 409:
                return {"skipped": True, "reason": "busy"}
            return {"ran": False, "failed": True, "error": str(e.detail)}
        return {"ran": True, "run_id": run.id}
    except Exception as e:  # noqa: BLE001
        logger.error("scheduled backup error: %s", svc.sanitize_error(e), exc_info=True)
        return {"ran": False, "failed": True, "error": svc.sanitize_error(e)}


async def start_backup_scheduler() -> None:
    """Loop يشتغل كـ background task من lifespan (لا يشتغل في الاختبارات: ASGITransport بلا lifespan)."""
    if not svc.auto_enabled():
        return
    await asyncio.sleep(60)  # استنى الـ DB والتطبيق يستقروا
    while True:
        try:
            res = await run_scheduled_backup_once()
            if res.get("ran") or res.get("failed"):
                logger.info("backup scheduler: %s", {k: v for k, v in res.items() if k != "error"})
        except Exception as e:  # noqa: BLE001
            logger.error("backup scheduler loop error: %s", svc.sanitize_error(e))
        await asyncio.sleep(3600)
