"""
einvoice_worker.py — عامل الطابور الخلفي للفاتورة الإلكترونية.

  * يعيد محاولة الصفوف queued/failed المستحقة (next_attempt_at <= الآن) ويستعلم عن الصفوف submitted القديمة.
  * ERP_EINVOICE_WORKER=0 يوقفه · ERP_EINVOICE_INTERVAL ثواني بين الدورات (افتراضي 60).
  * الـ job بلا JWT: يضبط current_tenant_id لكل صف ويعيده في finally، ومع ذلك كل الاستعلامات بـ tenant_id صريح.
    skip_tenant_filter مستخدم فقط في مسح الصفوف المستحقة عبر الورش.
  * run_einvoice_queue_once لا ترمي استثناءً أبدًا. التكامل غير مُجرَّب على ETA الفعلية.
"""
import asyncio
import logging
import os
from datetime import timedelta
from typing import Optional

from sqlalchemy import select, and_, or_

from app.core import database
from app.core.database import current_tenant_id
from app.models.models import EInvoiceSubmission as S
from app.services import einvoice_service as svc

logger = logging.getLogger("warsha.einvoice")

WORKER_RETRY_ATTEMPTS = 3   # داخل محاولة الـ worker: backoff 2s/4s (الـ worker ليس طلب HTTP)


def worker_enabled() -> bool:
    return (os.getenv("ERP_EINVOICE_WORKER", "1").strip() != "0")


def interval_seconds() -> int:
    try:
        return max(5, int(os.getenv("ERP_EINVOICE_INTERVAL", "60")))
    except ValueError:
        return 60


async def run_einvoice_queue_once(session_factory=None, tenant_id: Optional[int] = None,
                                  limit: int = 50, sleep=None) -> dict:
    counts = {"processed": 0, "valid": 0, "rejected": 0, "failed": 0}
    try:
        factory = session_factory or database.AsyncSessionLocal
        now = svc.utcnow()

        # 1) تنظيف الصفوف العالقة في submitting
        async with factory() as s:
            q = select(S).where(S.status == "submitting", S.locked_at.isnot(None),
                                S.locked_at < now - svc.STALE_LOCK).execution_options(skip_tenant_filter=True)
            if tenant_id is not None:
                q = q.where(S.tenant_id == tenant_id)
            reaped = 0
            for row in (await s.execute(q)).scalars().all():
                if await svc.reap_stale_row(s, row):
                    reaped += 1
            if reaped:
                await s.commit()

        # 2) الصفوف المستحقة
        due = or_(
            and_(S.status == "queued", or_(S.next_attempt_at.is_(None), S.next_attempt_at <= now)),
            and_(S.status == "failed", S.next_attempt_at.isnot(None), S.next_attempt_at <= now),
            and_(S.status == "submitted", S.eta_submission_id.isnot(None),
                 or_(S.last_attempt_at.is_(None), S.last_attempt_at < now - svc.POLL_AFTER)),
        )
        async with factory() as s:
            q = select(S.id, S.tenant_id, S.status).where(due).order_by(S.id).limit(limit) \
                .execution_options(skip_tenant_filter=True)
            if tenant_id is not None:
                q = q.where(S.tenant_id == tenant_id)
            todo = [(r[0], r[1], r[2]) for r in (await s.execute(q)).all()]

        # 3) معالجة كل صف في session مستقلة وبسياق ورشته
        for row_id, tid, st in todo:
            async with factory() as s:
                tok = current_tenant_id.set(tid)
                try:
                    if st in ("queued", "failed"):
                        outcome = await svc.attempt_row(s, tid, row_id, sleep=sleep,
                                                        max_attempts=WORKER_RETRY_ATTEMPTS)
                    else:
                        outcome = await svc.poll_row(s, tid, row_id, sleep=sleep)
                    if outcome in ("skipped", "not_claimed"):
                        continue
                    counts["processed"] += 1
                    final = await svc.load_row_by_id(s, tid, row_id)
                    if final is not None:
                        if final.status == "valid":
                            counts["valid"] += 1
                        elif final.status in ("rejected", "invalid"):
                            counts["rejected"] += 1
                        elif final.status == "failed":
                            counts["failed"] += 1
                except Exception as e:  # noqa: BLE001
                    logger.error("einvoice worker row error: %s", type(e).__name__)
                    try:
                        await s.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                finally:
                    current_tenant_id.reset(tok)
    except Exception as e:  # noqa: BLE001
        logger.error("einvoice worker error: %s", type(e).__name__)
    return counts


async def start_einvoice_worker() -> None:
    """Loop يشتغل كـ background task من lifespan (لا يشتغل في الاختبارات: ASGITransport بلا lifespan)."""
    if not worker_enabled():
        return
    await asyncio.sleep(30)
    while True:
        try:
            res = await run_einvoice_queue_once()
            if res.get("processed"):
                logger.info("einvoice worker: %s", res)
        except Exception as e:  # noqa: BLE001
            logger.error("einvoice worker loop error: %s", type(e).__name__)
        await asyncio.sleep(interval_seconds())
